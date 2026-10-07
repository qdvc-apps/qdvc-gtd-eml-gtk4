"""
Status reporting: building and printing the colour-coded per-folder report
(date/elapsed, subject, filename, correspondents, own-account label, next
action) used by gtd.py.
"""

import os
import re
from datetime import datetime, timezone

from . import config, emailutil, fs

# Colour used for the "└─ PINNED" marker AND for the body block of any pinned
# entry, so a pinned email reads in one consistent colour rather than being
# age-coloured (green/yellow/red).
PINNED_COLOUR = "magenta"


def colour_for_days(elapsed, green_max, yellow_max):
    """
    Return the ANSI colour name for a given elapsed-day count: green if
    days < green_max, else yellow if days < yellow_max, else red.

    Example:
        colour_for_days(1, 2, 14)   # -> "green"
        colour_for_days(5, 2, 14)   # -> "yellow"
        colour_for_days(30, 2, 14)  # -> "red"
    """
    if elapsed < green_max:
        return "green"
    if elapsed < yellow_max:
        return "yellow"
    return "red"


def colourize(text, colour_name, enabled=True):
    """
    Wrap text in ANSI colour codes for the named colour. If disabled (e.g. when
    output is not a TTY), return the text unchanged.

    For multi-line text, the colour code is re-applied at the START of every
    line (and reset at the end of every line). This is necessary because pagers
    like `less -R` and many terminals reset SGR colour state at each newline, so
    a single leading code would only colour the first line.

    Example:
        colourize("hi", "green")          # -> "\\033[32mhi\\033[0m"
        colourize("a\\nb", "green")        # -> "\\033[32ma\\033[0m\\n\\033[32mb\\033[0m"
        colourize("hi", "green", False)   # -> "hi"
    """
    if not enabled or colour_name not in config.COLOURS:
        return text
    start = config.COLOURS[colour_name]
    reset = config.COLOURS["reset"]
    return "\n".join(f"{start}{line}{reset}" for line in text.split("\n"))


def truncate(text, max_chars):
    """
    Truncate text to max_chars, appending a single-character ellipsis (…) if
    cut. A non-positive max_chars disables truncation.

    Example:
        truncate("a very long subject line", 10)  # -> "a very lo…"
        truncate("short", 10)                      # -> "short"
    """
    if max_chars and max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "\u2026"
    return text


def parse_flags(raw):
    """
    Parse the metadata `flags` cell into a lower-cased set of tokens. Tokens may
    be separated by commas and/or whitespace.

    Example:
        parse_flags("pinned")              # -> {"pinned"}
        parse_flags("pinned, urgent")      # -> {"pinned", "urgent"}
        parse_flags("")                    # -> set()
    """
    if not raw:
        return set()
    return {tok.lower() for tok in re.split(r"[,\s]+", raw.strip()) if tok}


