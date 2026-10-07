"""
Email-history splitting: turn ONE plain-text email body into the ordered list of
messages it contains (the current message plus every quoted/forwarded message
below it), so the web UI can render a thread as a series of distinct blocks
rather than one undifferentiated wall of text.

This module is deliberately PURE (no I/O, no email.message objects): it takes
the body text that `emailutil.get_email_body_text(msg, render_html=True)`
already produced and returns plain dicts. That keeps the single source of truth
for body extraction in `emailutil` (see MAINTENANCE §8) and makes the splitting
logic trivial to reason about and test.

The split is a BEST-EFFORT heuristic — mail clients quote in mutually
incompatible ways and there is no standard. Two signals are used:

  1. QUOTE DEPTH — leading ">" markers ("> ", "> > ", ">>"), the one convention
     that is near-universal. An increase in depth starts a new (nested) message.
  2. BOUNDARY MARKERS in the de-quoted text, in the forms clients actually emit:
       * attribution lines ending in "wrote:" (Gmail/Apple Mail), which may be
         wrapped across up to three physical lines;
       * "-----Original Message-----" / "---- Forwarded message ----" rules;
       * "Begin forwarded message:";
       * a long "________________" rule (Outlook) followed by a header block;
       * a bare Outlook-style header block ("From:" followed by "Sent:"/"Date:").
     Where the marker carries headers (From / Date / To / Subject) they are
     parsed out and attached to the message they introduce.

Anything not recognised is simply text appended to the message being built, so a
body with no quoting at all yields exactly one message — never an error.
"""

import re

# A boundary drawn as a rule: "-----Original Message-----", "---- Forwarded
# message ----", and friends (dash count and inner wording both vary).
_RULE_MARKER_RE = re.compile(
    r"^\s*-{2,}\s*(original message|forwarded message|original message follows|"
    r"weitergeleitete nachricht|message d'origine)\s*-{2,}\s*$",
    re.IGNORECASE,
)

# "Begin forwarded message:" (Apple Mail) and the bare "Forwarded message:".
_FORWARD_INTRO_RE = re.compile(
    r"^\s*(begin forwarded message|forwarded message)\s*:?\s*$", re.IGNORECASE)

# Outlook's separator rule: a run of underscores on its own line. On its own this
# is ambiguous (people draw rules), so it only counts as a boundary when a header
# block follows it.
_UNDERSCORE_RULE_RE = re.compile(r"^\s*_{10,}\s*$")

# One line of an RFC-822-ish header block as re-emitted inside a quoted message
# ("From: Jane Doe <jane@x.com>", "Sent: 03 June 2026 09:15", ...).
_HEADER_LINE_RE = re.compile(
    r"^\s*(from|sent|date|to|cc|bcc|subject|reply-to)\s*:\s*(.*)$", re.IGNORECASE)

# An attribution line: anything ending in "wrote:" (plus a couple of common
# non-English equivalents). Length-capped so a paragraph that happens to end in
# "wrote:" is not mistaken for one.
_ATTRIBUTION_RE = re.compile(
    r"^\s*\S.{0,300}?\b(wrote|schrieb|a\s+écrit|escribió)\s*:\s*$", re.IGNORECASE)

# The start of a possibly-wrapped attribution line ("On Wed, 3 Jun 2026 at
# 09:15,\nJane Doe <jane@x.com> wrote:"). Joined with following lines before
# testing against _ATTRIBUTION_RE.
_ATTRIBUTION_OPENER_RE = re.compile(r"^\s*(on|am|le|el)\b", re.IGNORECASE)

# How many physical lines a wrapped attribution may span (including the first).
_ATTRIBUTION_MAX_LINES = 3
# How far past a "From:" line to look for the "Sent:"/"Date:" that confirms a
# header block, and past an underscore rule to look for the block itself.
_HEADER_LOOKAHEAD = 5

# Header keys we keep, normalised. "sent" is Outlook's spelling of "date".
_HEADER_KEY_MAP = {"from": "from", "sent": "date", "date": "date", "to": "to",
                   "cc": "cc", "bcc": "bcc", "subject": "subject",
                   "reply-to": "reply_to"}


