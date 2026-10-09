"""Episode lists read from hpr.org show pages, plus remembered listening progress.

Each episode on the page is a paragraph with the air date and a playlist PDF link,
followed by a WordPress audio player:

  <p><strong>Saturday, October 3, 2026 </strong><a href="/hprplaylists/…/20261003.pdf">playlist</a></p>
  <audio class="wp-audio-shortcode" …><source type="audio/mpeg" src="https://archive.org/download/…mp3?_=1" />
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass

from . import net
from .catalog import Show, ShowSource
from .paths import DATA_DIR


@dataclass(frozen=True)
class Episode:
    key: str          # archive.org item name, e.g. "good-n-country-10-3-2026"
    title: str        # "Saturday, October 3, 2026"
    note: str | None  # e.g. "Dolly Parton tribute"
    date_key: int     # yyyymmdd; 0 if unknown
    audio_url: str
    playlist_url: str | None
    from_archive: bool

    @property
    def year(self) -> int:
        return self.date_key // 10_000


_AUDIO = re.compile(r"<audio\b[\s\S]*?</audio>", re.I)
_SOURCE_SRC = re.compile(r"""<source[^>]*\bsrc=["']([^"']+)["']""", re.I)
_MP3_HREF = re.compile(r"""href=["']([^"']+\.mp3[^"']*)["']""", re.I)
_PARA = re.compile(r"<p\b[^>]*>([\s\S]*?)</p>", re.I)
_PDF = re.compile(r"""href=["']([^"']+\.pdf)["']""", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                   r"\s+(\d{1,2}),?\s+(\d{4})", re.I)
_WEEKDAY = re.compile(r"\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s*", re.I)
_IA_DIRECT = re.compile(r"^https?://ia\d+\.us\.archive\.org/\d+/items/", re.I)
_IA_ITEM = re.compile(r"archive\.org/download/([^/]+)/", re.I)


def _normalize_audio(src: str) -> str:
    """Strips WordPress's '?_=1' and points at archive.org's stable download address."""
    src = src.replace("&amp;", "&")
    src = re.sub(r"[?&]_=\d+$", "", src)
    src = _IA_DIRECT.sub("https://archive.org/download/", src)
    return re.sub(r"^http://((?:[\w-]+\.)*archive\.org/)", r"https://\1", src)


