"""Overview → Dashboard: folder counts, Needs Attention, Projects."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import folders as F  # noqa: E402
from ..ui_prefs import plural  # noqa: E402
from .gtk4_pages import OverviewPage  # noqa: E402
from .gtk4_widgets import label, vbox  # noqa: E402


class DashboardPage(OverviewPage):
    def __init__(self, window):
        super().__init__(window, "Dashboard")

    def update(self, overview, subtitle):
        self.title.set_subtitle(subtitle)
        self.clear()
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=2,
                           max_children_per_line=3, column_spacing=12, row_spacing=12,
                           homogeneous=True)
        for f in F.FOLDERS:
            b = Gtk.Button(action_name=f"win.go-{f.alias}", tooltip_text=f"Show {f.dir}")
            b.add_css_class("card")
            inner = vbox(4)
            inner.add_css_class("stat-card")
            top = Gtk.Box(spacing=6)
            top.append(Gtk.Image.new_from_icon_name(f.icon))
            top.append(label(f.title, "dim-label"))
            inner.append(top)
            inner.append(label(str(overview.counts[f.alias]), "folder-count"))
            b.set_child(inner)
            flow.append(b)
        self.body.append(flow)

        att = Adw.PreferencesGroup(title="Needs Attention")
        for fig in overview.attention:
            row = Adw.ActionRow(title=fig.label, subtitle=fig.hint, activatable=fig.count > 0)
            num = label(str(fig.count), "title-2", "numeric", xalign=1.0)
            num.set_width_chars(3)
            if not fig.count:
                num.add_css_class("dim-label")
            row.add_prefix(num)
            if fig.count:
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", lambda _r, k=fig.key:
                            self.window.open_result(("attention", k)))
            att.add(row)
        self.body.append(att)

        if overview.projects:
            proj = Adw.PreferencesGroup(title="Projects")
            for name, ids in overview.projects:
                row = Adw.ActionRow(title=name, activatable=True)
                row.add_suffix(label(str(len(ids)), "dim-label", "numeric"))
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", lambda _r, n=name:
                            self.window.open_result(("project", n)))
                proj.add(row)
            self.body.append(proj)

        st = overview.statuses
        parts = ", ".join(f"{st.get(k, 0)} {k}" for k in ("ongoing", "resolved", "weird",
                                                           "untracked"))
        self.body.append(label(f"{plural(overview.total, 'email')}, {overview.tracked} tracked "
                               f"in metadata.csv. Statuses: {parts}.", "dim-label", "caption",
                               wrap=True))
