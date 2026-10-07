"""Preferences (Adw.PreferencesDialog, live-apply).

General: date format, time zone, quoted times with no zone, the on-radar
folders, dimming, reopening the last workspace. Workspace: a read-only summary
of the workspace's workspace.yml, which the CLI reads too."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import folders as F  # noqa: E402
from ..dates import DATE_STYLES, NAIVE_MODES, time_zone_names  # noqa: E402
from ..workspace_config import FILE_NAME  # noqa: E402


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__()
        self.window = window
        cfg = window.config
        self.set_search_enabled(False)

        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        dates = Adw.PreferencesGroup(title="Dates")
        style = Adw.ComboRow(title="Date Format",
                             model=Gtk.StringList.new([ex for _, ex in DATE_STYLES]))
        keys = [k for k, _ in DATE_STYLES]
        style.set_selected(keys.index(cfg.get("date_style")) if cfg.get("date_style") in keys
                           else 0)
        style.connect("notify::selected", lambda r, _p: self._set("date_style",
                                                                   keys[r.get_selected()]))
        dates.add(style)
        import time as _time
        self.zones = [""] + time_zone_names()
        system = _time.tzname[0] if _time.tzname else "local"
        zone = Adw.ComboRow(title="Time Zone", subtitle="For the emails’ own Date headers",
                            enable_search=True,
                            model=Gtk.StringList.new([f"System ({system})"] + self.zones[1:]))
        current = cfg.get("time_zone") or ""
        zone.set_selected(self.zones.index(current) if current in self.zones else 0)
        zone.connect("notify::selected",
                     lambda r, _p: self._set("time_zone", self.zones[r.get_selected()]))
        dates.add(zone)
        nkeys = [k for k, _ in NAIVE_MODES]
        naive = Adw.ComboRow(title="Quoted Times With No Zone",
                             subtitle="Times inside quoted messages that name no time zone",
                             model=Gtk.StringList.new([t for _, t in NAIVE_MODES]))
        naive.set_selected(nkeys.index(cfg.get("naive_dates")) if cfg.get("naive_dates") in nkeys
                           else 0)
        naive.connect("notify::selected", lambda r, _p: self._set("naive_dates",
                                                                   nkeys[r.get_selected()]))
        dates.add(naive)
        page.add(dates)

        radar = Adw.PreferencesGroup(
            title="On the Radar",
            description="Filters list only emails in these folders. Inbox and Sent always cover "
                        "every folder.")
        for f in F.FOLDERS:
            row = Adw.SwitchRow(title=f.title, active=f in cfg.radar_folders)
            row.connect("notify::active", lambda r, _p, folder=f: self._radar(folder,
                                                                            r.get_active()))
            radar.add(row)
        dim = Adw.SwitchRow(title="Dim Emails Outside These Folders",
                            subtitle="In search results, and their folders in the sidebar",
                            active=cfg.flag("dim_off_radar"))
        dim.connect("notify::active", lambda r, _p: self._set("dim_off_radar", r.get_active()))
        radar.add(dim)
        page.add(radar)

        start = Adw.PreferencesGroup(title="Startup")
        reopen = Adw.SwitchRow(title="Reopen the Last Workspace", active=cfg.flag("reopen_last"))
        reopen.connect("notify::active", lambda r, _p: self._set("reopen_last", r.get_active()))
        start.add(reopen)
        page.add(start)
        self.add(page)
        self.add(self._workspace_page())

    def _workspace_page(self):
        w = self.window
        page = Adw.PreferencesPage(title="Workspace", icon_name="folder-symbolic")
        group = Adw.PreferencesGroup(
            title=FILE_NAME,
            description="These settings belong to the workspace, so the gtd command-line tool "
                        f"uses them too. Change them by editing {FILE_NAME}; the app reloads it "
                        "when it changes.")
        buttons = Gtk.Box(spacing=6)
        open_b = Gtk.Button(label="Open", action_name="win.open-config",
                            valign=Gtk.Align.CENTER)
        buttons.append(open_b)
        group.set_header_suffix(buttons)
        page.add(group)
        if w.workspace is None:
            group.add(Adw.ActionRow(title="No workspace is open"))
            open_b.set_sensitive(False)
            return page
        c = w.wcfg

        def row(title, value):
            r = Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True)
            r.add_css_class("property")
            group.add(r)
        if w.wcfg_error:
            err = Adw.ActionRow(title="Could not be used; defaults are in use",
                                subtitle=w.wcfg_error)
            err.add_css_class("error")
            group.add(err)
        row("Workspace", w.workspace.root)
        row("Filename Length", f"{c.max_filename_chars} characters")
        row("Age Colours", f"green under {c.green_max_days} days, yellow under "
                           f"{c.yellow_max_days}, red after")
        row("Accounts", "\n".join(f"{a['display_name']} ({a['email_address']}, {a['colour']})"
                                  for a in c.my_own_accounts) or "None")
        row("Monitored Hashtags", ", ".join(c.monitored_hashtags) or "None")
        return page

    def _set(self, key, value):
        self.window.config.set(key, value)
        self.window.preferences_changed()

    def _radar(self, folder, on):
        folders = set(self.window.config.radar_folders)
        (folders.add if on else folders.discard)(folder)
        self.window.config.radar_folders = folders
        self.window.preferences_changed()
