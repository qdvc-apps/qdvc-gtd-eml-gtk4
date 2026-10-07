"""
Workflow analytics: the pure (I/O-free) logic behind `gtd.py workflow_autofix`
and `gtd.py generate_dashboard`.

Two concerns live here, kept deliberately free of filesystem/printing side
effects so both commands share one source of truth (and so it is easy to test):

  1. AUTOFIX — given each email's folder and its metadata.csv ds_* date-stamps,
     work out the corrective mutations the workflow implies but that are not yet
     recorded. See `plan_autofix`. Two phases:
       * folder/stamp consistency: an email physically in a folder whose arrival
         date-stamp is unset gets that stamp proposed as *today* (someone filed
         it by hand without going through `alloc`/`close`);
       * date backfills: the logical-progression fixes (a resolved email must
         have been triaged and actioned first, etc.), per the rules in README /
         MAINTENANCE.

  2. METRICS — once the ds_* stamps are complete/consistent (i.e. after autofix
     has been applied), classify each email as ongoing / resolved / weird and
     compute the response-time metrics ttS, Td, Wd, tttR plus the Sankey flow.
     See `compute_email_metrics`.

The "date the email took place" (E) is parsed from the yyyy-mm-dd prefix of the
slugified filename (which ingestion derived from the email headers); it is the
authoritative persisted value, so we read it back from the filename rather than
re-parsing headers here. See `email_date_from_filename`.
"""

import re
from datetime import date

from . import config

# Resolution date-stamp fields, and the folders that own each ds_* stamp. These
# mirror config.DATE_STAMP_FIELD_BY_DIR but are spelled out here in the order
# the workflow visits them so the analytics code can rely on a stable ordering.
RESOLUTION_FIELDS = ["ds_delegated", "ds_reference", "ds_archive"]
STAGE_FIELDS = ["ds_triage", "ds_actionable"] + RESOLUTION_FIELDS

# Human-readable label for each stage/resolution field, used in the Sankey.
FIELD_LABEL = {
    "ds_triage": "triage",
    "ds_actionable": "actionable",
    "ds_delegated": "delegated",
    "ds_reference": "reference",
    "ds_archive": "archive",
}

