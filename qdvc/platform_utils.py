"""Launching system apps without GTK (the GTK front-end prefers
Gtk.FileLauncher, which goes through the desktop portal; these are the
fallbacks and the helpers for scripts and tests)."""

import os
import subprocess
import sys


def _spawn(argv):
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def open_with_default_app(path):
    if sys.platform == "darwin":
        return _spawn(["open", path])
    return _spawn(["xdg-open", path])


def open_with_text_editor(path):
    if sys.platform == "darwin":
        return _spawn(["open", "-t", path])
    return _spawn(["xdg-open", path])


def reveal_in_file_manager(path):
    """Show the file selected in the file manager (FileManager1 over D-Bus),
    else open its folder."""
    if sys.platform.startswith("linux"):
        uri = "file://" + os.path.abspath(path)
        ok = _spawn(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                     "--object-path", "/org/freedesktop/FileManager1",
                     "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""])
        if ok:
            return True
    target = path if os.path.isdir(path) else os.path.dirname(path)
    return open_with_default_app(target)
