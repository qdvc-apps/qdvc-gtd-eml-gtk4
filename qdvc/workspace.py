"""A gtd-eml workspace on disk, and the CLI's workflow actions on it.

Every mutating method reproduces a ``gtd.py`` command — ingest (``list``),
``alloc``, ``close``, ``pin``/``unpin``, ``metadata set``, ``metadata_check``
and ``workflow_autofix`` — including its refusals, worded as the CLI words
them. The work is done by the CLI's own functions (``qdvc.gtd_modules``); this
module adds only what an interactive app needs on top:

* refusal *checks* that change nothing, so the UI can disable a command and
  say why before it is chosen;
* several fields written in one ``metadata.csv`` write (the bytes are the same
  as the CLI's sequence of single-field writes);
* atomic writes: the CLI rewrites ``metadata.csv`` in place, while this app
  writes a temporary file and renames it over the original, as the QDVC spec
  requires. ``_write_rows`` uses exactly the CLI's ``csv`` calls, so the bytes
  are identical.
"""

import csv
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date

from . import folders as F
from .gtd_modules import config as cli_config
from .gtd_modules import emailutil, fs, metadata, metrics, naming

HEADERS = cli_config.METADATA_HEADERS
FIELDS = [h for h in HEADERS if h != "eml_filename"]
EDITABLE_FIELDS = metadata.EDITABLE_FIELDS
WRITABLE_FIELDS = metadata.WRITABLE_FIELDS

