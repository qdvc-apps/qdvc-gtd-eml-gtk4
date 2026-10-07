"""The one place that turns a date into text, following the web UI's rules
(and the macOS edition's DateFormatting):

* a ``Date:`` header is an absolute instant, shown in the chosen time zone;
* a quoted in-text header date is wall-clock numbers, shown as written unless
  the reader asks to treat zone-less times as UTC (a stated offset always wins);
* a ds_* stamp or due date is a calendar day, reformatted, never shifted.
"""

from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:  # pragma: no cover
    ZoneInfo = None

    def available_timezones():
        return set()

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# (key, example) — the web UI's three date formats.
DATE_STYLES = [
    ("long", "Tuesday, 3 August 2026 12:34"),
    ("medium", "3 Aug 2026 12:34"),
    ("iso", "2026-08-03 12:34"),
]
# How to read a quoted header's time that names no zone.
NAIVE_MODES = [("local", "As written"), ("utc", "As UTC, converted")]


def time_zone_names():
    return sorted(z for z in available_timezones() if "/" in z or z == "UTC")


class DateFormatter:
    def __init__(self, style="long", zone_name="", naive="local"):
        self.style = style if style in dict(DATE_STYLES) else "long"
        self.naive = naive if naive in dict(NAIVE_MODES) else "local"
        self.zone = None
        if zone_name and ZoneInfo is not None:
            try:
                self.zone = ZoneInfo(zone_name)
            except (KeyError, ValueError, OSError):
                self.zone = None

    # -------------------------------------------------- instants

    def local(self, dt):
        """The instant in the chosen zone (the system's when none is chosen)."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.zone) if self.zone else dt.astimezone()

    def day(self, dt):
        return self.local(dt).date()

    def today(self):
        return self.day(datetime.now(timezone.utc))

    def render(self, y, mo, d, h=None, mi=None, style=None):
        time = f" {h:02d}:{mi:02d}" if h is not None else ""
        style = style or self.style
        if style == "iso":
            return f"{y:04d}-{mo:02d}-{d:02d}{time}"
        if style == "medium":
            return f"{d} {MONTHS[mo - 1][:3]} {y}{time}"
        wd = WEEKDAYS[date(y, mo, d).weekday()]
        return f"{wd}, {d} {MONTHS[mo - 1]} {y}{time}"

    def full(self, dt):
        t = self.local(dt)
        return self.render(t.year, t.month, t.day, t.hour, t.minute)

    def compact_style(self):
        return "iso" if self.style == "iso" else "medium"

    def list_date(self, dt):
        """The message list's date: the time today, "Yesterday", the weekday
        within the last week, else a compact date."""
        t = self.local(dt)
        d, today = t.date(), self.today()
        if d == today:
            return f"{t.hour:02d}:{t.minute:02d}"
        if d == today - timedelta(days=1):
            return "Yesterday"
        if today - timedelta(days=7) < d < today:
            return WEEKDAYS[d.weekday()][:3]
        if self.style != "iso" and d.year == today.year:
            return f"{d.day} {MONTHS[d.month - 1][:3]}"
        return self.render(d.year, d.month, d.day, style=self.compact_style())

    # -------------------------------------------------- calendar days

    def day_text(self, value, style=None):
        """A metadata day (ds_* stamp or due date); free text passes through."""
        try:
            d = date.fromisoformat((value or "").strip())
        except ValueError:
            return value
        return self.render(d.year, d.month, d.day, style=style)

    def date_text(self, d, style=None):
        return self.render(d.year, d.month, d.day, style=style)

    # -------------------------------------------------- quoted headers

    def _quoted_instant(self, p):
        """The datetime a quoted header names, in the chosen zone, or None when
        it is to be shown as written."""
        if p.get("h") is None:
            return None
        offset = p.get("offset")
        if offset is None:
            if self.naive != "utc":
                return None
            offset = 0
        try:
            dt = datetime(p["y"], p["mo"], p["d"], p["h"], p.get("mi", 0),
                          tzinfo=timezone(timedelta(minutes=offset)))
        except (ValueError, OverflowError):
            return None
        return self.local(dt)

    def quoted(self, parts, raw):
        if not parts:
            return raw
        try:
            date(parts["y"], parts["mo"], parts["d"])
        except ValueError:
            return raw
        t = self._quoted_instant(parts)
        if t is not None:
            return self.render(t.year, t.month, t.day, t.hour, t.minute)
        return self.render(parts["y"], parts["mo"], parts["d"], parts.get("h"),
                           parts.get("mi", 0) if parts.get("h") is not None else None)

    def quoted_day(self, parts):
        """The day a quoted header's date falls on, by the same rules."""
        if not parts:
            return None
        try:
            written = date(parts["y"], parts["mo"], parts["d"])
        except ValueError:
            return None
        t = self._quoted_instant(parts)
        return t.date() if t is not None else written
