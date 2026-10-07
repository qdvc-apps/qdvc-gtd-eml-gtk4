"""Overview → Calendar: a month heatmap, like GNOME Calendar's month view.

Each day is shaded by how many emails had an event that day (received, quoted,
or a ds_* stamp), relative to the busiest day of the month shown. Clicking a
day lists its emails as a Results entry. Weeks start on Monday."""

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..calendar_index import EVENT_KINDS, month_days, shift_month  # noqa: E402
from ..dates import MONTHS, WEEKDAYS  # noqa: E402
from ..ui_prefs import plural  # noqa: E402
from .gtk4_pages import OverviewPage  # noqa: E402
from .gtk4_widgets import label, vbox  # noqa: E402


def heat_level(count, busiest):
    if not count or not busiest:
        return 0
    return max(1, min(5, round(5 * count / busiest)))


class CalendarPage(OverviewPage):
    def __init__(self, window):
        super().__init__(window, "Calendar")
        t = date.today()
        self.month = date(t.year, t.month, 1)
        nav = Gtk.Box(valign=Gtk.Align.CENTER)
        nav.add_css_class("linked")
        prev = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Previous Month")
        prev.connect("clicked", lambda *_: self.go(-1))
        nxt = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Next Month")
        nxt.connect("clicked", lambda *_: self.go(1))
        nav.append(prev)
        nav.append(nxt)
        self.today_button = Gtk.Button(label="Today", valign=Gtk.Align.CENTER)
        self.today_button.connect("clicked", lambda *_: self.go(0))
        # Beside the month title (not in the header bar, which must stay narrow).
        self.month_title = label("", "title-1", hexpand=True)
        self.month_row = Gtk.Box(spacing=12)
        self.month_row.append(self.month_title)
        self.month_row.append(self.today_button)
        self.month_row.append(nav)

    def go(self, months):
        if months == 0:
            t = date.today()
            self.month = date(t.year, t.month, 1)
        else:
            self.month = shift_month(self.month, months)
        self.window.refresh_page(self)

    def update(self, ctx):
        self.title.set_subtitle(ctx["subtitle"])
        index, today = ctx["calendar"], ctx["fmt"].today()
        self.today_button.set_sensitive((self.month.year, self.month.month) !=
                                        (today.year, today.month))
        self.clear()
        days = month_days(self.month)
        counts = [index.count(d) for d in days]
        busiest = max(counts) if counts else 0
        self.month_title.set_label(f"{MONTHS[self.month.month - 1]} {self.month.year}")
        self.body.append(self.month_row)

        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        for i, name in enumerate(WEEKDAYS):
            grid.attach(label(name[:3], "caption-heading", "dim-label", xalign=0.5), i, 0, 1, 1)
        lead = days[0].weekday()
        for i, d in enumerate(days):
            pos = lead + i
            n = counts[i]
            cell = vbox()
            cell.append(label(str(d.day), "numeric", "heading" if d == today else "dim-label"
                              if not n else "numeric"))
            cell.append(Gtk.Box(vexpand=True))
            if n:
                cell.append(label(str(n), "title-3", "numeric", xalign=1.0))
            b = Gtk.Button(child=cell, sensitive=n > 0)
            b.add_css_class("flat")
            b.add_css_class("day-cell")
            level = heat_level(n, busiest)
            if level:
                b.add_css_class(f"heat-{level}")
            if d == today:
                b.add_css_class("day-today")
            if n:
                b.set_tooltip_text(f"{plural(n, 'email')} — click to list them")
                b.connect("clicked", lambda _b, day=d: self.window.open_result(("day", day)))
            grid.attach(b, pos % 7, 1 + pos // 7, 1, 1)
        self.body.append(grid)

        legend = Gtk.Box(spacing=6)
        legend.append(label("Fewer", "caption", "dim-label"))
        for lv in range(1, 6):
            sw = Gtk.Box(valign=Gtk.Align.CENTER)
            sw.add_css_class("legend-swatch")
            sw.add_css_class(f"heat-{lv}")
            legend.append(sw)
        legend.append(label("More", "caption", "dim-label"))
        ids = set()
        for d in days:
            ids.update(index.days.get(d, {}))
        active = sum(1 for n in counts if n)
        summary = (f"{plural(len(ids), 'email')} on {plural(active, 'day')}" if active
                   else "No events this month")
        legend.append(label(summary, "caption", "dim-label", xalign=1.0, hexpand=True))
        self.body.append(legend)

        totals = []
        for kind, title, _field in EVENT_KINDS:
            who = set()
            for d in days:
                for rid, kinds in index.days.get(d, {}).items():
                    if kind in kinds:
                        who.add(rid)
            if who:
                totals.append((title, len(who)))
        if totals:
            group = Adw.PreferencesGroup(title="This Month")
            for title, n in totals:
                row = Adw.ActionRow(title=title)
                row.add_suffix(label(str(n), "dim-label", "numeric"))
                group.add(row)
            self.body.append(group)
        self.body.append(label(
            "Counts distinct emails per day with any event: the email's Date header, the dates "
            "of messages quoted in it, and the ds_* stamps in metadata.csv. Header dates use the "
            "time zone chosen in Preferences; stamps are used as written. Due dates are not "
            "events.", "dim-label", "caption", wrap=True))
