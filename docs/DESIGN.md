# QDVC GTD EML for GNOME — Design

Status: **approved; first implementation written.** The decisions taken on the
open questions are recorded in §10. Where the implementation differs from the
proposal, this document has been updated to match; screenshots of the real app
are in `docs/screenshots/`.

This document proposes how the GNOME edition of
[qdvc-gtd-eml](https://github.com/qdvc-apps/qdvc-gtd-eml) should look and
behave. It is a port of
[qdvc-gtd-eml-mac](https://github.com/qdvc-apps/qdvc-gtd-eml-mac): the same
workspace, the same actions and the same refusals, with a window designed for
GNOME rather than translated from macOS. It follows the
[QDVC Python GTK app specification](https://github.com/qdvc-apps/qdvc-python-gtk-app-specification)
except where §9 says otherwise, and the
[GNOME HIG](https://developer.gnome.org/hig/).

---

## 1. Principles

These are inherited unchanged from the macOS edition.

- **The workspace is the contract.** The app reads and writes the same six
  folders and the same `metadata.csv` as the CLI, byte for byte, so the CLI,
  the macOS app and this app can take turns on one workspace.
- **The CLI's rules are the app's rules.** `alloc` never overwrites a set
  `ds_*` stamp; `close` needs the other email and refuses an archived one;
  `ds_*` stamps are never hand-editable; `workflow_autofix` applies a whole
  reviewed batch or nothing. A refused action is insensitive, with the reason
  in its tooltip, or explained in a dialog using the CLI's wording.
- **Writes happen immediately** and atomically. There is no document to save.
- **Look like GNOME, where GNOME has an answer.** Where the macOS edition
  borrowed from Apple Mail, this edition borrows from Geary, Files (Nautilus),
  Settings and Calendar, and uses stock libadwaita patterns: split views,
  boxed lists, banners, toasts, status pages and alert dialogs.

## 2. Language and toolkit

**Python 3 + PyGObject + GTK 4 + libadwaita.** The alternatives were weighed:

| Option | For | Against |
| --- | --- | --- |
| **Python + PyGObject** | The CLI *is* Python: its `email`-based parser, naming, metadata, metrics and autofix code can be reused unmodified, so parity is exact rather than reproduced. No build step; reinstalling is a copy. It is the family's GTK stack (the spec, Bibliotheca's GTK 4 front-end). Many GNOME Circle apps are written this way. | Slower than native code on very large workspaces; mitigated by a background loader and a parse cache (§8). |
| Rust + gtk4-rs | Fast; excellent GTK 4 bindings. | Python's lenient `email` parser would have to be re-implemented and parity-tested, as the Swift port had to (about 3,500 lines of core). Slow rebuilds. |
| Vala | GNOME's own language. | Same re-implementation problem; smaller ecosystem. |
| Swift on Linux | `GTDCore` already builds on Linux. | GTK bindings for Swift are immature; a Swift toolchain is rarely installed on GNOME desktops. |

The macOS edition had to port Python's `email` package to Swift and pin it with
fixtures. Here the fixtures still run (§7), but against the CLI's own code.

## 3. Window layout

One window (`Adw.ApplicationWindow`) with libadwaita's three-pane pattern:

- an outer `Adw.NavigationSplitView`: **sidebar** | **content**;
- for mailboxes, the content is an inner `Adw.NavigationSplitView`:
  **message list** | **reading pane**, as in Geary;
- for Dashboard, Performance and Calendar, the content is a full-width page,
  as in the macOS edition, so the sidebar never jumps.

Each pane has its own `Adw.HeaderBar` inside an `Adw.ToolbarView` (GNOME's
split header bar), so actions sit above the thing they act on:

| Pane | Header bar |
| --- | --- |
| Sidebar | Ingest button (start), app name and workspace as title, primary menu (end) |
| Message list | folder name and count as title (`Adw.WindowTitle`); search toggle and sort menu (end) |
| Reading pane | Move To (menu button with label), Archive, Close With…, Pin (toggle), More (open in the default mail app, show in Files, copy filename) |

The window is **adaptive** (`Adw.Breakpoint`): below about 900 px the sidebar
collapses behind a back button, and below about 600 px the list and reading
pane become a navigation stack, like Geary on a phone. See
`docs/screenshots/narrow.png`.

### 3.1 Sidebar

A `Gtk.ListBox` with the `.navigation-sidebar` style and section headings, as
in Files and Settings. Counts are dim, right-aligned numerals.

| Section | Entries |
| --- | --- |
| Overview | Dashboard, Performance, Calendar |
| Workflow | Input, Triage, Actionable, Delegated, Reference, Archive (with counts) |
| Filters | Due Date Set, No Due Date, Pinned, one per monitored hashtag |
| Accounts | Inbox and Sent (expandable to one row per account; only when accounts are configured) |
| Results | a temporary row for a Dashboard figure, project or Calendar day |

"Smart Mailboxes" is Apple Mail's word; GNOME has no common equivalent, so
the section is called **Filters**. Dropping messages on a Workflow folder
moves them (`alloc`), as dragging to a folder does in Geary and Files.

### 3.2 Message list

A `Gtk.ListView` over a `Gio.ListStore` with `Gtk.MultiSelection` (Ctrl- and
Shift-click as in Files), and section headers (GTK 4.12+) for the date
groupings: Today, Yesterday, This Week, Last Week, Earlier This Month, Last
Month, then month names and years, as in the web UI. Rows follow Geary:

- **Line 1:** correspondent (bold) and a compact date (time today,
  "Yesterday", weekday, then day and month).
- **Line 2:** subject, with a pin glyph if pinned and a paperclip if it has
  attachments.
- **Line 3:** the next action, dimmed, as the preview line; or "No next
  action", fainter, for an open email without one.
- **Line 4 (when present):** the own-account label in its configured colour,
  a due-date tag that turns red once overdue, the folder when searching, and a
  warning glyph for a weird date progression.
- An **age dot** (green, yellow, red, from `workspace.yml`'s thresholds) in
  the leading gutter, where Geary puts its unread dot.

The sort menu (header bar) offers Date, Due Date, Subject, Correspondent and
Folder; Ascending; and Group by Date. Unlike Mail, the date groups are not
collapsible: GNOME lists rarely are, and the search and filters cover the need.

Right-click (or long-press) opens a context menu repeating the reading pane's
actions; a right-click on an unselected row selects it first, as in Files.
Activating a row (double-click or Enter) opens the `.eml` in the default mail
app. An empty list shows an `Adw.StatusPage` ("No Emails", "No Results").

### 3.3 Reading pane

A scrolling, `Adw.Clamp`-ed column (see `docs/screenshots/main.png`):

1. **Tags:** age (coloured to match the dot), folder, account, status
   (ongoing / resolved / weird), pinned, due date.
2. **Subject** as the title.
3. **Annotations** as a boxed list. Each single-line field is an
   `Adw.EntryRow` with an apply button: Enter or the ✓ writes that one field,
   exactly one `gtd metadata set`, and Escape reverts. This is GNOME's
   in-place editing idiom (as in Settings), rather than the macOS edition's
   Edit/Save form.
   - **Next Action**, **Project** (with a menu of existing projects),
     **Flags**: entry rows.
   - **Due Date**: a row showing the date, with a calendar button opening a
     `Gtk.Calendar` popover and a clear button (see §10, question 4).
   - **Notes**: an expander row with a multi-line text view and its own
     Apply / Revert buttons, shown when the text has changed.
   - **Message ref**: read-only, when set.
4. **Workflow:** the `ds_*` trail (Triaged → Actionable …, with dates) and an
   expander row with the email's metrics (ttS, Td, Wd, tttR).
5. **Message:** the email as a card (From, To, Cc, Date, File, attachments,
   body), then each quoted message as a collapsible card, nested quotes
   indented; the newest two open and older ones folded, as in Geary's
   conversation view.

Bodies are plain text (HTML parts converted to text), for the same reason as
the web UI: no remote content and no sender markup. With nothing selected an
`Adw.StatusPage` says "No Email Selected"; with several selected, it offers
Move To, Archive and Pin / Unpin for all of them.

### 3.4 Overview pages

- **Dashboard** (`docs/screenshots/dashboard.png`): a grid of folder-count cards
  (each opens its folder), a **Needs Attention** boxed list (each non-zero
  figure opens a Results list), **Projects**, and a status footnote.
- **Performance:** the open backlog (headline cards and the current pile by
  age) and throughput (arrivals and resolutions per week or month, and the
  open backlog over time), with an account selector at the top of the page
  and a Weekly / Monthly toggle beside the Throughput heading (in the page,
  not the header bar, so the header fits phone-width windows). Charts are drawn with Cairo in a
  `Gtk.DrawingArea`, coloured from the Adwaita palette and the accent colour,
  so they follow light/dark mode. As in the CLI and the macOS edition, the
  figures appear only once 01-input is empty and the stamps are consistent;
  otherwise an `Adw.StatusPage` explains what to do first and offers the
  button that does it.
- **Calendar** (`docs/screenshots/calendar.png`): a month heatmap like GNOME
  Calendar's month view, with Today and ‹ › beside the month title; each day
  shaded by how many emails had an event that day (received, quoted, or a
  `ds_*` stamp), relative to the busiest day shown. Clicking a day lists its
  emails as a Results entry, each labelled with what happened that day; below
  the grid, the month's counts per kind of event.

## 4. Actions

Every command is a `win.*` `Gio.SimpleAction`, so one action drives its menu
item, header button, context-menu item and shortcut, and greys them all out
together.

| CLI | Where | Shortcut | GNOME precedent |
| --- | --- | --- | --- |
| `list` (ingest) | sidebar button, banner, primary menu | Ctrl+Shift+N | Evolution's Send / Receive |
| — add files to Input | primary menu; drop `.eml` files from Files | Ctrl+I | the family's Import |
| `alloc <dest>` | Move To menu, context menu, drag to sidebar | Ctrl+2 … Ctrl+6; Ctrl+M opens Move To | Geary's Move (M) |
| `alloc archive` | Archive button | Ctrl+6 | Geary's Archive |
| `close … with …` | Close With… dialog: a searchable list of emails | Ctrl+K | — |
| `pin` / `unpin` | Pin toggle | Ctrl+D | Files' and Web's Ctrl+D bookmark |
| `metadata set` | the reading pane's entry rows | Ctrl+E focuses Next Action | — |
| `workflow_autofix` | Review Date Stamps… dialog (every proposed stamp, one Apply All) | — | — |
| `metadata_check` | Check Metadata… dialog (report, then Reconcile) | — | — |
| `view` | open in the default mail app | Enter / double-click | Files' open |
| `search` | search bar across all folders, including quoted text | Ctrl+F (or start typing) | Files, Geary |
| `stats` | sidebar counts, Dashboard | — | — |

Navigation and window: Alt+0 Dashboard, Alt+1 … Alt+6 the six folders, Alt+7
Performance, Alt+8 Calendar (the family's Alt+number convention); F9 toggles
the sidebar (Files, Text Editor); Ctrl+R or F5 refresh; Ctrl+O open
workspace; Ctrl+W close workspace; Ctrl+, Preferences; Ctrl+? Keyboard
Shortcuts; Ctrl+Q quit. Ctrl+Shift+I and Ctrl+Shift+D are avoided because GTK
reserves them for the Inspector.

As in the macOS edition, there is no undo for moves (it would mean clearing a
`ds_*` stamp, which the CLI never does), and actions on 01-input files other
than ingest are disabled (moving would skip the rename, and annotations would
be lost when ingest renames the file).

Feedback uses GNOME's vocabulary:

- **Banners** (`Adw.Banner`, above the message list): "N emails are waiting
  in Input [Ingest]", "N date stamps are missing or inconsistent
  [Review…]", "workspace.yml could not be read [Open]".
- **Toasts** (`Adw.ToastOverlay`) for completed actions: "Moved 3 emails to
  Actionable", "Ingested 2 emails [Show]".
- **Alert dialogs** (`Adw.AlertDialog`) for refusals, with the CLI's wording.

## 5. Primary menu, Preferences, About

The primary menu (sidebar header bar), per the HIG, ends with Preferences,
Keyboard Shortcuts and About:

- Open Workspace…, Open Recent ▸, Close Workspace
- Add Emails to Input…, Ingest Input
- Review Date Stamps…, Check Metadata…
- Open workspace.yml, Show Workspace in Files
- Preferences, Keyboard Shortcuts, About QDVC GTD EML

**Preferences** (`Adw.PreferencesDialog`, live-apply) has two pages:

- **General:** date format (the web UI's three), time zone for `.eml` dates
  (system by default; a searchable combo row), how to read quoted times with
  no zone, the on-radar folders (switch rows; the Filters cover only these,
  while Inbox and Sent cover every folder), dim off-radar mail, and reopen the
  last workspace.
- **Workspace:** a read-only summary of `workspace.yml` (`my_own_accounts`,
  `monitored_hashtags`, the age thresholds, `max_filename_chars`), with Open
  and Reload buttons. These belong to the workspace so that every tool
  agrees.

Light/dark follows the system (`Adw.StyleManager`); the accent colour is the
system's (GNOME 47+). **About** is an `Adw.AboutDialog` with the app icon,
name and one-line description, and no version or licence (spec §8.1).

## 6. Desktop integration and the rebuild script

- **App id** `qdvc.GtdEml` (spec §7.1); program name and command
  `qdvc-gtd-eml` (`GLib.set_prgname`, `StartupWMClass`).
- The launcher is installed as **`qdvc.GtdEml.desktop`**, not
  `qdvc-gtd-eml.desktop`: on Wayland, GTK 4 reports the application id as
  the window's app id, and GNOME Shell matches windows to launchers by
  `<app id>.desktop`. A differently named file shows a generic icon and a
  second Dock entry. `StartupWMClass` covers X11.
- `GApplication` single-instance: launching again raises the existing window;
  a workspace folder dropped on the launcher (`%F`) opens in it.
- **`scripts/build-app.sh`**, like the macOS edition's:
  - with no options: checks the dependencies (Python 3.10+, GTK 4.14+,
    libadwaita 1.5+, PyYAML), byte-compiles everything, runs the tests, and
    stages the app in `build/`;
  - `--install`: copies it to `~/.local/share/qdvc-gtd-eml/`, writes
    `~/.local/bin/qdvc-gtd-eml`, installs the launcher, the app icon and its
    symbolic variant under `~/.local/share/`, refreshes the desktop and icon
    caches, and quits a running copy so the next launch uses the new code;
  - `--uninstall`, `--run` (launch from the checkout), `--no-test`.

  The installed copy is independent of the checkout, so editing the source
  never breaks the app in the Dock until you reinstall.
- A few workflow icons are missing from the stock Adwaita icon theme
  (archive, inbox, chart, hashtag, funnel). The app bundles small symbolic
  SVGs for those (`tools/make_icon.py`), in the style of GNOME's Icon
  Library, and adds them to the icon theme at start-up, as GNOME apps
  commonly do.

## 7. Code layout and testing

```
qdvc_gtd_eml.py          entry point (GTK 4 only; see §9)
qdvc/                    pure package: no GTK imports
    __init__.py          APP_ID, APP_NAME, __version__
    gtd_modules/         unmodified copy of the CLI's pure modules (§10, Q1)
    workspace.py         actions and refusals over gtd_modules; atomic writes
    records.py           EmailRecord, Overview, date buckets, cached loader
    calendar_index.py    events per day for the Calendar
    dates.py             display formatting (style, time zone, zone-less times)
    config.py            app preferences: ~/.config/qdvc-gtd-eml/config.yml
    ui_prefs.py          SHORTCUTS table, sort keys, report text
    platform_utils.py    xdg-open, Files (FileManager1 D-Bus), text editor
    gtk4/                gtk4_app, gtk4_window, gtk4_actions, gtk4_sidebar,
                         gtk4_message_list, gtk4_reading_pane, gtk4_dashboard,
                         gtk4_performance, gtk4_charts, gtk4_calendar,
                         gtk4_dialogs, gtk4_preferences, gtk4_shortcuts,
                         gtk4_widgets, style.css
data/                    launcher template, app icons, bundled symbolic icons
scripts/build-app.sh     check, test, stage, install (§6)
tests/                   unittest; no third-party test runner
tools/                   vendor_core.py, make_icon.py, screenshots.py
sample-workspace/        the macOS edition's, made by the Python CLI
docs/                    DESIGN, MAINTENANCE, FILE_FORMAT
```

`workspace.py` does what the macOS edition's `Workspace.swift` does, but by
calling the CLI's own functions: `ingest_input_files`, `naming`,
`load_metadata`, `plan_autofix`, `compute_email_metrics` and so on. The
one thing it writes itself is `metadata.csv`, with the CLI's exact `csv`
calls but through a temporary file and `os.replace`, because the CLI writes
in place and the spec requires atomic writes. The bytes are identical.

Tests (`python3 -m unittest`, run by `build-app.sh`):

- **Parity:** the macOS edition's `parity.json`, replayed against this app:
  parsed emails, filenames, autofix plans, metrics, backlog and flow figures,
  and the scripted CLI session compared byte for byte with `metadata.csv`.
  This mostly checks the glue, since the core is the CLI's.
- **Model:** temporary workspaces on disk; refusals, batching, the cache.
- **UI smoke test:** every `qdvc.gtk4` module imported and the main window
  built and driven under Xvfb with the sample workspace, with a screenshot of
  each page. This is how the screenshots are made, and it means the GTK code is
  run before each hand-over, not just syntax-checked.

## 8. Behaviour beyond the macOS edition

- **Live reload.** `Gio.FileMonitor`s on the six folders, `metadata.csv` and
  `workspace.yml` trigger a debounced reload, so changes made by the CLI,
  Files or a sync tool appear without pressing Refresh. (The macOS edition
  reloads when the app becomes active; that also happens here.)
- **Parse cache.** Parsed emails are cached by (filename, size, mtime), in
  memory and in a disposable index under `~/.cache/qdvc-gtd-eml/` (spec §6;
  not in the workspace, so a synced workspace is not rewritten), so opening a
  large workspace parses only what changed.
- **Loading** happens on a worker thread; the window shows a spinner status
  page on first load and updates in place afterwards.

## 9. Deviations from the QDVC GTK specification

Each will be restated in `docs/MAINTENANCE.md` with its rationale.

1. **GTK 4 only** (spec §1, §3, §8): no GTK 3 front-end and no `--gtk3` /
   `--gtk4` dispatcher, as you asked. `docs/MAINTENANCE_GTK3_GTK4.md` is
   therefore omitted.
2. **Split header bars** (spec §9 says "a single `Adw.HeaderBar`"): one per
   pane, which is the HIG's pattern for split views.
3. **Sidebar navigation instead of `Adw.ViewStack` + `Adw.ViewSwitcher`**
   (spec §9): a view switcher suits three to five peer views; this app has
   twenty-odd sidebar entries, which GNOME presents as a sidebar (Files,
   Settings).
4. **Launcher file name** `qdvc.GtdEml.desktop` rather than
   `qdvc-gtd-eml.desktop` (spec §7.4), for Wayland (§6).
5. **App icon** (spec §7.3): see §10, question 3.
6. **`Adw.PreferencesDialog` / `Adw.AlertDialog` / `Adw.AboutDialog`**
   rather than the spec's `Adw.PreferencesWindow` / `Adw.MessageDialog` /
   `Adw.AboutWindow`, which libadwaita 1.6 deprecates in favour of the
   dialogs added in 1.5.

## 10. Decisions

The owner accepted every recommendation:

1. **Core:** the CLI's pure modules are copied unmodified into
   `qdvc/gtd_modules/`, with `tools/vendor_core.py` to refresh and verify them.
2. **Platform floor:** GNOME 46 (GTK 4.14, libadwaita 1.5).
3. **App icon:** a GNOME-style icon drawn by `tools/make_icon.py`, with the
   stock `mail-send-receive` as the fallback.
4. **Due dates:** picker only, with Clear; a free-text value written by the
   CLI is shown and kept until replaced.
5. **Names:** repository `qdvc-gtd-eml-gnome`, app "QDVC GTD EML", app id
   `qdvc.GtdEml`, command and prgname `qdvc-gtd-eml`, preferences in
   `~/.config/qdvc-gtd-eml/config.yml`.

Changes made while building: the sidebar's account rows are always shown
(indented under Inbox and Sent) rather than in an expander, and only when
there is more than one account; the Performance and Calendar view options
moved from the header bar into the page; the multi-selection buttons are
stacked vertically so they fit narrow windows.

## 11. Not planned

As in the macOS edition: `generate_dashboard`, `export`, the rest of the
performance dashboard (KPI table, box plots, percentiles, hit rates, Sankey),
and any network access. Also: Flatpak packaging (the install script covers
the local install you asked for; a Flatpak manifest can come later).
