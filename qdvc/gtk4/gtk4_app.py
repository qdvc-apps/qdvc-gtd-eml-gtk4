"""QDVC GTD EML — the Adw.Application.

Owns the application id (single instance: launching again raises the
window), the accelerators (from ui_prefs.SHORTCUTS), the stylesheet and the
bundled icons. `qdvc-gtd-eml <folder>` opens a workspace; `.eml` files passed
or dropped on the launcher are added to Input.
"""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, PRGNAME  # noqa: E402
from ..config import Config  # noqa: E402
from ..ui_prefs import accelerators  # noqa: E402
from .gtk4_window import MainWindow  # noqa: E402

# The app's own icon (installed by scripts/build-app.sh, and found in
# data/icons when run from a checkout); a stock icon if neither is present.
ICON_NAME = APP_ID
FALLBACK_ICON_NAME = "mail-send-receive"

# Load-bearing for X11: WM_CLASS must match the launcher's StartupWMClass.
# (On Wayland GNOME Shell matches the application id to qdvc.GtdEml.desktop.)
GLib.set_prgname(PRGNAME)
GLib.set_application_name(APP_NAME)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data")


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.config = Config.load()
        self.window = None
        self.icon_name = FALLBACK_ICON_NAME

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        # Bundled icons: data/icons is a theme root laid out like hicolor, so
        # its icons are themed (only themed symbolic icons follow the text
        # colour in dark mode). Works from a checkout and when installed.
        icons = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icons):
            theme.add_search_path(icons)
        self.icon_name = ICON_NAME if theme.has_icon(ICON_NAME) else FALLBACK_ICON_NAME
        Gtk.Window.set_default_icon_name(self.icon_name)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        for name, accels in accelerators().items():
            if accels:
                self.set_accels_for_action(name, accels)

    def _quit(self):
        if self.window is not None:
            self.window.close()
        self.quit()

    def _ensure_window(self):
        if self.window is None:
            self.window = MainWindow(self, self.config, self.icon_name)
            self.window.open_last_workspace()
        return self.window

    def do_activate(self):
        self._ensure_window().present()

    def do_open(self, files, _n, _hint):
        win = self._ensure_window()
        win.present()
        paths = [f.get_path() for f in files if f.get_path()]
        dirs = [p for p in paths if os.path.isdir(p)]
        if dirs:
            win.open_path(dirs[0])
        emls = [p for p in paths if p.lower().endswith(".eml")]
        if emls:
            if win.workspace is None:
                from .gtk4_dialogs import alert
                alert(win, "Open a Workspace First",
                      "Emails can be added to Input once a workspace is open.")
            else:
                win.import_files(emls)


def main(argv=None):
    return Application().run(argv if argv is not None else sys.argv)
