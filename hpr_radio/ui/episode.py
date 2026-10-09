"""Full player for an on-demand episode: cover, seek bar, skip back/forward, playlist."""

from __future__ import annotations

from typing import Callable

from gi.repository import GLib, Gtk

from ..player import Status
from .widgets import Art, PlayButton, clamp, format_clock, label, scrolled, status_line


class EpisodePage(Gtk.Box):
    def __init__(self, hub, open_show: Callable, open_url: Callable) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.hub = hub
        self.open_url = open_url
        p = hub.player
        self.show = p.show
        self.title = p.show.title if p.show else "Episode"

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=18, margin_bottom=24, margin_start=20, margin_end=20)
        self.append(scrolled(clamp(body, 480)))

        self.art = Art(hub.images, 260, radius=20)
        self.art.set_margin_bottom(14)
        body.append(self.art)
        self.eyebrow = label("", "eyebrow", xalign=0.5)
        self.ep_title = label("", "page-title", wrap=True, xalign=0.5)
        self.ep_title.set_justify(Gtk.Justification.CENTER)
        self.note = label("", "subtitle", wrap=True, xalign=0.5)
        self.host = label("", "dim", xalign=0.5)
        for w in (self.eyebrow, self.ep_title, self.note, self.host):
            body.append(w)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.scale.set_draw_value(False)
        self.scale.set_margin_top(14)
        self.scale.connect("change-value", self._on_change_value)
        body.append(self.scale)
        times = Gtk.Box()
        self.elapsed = label("0:00", "time")
        self.remaining = label("--:--", "time", xalign=1.0)
        self.remaining.set_hexpand(True)
        times.append(self.elapsed)
        times.append(self.remaining)
        body.append(times)

        controls = Gtk.Box(spacing=28, halign=Gtk.Align.CENTER, margin_top=10)
        back = self._skip_button("object-rotate-left-symbolic", "15", "Back 15 seconds", -15)
        fwd = self._skip_button("object-rotate-right-symbolic", "30", "Forward 30 seconds", 30)
        self.button = PlayButton(72)
        self.button.connect("clicked", lambda *_: hub.player.toggle())
        controls.append(back)
        controls.append(self.button)
        controls.append(fwd)
        body.append(controls)

        self.status = label("", "status", xalign=0.5)
        self.status.set_size_request(-1, 30)
        body.append(self.status)

        actions = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.playlist_button = Gtk.Button()
        self.playlist_button.add_css_class("pill")
        pl = Gtk.Box(spacing=8)
        pl.append(Gtk.Image(icon_name="view-list-symbolic"))
        pl.append(Gtk.Label(label="Song playlist (PDF)"))
        self.playlist_button.set_child(pl)
        self.playlist_button.connect("clicked", self._on_playlist)
        actions.append(self.playlist_button)
        if self.show is not None:
            all_eps = Gtk.Button(label="All episodes")
            all_eps.add_css_class("pill")
            all_eps.connect("clicked", lambda *_: open_show(self.show))
            actions.append(all_eps)
        body.append(actions)

        self._seek_source = 0
        self._pending_seek: float | None = None
        self._handlers = [(hub.player, hub.player.connect("changed", lambda *_: self.refresh()))]
        self._tick_source = GLib.timeout_add(500, self._tick)
        self.refresh()

    def teardown(self) -> None:
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0

    def _skip_button(self, icon: str, text: str, tip: str, delta: float) -> Gtk.Button:
        b = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text=tip)
        b.add_css_class("circular")
        b.add_css_class("flat")
        b.set_size_request(56, 56)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        box.append(Gtk.Image(icon_name=icon, pixel_size=22))
        box.append(label(text, "skip-label", xalign=0.5))
        b.set_child(box)
        b.connect("clicked", lambda *_: self.hub.player.skip(delta))
        return b

    def refresh(self) -> None:
        p = self.hub.player
        ep, show = p.episode, p.show
        if ep is None or show is None:
            return
        self.eyebrow.set_label(show.title.upper())
        self.ep_title.set_label(ep.title)
        self.note.set_label(ep.note or "")
        self.note.set_visible(bool(ep.note))
        self.host.set_label(f"with {show.host}" if show.host else "")
        self.art.set(show.image_url, show.badge, "#8A4B1F")
        self.button.update(p.status, pauses=True)
        if p.status == Status.STOPPED and p.position() == 0 and self.hub.progress.get(ep.key) \
                and self.hub.progress.get(ep.key).done:
            line = "Finished"
        elif p.status == Status.PAUSED:
            line = "Paused"
        elif p.status == Status.RECONNECTING:
            line = "Connection dropped, retrying…"
        else:
            line = status_line(p.status, episode=True) or ""
        self.status.set_label(line)
        if p.status == Status.ERROR:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")
        self.playlist_button.set_visible(bool(ep.playlist_url))
        self._tick()

    def _tick(self) -> bool:
        p = self.hub.player
        if p.kind != "episode":
            return True
        dur = p.duration()
        pos = self._pending_seek if self._pending_seek is not None else p.position()
        adj = self.scale.get_adjustment()
        if dur > 0:
            if adj.get_upper() != dur:
                adj.set_upper(dur)
            self.scale.set_sensitive(True)
        else:
            self.scale.set_sensitive(False)
        if self._pending_seek is None:
            self.scale.set_value(min(pos, dur) if dur > 0 else 0)
        self.elapsed.set_label(format_clock(pos))
        self.remaining.set_label("-" + format_clock(dur - pos) if dur > 0 else "--:--")
        return True

    def _on_change_value(self, _scale, _scroll, value) -> bool:
        # Wait until the drag settles, then seek once.
        self._pending_seek = max(0.0, value)
        self.elapsed.set_label(format_clock(value))
        if self._seek_source:
            GLib.source_remove(self._seek_source)
        self._seek_source = GLib.timeout_add(250, self._do_seek)
        return False

    def _do_seek(self) -> bool:
        self._seek_source = 0
        if self._pending_seek is not None:
            self.hub.player.seek(self._pending_seek)
            self._pending_seek = None
        return False

    def _on_playlist(self, _b) -> None:
        ep = self.hub.player.episode
        if ep and ep.playlist_url:
            self.open_url(ep.playlist_url)
