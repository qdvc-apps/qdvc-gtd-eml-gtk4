"""Small widget helpers shared by the views."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango  # noqa: E402

_dynamic_css = {}


def label(text, *classes, xalign=0.0, wrap=False, ellipsize=True, selectable=False,
          hexpand=False, lines=None):
    w = Gtk.Label(label=text or "", xalign=xalign, wrap=wrap, selectable=selectable,
                  hexpand=hexpand)
    if wrap:
        w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if lines:
            w.set_lines(lines)
            w.set_ellipsize(Pango.EllipsizeMode.END)
    elif ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
    for c in classes:
        w.add_css_class(c)
    return w


def tag(text, kind=None, icon=None, tooltip=None):
    """A small rounded label (age, folder, account, due date…)."""
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    box.add_css_class("tag")
    if kind:
        box.add_css_class(kind)
    if icon:
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(12)
        box.append(img)
    box.append(Gtk.Label(label=text))
    if tooltip:
        box.set_tooltip_text(tooltip)
    return box


def colour_class(colour):
    """A CSS class that colours text with `colour` (e.g. an account colour)."""
    name = "c-" + "".join(ch for ch in colour if ch.isalnum())
    if name not in _dynamic_css:
        _dynamic_css[name] = f".{name} {{ color: {colour}; }} .{name}.account-dot " \
                             f"{{ background: {colour}; }}"
        provider = Gtk.CssProvider()
        provider.load_from_string(_dynamic_css[name]) if hasattr(provider, "load_from_string") \
            else provider.load_from_data(_dynamic_css[name].encode())
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, 801)
    return name


def clear(box):
    """Remove every child of a Gtk.Box (or similar)."""
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def scrolled(child, hscroll=False):
    sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
    sw.set_policy(Gtk.PolicyType.AUTOMATIC if hscroll else Gtk.PolicyType.NEVER,
                  Gtk.PolicyType.AUTOMATIC)
    sw.set_child(child)
    return sw


def vbox(spacing=0, **kw):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)


def flat_button(icon, tooltip, action=None, callback=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    if action:
        b.set_action_name(action)
    if callback:
        b.connect("clicked", lambda *_: callback())
    return b


def header_grid(rows):
    """Two-column key/value grid (From, To, Date, …), skipping empty values."""
    g = Gtk.Grid(column_spacing=12, row_spacing=3)
    i = 0
    for key, value in rows:
        if not value:
            continue
        g.attach(label(key, "dim-label", xalign=1.0), 0, i, 1, 1)
        v = label(value, wrap=True, selectable=True, hexpand=True)
        g.attach(v, 1, i, 1, 1)
        i += 1
    return g


def sidebar_toggle(split):
    """A header-bar button that shows the outer sidebar when it is hidden or
    collapsed (shown only then), as GNOME apps do."""
    btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Show Sidebar (F9)")
    split.bind_property("show-sidebar", btn, "active", 3)  # SYNC_CREATE | BIDIRECTIONAL

    def update(*_):
        btn.set_visible(split.get_collapsed() or not split.get_show_sidebar())
    split.connect("notify::collapsed", update)
    split.connect("notify::show-sidebar", update)
    update()
    return btn
