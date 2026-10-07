# The gtd-eml workspace format

QDVC GTD EML opens the same folder as the `gtd` command-line tool
([qdvc-gtd-eml](https://github.com/qdvc-apps/qdvc-gtd-eml)), which defines the
format. This page summarises what the app reads and writes.

```
<working directory>/
    01-input/        new .eml files, not yet ingested (untracked)
    02-triage/       ingested; decide what it is
    03-actionable/   you have a next action
    04-delegated/    someone else has it
    05-reference/    kept for reference
    06-archive/      done
    metadata.csv     one row per .eml in any folder
    workspace.yml    optional workspace settings (read-only to the app)
```

## Email files

Any regular file ending in `.eml` (any case) in one of the six folders.
Ingestion moves each 01-input file to 02-triage as
`yyyy-mm-dd-brief-description[-ref-<ref>].eml`: the date is the `Date:`
header's own calendar day, the description a slug of the subject, and `<ref>`
the first "Message ref. XXXXXX" found in the body. The whole name fits in
`max_filename_chars` (60 by default); a clash gets `-2`, `-3`… before the ref.
The app never changes an email file's content.

## metadata.csv

UTF-8, comma-separated, `\r\n` line endings, fields quoted only when needed
(Python's `csv` excel dialect), rows sorted by filename. Columns, in order:

| Column | Written by | Meaning |
| --- | --- | --- |
| `eml_filename` | sync | the file name (the key) |
| `general_notes` | Edit Annotations / `gtd metadata set` | free text, may span lines |
| `project` | Edit Annotations | free text |
| `next_action` | Edit Annotations; Close With writes `Closed with <file>` | free text; `#tags` are matched by the hashtag mailboxes |
| `due_date` | Edit Annotations | `yyyy-mm-dd` (free text is allowed but cannot be compared with today) |
| `message_ref` | ingest | the ref found in the body |
| `flags` | Pin / Unpin, Edit Annotations | whitespace-separated tokens, e.g. `pinned` |
| `ds_triage` … `ds_archive` | ingest, Move To, Archive, Close With, Review Date Stamps | the day the email reached that folder (`yyyy-mm-dd`, local date); never edited by hand |

Every write first reconciles the file with the folders: rows are added for
new files and dropped for files that no longer exist.

## workspace.yml

Optional; every key is optional. See
[WORKSPACE_CONFIG_REQUEST.md](WORKSPACE_CONFIG_REQUEST.md) for the full
description.

```yaml
max_filename_chars: 60
green_max_days: 2
yellow_max_days: 14
my_own_accounts:
  - email_address: me@example.com
    display_name: "Work account"
    colour: yellow        # green, yellow, red, blue, magenta or cyan
monitored_hashtags:
  - "#urgent"
```

The app's own preferences (date format, time zone, on-radar folders, sort order,
window size, recent workspaces) are stored in
`$XDG_CONFIG_HOME/qdvc-gtd-eml/config.yml` (normally
`~/.config/qdvc-gtd-eml/config.yml`), never in the workspace. A disposable
parse cache lives in `~/.cache/qdvc-gtd-eml/`; deleting it is always safe.
