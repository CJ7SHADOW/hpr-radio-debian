import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Keep tests away from the real ~/.cache and ~/.local
_tmp = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = _tmp
os.environ["XDG_DATA_HOME"] = _tmp
os.environ["XDG_CONFIG_HOME"] = _tmp

from hpr_radio import artwork, catalog, episodes, recent  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "channels.json")


class CatalogTests(unittest.TestCase):
    def test_builtin_parses(self):
        with open(DATA, encoding="utf-8") as f:
            c = catalog.parse(f.read())
        self.assertIsNotNone(c)
        ids = [s.id for s in c.stations]
        self.assertIn("hpr1", ids)
        self.assertNotIn("hpr3", ids)  # disabled in the built-in copy
        self.assertTrue(all(s.available for s in c.stations))
        self.assertEqual(c.shows[0].id, "goodncountry")
        self.assertIsNotNone(c.shows[0].archive_source)

    def test_rejects_nothing_playable(self):
        self.assertIsNone(catalog.parse(json.dumps({"channels": [{"id": "x", "streams": []}]})))
        self.assertIsNone(catalog.parse("{not json"))
        self.assertIsNone(catalog.parse("[]"))

    def test_null_and_duplicates(self):
        c = catalog.parse(json.dumps({"channels": [
            {"id": "a", "streams": ["https://x/1", None, "ftp://nope"], "recentTracksUrl": None, "note": None},
            {"id": "a", "streams": ["https://x/2"]},
            {"id": "b", "enabled": True, "streams": []},
        ]}))
        self.assertEqual([s.id for s in c.stations], ["a", "b"])
        self.assertEqual(c.stations[0].streams, ("https://x/1",))
        self.assertIsNone(c.stations[0].recent_tracks_url)
        self.assertFalse(c.stations[1].available)


class RecentTests(unittest.TestCase):
    SAMPLE = ('{"type":"result","rid":"hpr1hi","data":[[{"artist":"Buck Owens","title":"Your Tender Loving Care",'
              '"time":1791054005},{"artist":"Loretta Lynn &amp; Conway Twitty","title":"Lead Me On","time":1791053833}]]}')

    def test_parse(self):
        t = recent.parse(self.SAMPLE)
        self.assertEqual(len(t), 2)
        self.assertEqual(t[0].artist, "Buck Owens")
        self.assertEqual(t[1].artist, "Loretta Lynn & Conway Twitty")
        self.assertEqual(t[0].played_at, 1791054005)

    def test_merge_keeps_history(self):
        a = recent.parse(self.SAMPLE)
        b = [recent.Track("Hank Snow", "I've Been Everywhere", 1791054200)]
        m = recent.merge(a, b)
        self.assertEqual([t.artist for t in m], ["Hank Snow", "Buck Owens", "Loretta Lynn & Conway Twitty"])


class EpisodeTests(unittest.TestCase):
    PAGE = """<h2><strong>Recent On-Demand Episodes:</strong></h2>
<p>(<em><a href="https://hpr.org/shows/goodncountry/archives/">Archived Episodes 2020 &#8211; 2025</a></em>)</p>
<p><strong><br />
Saturday, October 3, 2026 </strong><a href="/hprplaylists/goodncountry/20261003.pdf">playlist</a></p>
<!--[if lt IE 9]><script>document.createElement('audio');</script><![endif]-->
<audio class="wp-audio-shortcode" id="audio-225-1" preload="none" controls="controls"><source type="audio/mpeg" src="https://archive.org/download/good-n-country-10-3-2026/Good%20%27N%20Country%2010-3-2026.mp3?_=1" /><a href="x.mp3">x</a></audio>
<p><strong><br />
Saturday, September 12, 2026 &#8211; Dolly Parton tribute&nbsp;</strong><a href="/hprplaylists/goodncountry/20260912.pdf">playlist</a></p>
<audio class="wp-audio-shortcode"><source type="audio/mpeg" src="https://ia800508.us.archive.org/19/items/good-n-country-9-12-2026/GNC.mp3?_=2" /></audio>
<audio class="wp-audio-shortcode"><source type="audio/mpeg" src="https://archive.org/download/good-n-country-10-3-2026/dupe.mp3" /></audio>"""

    def test_parse(self):
        eps = episodes.parse(self.PAGE, from_archive=False)
        self.assertEqual(len(eps), 2)
        a, b = eps
        self.assertEqual(a.key, "good-n-country-10-3-2026")
        self.assertEqual(a.title, "Saturday, October 3, 2026")
        self.assertIsNone(a.note)
        self.assertEqual(a.date_key, 20261003)
        self.assertEqual(a.playlist_url, "https://hpr.org/hprplaylists/goodncountry/20261003.pdf")
        self.assertTrue(a.audio_url.endswith("10-3-2026.mp3"))
        self.assertEqual(b.note, "Dolly Parton tribute")
        self.assertEqual(b.audio_url, "https://archive.org/download/good-n-country-9-12-2026/GNC.mp3")

    def test_progress(self):
        store = episodes.ProgressStore()
        store.save("ep1", 10, 7200)          # too early to remember
        self.assertIsNone(store.get("ep1"))
        store.save("ep1", 600, 7200)
        self.assertEqual(store.resume_position("ep1"), 600)
        store.set_last("goodncountry", "ep1")
        self.assertEqual(store.last, ("goodncountry", "ep1"))
        store.save("ep1", 7150, 7200)        # near the end = played
        self.assertTrue(store.get("ep1").done)
        self.assertEqual(store.resume_position("ep1"), 0)
        self.assertIsNone(store.last)


class ArtworkTests(unittest.TestCase):
    def test_artist_matching(self):
        self.assertTrue(artwork.artist_matches("Loretta Lynn & Conway Twitty", "Conway Twitty & Loretta Lynn"))
        self.assertTrue(artwork.artist_matches("The Statler Brothers", "Statler Brothers"))
        self.assertTrue(artwork.artist_matches("Flatt & Scruggs", "Lester Flatt"))
        self.assertFalse(artwork.artist_matches("Buck Owens", "Hank Snow"))


if __name__ == "__main__":
    unittest.main()