def file_report_line(base_dir, folder, filename, exclude=None,
                     max_subject=0, next_action=None, accounts=None,
                     colour_enabled=True, flags=None, today=None):
    """
    Build the report content for one file. Correspondents are prefixed with
    "With: " and capped at 3 (plus a "+ N more" line). Trailing lines (the
    matched own-account, then a PINNED marker, then the next_action) are
    returned SEPARATELY from the age-coloured body so they render distinctly:
    the account and PINNED use their own colour; the next_action is uncoloured.
    The account label comes first; the tree-style indicators (PINNED, next)
    follow it.

    `flags` is the parsed flag set (see parse_flags); when it contains "pinned"
    a "└─ PINNED" marker is emitted (after the account label, before next).

    Returns (body_block, trailing_lines, date_dt, elapsed), where trailing_lines
    is a list of already-formatted strings (possibly empty), e.g.:
        ["                    [Work account]",
         "                    └─ PINNED",
         "                    └─ next: Reply to Jane"]

    Example:
        file_report_line("/home/me/gtd", "02-triage", "x.eml",
                         next_action="Reply", accounts=accts, flags={"pinned"})
        # -> ("2026-06-03  (20d)   Subject\\n...With: Jane <jane@x.com>",
        #     ["                    [Work account]",
        #      "                    └─ PINNED",
        #      "                    └─ next: Reply"], date_dt, 20)
    """
    if today is None:
        today = datetime.now(timezone.utc)
    flags = flags or set()

    account_emails = [a["email_address"] for a in (accounts or [])]
    exclude = list(exclude or []) + account_emails

    path = os.path.join(base_dir, folder, filename)
    message = emailutil.read_eml_message(path)
    date_dt = emailutil.get_email_date(message)
    subject = emailutil.get_email_subject(message) or "(no subject)"
    subject = truncate(subject, max_subject)
    correspondents = emailutil.get_email_correspondents(message, exclude=exclude)
    own_account = emailutil.match_own_account(message, accounts)

    date_str = date_dt.strftime("%Y-%m-%d")
    elapsed = (today.date() - date_dt.date()).days
    elapsed_str = f"({elapsed}d)".rjust(6)  # right-align up to "(9999d)"-ish

    indent = " " * len(f"{date_str} {elapsed_str}   ")
    rows = [f"{date_str} {elapsed_str}   {subject}", f"{indent}{filename}"]
    if correspondents:
        shown = correspondents[:3]
        rows.extend(f"{indent}With: {c}" for c in shown)
        remaining = len(correspondents) - len(shown)
        if remaining > 0:
            rows.append(f"{indent}      + {remaining} more")
    else:
        rows.append(f"{indent}With: (no correspondents)")

    trailing = []
    if own_account:
        label = colourize(f"[{own_account['display_name']}]",
                          own_account["colour"], colour_enabled)
        trailing.append(f"{indent}{label}")
    if "pinned" in flags:
        marker = colourize(f"\u2514\u2500 PINNED", PINNED_COLOUR, colour_enabled)
        trailing.append(f"{indent}{marker}")
    if next_action and next_action.strip():
        trailing.append(f"{indent}\u2514\u2500 next: {next_action.strip()}")

    return "\n".join(rows), trailing, date_dt, elapsed


def strip_ansi(text):
    """
    Remove ANSI SGR colour escape sequences from text, leaving plain characters.
    Used by `search` so matching ignores colour codes injected for display.

    Example:
        strip_ansi("\\033[32mhi\\033[0m")  # -> "hi"
    """
    return re.sub(r"\033\[[0-9;]*m", "", text)


# Reverse-video on/off, used by `search` to highlight the matched substring.
# Reverse video (7) / its specific reset (27) is used rather than a colour so
# the highlight stands out against ANY underlying age/account/pinned colour and,
# crucially, leaves the surrounding foreground colour untouched.
HIGHLIGHT_ON = "\033[7m"
HIGHLIGHT_OFF = "\033[27m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def highlight_matches(block, query, enabled=True):
    """
    Wrap every case-insensitive occurrence of `query` in `block` with
    reverse-video codes so it stands out in the `search` output. Works on text
    that already contains colour codes: the search is performed on the plain
    (ANSI-stripped) characters, but the highlight markers are spliced back into
    the original coloured string at the matching character positions, so the
    underlying colours are preserved. Matches that would span a line break are
    not highlighted (each line is handled independently, matching how the rest
    of the report applies colour per line). If `enabled` is False, or `query` is
    empty, the block is returned unchanged.

    Example:
        highlight_matches("\\033[32mProject\\033[0m", "ject")
        # -> "\\033[32mPro\\033[7mject\\033[27m\\033[0m"
    """
    if not enabled or not query:
        return block
    needle = query.lower()
    return "\n".join(_highlight_line(line, needle) for line in block.split("\n"))


