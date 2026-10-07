"""What the window shows: one record per email, plus the overview figures.

The fields mirror ``gtd_modules.site.build_email_record`` (the web UI's record),
split in two so a reload stays cheap:

* ``ParsedEmail`` — everything read from the ``.eml`` file itself. Parsing is
  the slow part, so it is cached by (filename, size, mtime) in memory and in a
  disposable index under ``~/.cache/qdvc-gtd-eml/`` (never in the workspace).
  A move between folders keeps size and mtime, so moving re-parses nothing.
* ``EmailRecord`` — the parsed email combined with its ``metadata.csv`` row and
  metrics, rebuilt on every load (cheap).
"""

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime

from . import folders as F
from .gtd_modules import emailutil, metrics, thread
from .gtd_modules.report import colour_for_days, parse_flags
from .gtd_modules.site import STALE_THRESHOLD_DAYS, STAMP_SEQUENCE, _thread_messages
from .workspace import is_iso_date

# Bump when ParsedEmail's fields or their meaning change.
INDEX_VERSION = 1


@dataclass
class ParsedEmail:
    subject: str = ""
    # The Date header as an aware datetime (ISO text in the index), or None
    # when it is missing or unusable (the CLI then uses "now").
    date: object = None
    from_text: str = ""
    to_text: str = ""
    cc_text: str = ""
    bcc_text: str = ""
    # Addresses, for matching against workspace.yml's accounts at load time.
    to_addrs: list = field(default_factory=list)
    cc_addrs: list = field(default_factory=list)
    bcc_addrs: list = field(default_factory=list)
    from_addrs: list = field(default_factory=list)
    correspondents_all: list = field(default_factory=list)   # (entry, address) pairs
    attachments: list = field(default_factory=list)
    body: str = ""
    preview: str = ""
    thread: list = field(default_factory=list)
    error: str = None

    def to_json(self):
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        return d

    @classmethod
    def from_json(cls, d):
        d = dict(d)
        d["date"] = datetime.fromisoformat(d["date"]) if d.get("date") else None
        d["correspondents_all"] = [tuple(p) for p in d.get("correspondents_all", [])]
        return cls(**d)


def _addresses(message, header):
    return [(a or "").lower() for _, a in getaddresses(message.get_all(header, [])) if a]


def parse_file(path):
    """Read one .eml file the way `site.build_email_record` does."""
    try:
        message = emailutil.read_eml_message(path)
    except (OSError, ValueError) as exc:
        return ParsedEmail(error=str(exc))
    raw_date = message.get("Date", "")
    has_date = False
    try:
        has_date = bool(raw_date) and parsedate_to_datetime(raw_date) is not None
    except Exception:  # noqa: BLE001 - mirrors emailutil.get_email_date
        has_date = False
    body = emailutil.get_email_body_text(message, render_html=True)
    # Correspondents as `emailutil.get_email_correspondents` builds them, but
    # with the address kept beside each entry, so the own-account exclusion
    # (which depends on workspace.yml) is applied at load time instead.
    raw_values = []
    for header in ("From", "To", "Cc"):
        raw_values.extend(message.get_all(header, []))
    pairs = []
    for name, addr in getaddresses(raw_values):
        name = emailutil.decode_mime(name)
        entry = f"{name} <{addr}>" if name and addr else (name or addr)
        pairs.append((entry, (addr or "").lower()))
    threads = _thread_messages(body)
    for m in threads:
        m["date_parts"] = thread.parse_date_text(m["date"]) if m.get("date") else None
    return ParsedEmail(
        subject=emailutil.get_email_subject(message),
        date=emailutil.get_email_date(message) if has_date else None,
        from_text=emailutil.format_addresses(message, "From"),
        to_text=emailutil.format_addresses(message, "To"),
        cc_text=emailutil.format_addresses(message, "Cc"),
        bcc_text=emailutil.format_addresses(message, "Bcc"),
        to_addrs=_addresses(message, "To"),
        cc_addrs=_addresses(message, "Cc"),
        bcc_addrs=_addresses(message, "Bcc"),
        from_addrs=_addresses(message, "From"),
        correspondents_all=pairs,
        attachments=emailutil.list_attachments(message),
        body=body,
        preview=thread.summarise(body),
        thread=threads,
    )


def display_name(entry):
    """"Jane Doe <jane@x.com>" -> "Jane Doe"; a bare address stays as it is."""
    if entry.endswith(">") and " <" in entry:
        name = entry[:entry.rfind(" <")].strip()
        return name or entry
    return entry


