"""MPRIS 2 on the session bus, so keyboard media keys, the GNOME/KDE/Cinnamon sound menu
and lock-screen controls work. For live radio, Next/Previous switch channels; for an
episode they jump forward 30 s / back 15 s."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gio, GLib

from . import APP_ID, APP_NAME
from .player import Player, Status

_PATH = "/org/mpris/MediaPlayer2"
_BUS_NAME = "org.mpris.MediaPlayer2.hpr_radio"

_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""

_ROOT = "org.mpris.MediaPlayer2"
_PLAYER = "org.mpris.MediaPlayer2.Player"


class Mpris:
    def __init__(self, player: Player, catalog, art_for: Callable[[], str | None],
                 on_raise: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self.player = player
        self.catalog = catalog
        self.art_for = art_for
        self.on_raise = on_raise
        self.on_quit = on_quit
        self._conn: Gio.DBusConnection | None = None
        self._last: dict = {}
        self._track_no = 0
        self._track_key = None
        self._node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, _BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       self._on_bus, None, None)
        player.connect("changed", lambda *_: self.update())
        player.connect("seeked", self._on_seeked)

    def close(self) -> None:
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0

    # ---- registration --------------------------------------------------------------

    def _on_bus(self, conn: Gio.DBusConnection, _name: str) -> None:
        self._conn = conn
        for iface in self._node.interfaces:
            conn.register_object(_PATH, iface, self._on_call, self._on_get, self._on_set)

    # ---- state ---------------------------------------------------------------------

    def _status(self) -> str:
        s = self.player.status
        if s in Status.ACTIVE:
            return "Playing"
        if s == Status.PAUSED:
            return "Paused"
        return "Stopped"

    def _track_id(self) -> str:
        p = self.player
        key = (p.kind, p.station.id if p.station else None, p.episode.key if p.episode else None,
               p.song_title, p.song_artist)
        if key != self._track_key:
            self._track_key = key
            self._track_no += 1
        return f"/org/kg4bhr/HprRadio/track/{self._track_no}"

    def _metadata(self) -> dict:
        p = self.player
        md: dict[str, GLib.Variant] = {"mpris:trackid": GLib.Variant("o", self._track_id())}
        if p.kind == "live" and p.station:
            title = p.song_title or p.station.full_name
            artist = p.song_artist or (p.station.full_name if p.song_title else p.station.description)
            md["xesam:title"] = GLib.Variant("s", title)
            md["xesam:artist"] = GLib.Variant("as", [artist] if artist else [])
            md["xesam:album"] = GLib.Variant("s", p.station.full_name)
        elif p.kind == "episode" and p.episode and p.show:
            md["xesam:title"] = GLib.Variant("s", p.episode.title)
            md["xesam:artist"] = GLib.Variant("as", [p.show.byline])
            md["xesam:album"] = GLib.Variant("s", p.show.title)
            dur = p.duration()
            if dur > 0:
                md["mpris:length"] = GLib.Variant("x", int(dur * 1_000_000))
        art = self.art_for()
        if art:
            md["mpris:artUrl"] = GLib.Variant("s", art)
        return md

    def _props(self) -> dict:
        p = self.player
        has = p.kind is not None or bool(self.catalog.available)
        return {
            "PlaybackStatus": GLib.Variant("s", self._status()),
            "Metadata": GLib.Variant("a{sv}", self._metadata()),
            "Volume": GLib.Variant("d", p.volume),
            "CanGoNext": GLib.Variant("b", p.kind is not None),
            "CanGoPrevious": GLib.Variant("b", p.kind is not None),
            "CanPlay": GLib.Variant("b", has),
            "CanPause": GLib.Variant("b", has),
            "CanSeek": GLib.Variant("b", p.kind == "episode"),
        }

    def update(self) -> None:
        if self._conn is None:
            return
        now = self._props()
        changed = {k: v for k, v in now.items() if self._last.get(k) is None or not self._last[k].equal(v)}
        self._last = now
        if changed:
            self._conn.emit_signal(None, _PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                   GLib.Variant("(sa{sv}as)", (_PLAYER, changed, [])))

    def _on_seeked(self, _player, seconds: float) -> None:
        if self._conn is not None:
            self._conn.emit_signal(None, _PATH, _PLAYER, "Seeked", GLib.Variant("(x)", (int(seconds * 1_000_000),)))

    # ---- D-Bus handlers ------------------------------------------------------------

    def _on_get(self, _conn, _sender, _path, iface, prop):
        if iface == _ROOT:
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", APP_NAME),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", []),
                "SupportedMimeTypes": GLib.Variant("as", []),
            }.get(prop)
        p = self.player
        if prop == "Position":
            return GLib.Variant("x", int(p.position() * 1_000_000))
        if prop in ("Rate", "MinimumRate", "MaximumRate"):
            return GLib.Variant("d", 1.0)
        if prop == "CanControl":
            return GLib.Variant("b", True)
        return self._props().get(prop)

    def _on_set(self, _conn, _sender, _path, _iface, prop, value) -> bool:
        if prop == "Volume":
            self.player.set_volume(value.get_double())
        return True

    def _on_call(self, _conn, _sender, _path, iface, method, params, invocation) -> None:
        p = self.player
        if iface == _ROOT:
            if method == "Raise":
                self.on_raise()
            elif method == "Quit":
                self.on_quit()
        elif method == "PlayPause":
            p.toggle()
        elif method == "Play":
            if not p.is_active:
                p.toggle()
        elif method == "Pause":
            if p.is_active:
                p.pause()
        elif method == "Stop":
            p.stop()
        elif method in ("Next", "Previous"):
            self._next(1 if method == "Next" else -1)
        elif method == "Seek":
            p.skip(params.unpack()[0] / 1_000_000)
        elif method == "SetPosition":
            p.seek(params.unpack()[1] / 1_000_000)
        invocation.return_value(None)

    def _next(self, step: int) -> None:
        p = self.player
        if p.kind == "episode":
            p.skip(30 if step > 0 else -15)
            return
        stations = self.catalog.available
        if not stations:
            return
        ids = [s.id for s in stations]
        i = ids.index(p.station.id) if p.station and p.station.id in ids else -step
        p.play_station(stations[(i + step) % len(stations)])
