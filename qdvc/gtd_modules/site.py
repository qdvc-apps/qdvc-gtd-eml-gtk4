"""
Site data model for `gtd.py generate_dashboard`: turn the workflow on disk into
the plain-data structure the generated web UI renders.

This is the read side of the website — the counterpart to `webui.py`, which turns
this data into HTML/CSS/JS. Keeping them apart means the UI never reaches into
the filesystem and this module never emits markup.

What it produces (see `build_site_data`): the Inbox/Sent views (which of your own
accounts sent or received each email), one record per email across ALL six
folders — including `01-input`, whose files are shown as *untracked* (not yet
ingested, so they have no metadata row, no date-stamps and no metrics) — plus the
pre-aggregated overview figures. Every number the UI displays is computed here,
in Python; the JavaScript only decides what to show, never what a figure is (the
same division the performance dashboard already uses).

Email bodies are converted to TEXT (via `emailutil.get_email_body_text(...,
render_html=True)`) and then split into the thread's individual messages by
`thread.split_history`. HTML mail is deliberately NOT carried through as markup:
a real HTML email routinely references remote images and stylesheets, and
embedding that would break the site's no-external-resources guarantee (as well as
letting sender markup loose in the UI).
"""

import os
import re
from datetime import datetime, timezone

from . import config, emailutil, fs, thread
from .report import colour_for_days, parse_flags

# Sidebar sections that map to a folder on disk, in workflow order. `key` is the
# stable identifier used in the UI (and in the URL hash); `label` is what the
# sidebar shows — numbered to mirror the folder names on disk.
FOLDER_SECTIONS = [
    {"key": "input", "dir": config.INPUT_DIR, "label": "1. Input"},
    {"key": "triage", "dir": config.TRIAGE_DIR, "label": "2. Triage"},
    {"key": "actionable", "dir": config.ACTIONABLE_DIR, "label": "3. Actionable"},
    {"key": "delegated", "dir": config.DELEGATED_DIR, "label": "4. Delegated"},
    {"key": "reference", "dir": config.REFERENCE_DIR, "label": "5. Reference"},
    {"key": "archive", "dir": config.ARCHIVE_DIR, "label": "6. Archive"},
]

# Account-role views shown above the mailboxes in the sidebar. These cut across
# the folders: an email appears in Inbox if one of `my_own_accounts` is among its
# recipients, and in Sent if one of them is the sender. Both are omitted entirely
# when no accounts are configured (there would be nothing to match).
ACCOUNT_VIEWS = [
    {"key": "inbox", "label": "Inbox", "role": "recipient"},
    {"key": "sent", "label": "Sent", "role": "sender"},
]

def hashtag_key(tag, taken=None):
    """
    Turn a monitored hashtag into a stable, URL-safe section key. The key is
    derived from the tag itself (not its position) so that a remembered selection
    still means the same tag after the config list is reordered; a collision after
    sanitising falls back to a numeric suffix.

    Example:
        hashtag_key("#urgent")        # -> "tag-urgent"
        hashtag_key("#follow up!")    # -> "tag-follow-up"
    """
    slug = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-") or "tag"
    key = "tag-" + slug
    if taken is not None and key in taken:
        suffix = 2
        while f"{key}-{suffix}" in taken:
            suffix += 1
        key = f"{key}-{suffix}"
    return key


# Folder key -> the `gtd alloc` destination alias for it, so the UI can offer
# "file this elsewhere" commands. Mirrors config.FOLDERS_BY_ALIAS.
ALLOC_ALIASES = [s["key"] for s in FOLDER_SECTIONS]

# The ds_* stamps in workflow order, with the labels the UI's workflow trail uses.
STAMP_SEQUENCE = [
    ("ds_triage", "Triaged"),
    ("ds_actionable", "Actionable"),
    ("ds_delegated", "Delegated"),
    ("ds_reference", "Reference"),
    ("ds_archive", "Archived"),
]

# Ongoing emails older than this are called out on the overview. Matches the
# dashboard's own staleness threshold (dashboard.STALE_THRESHOLD_DAYS).
STALE_THRESHOLD_DAYS = 14


