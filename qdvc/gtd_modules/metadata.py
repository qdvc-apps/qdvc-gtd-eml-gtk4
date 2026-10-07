"""
metadata.csv handling: read it into a dict and keep it in sync with the EML
files currently present across all folders.
"""

import csv
import os
from datetime import date

from . import config, fs

# Columns a user may read/write via `gtd.py metadata`. eml_filename is the key
# (not editable here); message_ref is derived at ingestion and left read-only to
# avoid desyncing it from the actual filename suffix. The ds_* date-stamp
# columns are also read-only to the user: they are written only by the workflow
# commands (`list`, `alloc`, `close`) via stamp_date_field().
#
# due_date is the one date column the user DOES set: unlike the ds_* stamps
# (which record what already happened, and so are written only by the commands
# that make it happen), a due date is an intention about the future. It is a
# free-text field like the rest — `gtd.py metadata` warns when it does not look
# like yyyy-mm-dd but still stores what you typed.
EDITABLE_FIELDS = ["general_notes", "project", "next_action", "due_date", "flags"]
# Fields set_metadata_value will write. Beyond the user-editable ones, this
# includes the ds_* date-stamp columns, which the workflow commands write
# internally (never via `gtd.py metadata set`).
WRITABLE_FIELDS = EDITABLE_FIELDS + list(config.DATE_STAMP_FIELD_BY_DIR.values())
READABLE_FIELDS = [h for h in config.METADATA_HEADERS if h != "eml_filename"]


