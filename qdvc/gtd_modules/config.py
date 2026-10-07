"""
Configuration: loads config.yml (installation settings) and
<working_directory>/workspace.yml (workspace settings), holds shared constants
(colours, folder names, metadata schema), and normalises the my_own_accounts
and monitored_hashtags lists.
"""

import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# config.yml lives next to the top-level scripts (the parent of this package).
CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
)

# workspace.yml lives at the root of the working directory, next to
# metadata.csv. It holds the settings that describe the *workspace* rather than
# the installation, so every tool that opens the workspace (this CLI, the macOS
# app) reads the same values. Optional; a missing file means "all defaults".
WORKSPACE_CONFIG_FILE = "workspace.yml"

# The keys read from workspace.yml. Any other key in that file is ignored, so a
# tool can add a key later without breaking the others. For the transition,
# these keys are still honoured in config.yml (with a deprecation warning) when
# workspace.yml does not define them.
WORKSPACE_KEYS = [
    "my_own_accounts",
    "monitored_hashtags",
    "green_max_days",
    "yellow_max_days",
    "max_filename_chars",
]

# Defaults — overridden by matching keys in config.yml, then (for WORKSPACE_KEYS)
# by workspace.yml, if present.
DEFAULTS = {
    "working_directory": os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gtd-eml"
    ),                         # root holding the 6 folders + metadata.csv
    # --- workspace settings (WORKSPACE_KEYS): read from workspace.yml ---
    "max_filename_chars": 60,  # max length of generated filename (incl. ".eml")
    "green_max_days": 2,       # days < this -> green
    "yellow_max_days": 14,     # days < this (and >= green) -> yellow; else red
    "my_own_accounts": [],     # list of {email_address, display_name, colour}
    "monitored_hashtags": [],  # e.g. ["#urgent", "#family"]: tags you use inside
                               # next_action, each given its own view in the
                               # generated site's sidebar
    # --- installation settings: read from config.yml only ---
    "archive_report_n": 10,    # number of most recent archive files to report
    "max_subject_chars": 72,   # truncate displayed subjects longer than this
    "force_colour": False,     # always emit colour (e.g. when piping to `less -R`)
    "plotly_js_path": "",      # local path to the minified plotly.js bundle,
                               # bundled into the `generate_dashboard` site so it
                               # runs fully offline (no CDN). See README.md.
    "cli_command": "gtd",      # how you invoke this tool: used only in the
                               # copy-pasteable command snippets the generated
                               # site shows (e.g. "python3 gtd.py").
}

# ANSI colour codes used for report lines.
COLOURS = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}

INPUT_DIR = "01-input"
TRIAGE_DIR = "02-triage"
ACTIONABLE_DIR = "03-actionable"
DELEGATED_DIR = "04-delegated"
REFERENCE_DIR = "05-reference"
ARCHIVE_DIR = "06-archive"

ALL_DIRS = [INPUT_DIR, TRIAGE_DIR, ACTIONABLE_DIR, DELEGATED_DIR, REFERENCE_DIR, ARCHIVE_DIR]

# Short names accepted by `gtd.py alloc` (and elsewhere) -> the numbered folder.
# Both the bare alias ("delegated") and the full folder name ("04-delegated")
# resolve, so either works on the command line.
FOLDERS_BY_ALIAS = {
    "input": INPUT_DIR,
    "triage": TRIAGE_DIR,
    "actionable": ACTIONABLE_DIR,
    "delegated": DELEGATED_DIR,
    "reference": REFERENCE_DIR,
    "archive": ARCHIVE_DIR,
}

METADATA_FILE = "metadata.csv"
METADATA_HEADERS = ["eml_filename", "general_notes", "project", "next_action", "due_date",
                    "message_ref", "flags",
                    "ds_triage", "ds_actionable", "ds_delegated", "ds_reference", "ds_archive"]

# Date-stamp columns: when an email enters one of these folders (via `list`,
# `alloc`, or `close`), the corresponding column is stamped with the current
# date (yyyy-mm-dd). Keyed by canonical folder name. 01-input has no stamp (a
# file only leaves input; it never "arrives" there through the workflow).
DATE_STAMP_FIELD_BY_DIR = {
    TRIAGE_DIR: "ds_triage",
    ACTIONABLE_DIR: "ds_actionable",
    DELEGATED_DIR: "ds_delegated",
    REFERENCE_DIR: "ds_reference",
    ARCHIVE_DIR: "ds_archive",
}


class ConfigError(RuntimeError):
    """
    A settings file could not be used: PyYAML is missing, or workspace.yml
    fails to parse or is not a YAML mapping. The message names the file.
    gtd.py prints it as a one-line error and exits 1.
    """


# Deprecation warnings already printed in this process, so a command that loads
# the settings more than once still warns once per key.
_warned_keys = set()


def workspace_config_path(working_directory):
    """
    Path of the workspace.yml for a working directory.

    Example:
        workspace_config_path("/home/james/gtd-eml-data")
        # -> "/home/james/gtd-eml-data/workspace.yml"
    """
    return os.path.join(working_directory, WORKSPACE_CONFIG_FILE)


def _require_yaml(path):
    """Raise ConfigError naming `path` if PyYAML is not installed."""
    if yaml is None:
        raise ConfigError(
            f"{path} found but PyYAML is not installed. "
            "Install it with: pip install pyyaml"
        )