def _thread_messages(body_text):
    """
    Split a body into thread messages and label each for display. Adds "index"
    (0 = newest) and "indent" (nesting depth capped at 4, so a deeply-quoted
    chain stays on screen) to what `thread.split_history` returns.

    Example:
        _thread_messages("Hi\\n\\nOn 1 Jun, J <j@x> wrote:\\n> Ping")
        # -> [{"index": 0, "indent": 0, "text": "Hi", ...},
        #     {"index": 1, "indent": 1, "from": "J <j@x>", ...}]
    """
    messages = thread.split_history(body_text)
    for i, message in enumerate(messages):
        message["index"] = i
        message["indent"] = min(message.get("depth", 0), 4)
    return messages


def build_email_record(base_dir, section, filename, metadata=None, accounts=None,
                       computed=None, today=None):
    """
    Build the UI record for a single email. `section` is a FOLDER_SECTIONS entry,
    `metadata` the loaded metadata.csv dict, `accounts` the my_own_accounts list,
    and `computed` that email's metrics dict (from
    metrics.compute_email_metrics) or None for an untracked/input file.

    An unreadable .eml yields a record carrying "error" instead of headers/body,
    so one bad file never aborts the build (the same tolerance `export.py` has).

    Example:
        build_email_record("/g", FOLDER_SECTIONS[2], "2026-06-03-x.eml",
                           metadata=meta, accounts=accts, computed=m)
        # -> {"file": "2026-06-03-x.eml", "folder": "actionable",
        #     "subject": "Project Pudding", "thread": [...], ...}
    """
    today = today or datetime.now(timezone.utc)
    metadata = metadata or {}
    meta = metadata.get(filename, {})
    tracked = section["dir"] != config.INPUT_DIR

    record = {
        "file": filename,
        "folder": section["key"],
        "folderDir": section["dir"],
        "folderLabel": section["label"],
        "tracked": tracked,
        "flags": sorted(parse_flags(meta.get("flags", ""))),
        "project": (meta.get("project") or "").strip(),
        "nextAction": (meta.get("next_action") or "").strip(),
        "dueDate": (meta.get("due_date") or "").strip(),
        "notes": (meta.get("general_notes") or "").strip(),
        "ref": (meta.get("message_ref") or "").strip(),
        "stamps": [{"field": field, "label": label,
                    "date": (meta.get(field) or "").strip()}
                   for field, label in STAMP_SEQUENCE
                   if (meta.get(field) or "").strip()],
        "status": ((computed or {}).get("status")
                   or ("untracked" if not tracked else "")),
        "metrics": (computed or {}).get("metrics") or {},
    }

    path = os.path.join(base_dir, section["dir"], filename)
    try:
        message = emailutil.read_eml_message(path)
    except (OSError, ValueError) as exc:
        record.update({
            "subject": f"(could not read {filename})",
            "error": str(exc), "date": "", "dateDisplay": "", "ageDays": None,
            "ageClass": "", "epoch": None, "from": "", "to": "", "cc": "", "bcc": "",
            "correspondents": [], "account": "", "accountColour": "", "attachments": [],
            "inbox": [], "sent": [], "preview": "", "thread": [],
        })
        return record

    date_dt = emailutil.get_email_date(message)
    own_account = emailutil.match_own_account(message, accounts)
    by_role = emailutil.match_own_accounts_by_role(message, accounts)
    body = emailutil.get_email_body_text(message, render_html=True)
    age_days = (today.date() - date_dt.date()).days
    record.update({
        "subject": emailutil.get_email_subject(message) or "(no subject)",
        # `date`/`dateDisplay` are the email's own local day and time, kept as a
        # fallback and for Python-side aggregation. `epoch` is the absolute instant
        # the .eml's Date header names: the browser formats THAT in whatever
        # timezone the reader has chosen, and sorts by it — a day-precision string
        # cannot order two emails that arrived on the same day.
        "date": date_dt.strftime("%Y-%m-%d"),
        "dateDisplay": date_dt.strftime("%Y-%m-%d %H:%M"),
        "epoch": int(date_dt.timestamp()),
        "ageDays": age_days,
        "from": emailutil.format_addresses(message, "From"),
        "to": emailutil.format_addresses(message, "To"),
        "cc": emailutil.format_addresses(message, "Cc"),
        "bcc": emailutil.format_addresses(message, "Bcc"),
        "correspondents": emailutil.get_email_correspondents(
            message, exclude=[a["email_address"] for a in (accounts or [])]),
        "account": own_account["display_name"] if own_account else "",
        # my_own_accounts' configured colour, so the web UI can label the account
        # in the same colour the terminal report uses.
        "accountColour": own_account["colour"] if own_account else "",
        # Display names of my own accounts that received / sent this email; these
        # drive the Inbox and Sent views and their per-account filter.
        "inbox": [a["display_name"] for a in by_role["recipient"]],
        "sent": [a["display_name"] for a in by_role["sender"]],
        "attachments": emailutil.list_attachments(message),
        "preview": thread.summarise(body),
        "thread": _thread_messages(body),
    })
    return record


