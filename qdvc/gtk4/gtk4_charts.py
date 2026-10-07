"""Small Cairo charts (bars and lines) that follow the theme: text and grid
lines use the widget's foreground colour, series use the Adwaita palette."""

import math

import cairo  # noqa: F401 - registers the cairo.Context converter (python3-gi-cairo)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, PangoCairo  # noqa: E402

# Adwaita palette: blue 3, green 4, orange 3, purple 3.
PALETTE = {"blue": (0.208, 0.518, 0.894), "green": (0.180, 0.761, 0.494),
           "orange": (1.0, 0.471, 0.0), "purple": (0.569, 0.255, 0.675)}


def nice_ceiling(value, steps=4):
    """(top, step) of a y axis that fits `value` in about `steps` steps."""
    if value <= 0:
        return steps, 1
    raw = value / steps
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    step = max(1, math.ceil(step)) if value >= steps else 1
    return step * math.ceil(value / step), step


class Chart(Gtk.DrawingArea):
    """kind: "bar" (grouped bars) or "line" (line with a filled area)."""

    def __init__(self, height=200, kind="bar"):
        super().__init__(hexpand=True)
        self.kind = kind
        self.set_content_height(height)
        self.labels = []
        self.series = []          # [(name, values, palette colour)]
        self.show_values = False
        self.set_draw_func(self._draw)

    def set_data(self, labels, series, show_values=False):
        self.labels = list(labels)
        self.series = list(series)
        self.show_values = show_values
        tip = []
        for i, lab in enumerate(self.labels):
            tip.append(f"{lab}: " + ", ".join(f"{n} {v[i]}" for n, v, _ in self.series))
        self.set_tooltip_text("\n".join(tip[-24:]) if tip else None)
        self.queue_draw()

    def _text(self, cr, text, x, y, align=0.0, alpha=0.7, fg=(0, 0, 0)):
        layout = self.create_pango_layout(text)
        w, h = layout.get_pixel_size()
        cr.set_source_rgba(*fg, alpha)
        cr.move_to(x - w * align, y - h / 2)
        PangoCairo.show_layout(cr, layout)
        return w, h

    def _measure(self, text):
        return self.create_pango_layout(text).get_pixel_size()

    def _draw(self, _area, cr, width, height):
        rgba = self.get_color()
        fg = (rgba.red, rgba.green, rgba.blue)
        n = len(self.labels)
        if not n or not self.series:
            return
        vmax = max((max(v) if v else 0) for _, v, _ in self.series)
        top, step = nice_ceiling(vmax)
        ylab_w = max(self._measure(str(int(top)))[0], self._measure("0")[0])
        legend = len(self.series) > 1
        left, right, bottom = ylab_w + 10, 6, 24
        ptop = 24 if legend else 10
        pw, ph = width - left - right, height - ptop - bottom
        if pw <= 10 or ph <= 10:
            return
        cr.set_line_width(1)
        v = 0
        while v <= top + 1e-9:
            y = ptop + ph - ph * v / top
            cr.set_source_rgba(*fg, 0.12 if v else 0.3)
            cr.move_to(left, round(y) + 0.5)
            cr.line_to(left + pw, round(y) + 0.5)
            cr.stroke()
            self._text(cr, str(int(v)), left - 6, y, align=1.0, alpha=0.55, fg=fg)
            v += step
        band = pw / n
        widest = max(self._measure(lab)[0] for lab in self.labels) + 8
        every = max(1, math.ceil(widest / band))
        for i, lab in enumerate(self.labels):
            if i % every == 0 or (i == n - 1 and n - 1 - (i // every) * every >= every):
                self._text(cr, lab, left + band * (i + 0.5), ptop + ph + bottom / 2 + 2,
                           align=0.5, alpha=0.6, fg=fg)
        if self.kind == "bar":
            k = len(self.series)
            group_w = band * (0.72 if k > 1 else 0.6)
            bar_w = group_w / k
            for si, (_name, values, colour) in enumerate(self.series):
                cr.set_source_rgb(*PALETTE[colour])
                for i, val in enumerate(values):
                    if not val:
                        continue
                    x = left + band * i + (band - group_w) / 2 + bar_w * si
                    hgt = ph * val / top
                    _rounded_top(cr, x + 0.5, ptop + ph - hgt, max(1.0, bar_w - 1), hgt,
                                 min(4.0, bar_w / 3))
                    cr.fill()
            if self.show_values and k == 1:
                for i, val in enumerate(self.series[0][1]):
                    if val:
                        self._text(cr, str(val), left + band * (i + 0.5),
                                   ptop + ph - ph * val / top - 9, align=0.5, alpha=0.7, fg=fg)
        else:
            for _name, values, colour in self.series:
                pts = [(left + band * (i + 0.5), ptop + ph - ph * val / top)
                       for i, val in enumerate(values)]
                rgb = PALETTE[colour]
                cr.set_source_rgba(*rgb, 0.18)
                cr.move_to(pts[0][0], ptop + ph)
                for x, y in pts:
                    cr.line_to(x, y)
                cr.line_to(pts[-1][0], ptop + ph)
                cr.close_path()
                cr.fill()
                cr.set_source_rgb(*rgb)
                cr.set_line_width(2)
                for i, (x, y) in enumerate(pts):
                    (cr.move_to if i == 0 else cr.line_to)(x, y)
                cr.stroke()
                if n <= 60:
                    for x, y in pts:
                        cr.arc(x, y, 3, 0, 2 * math.pi)
                        cr.fill()
        if legend:
            x = left + pw
            for name, _values, colour in reversed(self.series):
                w, _ = self._measure(name)
                x -= w
                self._text(cr, name, x, 10, alpha=0.75, fg=fg)
                x -= 16
                cr.set_source_rgb(*PALETTE[colour])
                _rounded_top(cr, x, 4, 10, 12, 3)
                cr.fill()
                x -= 14


def _rounded_top(cr, x, y, w, h, r):
    r = max(0.0, min(r, h, w / 2))
    cr.move_to(x, y + h)
    cr.line_to(x, y + r)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.line_to(x + w - r, y)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.line_to(x + w, y + h)
    cr.close_path()