def load_metadata(base_dir):
    """
    Read metadata.csv into a dict keyed by eml_filename. Returns {} if the file
    doesn't exist.

    Example:
        load_metadata("/home/me/gtd")
        # -> {"2026-06-03-x.eml": {"general_notes": "", "project": "Pudding",
        #                          "next_action": "Reply to Jane", "message_ref": ""}}
    """
    meta_path = os.path.join(base_dir, config.METADATA_FILE)
    rows = {}
    if os.path.isfile(meta_path):
        with open(meta_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = row.get("eml_filename", "")
                if key:
                    rows[key] = {h: row.get(h, "")
                                 for h in config.METADATA_HEADERS if h != "eml_filename"}
    return rows


def sync_metadata(base_dir, new_values=None):
    """
    Ensure metadata.csv exists at the root and has one row per current .eml file
    (across all folders). Preserves existing column values (notes, project,
    next_action, message_ref); adds blank rows for new files; drops rows for
    files that no longer exist.

    `new_values` optionally seeds columns for freshly-ingested files, e.g.
    {"2026-06-03-x.eml": {"message_ref": "8FKnj9Tx8d"}}. Seeds only apply to
    files not already present in the CSV (no retroactive overwrite).

    Example:
        sync_metadata("/home/me/gtd", {"x.eml": {"message_ref": "8FKnj9Tx8d"}})
        # -> writes/updates /home/me/gtd/metadata.csv
    """
    meta_path = os.path.join(base_dir, config.METADATA_FILE)
    current = fs.all_existing_filenames(base_dir)
    existing_rows = load_metadata(base_dir)
    new_values = new_values or {}
    blank = {h: "" for h in config.METADATA_HEADERS if h != "eml_filename"}

    with open(meta_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=config.METADATA_HEADERS)
        writer.writeheader()
        for name in sorted(current):
            if name in existing_rows:
                prev = existing_rows[name]
            else:
                prev = dict(blank)
                prev.update(new_values.get(name, {}))  # seed new files only
            row = {"eml_filename": name}
            row.update({h: prev.get(h, "")
                        for h in config.METADATA_HEADERS if h != "eml_filename"})
            writer.writerow(row)


def get_metadata_value(base_dir, filename, field):
    """
    Return the stored value of `field` for `filename`, or None if the file has
    no metadata row. Raises KeyError if the field name is not a known column.

    Example:
        get_metadata_value("/g", "2026-06-03-x.eml", "next_action")
        # -> "Reply to Jane"
    """
    if field not in READABLE_FIELDS:
        raise KeyError(field)
    row = load_metadata(base_dir).get(filename)
    if row is None:
        return None
    return row.get(field, "")


def set_metadata_value(base_dir, filename, field, value):
    """
    Set `field` to `value` for `filename` and rewrite metadata.csv. Performs a
    full sync first (so any new/removed EML files are reconciled) and preserves
    every other field. Raises KeyError for an unknown/non-writable field, and
    FileNotFoundError if `filename` is not present anywhere in the workflow.

    Writable fields are metadata.WRITABLE_FIELDS: the user-editable ones plus
    the ds_* date-stamp columns (the latter written only by the workflow
    commands, not `gtd.py metadata set`, which enforces EDITABLE_FIELDS itself).

    Example:
        set_metadata_value("/g", "2026-06-03-x.eml", "next_action", "Do it")
        # -> writes the value, returns None
    """
    if field not in WRITABLE_FIELDS:
        raise KeyError(field)
    if filename not in fs.all_existing_filenames(base_dir):
        raise FileNotFoundError(filename)

    # Reconcile the CSV with what's on disk, then apply the single edit on top.
    sync_metadata(base_dir)
    rows = load_metadata(base_dir)
    rows.setdefault(filename, {h: "" for h in READABLE_FIELDS})[field] = value

    meta_path = os.path.join(base_dir, config.METADATA_FILE)
    with open(meta_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=config.METADATA_HEADERS)
        writer.writeheader()
        for name in sorted(rows):
            row = {"eml_filename": name}
            row.update({h: rows[name].get(h, "") for h in READABLE_FIELDS})
            writer.writerow(row)


def add_flag(base_dir, filename, flag):
    """
    Add `flag` to the space-separated `flags` field for `filename` (a no-op if
    it is already present). Returns True if the flag was added, False if it was
    already there. Raises FileNotFoundError if the file is not in the workflow.

    Example:
        add_flag("/g", "2026-06-03-x.eml", "pinned")  # -> True (now flagged)
    """
    current = get_metadata_value(base_dir, filename, "flags") or ""
    tokens = current.split()
    if flag in tokens:
        return False
    tokens.append(flag)
    set_metadata_value(base_dir, filename, "flags", " ".join(tokens))
    return True


def remove_flag(base_dir, filename, flag):
    """
    Remove `flag` from the space-separated `flags` field for `filename` (a no-op
    if it is absent). Returns True if the flag was removed, False if it was not
    present. Raises FileNotFoundError if the file is not in the workflow.

    Example:
        remove_flag("/g", "2026-06-03-x.eml", "pinned")  # -> True (un-flagged)
    """
    current = get_metadata_value(base_dir, filename, "flags") or ""
    tokens = current.split()
    if flag not in tokens:
        return False
    tokens = [t for t in tokens if t != flag]
    set_metadata_value(base_dir, filename, "flags", " ".join(tokens))
    return True


def today_stamp():
    """
    Return today's local date as a yyyy-mm-dd string — the single source of the
    date the workflow commands (`list`, `alloc`, `close`) stamp into the ds_*
    columns when an email enters a folder.

    Example:
        today_stamp()  # -> "2026-06-30"
    """
    return date.today().isoformat()


def date_stamp_field(folder):
    """
    Return the name of the ds_* date-stamp field for a canonical folder name, or
    None if that folder has no stamp (only 01-input has none). A thin wrapper
    over config.DATE_STAMP_FIELD_BY_DIR so callers don't reach into config.

    Example:
        date_stamp_field("03-actionable")  # -> "ds_actionable"
        date_stamp_field("01-input")       # -> None
    """
    return config.DATE_STAMP_FIELD_BY_DIR.get(folder)


def stamp_date_field(base_dir, filename, folder, date_str):
    """
    Stamp the ds_* date-stamp column for `folder` on `filename` with `date_str`
    (a yyyy-mm-dd string). Raises FileNotFoundError if the file is not in the
    workflow (propagated from set_metadata_value). A folder with no stamp
    (01-input) is a silent no-op. Callers are responsible for the refuse-on-
    overwrite check (see the workflow commands) before calling this.

    Example:
        stamp_date_field("/g", "2026-06-03-x.eml", "03-actionable", "2026-06-30")
        # -> writes ds_actionable = "2026-06-30"
    """
    field = config.DATE_STAMP_FIELD_BY_DIR.get(folder)
    if field is None:
        return
    set_metadata_value(base_dir, filename, field, date_str)
