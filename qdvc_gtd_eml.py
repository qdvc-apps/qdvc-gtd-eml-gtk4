#!/usr/bin/env python3
"""QDVC GTD EML for GNOME — entry point.

    python3 qdvc_gtd_eml.py [workspace-folder]

GTK 4 / libadwaita only (see docs/MAINTENANCE.md, "Deviations"), so there is
no --gtk3 / --gtk4 backend switch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except (ImportError, ValueError) as exc:
        sys.stderr.write(f"qdvc-gtd-eml needs GTK 4 and libadwaita with PyGObject ({exc}).\n"
                         "On Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 "
                         "gir1.2-adw-1 python3-yaml\n"
                         "On Fedora: sudo dnf install python3-gobject gtk4 libadwaita "
                         "python3-pyyaml\n")
        return 1
    from qdvc.gtk4.gtk4_app import main as run
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
