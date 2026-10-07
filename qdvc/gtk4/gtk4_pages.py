"""Shared scaffolding for the full-width overview pages (Dashboard,
Performance, Calendar): a header bar, the window's banners, and a clamped,
scrolling column."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .gtk4_widgets import sidebar_toggle, vbox  # noqa: E402


class OverviewPage(Adw.Bin):
    def __init__(self, window, title, maximum=900):
        super().__init__()
        self.toolbar = Adw.ToolbarView()
        self.set_child(self.toolbar)
        self.window = window
        self.header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=title)
        self.header.set_title_widget(self.title)
        self.header.pack_start(sidebar_toggle(window.split))
        self.toolbar.add_top_bar(self.header)
        for banner in window.make_banners():
            self.toolbar.add_top_bar(banner)
        self.stack = Gtk.Stack()
        self.body = vbox(24, margin_top=24, margin_bottom=30, margin_start=18, margin_end=18)
        clamp = Adw.Clamp(maximum_size=maximum, child=self.body)
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(clamp)
        self.stack.add_named(sw, "body")
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        self.toolbar.set_content(self.stack)

    def clear(self):
        child = self.body.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt
        self.stack.set_visible_child_name("body")

    def show_status(self, icon, title, description, button_label=None, action=None):
        self.status.set_icon_name(icon)
        self.status.set_title(title)
        self.status.set_description(description)
        if button_label:
            b = Gtk.Button(label=button_label, action_name=action, halign=Gtk.Align.CENTER)
            b.add_css_class("pill")
            b.add_css_class("suggested-action")
            self.status.set_child(b)
        else:
            self.status.set_child(None)
        self.stack.set_visible_child_name("status")
