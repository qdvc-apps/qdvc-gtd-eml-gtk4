"""QDVC GTD EML for GNOME — the pure (toolkit-independent) package.

Nothing under ``qdvc/`` outside ``qdvc/gtk4/`` imports GTK, so the model can
be tested without a display. ``qdvc/gtd_modules/`` is an unmodified copy of
the command-line tool's own modules (see ``tools/vendor_core.py``).
"""

APP_ID = "qdvc.GtdEml"
APP_NAME = "QDVC GTD EML"
PRGNAME = "qdvc-gtd-eml"
__version__ = "0.1.0"
