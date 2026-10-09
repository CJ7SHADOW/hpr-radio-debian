"""Small HTTP helpers. All network calls run on a worker thread; results come back
to the GTK main loop through run_async()."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import __version__

USER_AGENT = f"HPRRadio/{__version__} (Linux; +https://hpr.org)"
# Two pools so the long tail of album-art lookups and image downloads never delays what
# the user is waiting on (episode lists, the channel list, song histories).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hpr-net")
_background = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hpr-art")


def get(url: str, timeout: float = 10.0, max_bytes: int = 2_000_000) -> bytes | None:
    """GET a URL. Returns the body, or None on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or 200  # file:// responses have no status
            if not 200 <= status < 300:
                return None
            return resp.read(max_bytes)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def get_text(url: str, timeout: float = 10.0, max_bytes: int = 2_000_000) -> str | None:
    body = get(url, timeout, max_bytes)
    return body.decode("utf-8", "replace") if body is not None else None


def run_async(fn: Callable, *args, callback: Callable | None = None, background: bool = False) -> None:
    """Run fn(*args) on a worker thread, then callback(result) on the GTK main loop.
    background=True uses the separate pool for art lookups and image downloads."""
    from gi.repository import GLib

    def done(future):
        try:
            result = future.result()
        except Exception:  # never let a network hiccup crash the app
            result = None
        if callback is not None:
            GLib.idle_add(lambda: (callback(result), False)[1])

    (_background if background else _executor).submit(fn, *args).add_done_callback(done)


_TAG = re.compile(r"<[^>]+>")


def html_to_text(s: str | None) -> str | None:
    """'Loretta Lynn &amp; <b>Conway</b>' -> 'Loretta Lynn & Conway'"""
    if s is None:
        return None
    text = html.unescape(_TAG.sub(" ", s)).replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
