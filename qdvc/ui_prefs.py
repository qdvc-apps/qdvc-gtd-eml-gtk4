"""Toolkit-independent UI tables shared by the front-end: the keyboard
shortcuts (one table drives the accelerators and the Keyboard Shortcuts
window), the sort keys and a few wording helpers."""

from . import folders as F

# (section title, [(action, [accelerators], title), ...]). Actions without a
# "." are window actions ("win."). GTK reserves Ctrl+Shift+I and Ctrl+Shift+D
# for the Inspector, so neither is used.
SHORTCUTS = [
    ("Workspace", [
        ("open-workspace", ["<Primary>o"], "Open workspace"),
        ("close-workspace", ["<Primary>w"], "Close workspace"),
        ("add-emails", ["<Primary>i"], "Add emails to Input"),
        ("ingest", ["<Primary><Shift>n"], "Ingest Input"),
        ("refresh", ["<Primary>r", "F5"], "Reload from disk"),
    ]),
    ("Navigation", [
        ("go-dashboard", ["<Alt>0"], "Dashboard"),
    ] + [(f"go-{f.alias}", [f"<Alt>{f.number}"], f.title) for f in F.FOLDERS] + [
        ("go-performance", ["<Alt>7"], "Performance"),
        ("go-calendar", ["<Alt>8"], "Calendar"),
        ("search", ["<Primary>f"], "Search all folders"),
        ("toggle-sidebar", ["F9"], "Show or hide the sidebar"),
    ]),
    ("Message", [
        (f"move-to-{f.alias}", [f"<Primary>{f.number}"], f"Move to {f.title}")
        for f in F.DESTINATIONS
    ] + [
        ("move-menu", ["<Primary>m"], "Open the Move To menu"),
        ("close-with", ["<Primary>k"], "Close with…"),
        ("toggle-pin", ["<Primary>d"], "Pin or unpin"),
        ("edit-annotations", ["<Primary>e"], "Edit annotations"),
        ("copy-filename", ["<Primary><Shift>c"], "Copy filename"),
        ("open-message", ["<Primary>Return"], "Open in the default mail app"),
    ]),
    ("General", [
        ("preferences", ["<Primary>comma"], "Preferences"),
        ("shortcuts", ["<Primary>question"], "Keyboard shortcuts"),
        ("app.quit", ["<Primary>q"], "Quit"),
    ]),
]


def accelerators():
    """{detailed action name: [accels]} for Gtk.Application.set_accels_for_action."""
    out = {}
    for _, entries in SHORTCUTS:
        for action, accels, _ in entries:
            name = action if "." in action else f"win.{action}"
            out[name] = accels
    return out


SORT_TITLES = [("date", "Date"), ("due", "Due Date"), ("subject", "Subject"),
               ("correspondent", "Correspondent"), ("folder", "Folder")]

METRIC_NAMES = [("ttS", "Time to start", "arrival → triage"),
                ("Td", "Time to decide", "triage → actionable"),
                ("Wd", "Work duration", "actionable → resolved"),
                ("tttR", "Total time to resolve", "arrival → resolved")]


def plural(n, one, many=None):
    """plural(1, "email") -> "1 email"; plural(3, "email") -> "3 emails"."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def is_are(n):
    return "is" if n == 1 else "are"