def strip_quote_markers(line):
    """
    Split one line into its quote depth and the text with the ">" markers
    removed. Leading whitespace before/between markers is tolerated, and a
    single space after each marker is eaten (the conventional quoting style).

    Example:
        strip_quote_markers("> > hello")  # -> (2, "hello")
        strip_quote_markers(">>hi")       # -> (2, "hi")
        strip_quote_markers("plain")      # -> (0, "plain")
    """
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        # Allow whitespace before the first marker and between markers.
        j = i
        while j < n and line[j] in " \t":
            j += 1
        if j < n and line[j] == ">":
            depth += 1
            i = j + 1
            if i < n and line[i] == " ":
                i += 1
        else:
            break
    return depth, line[i:] if depth else line


def _parse_header_block(lines, start):
    """
    Parse a run of header lines beginning at `start` (blank lines before the
    block are skipped). Returns (headers, next_index): a dict of normalised
    header keys to values, and the index of the first line after the block.
    Folded continuation lines (leading whitespace) are appended to the previous
    header. An empty dict means no header block was found at `start`.

    Example:
        _parse_header_block(["From: Jane <j@x.com>", "Sent: 3 Jun", "", "Hi"], 0)
        # -> ({"from": "Jane <j@x.com>", "date": "3 Jun"}, 2)
    """
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    headers = {}
    last_key = None
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            # A blank line ends the block, but only once we have something.
            if headers:
                i += 1
            break
        match = _HEADER_LINE_RE.match(line)
        if match:
            key = _HEADER_KEY_MAP.get(match.group(1).lower())
            value = match.group(2).strip()
            if key:
                headers[key] = (f"{headers[key]} {value}".strip()
                                if key in headers else value)
                last_key = key
            i += 1
            continue
        if headers and last_key and line[:1] in (" ", "\t"):
            headers[last_key] = f"{headers[last_key]} {line.strip()}".strip()
            i += 1
            continue
        break
    return headers, (i if headers else start)


def _split_attribution(text):
    """
    Pull the sender and date out of an attribution line. The line is
    "On <when>, <who> wrote:" in most clients, so we drop the opener and the
    trailing "wrote:", then split at the last comma — the part carrying an
    "<address>" (or, failing that, the right-hand part) is the sender.

    Example:
        _split_attribution("On Wed, 3 Jun 2026 at 09:15, Jane <j@x.com> wrote:")
        # -> {"date": "Wed, 3 Jun 2026 at 09:15", "from": "Jane <j@x.com>"}
        _split_attribution("Jane Doe wrote:")  # -> {"from": "Jane Doe"}
    """
    body = re.sub(r"\b(wrote|schrieb|a\s+écrit|escribió)\s*:\s*$", "", text.strip(),
                  flags=re.IGNORECASE).strip().rstrip(",")
    body = re.sub(r"^(on|am|le|el)\b\s*", "", body, flags=re.IGNORECASE).strip()
    if not body:
        return {}
    if "," in body:
        left, right = body.rsplit(",", 1)
        left, right = left.strip(), right.strip()
        if "<" in right or "@" in right or not left:
            return {k: v for k, v in (("date", left), ("from", right)) if v}
        if "<" in left or "@" in left:
            return {k: v for k, v in (("from", left), ("date", right)) if v}
    return {"from": body}


def _marker_at(lines, index):
    """
    Test whether a message boundary starts at `lines[index]` (lines are already
    de-quoted). Returns (headers, consumed) — the headers introducing the new
    message (possibly empty) and how many lines the marker itself occupies — or
    None when this is ordinary body text.

    Example:
        _marker_at(["-----Original Message-----", "From: Jane"], 0)
        # -> ({"from": "Jane"}, 2)
    """
    line = lines[index]

    if _RULE_MARKER_RE.match(line) or _FORWARD_INTRO_RE.match(line):
        headers, after = _parse_header_block(lines, index + 1)
        return headers, max(after - index, 1)

    if _UNDERSCORE_RULE_RE.match(line):
        # Only a boundary if a header block actually follows (people draw rules).
        headers, after = _parse_header_block(lines, index + 1)
        if headers:
            return headers, after - index
        return None

    match = _HEADER_LINE_RE.match(line)
    if match and match.group(1).lower() == "from":
        # An Outlook-style header block pasted inline: confirm with a nearby
        # Sent:/Date: so a body line like "From: the archives" is not a boundary.
        window = lines[index + 1:index + 1 + _HEADER_LOOKAHEAD]
        confirmed = any(
            (_HEADER_LINE_RE.match(w) or [None, ""])[1].lower() in ("sent", "date")
            for w in window if _HEADER_LINE_RE.match(w))
        if confirmed:
            headers, after = _parse_header_block(lines, index)
            if headers:
                return headers, after - index
        return None

    # Attribution ("… wrote:"), possibly wrapped over a few physical lines.
    if _ATTRIBUTION_RE.match(line):
        return _split_attribution(line), 1
    if _ATTRIBUTION_OPENER_RE.match(line):
        joined = line.rstrip()
        for extra in range(1, _ATTRIBUTION_MAX_LINES):
            if index + extra >= len(lines):
                break
            nxt = lines[index + extra]
            if not nxt.strip():
                break
            joined = f"{joined} {nxt.strip()}"
            if _ATTRIBUTION_RE.match(joined):
                return _split_attribution(joined), extra + 1
    return None


