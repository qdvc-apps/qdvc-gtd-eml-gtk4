#!/usr/bin/env python3
"""Drive the real app headless on a copy of sample-workspace: visit every
page, exercise the main actions, and write screenshots.

    xvfb-run -a python3 tools/screenshots.py --out docs/screenshots
    python3 tools/screenshots.py --smoke        # actions only, no images

Used by tests/test_ui_smoke.py. Preferences and caches go to a temporary
XDG home, so your own settings are never touched. Exits non-zero on any
Python exception raised inside a GTK callback.
"""

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="qdvc-gtd-eml-shots-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("NO_AT_BRIDGE", "1")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from qdvc import folders as F  # noqa: E402
from qdvc.gtk4 import gtk4_app  # noqa: E402

ERRORS = []
_hook = sys.excepthook


def _record(exc_type, exc, tb):
    ERRORS.append("".join(traceback.format_exception(exc_type, exc, tb)))
    _hook(exc_type, exc, tb)


sys.excepthook = _record


def snapshot(win, path):
    paintable = Gtk.WidgetPaintable.new(win)
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return
    texture = win.get_native().get_renderer().render_texture(node, None)
    texture.save_to_png(str(path))
    print("wrote", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "build" / "screenshots"))
    ap.add_argument("--smoke", action="store_true", help="no screenshots")
    ap.add_argument("--dark", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    if not args.smoke:
        out.mkdir(parents=True, exist_ok=True)
    ws_root = os.path.join(TMP, "sample-workspace")
    shutil.copytree(ROOT / "sample-workspace", ws_root)

    app = gtk4_app.Application()
    # Never talk to an instance you have open: run as a separate instance.
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    steps = []

    def shot(name):
        if not args.smoke:
            snapshot(app.window, out / f"{name}.png")

    def step(fn, delay=500):
        steps.append((fn, delay))

    def run_steps():
        if not steps:
            app.window.close()
            app.quit()
            return False
        fn, delay = steps.pop(0)
        try:
            fn()
        except Exception:  # noqa: BLE001
            ERRORS.append(traceback.format_exc())
            traceback.print_exc()
        GLib.timeout_add(delay, run_steps)
        return False

    def activate(name, param=None):
        app.window.activate_action(f"win.{name}", param)

    def select_subject(subject):
        win = app.window
        ids = [r.id for r in win.message_list.ordered_records() if r.subject == subject]
        win.message_list.select_ids(ids)

    def on_activate(a):
        Gtk.Settings.get_default().set_property("gtk-font-name", "Cantarell 11")
        if args.dark:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        a._ensure_window()
        win = a.window
        win.set_default_size(1400, 860)
        win.present()
        step(lambda: win.open_path(ws_root), 1200)
        step(lambda: activate("go-actionable"), 300)
        step(lambda: select_subject("Project pudding: budget"), 600)
        step(lambda: shot("main"), 100)
        step(lambda: activate("go-dashboard"), 500)
        step(lambda: shot("dashboard"), 100)
        step(lambda: activate("go-calendar"), 300)
        step(lambda: win.calendar_page.go(-1) if win.calendar_page.month.month != 9 else None,
             500)
        step(lambda: shot("calendar"), 100)
        step(lambda: activate("go-performance"), 500)
        step(lambda: shot("performance-not-ready"), 100)
        # Ingest, then review and apply the date stamps.
        step(lambda: activate("ingest"), 900)
        step(lambda: activate("review-date-stamps"), 600)
        step(lambda: shot("review-date-stamps"), 100)
        step(lambda: win.get_visible_dialog().action.emit("clicked"), 900)
        step(lambda: activate("go-performance"), 600)
        step(lambda: shot("performance"), 100)
        # Mailbox actions on Triage.
        step(lambda: activate("go-triage"), 400)
        step(lambda: select_subject("Your ticket"), 400)
        step(lambda: activate("toggle-pin"), 700)
        step(lambda: win.save_fields(win.selected_ids()[0],
                                     {"next_action": "Reply to the ticket #urgent",
                                      "due_date": "2026-10-09", "project": "Support"}), 800)
        step(lambda: shot("triage"), 100)
        step(lambda: activate("move-to-actionable"), 800)
        step(lambda: activate("go-actionable"), 400)
        step(lambda: select_subject("Your ticket"), 400)
        step(lambda: activate("close-with"), 600)
        step(lambda: shot("close-with"), 100)
        step(lambda: _close_dialogs(win), 300)
        step(lambda: activate("search"), 200)
        step(lambda: win.search_entry.set_text("pudding"), 700)
        step(lambda: shot("search"), 100)
        step(lambda: activate("go-dashboard"), 300)
        step(lambda: win.open_result(("attention", "no-action")), 600)
        step(lambda: shot("results"), 100)
        step(lambda: activate("check-metadata"), 600)
        step(lambda: _close_dialogs(win), 300)
        step(lambda: activate("preferences"), 600)
        step(lambda: shot("preferences"), 100)
        step(lambda: _close_dialogs(win), 300)
        step(lambda: activate("shortcuts"), 300)
        step(lambda: _close_toplevels(win), 300)
        step(lambda: activate("about"), 300)
        step(lambda: _close_dialogs(win), 300)
        step(lambda: activate("go-triage"), 300)
        step(lambda: win.set_default_size(420, 860), 800)
        step(lambda: shot("narrow"), 100)
        GLib.timeout_add(800, run_steps)

    app.connect("activate", on_activate)
    app.run([sys.argv[0]])
    _verify(ws_root)
    shutil.rmtree(TMP, ignore_errors=True)
    if ERRORS:
        print(f"{len(ERRORS)} error(s) in GTK callbacks", file=sys.stderr)
        return 1
    print("UI run completed without errors")
    return 0


def _close_dialogs(win):
    dialog = win.get_visible_dialog() if hasattr(win, "get_visible_dialog") else None
    while dialog is not None:
        dialog.force_close() if hasattr(dialog, "force_close") else dialog.close()
        dialog = win.get_visible_dialog()


def _close_toplevels(win):
    for w in Gtk.Window.list_toplevels():
        if w is not win and w.get_visible():
            w.close()


def _verify(root):
    """The actions above must have changed the workspace as the CLI would."""
    from qdvc.workspace import Workspace
    ws = Workspace(root)
    rows = ws.load_metadata()
    name = "2026-09-26-your-ticket-ref-HtMl_Ref-01.eml"
    found = ws.find(name)
    problems = []
    if not found or found[0] != F.ACTIONABLE:
        problems.append(f"{name} should be in 03-actionable, is in {found}")
    row = rows.get(name, {})
    for key, want in (("next_action", "Reply to the ticket #urgent"), ("due_date", "2026-10-09"),
                      ("project", "Support"), ("flags", "pinned")):
        if row.get(key) != want:
            problems.append(f"{key} = {row.get(key)!r}, expected {want!r}")
    if not row.get("ds_actionable"):
        problems.append("ds_actionable was not stamped")
    if ws.list_eml(F.INPUT):
        problems.append("01-input should be empty after ingest")
    if problems:
        ERRORS.extend(problems)
        print("Workspace check failed:\n  " + "\n  ".join(problems), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
