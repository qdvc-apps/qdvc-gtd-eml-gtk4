"""The main window (the macOS edition's AppModel + ContentView).

Layout: an Adw.OverlaySplitView with the sidebar on the left; on the right a
stack of the mailbox view (an Adw.NavigationSplitView: message list | reading
pane) and the Dashboard, Performance and Calendar pages. Each pane has its own
header bar. Below about 900 px the sidebar overlays; below about 600 px the
list and reading pane become a navigation stack.

Every workflow action goes through qdvc.workspace and is followed by a
reload. Loading runs on a worker thread; parses are cached, so a reload after
an action re-reads only metadata.csv. File monitors reload the window when
the CLI (or anything else) changes the workspace.
"""

import os
import threading
import time
from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from .. import folders as F  # noqa: E402
from .. import views, workspace_config  # noqa: E402
from ..calendar_index import CalendarIndex  # noqa: E402
from ..dates import DateFormatter  # noqa: E402
from ..records import Overview, RecordLoader  # noqa: E402
from ..ui_prefs import is_are, plural  # noqa: E402
from ..workspace import AutofixPlan, GTDError, Workspace  # noqa: E402
from . import gtk4_actions as actions  # noqa: E402
from .gtk4_calendar import CalendarPage  # noqa: E402
from .gtk4_dashboard import DashboardPage  # noqa: E402
from .gtk4_dialogs import (CheckMetadataDialog, CloseWithDialog,  # noqa: E402
                           ReviewDateStampsDialog, alert, confirm)
from .gtk4_message_list import MessageList  # noqa: E402
from .gtk4_performance import PerformancePage  # noqa: E402
from .gtk4_reading_pane import ReadingPane  # noqa: E402
from .gtk4_sidebar import Sidebar  # noqa: E402
from .gtk4_widgets import sidebar_toggle, vbox  # noqa: E402

