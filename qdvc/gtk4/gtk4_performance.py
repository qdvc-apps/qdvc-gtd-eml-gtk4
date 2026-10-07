"""Overview → Performance: the open backlog and throughput sections of the
CLI's performance dashboard (dashboard.build_backlog_stats,
build_backlog_age_histogram and build_flow_series). As `generate_dashboard`
does, figures are shown only once 01-input is empty and the date stamps are
consistent; otherwise the page says what to do first."""

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..dates import MONTHS  # noqa: E402
from ..gtd_modules import dashboard  # noqa: E402
from ..ui_prefs import is_are, plural  # noqa: E402
from .gtk4_charts import Chart  # noqa: E402
from .gtk4_pages import OverviewPage  # noqa: E402
from .gtk4_widgets import label, vbox  # noqa: E402


class PerformancePage(OverviewPage):
    def __init__(self, window):
        super().__init__(window, "Performance", maximum=980)
        self.period = "monthly"
        self.account = None
        self._accounts = []
        self._building = False
        # View options live in the page, not the header bar, so the header
        # stays narrow enough for phone-sized windows.
        self.toggles = Gtk.Box(valign=Gtk.Align.CENTER)
        self.toggles.add_css_class("linked")
        self.weekly = Gtk.ToggleButton(label="Weekly")
        self.monthly = Gtk.ToggleButton(label="Monthly", group=self.weekly, active=True)
        for b in (self.weekly, self.monthly):
            b.connect("toggled", self._on_period)
            self.toggles.append(b)
        self.account_group = Adw.PreferencesGroup()
        self.accounts = Adw.ComboRow(title="Account", model=Gtk.StringList.new(["All Accounts"]))
        self.accounts.connect("notify::selected", self._on_account)
        self.account_group.add(self.accounts)

    def _on_period(self, button):
        if button.get_active():
            self.period = "weekly" if button is self.weekly else "monthly"
            self.window.refresh_page(self)

    def _on_account(self, *_):
        if self._building:
            return
        i = self.accounts.get_selected()
        self.account = self._accounts[i - 1]["email_address"] if 0 < i <= len(self._accounts) \
            else None
        self.window.refresh_page(self)

    def update(self, ctx):
        self.title.set_subtitle(ctx["subtitle"])
        accounts = ctx["wcfg"].my_own_accounts
        if accounts != self._accounts:
            self._building = True
            self._accounts = accounts
            self.accounts.set_model(Gtk.StringList.new(
                ["All Accounts"] + [a["display_name"] for a in accounts]))
            emails = [a["email_address"] for a in accounts]
            self.accounts.set_selected(emails.index(self.account) + 1
                                       if self.account in emails else 0)
            if self.account not in emails:
                self.account = None
            self._building = False
        plan = ctx["autofix"]
        if plan.pending_input:
            self.show_status("gtd-chart-symbolic", "Ingest Input First",
                             f"{plural(plan.pending_input, 'file')} {is_are(plan.pending_input)} "
                             "still in 01-input. Performance figures need a complete workflow "
                             "history.", "Ingest Input", "win.ingest")
            return
        if plan.needs_review:
            text = (f"{plural(len(plan.blockers), 'email')} need manual attention"
                    if plan.blockers else
                    f"{plural(len(plan.fixes), 'date stamp')} {is_are(len(plan.fixes))} missing "
                    "or inconsistent")
            self.show_status("gtd-chart-symbolic", "Review Date Stamps First",
                             text + ". Performance figures need consistent date stamps.",
                             "Review Date Stamps…", "win.review-date-stamps")
            return
        computed = ctx["computed"]
        if self.account:
            names = {r.filename for r in ctx["records"]
                     if r.tracked and r.account and r.account["email_address"] == self.account}
            computed = [m for m in computed if m["filename"] in names]
        self.clear()
        if accounts:
            self.body.append(self.account_group)
        self._backlog(computed, ctx["today"])
        self._throughput(computed)

    def _backlog(self, computed, today):
        stats = dashboard.build_backlog_stats(computed, today)
        group = Adw.PreferencesGroup(title="Open Backlog")
        self.body.append(group)
        if not stats["count"]:
            group.set_description("No ongoing emails — the backlog is empty.")
            return

        def days(v, digits=0):
            if v is None:
                return "–"
            return f"{v:.1f} days" if digits else plural(int(v), "day")
        cards = [(str(stats["count"]), "open now", ""),
                 (days(stats["median_age"], 1), "median age", ""),
                 (days(stats["mean_age"], 1), "mean age", ""),
                 (str(stats["stale_count"]), f"older than {stats['stale_threshold']} days", ""),
                 (days(stats["oldest_age"]), "oldest open", stats["oldest_filename"] or ""),
                 (days(stats["longest_stall_days"]), "longest stall",
                  f"in {stats['longest_stall_stage']}: {stats['longest_stall_filename']}"
                  if stats["longest_stall_filename"] else "")]
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=2,
                           max_children_per_line=3, column_spacing=12, row_spacing=12,
                           homogeneous=True)
        for value, title, sub in cards:
            card = vbox(2)
            card.add_css_class("card")
            card.add_css_class("stat-card")
            card.append(label(value, "stat-value"))
            card.append(label(title, "dim-label"))
            if sub:
                s = label(sub, "caption", "dim-label")
                s.set_ellipsize(2)  # middle
                s.set_tooltip_text(sub)
                card.append(s)
            flow.append(card)
        self.body.append(flow)
        labels, counts = dashboard.build_backlog_age_histogram(computed, today)
        self.body.append(label("Current pile by age (days)", "heading"))
        chart = Chart(190, "bar")
        chart.set_data(labels, [("Emails", counts, "blue")], show_values=True)
        self.body.append(chart)

    def _throughput(self, computed):
        flow = dashboard.build_flow_series(computed, self.period)
        unit = "week" if self.period == "weekly" else "month"
        group = Adw.PreferencesGroup(title="Throughput")
        parent = self.toggles.get_parent()
        if parent is not None:
            parent.remove(self.toggles) if hasattr(parent, "remove") else None
        group.set_header_suffix(self.toggles)
        self.body.append(group)
        if not flow["periods"]:
            group.set_description("No activity yet.")
            return
        labels = [_period_label(p, self.period) for p in flow["periods"]]
        self.body.append(label(f"Arrivals and resolutions per {unit}", "heading"))
        bars = Chart(220, "bar")
        bars.set_data(labels, [("Arrived", flow["arrivals"], "blue"),
                               ("Resolved", flow["resolutions"], "green")])
        self.body.append(bars)
        self.body.append(label(f"Open backlog at the end of each {unit}", "heading"))
        line = Chart(190, "line")
        line.set_data(labels, [("Open backlog", flow["backlog"], "orange")])
        self.body.append(line)
        self.body.append(label("Weird emails (inconsistent date progression) are excluded, as "
                               "in the CLI's dashboard.", "dim-label", "caption", wrap=True))


def _period_label(iso, period):
    d = date.fromisoformat(iso)
    if period == "weekly":
        return f"{d.day} {MONTHS[d.month - 1][:3]}"
    return f"{MONTHS[d.month - 1][:3]} {d.year}"