def build_emails(base_dir, metadata=None, accounts=None, computed_by_file=None,
                 today=None, green_max=2, yellow_max=14):
    """
    Build every email record, walking the folders in workflow order (input →
    triage → … → archive) and, within a folder, newest email first — the order a
    mail client lists a mailbox in. Each record gets an "ageClass" of
    green/yellow/red from the same thresholds the terminal report colours by, so
    the UI's age dots agree with `gtd list`.

    Example:
        build_emails("/g", metadata=meta, accounts=accts,
                     computed_by_file=by_file)
        # -> [{"file": "2026-06-09-newest.eml", ...}, ...]
    """
    today = today or datetime.now(timezone.utc)
    computed_by_file = computed_by_file or {}
    records = []
    for section in FOLDER_SECTIONS:
        folder_records = [
            build_email_record(base_dir, section, name, metadata=metadata,
                               accounts=accounts,
                               computed=computed_by_file.get(name), today=today)
            for name in fs.list_eml_files(base_dir, section["dir"])
        ]
        # Newest first by absolute instant, so two emails on the same day order
        # correctly; unreadable files (no date at all) sort to the end.
        folder_records.sort(
            key=lambda r: (r.get("epoch") is not None, r.get("epoch") or 0),
            reverse=True)
        records.extend(folder_records)

    for record in records:
        age = record.get("ageDays")
        record["ageClass"] = (colour_for_days(age, green_max, yellow_max)
                              if age is not None else "")
    return records