def load_workspace_config(working_directory):
    """
    Read <working_directory>/workspace.yml and return the WORKSPACE_KEYS it
    sets to a non-null value, un-normalised (exactly as written). A missing
    file, or an empty one (nothing but comments), gives {}. Unknown keys are
    ignored. A file that fails to parse, or whose top level is not a mapping,
    raises ConfigError naming the file — never a silent fallback, since a
    silently ignored max_filename_chars would make this CLI and the macOS app
    produce different filenames.

    Example (workspace.yml holds "max_filename_chars: 80" and "colour: x"):
        load_workspace_config("/home/james/gtd-eml-data")
        # -> {"max_filename_chars": 80}
    """
    path = workspace_config_path(working_directory)
    if not os.path.isfile(path):
        return {}
    _require_yaml(path)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        detail = " ".join(str(e).split())
        raise ConfigError(f"{path} could not be parsed as YAML: {detail}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path} could not be read: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path} must be a YAML mapping of settings "
            f"(key: value lines), not a {type(data).__name__}"
        )
    return {key: data[key] for key in WORKSPACE_KEYS
            if key in data and data[key] is not None}


def load_config(config_path=CONFIG_FILE):
    """
    Load settings, in this order:
        1. DEFAULTS, overridden by any non-null key in config.yml;
        2. working_directory as resolved by step 1;
        3. for WORKSPACE_KEYS, any non-null value in
           <working_directory>/workspace.yml overrides step 1;
        4. a WORKSPACE_KEY that config.yml still sets and workspace.yml does not
           keeps its config.yml value, with a one-line deprecation warning on
           stderr (once per key per process);
        5. my_own_accounts and monitored_hashtags are normalised.
    my_own_accounts ends up as a list of dicts with lower-cased email_address
    plus display_name and colour. Raises ConfigError if PyYAML is needed but
    missing, or if workspace.yml is unparseable or not a mapping.

    Example (workspace.yml has my_own_accounts with one entry):
        load_config("/path/config.yml")["my_own_accounts"]
        # -> [{"email_address": "james@x.com",
        #      "display_name": "Work account", "colour": "yellow"}]
    """
    cfg = dict(DEFAULTS)
    from_config_yml = set()
    if os.path.isfile(config_path):
        _require_yaml(config_path)
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for key in cfg:
            if key in data and data[key] is not None:
                cfg[key] = data[key]
                from_config_yml.add(key)

    workspace = load_workspace_config(cfg["working_directory"])
    cfg.update(workspace)

    for key in WORKSPACE_KEYS:
        if key in from_config_yml and key not in workspace and key not in _warned_keys:
            _warned_keys.add(key)
            print(f"warning: '{key}' in config.yml is deprecated; move it to "
                  f"{workspace_config_path(cfg['working_directory'])}",
                  file=sys.stderr)

    cfg["my_own_accounts"] = normalise_accounts(cfg.get("my_own_accounts"))
    cfg["monitored_hashtags"] = normalise_hashtags(cfg.get("monitored_hashtags"))
    return cfg


def normalise_accounts(accounts):
    """
    Normalise the my_own_accounts list: lower-case email addresses, supply a
    fallback display_name, and validate the colour against COLOURS.

    Example:
        normalise_accounts([{"email_address": "ME@X.com"}])
        # -> [{"email_address": "me@x.com", "display_name": "me@x.com",
        #      "colour": "cyan"}]
    """
    result = []
    for entry in accounts or []:
        if not isinstance(entry, dict):
            continue
        email = (entry.get("email_address") or "").strip().lower()
        if not email:
            continue
        colour = (entry.get("colour") or "cyan").strip().lower()
        if colour not in COLOURS:
            colour = "cyan"
        result.append({
            "email_address": email,
            "display_name": (entry.get("display_name") or email).strip(),
            "colour": colour,
        })
    return result


def normalise_hashtags(hashtags):
    """
    Normalise the monitored_hashtags list: keep only non-empty strings, strip
    surrounding whitespace, drop duplicates (case-insensitively) while preserving
    the configured order. The leading "#" is NOT required or added — these are
    plain substrings matched against the next_action column, so whatever you write
    here is what is looked for.

    Example:
        normalise_hashtags(["#urgent", " #family ", "#URGENT", "", 7])
        # -> ["#urgent", "#family"]
    """
    result = []
    seen = set()
    for entry in hashtags or []:
        if not isinstance(entry, str):
            continue
        tag = entry.strip()
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        result.append(tag)
    return result


def should_use_colour(cfg, stream):
    """
    Decide whether to emit ANSI colour, applying this precedence (highest
    first):
        1. NO_COLOR env var set (to anything)        -> never colour
        2. FORCE_COLOR env var set to a truthy value  -> always colour
        3. force_colour: true in config.yml           -> always colour
        4. otherwise                                   -> colour iff stream is a TTY

    NO_COLOR and FORCE_COLOR follow the conventions documented at
    https://no-color.org and https://force-color.org . A FORCE_COLOR value of
    "0", "false", "no", or "off" (case-insensitive) is treated as NOT forcing.

    Example:
        should_use_colour({"force_colour": False}, sys.stdout)  # -> True if a TTY
        should_use_colour({"force_colour": True}, piped_stream)  # -> True
    """
    if "NO_COLOR" in os.environ:
        return False

    force_env = os.environ.get("FORCE_COLOR")
    if force_env is not None and force_env.strip().lower() not in ("0", "false", "no", "off", ""):
        return True

    if cfg.get("force_colour"):
        return True

    return bool(getattr(stream, "isatty", lambda: False)())
