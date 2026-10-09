"""Shared app state the screens read from: the catalog, the player, song histories,
cover-art lookups and listening progress."""

from __future__ import annotations

from typing import Callable

from gi.repository import GLib, GObject

from . import artwork, net, recent
from .artwork import Artwork
from .catalog import Catalog, Station
from .episodes import ProgressStore
from .images import ImageCache
from .player import Player
from .settings import Settings

POLL_SECONDS = 20


class Hub(GObject.Object):
    __gsignals__ = {
        # A channel's recently played list changed (station id).
        "recent": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Cover art for what's playing now changed.
        "now-art": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Episode progress changed (played / unplayed / resume point).
        "progress": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.catalog = Catalog(settings.config_url)
        self.progress = ProgressStore()
        self.player = Player(settings, self.progress, self.catalog)
        self.images = ImageCache()
        self.recent: dict[str, list[recent.Track]] = {}
        self.recent_loaded = False
        self.now_art: Artwork | None = None
        self._now_key = None
        self._poll_source = 0
        self._fetching: set[str] = set()

        self.player.connect("changed", self._on_player_changed)
        self.catalog.connect("changed", lambda *_: self.refresh_recent())

    # ---- song histories ------------------------------------------------------------

    def start_polling(self) -> None:
        if not self._poll_source:
            self.refresh_recent()
            self._poll_source = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

    def stop_polling(self) -> None:
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _poll(self) -> bool:
        self.refresh_recent()
        return True

    def refresh_recent(self) -> None:
        for st in self.catalog.available:
            if st.id in self._fetching:
                continue  # the last request for this channel hasn't come back yet
            self._fetching.add(st.id)
            net.run_async(recent.fetch, st, lane="poll",
                          callback=lambda tracks, sid=st.id: self._got_recent(sid, tracks))

    def _got_recent(self, sid: str, tracks) -> None:
        self._fetching.discard(sid)
        self.recent_loaded = True
        if tracks is not None:
            self.recent[sid] = recent.merge(self.recent.get(sid), tracks)
        self.emit("recent", sid)

    def on_air(self, station: Station) -> tuple[str | None, str | None]:
        """(artist, title) on the air now: the stream's own title if we're listening to
        this channel, otherwise the top of its song history."""
        p = self.player
        if p.kind == "live" and p.station and p.station.id == station.id and p.song_title:
            return p.song_artist, p.song_title
        tracks = self.recent.get(station.id)
        if tracks:
            return tracks[0].artist, tracks[0].title
        return None, None

    # ---- cover art -----------------------------------------------------------------

    def art_for(self, artist: str | None, title: str | None, callback: Callable[[Artwork | None], None]) -> None:
        if not artist or not title:
            callback(None)
            return
        hit = artwork.cached(artist, title)
        if hit is not None or artwork.is_known(artist, title):
            callback(hit)
            return
        net.run_async(artwork.find, artist, title, callback=callback, lane="art")

    def _on_player_changed(self, _p) -> None:
        p = self.player
        key = (p.kind, p.station.id if p.station else None, p.song_artist, p.song_title,
               p.show.id if p.show else None)
        if key == self._now_key:
            return
        self._now_key = key
        self.now_art = None
        self.emit("now-art")
        if p.kind == "live" and p.song_title:
            def got(art, k=key):
                if k == self._now_key:
                    self.now_art = art
                    if art:  # make sure it's on disk so the desktop's media controls can show it
                        self.images.get(art.image_url, lambda _t: self.emit("now-art"))
                    self.emit("now-art")
            self.art_for(p.song_artist, p.song_title, got)
        elif p.kind == "episode" and p.show and p.show.image_url:
            self.images.get(p.show.image_url, lambda _t: self.emit("now-art"))

    def now_art_uri(self) -> str | None:
        """file:// URI of the current cover, for MPRIS."""
        p = self.player
        if p.kind == "live" and self.now_art:
            return self.images.local_uri(self.now_art.image_url)
        if p.kind == "episode" and p.show:
            return self.images.local_uri(p.show.image_url)
        return None
