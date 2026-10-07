"""
Filename-convention generation: turning an email's date, subject, and optional
message-ref into a unique "yyyy-mm-dd-brief-description[-ref-<nanoid>].eml" name
within a configured character budget.
"""

import re


def slugify(text):
    """
    Convert arbitrary text into a lowercase dash-separated slug containing only
    [a-z0-9-].

    Example:
        slugify("Meeting Minutes - Project Pudding!")
        # -> "meeting-minutes-project-pudding"
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)   # non-alnum -> dash
    text = re.sub(r"-{2,}", "-", text)        # collapse repeats
    return text.strip("-")


def build_base_filename(date_dt, subject, max_chars, message_ref=None):
    """
    Build a "yyyy-mm-dd-brief-description[-ref-<nanoid>]" base name (no
    extension), truncated so that base + ".eml" fits within max_chars. When a
    message_ref is given, the "-ref-<nanoid>" suffix is preserved intact and the
    subject slug is truncated to make room.

    Example:
        build_base_filename(datetime(2026,6,12), "Meeting Minutes", 40)
        # -> "2026-06-12-meeting-minutes"
        build_base_filename(datetime(2026,6,12), "Meeting Minutes", 40, "8FKnj9Tx8d")
        # -> "2026-06-12-meeting-ref-8FKnj9Tx8d"
    """
    date_str = date_dt.strftime("%Y-%m-%d")
    slug = slugify(subject) or "no-subject"

    ref_suffix = f"-ref-{message_ref}" if message_ref else ""
    max_base = max_chars - len(".eml")

    # Reserve space for the date prefix and the (protected) ref suffix; the
    # subject slug gets whatever remains.
    available_for_slug = max_base - len(date_str) - len("-") - len(ref_suffix)
    if available_for_slug < 0:
        # Pathological: ref alone overflows. Keep date + ref, drop the slug.
        base = f"{date_str}{ref_suffix}"
        return base[:max_base].rstrip("-")

    if len(slug) > available_for_slug:
        slug = slug[:available_for_slug].rstrip("-")

    base = f"{date_str}-{slug}{ref_suffix}" if slug else f"{date_str}{ref_suffix}"
    return base.strip("-")


def unique_filename(base, existing, max_chars, message_ref=None):
    """
    Given a base name (no extension) and a set of existing filenames, return a
    unique "<base>.eml". Appends "-N" before the extension on collision, keeping
    the total within max_chars.

    When a message_ref is given, the "-ref-<nanoid>" suffix is protected: the
    "-N" counter is inserted *before* the ref suffix, and any trimming needed to
    fit max_chars eats into the subject slug rather than the ref. This mirrors
    build_base_filename, which also never truncates the ref.

    Example:
        unique_filename("2026-06-12-meeting", {"2026-06-12-meeting.eml"}, 60)
        # -> "2026-06-12-meeting-2.eml"
        unique_filename("2026-06-12-meeting-ref-8FKnj9Tx8d",
                        {"2026-06-12-meeting-ref-8FKnj9Tx8d.eml"}, 40,
                        message_ref="8FKnj9Tx8d")
        # -> "2026-06-12-mee-2-ref-8FKnj9Tx8d.eml"
    """
    candidate = f"{base}.eml"
    if candidate not in existing:
        return candidate

    ref_suffix = f"-ref-{message_ref}" if message_ref else ""
    # The part of the base before the protected ref suffix; the "-N" counter is
    # inserted here so the ref always survives intact.
    if ref_suffix and base.endswith(ref_suffix):
        head = base[: -len(ref_suffix)]
    else:
        ref_suffix = ""
        head = base

    n = 2
    while True:
        suffix = f"-{n}"
        # Reserve room for the counter and the (protected) ref suffix; the head
        # gets whatever remains.
        max_head = max_chars - len(".eml") - len(suffix) - len(ref_suffix)
        trimmed = head[:max_head].rstrip("-") if len(head) > max_head else head
        candidate = f"{trimmed}{suffix}{ref_suffix}.eml"
        if candidate not in existing:
            return candidate
        n += 1
