"""Home: the channel list and the on-demand shows."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gtk

from .. import __version__
from ..player import Status
from .widgets import Art, PlayButton, clamp, clear, label, scrolled


class HomePage(Gtk.Box):
    def __init__(self, hub, open_channel: Callable, open_show: Callable) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.hub = hub
        self.open_channel = open_channel
        self.open_show = open_show

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                            margin_top=18, margin_bottom=18, margin_start=16, margin_end=16)
        self.append(scrolled(clamp(self.body)))

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_bottom=6)
        hero.append(label("LISTENER-SUPPORTED · COMMERCIAL-FREE", "eyebrow"))
        hero.append(label("Heartland Public Radio", "hero-title", wrap=True))
        hero.append(label("Classic Country & Bluegrass Gospel", "subtitle"))
        self.body.append(hero)

        self.channels = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.channels.add_css_class("boxed-list")
        self.channels.connect("row-activated", self._on_channel_row)
        self.body.append(self.channels)

        self.shows_header = label("ON DEMAND", "eyebrow")
        self.shows_header.set_margin_top(12)
        self.body.append(self.shows_header)
        self.shows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.shows.add_css_class("boxed-list")
        self.shows.connect("row-activated", lambda _l, row: self.open_show(row.show))
        self.body.append(self.shows)

        self.footer = label("", "footer", wrap=True)
        self.footer.set_margin_top(14)
        self.footer_small = label("", "footer-small", wrap=True)
        self.body.append(self.footer)
        self.body.append(self.footer_small)

        self._rows: dict[str, _ChannelRow] = {}
        hub.catalog.connect("changed", lambda *_: self.rebuild())
        hub.player.connect("changed", lambda *_: self.refresh())
        hub.connect("recent", lambda _h, sid: self._refresh_row(sid))
        self.rebuild()

    def rebuild(self) -> None:
        clear(self.channels)
        self._rows.clear()
        for st in self.hub.catalog.stations:
            row = _ChannelRow(self.hub, st)
            self._rows[st.id] = row
            self.channels.append(row)
        clear(self.shows)
        for show in self.hub.catalog.shows:
            self.shows.append(_ShowRow(self.hub, show))
        visible = bool(self.hub.catalog.shows)
        self.shows_header.set_visible(visible)
        self.shows.set_visible(visible)
        self.footer.set_label("Unofficial player. Programming and streams belong to Heartland Public Radio, Inc. "
                              "Cover art from Apple Music and Deezer.")
        self.refresh()

    def refresh(self) -> None:
        for row in self._rows.values():
            row.refresh()
        row = self.shows.get_first_child()
        while row is not None:
            if isinstance(row, _ShowRow):
                row.refresh()
            row = row.get_next_sibling()
        self.footer_small.set_label(f"Version {__version__} · channel list: {self.hub.catalog.source}")

    def _refresh_row(self, sid: str) -> None:
        if sid in self._rows:
            self._rows[sid].refresh()

    def _on_channel_row(self, _list, row) -> None:
        if row.station.available:
            self.open_channel(row.station)


class _ChannelRow(Gtk.ListBoxRow):
    def __init__(self, hub, station) -> None:
        super().__init__(activatable=station.available)
        self.hub = hub
        self.station = station
        box = Gtk.Box(spacing=14)
        self.art = Art(hub.images, 64, radius=12)
        box.append(self.art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER, hexpand=True)
        self.eyebrow = label(station.channel.upper(), "eyebrow")
        self.name = label(station.name, "row-title")
        self.sub = label("", "dim", wrap=True, lines=2)
        for w in (self.eyebrow, self.name, self.sub):
            texts.append(w)
        box.append(texts)
        self.button = PlayButton(40, suggested=False)
        self.button.add_css_class("flat")
        self.button.connect("clicked", self._on_play)
        self.button.set_visible(station.available)
        box.append(self.button)
        self.set_child(box)
        self._art_key = None

    def refresh(self) -> None:
        st, p = self.station, self.hub.player
        current = p.kind == "live" and p.station is not None and p.station.id == st.id
        if current:
            self.add_css_class("selected-channel")
        else:
            self.remove_css_class("selected-channel")
        artist, title = self.hub.on_air(st)
        if not st.available:
            sub = st.note or st.description
        elif title:
            sub = f"{title} · {artist}" if artist else title
        else:
            sub = st.description
        self.sub.set_label(sub)
        self.button.update(p.status if current else Status.STOPPED, pauses=False)

        key = (artist, title)
        if key != self._art_key:
            self._art_key = key
            self.art.set(None, st.channel, st.color)
            self.hub.art_for(artist, title,
                             lambda art, k=key: k == self._art_key and self.art.set(
                                 art.image_url if art else None, st.channel, st.color))

    def _on_play(self, _b) -> None:
        p = self.hub.player
        if p.kind == "live" and p.station and p.station.id == self.station.id and p.is_active:
            p.stop()
        else:
            p.play_station(self.station)


class _ShowRow(Gtk.ListBoxRow):
    def __init__(self, hub, show) -> None:
        super().__init__(activatable=True)
        self.hub = hub
        self.show = show
        box = Gtk.Box(spacing=14)
        art = Art(hub.images, 64, radius=12)
        art.set(show.image_url, show.badge, "#8A4B1F")
        box.append(art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER, hexpand=True)
        texts.append(label(show.title, "row-title"))
        if show.host:
            texts.append(label(f"with {show.host}", "dim"))
        self.status = label("", "eyebrow")
        texts.append(self.status)
        box.append(texts)
        box.append(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER))
        self.set_child(box)

    def refresh(self) -> None:
        p = self.hub.player
        last = self.hub.progress.last
        if p.kind == "episode" and p.show and p.show.id == self.show.id:
            text = "NOW PLAYING" if p.is_active else "PAUSED"
        elif last and last[0] == self.show.id:
            text = "CONTINUE WHERE YOU LEFT OFF"
        else:
            text = "RECENT EPISODES AND ARCHIVE"
        self.status.set_label(text)