def build_overview(emails, today=None):
    """
    Pre-aggregate the overview pane: per-folder counts, workflow status counts,
    and the id lists behind each "needs attention" figure (so clicking a figure
    can open the emails it counted). Every number the overview shows is computed
    here rather than in JavaScript.

    Returns a dict with:
        "counts"      — {folder key: n} plus "tracked" and "total"
        "statuses"    — {ongoing/resolved/weird/untracked: n}
        "attention"   — list of {"key", "label", "count", "files", "hint"}
        "projects"    — [{"name", "count", "files"}] busiest first
        "stale_days"  — the staleness threshold used

    Example:
        build_overview(emails)["counts"]["triage"]  # -> 4
    """
    today = today or datetime.now(timezone.utc)
    counts = {section["key"]: 0 for section in FOLDER_SECTIONS}
    for email in emails:
        counts[email["folder"]] += 1
    counts["tracked"] = sum(1 for e in emails if e["tracked"])
    counts["total"] = len(emails)

    statuses = {}
    for email in emails:
        key = email["status"] or "untracked"
        statuses[key] = statuses.get(key, 0) + 1

    def files(predicate):
        return [e["file"] for e in emails if predicate(e)]

    open_folders = ("triage", "actionable", "delegated")
    stale = files(lambda e: e["tracked"] and e["status"] == "ongoing"
                  and (e["ageDays"] or 0) > STALE_THRESHOLD_DAYS)
    no_action = files(lambda e: e["folder"] in open_folders and not e["nextAction"])
    no_due = files(lambda e: e["folder"] in open_folders and not e["dueDate"])
    pinned = files(lambda e: "pinned" in e["flags"])
    weird = files(lambda e: e["status"] == "weird")
    untriaged = files(lambda e: e["folder"] == "input")
    unreadable = files(lambda e: bool(e.get("error")))

    attention = [
        {"key": "input", "label": "awaiting ingestion in 01-input", "count": len(untriaged),
         "files": untriaged, "hint": "Run `gtd list` to rename and file these into triage."},
        {"key": "stale", "label": f"open longer than {STALE_THRESHOLD_DAYS} days",
         "count": len(stale), "files": stale,
         "hint": "Still unresolved and aging — decide, delegate, or archive."},
        {"key": "no-action", "label": "no next action recorded", "count": len(no_action),
         "files": no_action,
         "hint": "An open email with no next action is where a GTD system leaks."},
        {"key": "no-due", "label": "no due date set", "count": len(no_due),
         "files": no_due,
         "hint": "Only counts the open folders — reference and archive rarely need one."},
        {"key": "pinned", "label": "pinned", "count": len(pinned), "files": pinned,
         "hint": "Flagged with `gtd pin` for your attention."},
        {"key": "weird", "label": "inconsistent date progression", "count": len(weird),
         "files": weird,
         "hint": "Excluded from every performance metric until the stamps make sense."},
        {"key": "unreadable", "label": "could not be parsed", "count": len(unreadable),
         "files": unreadable, "hint": "The .eml file itself could not be read."},
    ]

    by_project = {}
    for email in emails:
        if email["project"]:
            by_project.setdefault(email["project"], []).append(email["file"])
    projects = [{"name": name, "count": len(names), "files": names}
                for name, names in sorted(by_project.items(),
                                          key=lambda kv: (-len(kv[1]), kv[0].lower()))]

    return {"counts": counts, "statuses": statuses, "attention": attention,
            "projects": projects, "stale_days": STALE_THRESHOLD_DAYS}


def build_site_data(base_dir, cfg, metadata=None, computed_by_file=None,
                    today=None, generated_at=None):
    """
    Assemble the whole mail-UI payload: the folder sections (with counts), every
    email record, the overview aggregates, and the small amount of context the UI
    puts in its status bar and command snippets.

    `cfg` is the loaded config (used for the age-colour thresholds and the
    `cli_command` name that appears in generated command snippets).

    Example:
        build_site_data("/g", cfg, metadata=meta, computed_by_file=by_file)
        # -> {"emails": [...], "sections": [...], "overview": {...}, ...}
    """
    today = today or datetime.now(timezone.utc)
    emails = build_emails(base_dir, metadata=metadata,
                          accounts=cfg.get("my_own_accounts"),
                          computed_by_file=computed_by_file, today=today,
                          green_max=cfg.get("green_max_days", 2),
                          yellow_max=cfg.get("yellow_max_days", 14))
    overview = build_overview(emails, today=today)
    sections = [dict(section, count=overview["counts"][section["key"]])
                for section in FOLDER_SECTIONS]

    # One view per monitored hashtag. Membership is a case-insensitive substring
    # match on next_action, evaluated in the browser (it is also narrowed by the
    # on-radar folder scope, which is a client-side setting).
    hashtags = []
    taken = set()
    for tag in (cfg.get("monitored_hashtags") or []):
        key = hashtag_key(tag, taken)
        taken.add(key)
        hashtags.append({"key": key, "label": tag, "tag": tag})

    # The Inbox/Sent views only make sense when own-accounts are configured.
    accounts = [a["display_name"] for a in (cfg.get("my_own_accounts") or [])]
    views = []
    if accounts:
        for view in ACCOUNT_VIEWS:
            key = view["key"]
            views.append(dict(view, count=sum(1 for e in emails if e[key])))

    generated_at = generated_at or datetime.now()
    return {
        "generatedAt": generated_at.strftime("%Y-%m-%d %H:%M"),
        "generatedEpoch": int(generated_at.timestamp()),
        "workingDirectory": base_dir,
        "cli": (cfg.get("cli_command") or "gtd").strip() or "gtd",
        "sections": sections,
        "views": views,
        "hashtags": hashtags,
        "accounts": accounts,
        "destinations": ALLOC_ALIASES,
        "emails": emails,
        "overview": overview,
    }
