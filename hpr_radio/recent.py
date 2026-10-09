"""Recently played songs from HPR's stream host (Centova Cast)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import net
from .catalog import Station

LIMIT = 15


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    played_at: int  # unix seconds; 0 if unknown

    @property
    def key(self) -> str:
        return f"{self.artist.lower()}|{self.title.lower()}"


def parse(text: str) -> list[Track]:
    # {"type":"result","rid":"hpr1hi","data":[[{"artist":"..","title":"..","time":1791054005}, ...]]}
    root = json.loads(text)
    if not isinstance(root, dict) or root.get("type") != "result":
        return []
    data = root.get("data") or []
    items = data[0] if data and isinstance(data[0], list) else data
    out: list[Track] = []
    for o in items:
        if not isinstance(o, dict):
            continue
        title = net.html_to_text(o.get("title"))
        if not title:
            continue
        try:
            played = int(o.get("time") or 0)
        except (TypeError, ValueError):
            played = 0
        out.append(Track(net.html_to_text(o.get("artist")) or "", title, played))
    return out[:LIMIT]


def fetch(station: Station) -> list[Track] | None:
    """Blocking; call through net.run_async."""
    url = station.recent_tracks_url
    if not url:
        return None
    body = net.get_text(url)
    if body is None and url.startswith("https://"):
        # Centova's port 2199 often answers on plain http only.
        body = net.get_text("http://" + url[len("https://"):])
    if body is None:
        return None
    try:
        return parse(body)
    except ValueError:
        return None


def merge(old: list[Track] | None, new: list[Track]) -> list[Track]:
    """Keep songs seen in earlier refreshes so the list fills out to the full length."""
    seen: set[str] = set()
    out: list[Track] = []
    for t in sorted(new + (old or []), key=lambda t: t.played_at, reverse=True):
        k = f"{t.played_at}|{t.key}"
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out[: LIMIT + 1]
