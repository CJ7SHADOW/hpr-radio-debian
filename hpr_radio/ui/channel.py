"""One channel: big cover for the song on the air, play/stop, and the recently played list."""

from __future__ import annotations

from gi.repository import Gtk

from .. import recent
from ..player import Status
from .widgets import Art, PlayButton, clamp, clear, format_time_of_day, label, scrolled, status_line


class ChannelPage(Gtk.Box):
    def __init__(self, hub, station) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.hub = hub
        self.station = station
        self.title = station.full_name

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=18, margin_bottom=24, margin_start=16, margin_end=16)
        self.append(scrolled(clamp(body, 560)))

        self.art = Art(hub.images, 260, radius=20)
        self.art.set_margin_bottom(14)
        body.append(self.art)
        self.eyebrow = label("", "eyebrow", xalign=0.5)
        self.song = label("", "page-title", wrap=True, xalign=0.5)
        self.song.set_justify(Gtk.Justification.CENTER)
        self.artist = label("", "subtitle", wrap=True, xalign=0.5)
        self.album = label("", "dim", xalign=0.5)
        for w in (self.eyebrow, self.song, self.artist, self.album):
            body.append(w)

        self.button = PlayButton(68)
        self.button.set_margin_top(14)
        self.button.connect("clicked", self._on_play)
        body.append(self.button)
        self.status = label("", "status", xalign=0.5)
        self.status.set_size_request(-1, 28)
        body.append(self.status)

        body.append(label("Recently played", "section"))
        self.empty = label("", "dim")
        self.empty.set_margin_top(6)
        body.append(self.empty)
        self.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.history.add_css_class("boxed-list")
        self.history.set_margin_top(6)
        body.append(self.history)

        self._art_key = None
        self._history_keys: list[str] = []
        self._handlers = [
            (hub.player, hub.player.connect("changed", lambda *_: self.refresh())),
            (hub, hub.connect("recent", lambda _h, sid: sid == station.id and self.refresh())),
            (hub, hub.connect("now-art", lambda *_: self.refresh())),
        ]
        self.refresh()

    def teardown(self) -> None:
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []

    def _is_current(self) -> bool:
        p = self.hub.player
        return p.kind == "live" and p.station is not None and p.station.id == self.station.id

    def refresh(self) -> None:
        st, p = self.station, self.hub.player
        current = self._is_current()
        status = p.status if current else Status.STOPPED
        artist, title = self.hub.on_air(st)

        self.eyebrow.set_label("ON THE AIR" if title else st.description.upper())
        self.song.set_label(title or st.name)
        self.artist.set_label(artist or "")
        self.artist.set_visible(bool(artist))

        key = (artist, title)
        if key != self._art_key:
            self._art_key = key
            self.album.set_visible(False)
            self.art.set(None, st.channel, st.color)

            def got(art, k=key):
                if k != self._art_key:
                    return
                self.art.set(art.image_url if art else None, st.channel, st.color)
                self.album.set_label(art.album if art and art.album else "")
                self.album.set_visible(bool(art and art.album))
            self.hub.art_for(artist, title, got)

        self.button.update(status, pauses=False)
        line = status_line(status) or ("Listening live" if status == Status.PLAYING else "")
        self.status.set_label(line)
        if status == Status.ERROR:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")

        tracks = self.hub.recent.get(st.id)
        now_key = f"{(artist or '').lower()}|{(title or '').lower()}"
        history = [t for t in (tracks or []) if t.key != now_key][: recent.LIMIT - 1]
        keys = [f"{t.played_at}|{t.key}" for t in history]
        if keys != self._history_keys:
            self._history_keys = keys
            clear(self.history)
            for t in history:
                self.history.append(_TrackRow(self.hub, st, t))
        self.history.set_visible(bool(history))
        if st.recent_tracks_url is None:
            empty = "No song history for this channel."
        elif tracks is None and not self.hub.recent_loaded:
            empty = "Loading…"
        elif tracks is None:
            empty = "Couldn't load the song history. It'll retry shortly."
        elif not history:
            empty = "Nothing yet."
        else:
            empty = ""
        self.empty.set_label(empty)
        self.empty.set_visible(bool(empty))

    def _on_play(self, _b) -> None:
        p = self.hub.player
        if self._is_current() and p.is_active:
            p.stop()
        else:
            p.play_station(self.station)


class _TrackRow(Gtk.ListBoxRow):
    def __init__(self, hub, station, track) -> None:
        super().__init__(activatable=False)
        box = Gtk.Box(spacing=12)
        art = Art(hub.images, 48, radius=8)
        art.set(None, station.channel, station.color)
        hub.art_for(track.artist, track.title,
                    lambda a: a and art.set(a.image_url, station.channel, station.color))
        box.append(art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        texts.append(label(track.title, "row-title"))
        if track.artist:
            texts.append(label(track.artist, "dim"))
        box.append(texts)
        box.append(label(format_time_of_day(track.played_at), "time"))
        self.set_child(box)
