"""The sidebar: Overview, Workflow, Filters, Accounts and (when one is open)
Results, as a `.navigation-sidebar` list with section headings and counts.

Dropping messages (dragged from the list) on a Workflow folder moves them.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from .. import folders as F  # noqa: E402
from .. import views  # noqa: E402
from ..workspace_config import ACCOUNT_COLOURS  # noqa: E402
from .gtk4_widgets import colour_class, label  # noqa: E402


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, item, title, icon, section, show_count=True, indent=False,
                 colour=None, italic=False):
        super().__init__()
        self.item = item
        self.section = section
        box = Gtk.Box(spacing=12, margin_top=1, margin_bottom=1)
        if indent:
            box.add_css_class("sidebar-indent")
        if colour:
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("account-dot")
            dot.add_css_class(colour_class(colour))
            box.append(dot)
        else:
            box.append(Gtk.Image.new_from_icon_name(icon))
        self.title_label = label(title, hexpand=True)
        if italic:
            self.title_label.set_markup(f"<i>{GObject.markup_escape_text(title)}</i>")
        box.append(self.title_label)
        self.count_label = label("", "dim-label", "numeric", xalign=1.0)
        self.count_label.set_visible(show_count)
        box.append(self.count_label)
        self.set_child(box)
        self.set_tooltip_text(title)

    def set_count(self, n):
        self.count_label.set_label(str(n) if n else "")

    def set_dimmed(self, dimmed):
        if dimmed:
            self.title_label.add_css_class("dim-label")
        else:
            self.title_label.remove_css_class("dim-label")


class Sidebar(Gtk.ListBox):
    __gsignals__ = {
        # The user chose an entry (programmatic selection does not emit).
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Message ids (newline-separated) dropped on a folder.
        "messages-dropped": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self):
        super().__init__()
        self.add_css_class("navigation-sidebar")
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._rows = []
        self._quiet = False
        self.set_header_func(self._header)
        self._just_emitted = None
        self.connect("row-selected", self._on_row_selected)
        # Clicking the entry that is already selected (say, to leave a search)
        # selects nothing new, so activation is handled too.
        self.connect("row-activated", self._on_row_activated)

    # ------------------------------------------------------------ building

    def rebuild(self, wcfg, result_item=None, result_title=None):
        """(Re)create the rows for the workspace's accounts and hashtags."""
        selected = self.selected_item()
        self._quiet = True
        while (row := self.get_row_at_index(0)) is not None:
            self.remove(row)
        self._rows = []

        def add(row):
            self.append(row)
            self._rows.append(row)
            return row

        add(SidebarRow(("dashboard",), "Dashboard", "view-grid-symbolic", "Overview", False))
        add(SidebarRow(("performance",), "Performance", "gtd-chart-symbolic", "Overview", False))
        add(SidebarRow(("calendar",), "Calendar", "x-office-calendar-symbolic", "Overview",
                       False))
        for f in F.FOLDERS:
            row = add(SidebarRow(("folder", f.alias), f.title, f.icon, "Workflow"))
            self._add_drop_target(row, f)
        add(SidebarRow(("due",), "Due Date Set", "task-due-symbolic", "Filters"))
        add(SidebarRow(("nodue",), "No Due Date", "gtd-no-due-symbolic", "Filters"))
        add(SidebarRow(("pinned",), "Pinned", "view-pin-symbolic", "Filters"))
        for tag in wcfg.monitored_hashtags:
            add(SidebarRow(("tag", tag), tag, "gtd-hashtag-symbolic", "Filters"))
        if wcfg.my_own_accounts:
            for kind, title, icon in (("inbox", "Inbox", "gtd-inbox-symbolic"),
                                      ("sent", "Sent", "mail-send-symbolic")):
                add(SidebarRow((kind, None), title, icon, "Accounts"))
                if len(wcfg.my_own_accounts) > 1:
                    for a in wcfg.my_own_accounts:
                        add(SidebarRow((kind, a["email_address"]), a["display_name"], None,
                                       "Accounts", indent=True,
                                       colour=ACCOUNT_COLOURS.get(a["colour"], "#77767b")))
        if result_item is not None:
            add(SidebarRow(result_item, result_title, "edit-find-symbolic", "Results",
                           italic=True))
        self._quiet = False
        if selected is not None:
            self.select_item(selected)

    def _add_drop_target(self, row, folder):
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", lambda _t, value, _x, _y: self._dropped(value, folder))
        row.add_controller(target)

    def _dropped(self, value, folder):
        if not value:
            return False
        self.emit("messages-dropped", value, folder)
        return True

    def _header(self, row, before):
        if before is None or before.section != row.section:
            h = label(row.section, "heading", "dim-label")
            h.set_margin_start(12)
            h.set_margin_top(12 if before is not None else 6)
            h.set_margin_bottom(4)
            row.set_header(h)
        else:
            row.set_header(None)

    # ------------------------------------------------------------ state

    def update(self, counts, dimmed_folders=()):
        """counts: {item: n}; folders not on the radar can be dimmed."""
        for row in self._rows:
            row.set_count(counts.get(row.item, 0))
            row.set_dimmed(row.item[0] == "folder" and row.item[1] in dimmed_folders)

    def items(self):
        return [row.item for row in self._rows]

    def selected_item(self):
        row = self.get_selected_row()
        return row.item if row is not None else None

    def select_item(self, item):
        self._quiet = True
        try:
            for row in self._rows:
                if row.item == item:
                    self.select_row(row)
                    return True
            self.unselect_all()
            return False
        finally:
            self._quiet = False

    def _on_row_selected(self, _lb, row):
        if not self._quiet and row is not None:
            self._just_emitted = row
            GLib.idle_add(self._forget_emitted)
            self.emit("item-activated", row.item)

    def _forget_emitted(self):
        self._just_emitted = None
        return False

    def _on_row_activated(self, _lb, row):
        if not self._quiet and row is not None and row is not self._just_emitted:
            self.emit("item-activated", row.item)

    @staticmethod
    def title_of(item, wcfg, overview, fmt):
        return views.title(item, wcfg, overview, fmt)
