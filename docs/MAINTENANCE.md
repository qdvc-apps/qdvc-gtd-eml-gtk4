# QDVC GTD EML for GNOME — Maintenance Guide

This guide is for whoever maintains the app next. Shared QDVC conventions are
defined in the **QDVC Python desktop applications specification**,
<https://github.com/qdvc-apps/qdvc-python-gtk-app-specification/>, and are not
repeated here. This document covers only what is specific to this app: how it
relates to the command-line tool, its module map, what must not drift, how it
is tested, and every deviation from the specification (§6).

The one rule that matters most, shared with the macOS edition: **the
workspace is the contract.** The app and the `gtd` CLI read and write the
same six folders and the same `metadata.csv`, byte for byte. Any change that
alters a filename the app generates, or a byte of `metadata.csv` it writes, is
a bug unless the CLI changes the same way. The format is summarised in
[FILE_FORMAT.md](FILE_FORMAT.md).

## 1. Relationship to the CLI and the macOS edition

- **The core is the CLI's own code.** `qdvc/gtd_modules/` is an *unmodified*
  copy of the CLI's pure modules (`config`, `dashboard`, `emailutil`, `fs`,
  `ingest`, `metadata`, `metrics`, `naming`, `report`, `site`, `thread`).
  `qdvc/gtd_modules/VENDORED.json` records the CLI commit and each file's
  SHA-256. **Never edit these files**: fix the CLI, then refresh:

  ```
  python3 tools/vendor_core.py --cli-repo ../qdvc-gtd-eml
  scripts/build-app.sh          # runs the parity tests
  ```

  `tools/vendor_core.py --check` (run by the tests and the build script)
  fails if a vendored file was edited by hand. A parity failure after a
  refresh is either a CLI change the app layer must follow, or a bug.
