# -*- coding: utf-8 -*-
"""
এন্ড-টু-এন্ড টেস্ট: proxy.py --demo চালু করে HTTP লেভেলে যাচাই (নেটওয়ার্ক/internet লাগে না)।
যা টেস্ট করা হয়: হেলথ, সার্চ, player পেলোড, Range/206 প্রোক্সি, static অ্যাসেট, 404 হ্যান্ডলিং।
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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
        cls.root = tempfile.mkdtemp(prefix="kaitube-proxy-")
        env = dict(os.environ)
        env.update({
            "DOWNLOAD_DIR": os.path.join(cls.root, "downloads"),
            "TEMP_DIR": os.path.join(cls.root, "tmp"),
            "STATE_DIR": os.path.join(cls.root, "state"),
            "DEMO_SIZE": "32768",          # টেস্ট দ্রুত রাখতে ছোট ফাইল
            "DEMO_DELAY": "0.01",
            "MAX_CONCURRENT_DOWNLOADS": "2",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, PROXY, "--port", str(cls.port), "--demo", "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
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
        shutil.rmtree(cls.root, ignore_errors=True)

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

    def post(self, path, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            return exc

    def wait_for(self, predicate, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.15)
        return False

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
        # আলাদা আইডি: অন্য টেস্ট ডাউনলোড করে রাখলে সেটাই সার্ভ হবে (স্থানীয় ফাইল অগ্রাধিকার পায়)
        stream_id = "streamonly1"
        with self.get("/api/stream/" + stream_id) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
            total = int(resp.headers["Content-Length"])
            self.assertEqual(total, 512 * 1024)

        with self.get("/api/stream/" + stream_id, {"Range": "bytes=0-1023"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers.get("Content-Range"), "bytes 0-1023/524288")
            self.assertEqual(len(resp.read()), 1024)

        # ফাইলের শেষ থেকে suffix range (seek-এর সময় প্লেয়ার এটা চায়)
        with self.get("/api/stream/" + stream_id, {"Range": "bytes=-2048"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers.get("Content-Range"), "bytes 522240-524287/524288")
            self.assertEqual(len(resp.read()), 2048)

        # সীমার বাইরের রেঞ্জ → 416 (KaiOS-এর প্লেয়ার এতে থেমে যায় না)
        with self.get("/api/stream/" + stream_id, {"Range": "bytes=524288-"}) as resp:
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

    # ------------------------------------------------- ডাউনলোড কিউ (MeTube মডেল)
    def test_download_queue_full_flow(self) -> None:
        """add → progress → finished → ফাইল Range-সহ সার্ভ → completed থেকে সরানো।"""
        with self.post("/api/downloads", {"url": "https://youtu.be/dQw4w9WgXcQ", "height": 360}) as resp:
            self.assertEqual(resp.status, 200)
            item = json.loads(resp.read().decode("utf-8"))["item"]
        self.assertEqual(item["id"], "dQw4w9WgXcQ")
        self.assertEqual(item["status"], "queueing")

        def finished():
            data = self.json_of("/api/downloads")
            return any(i["id"] == "dQw4w9WgXcQ" and i["status"] == "finished" for i in data["completed"])

        self.assertTrue(self.wait_for(finished), "ডাউনলোড শেষ হয়নি")
        data = self.json_of("/api/downloads")
        self.assertEqual(data["stats"]["finished"], 1)
        completed = [i for i in data["completed"] if i["id"] == "dQw4w9WgXcQ"][0]
        self.assertTrue(completed["playable"])

        # সম্পন্ন ফাইল Range-সহ
        with self.get("/api/file/dQw4w9WgXcQ") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
            self.assertEqual(int(resp.headers["Content-Length"]), 32768)
        with self.get("/api/file/dQw4w9WgXcQ", {"Range": "bytes=0-1023"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers.get("Content-Range").split("/")[1], "32768")
            self.assertEqual(len(resp.read()), 1024)

        # /api/stream এখন IP-lock-এর বদলে স্থানীয় ফাইল দেয়
        with self.get("/api/stream/dQw4w9WgXcQ", {"Range": "bytes=0-511"}) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(resp.read()), 512)

        with self.post("/api/downloads/delete", {"ids": ["dQw4w9WgXcQ"], "where": "done"}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(json.loads(resp.read().decode("utf-8"))["ok"])
        self.assertEqual(self.json_of("/api/downloads")["completed"], [])
        with self.get("/api/file/dQw4w9WgXcQ") as resp:
            self.assertEqual(resp.status, 404)

    def test_download_queue_extra(self) -> None:
        data = self.json_of("/api/downloads")
        for key in ("queueing", "downloading", "finished", "max_concurrent"):
            self.assertIn(key, data["stats"])
        with self.post("/api/downloads/start", {}) as resp:
            self.assertEqual(resp.status, 200)
        with self.post("/api/downloads/delete", {"ids": ["nope"], "where": "queue"}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertFalse(json.loads(resp.read().decode("utf-8"))["ok"])

    def test_downloads_ssrf_is_blocked(self) -> None:
        """ক্লাউড মেটাডেটা/লোকালহোস্ট URL কিউতে ঢুকতেই পারবে না (url_guard)।"""
        for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/api/health",
                    "http://[::1]/", "http://localhost/", "file:///etc/passwd"):
            with self.post("/api/downloads", {"url": url}) as resp:
                self.assertEqual(resp.status, 400, url)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertIn("প্রত্যাখ্যাত", body["error"])

    def test_player_url_guard(self) -> None:
        with self.get("/api/player?id=" + urllib.request.quote("http://169.254.169.254/")) as resp:
            self.assertEqual(resp.status, 400)

    def test_downloads_bad_requests(self) -> None:
        with self.post("/api/downloads", {}) as resp:
            self.assertEqual(resp.status, 400)
        with self.post("/api/downloads", {"url": "কিছু এলোমেলো"}) as resp:
            self.assertEqual(resp.status, 400)
        with self.post("/api/downloads/delete", {"ids": ["x"], "where": "wrong"}) as resp:
            self.assertEqual(resp.status, 400)
        with self.post("/api/unknown", {}) as resp:
            self.assertEqual(resp.status, 404)

    def test_file_path_traversal_blocked(self) -> None:
        for path in ("/api/file/..%2F..%2Fetc%2Fpasswd", "/api/file/%2e%2e%2fsecret", "/api/file/nope"):
            with self.get(path) as resp:
                self.assertEqual(resp.status, 404, path)

    def test_health_reports_queue_and_guard(self) -> None:
        data = self.json_of("/api/health")
        self.assertIn("downloads", data)
        self.assertEqual(data["url_guard"]["allow_private"], False)
        self.assertEqual(data["max_concurrent"], 2)
        self.assertIsNotNone(data["download_dir"])

    def test_transcript_demo(self) -> None:
        data = self.json_of("/api/transcript?id=dQw4w9WgXcQ&lang=bn")
        self.assertTrue(data["lines"])
        self.assertEqual(data["lang"], "bn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