# --------------------------------------------------------------------------
# Parsing the dates that appear INSIDE quoted headers.
#
# These are not RFC-2822 Date: headers — they are whatever the quoting client
# wrote in prose ("Sent: Tuesday, 3 August 2026 12:34", "On 3 Jun 2026 at 09:15").
# Most carry no timezone at all, so the generated site lets the reader say how to
# interpret them; what we do here is recover the numbers so it CAN.
#
# English month/weekday names only. Anything unrecognised simply yields None and
# the site shows the original text verbatim, which is honest and never wrong.
_MONTHS = {}
for _i, _names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"),
        ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
        ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
        ("november", "nov"), ("december", "dec")], start=1):
    for _name in _names:
        _MONTHS[_name] = _i

_MONTH_ALTERNATION = "|".join(sorted(_MONTHS, key=len, reverse=True))
# "3 August 2026", "03 Jun 2026"
_DMY_RE = re.compile(r"\b(\d{1,2})\s+(" + _MONTH_ALTERNATION + r")\.?,?\s+(\d{4})\b",
                     re.IGNORECASE)
# "August 3, 2026", "Jun 3 2026"
_MDY_RE = re.compile(r"\b(" + _MONTH_ALTERNATION + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?"
                     r"\s+(\d{4})\b", re.IGNORECASE)
# "2026-08-03"
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# "12:34", "12:34:56", "9:15 AM"
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])\.?m\.?\b|"
                      r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", re.IGNORECASE)
# "+0100", "-05:30", "UTC", "GMT", trailing "Z"
_OFFSET_RE = re.compile(r"(?:GMT|UTC)?\s*([+-])(\d{2}):?(\d{2})\b|\b(UTC|GMT|Z)\b",
                        re.IGNORECASE)


def parse_date_text(text):
    """
    Recover the date/time components from a quoted header's date string. Returns
        {"y", "mo", "d"}                      always, when a date was found
        + {"h", "mi"} and optionally {"s"}    when a time was found
        + {"offset": minutes east of UTC}     only when one was stated
    or None when no date could be found at all.

    `offset` being absent is meaningful: it means the string named no timezone, so
    the reader's "treat these as UTC / as local time" setting decides. When an
    offset IS stated it is authoritative and that setting does not apply.

    Example:
        parse_date_text("Tuesday, 3 August 2026 12:34")
        # -> {"y": 2026, "mo": 8, "d": 3, "h": 12, "mi": 34}
        parse_date_text("Wed, 03 Jun 2026 09:15:00 +0100")
        # -> {"y": 2026, "mo": 6, "d": 3, "h": 9, "mi": 15, "s": 0, "offset": 60}
        parse_date_text("last Tuesday")  # -> None
    """
    if not text:
        return None
    text = str(text).strip()

    parts = None
    match = _ISO_RE.search(text)
    if match:
        parts = {"y": int(match.group(1)), "mo": int(match.group(2)),
                 "d": int(match.group(3))}
    if parts is None:
        match = _DMY_RE.search(text)
        if match:
            parts = {"y": int(match.group(3)),
                     "mo": _MONTHS[match.group(2).lower()], "d": int(match.group(1))}
    if parts is None:
        match = _MDY_RE.search(text)
        if match:
            parts = {"y": int(match.group(3)),
                     "mo": _MONTHS[match.group(1).lower()], "d": int(match.group(2))}
    if parts is None:
        return None
    if not (1 <= parts["mo"] <= 12 and 1 <= parts["d"] <= 31):
        return None

    time_match = _TIME_RE.search(text)
    if time_match:
        if time_match.group(1) is not None:      # 12-hour form with am/pm
            hour = int(time_match.group(1)) % 12
            if time_match.group(4).lower() == "p":
                hour += 12
            minute, second = int(time_match.group(2)), time_match.group(3)
        else:
            hour, minute, second = (int(time_match.group(5)), int(time_match.group(6)),
                                    time_match.group(7))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            parts["h"], parts["mi"] = hour, minute
            if second is not None and 0 <= int(second) <= 59:
                parts["s"] = int(second)

    offset_match = _OFFSET_RE.search(text)
    if offset_match:
        if offset_match.group(1):
            sign = 1 if offset_match.group(1) == "+" else -1
            parts["offset"] = sign * (int(offset_match.group(2)) * 60
                                      + int(offset_match.group(3)))
        else:
            parts["offset"] = 0      # UTC / GMT / Z
    return parts


