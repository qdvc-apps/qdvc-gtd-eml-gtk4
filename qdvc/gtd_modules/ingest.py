"""
Ingestion: rename new EML files dropped in 01-input per the naming convention
(including a detected message-ref) and move them into 02-triage.
"""

import os

from . import config, emailutil, fs, naming


def ingest_input_files(base_dir, max_chars):
    """
    Rename every .eml in 01-input per the naming convention and move it to
    02-triage. Detects a "Message ref. <nanoid>" in each body and appends
    "-ref-<nanoid>" to the filename. Returns a list of
    (old_name, new_name, message_ref) tuples (message_ref is "" if none found).

    Example:
        ingest_input_files("/home/me/gtd", 60)
        # -> [("inbox123.eml", "2026-06-12-meeting-ref-8FKnj9Tx8d.eml", "8FKnj9Tx8d")]
    """
    moved = []
    existing = fs.all_existing_filenames(base_dir)
    input_path = os.path.join(base_dir, config.INPUT_DIR)
    triage_path = os.path.join(base_dir, config.TRIAGE_DIR)

    for old_name in fs.list_eml_files(base_dir, config.INPUT_DIR):
        old_path = os.path.join(input_path, old_name)
        message = emailutil.read_eml_message(old_path)
        date_dt = emailutil.get_email_date(message)
        subject = emailutil.get_email_subject(message)
        message_ref = emailutil.find_message_ref(message)

        base = naming.build_base_filename(date_dt, subject, max_chars, message_ref=message_ref)
        new_name = naming.unique_filename(base, existing, max_chars, message_ref=message_ref)
        existing.add(new_name)

        os.rename(old_path, os.path.join(triage_path, new_name))
        moved.append((old_name, new_name, message_ref or ""))

    return moved