@dataclass
class EmailRecord:
    id: str                     # "02-triage/2026-09-30-x.eml": unique even across folders
    filename: str
    folder: F.Folder
    parsed: ParsedEmail
    row: dict
    flags: list
    project: str
    next_action: str
    due_date: str
    notes: str
    ref: str
    stamps: list                # [(field, label, date)]
    status: str                 # ongoing / resolved / weird, "untracked" for 01-input
    metrics: dict
    date: datetime              # effective date: the header's, else "now" (as the CLI)
    age_days: object
    age_class: str              # green / yellow / red, "" when unreadable
    account: object             # the first own account (dict) or None
    inbox_accounts: list
    sent_accounts: list
    correspondents: list
    search_text: str

    @property
    def tracked(self):
        return self.folder != F.INPUT

    @property
    def subject(self):
        if self.parsed.error:
            return f"(could not read {self.filename})"
        return self.parsed.subject or "(no subject)"

    @property
    def is_pinned(self):
        return "pinned" in self.flags

    @property
    def is_unreadable(self):
        return self.parsed.error is not None

    @property
    def due_day(self):
        if is_iso_date(self.due_date):
            try:
                return date.fromisoformat(self.due_date.strip())
            except ValueError:
                return None
        return None

    @property
    def has_attachments(self):
        return bool(self.parsed.attachments)

    @property
    def list_correspondent(self):
        if self.correspondents:
            return display_name(self.correspondents[0])
        if self.parsed.from_text:
            return display_name(self.parsed.from_text)
        return "(no correspondents)"

    def is_overdue(self, today):
        due = self.due_day
        return due is not None and due < today

    def mentions(self, tag):
        return bool(tag) and tag.lower() in self.next_action.lower()


def build_record(folder, filename, parsed, row, config, computed, now_utc):
    """Combine a parsed email with its metadata row (`site.build_email_record`)."""
    meta = row or {}

    def value(key):
        return (meta.get(key) or "").strip()

    accounts = config.my_own_accounts
    by_email = {a["email_address"]: a for a in accounts}
    tracked = folder != F.INPUT
    status = (computed or {}).get("status") or ("untracked" if not tracked else "")
    if parsed.error:
        effective = now_utc
        age = None
        age_class = ""
        account = None
        inbox, sent, correspondents = [], [], []
    else:
        effective = parsed.date or now_utc
        age = (now_utc.date() - effective.date()).days
        age_class = colour_for_days(age, config.green_max_days, config.yellow_max_days)
        account = None
        for addrs in (parsed.to_addrs, parsed.cc_addrs, parsed.bcc_addrs, parsed.from_addrs):
            account = next((by_email[a] for a in addrs if a in by_email), None)
            if account:
                break
        inbox = _ordered_unique(by_email, parsed.to_addrs + parsed.cc_addrs + parsed.bcc_addrs)
        sent = _ordered_unique(by_email, parsed.from_addrs)
        correspondents = []
        for entry, addr in parsed.correspondents_all:
            if addr and addr in by_email:
                continue
            if entry and entry not in correspondents:
                correspondents.append(entry)
    flags = sorted(parse_flags(meta.get("flags", "")))
    stamps = [(f, label, value(f)) for f, label in STAMP_SEQUENCE if value(f)]
    haystack = [filename, parsed.subject, parsed.from_text, parsed.to_text, parsed.cc_text,
                parsed.bcc_text, value("project"), value("next_action"), value("due_date"),
                value("general_notes"), value("message_ref"), " ".join(flags), parsed.body]
    haystack += parsed.attachments + correspondents
    return EmailRecord(
        id=f"{folder.dir}/{filename}", filename=filename, folder=folder, parsed=parsed,
        row=meta, flags=flags, project=value("project"), next_action=value("next_action"),
        due_date=value("due_date"), notes=value("general_notes"), ref=value("message_ref"),
        stamps=stamps, status=status, metrics=(computed or {}).get("metrics") or {},
        date=effective, age_days=age, age_class=age_class, account=account,
        inbox_accounts=inbox, sent_accounts=sent, correspondents=correspondents,
        search_text="\n".join(haystack).lower(),
    )


def _ordered_unique(by_email, addrs):
    """Accounts among `addrs`, in configured order, de-duplicated (as
    emailutil.match_own_accounts_by_role)."""
    present = set(addrs)
    return [a for key, a in by_email.items() if key in present]


# ---------------------------------------------------------------- overview

@dataclass
class Figure:
    key: str
    label: str
    hint: str
    ids: list

    @property
    def count(self):
        return len(self.ids)