RELOAD_DELAY_MS = 600


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, config, icon_name):
        super().__init__(application=app, title=APP_NAME)
        self.config = config
        self.icon_name = icon_name
        self.set_icon_name(icon_name)
        self.workspace = None
        self.wcfg = workspace_config.WorkspaceConfig()
        self.wcfg_error = None
        self.records, self.by_id, self.computed, self.rows = [], {}, [], {}
        self.autofix = AutofixPlan()
        self.overview = Overview([])
        self.calendar = CalendarIndex()
        self.item = ("folder", "triage")
        self.result_item = None
        self.search_text = ""
        self.loader = RecordLoader()
        self._generation = 0
        self._loading = False
        self._reload_again = False
        self._has_loaded = False
        self._reload_source = None
        self._monitors = []
        self._banners = []
        self._fallback = None
        self.fmt = self._formatter()

        # A minimum size is required with breakpoints (360 × 294 is GNOME's
        # phone-sized minimum).
        self.set_size_request(360, 294)
        self.set_default_size(int(config.get("window_width")), int(config.get("window_height")))
        if config.flag("window_maximized"):
            self.maximize()
        actions.install(self)
        self.toasts = Adw.ToastOverlay()
        self.main = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.main)
        self.set_content(self.toasts)
        self._build_welcome()
        self._build_loading()
        self._build_workspace_view()
        self._install_breakpoints()
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_files_dropped)
        self.add_controller(drop)
        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", self._on_active_changed)
        self.main.set_visible_child_name("welcome")
        actions.update(self)

    # ================================================================ building

    def _menu_button(self):
        b = Gtk.MenuButton(icon_name="open-menu-symbolic", primary=True, tooltip_text="Main Menu")
        b.set_menu_model(actions.primary_menu(self.config.recent_workspaces))
        self._menu_buttons = getattr(self, "_menu_buttons", []) + [b]
        return b

    def _refresh_menus(self):
        model = actions.primary_menu(self.config.recent_workspaces)
        for b in getattr(self, "_menu_buttons", []):
            b.set_menu_model(model)

    def _build_welcome(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.pack_end(self._menu_button())
        tv.add_top_bar(hb)
        self.welcome = Adw.StatusPage(
            icon_name=self.icon_name, title=APP_NAME,
            description="Open a gtd-eml working directory — the folder containing 01-input … "
                        "06-archive and metadata.csv — to work through your email.")
        box = vbox(24, halign=Gtk.Align.CENTER)
        b = Gtk.Button(label="Open Workspace…", action_name="win.open-workspace",
                       halign=Gtk.Align.CENTER)
        b.add_css_class("pill")
        b.add_css_class("suggested-action")
        box.append(b)
        self.recent_group = Adw.PreferencesGroup(title="Recent")
        clamp = Adw.Clamp(maximum_size=460, child=self.recent_group)
        box.append(clamp)
        self.welcome.set_child(box)
        tv.set_content(self.welcome)
        self.main.add_named(tv, "welcome")

    def _update_welcome(self):
        group = self.recent_group
        for row in getattr(self, "_recent_rows", []):
            group.remove(row)
        self._recent_rows = []
        for path in self.config.recent_workspaces[:5]:
            row = Adw.ActionRow(title=os.path.basename(path) or path,
                                subtitle=actions._tilde(os.path.dirname(path)), activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, p=path: self.open_path(p))
            group.add(row)
            self._recent_rows.append(row)
        group.set_visible(bool(self._recent_rows))

    def _build_loading(self):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.StatusPage(title="Loading Workspace…")
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER)
        page.set_child(spinner)
        tv.set_content(page)
        self.loading_title = page
        self.main.add_named(tv, "loading")

    def _build_workspace_view(self):
        self.split = Adw.OverlaySplitView(min_sidebar_width=220, max_sidebar_width=300,
                                          sidebar_width_fraction=0.2)
        # Sidebar
        stv = Adw.ToolbarView()
        shb = Adw.HeaderBar()
        self.sidebar_title = Adw.WindowTitle(title=APP_NAME)
        shb.set_title_widget(self.sidebar_title)
        shb.pack_start(Gtk.Button(icon_name="mail-send-receive-symbolic", action_name="win.ingest",
                                  tooltip_text="Ingest Input (Ctrl+Shift+N)"))
        shb.pack_end(self._menu_button())
        stv.add_top_bar(shb)
        self.sidebar = Sidebar()
        self.sidebar.connect("item-activated", lambda _s, item: self.navigate(item))
        self.sidebar.connect("messages-dropped",
                             lambda _s, ids, folder: self.move(ids.split("\n"), folder))
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.sidebar)
        stv.set_content(sw)
        self.split.set_sidebar(stv)

        # Mailbox view: list | reading pane
        self.inner = Adw.NavigationSplitView(min_sidebar_width=330, max_sidebar_width=480,
                                             sidebar_width_fraction=0.38)
        ltv = Adw.ToolbarView()
        lhb = Adw.HeaderBar()
        self.list_title = Adw.WindowTitle(title="Triage")
        lhb.set_title_widget(self.list_title)
        lhb.pack_start(sidebar_toggle(self.split))
        lhb.pack_end(Gtk.MenuButton(icon_name="view-sort-descending-symbolic",
                                    menu_model=actions.sort_menu(),
                                    tooltip_text="Sort and Group"))
        self.search_button = Gtk.ToggleButton(icon_name="edit-find-symbolic",
                                              tooltip_text="Search All Folders (Ctrl+F)")
        lhb.pack_end(self.search_button)
        ltv.add_top_bar(lhb)
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search all folders", hexpand=True)
        self.search_bar.set_child(Adw.Clamp(maximum_size=480, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.bind_property("search-mode-enabled", self.search_button, "active", 3)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode)
        self.search_entry.connect("search-changed", self._on_search_changed)
        ltv.add_top_bar(self.search_bar)
        for banner in self.make_banners():
            ltv.add_top_bar(banner)
        self.message_list = MessageList()
        self.message_list.connect("selection-changed", lambda *_: self._on_selection_changed())
        self.message_list.connect("activated", lambda _l, r: self.open_messages([r.id]) if r
                                  else None)
        self.message_list.connect("context-menu", self._on_context_menu)
        self.search_bar.set_key_capture_widget(self.message_list)
        ltv.set_content(self.message_list)
        self.list_page = Adw.NavigationPage(title="Messages", child=ltv, tag="list")
        self.inner.set_sidebar(self.list_page)
        self.reading = ReadingPane(self)
        self.reading_page = Adw.NavigationPage(title="Message", child=self.reading, tag="message")
        self.inner.set_content(self.reading_page)

        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content.add_named(self.inner, "mail")
        self.dashboard = DashboardPage(self)
        self.performance = PerformancePage(self)
        self.calendar_page = CalendarPage(self)
        self.content.add_named(self.dashboard, "dashboard")
        self.content.add_named(self.performance, "performance")
        self.content.add_named(self.calendar_page, "calendar")
        self.split.set_content(self.content)
        self.main.add_named(self.split, "workspace")

    def _install_breakpoints(self):
        wide = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        wide.add_setter(self.split, "collapsed", True)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.inner, "collapsed", True)
        self.add_breakpoint(wide)
        self.add_breakpoint(narrow)

    def make_banners(self):
        """The three notices, one set per page header (kept in step)."""
        made = []
        for kind, action in (("config", "win.open-config"), ("input", "win.ingest"),
                             ("review", "win.review-date-stamps")):
            b = Adw.Banner(revealed=False)
            b.set_action_name(action)
            b.kind = kind
            made.append(b)
        self._banners.extend(made)
        return made

    # ================================================================ state helpers

    def _formatter(self):
        c = self.config
        return DateFormatter(c.get("date_style"), c.get("time_zone"), c.get("naive_dates"))

    @property
    def radar(self):
        return self.config.radar_folders

    def selected_ids(self):
        return self.message_list.selected_ids() if self.workspace else []

    def selected_records(self):
        return [self.by_id[i] for i in self.selected_ids() if i in self.by_id]

    def is_searching(self):
        return bool(self.search_text.strip())

    def _subtitle(self):
        return os.path.basename(self.workspace.root) if self.workspace else ""

    def toast(self, text, button=None, action=None):
        t = Adw.Toast(title=text, timeout=4)
        if button:
            t.set_button_label(button)
            t.set_action_name(action)
        self.toasts.add_toast(t)

    def _fail(self, heading, error):
        message = error.message if isinstance(error, GTDError) else str(error)
        if isinstance(error, GTDError) and error.detail:
            message += "\n\n" + error.detail
        alert(self, heading, message)

    # ================================================================ opening

    def open_last_workspace(self):
        last = self.config.get("last_workspace")
        if self.config.flag("reopen_last") and last and os.path.isdir(last):
            self.open_path(last)
        else:
            self._update_welcome()

    def choose_workspace(self):
        dialog = Gtk.FileDialog(title="Open Workspace", accept_label="Open", modal=True)
        last = self.config.get("last_workspace")
        if last and os.path.isdir(os.path.dirname(last)):
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(last)))

        def done(d, result):
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.open_path(folder.get_path())
        dialog.select_folder(self, None, done)

    def open_path(self, path, confirmed=False):
        root = os.path.abspath(path)
        if not os.path.isdir(root):
            self.config.remove_recent(root)
            self._refresh_menus()
            self._update_welcome()
            alert(self, "Folder Not Found", f"{root} no longer exists.")
            return
        if not confirmed and not Workspace.looks_like_workspace(root):
            confirm(self, "Create a GTD Workspace Here?",
                    f"{root} has no workflow folders yet. {APP_NAME} can create 01-input … "
                    "06-archive in it, as `gtd list` would.", "Create Folders",
                    lambda: self.open_path(root, confirmed=True))
            return
        ws = Workspace(root)
        try:
            ws.ensure_folders()
        except OSError as e:
            alert(self, "Could Not Create Folders", str(e))
            return
        self._stop_monitors()
        self.workspace = ws
        self.loader.attach(root)
        self.records, self.by_id, self.rows, self.computed = [], {}, {}, []
        self._has_loaded = False
        self.result_item = None
        self.search_bar.set_search_mode(False)
        self.search_text = ""
        self.item = ("folder", "triage")
        self.config.add_recent(root)
        self._refresh_menus()
        self._load_config()
        self.sidebar_title.set_subtitle(os.path.basename(root))
        self.set_title(f"{os.path.basename(root)} — {APP_NAME}")
        self.loading_title.set_description(actions._tilde(root))
        self.main.set_visible_child_name("loading")
        self._start_monitors()
        self.reload()

    def close_workspace(self):
        self._stop_monitors()
        self.workspace = None
        self._generation += 1
        self.records, self.by_id, self.rows, self.computed = [], {}, {}, []
        self.config.set("last_workspace", "")
        self.set_title(APP_NAME)
        self._update_welcome()
        self.main.set_visible_child_name("welcome")
        actions.update(self)

    def _load_config(self):
        self.wcfg, self.wcfg_error = workspace_config.load(self.workspace.root)
        self.sidebar.rebuild(self.wcfg, self.result_item, self._result_title())

    # ================================================================ loading

    def refresh(self):
        if self.workspace is None:
            return
        self._load_config()
        self.reload()

    def reload(self, select=None):
        """Reload from disk on a worker thread, then select `select` (or keep
        the selection where it still exists)."""
        if self.workspace is None:
            return
        if self._loading:
            self._reload_again = True
            self._pending_select = select if select is not None else \
                getattr(self, "_pending_select", None)
            return
        self._loading = True
        self._generation += 1
        gen, ws, cfg = self._generation, self.workspace, self.wcfg

        def work():
            try:
                snap = self.loader.load(ws, cfg)
                err = None
            except Exception as e:  # noqa: BLE001 - shown to the user
                snap, err = None, e
            GLib.idle_add(self._apply, gen, snap, err, select)
        threading.Thread(target=work, daemon=True).start()

    def _apply(self, gen, snap, err, select):
        self._loading = False
        if gen == self._generation and self.workspace is not None:
            if err is not None:
                self._fail("Could Not Load the Workspace", err)
            else:
                self.records = snap.records
                self.by_id = {r.id: r for r in snap.records}
                self.computed = snap.computed
                self.autofix = snap.autofix
                self.rows = snap.rows
                self.overview = Overview(snap.records)
                self._rebuild_calendar()
                if not self._has_loaded:
                    self._has_loaded = True
                    self.main.set_visible_child_name("workspace")
                    self.sidebar.select_item(self.item)
                self._update_all(select)
        if self._reload_again:
            self._reload_again = False
            pending = getattr(self, "_pending_select", None)
            self._pending_select = None
            self.reload(pending)
        return False

    def _rebuild_calendar(self):
        fmt = self.fmt
        self.calendar = CalendarIndex.build(self.records, fmt.day, fmt.quoted_day)

    def _update_all(self, select=None):
        self._update_sidebar()
        self._update_banners()
        self._show_current(select)

    def _update_sidebar(self):
        counts = {}
        for item in self.sidebar.items():
            if item[0] == "folder":
                counts[item] = self.overview.counts[item[1]]
            elif views.is_mailbox(item):
                counts[item] = len(views.members(item, self.records, self.radar, self.overview,
                                                 self.calendar))
        dimmed = {f.alias for f in F.FOLDERS if f not in self.radar} \
            if self.config.flag("dim_off_radar") else set()
        self.sidebar.update(counts, dimmed)

    def _update_banners(self):
        n_in = self.overview.counts.get("input", 0)
        plan = self.autofix
        for b in self._banners:
            if b.kind == "config":
                b.set_title(f"workspace.yml could not be used, so defaults are in use: "
                            f"{self.wcfg_error}" if self.wcfg_error else "")
                b.set_button_label("Open")
                b.set_revealed(bool(self.wcfg_error))
            elif b.kind == "input":
                b.set_title(f"{plural(n_in, 'email')} {is_are(n_in)} waiting in Input")
                b.set_button_label("Ingest")
                b.set_revealed(n_in > 0 and not self.wcfg_error)
            else:
                if plan.blockers:
                    text = (f"{plural(len(plan.blockers), 'email')} need manual attention "
                            "before date stamps can be fixed")
                else:
                    text = (f"{plural(len(plan.fixes), 'date stamp')} "
                            f"{is_are(len(plan.fixes))} missing or inconsistent")
                b.set_title(text)
                b.set_button_label("Review…")
                b.set_revealed(plan.needs_review and plan.pending_input == 0)

    # ================================================================ navigation

    def navigate(self, item):
        """Show a sidebar entry (from the sidebar, a shortcut or a page)."""
        if self.workspace is None:
            return
        if not views.is_result(item) and self.result_item is not None:
            self.result_item = None
            self.sidebar.rebuild(self.wcfg)
        self.item = item
        if self.is_searching() or self.search_bar.get_search_mode():
            self.search_entry.set_text("")
            self.search_text = ""
            self.search_bar.set_search_mode(False)
        self.sidebar.select_item(item)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.inner.set_show_content(False)
        self._show_current(keep_selection=False)

    def open_result(self, item):
        """A Dashboard figure, project or Calendar day, as a Results entry."""
        self.result_item = item
        self.sidebar.rebuild(self.wcfg, item, self._result_title())
        self._update_sidebar()
        self.navigate(item)

    def _result_title(self):
        if self.result_item is None:
            return None
        return views.title(self.result_item, self.wcfg, self.overview, self.fmt)

    def _show_current(self, select=None, keep_selection=True):
        if self.workspace is None:
            return
        if self.is_searching() or views.is_mailbox(self.item):
            self.content.set_visible_child_name("mail")
            self._rebuild_list(select, keep_selection)
        else:
            page = {"dashboard": self.dashboard, "performance": self.performance,
                    "calendar": self.calendar_page}[self.item[0]]
            self.content.set_visible_child(page)
            self.refresh_page(page)
            actions.update(self)

    def refresh_page(self, page):
        if page is self.dashboard:
            page.update(self.overview, self._subtitle())
        else:
            page.update({"subtitle": self._subtitle(), "wcfg": self.wcfg, "autofix": self.autofix,
                         "computed": self.computed, "records": self.records,
                         "today": date.today(), "calendar": self.calendar, "fmt": self.fmt})

    def _list_context(self):
        searching = self.is_searching()
        dim = self.config.flag("dim_off_radar")
        day = self.item[1] if self.item[0] == "day" and not searching else None
        return {"fmt": self.fmt, "today": date.today(), "calendar": self.calendar, "day": day,
                "show_folder": searching or self.item[0] != "folder",
                "dim": (lambda r: dim and searching and r.folder not in self.radar)}

    def _rebuild_list(self, select=None, keep_selection=True):
        searching = self.is_searching()
        if searching:
            base = views.search(self.records, self.search_text)
            title = "Search Results"
        else:
            base = views.members(self.item, self.records, self.radar, self.overview,
                                 self.calendar)
            title = views.title(self.item, self.wcfg, self.overview, self.fmt)
        key = self.config.sort_key
        ordered = views.sort_records(base, key, self.config.flag("sort_ascending"))
        grouped = views.groups(ordered, key, self.config.flag("date_headings"),
                               self.fmt.day, self.fmt.today())
        self.list_title.set_title(title)
        self.list_title.set_subtitle(plural(len(ordered), "email"))
        self.list_page.set_title(title)
        listed = {r.id for r in ordered}
        if select is not None:
            keep = [i for i in select if i in listed]
        elif keep_selection:
            keep = [i for i in self.selected_ids() if i in listed]
        else:
            keep = []
        if not keep and self._fallback in listed:
            keep = [self._fallback]
        self._fallback = None
        if searching:
            empty = ("No Results", "edit-find-symbolic", "Try a different search")
        else:
            empty = ("No Emails", self._item_icon(), None)
        self.message_list.set_records(grouped, self._list_context(), set(keep), *empty)

    def _item_icon(self):
        if self.item[0] == "folder":
            return F.BY_ALIAS[self.item[1]].icon
        return "gtd-inbox-symbolic"

    def _neighbour(self, ids):
        """The row after (or before) the given ones, to keep a selection when
        emails leave the list, as mail apps do."""
        order = [r.id for r in self.message_list.ordered_records()]
        idx = [i for i, rid in enumerate(order) if rid in ids]
        if not idx:
            return None
        after = [rid for rid in order[idx[-1] + 1:] if rid not in ids]
        if after:
            return after[0]
        before = [rid for rid in order[:idx[-1]] if rid not in ids]
        return before[-1] if before else None

    # ================================================================ selection

    def _on_selection_changed(self):
        recs = self.selected_records()
        if len(recs) == 1:
            r = recs[0]
            self.reading.show_record(r, {
                "fmt": self.fmt, "today": date.today(),
                "fmt_key": (self.config.get("date_style"), self.config.get("time_zone"),
                            self.config.get("naive_dates")),
                "projects": [name for name, _ in self.overview.projects],
                "editable": r.tracked, "edit_refusal": views.ANNOTATIONS_REFUSAL})
            self.reading_page.set_title(r.subject)
            if self.inner.get_collapsed() and getattr(self, "_user_clicked", True):
                self.inner.set_show_content(True)
        elif len(recs) > 1:
            self.reading.show_multiple(len(recs))
        else:
            self.reading.show_none()
        self.reading.set_move_menu(actions.move_menu(self))
        actions.update(self)

    def select_all(self):
        self.message_list.select_all()

    def _on_context_menu(self, _list, row, x, y):
        menu = Gtk.PopoverMenu.new_from_model(actions.message_menu(self))
        menu.set_has_arrow(False)
        menu.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        menu.set_pointing_to(rect)
        menu.connect("closed", lambda m: GLib.idle_add(m.unparent))
        menu.popup()

    def popup_move_menu(self):
        if self.inner.get_collapsed() and not self.inner.get_show_content():
            self.inner.set_show_content(True)
        self.reading.move_button.popup()

    # ================================================================ search

    def start_search(self):
        if self.workspace is None:
            return
        self.content.set_visible_child_name("mail")
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def _on_search_mode(self, *_):
        if not self.search_bar.get_search_mode() and self.search_text:
            self.search_text = ""
            self.search_entry.set_text("")
            self.sidebar.select_item(self.item)
            self._show_current(keep_selection=False)

    def _on_search_changed(self, entry):
        text = entry.get_text()
        if text == self.search_text:
            return
        self.search_text = text
        # While searching, the list is not any one sidebar entry's.
        if self.is_searching():
            self.sidebar.select_item(None)
        else:
            self.sidebar.select_item(self.item)
        self._show_current(keep_selection=False)

    def toggle_sidebar(self):
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    # ================================================================ workflow actions

    def ingest(self, then_review=False):
        if self.workspace is None:
            return
        if self.wcfg_error:
            alert(self, "Cannot Ingest", "workspace.yml could not be used, so the filename "
                  "length is unknown and ingested files could be named differently from the "
                  f"CLI's. Fix workspace.yml first.\n\n{self.wcfg_error}")
            return
        try:
            moved = self.workspace.ingest(self.wcfg.max_filename_chars)
        except (GTDError, OSError) as e:
            self._fail("Could Not Ingest", e)
            self.reload()
            return
        if not moved:
            self.toast("No new files in 01-input")
            self.reload()
        else:
            ids = [f"{F.TRIAGE.dir}/{new}" for _, new, _ in moved]
            self.toast(f"Ingested {plural(len(moved), 'email')} into Triage", "Show",
                       "win.go-triage")
            if self.item in (("folder", "input"), ("attention", "input")):
                self.item = ("folder", "triage")
                self.sidebar.select_item(self.item)
            self.reload(select=ids)
        if then_review:
            GLib.timeout_add(300, lambda: (self.review_date_stamps(), False)[1])

    def choose_emails_to_add(self):
        dialog = Gtk.FileDialog(title="Add Emails to Input", accept_label="Add", modal=True)
        f = Gtk.FileFilter(name="Email messages (.eml)")
        f.add_pattern("*.eml")
        f.add_pattern("*.EML")
        f.add_mime_type("message/rfc822")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                files = d.open_multiple_finish(result)
            except GLib.Error:
                return
            self.import_files([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dialog.open_multiple(self, None, done)

    def import_files(self, paths):
        if self.workspace is None:
            return
        emls = [p for p in paths if p and p.lower().endswith(".eml")]
        if not emls:
            alert(self, "Nothing to Add", "Only .eml files can be added to Input.")
            return
        try:
            added = self.workspace.import_to_input(emls)
        except OSError as e:
            self._fail("Could Not Add Files", e)
            return
        self.toast(f"Added {plural(len(added), 'email')} to Input", "Ingest", "win.ingest")
        self.reload()

    def _on_files_dropped(self, _target, value, _x, _y):
        files = value.get_files() if hasattr(value, "get_files") else []
        paths = [f.get_path() for f in files if f.get_path()]
        dirs = [p for p in paths if os.path.isdir(p)]
        if self.workspace is None or (dirs and len(paths) == 1):
            if dirs:
                self.open_path(dirs[0])
                return True
            return False
        self.import_files(paths)
        return True

    def move(self, ids, dest):
        if self.workspace is None:
            return
        refusals, moved = [], []
        self._fallback = self._neighbour(set(ids))
        for rid in ids:
            r = self.by_id.get(rid)
            if r is None:
                continue
            if r.folder == F.INPUT:
                refusals.append(f"'{r.filename}' is still in 01-input; ingest it first.")
                continue
            if r.folder == dest:
                continue
            try:
                self.workspace.alloc(r.filename, dest)
                moved.append(f"{dest.dir}/{r.filename}")
            except (GTDError, OSError) as e:
                refusals.append(str(e))
        if moved:
            self.toast(f"Moved {plural(len(moved), 'email')} to {dest.title}")
        stays = self.is_searching() or views.is_mailbox(self.item) and \
            self.item[0] != "folder"
        self.reload(select=moved if stays else None)
        if refusals:
            alert(self, "Could Not Move Email" if len(refusals) == 1 else
                  "Some Emails Were Not Moved", "\n\n".join(refusals))

    def begin_close(self):
        recs = self.selected_records()
        if len(recs) != 1:
            return
        r = recs[0]
        reason = views.close_refusal(r, self.workspace, self.rows)
        if reason:
            alert(self, "Cannot Close This Email", reason)
            return
        candidates = sorted((c for c in self.records if c.id != r.id and c.tracked),
                            key=lambda c: -c.date.timestamp())
        CloseWithDialog(self, r, candidates, self.fmt).present(self)

    def close_with(self, rid, other_id):
        r, other = self.by_id.get(rid), self.by_id.get(other_id)
        if r is None or other is None:
            return
        self._fallback = self._neighbour({rid})
        try:
            self.workspace.close(r.filename, other.filename)
            self.toast(f"Closed with {other.filename}")
            self.reload(select=[f"{F.ARCHIVE.dir}/{r.filename}"])
        except (GTDError, OSError) as e:
            self._fail("Could Not Close Email", e)
            self.reload()

    def toggle_pin(self):
        targets = [r for r in self.selected_records() if r.tracked]
        if not targets:
            return
        pin = not all(r.is_pinned for r in targets)
        try:
            for r in targets:
                self.workspace.set_flag(r.filename, "pinned", pin)
        except (GTDError, OSError) as e:
            self._fail("Could Not Pin" if pin else "Could Not Unpin", e)
        self.reload()

    def edit_annotations(self):
        if self.inner.get_collapsed():
            self.inner.set_show_content(True)
        self.reading.focus_next_action()

    def save_fields(self, rid, changes):
        r = self.by_id.get(rid)
        if r is None or self.workspace is None:
            return
        changes = {k: v for k, v in changes.items() if (r.row.get(k, "") or "") != v}
        if not changes:
            return
        try:
            warnings = self.workspace.set_fields(r.filename, changes)
        except (GTDError, OSError) as e:
            self._fail("Could Not Save", e)
            return
        self.reload()
        if warnings:
            alert(self, "Saved, With a Warning", "\n".join(warnings))

    def review_date_stamps(self):
        if self.workspace is None:
            return
        self.autofix = self.workspace.plan_autofix()
        ReviewDateStampsDialog(self, self.autofix).present(self)

    def apply_autofix(self, plan, dialog):
        fresh = self.workspace.plan_autofix()
        if (fresh.fixes, fresh.blockers, fresh.pending_input) != \
                (plan.fixes, plan.blockers, plan.pending_input):
            dialog.close()
            alert(self, "The Workspace Changed",
                  "The date stamps changed on disk while this list was open. Review the "
                  "updated list, then apply again.")
            self.reload()
            return
        try:
            self.workspace.apply_autofix(fresh)
            dialog.close()
            self.toast(f"Applied {plural(len(fresh.fixes), 'date stamp')}")
        except (GTDError, OSError) as e:
            self._fail("Could Not Apply Date Stamps", e)
        self.reload()

    def check_metadata(self):
        if self.workspace is None:
            return
        CheckMetadataDialog(self, self.workspace.preview_metadata_check()).present(self)

    def reconcile_metadata(self, dialog):
        try:
            self.workspace.metadata_check()
            dialog.close()
            self.toast("metadata.csv reconciled with the files on disk")
        except (GTDError, OSError) as e:
            self._fail("Could Not Reconcile metadata.csv", e)
        self.reload()

    # ================================================================ files

    def _paths(self, ids=None):
        ids = ids if ids is not None else self.selected_ids()
        return [self.workspace.path(self.by_id[i].folder, self.by_id[i].filename)
                for i in ids if i in self.by_id]

    def open_messages(self, ids=None):
        for path in self._paths(ids)[:10]:
            Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self, None, None)

    def show_in_files(self):
        paths = self._paths()
        if paths:
            Gtk.FileLauncher.new(Gio.File.new_for_path(paths[0])).open_containing_folder(
                self, None, None)

    def copy_filenames(self):
        names = [self.by_id[i].filename for i in self.selected_ids() if i in self.by_id]
        if names:
            self.get_clipboard().set("\n".join(names))
            self.toast("Copied " + ("filename" if len(names) == 1 else
                                    plural(len(names), "filename")))

    def show_workspace(self):
        if self.workspace:
            Gtk.FileLauncher.new(Gio.File.new_for_path(self.workspace.root)).launch(
                self, None, None)

    def open_config(self):
        if self.workspace is None:
            return
        path = self.workspace.config_path
        if os.path.exists(path):
            Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self, None, None)
            return

        def copy():
            self.get_clipboard().set(workspace_config.EXAMPLE)
            self.toast("Example copied")
        confirm(self, "This Workspace Has No workspace.yml",
                "All workspace settings are at their defaults. To change them, create "
                f"{workspace_config.FILE_NAME} at the root of the workspace, next to "
                "metadata.csv. The example can be copied as a starting point.", "Create It",
                lambda: self._create_config(path), extra=("Copy Example", copy))

    def _create_config(self, path):
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(workspace_config.EXAMPLE)
        except OSError as e:
            self._fail("Could Not Create workspace.yml", e)
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self, None, None)

    # ================================================================ preferences etc.

    def set_sort(self, key=None, sort_ascending=None, date_headings=None):
        if key is not None:
            self.config.set("sort_key", key)
        if sort_ascending is not None:
            self.config.set("sort_ascending", sort_ascending)
        if date_headings is not None:
            self.config.set("date_headings", date_headings)
        if self.workspace is not None:
            self._show_current()

    def preferences_changed(self):
        self.fmt = self._formatter()
        if self.workspace is None or not self._has_loaded:
            return
        self._rebuild_calendar()
        self._update_all()
        self._on_selection_changed()

    def show_preferences(self):
        from .gtk4_preferences import PreferencesDialog
        PreferencesDialog(self).present(self)

    def show_shortcuts(self):
        from .gtk4_shortcuts import build_shortcuts_window
        build_shortcuts_window(self).present()

    def show_about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=self.icon_name, developer_name="QDVC",
            comments="Work a Getting Things Done workflow over .eml files, sharing the "
                     "workspace with the gtd command-line tool.",
            website="https://github.com/qdvc-apps/qdvc-gtd-eml-gnome")
        about.present(self)

    # ================================================================ monitors, lifecycle

    def _start_monitors(self):
        ws = self.workspace
        for d in [ws.root] + [ws.folder_path(f) for f in F.FOLDERS]:
            try:
                mon = Gio.File.new_for_path(d).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES,
                                                                 None)
            except GLib.Error:
                continue
            mon.connect("changed", self._on_disk_changed)
            self._monitors.append(mon)

    def _stop_monitors(self):
        for mon in self._monitors:
            mon.cancel()
        self._monitors = []

    def _on_disk_changed(self, _mon, file, _other, _event):
        name = file.get_basename() or ""
        if name.startswith(".metadata-") or name.endswith(".tmp"):
            return
        config_changed = name == workspace_config.FILE_NAME
        self._config_dirty = getattr(self, "_config_dirty", False) or config_changed
        if self._reload_source is None:
            self._reload_source = GLib.timeout_add(RELOAD_DELAY_MS, self._reload_from_disk)

    def _reload_from_disk(self):
        self._reload_source = None
        if self.workspace is None:
            return False
        if getattr(self, "_config_dirty", False):
            self._config_dirty = False
            self._load_config()
            self.sidebar.select_item(self.item)
        self.reload()
        return False

    def _on_active_changed(self, *_):
        # Monitors cover local disks; this catches network mounts too.
        if self.is_active() and self.workspace is not None and self._has_loaded:
            now = time.monotonic()
            if now - getattr(self, "_last_focus_reload", 0) > 5:
                self._last_focus_reload = now
                self.reload()

    def _on_close_request(self, *_):
        if not self.is_maximized():
            w, h = self.get_default_size()
            self.config.set("window_width", w, save=False)
            self.config.set("window_height", h, save=False)
        self.config.set("window_maximized", self.is_maximized())
        self._stop_monitors()
        return False
