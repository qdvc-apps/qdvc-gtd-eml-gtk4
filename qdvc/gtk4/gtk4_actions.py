"""The window's commands as `win.*` Gio actions, so one action drives its menu
item, header button, context-menu entry and shortcut, and `update()` greys
them all out together. Accelerators come from ui_prefs.SHORTCUTS (registered
by the application)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import folders as F  # noqa: E402
from .. import views  # noqa: E402

WORKSPACE_ACTIONS = ["close-workspace", "add-emails", "ingest", "refresh", "search",
                     "review-date-stamps", "check-metadata", "open-config", "show-workspace",
                     "go-dashboard", "go-performance", "go-calendar"] + \
                    [f"go-{f.alias}" for f in F.FOLDERS]


def install(win):
    def add(name, callback, param=None):
        action = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
        action.connect("activate", lambda _a, p: callback(p) if param else callback())
        win.add_action(action)
        return action

    add("open-workspace", win.choose_workspace)
    add("open-recent", lambda p: win.open_path(p.get_string()), "s")
    add("close-workspace", win.close_workspace)
    add("add-emails", win.choose_emails_to_add)
    add("ingest", win.ingest)
    add("refresh", win.refresh)
    add("search", win.start_search)
    add("toggle-sidebar", win.toggle_sidebar)
    add("review-date-stamps", win.review_date_stamps)
    add("check-metadata", win.check_metadata)
    add("open-config", win.open_config)
    add("show-workspace", win.show_workspace)
    add("preferences", win.show_preferences)
    add("shortcuts", win.show_shortcuts)
    add("about", win.show_about)
    add("go-dashboard", lambda: win.navigate(("dashboard",)))
    add("go-performance", lambda: win.navigate(("performance",)))
    add("go-calendar", lambda: win.navigate(("calendar",)))
    for f in F.FOLDERS:
        add(f"go-{f.alias}", lambda folder=f: win.navigate(("folder", folder.alias)))
    for f in F.DESTINATIONS:
        add(f"move-to-{f.alias}", lambda folder=f: win.move(win.selected_ids(), folder))
    add("move-menu", win.popup_move_menu)
    add("close-with", win.begin_close)
    add("edit-annotations", win.edit_annotations)
    add("open-message", win.open_messages)
    add("show-in-files", win.show_in_files)
    add("copy-filename", win.copy_filenames)
    add("select-all", win.select_all)

    pin = Gio.SimpleAction.new_stateful("toggle-pin", None, GLib.Variant.new_boolean(False))
    pin.connect("activate", lambda *_: win.toggle_pin())
    win.add_action(pin)

    cfg = win.config
    sort = Gio.SimpleAction.new_stateful("sort-key", GLib.VariantType.new("s"),
                                         GLib.Variant.new_string(cfg.sort_key))
    sort.connect("change-state", lambda a, v: (a.set_state(v), win.set_sort(key=v.get_string())))
    win.add_action(sort)
    for name, key in (("sort-ascending", "sort_ascending"), ("date-headings", "date_headings")):
        act = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(cfg.flag(key)))
        act.connect("change-state", lambda a, v, k=key: (a.set_state(v),
                                                         win.set_sort(**{k: v.get_boolean()})))
        win.add_action(act)


def _enable(win, name, on):
    action = win.lookup_action(name)
    if action is not None:
        action.set_enabled(bool(on))


def update(win):
    """Recompute every action's sensitivity (and the pin toggle's state)."""
    has_ws = win.workspace is not None
    for name in WORKSPACE_ACTIONS:
        _enable(win, name, has_ws)
    _enable(win, "ingest", has_ws and not win.wcfg_error)
    recs = win.selected_records() if has_ws else []
    single = recs[0] if len(recs) == 1 else None
    rows = win.rows
    for f in F.DESTINATIONS:
        _enable(win, f"move-to-{f.alias}",
                any(views.move_refusal(r, f, win.workspace, rows) is None for r in recs))
    _enable(win, "move-menu", bool(recs))
    _enable(win, "close-with", single is not None and
            views.close_refusal(single, win.workspace, rows) is None)
    pinnable = [r for r in recs if r.tracked]
    _enable(win, "toggle-pin", bool(pinnable))
    pin = win.lookup_action("toggle-pin")
    pin.set_state(GLib.Variant.new_boolean(bool(pinnable) and all(r.is_pinned for r in pinnable)))
    _enable(win, "edit-annotations", single is not None and single.tracked)
    for name in ("open-message", "show-in-files", "copy-filename"):
        _enable(win, name, bool(recs))
    _enable(win, "select-all", has_ws)


def move_menu(win):
    """The Move To menu; for one email, an unavailable folder says why."""
    recs = win.selected_records()
    menu = Gio.Menu()
    for f in F.DESTINATIONS:
        title = f.title
        if len(recs) == 1:
            reason = views.move_refusal(recs[0], f, win.workspace, win.rows)
            if reason and reason != "Already here":
                title = f"{f.title} — {reason}"
        menu.append(title, f"win.move-to-{f.alias}")
    return menu


def message_menu(win):
    """The right-click menu for the selected emails."""
    recs = win.selected_records()
    pinnable = [r for r in recs if r.tracked]
    menu = Gio.Menu()
    s1 = Gio.Menu()
    s1.append("Open in Default App", "win.open-message")
    s1.append("Show in Files", "win.show-in-files")
    menu.append_section(None, s1)
    s2 = Gio.Menu()
    s2.append_submenu("Move To", move_menu(win))
    s2.append("Archive", "win.move-to-archive")
    if len(recs) == 1:
        s2.append("Close With…", "win.close-with")
    menu.append_section(None, s2)
    s3 = Gio.Menu()
    unpin = bool(pinnable) and all(r.is_pinned for r in pinnable)
    s3.append("Unpin" if unpin else "Pin", "win.toggle-pin")
    if len(recs) == 1:
        s3.append("Edit Annotations", "win.edit-annotations")
    menu.append_section(None, s3)
    s4 = Gio.Menu()
    s4.append("Copy Filename" if len(recs) < 2 else "Copy Filenames", "win.copy-filename")
    menu.append_section(None, s4)
    return menu


def primary_menu(recent):
    menu = Gio.Menu()
    s1 = Gio.Menu()
    s1.append("Open Workspace…", "win.open-workspace")
    if recent:
        sub = Gio.Menu()
        for path in recent:
            item = Gio.MenuItem.new(_tilde(path), None)
            item.set_action_and_target_value("win.open-recent", GLib.Variant.new_string(path))
            sub.append_item(item)
        s1.append_submenu("Open Recent", sub)
    s1.append("Close Workspace", "win.close-workspace")
    menu.append_section(None, s1)
    s2 = Gio.Menu()
    s2.append("Add Emails to Input…", "win.add-emails")
    s2.append("Ingest Input", "win.ingest")
    menu.append_section(None, s2)
    s3 = Gio.Menu()
    s3.append("Review Date Stamps…", "win.review-date-stamps")
    s3.append("Check Metadata…", "win.check-metadata")
    menu.append_section(None, s3)
    s4 = Gio.Menu()
    s4.append("Open workspace.yml", "win.open-config")
    s4.append("Show Workspace in Files", "win.show-workspace")
    menu.append_section(None, s4)
    s5 = Gio.Menu()
    s5.append("Preferences", "win.preferences")
    s5.append("Keyboard Shortcuts", "win.shortcuts")
    s5.append("About QDVC GTD EML", "win.about")
    menu.append_section(None, s5)
    return menu


def sort_menu():
    from ..ui_prefs import SORT_TITLES
    menu = Gio.Menu()
    s1 = Gio.Menu()
    for key, title in SORT_TITLES:
        item = Gio.MenuItem.new(title, None)
        item.set_action_and_target_value("win.sort-key", GLib.Variant.new_string(key))
        s1.append_item(item)
    menu.append_section("Sort By", s1)
    s2 = Gio.Menu()
    s2.append("Ascending", "win.sort-ascending")
    s2.append("Group by Date", "win.date-headings")
    menu.append_section(None, s2)
    return menu


def _tilde(path):
    import os
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path