class Overview:
    """The Dashboard's figures (`site.build_overview`)."""

    def __init__(self, records):
        self.counts = {f.alias: 0 for f in F.FOLDERS}
        for r in records:
            self.counts[r.folder.alias] += 1
        self.total = len(records)
        self.tracked = sum(1 for r in records if r.tracked)
        self.statuses = {}
        for r in records:
            key = r.status or "untracked"
            self.statuses[key] = self.statuses.get(key, 0) + 1

        def ids(pred):
            return [r.id for r in records if pred(r)]

        stale = STALE_THRESHOLD_DAYS
        self.attention = [
            Figure("input", "Awaiting ingestion in 01-input",
                   "Ingest them to rename and file them into Triage",
                   ids(lambda r: r.folder == F.INPUT)),
            Figure("stale", f"Open longer than {stale} days",
                   "Still unresolved and aging — decide, delegate, or archive",
                   ids(lambda r: r.tracked and r.status == "ongoing"
                       and (r.age_days or 0) > stale)),
            Figure("no-action", "No next action recorded",
                   "An open email with no next action is where a GTD system leaks",
                   ids(lambda r: r.folder.is_open and not r.next_action)),
            Figure("no-due", "No due date set",
                   "Only counts the open folders — reference and archive rarely need one",
                   ids(lambda r: r.folder.is_open and not r.due_date)),
            Figure("pinned", "Pinned", "Pinned for your attention",
                   ids(lambda r: r.is_pinned)),
            Figure("weird", "Inconsistent date progression",
                   "Excluded from every performance metric until the stamps make sense",
                   ids(lambda r: r.status == "weird")),
            Figure("unreadable", "Could not be parsed", "The .eml file itself could not be read",
                   ids(lambda r: r.is_unreadable)),
        ]
        by_project = {}
        for r in records:
            if r.project:
                by_project.setdefault(r.project, []).append(r.id)
        self.projects = sorted(by_project.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    def figure(self, key):
        return next((f for f in self.attention if f.key == key), None)


# ---------------------------------------------------------------- date headings

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def date_bucket(day, today):
    """The message list's heading for an email received on `day`."""
    if day >= today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    week_start = today - timedelta(days=today.weekday())
    if day >= week_start:
        return "This Week"
    if day >= week_start - timedelta(days=7):
        return "Last Week"
    month_start = today.replace(day=1)
    if day >= month_start:
        return "Earlier This Month"
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    if day >= last_month_start:
        return "Last Month"
    if day.year == today.year:
        return MONTHS[day.month - 1]
    return str(day.year)


# ---------------------------------------------------------------- loading

@dataclass
class Snapshot:
    records: list
    computed: list          # metrics.compute_email_metrics dicts, tracked emails
    autofix: object         # workspace.AutofixPlan
    rows: dict              # metadata.csv as loaded


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "qdvc-gtd-eml")


class RecordLoader:
    """Loads every email of a workspace, reusing parses of unchanged files."""

    def __init__(self, cache_dir=None):
        self._lock = threading.Lock()
        self._cache = {}
        self._cache_dir = cache_dir if cache_dir is not None else _cache_dir()
        self._index_path = None
        self._dirty = False

    def attach(self, root):
        """Use the on-disk index for this workspace (loaded lazily)."""
        digest = hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:16]
        with self._lock:
            self._cache = {}
            self._index_path = os.path.join(self._cache_dir, f"index-{digest}.json")
            try:
                with open(self._index_path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == INDEX_VERSION:
                    for key, value in data.get("entries", {}).items():
                        self._cache[key] = ParsedEmail.from_json(value)
            except (OSError, ValueError, TypeError, KeyError):
                self._cache = {}

    def _key(self, path):
        try:
            st = os.stat(path)
            return f"{os.path.basename(path)}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            return None

    def parsed(self, path):
        key = self._key(path)
        with self._lock:
            hit = self._cache.get(key) if key else None
        if hit is not None:
            return hit
        result = parse_file(path)
        if key:
            with self._lock:
                self._cache[key] = result
                self._dirty = True
        return result

    def save(self, live_keys=None):
        """Write the index, keeping only entries for files that still exist."""
        with self._lock:
            if not self._index_path or not self._dirty:
                return
            entries = {k: v.to_json() for k, v in self._cache.items()
                       if live_keys is None or k in live_keys}
            self._dirty = False
            path = self._index_path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": INDEX_VERSION, "entries": entries}, f)
            os.replace(tmp, path)
        except OSError:
            pass

    def load(self, workspace, config, now_utc=None):
        """Everything the window shows, built from disk."""
        now_utc = now_utc or datetime.now(timezone.utc)
        rows = workspace.load_metadata()
        records, computed, live = [], [], set()
        for folder in F.FOLDERS:
            for name in workspace.list_eml(folder):
                path = workspace.path(folder, name)
                key = self._key(path)
                if key:
                    live.add(key)
                parsed = self.parsed(path)
                m = None
                if folder != F.INPUT:
                    m = metrics.compute_email_metrics(name, rows.get(name, {}))
                    computed.append(m)
                records.append(build_record(folder, name, parsed, rows.get(name), config, m,
                                            now_utc))
        self.save(live)
        plan = workspace.plan_autofix(rows)
        return Snapshot(records, computed, plan, rows)
