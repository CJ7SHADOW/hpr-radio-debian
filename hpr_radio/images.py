"""Downloads and caches cover images: on disk (~/.cache/hpr-radio/art) and in memory
as GDK textures."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from gi.repository import Gdk, GLib

from . import net
from .paths import ART_DIR

_MAX_TEXTURES = 300


def path_for(url: str) -> Path:
    return ART_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".img")


def _fetch(url: str) -> bytes | None:
    p = path_for(url)
    try:
        return p.read_bytes()
    except OSError:
        pass
    data = net.get(url, timeout=15, max_bytes=5_000_000)
    if data:
        try:
            ART_DIR.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except OSError:
            pass
    return data


class ImageCache:
    def __init__(self) -> None:
        self._textures: OrderedDict[str, Gdk.Texture | None] = OrderedDict()
        self._waiting: dict[str, list[Callable]] = {}

    def get(self, url: str | None, callback: Callable[[Gdk.Texture | None], None]) -> None:
        """callback(texture) on the main loop; texture is None if the image can't be had."""
        if not url:
            callback(None)
            return
        if url in self._textures:
            self._textures.move_to_end(url)
            callback(self._textures[url])
            return
        if url in self._waiting:
            self._waiting[url].append(callback)
            return
        self._waiting[url] = [callback]
        net.run_async(_fetch, url, callback=lambda data: self._loaded(url, data), background=True)

    def local_uri(self, url: str | None) -> str | None:
        """file:// address of a cached image (for the desktop's media controls)."""
        if not url:
            return None
        p = path_for(url)
        return p.as_uri() if p.exists() else None

    def _loaded(self, url: str, data: bytes | None) -> None:
        tex = None
        if data:
            try:
                tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                tex = None
        self._textures[url] = tex
        while len(self._textures) > _MAX_TEXTURES:
            self._textures.popitem(last=False)
        for cb in self._waiting.pop(url, []):
            cb(tex)
