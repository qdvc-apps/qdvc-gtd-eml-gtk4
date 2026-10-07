"""Which emails had which events on each day, for the Calendar page.

Events are each email's own ``Date:`` header (placed on a day in the chosen
time zone), the dates of the earlier messages quoted in its body, and the five
ds_* stamps in metadata.csv (calendar days, used as written). ``due_date`` is
a deadline, not an event. An email without a usable Date header contributes
no "received" event (the CLI's fallback of "now" is not a real date).
"""

import calendar as _cal
from datetime import date

from .gtd_modules.metrics import parse_ds

# (key, label, ds_* field or None), in display order.
EVENT_KINDS = [
    ("received", "Received", None),
    ("quoted", "Quoted message", None),
    ("triaged", "Triaged", "ds_triage"),
    ("actionable", "Made actionable", "ds_actionable"),
    ("delegated", "Delegated", "ds_delegated"),
    ("reference", "Filed as reference", "ds_reference"),
    ("archived", "Archived", "ds_archive"),
]
LABELS = {k: label for k, label, _ in EVENT_KINDS}
_ORDER = {k: i for i, (k, _, _) in enumerate(EVENT_KINDS)}


class CalendarIndex:
    def __init__(self, days=None):
        # date -> {record id: [kind, ...]} (kinds in display order)
        self.days = days or {}

    @classmethod
    def build(cls, records, instant_day, quoted_day):
        sets = {}

        def add(day, rid, kind):
            sets.setdefault(day, {}).setdefault(rid, set()).add(kind)

        for r in records:
            if r.parsed.error is None and r.parsed.date is not None:
                add(instant_day(r.parsed.date), r.id, "received")
            for m in r.parsed.thread:
                if m.get("depth", 0) > 0 and m.get("date_parts"):
                    d = quoted_day(m["date_parts"])
                    if d is not None:
                        add(d, r.id, "quoted")
            for kind, _, field in EVENT_KINDS:
                if field:
                    d = parse_ds(r.row.get(field))
                    if d is not None:
                        add(d, r.id, kind)
        return cls({day: {rid: sorted(k, key=_ORDER.get) for rid, k in by.items()}
                    for day, by in sets.items()})

    def count(self, day):
        return len(self.days.get(day, {}))

    def kinds(self, day, rid):
        return self.days.get(day, {}).get(rid, [])


def month_days(first):
    n = _cal.monthrange(first.year, first.month)[1]
    return [date(first.year, first.month, d) for d in range(1, n + 1)]


def shift_month(first, months):
    index = first.year * 12 + (first.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)
