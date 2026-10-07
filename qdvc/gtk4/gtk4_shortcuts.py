"""The Keyboard Shortcuts window, built from ui_prefs.SHORTCUTS (the same
table that registers the accelerators)."""

from xml.sax.saxutils import escape

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..ui_prefs import SHORTCUTS  # noqa: E402


def build_shortcuts_window(parent):
    groups = []
    for title, entries in SHORTCUTS:
        items = "".join(
            f'<child><object class="GtkShortcutsShortcut">'
            f'<property name="accelerator">{escape(" ".join(accels))}</property>'
            f'<property name="title">{escape(text)}</property></object></child>'
            for _action, accels, text in entries if accels)
        groups.append(f'<child><object class="GtkShortcutsGroup">'
                      f'<property name="title">{escape(title)}</property>{items}</object></child>')
    xml = ('<interface><object class="GtkShortcutsWindow" id="win">'
           '<property name="modal">1</property>'
           '<child><object class="GtkShortcutsSection">'
           '<property name="section-name">main</property>'
           '<property name="max-height">12</property>'
           + "".join(groups) + "</object></child></object></interface>")
    builder = Gtk.Builder.new_from_string(xml, -1)
    win = builder.get_object("win")
    win.set_transient_for(parent)
    return win