def _highlight_line(line, needle):
    """
    Highlight every case-insensitive occurrence of `needle` (already lower-cased)
    within a single line that may contain ANSI codes. Returns the line with
    reverse-video markers spliced in around each match; unchanged if no match.

    Example:
        _highlight_line("\\033[32mProject\\033[0m", "ject")
        # -> "\\033[32mPro\\033[7mject\\033[27m\\033[0m"
    """
    # Split into a token stream: ANSI codes (opaque, length 0 in plain text) and
    # single literal characters. Build the plain text alongside, recording for
    # each plain-text position which token index the character sits at.
    tokens = []          # list of ("ansi", s) or ("char", s)
    plain_chars = []     # the literal characters, in order
    char_token_idx = []  # token index of each literal char
    pos = 0
    for m in _ANSI_RE.finditer(line):
        for ch in line[pos:m.start()]:
            char_token_idx.append(len(tokens))
            tokens.append(("char", ch))
            plain_chars.append(ch)
        tokens.append(("ansi", m.group()))
        pos = m.end()
    for ch in line[pos:]:
        char_token_idx.append(len(tokens))
        tokens.append(("char", ch))
        plain_chars.append(ch)

    plain = "".join(plain_chars)
    low = plain.lower()
    n = len(needle)

    # Find non-overlapping match spans in plain-text coordinates.
    spans = []
    start = 0
    while True:
        i = low.find(needle, start)
        if i < 0:
            break
        spans.append((i, i + n))
        start = i + n
    if not spans:
        return line

    # Mark the token immediately before each match's first char to be preceded
    # by HIGHLIGHT_ON, and the token of the match's last char to be followed by
    # HIGHLIGHT_OFF.
    on_before = {}   # token_idx -> True (emit HIGHLIGHT_ON before this token)
    off_after = {}   # token_idx -> True (emit HIGHLIGHT_OFF after this token)
    for s, e in spans:
        on_before[char_token_idx[s]] = True
        off_after[char_token_idx[e - 1]] = True

    out = []
    for idx, (kind, s) in enumerate(tokens):
        if idx in on_before:
            out.append(HIGHLIGHT_ON)
        out.append(s)
        if idx in off_after:
            out.append(HIGHLIGHT_OFF)
    return "".join(out)


def build_folder_entries(base_dir, folder, colour_cfg, exclude=None, limit=None,
                         max_subject=0, metadata=None, show_next_action=False,
                         accounts=None):
    """
    Build the ordered list of report blocks for one folder WITHOUT printing.
    Returns a list of (pinned, date_dt, block) tuples in final display order
    (pinned floated to the top, otherwise oldest -> newest), with the archive
    `limit` applied exactly as in the printed report. `report_folder` prints
    these; `search` scans them. Arguments mirror `report_folder`.

    Example:
        build_folder_entries("/home/me/gtd", "03-actionable", (2, 14, True),
                             metadata=meta, show_next_action=True)
        # -> [(False, date_dt, "2026-06-03  (20d)   Subject\\n..."), ...]
    """
    green_max, yellow_max, enabled = colour_cfg
    metadata = metadata or {}
    files = fs.list_eml_files(base_dir, folder)
    entries = []
    for name in files:
        meta = metadata.get(name, {})
        flags = parse_flags(meta.get("flags", ""))
        pinned = "pinned" in flags
        try:
            na = meta.get("next_action", "") if show_next_action else None
            body, trailing, date_dt, elapsed = file_report_line(
                base_dir, folder, name, exclude=exclude,
                max_subject=max_subject, next_action=na,
                accounts=accounts, colour_enabled=enabled, flags=flags)
            body_colour = PINNED_COLOUR if pinned else colour_for_days(elapsed, green_max, yellow_max)
            body = colourize(body, body_colour, enabled)
            block = "\n".join([body] + trailing)
            entries.append((pinned, date_dt, block))
        except Exception as e:
            entries.append((pinned, datetime.min.replace(tzinfo=timezone.utc),
                            f"!! could not read {name}: {e}"))

    entries.sort(key=lambda t: t[1])  # oldest -> newest
    if limit is not None:
        # Keep the most recent `limit`, but never drop pinned entries.
        recent = entries[-limit:]
        pinned_extra = [e for e in entries[:-limit] if e[0]]
        entries = pinned_extra + recent

    # Float pinned entries to the top of the section (stable: preserves the
    # date order established above within each group).
    entries.sort(key=lambda t: not t[0])
    return entries


def report_folder(base_dir, folder, colour_cfg, exclude=None, limit=None,
                  max_subject=0, metadata=None, show_next_action=False,
                  accounts=None):
    """
    Print a report block for a folder, colour-coding each entry by elapsed days.
    If limit is set, show only the most recent `limit` files (by email date).
    `colour_cfg` is (green_max, yellow_max, enabled); `exclude` is a list of
    correspondent addresses to omit; `max_subject` truncates subjects;
    `metadata` is the dict from load_metadata; `show_next_action` toggles the
    next_action branch line (off for the archive); `accounts` is the
    my_own_accounts list used to label which account received each email.

    Entries flagged "pinned" in metadata float to the top of their section
    (and are never dropped by the archive `limit`).

    Example:
        report_folder("/home/me/gtd", "03-actionable", (2, 14, True),
                      metadata=meta, show_next_action=True, accounts=accts)
        # -> prints colour-coded actionable entries with account + next-action
    """
    files = fs.list_eml_files(base_dir, folder)
    entries = build_folder_entries(
        base_dir, folder, colour_cfg, exclude=exclude, limit=limit,
        max_subject=max_subject, metadata=metadata,
        show_next_action=show_next_action, accounts=accounts)

    print(f"\n=== {folder} ({len(files)} file{'s' if len(files) != 1 else ''}) ===")
    if not entries:
        print("   (empty)")
    for _, _, block in entries:
        print(block + "\n")  # trailing newline => blank line after each entry


