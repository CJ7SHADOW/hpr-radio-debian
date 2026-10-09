"""The channel and show lists, from the same channels.json the Android app uses.

Where the list comes from, in order:
  1. The server copy (settings.config_url), downloaded every time the app starts.
  2. The last server copy that loaded successfully, saved in ~/.cache/hpr-radio.
  3. The copy shipped with the app (data/channels.json).

A server file that can't be parsed, or that has no playable channel, is ignored.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from gi.repository import GObject

from . import net
from .paths import CACHE_DIR, SHARE_DIR


@dataclass(frozen=True)
class Station:
    id: str
    channel: str
    name: str
    description: str
    streams: tuple[str, ...]
    recent_tracks_url: str | None = None
    note: str | None = None
    color: str | None = None  # "#RRGGBB"

    @property
    def available(self) -> bool:
        return bool(self.streams)

    @property
    def full_name(self) -> str:
        return f"{self.channel}: {self.name}"


@dataclass(frozen=True)
class ShowSource:
    label: str
    rest_url: str | None
    html_url: str | None
    is_archive: bool

    @property
    def cache_key(self) -> str:
        return self.rest_url or self.html_url or ""


@dataclass(frozen=True)
class Show:
    id: str
    title: str
    badge: str
    host: str
    channel: str
    schedule: str
    description: str
    image_url: str | None
    page_url: str | None
    sources: tuple[ShowSource, ...] = field(default_factory=tuple)

    @property
    def byline(self) -> str:
        return f"{self.title} with {self.host}" if self.host else self.title

    @property
    def recent_source(self) -> ShowSource:
        return next((s for s in self.sources if not s.is_archive), self.sources[0])

    @property
    def archive_source(self) -> ShowSource | None:
        return next((s for s in self.sources if s.is_archive and s != self.recent_source), None)


@dataclass(frozen=True)
class Content:
    stations: tuple[Station, ...]
    shows: tuple[Show, ...]


def _str(o: dict, key: str) -> str | None:
    v = o.get(key)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def parse(text: str) -> Content | None:
    """Parse channels.json. Same rules as the Android app."""
    try:
        root = json.loads(text)
    except ValueError:
        return None
    if not isinstance(root, dict):
        return None

    stations: list[Station] = []
    seen: set[str] = set()
    for o in root.get("channels") or []:
        if not isinstance(o, dict) or not o.get("enabled", True):
            continue
        sid = _str(o, "id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        channel = _str(o, "channel") or sid.upper()
        streams = tuple(
            s.strip() for s in (o.get("streams") or [])
            if isinstance(s, str) and s.strip().startswith("http")
        )
        recent = _str(o, "recentTracksUrl")
        stations.append(Station(
            id=sid,
            channel=channel,
            name=_str(o, "name") or channel,
            description=_str(o, "description") or "",
            streams=streams,
            recent_tracks_url=recent if recent and recent.startswith("http") else None,
            note=_str(o, "note"),
            color=_str(o, "color"),
        ))

    shows: list[Show] = []
    seen = set()
    for o in root.get("shows") or []:
        if not isinstance(o, dict) or not o.get("enabled", True):
            continue
        sid, title = _str(o, "id"), _str(o, "title")
        if not sid or not title or sid in seen:
            continue
        pages = []
        for p in o.get("episodePages") or []:
            if not isinstance(p, dict):
                continue
            rest, html_url = _str(p, "restUrl"), _str(p, "htmlUrl")
            if rest or html_url:
                pages.append(ShowSource(_str(p, "label") or "Episodes", rest, html_url, bool(p.get("archive", False))))
        if not pages:
            continue
        seen.add(sid)
        shows.append(Show(
            id=sid,
            title=title,
            badge=_str(o, "badge") or title[:3].upper(),
            host=_str(o, "host") or "",
            channel=_str(o, "channel") or "",
            schedule=_str(o, "schedule") or "",
            description=_str(o, "description") or "",
            image_url=_str(o, "imageUrl"),
            page_url=_str(o, "pageUrl"),
            sources=tuple(pages),
        ))

    if not any(s.available for s in stations):
        return None  # never accept a list with nothing to play
    return Content(tuple(stations), tuple(shows))


class Catalog(GObject.Object):
    """Holds the current channel/show lists. Emits 'changed' when a new list loads."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    SOURCE_BUILTIN = "built-in"
    SOURCE_SAVED = "saved copy"
    SOURCE_SERVER = "live from server"

    def __init__(self, config_url: str) -> None:
        super().__init__()
        self.config_url = config_url
        self._cache_file = CACHE_DIR / "channels.json"
        self._cache_from = CACHE_DIR / "channels.url"

        builtin = parse((SHARE_DIR / "channels.json").read_text("utf-8"))
        if builtin is None:
            raise RuntimeError("Built-in channels.json is invalid")
        self.content: Content = builtin
        self.source = self.SOURCE_BUILTIN

        # Use the saved server copy only if it came from the address we're using now.
        try:
            if self._cache_from.read_text("utf-8").strip() == config_url:
                saved = parse(self._cache_file.read_text("utf-8"))
                if saved is not None:
                    self.content, self.source = saved, self.SOURCE_SAVED
        except OSError:
            pass

    @property
    def stations(self) -> tuple[Station, ...]:
        return self.content.stations

    @property
    def available(self) -> list[Station]:
        return [s for s in self.content.stations if s.available]

    @property
    def shows(self) -> tuple[Show, ...]:
        return self.content.shows

    def station(self, sid: str | None) -> Station | None:
        return next((s for s in self.content.stations if s.id == sid), None)

    def show(self, sid: str | None) -> Show | None:
        return next((s for s in self.content.shows if s.id == sid), None)

    def refresh(self) -> None:
        """Download the server copy in the background."""
        if not self.config_url:
            return
        url = self.config_url
        if url.startswith("http"):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}t={int(time.time() // 60)}"  # sidestep stale caches
        net.run_async(net.get_text, url, 10.0, 500_000, callback=self._on_downloaded)

    def _on_downloaded(self, text: str | None) -> None:
        if text is None:
            return
        parsed = parse(text)
        if parsed is None:
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(text, "utf-8")
            self._cache_from.write_text(self.config_url, "utf-8")
        except OSError:
            pass
        self.content, self.source = parsed, self.SOURCE_SERVER
        self.emit("changed")
