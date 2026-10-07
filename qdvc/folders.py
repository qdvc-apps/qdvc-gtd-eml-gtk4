"""The six workflow folders, in workflow order, with their display data."""

from dataclasses import dataclass

from .gtd_modules import config as cli_config


@dataclass(frozen=True)
class Folder:
    dir: str            # "02-triage": the name on disk (and the CLI's canonical name)
    alias: str          # "triage": the `gtd alloc` alias, also a stable key
    title: str          # "Triage"
    icon: str           # symbolic icon name
    number: int         # 1 … 6

    @property
    def stamp_field(self):
        """The ds_* column stamped when an email arrives here (None for input)."""
        return cli_config.DATE_STAMP_FIELD_BY_DIR.get(self.dir)

    @property
    def is_open(self):
        """Emails here are open work (the overview's "open" folders)."""
        return self.alias in ("triage", "actionable", "delegated")


FOLDERS = [
    Folder(cli_config.INPUT_DIR, "input", "Input", "gtd-input-symbolic", 1),
    Folder(cli_config.TRIAGE_DIR, "triage", "Triage", "gtd-triage-symbolic", 2),
    Folder(cli_config.ACTIONABLE_DIR, "actionable", "Actionable", "gtd-actionable-symbolic", 3),
    Folder(cli_config.DELEGATED_DIR, "delegated", "Delegated", "gtd-delegated-symbolic", 4),
    Folder(cli_config.REFERENCE_DIR, "reference", "Reference", "gtd-reference-symbolic", 5),
    Folder(cli_config.ARCHIVE_DIR, "archive", "Archive", "gtd-archive-symbolic", 6),
]

BY_DIR = {f.dir: f for f in FOLDERS}
BY_ALIAS = {f.alias: f for f in FOLDERS}
INPUT, TRIAGE, ACTIONABLE, DELEGATED, REFERENCE, ARCHIVE = FOLDERS
# Where Move To can send an email (never back to Input).
DESTINATIONS = FOLDERS[1:]


def resolve(name):
    """`fs.resolve_folder`, returning the Folder: an alias or full folder name."""
    key = (name or "").strip().lower()
    return BY_ALIAS.get(key) or BY_DIR.get(key)