def print_report(base_dir, archive_n, colour_cfg, accounts=None,
                 max_subject=0, metadata=None, only=None):
    """
    Print the GTD status report. next_action lines are shown for
    triage/actionable/delegated/reference but NOT for the archive. The matched
    own-account label is shown in every segment.

    `only`, if given, is a single canonical folder name (e.g. "03-actionable");
    just that folder is printed. When showing a single folder the archive
    `limit` is not applied (you asked for that folder specifically, so show it
    all); in the full report the archive is still capped at archive_n.

    Example:
        print_report("/home/me/gtd", 10, (2, 14, True), accounts=accts,
                     max_subject=72, metadata=meta)
        # -> prints all segments
        print_report(..., only="03-actionable")
        # -> prints just the actionable segment
    """
    common = dict(accounts=accounts, max_subject=max_subject, metadata=metadata)

    # (folder, show_next_action, limit_in_full_report)
    segments = [
        (config.TRIAGE_DIR, True, None),
        (config.ACTIONABLE_DIR, True, None),
        (config.DELEGATED_DIR, True, None),
        (config.REFERENCE_DIR, True, None),
        (config.ARCHIVE_DIR, False, archive_n),
    ]

    for folder, show_next_action, limit in segments:
        if only is not None and folder != only:
            continue
        # A specifically-requested single folder is shown in full (no limit).
        effective_limit = None if only is not None else limit
        report_folder(base_dir, folder, colour_cfg,
                      show_next_action=show_next_action,
                      limit=effective_limit, **common)


def search_report(base_dir, query, colour_cfg, accounts=None,
                  max_subject=0, metadata=None):
    """
    Search the full `gtd list` report for `query` and print the email entries
    that match. Matching is a case-insensitive substring test over the plain
    (ANSI-stripped) text of each rendered entry, so the literal query — spaces,
    `#`, `@` and all — is matched verbatim rather than split into words.

    Every segment is searched in the same order as the full report
    (triage, actionable, delegated, reference, archive), and matching entries
    are printed under their folder header, in the same colour/format `gtd list`
    would use, with the matched term highlighted (reverse video) when colour is
    enabled. Unlike the printed report, the archive is searched in full (the
    archive_n display cap is not applied) so a match is never hidden. Returns
    the number of matching entries.

    Example:
        search_report("/home/me/gtd", "#quick", (2, 14, True), metadata=meta)
        # -> prints each entry whose rendered text contains "#quick", returns N
    """
    common = dict(accounts=accounts, max_subject=max_subject, metadata=metadata)
    needle = query.lower()
    enabled = colour_cfg[2]
    total = 0

    # (folder, show_next_action) — every segment, archive uncapped (limit=None).
    segments = [
        (config.TRIAGE_DIR, True),
        (config.ACTIONABLE_DIR, True),
        (config.DELEGATED_DIR, True),
        (config.REFERENCE_DIR, True),
        (config.ARCHIVE_DIR, False),
    ]

    for folder, show_next_action in segments:
        entries = build_folder_entries(
            base_dir, folder, colour_cfg, limit=None,
            show_next_action=show_next_action, **common)
        matches = [block for _, _, block in entries
                   if needle in strip_ansi(block).lower()]
        if not matches:
            continue
        print(f"\n=== {folder} ({len(matches)} match"
              f"{'es' if len(matches) != 1 else ''}) ===")
        for block in matches:
            # Highlight the matched term (only when colour is enabled, so plain
            # piped output stays free of escape codes).
            print(highlight_matches(block, query, enabled) + "\n")
        total += len(matches)

    if total == 0:
        print(f"No emails match {query!r}.")
    return total