def _tidy(text_lines):
    """Drop leading/trailing blank lines and collapse 3+ blank runs to one."""
    text = "\n".join(text_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def split_history(body_text):
    """
    Split an email body into its ordered list of messages, newest first (the
    message the .eml itself is, then each quoted/forwarded message below it).

    Returns a list of dicts:
        {"depth": int,           # 0 = the message itself, 1+ = nesting level
         "from", "date", "to", "cc", "subject", "reply_to",  # only when parsed
         "text": str}            # that message's own text, de-quoted
    A body with no quoting yields a single depth-0 entry; an empty body yields
    an empty list.

    Example:
        split_history("Thanks!\\n\\nOn Wed, 3 Jun 2026, Jane <j@x.com> wrote:\\n"
                      "> Please review\\n")
        # -> [{"depth": 0, "text": "Thanks!"},
        #     {"depth": 1, "from": "Jane <j@x.com>",
        #      "date": "Wed, 3 Jun 2026", "text": "Please review"}]
    """
    if not (body_text or "").strip():
        return []

    raw_lines = body_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    depths, texts = [], []
    for line in raw_lines:
        depth, text = strip_quote_markers(line)
        depths.append(depth)
        texts.append(text)

    # Each message tracks two depths: "depth" is its nesting level in the thread
    # (what the UI indents by) and "qdepth" is the ">" quote depth of its own
    # body lines, which is how we tell whether a later line belongs to it.
    messages = [{"depth": 0, "qdepth": None, "lines": []}]
    current = messages[0]
    i = 0
    while i < len(texts):
        marker = _marker_at(texts, i)
        if marker is not None:
            headers, consumed = marker
            current = {"depth": max(current["depth"] + 1, depths[i]),
                       "qdepth": None, "lines": []}
            current.update(headers)
            messages.append(current)
            i += consumed
            continue

        line, depth = texts[i], depths[i]
        if not line.strip():
            current["lines"].append(line)  # blank lines carry no depth signal
            i += 1
            continue
        here = current["qdepth"] if current["qdepth"] is not None else depth

        if depth > here:
            # An unannounced jump in quote depth starts a nested message.
            current = {"depth": max(current["depth"] + 1, depth),
                       "qdepth": depth, "lines": [line]}
            messages.append(current)
            i += 1
            continue
        if depth < here:
            # Back out to the most recent message quoted at this depth — that is
            # the one still being quoted (e.g. the sign-off that follows a
            # doubly-quoted block belongs to the singly-quoted message).
            resumed = next((m for m in reversed(messages)
                            if m["qdepth"] == depth), None)
            if resumed is not None:
                current = resumed
        if current["qdepth"] is None:
            current["qdepth"] = depth
        current["lines"].append(line)
        i += 1

    result = []
    for message in messages:
        message.pop("qdepth", None)  # internal bookkeeping only
        if message.get("date"):
            parsed = parse_date_text(message["date"])
            if parsed:
                message["dateParts"] = parsed
        text = _tidy(message.pop("lines"))
        has_headers = any(k in message for k in
                          ("from", "date", "to", "cc", "subject", "reply_to"))
        if not text and not has_headers:
            continue  # e.g. the empty shell left by a body that opens with a marker
        message["text"] = text
        result.append(message)
    return result


def summarise(body_text, max_chars=140):
    """
    One-line preview of the newest message in a body — the first non-empty lines
    of the depth-0 message, whitespace-collapsed and truncated with an ellipsis.
    Used for the message-list pane, so it deliberately ignores quoted history.

    Example:
        summarise("Hi Jane,\\n\\nCan you review this?\\n\\n> old stuff")
        # -> "Hi Jane, Can you review this?"
    """
    messages = split_history(body_text)
    text = ""
    for message in messages:
        if message["text"].strip():
            text = message["text"]
            break
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "\u2026"
    return text