# metadata_check's pattern for an .eml filename inside free text.
_EML_IN_TEXT = re.compile(r"\S+\.eml", re.IGNORECASE)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class GTDError(Exception):
    """A refused or failed action. ``message`` matches the CLI's ``error:``
    line (without the prefix); ``detail`` its indented second line, if any."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self):
        return f"{self.message}\n{self.detail}" if self.detail else self.message


@dataclass
class AutofixPlan:
    pending_input: int = 0
    fixes: list = field(default_factory=list)      # metrics.plan_autofix dicts
    blockers: list = field(default_factory=list)

    @property
    def is_clean(self):
        return self.pending_input == 0 and not self.fixes and not self.blockers

    @property
    def needs_review(self):
        return bool(self.fixes or self.blockers)


@dataclass
class MetadataCheckReport:
    missing_files: list        # rows whose eml_filename no longer exists
    dangling_refs: list        # (email, referenced filename) pairs

    @property
    def is_clean(self):
        return not self.missing_files and not self.dangling_refs


def blank_row():
    return {h: "" for h in FIELDS}


def is_iso_date(value):
    return bool(_ISO_DATE.match((value or "").strip()))


class Workspace:
    """The folder holding 01-input … 06-archive and metadata.csv."""

    def __init__(self, root, today=None):
        self.root = os.path.abspath(root)
        # The date written into ds_* stamps: the local date, as the CLI's
        # metadata.today_stamp(). Injectable for tests.
        self.today = today or date.today

    # ------------------------------------------------------------ paths

    def folder_path(self, folder):
        return os.path.join(self.root, folder.dir)

    def path(self, folder, filename):
        return os.path.join(self.root, folder.dir, filename)

    @property
    def metadata_path(self):
        return os.path.join(self.root, cli_config.METADATA_FILE)

    @property
    def config_path(self):
        return cli_config.workspace_config_path(self.root)

    @staticmethod
    def looks_like_workspace(root):
        """True when the folder already has metadata.csv or a workflow folder."""
        if os.path.isfile(os.path.join(root, cli_config.METADATA_FILE)):
            return True
        return any(os.path.isdir(os.path.join(root, d)) for d in cli_config.ALL_DIRS)

    def ensure_folders(self):
        fs.ensure_folders(self.root)

    def list_eml(self, folder):
        return fs.list_eml_files(self.root, folder.dir)

    def all_existing(self):
        return fs.all_existing_filenames(self.root)

    def find(self, filename):
        """(Folder, canonical name) of an email anywhere in the workflow, or None."""
        found = fs.find_eml(self.root, filename)
        if found is None:
            return None
        return F.BY_DIR[os.path.basename(os.path.dirname(found))], os.path.basename(found)

    # ------------------------------------------------------------ metadata.csv

    def load_metadata(self):
        return metadata.load_metadata(self.root)

    @staticmethod
    def synced(rows, current, seeds=None):
        """The rows `metadata.sync_metadata` would write: one per file in
        `current`, existing values kept, new rows seeded."""
        seeds = seeds or {}
        out = {}
        for name in sorted(current):
            if name in rows:
                prev = rows[name]
            else:
                prev = blank_row()
                prev.update(seeds.get(name, {}))
            out[name] = {h: prev.get(h, "") for h in FIELDS}
        return out

    def _write_rows(self, rows):
        """Write metadata.csv as the CLI does, but atomically."""
        fd, tmp = tempfile.mkstemp(prefix=".metadata-", suffix=".csv.tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()
                for name in sorted(rows):
                    row = {"eml_filename": name}
                    row.update({h: rows[name].get(h, "") for h in FIELDS})
                    writer.writerow(row)
            if os.path.exists(self.metadata_path):
                shutil.copymode(self.metadata_path, tmp)
            else:
                os.chmod(tmp, 0o666 & ~_umask())
            os.replace(tmp, self.metadata_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def sync_metadata(self, seeds=None):
        """`metadata.sync_metadata(base_dir, new_values=seeds)`."""
        self._write_rows(self.synced(self.load_metadata(), self.all_existing(), seeds))

    def update_metadata(self, filename, changes):
        """`set_metadata_value` for several fields at once: sync, then set."""
        for key in changes:
            if key not in WRITABLE_FIELDS:
                raise GTDError(f"field '{key}' is not editable",
                               "editable fields: " + ", ".join(EDITABLE_FIELDS))
        current = self.all_existing()
        if filename not in current:
            raise GTDError(f"'{filename}' is not in the workflow")
        rows = self.synced(self.load_metadata(), current)
        row = rows.setdefault(filename, blank_row())
        row.update(changes)
        self._write_rows(rows)

    # ------------------------------------------------------------ helpers

    def _not_found(self, filename, suffix=""):
        return GTDError(f"'{filename}' not found in any GTD folder under {self.root}{suffix}")

    def _locate(self, filename, suffix=""):
        found = self.find(filename)
        if found is None:
            raise self._not_found(filename, suffix)
        return found

    def _move(self, name, src, dest):
        try:
            fs.move_eml(self.path(src, name), self.root, dest.dir)
        except FileExistsError:
            raise GTDError(f"a file named '{name}' already exists in {dest.dir}") from None

    # ------------------------------------------------------------ ingest (`gtd list`)

    def ingest(self, max_filename_chars):
        """Rename every .eml in 01-input and move it to 02-triage, then sync
        metadata.csv, seeding ds_triage (today) and message_ref for the new
        rows. Returns [(old_name, new_name, message_ref)].

        This is `ingest.ingest_input_files` followed by the `list` command's
        sync, with one difference: if a file cannot be read, the files already
        moved are still recorded in metadata.csv before the error is raised.
        """
        self.ensure_folders()
        existing = self.all_existing()
        moved, failure = [], None
        for old_name in self.list_eml(F.INPUT):
            old_path = self.path(F.INPUT, old_name)
            try:
                message = emailutil.read_eml_message(old_path)
                date_dt = emailutil.get_email_date(message)
                subject = emailutil.get_email_subject(message)
                ref = emailutil.find_message_ref(message)
                base = naming.build_base_filename(date_dt, subject, max_filename_chars,
                                                  message_ref=ref)
                new_name = naming.unique_filename(base, existing, max_filename_chars,
                                                  message_ref=ref)
                existing.add(new_name)
                os.rename(old_path, self.path(F.TRIAGE, new_name))
            except (OSError, ValueError) as e:
                failure = GTDError(f"could not ingest '{old_name}': {e}")
                break
            moved.append((old_name, new_name, ref or ""))
        stamp = self.today().isoformat()
        seeds = {}
        for _, new_name, ref in moved:
            seed = {"ds_triage": stamp}
            if ref:
                seed["message_ref"] = ref
            seeds[new_name] = seed
        self.sync_metadata(seeds)
        if failure:
            raise failure
        return moved

    # ------------------------------------------------------------ alloc / close

    def alloc_refusal(self, filename, dest, rows=None):
        """Why `alloc` would refuse, without doing anything; None if it would not."""
        found = self.find(filename)
        if found is None:
            return self._not_found(filename)
        src, name = found
        if src == dest:
            return None
        field_name = dest.stamp_field
        if field_name:
            existing = ((rows if rows is not None else self.load_metadata())
                        .get(name, {}).get(field_name, ""))
            if existing:
                return GTDError(f"'{name}' already has {field_name} = {existing}; refusing to "
                                f"move it to {dest.dir} and overwrite that date.",
                                "Please handle this email manually.")
        return None

    def alloc(self, filename, dest):
        """`gtd alloc <file> <dest>`. Returns "moved" or "already-there"."""
        src, name = self._locate(filename)
        if src == dest:
            return "already-there"
        refusal = self.alloc_refusal(name, dest)
        if refusal:
            raise refusal
        self._move(name, src, dest)
        if dest.stamp_field:
            self.update_metadata(name, {dest.stamp_field: self.today().isoformat()})
        return "moved"

    def close_refusal(self, filename, rows=None):
        found = self.find(filename)
        if found is None:
            return self._not_found(filename)
        src, name = found
        if src == F.ARCHIVE:
            return GTDError(f"'{name}' is already in {F.ARCHIVE.dir}; refusing to close it again")
        existing = (rows if rows is not None else self.load_metadata()).get(name, {}) \
            .get("ds_archive", "")
        if existing:
            return GTDError(f"'{name}' already has ds_archive = {existing}; refusing to close "
                            "it and overwrite that date.", "Please handle this email manually.")
        return None

    def close(self, filename, other):
        """`gtd close <file> with <other>`: archive it and record what closed it."""
        src, name = self._locate(filename)
        other_name = self._locate(other, "; nothing was changed")[1]
        refusal = self.close_refusal(name)
        if refusal:
            raise refusal
        self._move(name, src, F.ARCHIVE)
        self.update_metadata(name, {"next_action": f"Closed with {other_name}",
                                    "ds_archive": self.today().isoformat()})

    # ------------------------------------------------------------ flags and fields

    def set_flag(self, filename, flag, on):
        """`gtd pin` / `gtd unpin`. Returns False when nothing changed."""
        name = self._locate(filename)[1]
        tokens = (self.load_metadata().get(name, {}).get("flags", "") or "").split()
        if on:
            if flag in tokens:
                return False
            tokens.append(flag)
        else:
            if flag not in tokens:
                return False
            tokens = [t for t in tokens if t != flag]
        self.update_metadata(name, {"flags": " ".join(tokens)})
        return True

    def set_fields(self, filename, changes):
        """`gtd metadata <file> set <field> = <value>` for editable fields.
        Returns the CLI's warning for a due date that is not yyyy-mm-dd."""
        name = self._locate(filename)[1]
        for key in changes:
            if key not in EDITABLE_FIELDS:
                raise GTDError(f"field '{key}' is not editable",
                               "editable fields: " + ", ".join(EDITABLE_FIELDS))
        if not changes:
            return []
        self.update_metadata(name, changes)
        due = changes.get("due_date")
        if due is not None and due.strip() and not is_iso_date(due):
            return [f"due_date '{due.strip()}' is not in yyyy-mm-dd form, so it cannot be "
                    "compared against today's date."]
        return []

    # ------------------------------------------------------------ metadata_check

    def preview_metadata_check(self):
        """What `gtd metadata_check` would report, changing nothing."""
        rows = self.load_metadata()
        on_disk = self.all_existing()
        missing = sorted(n for n in rows if n not in on_disk)
        dangling = []
        for name in sorted(rows):
            for ref in _EML_IN_TEXT.findall(rows[name].get("next_action", "") or ""):
                if ref not in on_disk:
                    dangling.append((name, ref))
        return MetadataCheckReport(missing, dangling)

    def metadata_check(self):
        """`gtd metadata_check`: report, then reconcile metadata.csv."""
        self.ensure_folders()
        report = self.preview_metadata_check()
        self.sync_metadata()
        return report

    # ------------------------------------------------------------ workflow_autofix

    def autofix_records(self, rows=None):
        synced = self.synced(rows if rows is not None else self.load_metadata(),
                             self.all_existing())
        return [{"filename": n, "folder": folder.dir, "row": synced.get(n, {})}
                for folder in F.FOLDERS[1:] for n in self.list_eml(folder)]

    def plan_autofix(self, rows=None):
        """The plan `gtd workflow_autofix` would propose (planning never writes)."""
        fixes, blockers = metrics.plan_autofix(self.autofix_records(rows), today=self.today())
        return AutofixPlan(len(self.list_eml(F.INPUT)), fixes, blockers)

    def apply_autofix(self, plan):
        """Apply a reviewed plan in one batch (the CLI's single yes/no)."""
        pending = len(self.list_eml(F.INPUT))
        if pending:
            raise GTDError(f"{pending} file(s) are still in {F.INPUT.dir}; workflow_autofix "
                           "cannot run.",
                           f"Ingest them into {F.TRIAGE.dir} first, then try again.")
        if plan.blockers:
            raise GTDError(f"{len(plan.blockers)} email(s) cannot be fixed automatically and "
                           "must be resolved by hand first.")
        self.ensure_folders()
        current = self.all_existing()
        rows = self.synced(self.load_metadata(), current)
        for fx in plan.fixes:
            if fx["filename"] not in current:
                raise GTDError(f"'{fx['filename']}' is not in the workflow")
            rows.setdefault(fx["filename"], blank_row())[fx["field"]] = fx["new"]
        self._write_rows(rows)

    # ------------------------------------------------------------ adding files

    def import_to_input(self, paths):
        """Copy .eml files into 01-input, never overwriting. Returns the names
        used. Ingesting them stays a separate, explicit step."""
        self.ensure_folders()
        added = []
        for src in paths:
            if not src.lower().endswith(".eml") or not os.path.isfile(src):
                continue
            base = os.path.basename(src)
            stem = base[:-4]
            name, n = base, 2
            while os.path.exists(self.path(F.INPUT, name)):
                name = f"{stem}-{n}.eml"
                n += 1
            shutil.copyfile(src, self.path(F.INPUT, name))
            added.append(name)
        return added


def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask
