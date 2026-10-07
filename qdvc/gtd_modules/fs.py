"""
Filesystem helpers: creating the folder structure, listing EML files, and
locating a single EML by name across all folders.
"""

import os

from . import config


def ensure_folders(base_dir):
    """
    Create the 5 GTD folders if they don't exist.

    Example:
        ensure_folders("/home/me/gtd")
        # -> creates /home/me/gtd/01-input, .../02-triage, ... if missing
    """
    for folder in config.ALL_DIRS:
        os.makedirs(os.path.join(base_dir, folder), exist_ok=True)


def list_eml_files(base_dir, folder):
    """
    Return a sorted list of .eml filenames (not full paths) in a given folder.

    Example:
        list_eml_files("/home/me/gtd", "02-triage")
        # -> ["2026-06-12-meeting-minutes.eml", "2026-06-13-update.eml"]
    """
    folder_path = os.path.join(base_dir, folder)
    if not os.path.isdir(folder_path):
        return []
    return sorted(
        f for f in os.listdir(folder_path)
        if f.lower().endswith(".eml")
        and os.path.isfile(os.path.join(folder_path, f))
    )


def all_existing_filenames(base_dir):
    """
    Return a set of every .eml filename present across all 5 folders.

    Example:
        all_existing_filenames("/home/me/gtd")
        # -> {"2026-06-12-meeting.eml", "2026-06-13-update.eml"}
    """
    names = set()
    for folder in config.ALL_DIRS:
        names.update(list_eml_files(base_dir, folder))
    return names


def find_eml(base_dir, filename):
    """
    Search the 5 GTD folders for an .eml file and return its full path, or None.
    A ".eml" extension is appended if the user omitted it.

    Example:
        find_eml("/home/me/gtd-eml", "2026-06-03-project-pudding.eml")
        # -> "/home/me/gtd-eml/03-actionable/2026-06-03-project-pudding.eml"
    """
    if not filename.lower().endswith(".eml"):
        filename += ".eml"
    for folder in config.ALL_DIRS:
        candidate = os.path.join(base_dir, folder, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_folder(name):
    """
    Resolve a folder alias or full folder name to its canonical folder name,
    or None if unrecognised. Case-insensitive.

    Example:
        resolve_folder("delegated")     # -> "04-delegated"
        resolve_folder("04-delegated")  # -> "04-delegated"
        resolve_folder("nope")          # -> None
    """
    key = (name or "").strip().lower()
    if key in config.FOLDERS_BY_ALIAS:
        return config.FOLDERS_BY_ALIAS[key]
    if key in config.ALL_DIRS:
        return key
    return None


def move_eml(src_path, base_dir, dest_folder):
    """
    Move an EML file to dest_folder under base_dir. Returns the new full path.
    The destination folder is created if missing. Raises FileExistsError if a
    file of the same name already exists there.

    Example:
        move_eml("/g/03-actionable/x.eml", "/g", "04-delegated")
        # -> "/g/04-delegated/x.eml"
    """
    filename = os.path.basename(src_path)
    dest_dir = os.path.join(base_dir, dest_folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    if os.path.abspath(dest_path) == os.path.abspath(src_path):
        return dest_path  # already there; caller decides what to report
    if os.path.exists(dest_path):
        raise FileExistsError(dest_path)
    os.rename(src_path, dest_path)
    return dest_path
