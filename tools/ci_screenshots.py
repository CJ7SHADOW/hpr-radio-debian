#!/usr/bin/env python3
"""Drives the installed app against the real HPR services and takes screenshots.
Used by the GitHub workflow (inside xvfb-run + dbus-run-session); also handy locally.

    python3 tools/ci_screenshots.py OUTPUT_DIR
"""
import os
import subprocess
import sys

sys.path.insert(0, "/usr/share/hpr-radio")
os.environ.setdefault("HPR_AUDIO_SINK", "fakesink")
OUT = sys.argv[1] if len(sys.argv) > 1 else "screenshots"
os.makedirs(OUT, exist_ok=True)

from gi.repository import GLib  # noqa: E402

from hpr_radio import episodes  # noqa: E402
from hpr_radio.app import HprApplication  # noqa: E402

app = HprApplication()
t = 0


def at(delay_ms, fn):
    global t
    t += delay_ms
    GLib.timeout_add(t, lambda: (fn(), False)[1])


def shot(name):
    p = app.hub.player
    print(f"[{name}] catalog={app.hub.catalog.source} status={p.status} "
          f"song={p.song_artist!r} - {p.song_title!r} art={app.hub.now_art}", flush=True)
    subprocess.run(["import", "-window", "root", os.path.join(OUT, f"{name}.png")], check=False)


def report():
    hub = app.hub
    for st in hub.catalog.available:
        tracks = hub.recent.get(st.id)
        print(f"recent {st.id}: {len(tracks) if tracks else tracks} "
              f"{(tracks[0].artist + ' - ' + tracks[0].title) if tracks else ''}", flush=True)
    for show in hub.catalog.shows:
        for src in show.sources:
            eps = episodes.cached(src)
            print(f"episodes {show.id}/{src.label}: {len(eps) if eps else eps} "
                  f"{eps[0].title + ' ' + eps[0].audio_url if eps else ''}", flush=True)


def script():
    w, hub = app.window, app.hub
    at(8000, lambda: shot("1-home"))
    at(500, lambda: hub.player.play_station(hub.catalog.available[0]))
    at(15000, lambda: shot("2-home-playing"))
    at(500, lambda: w.open_channel(hub.player.station or hub.catalog.available[0]))
    at(6000, lambda: shot("3-channel"))
    at(500, lambda: w.go_back())
    if hub.catalog.shows:
        at(500, lambda: w.open_show(hub.catalog.shows[0]))
        at(12000, lambda: shot("4-show"))

        def play_first():
            show = hub.catalog.shows[0]
            eps = episodes.cached(show.recent_source)
            if eps:
                hub.player.play_episode(show, eps[0])
                GLib.timeout_add(1000, lambda: (w.open_episode_player(), False)[1])
        at(500, play_first)
        at(15000, lambda: shot("5-episode"))
    at(500, report)
    at(500, app.quit)
    return False


app.connect("activate", lambda *_: GLib.timeout_add(100, script))
sys.exit(app.run([]))
