"""Where the app keeps its files (XDG base directories)."""

import os
from pathlib import Path

_HOME = Path.home()


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", _HOME / ".config") / "hpr-radio"
CACHE_DIR = _xdg("XDG_CACHE_HOME", _HOME / ".cache") / "hpr-radio"
DATA_DIR = _xdg("XDG_DATA_HOME", _HOME / ".local" / "share") / "hpr-radio"
ART_DIR = CACHE_DIR / "art"

# Files shipped with the app: next to the package when run from the source tree,
# /usr/share/hpr-radio when installed from the .deb.
_SOURCE_DATA = Path(__file__).resolve().parent.parent / "data"
SHARE_DIR = _SOURCE_DATA if _SOURCE_DATA.is_dir() else Path("/usr/share/hpr-radio")


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, CACHE_DIR, DATA_DIR, ART_DIR):
        d.mkdir(parents=True, exist_ok=True)
