"""GStreamer playback for live channels and on-demand episodes.

Live streams get the same treatment as the Android app: when the server drops the
connection (or the network goes away) it reconnects with a back-off, and if a server
answers with something that isn't audio it fails over to the next stream address.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst  # noqa: E402

from . import net  # noqa: E402
from .catalog import Show, Station  # noqa: E402
from .episodes import Episode, ProgressStore  # noqa: E402

Gst.init(None)

# Stream errors that mean "this isn't audio we can play"; anything else (like the generic
# "internal data stream error" a dropped connection causes) is retried.
_BAD_STREAM_CODES = (Gst.StreamError.TYPE_NOT_FOUND, Gst.StreamError.WRONG_TYPE,
                     Gst.StreamError.CODEC_NOT_FOUND, Gst.StreamError.DECODE, Gst.StreamError.DEMUX,
                     Gst.StreamError.FORMAT, Gst.StreamError.NOT_IMPLEMENTED)

_PLAY_FLAG_AUDIO = 0x02
_PLAY_FLAG_SOFT_VOLUME = 0x10


class Status:
    STOPPED = "stopped"
    CONNECTING = "connecting"
    PLAYING = "playing"
    PAUSED = "paused"            # episodes only
    RECONNECTING = "reconnecting"
    ERROR = "error"

    ACTIVE = {CONNECTING, PLAYING, RECONNECTING}


class Player(GObject.Object):
    """Emits 'changed' whenever anything the UI shows may have changed."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    NETWORK_RETRIES_PER_URL = 3
    EPISODE_RETRIES = 5

    def __init__(self, settings, progress: ProgressStore, catalog) -> None:
        super().__init__()
        self.settings = settings
        self.progress = progress
        self.catalog = catalog

        self.station: Station | None = None
        self.show: Show | None = None
        self.episode: Episode | None = None
        self.status = Status.STOPPED
        self.song_title: str | None = None
        self.song_artist: str | None = None

        self._stream_index = 0
        self._candidates_tried = 0
        self._network_retries = 0
        self._retry_source = 0
        self._progress_source = 0
        self._pending_seek: float | None = None
        self._last_position = 0.0
        self._buffering = False

        self.pb = Gst.ElementFactory.make("playbin", "hpr-player")
        if self.pb is None:
            raise RuntimeError("GStreamer 'playbin' is missing (install gstreamer1.0-plugins-base)")
        self.pb.props.flags = _PLAY_FLAG_AUDIO | _PLAY_FLAG_SOFT_VOLUME
        # First run starts at 70% on the slider (about -9 dB), not full scale.
        self.pb.props.volume = float(settings.get("volume", 0.7 ** 3))
        self.pb.props.mute = bool(settings.get("muted", False))
        sink_name = os.environ.get("HPR_AUDIO_SINK")  # e.g. "fakesink" for testing without a sound card
        if sink_name:
            sink = Gst.ElementFactory.make(sink_name)
            if sink is not None:
                if sink.find_property("sync"):
                    sink.set_property("sync", True)
                self.pb.props.audio_sink = sink
        self.pb.connect("source-setup", self._on_source_setup)
        bus = self.pb.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

    # ---- what's loaded -------------------------------------------------------------

    @property
    def kind(self) -> str | None:
        if self.episode is not None:
            return "episode"
        if self.station is not None:
            return "live"
        return None

    @property
    def is_active(self) -> bool:
        return self.status in Status.ACTIVE

    # ---- live channels -------------------------------------------------------------

    def play_station(self, station: Station) -> None:
        if not station.available:
            return
        if self.station and self.station.id == station.id and self.is_active:
            return
        self._save_episode_progress()
        self._reset()
        self.station, self.show, self.episode = station, None, None
        self.song_title = self.song_artist = None
        idx = int((self.settings.get("stream_index") or {}).get(station.id, 0))
        self._stream_index = idx if 0 <= idx < len(station.streams) else 0
        self.settings.set("last_station", station.id)
        self._start(station.streams[self._stream_index])

    # ---- episodes ------------------------------------------------------------------

    def play_episode(self, show: Show, episode: Episode, from_start: bool = False) -> None:
        if from_start:
            self.progress.clear(episode.key)
        same = self.episode is not None and self.episode.key == episode.key
        if same and not from_start and self.status == Status.PAUSED:
            self.resume()
            return
        if same and not from_start and self.is_active:
            return
        self._save_episode_progress()
        self._reset()
        self.station, self.show, self.episode = None, show, episode
        self.song_title = self.song_artist = None
        resume = self.progress.resume_position(episode.key)
        self._pending_seek = resume if resume > 0 else None
        self._last_position = resume
        self.progress.set_last(show.id, episode.key)
        self._start(episode.audio_url)

    def seek(self, seconds: float) -> None:
        if self.kind != "episode":
            return
        seconds = max(0.0, seconds)
        dur = self.duration()
        if dur > 0:
            seconds = min(seconds, max(0.0, dur - 1))
        if self.status in (Status.CONNECTING, Status.RECONNECTING):
            self._pending_seek = seconds
            return
        ok = self.pb.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                 int(seconds * Gst.SECOND))
        if not ok:
            self._pending_seek = seconds
        self._last_position = seconds
        self._save_episode_progress()
        self.emit("seeked", seconds)

    def skip(self, delta: float) -> None:
        self.seek(self.position() + delta)

    def position(self) -> float:
        if self.kind != "episode":
            return 0.0
        if self._pending_seek is not None:
            return self._pending_seek
        ok, ns = self.pb.query_position(Gst.Format.TIME)
        if ok and ns >= 0:
            self._last_position = ns / Gst.SECOND
        return self._last_position

    def duration(self) -> float:
        if self.kind != "episode":
            return 0.0
        ok, ns = self.pb.query_duration(Gst.Format.TIME)
        return ns / Gst.SECOND if ok and ns > 0 else 0.0

    # ---- either --------------------------------------------------------------------

    def pause(self) -> None:
        """Episodes pause in place; live channels stop (a live stream can't be paused)."""
        if self.kind == "episode":
            self._cancel_retry()
            self._save_episode_progress()
            self.pb.set_state(Gst.State.PAUSED)
            self._set_status(Status.PAUSED)
        else:
            self.stop()

    def resume(self) -> None:
        if self.kind == "episode":
            if self.status == Status.PAUSED:
                self.pb.set_state(Gst.State.PLAYING)
                self._set_status(Status.CONNECTING)
            elif not self.is_active:
                self._pending_seek = self._last_position or self.progress.resume_position(self.episode.key)
                self._start(self.episode.audio_url)
        elif self.station is not None and not self.is_active:
            self._reset()
            self._start(self.station.streams[self._stream_index])

    def toggle(self) -> None:
        if self.is_active:
            self.pause()
        elif self.kind is not None:
            self.resume()
        else:
            station = self.catalog.station(self.settings.get("last_station"))
            if station is None or not station.available:
                station = next(iter(self.catalog.available), None)
            if station is not None:
                self.play_station(station)

    def stop(self) -> None:
        self._save_episode_progress()
        self._cancel_retry()
        self._stop_progress_timer()
        self.pb.set_state(Gst.State.NULL)
        self._buffering = False
        self._set_status(Status.STOPPED)

    def set_volume(self, v: float) -> None:
        v = max(0.0, min(1.0, v))
        self.pb.props.volume = v
        self.settings.set("volume", v)
        self.emit("changed")

    @property
    def volume(self) -> float:
        return float(self.pb.props.volume)

    def set_muted(self, muted: bool) -> None:
        self.pb.props.mute = bool(muted)
        self.settings.set("muted", bool(muted))
        self.emit("changed")

    @property
    def muted(self) -> bool:
        return bool(self.pb.props.mute)

    def shutdown(self) -> None:
        self._save_episode_progress()
        self._cancel_retry()
        self._stop_progress_timer()
        self.pb.set_state(Gst.State.NULL)

    # ---- internals -----------------------------------------------------------------

    def _reset(self) -> None:
        self._cancel_retry()
        self._stop_progress_timer()
        self._network_retries = 0
        self._candidates_tried = 0
        self._pending_seek = None
        self._buffering = False

    def _start(self, uri: str, status: str = Status.CONNECTING) -> None:
        self.pb.set_state(Gst.State.NULL)
        # Drop anything the previous stream left queued (e.g. a late error), so it isn't
        # blamed on the new one.
        bus = self.pb.get_bus()
        bus.set_flushing(True)
        bus.set_flushing(False)
        self.pb.props.uri = uri
        self._set_status(status)
        self.pb.set_state(Gst.State.PLAYING)

    def _set_status(self, status: str) -> None:
        self.status = status
        if status == Status.PLAYING and self.kind == "episode":
            self._start_progress_timer()
        elif status != Status.PLAYING:
            self._stop_progress_timer()
        self.emit("changed")

    def _on_source_setup(self, _pb, source) -> None:
        props = {p.name for p in source.list_properties()}
        if "user-agent" in props:
            source.set_property("user-agent", net.USER_AGENT)
        if "timeout" in props:
            source.set_property("timeout", 20)
        if "iradio-mode" in props:
            source.set_property("iradio-mode", True)  # ask for song titles

    def _on_message(self, _bus, msg: Gst.Message) -> None:
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            self._on_error(err, debug)
        elif t == Gst.MessageType.EOS:
            self._on_eos()
        elif t == Gst.MessageType.TAG:
            self._on_tags(msg.parse_tag())
        elif t == Gst.MessageType.BUFFERING:
            self._on_buffering(msg.parse_buffering())
        elif t == Gst.MessageType.STATE_CHANGED and msg.src == self.pb:
            _old, new, _pending = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._buffering:
                self._on_playing()
        elif t == Gst.MessageType.ASYNC_DONE:
            if self._pending_seek is not None and self.kind == "episode":
                target, self._pending_seek = self._pending_seek, None
                self.pb.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                    int(target * Gst.SECOND))
                self._last_position = target
        elif t == Gst.MessageType.CLOCK_LOST:
            self.pb.set_state(Gst.State.PAUSED)
            self.pb.set_state(Gst.State.PLAYING)

    def _on_playing(self) -> None:
        self._network_retries = 0
        self._candidates_tried = 0
        if self.kind == "live" and self.station is not None:
            indexes = dict(self.settings.get("stream_index") or {})
            if indexes.get(self.station.id) != self._stream_index:
                indexes[self.station.id] = self._stream_index
                self.settings.set("stream_index", indexes)
        if self.status != Status.PLAYING:
            self._set_status(Status.PLAYING)

    def _on_buffering(self, percent: int) -> None:
        # Network streams: hold playback until the buffer refills, as GStreamer recommends.
        if self.status in (Status.STOPPED, Status.PAUSED, Status.ERROR):
            return
        if percent < 100 and not self._buffering:
            self._buffering = True
            self.pb.set_state(Gst.State.PAUSED)
            if self.status == Status.PLAYING:
                self._set_status(Status.CONNECTING)
        elif percent >= 100 and self._buffering:
            self._buffering = False
            self.pb.set_state(Gst.State.PLAYING)

    def _on_tags(self, tags: Gst.TagList) -> None:
        if self.kind != "live":
            return
        ok, raw = tags.get_string(Gst.TAG_TITLE)
        if not ok or not raw or not raw.strip():
            return
        raw = raw.strip()
        if " - " in raw:
            artist, title = (p.strip() for p in raw.split(" - ", 1))
        else:
            artist, title = "", raw
        if (title, artist) != (self.song_title, self.song_artist or ""):
            self.song_title, self.song_artist = title, artist or None
            self.emit("changed")

    def _on_eos(self) -> None:
        if self.kind == "episode":
            dur = self.duration()
            self.progress.mark_played(self.episode.key, dur)
            self._stop_progress_timer()
            self.pb.set_state(Gst.State.NULL)
            self._last_position = 0.0
            self._set_status(Status.STOPPED)
        elif self.kind == "live" and self.is_active:
            # A live stream "ends" when the server drops the connection: reconnect.
            self._schedule_retry(1.0)

    def _on_error(self, err: GLib.Error, debug: str | None) -> None:
        if os.environ.get("HPR_DEBUG"):  # run with HPR_DEBUG=1 to see playback errors
            print(f"[player] {self.kind} error {err.domain}:{err.code} {err.message} | {debug}", flush=True)
        if self.status in (Status.STOPPED, Status.PAUSED):
            return
        self.pb.set_state(Gst.State.NULL)
        # "This address isn't an audio stream" (404, HTML page, unknown format), as opposed
        # to "the network hiccuped".
        bad_source = (
            any(err.matches(Gst.StreamError.quark(), c) for c in _BAD_STREAM_CODES)
            or err.matches(Gst.ResourceError.quark(), Gst.ResourceError.NOT_FOUND)
            or err.matches(Gst.ResourceError.quark(), Gst.ResourceError.NOT_AUTHORIZED)
            or err.matches(Gst.CoreError.quark(), Gst.CoreError.MISSING_PLUGIN)  # e.g. an HTML page
        )

        if self.kind == "episode":
            if bad_source or self._network_retries >= self.EPISODE_RETRIES:
                self._set_status(Status.ERROR)
            else:
                self._network_retries += 1
                self._pending_seek = self._last_position
                self._schedule_retry(2.0 * self._network_retries)
            return

        station = self.station
        if station is None:
            self._set_status(Status.ERROR)
            return
        if bad_source or self._network_retries >= self.NETWORK_RETRIES_PER_URL:
            self._candidates_tried += 1
            if self._candidates_tried >= len(station.streams):
                self._candidates_tried = 0
                if bad_source:
                    # Every address answered with something that isn't audio.
                    self._set_status(Status.ERROR)
                    return
            self._stream_index = (self._stream_index + 1) % len(station.streams)
            self._network_retries = 0
            self._schedule_retry(0.5)
        else:
            delay = min(2.0 * (2 ** min(self._network_retries, 4)), 30.0)
            self._network_retries += 1
            self._schedule_retry(delay)

    def _schedule_retry(self, delay: float) -> None:
        self._cancel_retry()
        self._buffering = False
        self.status = Status.RECONNECTING
        self.emit("changed")
        self._retry_source = GLib.timeout_add(int(delay * 1000), self._retry)

    def _retry(self) -> bool:
        self._retry_source = 0
        if self.kind == "episode" and self.episode is not None:
            self._start(self.episode.audio_url, Status.RECONNECTING)
        elif self.kind == "live" and self.station is not None:
            self._start(self.station.streams[self._stream_index], Status.RECONNECTING)
        return False

    def _cancel_retry(self) -> None:
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0

    # ---- episode progress ----------------------------------------------------------

    def _start_progress_timer(self) -> None:
        if not self._progress_source:
            self._progress_source = GLib.timeout_add_seconds(10, self._progress_tick)

    def _stop_progress_timer(self) -> None:
        if self._progress_source:
            GLib.source_remove(self._progress_source)
            self._progress_source = 0

    def _progress_tick(self) -> bool:
        self._save_episode_progress()
        return True

    def _save_episode_progress(self) -> None:
        if self.kind != "episode" or self.episode is None:
            return
        pos = self.position()
        if pos > 0:
            self.progress.save(self.episode.key, pos, self.duration())
