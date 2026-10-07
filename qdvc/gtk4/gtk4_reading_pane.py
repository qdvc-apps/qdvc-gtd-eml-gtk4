"""The reading pane: the selected email's tags, subject, annotations (edited
in place), workflow trail and metrics, then the message and the messages
quoted in it as cards.

Each single-line annotation is an Adw.EntryRow with an apply button: Enter or
✓ writes that field — one `gtd metadata set` — and Escape reverts. The due
date is set with a calendar popover (dates are entered only through a picker;
a free-text value written by the CLI is shown and kept until it is replaced or
cleared). Notes have their own Apply / Revert buttons.
"""

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..records import display_name  # noqa: E402
from ..ui_prefs import METRIC_NAMES, plural  # noqa: E402
from ..workspace import is_iso_date  # noqa: E402
from .gtk4_widgets import header_grid, label, tag, vbox  # noqa: E402

MAX_BODY_CHARS = 200_000   # a label this long is slow; the rest is one click away


class ReadingPane(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.toolbar = Adw.ToolbarView()
        self.set_child(self.toolbar)
        self.window = window
        self.record = None
        self._signature = None
        self._expanded = {}          # record id -> set of open thread indices
        self._refocus = None
        self._fields = {}

        hb = Adw.HeaderBar(show_title=False)
        more = Gio_menu([("Open in Default App", "win.open-message"),
                         ("Show in Files", "win.show-in-files"),
                         ("Copy Filename", "win.copy-filename")])
        hb.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=more,
                                   tooltip_text="More Actions"))
        self.pin_button = Gtk.ToggleButton(icon_name="view-pin-symbolic",
                                           action_name="win.toggle-pin",
                                           tooltip_text="Pin (Ctrl+D)")
        hb.pack_end(self.pin_button)
        hb.pack_end(Gtk.Button(icon_name="object-select-symbolic", action_name="win.close-with",
                               tooltip_text="Close With… (Ctrl+K)"))
        hb.pack_end(Gtk.Button(icon_name="gtd-archive-symbolic",
                               action_name="win.move-to-archive",
                               tooltip_text="Archive (Ctrl+6)"))
        self.move_button = Gtk.MenuButton(tooltip_text="Move To (Ctrl+M)", can_shrink=True)
        self.move_button.set_child(Adw.ButtonContent(icon_name="go-jump-symbolic",
                                                     label="Move To", can_shrink=True))
        hb.pack_end(self.move_button)
        self.header = hb
        self.toolbar.add_top_bar(hb)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.none_page = Adw.StatusPage(icon_name="mail-unread-symbolic",
                                        title="No Email Selected")
        self.stack.add_named(self.none_page, "none")
        self.multi_page = Adw.StatusPage(icon_name="edit-select-all-symbolic")
        mbox = vbox(12, halign=Gtk.Align.CENTER)
        self.multi_move = Gtk.MenuButton(label="Move To")
        self.multi_move.add_css_class("pill")
        mbox.append(self.multi_move)
        for text, action in (("Archive", "win.move-to-archive"), ("Pin / Unpin", "win.toggle-pin")):
            b = Gtk.Button(label=text, action_name=action)
            b.add_css_class("pill")
            mbox.append(b)
        self.multi_page.set_child(mbox)
        self.stack.add_named(self.multi_page, "multi")
        self.content = vbox(18, margin_top=18, margin_bottom=30, margin_start=18,
                            margin_end=18)
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=600, child=self.content)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_child(clamp)
        self.stack.add_named(self.scroller, "email")
        self.toolbar.set_content(self.stack)
        self.show_none()

    # ------------------------------------------------------------ public

    def set_move_menu(self, model):
        self.move_button.set_menu_model(model)
        self.multi_move.set_menu_model(model)

    def show_none(self):
        self.record = None
        self._signature = None
        self.stack.set_visible_child_name("none")

    def show_multiple(self, n):
        self.record = None
        self._signature = None
        self.multi_page.set_title(f"{n} Emails Selected")
        self.stack.set_visible_child_name("multi")

    def show_record(self, r, ctx):
        signature = (r.id, tuple(sorted(r.row.items())), id(r.parsed), ctx["fmt_key"],
                     tuple(ctx["projects"]))
        if signature == self._signature:
            return
        same = self.record is not None and self.record.id == r.id
        scroll = self.scroller.get_vadjustment().get_value() if same else 0.0
        self.record = r
        self._signature = signature
        self._build(r, ctx)
        self.stack.set_visible_child_name("email")
        GLib.idle_add(self._restore, scroll)

    def focus_next_action(self):
        row = self._fields.get("next_action")
        if row is not None and row.get_sensitive():
            row.grab_focus()

    def _restore(self, scroll):
        self.scroller.get_vadjustment().set_value(scroll)
        if self._refocus and self._refocus in self._fields:
            self._fields[self._refocus].grab_focus()
        self._refocus = None
        return False

    # ------------------------------------------------------------ building

    def _build(self, r, ctx):
        c = self.content
        child = c.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            c.remove(child)
            child = nxt
        self._fields = {}
        fmt = ctx["fmt"]
        c.append(self._tags(r, ctx))
        c.append(label(r.subject, "title-1", wrap=True, selectable=True))
        c.append(self._annotations(r, ctx))
        if r.tracked or r.notes:
            c.append(self._notes(r, ctx))
        c.append(self._workflow(r, fmt))
        c.append(self._message(r, fmt))

    def _tags(self, r, ctx):
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                          max_children_per_line=12, column_spacing=6, row_spacing=6,
                          homogeneous=False, valign=Gtk.Align.START)
        fmt = ctx["fmt"]
        items = []
        if r.age_days is not None:
            kind = {"green": "success", "yellow": "warning", "red": "error"}.get(r.age_class)
            items.append(tag(plural(r.age_days, "day"), kind, "alarm-symbolic", "Age"))
        items.append(tag(r.folder.title, "accent", r.folder.icon, "Folder"))
        if r.account:
            items.append(tag(r.account["display_name"], None, "avatar-default-symbolic",
                             r.account["email_address"]))
        if r.status:
            items.append(tag(r.status.capitalize(), "warning" if r.status == "weird" else None,
                             "emblem-ok-symbolic" if r.status == "resolved" else None,
                             "Workflow status"))
        if r.is_pinned:
            items.append(tag("Pinned", "accent", "view-pin-symbolic"))
        if r.due_date:
            overdue = r.is_overdue(ctx["today"])
            items.append(tag("Due " + fmt.day_text(r.due_date, style=fmt.compact_style()),
                             "error" if overdue else "warning", "task-due-symbolic",
                             "Overdue" if overdue else "Due date"))
        for w in items:
            child = Gtk.FlowBoxChild(child=w, halign=Gtk.Align.START, focusable=False)
            box.append(child)
        return box

    def _entry_row(self, r, field, title, editable):
        row = Adw.EntryRow(title=title, show_apply_button=True)
        original = r.row.get(field, "") or ""
        row.set_text(original)
        row.set_sensitive(editable)
        row.connect("apply", lambda w: self._apply(r, field, w.get_text()))
        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _state):
            if keyval == Gdk.KEY_Escape and row.get_text() != original:
                row.set_text(original)
                return True
            return False
        keys.connect("key-pressed", on_key)
        row.add_controller(keys)
        self._fields[field] = row
        return row

    def _apply(self, r, field, value):
        self._refocus = field
        self.window.save_fields(r.id, {field: value})

    def _annotations(self, r, ctx):
        editable = ctx["editable"]
        group = Adw.PreferencesGroup(title="Annotations")
        if not editable:
            group.set_description(ctx["edit_refusal"])
        group.add(self._entry_row(r, "next_action", "Next Action", editable))

        project = self._entry_row(r, "project", "Project", editable)
        if ctx["projects"]:
            pop = Gtk.Popover()
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("navigation-sidebar")
            for name in ctx["projects"]:
                lb.append(_padded(label(name)))
            sw = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=320,
                                    propagate_natural_width=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
            sw.set_child(lb)
            pop.set_child(sw)

            def chosen(_lb, row):
                pop.popdown()
                name = ctx["projects"][row.get_index()]
                project.set_text(name)
                self._apply(r, "project", name)
            lb.connect("row-activated", chosen)
            mb = Gtk.MenuButton(icon_name="pan-down-symbolic", popover=pop,
                                valign=Gtk.Align.CENTER, tooltip_text="Choose an Existing Project")
            mb.add_css_class("flat")
            mb.set_sensitive(editable)
            project.add_suffix(mb)
        group.add(project)

        group.add(self._due_row(r, ctx, editable))
        group.add(self._entry_row(r, "flags", "Flags", editable))
        if r.ref:
            ref = Adw.ActionRow(title="Message Ref", subtitle=r.ref, subtitle_selectable=True)
            ref.add_css_class("property")
            group.add(ref)
        return group

    def _due_row(self, r, ctx, editable):
        fmt = ctx["fmt"]
        raw = (r.row.get("due_date", "") or "").strip()
        row = Adw.ActionRow(title="Due Date")
        if not raw:
            row.set_subtitle("Not set")
        elif is_iso_date(raw):
            try:
                row.set_subtitle(fmt.date_text(date.fromisoformat(raw), style="long"))
            except ValueError:
                row.set_subtitle(raw)
        else:
            row.set_subtitle(f"“{raw}” — not a date, so it cannot be overdue; pick a date "
                             "to replace it")
        if r.is_overdue(ctx["today"]):
            row.add_css_class("error")
        clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Clear Due Date", sensitive=editable and bool(raw))
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self.window.save_fields(r.id, {"due_date": ""}))
        pop = Gtk.Popover()
        box = vbox(8, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        cal = Gtk.Calendar()
        start = r.due_day or ctx["today"]
        cal.select_day(GLib.DateTime.new_local(start.year, start.month, start.day, 12, 0, 0))
        box.append(cal)
        setb = Gtk.Button(label="Set Due Date")
        setb.add_css_class("suggested-action")

        def set_date(*_):
            d = cal.get_date()
            pop.popdown()
            value = f"{d.get_year():04d}-{d.get_month():02d}-{d.get_day_of_month():02d}"
            self.window.save_fields(r.id, {"due_date": value})
        setb.connect("clicked", set_date)
        box.append(setb)
        pop.set_child(box)
        pick = Gtk.MenuButton(icon_name="x-office-calendar-symbolic", popover=pop,
                              valign=Gtk.Align.CENTER, tooltip_text="Pick a Date",
                              sensitive=editable)
        pick.add_css_class("flat")
        row.add_suffix(clear)
        row.add_suffix(pick)
        row.set_activatable_widget(pick)
        return row

    def _notes(self, r, ctx):
        editable = ctx["editable"]
        group = Adw.PreferencesGroup(title="Notes")
        original = r.row.get("general_notes", "") or ""
        buf = Gtk.TextBuffer(text=original)
        view = Gtk.TextView(buffer=buf, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                            editable=editable, top_margin=10, bottom_margin=10,
                            left_margin=12, right_margin=12)
        view.add_css_class("notes-view")
        view.set_size_request(-1, 72)
        frame = Gtk.Frame(child=view)
        frame.add_css_class("view")
        frame.add_css_class("card")
        group.add(frame)
        buttons = Gtk.Box(spacing=6, visible=False)
        revert = Gtk.Button(label="Revert")
        revert.add_css_class("flat")
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        buttons.append(revert)
        buttons.append(apply)
        group.set_header_suffix(buttons)

        def text():
            return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        buf.connect("changed", lambda *_: buttons.set_visible(text() != original))
        revert.connect("clicked", lambda *_: buf.set_text(original))
        apply.connect("clicked", lambda *_: self._apply(r, "general_notes", text()))
        self._fields["general_notes"] = view
        return group

    def _workflow(self, r, fmt):
        group = Adw.PreferencesGroup(title="Workflow")
        if not r.stamps:
            group.set_description("Not ingested yet." if not r.tracked
                                  else "No date stamps recorded.")
        for _field, lab, value in r.stamps:
            row = Adw.ActionRow(title=lab)
            row.add_suffix(label(fmt.day_text(value), "dim-label", "numeric"))
            group.add(row)
        if r.metrics:
            exp = Adw.ExpanderRow(title="Metrics",
                                  subtitle=" · ".join(f"{k} {v} d" for k, v in r.metrics.items()))
            for key, name, span in METRIC_NAMES:
                if key in r.metrics:
                    mrow = Adw.ActionRow(title=name, subtitle=f"{key}: {span}")
                    mrow.add_suffix(label(plural(r.metrics[key], "day"), "numeric"))
                    exp.add_row(mrow)
            group.add(exp)
        if r.status == "weird":
            warn = Adw.ActionRow(title="Inconsistent date progression",
                                 subtitle="Left out of the performance figures until the "
                                          "stamps make sense.")
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            warn.add_prefix(icon)
            group.add(warn)
        return group

    def _message(self, r, fmt):
        group = Adw.PreferencesGroup(title="Message")
        p = r.parsed
        card = vbox(12)
        card.add_css_class("card")
        card.add_css_class("thread-card")
        if p.error:
            err = label(f"This file could not be read: {p.error}", "error", wrap=True)
            card.append(err)
        date_text = fmt.full(p.date) if p.date else "(no usable Date header)"
        card.append(header_grid([("From", p.from_text), ("To", p.to_text), ("Cc", p.cc_text),
                                 ("Bcc", p.bcc_text), ("Date", date_text if not p.error else ""),
                                 ("File", f"{r.folder.dir}/{r.filename}")]))
        if p.attachments:
            att = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                              column_spacing=6, row_spacing=6)
            for name in p.attachments:
                att.append(tag(name, None, "mail-attachment-symbolic"))
            card.append(att)
        thread = p.thread
        first_plain = bool(thread) and not _has_headers(thread[0])
        if first_plain or (not thread and not p.error):
            card.append(Gtk.Separator())
            card.append(_body_label(thread[0]["text"] if thread else "(no text)"))
        group.add(card)
        opened = self._expanded.setdefault(r.id, {i for i in range(len(thread)) if i < 2})
        for i, m in enumerate(thread):
            if i == 0 and first_plain:
                continue
            group.add(self._quoted(r.id, i, m, fmt, opened))
        return group

    def _quoted(self, rid, index, m, fmt, opened):
        who = display_name(m["from"]) if m.get("from") else "Quoted message"
        when = fmt.quoted(m.get("date_parts"), m["date"]) if m.get("date") else ""
        exp = Gtk.Expander(label=f"{who} — {when}" if when else who, expanded=index in opened)
        exp.get_label_widget().add_css_class("heading")
        inner = vbox(10, margin_top=10)
        inner.append(header_grid([("From", m.get("from", "")), ("To", m.get("to", "")),
                                  ("Cc", m.get("cc", "")), ("Subject", m.get("subject", "")),
                                  ("Date", when)]))
        inner.append(_body_label(m.get("text", "")))
        exp.set_child(inner)

        def toggled(e, _p):
            (opened.add if e.get_expanded() else opened.discard)(index)
        exp.connect("notify::expanded", toggled)
        card = vbox()
        card.add_css_class("card")
        card.add_css_class("thread-card")
        card.set_margin_start(16 * min(m.get("indent", m.get("depth", 0)), 4))
        card.set_margin_top(10)
        card.append(exp)
        return card


def _has_headers(m):
    return any(k in m for k in ("from", "to", "cc", "subject", "date"))


def _body_label(text):
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + "\n\n[…] (truncated here; open the email to read it all)"
    return label(text, wrap=True, selectable=True, ellipsize=False)


def _padded(widget):
    widget.set_margin_start(6)
    widget.set_margin_end(6)
    widget.set_margin_top(4)
    widget.set_margin_bottom(4)
    return widget


def Gio_menu(entries):
    from gi.repository import Gio
    m = Gio.Menu()
    for text, action in entries:
        m.append(text, action)
    return m
