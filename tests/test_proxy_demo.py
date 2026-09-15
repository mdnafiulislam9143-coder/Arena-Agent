# -*- coding: utf-8 -*-
"""
এন্ড-টু-এন্ড টেস্ট: proxy.py --demo চালু করে HTTP লেভেলে যাচাই (নেটওয়ার্ক/internet লাগে না)।
যা টেস্ট করা হয়: হেলথ, সার্চ, player পেলোড, Range/206 প্রোক্সি, static অ্যাসেট, 404 হ্যান্ডলিং।
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(ROOT, "server", "proxy.py")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DemoProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.port = free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.proc = subprocess.Popen(
            [sys.executable, PROXY, "--port", str(cls.port), "--demo", "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(cls.base + "/api/health", timeout=1).read()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        cls.proc.kill()
        raise RuntimeError("ডেমো প্রোক্সি চালু হয়নি")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # ------------------------------------------------------------------ হেল্পার
    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            return urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            return exc

    def json_of(self, path):
        with self.get(path) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -------------------------------------------------------------------- টেস্ট
    def test_health_reports_demo_and_clients(self) -> None:
        data = self.json_of("/api/health")
        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "demo")
        self.assertIn("ANDROID_VR", data["clients"])
        self.assertEqual(data["max_height"], 360)

    def test_search_demo_returns_items(self) -> None:
        data = self.json_of("/api/search?q=" + urllib.parse.quote("বাংলা"))
        self.assertTrue(data["items"])
        self.assertTrue(all("id" in it and "title" in it for it in data["items"]))

    def test_player_never_leaks_upstream_url(self) -> None:
        data = self.json_of("/api/player?id=dQw4w9WgXcQ")
        self.assertTrue(data["ok"])
        self.assertTrue(data["stream"]["url"].startswith("/api/stream/"))
        self.assertNotIn("googlevideo", json.dumps(data))       # IP-lock ফাঁদ এড়ানো
        self.assertEqual(data["stream"]["mode"], "progressive")
        self.assertEqual(data["stream"]["itag"], 18)
        self.assertTrue(data["stream"]["kaios_safe"])

    def test_player_accepts_full_url(self) -> None:
        data = self.json_of("/api/player?id=" + urllib.request.quote("https://youtu.be/dQw4w9WgXcQ"))
        self.assertEqual(data["video"]["id"], "dQw4w9WgXcQ")

    def test_height_override(self) -> None:
        data = self.json_of("/api/player?id=dQw4w9WgXcQ&h=480")
        self.assertTrue(data["ok"])
        self.assertLessEqual(data["stream"]["height"], 480)

    def test_stream_full_and_range(self) -> None:
        with self.get("/api/stream/dQw4w9WgXcQ") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
            total = int(resp.headers["Content-Length"])
            self.assertEqual(total, 512 * 1024)

        with self.get("/api/stream/dQw4w9WgXcQ", {"Range": "bytes=0-1023"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers.get("Content-Range"), "bytes 0-1023/524288")
            self.assertEqual(len(resp.read()), 1024)

        # ফাইলের শেষ থেকে suffix range (seek-এর সময় প্লেয়ার এটা চায়)
        with self.get("/api/stream/dQw4w9WgXcQ", {"Range": "bytes=-2048"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers.get("Content-Range"), "bytes 522240-524287/524288")
            self.assertEqual(len(resp.read()), 2048)

        # সীমার বাইরের রেঞ্জ → 416 (KaiOS-এর প্লেয়ার এতে থেমে যায় না)
        with self.get("/api/stream/dQw4w9WgXcQ", {"Range": "bytes=524288-"}) as resp:
            self.assertEqual(resp.status, 416)
            self.assertEqual(resp.headers.get("Content-Range"), "bytes */524288")

    def test_static_webapp_assets(self) -> None:
        for path, needle in (("/", b"KaiOS Tube"),
                             ("/app.js", b"keydown"),
                             ("/manifest.webapp", b"KaiOS Tube"),
                             ("/icons/icon-56.png", b"\x89PNG")):
            with self.get(path) as resp:
                self.assertEqual(resp.status, 200, path)
                self.assertIn(needle, resp.read(), path)

    def test_missing_file_404(self) -> None:
        with self.get("/nope.txt") as resp:
            self.assertEqual(resp.status, 404)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertFalse(body["ok"])

    def test_bad_video_id_400(self) -> None:
        with self.get("/api/player?id=!!!") as resp:
            self.assertEqual(resp.status, 400)

    def test_transcript_demo(self) -> None:
        data = self.json_of("/api/transcript?id=dQw4w9WgXcQ&lang=bn")
        self.assertTrue(data["lines"])
        self.assertEqual(data["lang"], "bn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
