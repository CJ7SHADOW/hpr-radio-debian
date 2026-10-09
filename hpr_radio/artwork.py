"""Album art lookup: iTunes Search first, Deezer second (both free, no key).
A result is only accepted when its artist matches, so a wrong cover is less likely
than no cover. Results, including misses, are remembered on disk."""

from __future__ import annotations

import json
import re
import threading
import unicodedata
import urllib.parse
from dataclasses import dataclass

from . import net
from .paths import CACHE_DIR


@dataclass(frozen=True)
class Artwork:
    image_url: str
    album: str | None


_lock = threading.Lock()
_cache: dict[str, dict | None] = {}
_loaded = False
_FILE = CACHE_DIR / "artwork.json"
_MAX = 2000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ")
    s = re.sub(r"^the ", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _key(artist: str, title: str) -> str:
    return f"{_norm(artist)}|{_norm(title)}"


def _clean_title(t: str) -> str:
    """Drops '(Live)', '[Remastered]' and the like from the search."""
    return re.sub(r"[(\[].*?[)\]]", "", t).strip()


def artist_matches(wanted: str, found: str) -> bool:
    a, b = _norm(wanted), _norm(found)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    names = [n.strip() for n in re.split(r" and | with | feat | ft ", a) if len(n.strip()) > 2]
    return any(n in b for n in names)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        _cache.update(json.loads(_FILE.read_text("utf-8")))
    except (OSError, ValueError):
        pass


def _save() -> None:
    try:
        items = list(_cache.items())[-_MAX:]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(items)), "utf-8")
        tmp.replace(_FILE)
    except OSError:
        pass


def cached(artist: str, title: str) -> Artwork | None:
    with _lock:
        _load()
        v = _cache.get(_key(artist, title))
    return Artwork(v["image_url"], v.get("album")) if v else None


def is_known(artist: str, title: str) -> bool:
    with _lock:
        _load()
        return _key(artist, title) in _cache


def _itunes(artist: str, title: str) -> Artwork | None:
    term = urllib.parse.quote_plus(f"{artist} {_clean_title(title)}")
    body = net.get_text(f"https://itunes.apple.com/search?term={term}&media=music&entity=song&limit=10")
    if not body:
        return None
    try:
        results = json.loads(body).get("results") or []
    except ValueError:
        return None
    for r in results:
        if not artist_matches(artist, r.get("artistName") or ""):
            continue
        art = r.get("artworkUrl100")
        if art:
            return Artwork(art.replace("100x100bb", "600x600bb"), r.get("collectionName") or None)
    return None


def _deezer(artist: str, title: str) -> Artwork | None:
    q = urllib.parse.quote_plus(f'artist:"{artist}" track:"{_clean_title(title)}"')
    body = net.get_text(f"https://api.deezer.com/search?q={q}&limit=10")
    if not body:
        return None
    try:
        data = json.loads(body).get("data") or []
    except ValueError:
        return None
    for r in data:
        if not artist_matches(artist, (r.get("artist") or {}).get("name") or ""):
            continue
        album = r.get("album") or {}
        art = album.get("cover_xl") or album.get("cover_big")
        if art:
            return Artwork(art, album.get("title") or None)
    return None


def find(artist: str, title: str) -> Artwork | None:
    """Blocking lookup; call through net.run_async."""
    if not artist.strip() or not title.strip():
        return None
    k = _key(artist, title)
    with _lock:
        _load()
        if k in _cache:
            v = _cache[k]
            return Artwork(v["image_url"], v.get("album")) if v else None
    result = _itunes(artist, title) or _deezer(artist, title)
    with _lock:
        _cache[k] = {"image_url": result.image_url, "album": result.album} if result else None
        _save()
    return result
