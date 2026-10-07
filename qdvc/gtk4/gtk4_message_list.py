"""The message list: Geary-style rows in a Gtk.ListView with multiple
selection and date section headers.

Rows: an age dot; correspondent and date; pin, subject and paperclip; the next
action as the preview line; then the account, the due date and (when it
helps) the folder. Right-click or long-press opens the message menu, selecting
the row first if it was not selected; rows can be dragged onto a sidebar
folder; activating a row opens the .eml in the default mail app.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject, Gtk  # noqa: E402

from ..calendar_index import LABELS as EVENT_LABELS  # noqa: E402
from ..workspace_config import ACCOUNT_COLOURS  # noqa: E402
from .gtk4_widgets import colour_class, label, tag, vbox  # noqa: E402


class RecordItem(GObject.Object):
    __gtype_name__ = "QdvcRecordItem"

    def __init__(self, record, group, group_index):
        super().__init__()
        self.record = record
        self.group = group
        self.group_index = group_index


class MessageRow(Gtk.Box):
    """One recyclable row widget."""

    def __init__(self, owner):
        super().__init__(spacing=10)
        self.owner = owner
        self.item = None
        self.position = -1
        self.add_css_class("message-row")
        self.dot = Gtk.Box(valign=Gtk.Align.START)
        self.dot.add_css_class("age-dot")
        self.append(self.dot)
        col = vbox(2, hexpand=True)
        l1 = Gtk.Box(spacing=6)
        self.who = label("", "heading", hexpand=True)
        self.when = label("", "dim-label", "caption", "numeric", xalign=1.0, ellipsize=False)
        l1.append(self.who)
        l1.append(self.when)
        col.append(l1)
        l2 = Gtk.Box(spacing=4)
        self.pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
        self.pin.add_css_class("accent")
        self.subject = label("")
        self.clip = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
        self.clip.add_css_class("dim-label")
        l2.append(self.pin)
        l2.append(self.subject)
        l2.append(self.clip)
        col.append(l2)
        self.preview = label("", "dim-label", wrap=True, lines=2)
        col.append(self.preview)
        self.tags = Gtk.Box(spacing=8)
        col.append(self.tags)
        self.append(col)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, n, x, y: self.owner.context_menu(self, x, y))
        self.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda g, x, y: self.owner.context_menu(self, x, y))
        self.add_controller(long_press)
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_: self.owner.drag_content(self))
        drag.connect("drag-begin", self._drag_begin)
        self.add_controller(drag)

    def _drag_begin(self, source, _drag):
        icon = Gtk.WidgetPaintable.new(self.who)
        source.set_icon(icon, 0, 0)

    def bind(self, item, position, ctx):
        self.item = item
        self.position = position
        r = item.record
        fmt = ctx["fmt"]
        for cls in ("age-green", "age-yellow", "age-red", "age-none"):
            self.dot.remove_css_class(cls)
        self.dot.add_css_class(f"age-{r.age_class}" if r.age_class else "age-none")
        self.dot.set_tooltip_text(f"{r.age_days} day{'s' if r.age_days != 1 else ''} old"
                                  if r.age_days is not None else "Could not be read")
        self.who.set_label(r.list_correspondent)
        self.when.set_label(fmt.list_date(r.date))
        self.when.set_tooltip_text(fmt.full(r.date))
        self.pin.set_visible(r.is_pinned)
        self.subject.set_label(r.subject)
        self.clip.set_visible(r.has_attachments)
        if r.is_unreadable:
            text, faint = r.parsed.error or "Could not be read", False
        elif not r.tracked:
            text, faint = "Not ingested yet", True
        else:
            text, faint = (r.next_action, False) if r.next_action else ("No next action", True)
        self.preview.set_label(text)
        if faint:
            self.preview.add_css_class("faint")
        else:
            self.preview.remove_css_class("faint")

        child = self.tags.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.tags.remove(child)
            child = nxt
        if ctx["show_folder"]:
            self.tags.append(tag(r.folder.title))
        if r.account:
            colour = ACCOUNT_COLOURS.get(r.account["colour"], "#77767b")
            self.tags.append(label(r.account["display_name"], "caption", colour_class(colour)))
        if r.due_date:
            overdue = r.is_overdue(ctx["today"])
            self.tags.append(tag("Due " + fmt.day_text(r.due_date, style=fmt.compact_style()),
                                 "error" if overdue else None,
                                 tooltip="Overdue" if overdue else None))
        if ctx.get("day") is not None:
            for kind in ctx["calendar"].kinds(ctx["day"], r.id):
                self.tags.append(tag(EVENT_LABELS[kind], "accent"))
        if r.status == "weird":
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn.add_css_class("warning")
            warn.set_tooltip_text("Inconsistent date progression")
            self.tags.append(warn)
        self.tags.set_visible(self.tags.get_first_child() is not None)
        if ctx["dim"](r):
            self.add_css_class("dimmed")
        else:
            self.remove_css_class("dimmed")


class MessageList(Gtk.Stack):
    __gsignals__ = {
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # (row widget, x, y): the window shows the message menu.
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
    }

    def __init__(self):
        super().__init__()
        self.store = Gio.ListStore(item_type=RecordItem)
        self._section_sorter = Gtk.CustomSorter.new(
            lambda a, b, _d: (a.group_index > b.group_index) - (a.group_index < b.group_index),
            None)
        self.sorted = Gtk.SortListModel(model=self.store)
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.selection.connect("selection-changed", self._on_selection_changed)
        self._quiet = False
        self.ctx = {}

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda f, li: li.set_child(MessageRow(self)))
        factory.connect("bind", self._bind)
        self.header_factory = Gtk.SignalListItemFactory()
        self.header_factory.connect("setup", self._setup_header)
        self.header_factory.connect("bind", self._bind_header)

        self.view = Gtk.ListView(model=self.selection, factory=factory)
        self.view.add_css_class("navigation-sidebar")
        self.view.connect("activate", lambda _v, pos: self.emit("activated", self._record_at(pos)))
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.view)
        self.scroller = sw
        self.add_named(sw, "list")
        self.empty = Adw.StatusPage(icon_name="gtd-inbox-symbolic", title="No Emails")
        self.empty.add_css_class("compact")
        self.add_named(self.empty, "empty")

    # ------------------------------------------------------------ factory

    def _bind(self, _f, list_item):
        list_item.get_child().bind(list_item.get_item(), list_item.get_position(), self.ctx)

    def _setup_header(self, _f, header):
        lbl = label("", "heading", "dim-label")
        lbl.add_css_class("list-heading")
        header.set_child(lbl)

    def _bind_header(self, _f, header):
        item = header.get_item()
        header.get_child().set_label(item.group if item is not None else "")

    # ------------------------------------------------------------ content

    def set_records(self, grouped, ctx, keep_ids, empty_title, empty_icon, empty_text=None):
        """Replace the list. `grouped` is [(heading, [records])]; `keep_ids`
        are re-selected when still listed."""
        self.ctx = ctx
        items = []
        for gi_, (heading, recs) in enumerate(grouped):
            items.extend(RecordItem(r, heading, gi_) for r in recs)
        headed = any(h for h, _ in grouped)
        self._quiet = True
        try:
            self.sorted.set_section_sorter(self._section_sorter if headed else None)
            self.view.set_header_factory(self.header_factory if headed else None)
            self.store.splice(0, self.store.get_n_items(), items)
            self.selection.unselect_all()
            first = None
            for pos in range(self.sorted.get_n_items()):
                if self.sorted.get_item(pos).record.id in keep_ids:
                    self.selection.select_item(pos, False)
                    first = pos if first is None else first
        finally:
            self._quiet = False
        self.empty.set_title(empty_title)
        self.empty.set_icon_name(empty_icon)
        self.empty.set_description(empty_text)
        self.set_visible_child_name("list" if items else "empty")
        if first is not None:
            self.view.scroll_to(first, Gtk.ListScrollFlags.NONE, None)
        else:
            self.scroller.get_vadjustment().set_value(0)
        self.emit("selection-changed")
        return first

    def refresh_rows(self, ctx):
        """Re-bind the visible rows (e.g. after a date-format change)."""
        self.ctx = ctx
        n = self.store.get_n_items()
        items = [self.store.get_item(i) for i in range(n)]
        self._quiet = True
        try:
            keep = self.selected_ids()
            self.store.splice(0, n, items)
            for pos in range(self.sorted.get_n_items()):
                if self.sorted.get_item(pos).record.id in keep:
                    self.selection.select_item(pos, False)
        finally:
            self._quiet = False

    def _record_at(self, pos):
        item = self.sorted.get_item(pos)
        return item.record if item is not None else None

    def ordered_records(self):
        return [self.sorted.get_item(i).record for i in range(self.sorted.get_n_items())]

    def selected_ids(self):
        bits = self.selection.get_selection()
        out = []
        ok, it, pos = Gtk.BitsetIter.init_first(bits)
        while ok:
            item = self.sorted.get_item(pos)
            if item is not None:
                out.append(item.record.id)
            ok, pos = it.next()
        return out

    def select_ids(self, ids, scroll=True):
        self._quiet = True
        first = None
        try:
            self.selection.unselect_all()
            for pos in range(self.sorted.get_n_items()):
                if self.sorted.get_item(pos).record.id in ids:
                    self.selection.select_item(pos, False)
                    first = pos if first is None else first
        finally:
            self._quiet = False
        if scroll and first is not None:
            self.view.scroll_to(first, Gtk.ListScrollFlags.FOCUS, None)
        self.emit("selection-changed")

    def select_all(self):
        self.selection.select_all()

    def _on_selection_changed(self, *_):
        if not self._quiet:
            self.emit("selection-changed")

    # ------------------------------------------------------------ row callbacks

    def context_menu(self, row, x, y):
        if row.position < 0:
            return
        if not self.selection.is_selected(row.position):
            self.selection.select_item(row.position, True)
        self.emit("context-menu", row, x, y)

    def drag_content(self, row):
        if row.item is None:
            return None
        ids = self.selected_ids()
        if row.item.record.id not in ids:
            ids = [row.item.record.id]
        value = GObject.Value(GObject.TYPE_STRING, "\n".join(ids))
        return Gdk.ContentProvider.new_for_value(value)

    def focus_list(self):
        self.view.grab_focus()