# yyyy-mm-dd at the very start of a filename (the ingestion naming convention).
_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def email_date_from_filename(filename):
    """
    Parse the yyyy-mm-dd date prefix out of a slugified .eml filename and return
    it as a datetime.date, or None if the name does not start with one.

    Example:
        email_date_from_filename("2026-06-03-project-pudding.eml")
        # -> date(2026, 6, 3)
        email_date_from_filename("no-date-here.eml")  # -> None
    """
    m = _DATE_PREFIX.match(filename or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_ds(value):
    """
    Parse a ds_* metadata value (a yyyy-mm-dd string) into a datetime.date, or
    None if empty/blank/unparseable.

    Example:
        parse_ds("2026-06-03")  # -> date(2026, 6, 3)
        parse_ds("")            # -> None
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def resolution_of(row):
    """
    Given a metadata row (dict of ds_* string values), return the "final resting"
    resolution date — the LATEST of any set resolution stamps (ds_delegated /
    ds_reference / ds_archive) — as a datetime.date, or None if the email has no
    resolution stamp (i.e. it is still ongoing). This latest date is what the
    metrics (Wd, tttR) treat as the moment of resolution.

    The individual resolution states an email passed through (and their order)
    are handled by compute_email_metrics via RESOLUTION_FIELDS; this helper only
    answers "when did it finally come to rest".

    Example:
        resolution_of({"ds_reference": "2026-06-05", "ds_archive": "2026-06-09"})
        # -> date(2026, 6, 9)
        resolution_of({})  # -> None
    """
    dates = [parse_ds(row.get(f)) for f in RESOLUTION_FIELDS if parse_ds(row.get(f))]
    return max(dates) if dates else None


def earliest_resolution_date(row):
    """
    Return the EARLIEST of any set resolution stamps as a datetime.date, or None
    if none is set. Used only for backfilling missing ds_triage / ds_actionable
    (a resolved email must have been triaged/actioned no later than it was first
    resolved). Contrast resolution_of(), which uses the LATEST for metrics.

    Example:
        earliest_resolution_date({"ds_reference": "2026-06-05",
                                  "ds_archive": "2026-06-09"})
        # -> date(2026, 6, 5)
    """
    dates = [parse_ds(row.get(f)) for f in RESOLUTION_FIELDS if parse_ds(row.get(f))]
    return min(dates) if dates else None


def folder_stamp_fixes(folder, row, today):
    """
    Phase 1 of autofix — folder/stamp consistency for a single email. Given the
    canonical folder the .eml physically sits in and its metadata row, return a
    dict {field: today} for every arrival stamp the folder implies but that is
    not yet set. An email filed by hand (not via `alloc`/`close`) can land in a
    folder without its ds_* ever being written; we propose stamping it *today*.

    Only the stamp OWNED by the current folder is considered here (triage →
    ds_triage, actionable → ds_actionable, delegated → ds_delegated, etc.);
    earlier-stage gaps are left to the Phase 2 date backfills, which can date
    them precisely rather than as "today". 01-input has no stamp, so a file
    there yields no fix (though `generate_dashboard`/`workflow_autofix` refuse to
    run at all while 01-input is non-empty).

    Example:
        folder_stamp_fixes("04-delegated", {"ds_triage": "2026-06-02"}, today)
        # -> {"ds_delegated": today}   # in delegated but never stamped
        folder_stamp_fixes("04-delegated",
                           {"ds_delegated": "2026-06-05"}, today)  # -> {}
    """
    field = config.DATE_STAMP_FIELD_BY_DIR.get(folder)
    if field is None:
        return {}
    if parse_ds(row.get(field)):
        return {}
    return {field: today.isoformat()}


def date_backfill_fixes(row, today, in_triage=False, in_actionable=False):
    """
    Phase 2 of autofix — logical-progression date backfills for a single email,
    computed against the row AS IT WILL BE after Phase 1 (so pass a row that
    already has any folder/stamp fixes applied). Returns a dict {field: value}
    of stamps to write. The rules (see README / MAINTENANCE §"generate_dashboard"):

      * any status: a set ds_actionable with a blank ds_triage backfills
        ds_triage from the listed ds_actionable (reaching actionable implies
        having been triaged) — applied whether ongoing or resolved.
      * resolved (has a resolution stamp):
          - missing BOTH ds_triage and ds_actionable -> set both to the EARLIEST
            resolution date;
          - missing ds_actionable only -> set it to the EARLIEST resolution date.
      * ongoing (no resolution stamp):
          - missing ds_triage AND in the triage folder -> set ds_triage today;
          - missing ds_actionable AND in the actionable folder -> set BOTH
            ds_triage and ds_actionable today (an actionable email is triaged).

    An ongoing email that is missing ds_triage but is NOT in triage/actionable
    is left alone here: it is a folder/stamp contradiction surfaced as a hard
    block by the command layer, never silently dated.

    Example:
        date_backfill_fixes({"ds_actionable": "2026-06-05",
                            "ds_archive": "2026-06-10"}, today)
        # -> {"ds_triage": "2026-06-05"}
    """
    fixes = {}
    triage = parse_ds(row.get("ds_triage"))
    actionable = parse_ds(row.get("ds_actionable"))
    earliest_res = earliest_resolution_date(row)

    # Status-independent rule: if an email reached actionable it was necessarily
    # triaged first, so a set ds_actionable with a blank ds_triage backfills
    # ds_triage from the listed ds_actionable. This holds whether the email is
    # still ongoing or has since been resolved.
    if actionable is not None and triage is None:
        fixes["ds_triage"] = row.get("ds_actionable", "").strip()
        triage = actionable  # reflect the fix for the logic below

    if earliest_res is not None:  # resolved
        if triage is None and actionable is None:
            stamp = earliest_res.isoformat()
            fixes["ds_triage"] = stamp
            fixes["ds_actionable"] = stamp
        elif actionable is None:  # has triage, missing actionable
            fixes["ds_actionable"] = earliest_res.isoformat()
        # (actionable set, triage missing) already handled above.
    else:  # ongoing
        if actionable is None and in_actionable:
            stamp = today.isoformat()
            if triage is None:
                fixes["ds_triage"] = stamp
            fixes["ds_actionable"] = stamp
        elif triage is None and in_triage:
            fixes["ds_triage"] = today.isoformat()

    return fixes


def plan_autofix(records, today=None):
    """
    Plan all autofix mutations across the workflow. `records` is a list of dicts,
    one per tracked .eml, each with keys: "filename", "folder" (canonical), and
    "row" (its metadata dict of ds_* string values — other columns are ignored).

    Returns (fixes, blockers):
      * fixes    — list of {"filename", "folder", "field", "old", "new",
                   "phase"} entries, one per proposed stamp write. "phase" is
                   "folder-consistency" (Phase 1) or "date-backfill" (Phase 2).
                   Phase 1 fixes are computed first and folded into the row so
                   Phase 2 sees them.
      * blockers — list of {"filename", "folder", "reason"} for emails that
                   cannot be resolved automatically and need manual attention:
                   an ONGOING email (no resolution stamp) that is missing
                   ds_triage yet sits in neither triage nor actionable — i.e. it
                   was hand-filed into a resolved folder without a resolution
                   date, a genuine contradiction we refuse to invent history for.

    The caller is responsible for the 01-input guard (this function assumes the
    records passed are the tracked set) and for actually writing the fixes.

    Example:
        plan_autofix([{"filename": "x.eml", "folder": "04-delegated",
                       "row": {"ds_triage": "2026-06-02"}}], today)
        # -> ([{... ds_delegated today ...}], [])
    """
    today = today or date.today()
    fixes = []
    blockers = []

    for rec in records:
        filename = rec["filename"]
        folder = rec["folder"]
        row = dict(rec["row"])  # shallow copy; we fold Phase 1 fixes into it

        # Phase 1: folder/stamp consistency (stamp the current folder's arrival
        # date if unset).
        for field, new in folder_stamp_fixes(folder, row, today).items():
            fixes.append({"filename": filename, "folder": folder, "field": field,
                          "old": row.get(field, ""), "new": new,
                          "phase": "folder-consistency"})
            row[field] = new  # so Phase 2 sees the corrected row

        in_triage = folder == config.TRIAGE_DIR
        in_actionable = folder == config.ACTIONABLE_DIR

        # Blocker: ongoing, missing ds_triage, and not in triage/actionable.
        # (A resolved email never blocks: its stamps can be backfilled.)
        if earliest_resolution_date(row) is None and parse_ds(row.get("ds_triage")) is None \
                and not in_triage and not in_actionable:
            blockers.append({
                "filename": filename, "folder": folder,
                "reason": (f"ongoing email in {folder} with no ds_triage and no "
                           f"resolution stamp — cannot infer its dates"),
            })
            continue  # do not attempt Phase 2 on a blocked email

        # Phase 2: logical-progression date backfills.
        for field, new in date_backfill_fixes(
                row, today, in_triage=in_triage, in_actionable=in_actionable).items():
            fixes.append({"filename": filename, "folder": folder, "field": field,
                          "old": row.get(field, ""), "new": new,
                          "phase": "date-backfill"})

    return fixes, blockers


def compute_email_metrics(filename, row):
    """
    Classify one email and compute its response-time metrics, assuming autofix
    has already been applied (so ds_triage / ds_actionable are present wherever
    the logical progression requires them). `row` is its metadata ds_* dict.

    Returns a dict:
        {"filename", "email_date" (date|None), "triage_date" (date|None),
         "actionable_date" (date|None), "resolution_date" (date|None),
         "status", "metrics", "stages"}
    where:
      * "status" is "ongoing", "resolved", or "weird";
      * "metrics" is a dict with any of "ttS", "Td", "Wd", "tttR" (integer days)
        that apply — empty for weird emails (excluded from ALL metrics);
      * "stages" is the ordered list of (label, date) the email passed through,
        starting with ("took place", E), used to build the Sankey; empty for
        weird emails.

    "weird" means the normal progression E <= ds_triage <= ds_actionable <=
    resolution is violated somewhere (each comparison only where both sides are
    set) — including violations caused by timezone skew in the header date.

    Example:
        compute_email_metrics("2026-06-01-x.eml",
            {"ds_triage": "2026-06-03", "ds_actionable": "2026-06-05",
             "ds_archive": "2026-06-10"})
        # -> {... "status": "resolved",
        #     "metrics": {"ttS": 2, "Td": 2, "Wd": 5, "tttR": 9}, ...}
    """
    E = email_date_from_filename(filename)
    t = parse_ds(row.get("ds_triage"))
    a = parse_ds(row.get("ds_actionable"))
    R = resolution_of(row)
    resolved = R is not None

    # The resolution stamps this email carries, in the implied workflow order
    # delegated -> reference -> archive (actionability decays left to right,
    # matching the folder numbering). Each is a (label, date) the email passed
    # through on its way to its final resting state.
    res_stages = [(FIELD_LABEL[f], parse_ds(row.get(f)))
                  for f in RESOLUTION_FIELDS if parse_ds(row.get(f))]

    # Weirdness: the whole progression must be non-decreasing in date, in the
    # order E -> triage -> actionable -> delegated -> reference -> archive (each
    # only where set). This now also flags resolution stamps that are out of
    # workflow order — e.g. an archived email later sent to reference, or a
    # reference email later sent to delegated. A missing email date (unparseable
    # filename) can't anchor the progression, so it is treated as weird.
    chain = [E] if E is not None else []
    if t:
        chain.append(t)
    if a:
        chain.append(a)
    chain.extend(d for _, d in res_stages)  # already in workflow order
    weird = E is None or any(chain[i] > chain[i + 1] for i in range(len(chain) - 1))

    result = {
        "filename": filename,
        "email_date": E,
        "triage_date": t,
        "actionable_date": a,
        "resolution_date": R,
        "metrics": {},
        "stages": [],
        "status": "weird" if weird else ("resolved" if resolved else "ongoing"),
    }
    if weird:
        return result

    m = result["metrics"]
    if t is not None:
        m["ttS"] = (t - E).days
    if t is not None and a is not None:
        m["Td"] = (a - t).days
    if resolved and a is not None:
        m["Wd"] = (R - a).days
    if resolved:
        m["tttR"] = (R - E).days

    # Sankey stages: the (label, date) steps the email passed through, in
    # workflow order — arrival, then triage/actionable if set, then EVERY set
    # resolution stamp in the delegated -> reference -> archive order. The
    # dashboard collapses stages that share a date into one node, keeping the
    # furthest-progressed label; because the resolution stamps are listed in
    # increasing actionability-decay order, a same-date tie among them resolves
    # to the most-resolved state (e.g. delegated+reference same day -> reference;
    # all three same day -> archive).
    stages = [("took place", E)]
    if t is not None:
        stages.append((FIELD_LABEL["ds_triage"], t))
    if a is not None:
        stages.append((FIELD_LABEL["ds_actionable"], a))
    stages.extend(res_stages)
    result["stages"] = stages
    return result
