"""Dialogs. Everything is asynchronous (no dialog.run()): each dialog calls
back into the window, which performs the action and closes it."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..ui_prefs import is_are, plural  # noqa: E402
from .gtk4_widgets import label, vbox  # noqa: E402


def alert(parent, heading, body):
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("ok", "OK")
    d.set_default_response("ok")
    d.present(parent)
    return d


def confirm(parent, heading, body, ok_label, on_ok, destructive=False, extra=None):
    """A two-button alert; `extra` = (label, callback) adds a third response."""
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("cancel", "Cancel")
    if extra:
        d.add_response("extra", extra[0])
    d.add_response("ok", ok_label)
    d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                              else Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("ok")
    d.set_close_response("cancel")

    def done(_d, response):
        if response == "ok":
            on_ok()
        elif response == "extra" and extra:
            extra[1]()
    d.connect("response", done)
    d.present(parent)
    return d


class _ActionDialog(Adw.Dialog):
    """A dialog with Cancel and one suggested action in its header bar."""

    def __init__(self, title, action_label=None, width=600, height=560, destructive=False):
        super().__init__(title=title, content_width=width, content_height=height)
        self.toolbar = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        self.cancel = Gtk.Button(label="Cancel")
        self.cancel.connect("clicked", lambda *_: self.close())
        hb.pack_start(self.cancel)
        self.action = None
        if action_label:
            self.action = Gtk.Button(label=action_label)
            self.action.add_css_class("destructive-action" if destructive else "suggested-action")
            hb.pack_end(self.action)
            self.set_default_widget(self.action)
        self.header = hb
        self.toolbar.add_top_bar(hb)
        self.set_child(self.toolbar)


class CloseWithDialog(_ActionDialog):
    """`gtd close <file> with <other>`: choose the email that closed this one."""

    def __init__(self, window, record, candidates, fmt):
        super().__init__("Close With", "Close Email", 580, 600)
        self.window = window
        self.record = record
        self.chosen = None
        body = vbox(12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        body.append(label(f"Archive “{record.subject}”, set its next action to “Closed with …” "
                          "and stamp ds_archive with today’s date.", "dim-label", wrap=True))
        self.search = Gtk.SearchEntry(placeholder_text="Filter by subject, correspondent or "
                                                       "filename")
        body.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("boxed-list")
        self._rows = []
        for c in candidates:
            row = Adw.ActionRow(title=c.list_correspondent, subtitle=c.subject,
                                title_lines=1, subtitle_lines=1)
            row.add_suffix(label(fmt.list_date(c.date), "dim-label", "caption"))
            row.set_tooltip_text(f"{c.folder.title} · {c.filename}")
            row.record = c
            row.haystack = f"{c.filename}\n{c.subject}\n{c.list_correspondent}".lower()
            self.list.append(row)
            self._rows.append(row)
        self.list.set_filter_func(self._filter)
        self.list.set_placeholder(Adw.StatusPage(icon_name="edit-find-symbolic",
                                                 title="No Matching Emails"))
        self.list.connect("row-selected", self._selected)
        self.list.connect("row-activated", lambda *_: self._close())
        self.search.connect("search-changed", lambda *_: self.list.invalidate_filter())
        self.search.connect("activate", lambda *_: self._close())
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.list)
        body.append(sw)
        self.toolbar.set_content(body)
        self.action.set_sensitive(False)
        self.action.connect("clicked", lambda *_: self._close())
        self.set_focus(self.search)

    def _filter(self, row):
        q = self.search.get_text().strip().lower()
        return not q or q in row.haystack

    def _selected(self, _lb, row):
        self.chosen = row.record if row is not None else None
        self.action.set_sensitive(self.chosen is not None)

    def _close(self):
        if self.chosen is None:
            return
        other = self.chosen
        self.close()
        self.window.close_with(self.record.id, other.id)


class ReviewDateStampsDialog(_ActionDialog):
    """`gtd workflow_autofix`: every proposed stamp, applied all at once."""

    def __init__(self, window, plan):
        if plan.pending_input:
            act = "Ingest"
        elif plan.fixes and not plan.blockers:
            act = "Apply All"
        else:
            act = None
        super().__init__("Review Date Stamps", act, 640, 560)
        self.window = window
        self.plan = plan
        if act is None:
            self.cancel.set_label("Done")
        page = Adw.PreferencesPage()
        if plan.pending_input:
            status = Adw.StatusPage(
                icon_name="gtd-input-symbolic", title="Ingest Input First",
                description=f"{plural(plan.pending_input, 'file')} {is_are(plan.pending_input)} "
                            "still in 01-input, so date stamps cannot be fixed yet.")
            self.toolbar.set_content(status)
            self.action.connect("clicked", lambda *_: self._ingest())
            return
        if plan.blockers:
            group = Adw.PreferencesGroup(
                title=f"Needs Manual Attention ({len(plan.blockers)})",
                description="These emails cannot be fixed automatically. File each into the "
                            "folder that matches its actual state, then review again. Nothing "
                            "will be changed.")
            for b in plan.blockers:
                group.add(_mono_row(b["filename"], f"[{b['folder']}] {b['reason']}"))
            page.add(group)
        elif not plan.fixes:
            self.toolbar.set_content(Adw.StatusPage(
                icon_name="emblem-ok-symbolic", title="Date Stamps Are Consistent",
                description="metadata.csv is already consistent; no changes needed."))
            return
        else:
            intro = Adw.PreferencesGroup(
                description=f"{plural(len(plan.fixes), 'stamp')} will be written to "
                            "metadata.csv. If you track the workspace with git, you may want to "
                            "commit first, so these changes are easy to reverse.")
            page.add(intro)
            titles = {"folder-consistency": "Folder/stamp consistency",
                      "date-backfill": "Logical-progression date backfills"}
            for phase in ("folder-consistency", "date-backfill"):
                fixes = [f for f in plan.fixes if f["phase"] == phase]
                if not fixes:
                    continue
                group = Adw.PreferencesGroup(title=f"{titles[phase]} ({len(fixes)})")
                for f in fixes:
                    old = f["old"] or "(unset)"
                    group.add(_mono_row(f["filename"],
                                        f"[{f['folder']}] {f['field']}: {old} → {f['new']}"))
                page.add(group)
            self.action.connect("clicked", lambda *_: self.window.apply_autofix(self.plan, self))
        self.toolbar.set_content(page)

    def _ingest(self):
        self.close()
        self.window.ingest(then_review=True)


class CheckMetadataDialog(_ActionDialog):
    """`gtd metadata_check`: what reconciling would drop, and dangling refs."""

    def __init__(self, window, report):
        super().__init__("Check Metadata", "Reconcile", 620, 520,
                         destructive=bool(report.missing_files))
        self.window = window
        page = Adw.PreferencesPage()
        intro = Adw.PreferencesGroup(
            description="Reconciling adds a row to metadata.csv for every .eml file that lacks "
                        "one and removes rows whose file no longer exists. It never moves or "
                        "renames emails.")
        page.add(intro)
        g1 = Adw.PreferencesGroup(title=f"Rows Whose File No Longer Exists "
                                        f"({len(report.missing_files)})")
        if report.missing_files:
            g1.set_description("Reconciling drops these rows, including their notes.")
        else:
            g1.add(Adw.ActionRow(title="None"))
        for name in report.missing_files:
            g1.add(_mono_row(name, None))
        page.add(g1)
        g2 = Adw.PreferencesGroup(title=f"Next Actions Naming a Missing .eml "
                                        f"({len(report.dangling_refs)})")
        if not report.dangling_refs:
            g2.add(Adw.ActionRow(title="None"))
        for name, ref in report.dangling_refs:
            g2.add(_mono_row(name, f"next_action → {ref}"))
        page.add(g2)
        self.toolbar.set_content(page)
        self.action.connect("clicked", lambda *_: self.window.reconcile_metadata(self))


def _mono_row(title, subtitle):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "", title_lines=1)
    row.set_tooltip_text(title)
    return row