- **The window is a port of the macOS edition**
  ([qdvc-gtd-eml-mac](https://github.com/qdvc-apps/qdvc-gtd-eml-mac)): the
  same actions, refusals, sidebar entries, Dashboard, Performance and
  Calendar, redesigned for GNOME ([DESIGN.md](DESIGN.md)). Its parity
  fixture is reused as-is: `tests/fixtures/parity.json` is a copy of
  `qdvc-gtd-eml-mac/Tests/GTDCoreTests/Fixtures/parity.json`. To refresh it,
  regenerate it in that repository (`tools/make_fixtures.py`) and copy it here.

## 2. Modules

Pure package (no GTK; spec §2):

| Module | Responsibility |
| --- | --- |
| `__init__.py` | `APP_ID = "qdvc.GtdEml"`, `APP_NAME`, `PRGNAME = "qdvc-gtd-eml"` |
| `gtd_modules/` | the CLI's modules, vendored unmodified (§1) |
| `folders.py` | the six folders with title, icon, stamp field, "open" flag |
| `workspace_config.py` | `workspace.yml` via the CLI's loader and normalisers; account colours |
| `workspace.py` | `Workspace`: ingest, alloc, close, pin/unpin, metadata set, metadata_check, autofix, import; refusal checks; atomic `metadata.csv` writes |
| `records.py` | `ParsedEmail` (cached), `EmailRecord`, `Overview`, date headings, `RecordLoader` (in-memory + `~/.cache` index) |
| `views.py` | what each sidebar entry lists, search, sorting, grouping, titles, short refusal wording |
| `calendar_index.py` | events per day for the Calendar |
| `dates.py` | all date display (style, time zone, zone-less quoted times) |
| `config.py` | app preferences in `~/.config/qdvc-gtd-eml/config.yml` (spec §5) |
| `ui_prefs.py` | `SHORTCUTS` (spec §10), sort titles, metric names, wording helpers |
| `platform_utils.py` | fallbacks for launching apps without GTK (spec §11) |

GTK 4 front-end (`qdvc/gtk4/`, every module `gtk4_*`):

| Module | Responsibility |
| --- | --- |
| `gtk4_app.py` | `Adw.Application`, prgname, bundled icons, stylesheet, accelerators, `do_open` |
| `gtk4_window.py` | the window: layout, breakpoints, state, loading thread, file monitors, banners, toasts, every workflow action |
| `gtk4_actions.py` | the `win.*` actions, their sensitivity (`update()`), and the menus |
| `gtk4_sidebar.py` | the `.navigation-sidebar` list, counts, drop targets |
| `gtk4_message_list.py` | `Gtk.ListView` rows, multi-selection, section headers, context menu, drag source |
| `gtk4_reading_pane.py` | tags, in-place annotations, notes, workflow trail, thread cards |
| `gtk4_pages.py` | the shared header/banners/clamp scaffold of the overview pages |
| `gtk4_dashboard.py`, `gtk4_performance.py`, `gtk4_calendar.py` | the overview pages |
| `gtk4_charts.py` | Cairo bar and line charts that follow the theme |
| `gtk4_dialogs.py` | alerts, Close With, Review Date Stamps, Check Metadata |
| `gtk4_preferences.py` | `Adw.PreferencesDialog` (live-apply) |
| `gtk4_shortcuts.py` | the Keyboard Shortcuts window, built from `SHORTCUTS` |
| `gtk4_widgets.py`, `style.css` | small helpers and the few custom styles |

Other files: `qdvc_gtd_eml.py` (entry point), `data/` (launcher template, app
icon, symbolic icons), `scripts/build-app.sh`, `tools/` (`vendor_core.py`,
`make_icon.py`, `screenshots.py`), `tests/`, `sample-workspace/` (made by the
CLI; also a quick manual check).

### 2.1 How a change flows

Every workflow action is a `MainWindow` method that calls `Workspace`, shows
a toast or an alert, and calls `reload()`. `reload()` runs
`RecordLoader.load()` on a worker thread and applies the snapshot on the main
loop (`_apply`), which updates the sidebar counts, the banners and the visible
page or list. A generation counter drops stale loads. Parses are cached by
(filename, size, mtime), so a reload after an action re-reads only
`metadata.csv`. `Gio.FileMonitor`s on the root and the six folders trigger a
debounced reload (600 ms); a change to `workspace.yml` also reloads the
workspace settings and the sidebar.

Sensitivity is computed in one place, `gtk4_actions.update()`, called after
every selection change and reload; the reading pane's buttons and the context
menu only reference actions.

### 2.2 Where to touch

- **A new command:** add a method to `MainWindow`, an action in
  `gtk4_actions.install()`, its rule in `update()`, an entry in
  `ui_prefs.SHORTCUTS` if it has a shortcut, and a menu or button that names
  the action. If it writes the workspace, the write belongs in `Workspace`,
  built on the CLI's functions, with a test in `tests/test_workspace.py`.
- **A new sidebar entry:** an item tuple in `views.py` (`members`, `title`),
  a row in `Sidebar.rebuild()`.
- **A new preference:** a default in `config.DEFAULTS`, a row in
  `gtk4_preferences.py` that calls `window.preferences_changed()`.
- **A new icon:** add it to `tools/make_icon.py` and run it.

## 3. Behaviour that must be preserved

Everything in the macOS edition's MAINTENANCE §3 that concerns the
workspace still applies, and here it holds by construction because the CLI's
code does the work. What the app layer adds, and the tests pin:

- **`metadata.csv` bytes:** `Workspace._write_rows` uses exactly the CLI's
  `csv.DictWriter` calls, through a temporary file and `os.replace`, keeping
  the file's mode. Several fields written at once produce the same bytes as
  the CLI's sequence of single-field writes.
- **Refusals** use the CLI's wording (`GTDError.message` / `.detail`):
  `alloc` never overwrites a set stamp; `close` needs the other email and
  refuses an archived one; `ds_*` are never user-editable; autofix refuses
  while 01-input has files and never runs with blockers.
- **Ingest:** the CLI's naming, with existing names across all six folders
  taken; if one file cannot be read, the files already moved are still
  recorded (the CLI would leave them untracked until the next `list`).
  Ingest is refused while `workspace.yml` is unusable, since the filename
  length would be unknown.
- **Records** agree with the web UI's (`site.build_email_record`), checked
  on the sample workspace by `RecordsMatchSite`.
- **Actions on 01-input** other than ingest are disabled (moving would skip
  the rename; annotations would be lost when ingest renames the file).

## 4. Tests

```
python3 -m unittest discover -s tests -t .     # or scripts/build-app.sh
```

- `test_parity.py` — the macOS fixture: 21 emails, autofix plans, metrics,
  backlog/flow shapes, and the scripted CLI session replayed with this app's
  `Workspace` and compared file-by-file and byte-for-byte; plus records
  against `site.build_email_record`.
- `test_workspace.py` — refusals, atomic writes, import, ingest/autofix,
  metadata_check, the cache, views, date buckets and quoted dates, and the
  vendored-files check.
- `test_ui_smoke.py` — imports every `qdvc.gtk4` module, then runs
  `tools/screenshots.py --smoke` (under `xvfb-run` when there is no display):
  it drives the real window through every page and dialog, ingests, applies
  autofix, pins, edits, moves, searches, and checks the resulting workspace.
  It runs as a separate instance (`NON_UNIQUE`) with a temporary XDG home, so
  it never touches your open app or your settings. Skipped without GTK.

`xvfb-run -a python3 tools/screenshots.py --out docs/screenshots` refreshes
the screenshots (add `--dark` for dark mode).

## 5. Pitfalls found while building

- `Adw.ToolbarView` is a final type: pages wrap it in an `Adw.Bin`.
- Windows with breakpoints need a minimum size (`set_size_request(360, 294)`),
  and nothing in a header bar may need more than that: view options (the
  Performance account and period, the Calendar's month navigation) live in
  the page body for this reason.
- Only *themed* symbolic icons are recoloured. `data/icons` is added as a
  theme root laid out like hicolor; adding the leaf folders instead loads the
  icons unthemed, and they stay black in dark mode.
- Cairo drawing from Python needs `python3-gi-cairo` (`import cairo` in
  `gtk4_charts.py` registers the converter).
- The launcher must be named after the application id (§6.4).

## 6. Deviations from the specification

1. **GTK 4 only** (spec §1, §3, §8). There is no GTK 3 front-end, no
   `--gtk3` / `--gtk4` dispatcher and no `ui_backend` preference, as the owner
   requested a GNOME / libadwaita app. `docs/MAINTENANCE_GTK3_GTK4.md` is
   therefore omitted. The entry point reports missing GTK 4 / libadwaita with
   the install command instead of falling back.
2. **Split header bars** (spec §9: "a single `Adw.HeaderBar`"): each pane of
   the split views has its own header bar, the HIG's pattern for split views.
3. **Sidebar navigation instead of `Adw.ViewStack` + `Adw.ViewSwitcher`**
   (spec §9): the app has twenty-odd views (folders, filters, accounts,
   results, overview pages), which GNOME presents as a sidebar; a view
   switcher suits three to five peers.
4. **Launcher file name** `qdvc.GtdEml.desktop` (spec §7.4 suggests
   `qdvc-<app>.desktop`). On Wayland, GTK 4 reports the application id as the
   window's app id and GNOME Shell matches it to `<app id>.desktop`; another
   name gives a generic icon and a second Dock entry. `StartupWMClass` still
   matches the prgname for X11.
5. **App icon** (spec §7.3): the app ships its own GNOME-style icon (drawn by
   `tools/make_icon.py`, installed by the build script), with the stock
   `mail-send-receive` as the fallback when it is not installed, because the
   HIG expects apps to have their own icon. There is no custom-icon
   preference.
6. **Dialog classes**: `Adw.PreferencesDialog`, `Adw.AlertDialog` and
   `Adw.AboutDialog` instead of the spec's `Adw.PreferencesWindow`,
   `Adw.MessageDialog` and `Adw.AboutWindow`, which libadwaita 1.6 deprecates.
7. **Index cache location** (spec §6): in `~/.cache/qdvc-gtd-eml/`, not in
   the workspace, so a synced workspace is not rewritten by whichever machine
   opened it last (as in the Bibliotheca macOS port).
8. **Due dates** follow spec §8.2 (picker only), but a free-text due date
   written by the CLI is shown and kept until it is replaced or cleared, so
   the app never destroys data it cannot represent.
9. **Workspace settings** (accounts, hashtags, age thresholds, filename
   length) come from the workspace's `workspace.yml`, read-only, not from the
   app's config: the CLI reads the same file, so the two always agree.

## 7. Roadmap

- The rest of the CLI's performance dashboard (KPI table, box plots,
  percentiles, hit rates, Sankey), if wanted.
- Undo for annotation edits (moves stay un-undoable, as in the CLI).
- A Flatpak manifest, if the app is to be distributed beyond a local install.
