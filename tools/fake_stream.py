#!/usr/bin/env python3
"""A pretend HPR stream server for testing without the real one.

    python3 tools/fake_stream.py some.mp3 [port]

  /live            Shoutcast-style stream (loops the MP3) with song titles every 10 s
  /live?drop=15    same, but hangs up after 15 seconds (tests reconnecting)
  /404             not found
  /html            an HTML page instead of audio
  /episode.mp3     the MP3 as a normal file, with seeking (Range) support
  /recent          Centova-style recently played JSON
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MP3 = open(sys.argv[1], "rb").read()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
SONGS = [("Buck Owens", "Act Naturally"), ("Patsy Cline", "Walkin' After Midnight"),
         ("Marty Robbins", "El Paso"), ("Hank Snow", "I've Been Everywhere")]
METAINT = 8192
BYTES_PER_SEC = 8000  # 64 kbit/s


def song_now():
    return SONGS[int(time.time() // 10) % len(SONGS)]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/live":
            return self.live(float(q.get("drop", ["0"])[0]))
        if u.path == "/episode.mp3":
            return self.file()
        if u.path == "/html":
            return self.send(200, "text/html", b"<html><body>Not a stream</body></html>")
        if u.path == "/recent":
            now = int(time.time())
            items = [{"artist": a, "title": t, "time": now - i * 180} for i, (a, t) in enumerate(SONGS * 4)]
            return self.send(200, "application/json", json.dumps({"type": "result", "data": [items]}).encode())
        return self.send(404, "text/plain", b"not found")

    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def live(self, drop):
        icy = self.headers.get("Icy-MetaData") == "1"
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("icy-name", "Fake HPR")
        if icy:
            self.send_header("icy-metaint", str(METAINT))
        self.send_header("Connection", "close")
        self.end_headers()
        start, pos, since_meta = time.time(), 0, 0
        try:
            while True:
                if drop and time.time() - start > drop:
                    return
                chunk = MP3[pos:pos + 1024] or MP3[:1024]
                pos = (pos + 1024) % len(MP3)
                while chunk:
                    room = METAINT - since_meta if icy else len(chunk)
                    part, chunk = chunk[:room], chunk[room:]
                    self.wfile.write(part)
                    since_meta += len(part)
                    if icy and since_meta == METAINT:
                        a, t = song_now()
                        meta = f"StreamTitle='{a} - {t}';".encode()
                        meta += b"\0" * (-len(meta) % 16)
                        self.wfile.write(bytes([len(meta) // 16]) + meta)
                        since_meta = 0
                time.sleep(1024 / BYTES_PER_SEC)
        except (BrokenPipeError, ConnectionResetError):
            return

    def file(self):
        rng = self.headers.get("Range")
        start, end = 0, len(MP3) - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a or 0)
            end = int(b) if b else end
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(MP3)}")
        else:
            self.send_response(200)
        body = MP3[start:end + 1]
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
