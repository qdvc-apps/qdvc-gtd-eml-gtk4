"""
Shared EML helpers: reading messages, decoding MIME headers, extracting body
text (handling base64 / quoted-printable), listing attachments, matching
configured accounts, and finding the message-ref nanoid. Used by both gtd.py
and gtd_email_preview.py.
"""

import re
from datetime import datetime, timezone
from email import message_from_binary_file
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime


def read_eml_message(eml_path):
    """
    Parse an .eml file into an email.message.Message object.

    Example:
        read_eml_message("/home/me/gtd/01-input/foo.eml")
        # -> <email.message.Message object>
    """
    with open(eml_path, "rb") as f:
        return message_from_binary_file(f)


def decode_mime(raw):
    """
    Decode a possibly MIME-encoded header value into a plain string.

    Example:
        decode_mime("=?utf-8?q?Z=C3=B6e?=")  # -> "Zöe"
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw.strip()


def get_email_subject(message):
    """
    Extract the (decoded) subject line from an email Message.

    Example:
        get_email_subject(msg)  # Subject: "Meeting Minutes - Project Pudding"
        # -> "Meeting Minutes - Project Pudding"
    """
    return decode_mime(message.get("Subject", "") or "")


def get_email_date(message):
    """
    Extract the email's Date header as a timezone-aware datetime.
    Falls back to the current time if the header is missing/unparseable.

    Example:
        get_email_date(msg)  # Date: "Wed, 03 Jun 2026 09:15:00 +0000"
        # -> datetime(2026, 6, 3, 9, 15, tzinfo=...)
    """
    raw = message.get("Date", "")
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    return datetime.now(timezone.utc)


def format_date(message):
    """
    Return the Date header reformatted as "YYYY-MM-DD HH:MM" (as given), or the
    raw value if unparseable, or "" if absent. (Display helper for the preview.)

    Example:
        format_date(msg)  # Date: "Wed, 03 Jun 2026 09:15:00 +0000"
        # -> "2026-06-03 09:15"
    """
    raw = message.get("Date", "")
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return raw.strip()


def format_addresses(message, header):
    """
    Return a decoded, comma-joined string of all addresses in a header (handles
    repeated headers and grouped lists). Empty string if the header is absent.

    Example:
        format_addresses(msg, "To")
        # To: "Bob <bob@x.com>, carol@y.com"
        # -> "Bob <bob@x.com>, carol@y.com"
    """
    raw_values = message.get_all(header, [])
    if not raw_values:
        return ""
    parts = []
    for name, addr in getaddresses(raw_values):
        name = decode_mime(name)
        if name and addr:
            parts.append(f"{name} <{addr}>")
        elif addr:
            parts.append(addr)
        elif name:
            parts.append(name)
    return ", ".join(parts)


def get_email_correspondents(message, exclude=None):
    """
    Return a de-duplicated, order-preserving list of correspondent display
    names/addresses drawn from the From, To, and Cc headers (decoded). Any
    correspondent whose email address (case-insensitive) is in `exclude` is
    omitted.

    Example:
        get_email_correspondents(msg, exclude=["bob@y.com"])
        # From: "Jane Doe <jane@x.com>", To: "bob@y.com"
        # -> ["Jane Doe <jane@x.com>"]
    """
    exclude_set = {e.lower() for e in (exclude or [])}

    raw_values = []
    for header in ("From", "To", "Cc"):
        raw_values.extend(message.get_all(header, []))

    people = []
    for name, addr in getaddresses(raw_values):
        if addr and addr.lower() in exclude_set:
            continue
        name = decode_mime(name)
        if name and addr:
            entry = f"{name} <{addr}>"
        else:
            entry = name or addr
        if entry and entry not in people:
            people.append(entry)
    return people


def match_own_account(message, accounts):
    """
    Return the first configured own-account (dict) whose email_address appears
    in the To, Cc, Bcc, or From headers, or None. Header order of preference:
    To, Cc, Bcc, From (i.e. prefer the account that received the mail).

    Example:
        match_own_account(msg, [{"email_address": "me@x.com",
                                  "display_name": "Work", "colour": "yellow"}])
        # To: "me@x.com" -> {"email_address": "me@x.com", ...}
    """
    if not accounts:
        return None
    by_email = {a["email_address"]: a for a in accounts}
    for header in ("To", "Cc", "Bcc", "From"):
        for _, addr in getaddresses(message.get_all(header, [])):
            if addr and addr.lower() in by_email:
                return by_email[addr.lower()]
    return None


def match_own_accounts_by_role(message, accounts):
    """
    Return which configured own-accounts appear in this message, split by role:
        {"recipient": [account, ...], "sender": [account, ...]}
    `recipient` covers the To, Cc and Bcc headers; `sender` covers From. Each list
    holds the account dicts themselves, in configured order, de-duplicated. An
    account can appear in both lists (a note you sent to yourself).

    This is what the generated site's Inbox and Sent views are built from, so it
    deliberately reports every match rather than just the first — unlike
    match_own_account(), which answers the narrower "which account received this"
    question for the report's single label.

    Example:
        match_own_accounts_by_role(msg, [{"email_address": "me@x.com", ...}])
        # From: "me@x.com" -> {"recipient": [], "sender": [{...}]}
    """
    if not accounts:
        return {"recipient": [], "sender": []}
    by_email = {a["email_address"]: a for a in accounts}
    found = {"recipient": [], "sender": []}
    for role, headers in (("recipient", ("To", "Cc", "Bcc")), ("sender", ("From",))):
        seen = set()
        for header in headers:
            for _, addr in getaddresses(message.get_all(header, [])):
                key = (addr or "").lower()
                if key in by_email and key not in seen:
                    seen.add(key)
                    found[role].append(by_email[key])
    return found


def is_attachment(part):
    """
    Decide whether a MIME part is an attachment (vs inline body).

    Example:
        is_attachment(part)  # Content-Disposition: attachment; filename=...
        # -> True
    """
    disposition = (part.get("Content-Disposition") or "").lower()
    if "attachment" in disposition:
        return True
    # A named part that isn't plain/html body text counts as an attachment.
    if part.get_filename():
        return True
    return False


def list_attachments(message):
    """
    Return decoded filenames of all attachment parts.

    Example:
        list_attachments(msg)  # -> ["report.pdf", "image.png"]
    """
    names = []
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            if is_attachment(part):
                fname = part.get_filename()
                names.append(decode_mime(fname) if fname else "(unnamed attachment)")
    return names


def decode_part_text(part):
    """
    Decode a single MIME part's payload to text, honouring its transfer encoding
    (base64, quoted-printable, etc.) and declared charset.

    Example:
        decode_part_text(part)  # base64 payload "SGk=" -> "Hi"
    """
    payload = part.get_payload(decode=True)  # handles base64 / quoted-printable
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def strip_html(html):
    """
    Crudely convert HTML to readable plain text (used only when no text/plain
    part exists).

    Example:
        strip_html("<p>Hi <b>Jane</b></p>")  # -> "Hi Jane"
    """
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(ent, ch)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_email_body_text(message, render_html=False):
    """
    Return the email's text body. Prefers text/plain; falls back to text/html.
    Decodes base64 / quoted-printable transparently. When render_html is True,
    an HTML fallback is converted to readable plain text (used by the preview);
    when False, the raw HTML text is returned (sufficient for ref-scanning).

    Example:
        get_email_body_text(msg)  # base64 body "SGk=" -> "Hi"
    """
    if not message.is_multipart():
        text = decode_part_text(message)
        if render_html and message.get_content_type() == "text/html":
            return strip_html(text)
        return text

    plain, html = None, None
    for part in message.walk():
        if part.is_multipart() or is_attachment(part):
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = decode_part_text(part)
        elif ctype == "text/html" and html is None:
            html = decode_part_text(part)

    if plain and plain.strip():
        return plain
    if html and html.strip():
        return strip_html(html) if render_html else html
    return ""


# Matches e.g. "Message ref. 8FKnj9Tx8d" — case-insensitive, with flexible
# spacing/punctuation after "ref". The nanoid uses the default nanoid alphabet
# (A-Za-z0-9_-). Length 6-32 keeps it from grabbing unrelated long tokens.
MESSAGE_REF_RE = re.compile(
    r"message\s+ref\.?\s*[:\-]?\s*([A-Za-z0-9_-]{6,32})",
    re.IGNORECASE,
)


def find_message_ref(message):
    """
    Return the FIRST message-ref nanoid found in the email body, or None.
    In a thread with several refs (quoted replies), the earliest occurrence in
    the body text is treated as authoritative.

    Example:
        find_message_ref(msg)  # body contains "Message ref. 8FKnj9Tx8d"
        # -> "8FKnj9Tx8d"
    """
    body = get_email_body_text(message)
    if not body:
        return None
    match = MESSAGE_REF_RE.search(body)
    return match.group(1) if match else None
