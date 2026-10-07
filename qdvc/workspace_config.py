"""The workspace's own settings, read from <workspace>/workspace.yml.

The CLI reads the same file (gtd_modules.config.load_workspace_config), so the
two always agree on accounts, hashtags, age thresholds and — the one that
matters most — max_filename_chars, which decides the filenames ingest writes.
The app never writes this file.
"""

from dataclasses import dataclass, field

from .gtd_modules import config as cli_config

FILE_NAME = cli_config.WORKSPACE_CONFIG_FILE

# Colour names the CLI accepts for my_own_accounts, as CSS colours that read
# well on both light and dark Adwaita backgrounds.
ACCOUNT_COLOURS = {
    "green": "#2ec27e", "yellow": "#c88800", "red": "#e01b24", "blue": "#3584e4",
    "magenta": "#c061cb", "cyan": "#1c9fb0", "reset": "#77767b",
}

EXAMPLE = """\
# workspace.yml — settings shared by the gtd CLI and QDVC GTD EML.
max_filename_chars: 60
green_max_days: 2
yellow_max_days: 14
my_own_accounts:
  - email_address: me@example.com
    display_name: "Work account"
    colour: yellow
monitored_hashtags:
  - "#urgent"
"""


@dataclass
class WorkspaceConfig:
    max_filename_chars: int = cli_config.DEFAULTS["max_filename_chars"]
    green_max_days: int = cli_config.DEFAULTS["green_max_days"]
    yellow_max_days: int = cli_config.DEFAULTS["yellow_max_days"]
    # Normalised as the CLI does: dicts with email_address, display_name, colour.
    my_own_accounts: list = field(default_factory=list)
    monitored_hashtags: list = field(default_factory=list)

    def account_name(self, email):
        for a in self.my_own_accounts:
            if a["email_address"] == email:
                return a["display_name"]
        return None


def load(root):
    """Return (WorkspaceConfig, error message or None).

    On an unreadable file the defaults are returned with the CLI's own error
    message, so the window can say what is wrong while staying usable.
    """
    try:
        raw = cli_config.load_workspace_config(str(root))
    except cli_config.ConfigError as e:
        return WorkspaceConfig(), str(e)
    cfg = WorkspaceConfig(
        my_own_accounts=cli_config.normalise_accounts(raw.get("my_own_accounts")),
        monitored_hashtags=cli_config.normalise_hashtags(raw.get("monitored_hashtags")),
    )
    problems = []
    for key in ("max_filename_chars", "green_max_days", "yellow_max_days"):
        if key in raw:
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{key} must be a whole number, not {value!r}")
            else:
                setattr(cfg, key, value)
    return cfg, ("; ".join(problems) if problems else None)
