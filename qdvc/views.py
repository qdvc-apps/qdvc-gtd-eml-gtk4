"""What each sidebar entry lists, and how the message list is sorted and
grouped. Pure (no GTK), so the rules can be tested without a display.

A sidebar entry ("item") is a tuple:

    ("dashboard",) ("performance",) ("calendar",)      overview pages
    ("folder", alias)                                  a workflow folder
    ("due",) ("nodue",) ("pinned",) ("tag", tag)       filters (on-radar emails)
    ("inbox", email or None) ("sent", email or None)   account views (all folders)
    ("attention", key) ("project", name) ("day", date) results
"""

from . import folders as F
from .records import date_bucket

OVERVIEW = [("dashboard",), ("performance",), ("calendar",)]
PAGES = {"dashboard", "performance", "calendar"}
RESULTS = {"attention", "project", "day"}


def is_mailbox(item):
    return item is not None and item[0] not in PAGES


def is_result(item):
    return item is not None and item[0] in RESULTS


def members(item, records, radar, overview=None, calendar=None):
    """The emails an entry lists, before search and sorting."""
    kind = item[0]
    on_radar = [r for r in records if r.folder in radar]
    if kind == "folder":
        return [r for r in records if r.folder.alias == item[1]]
    if kind == "due":
        return [r for r in on_radar if r.due_date]
    if kind == "nodue":
        return [r for r in on_radar if not r.due_date]
    if kind == "pinned":
        return [r for r in on_radar if r.is_pinned]
    if kind == "tag":
        return [r for r in on_radar if r.mentions(item[1])]
    if kind in ("inbox", "sent"):
        attr = "inbox_accounts" if kind == "inbox" else "sent_accounts"
        if item[1] is None:
            return [r for r in records if getattr(r, attr)]
        return [r for r in records
                if any(a["email_address"] == item[1] for a in getattr(r, attr))]
    if kind == "attention" and overview is not None:
        fig = overview.figure(item[1])
        ids = set(fig.ids) if fig else set()
        return [r for r in records if r.id in ids]
    if kind == "project":
        return [r for r in records if r.project == item[1]]
    if kind == "day" and calendar is not None:
        ids = calendar.days.get(item[1], {})
        return [r for r in records if r.id in ids]
    return []


def search(records, text):
    query = (text or "").strip().lower()
    if not query:
        return []
    return [r for r in records if query in r.search_text]


def sort_records(records, key, ascending):
    """Sort by `key`; ties newest first, then by id, as the macOS edition."""
    def primary(r):
        if key == "correspondent":
            return r.list_correspondent.casefold()
        if key == "subject":
            return r.subject.casefold()
        if key == "folder":
            return r.folder.number
        if key == "due":
            # Real dates first, then free text, then none.
            if r.due_day is not None:
                return "0" + r.due_day.isoformat()
            return "2" if not r.due_date else "1" + r.due_date.lower()
        return r.date.timestamp()

    tiebreak = sorted(records, key=lambda r: (-r.date.timestamp(), r.id))
    return sorted(tiebreak, key=primary, reverse=not ascending)


def groups(records, key, headings, day_of, today):
    """[(heading, [records])]: date headings when sorted by date, else one
    untitled group."""
    if not headings or key != "date":
        return [("", list(records))]
    out = []
    for r in records:
        title = date_bucket(day_of(r.date), today)
        if out and out[-1][0] == title:
            out[-1][1].append(r)
        else:
            out.append((title, [r]))
    return out


def title(item, wcfg=None, overview=None, fmt=None):
    kind = item[0]
    if kind in ("dashboard", "performance", "calendar"):
        return kind.capitalize()
    if kind == "folder":
        return F.BY_ALIAS[item[1]].title
    if kind == "due":
        return "Due Date Set"
    if kind == "nodue":
        return "No Due Date"
    if kind == "pinned":
        return "Pinned"
    if kind == "tag":
        return item[1]
    if kind in ("inbox", "sent"):
        base = "Inbox" if kind == "inbox" else "Sent"
        name = wcfg.account_name(item[1]) if (wcfg and item[1]) else None
        return f"{base} — {name}" if name else base
    if kind == "attention":
        fig = overview.figure(item[1]) if overview else None
        return fig.label if fig else item[1]
    if kind == "project":
        return f"Project: {item[1]}"
    if kind == "day":
        return fmt.date_text(item[1], style="long") if fmt else item[1].isoformat()
    return ""


def move_refusal(record, dest, workspace, rows):
    """Why `record` cannot be moved to `dest` (short, for menus), or None."""
    if record.folder == F.INPUT:
        return "Ingest it first"
    if record.folder == dest:
        return "Already here"
    if dest == F.INPUT:
        return "Emails are never returned to Input"
    field = dest.stamp_field
    value = rows.get(record.filename, {}).get(field, "") if field else ""
    if value:
        return f"Already has {field} = {value}"
    err = workspace.alloc_refusal(record.filename, dest, rows) if workspace else None
    return err.message if err else None


def close_refusal(record, workspace, rows):
    if record.folder == F.INPUT:
        return "Ingest it first"
    err = workspace.close_refusal(record.filename, rows) if workspace else None
    return err.message if err else None


ANNOTATIONS_REFUSAL = ("Ingest this email first: ingestion renames the file, so its "
                       "annotations would be lost.")