def _absolute(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://hpr.org" + href
    return re.sub(r"^http://", "https://", href)


def display_date(date_key: int) -> str:
    d = dt.date(date_key // 10_000, (date_key // 100) % 100, date_key % 100)
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def parse(html_text: str, from_archive: bool) -> list[Episode]:
    out: list[Episode] = []
    seen: set[str] = set()
    last = 0
    for m in _AUDIO.finditer(html_text):
        before = html_text[last:m.start()]
        last = m.end()

        src = _SOURCE_SRC.search(m.group(0)) or _MP3_HREF.search(m.group(0))
        if not src:
            continue
        audio = _normalize_audio(src.group(1))
        item = _IA_ITEM.search(audio)
        key = item.group(1) if item else audio
        if key in seen:
            continue
        seen.add(key)

        # The label is the paragraph(s) from the last one carrying a date up to the player.
        paras = _PARA.findall(before)
        start = max((i for i, p in enumerate(paras) if _DATE.search(net.html_to_text(p) or "")), default=-1)
        label_html = " ".join(paras[start:] if start >= 0 else paras[-1:])
        text = re.sub(r"\bplaylist\b", " ", net.html_to_text(label_html) or "", flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()

        date = _DATE.search(text)
        date_key = 0
        if date:
            month = _MONTHS.index(date.group(1).lower()) + 1
            date_key = int(date.group(3)) * 10_000 + month * 100 + int(date.group(2))
        if date_key:
            title = display_date(date_key)
        else:
            title = text or urllib.parse.unquote(audio.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        note = None
        if date:
            n = _WEEKDAY.sub(" ", text.replace(date.group(0), " "))
            n = re.sub(r"\s+", " ", n).strip(" -–—:,.|")
            note = n if len(n) > 1 else None
        pdf = _PDF.search(label_html)
        out.append(Episode(key, title, note, date_key, audio,
                           _absolute(pdf.group(1)) if pdf else None, from_archive))
    out.sort(key=lambda e: e.date_key, reverse=True)
    return out


_cache: dict[str, list[Episode]] = {}
_cache_lock = threading.Lock()


def cached(source: ShowSource) -> list[Episode] | None:
    with _cache_lock:
        return _cache.get(source.cache_key)


def load(source: ShowSource) -> list[Episode] | None:
    """Blocking; call through net.run_async."""
    html_text = None
    if source.rest_url:
        body = net.get_text(source.rest_url)
        if body:
            try:
                html_text = json.loads(body)["content"]["rendered"]
            except (ValueError, KeyError, TypeError):
                html_text = None
    if html_text is None and source.html_url:
        html_text = net.get_text(source.html_url)
    if html_text is None:
        return None
    eps = parse(html_text, source.is_archive)
    if eps:
        with _cache_lock:
            _cache[source.cache_key] = eps
    return eps


def find(show: Show, key: str) -> Episode | None:
    for s in show.sources:
        for e in cached(s) or []:
            if e.key == key:
                return e
    return None


# ---- listening progress ----------------------------------------------------------------

@dataclass(frozen=True)
class Progress:
    position: float  # seconds
    duration: float  # seconds; 0 if unknown
    done: bool

    @property
    def fraction(self) -> float:
        return max(0.0, min(1.0, self.position / self.duration)) if self.duration > 0 else 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.duration - self.position)


class ProgressStore:
    """Where you stopped in each episode, saved to ~/.local/share/hpr-radio/progress.json."""

    MIN_SAVE = 30.0    # don't remember the first 30 seconds
    DONE_WITHIN = 90.0  # within 90 s of the end counts as played

    def __init__(self) -> None:
        self._file = DATA_DIR / "progress.json"
        try:
            self._data = json.loads(self._file.read_text("utf-8"))
        except (OSError, ValueError):
            self._data = {}
        self._data.setdefault("episodes", {})

    def get(self, key: str) -> Progress | None:
        v = self._data["episodes"].get(key)
        return Progress(v.get("pos", 0.0), v.get("dur", 0.0), v.get("done", False)) if v else None

    def resume_position(self, key: str) -> float:
        p = self.get(key)
        return 0.0 if p is None or p.done else p.position

    def save(self, key: str, position: float, duration: float) -> None:
        if position < 0:
            return
        if duration > 0 and position >= duration - self.DONE_WITHIN:
            self.mark_played(key, duration)
            return
        existing = self.get(key)
        if position < self.MIN_SAVE and not (existing and existing.done):
            return
        self._write(key, {"pos": position, "dur": max(duration, 0.0), "done": False})

    def mark_played(self, key: str, duration: float | None = None) -> None:
        if duration is None:
            p = self.get(key)
            duration = p.duration if p else 0.0
        self._write(key, {"pos": 0.0, "dur": duration, "done": True})

    def clear(self, key: str) -> None:
        self._data["episodes"].pop(key, None)
        self._flush()

    @property
    def last(self) -> tuple[str, str] | None:
        """(show id, episode key) of the most recent episode you haven't finished."""
        v = self._data.get("last")
        if not v:
            return None
        p = self.get(v[1])
        return (v[0], v[1]) if p and not p.done else None

    def set_last(self, show_id: str, key: str) -> None:
        self._data["last"] = [show_id, key]
        self._flush()

    def _write(self, key: str, value: dict) -> None:
        self._data["episodes"][key] = value
        self._flush()

    def _flush(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data), "utf-8")
            tmp.replace(self._file)
        except OSError:
            pass
