"""App preferences, stored as YAML at $XDG_CONFIG_HOME/qdvc-gtd-eml/config.yml.

These are the reader's own preferences only. Workspace settings (accounts,
hashtags, age thresholds, filename length) live in the workspace's
workspace.yml, shared with the CLI (see workspace_config.py).
"""

import os

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from . import folders as F

DEFAULTS = {
    "recent_workspaces": [],
    "last_workspace": "",
    "reopen_last": True,
    "date_style": "long",
    "time_zone": "",               # "" = the system's
    "naive_dates": "local",
    "radar_folders": ["input", "triage", "actionable", "delegated"],
    "dim_off_radar": True,
    "date_headings": True,
    "sort_key": "date",
    "sort_ascending": False,
    "window_width": 1320,
    "window_height": 820,
    "window_maximized": False,
}

SORT_KEYS = ["date", "due", "subject", "correspondent", "folder"]
MAX_RECENT = 10


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "qdvc-gtd-eml", "config.yml")


class Config:
    def __init__(self, path=None, data=None):
        self.path = path or config_path()
        self.data = data or {}

    @classmethod
    def load(cls, path=None):
        path = path or config_path()
        data = {}
        if yaml is not None and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, yaml.YAMLError):
                data = {}
        return cls(path, data)

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return DEFAULTS.get(key, default) if default is None else default

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def save(self):
        if yaml is None:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -------------------------------------------------- validated accessors

    @property
    def sort_key(self):
        value = str(self.get("sort_key")).lower()
        return value if value in SORT_KEYS else "date"

    @property
    def radar_folders(self):
        raw = self.get("radar_folders")
        if not isinstance(raw, list):
            raw = DEFAULTS["radar_folders"]
        return {F.BY_ALIAS[a] for a in raw if a in F.BY_ALIAS}

    @radar_folders.setter
    def radar_folders(self, folders):
        self.set("radar_folders", [f.alias for f in F.FOLDERS if f in folders])

    @property
    def recent_workspaces(self):
        raw = self.get("recent_workspaces")
        return [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []

    def add_recent(self, path):
        recent = [p for p in self.recent_workspaces if p != path]
        recent.insert(0, path)
        self.data["recent_workspaces"] = recent[:MAX_RECENT]
        self.data["last_workspace"] = path
        self.save()

    def remove_recent(self, path):
        self.set("recent_workspaces", [p for p in self.recent_workspaces if p != path])

    def flag(self, key):
        return bool(self.get(key))
