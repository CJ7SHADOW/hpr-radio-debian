"""Small HTTP helpers. All network calls run on a worker thread; results come back
to the GTK main loop through run_async()."""

from __future__ import annotations

import html
import os
import time
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import __version__

USER_AGENT = f"HPRRadio/{__version__} (Linux; +https://hpr.org)"
# Two pools so the long tail of album-art lookups and image downloads never delays what
# the user is waiting on (episode lists, the channel list, song histories).
_pools = {
    "page": ThreadPoolExecutor(max_workers=3, thread_name_prefix="hpr-page"),  # episode lists, channel list
    "poll": ThreadPoolExecutor(max_workers=3, thread_name_prefix="hpr-poll"),  # song histories
    "art": ThreadPoolExecutor(max_workers=4, thread_name_prefix="hpr-art"),    # cover-art lookups, images
}
_DEBUG = bool(os.environ.get("HPR_DEBUG"))
_T0 = time.monotonic()


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


def run_async(fn: Callable, *args, callback: Callable | None = None, lane: str = "page") -> None:
    """Run fn(*args) on a worker thread, then callback(result) on the GTK main loop.
    lane picks the worker pool: "page" (what the user is waiting on), "poll", or "art"."""
    from gi.repository import GLib

    def done(future):
        try:
            result = future.result()
        except Exception:  # never let a network hiccup crash the app
            result = None
        if callback is not None:
            def deliver():
                if _DEBUG and lane == "page":
                    print(f"[net] delivered t={time.monotonic() - _T0:.1f}", flush=True)
                callback(result)
                return False
            GLib.idle_add(deliver)

    queued = time.monotonic()

    def timed(*a):
        started = time.monotonic()
        try:
            return fn(*a)
        finally:
            if _DEBUG:
                print(f"[net] {lane} t={time.monotonic() - _T0:.1f} {getattr(fn, '__qualname__', fn)}: waited "
                      f"{started - queued:.1f}s, ran {time.monotonic() - started:.1f}s", flush=True)

    _pools[lane].submit(timed, *args).add_done_callback(done)


_TAG = re.compile(r"<[^>]+>")


def html_to_text(s: str | None) -> str | None:
    """'Loretta Lynn &amp; <b>Conway</b>' -> 'Loretta Lynn & Conway'"""
    if s is None:
        return None
    text = html.unescape(_TAG.sub(" ", s)).replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
