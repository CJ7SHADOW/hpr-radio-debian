"""User settings, stored as JSON in ~/.config/hpr-radio/settings.json.

    {
      "config_url": "https://hpr.org/apps/android/channels.json",
      "volume": 1.0
    }

config_url can also be overridden for one run with the HPR_CONFIG_URL environment
variable or the --config-url command-line option.
"""

from __future__ import annotations

import json
import os

from .paths import CONFIG_DIR

DEFAULT_CONFIG_URL = "https://hpr.org/apps/android/channels.json"
_FILE = CONFIG_DIR / "settings.json"


class Settings:
    def __init__(self) -> None:
        self._data: dict = {}
        try:
            self._data = json.loads(_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            self._data = {}
        self.override_config_url: str | None = os.environ.get("HPR_CONFIG_URL") or None

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
            tmp.replace(_FILE)
        except OSError:
            pass

    @property
    def config_url(self) -> str:
        return (self.override_config_url or self._data.get("config_url") or DEFAULT_CONFIG_URL).strip()
