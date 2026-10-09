# HPR Radio for Debian

A desktop player for **Heartland Public Radio** (Classic Country and Bluegrass Gospel, commercial-free),
built with GTK 4, libadwaita and GStreamer. It's the Linux companion to the Android app and reads the
same `channels.json`.

- **Live channels** with the song on the air, album art, and the last 15 songs played
- **Keeps itself connected.** It reconnects when the stream or your network drops, and fails over to the
  next stream address if one stops working
- **Good 'N Country on demand**: recent episodes and the archive, seek bar, 15 s back / 30 s forward,
  resume where you stopped, playlist PDFs
- **Media keys and the desktop's sound menu** (MPRIS): Play/Pause, Stop, Next/Previous. For live radio,
  Next/Previous switch channels; in an episode they skip forward 30 s / back 15 s
- Keeps the computer from sleeping while something's playing
- Light and dark mode follow your desktop

## Install

Download **hpr-radio-deb-bookworm** (Debian 12) or **hpr-radio-deb-trixie** (Debian 13) from the latest
run on the repo's **Actions** tab, unzip it, then:

```bash
sudo apt install ./hpr-radio_1.0.0_all.deb
```

`apt` pulls in GTK 4, libadwaita and GStreamer if they aren't already there. Then start **HPR Radio**
from your applications menu, or run `hpr-radio`.

It also works on Ubuntu 24.04 and newer, and on Raspberry Pi OS (Bookworm), since it's architecture-independent.

## The channel list

The app downloads `channels.json` every time it starts, from `https://hpr.org/apps/android/channels.json`
by default: the same file the Android app uses. If that can't be reached, it uses the last copy it
downloaded, and failing that, the copy built into the package. The bottom of the home screen shows which
one is in use.

To point it somewhere else, for testing say, either:

- run it once with `hpr-radio --config-url https://example.com/channels.json`, or
- put this in `~/.config/hpr-radio/settings.json`:
  ```json
  { "config_url": "https://example.com/channels.json" }
  ```

## Where it keeps things

| | |
|---|---|
| `~/.config/hpr-radio/settings.json` | settings: volume, last channel, working stream per channel |
| `~/.local/share/hpr-radio/progress.json` | where you stopped in each episode |
| `~/.cache/hpr-radio/` | saved channel list, album art, art lookups (safe to delete) |

## Troubleshooting

Run it from a terminal with `HPR_DEBUG=1 hpr-radio` to see playback errors.

If the play buttons show broken-image icons, install `librsvg2-common` (the package depends on it, so this
only matters when running from source).

## Run from source

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 \
  gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good librsvg2-common
python3 -m hpr_radio
python3 -m unittest discover -s tests      # tests
```

`tools/fake_stream.py` is a pretend Shoutcast server (song titles, dropped connections, a seekable
episode file, a song-history feed) for testing without the real streams.

## Build the package yourself

```bash
sudo apt install debhelper dpkg-dev
dpkg-buildpackage -us -uc -b      # the .deb lands in the parent folder
```

## Project layout

```
hpr_radio/
  app.py        application: startup, styles, menu, sleep inhibit
  hub.py        shared state the screens read: catalog, player, song histories, cover art
  catalog.py    channels.json: download, check, save
  player.py     GStreamer playback: reconnect, failover, song titles, episodes with resume
  mpris.py      media keys and sound-menu control
  recent.py     recently played songs (Centova Cast)
  artwork.py    album-art lookup (iTunes, then Deezer)
  episodes.py   episode lists from hpr.org pages, listening progress
  images.py     image download and cache
  ui/           home, channel page, show page, episode player, now-playing bar
data/           built-in channels.json, styles, icons, desktop entry
debian/         packaging
```

Unofficial. Programming, streams and the HPR logo belong to Heartland Public Radio, Inc.
