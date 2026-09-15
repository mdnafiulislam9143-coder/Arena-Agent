#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KaiOS Tube প্রোক্সি — Innertube + yt-dlp + Range-প্রোক্সি (stdlib-only)।

চালান:
    python3 server/proxy.py --port 8080 --demo      # অফলাইন ডেমো: নেটওয়ার্ক ছাড়াই UI টেস্ট
    python3 server/proxy.py --port 8080             # লাইভ: Innertube → প্রয়োজনে yt-dlp
    python3 server/proxy.py --port 8080 --ytdlp force --max-height 360

কেন প্রোক্সি (এবং কেন googlevideo URL ফোনে পাঠাই না):
  googlevideo.com-এর URL যে IP রিকোয়েস্ট করেছে তার সাথে বাঁধা থাকে (IP-lock)।
  সার্ভার URL বের করে ফোনে পাঠালে ফোন থেকে 403 আসবে। তাই বাইট আমাদের সার্ভার দিয়েই যায়,
  আর Range/206 ঠিকঠাক পাস-থ্রু করা হয় — নইলে KaiOS প্লেয়ার seek করতে গিয়ে আটকে যায়।
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import formats  # noqa: E402
import innertube  # noqa: E402
import clients  # noqa: E402

try:
    import ytdlp_bridge  # noqa: E402
except Exception:  # noqa: BLE001
    ytdlp_bridge = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
CHUNK = 64 * 1024
META_TTL = 1800          # ৩০ মিনিট — মেটাডেটা ক্যাশ (ডিভাইসের ব্যান্ডউইথ বাঁচায়)
_META: Dict[str, Tuple[float, Dict[str, Any]]] = {}

ARGS = argparse.Namespace()


# --------------------------------------------------------------------------- helpers

def meta_cached(video_id: str) -> Optional[Dict[str, Any]]:
    hit = _META.get(video_id)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def meta_store(video_id: str, payload: Dict[str, Any]) -> None:
    _META[video_id] = (time.time() + META_TTL, payload)


def video_id_of(value: str) -> str:
    """URL বা খালি ID — দুটোই গ্রহণ করে (ফোন থেকে পেস্ট করা লিংক কাজ করবে)।"""
    value = (value or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", value)
    if m:
        return m.group(1)
    m = re.fullmatch(r"[A-Za-z0-9_-]{11}", value)
    return m.group(0) if m else ""


def demo_bytes(video_id: str, start: int, end: int) -> bytes:
    """
    অফলাইন মোড: ডিটারমিনিস্টিক pseudo-ডেটা — ডাউনলোড/সেভ/seek ফ্লো টেস্ট করার জন্য।
    (আসল প্লেব্যাকের জন্য লাইভ মোড লাগবে।)
    """
    size = max(0, end - start + 1)
    seed = sum(ord(c) for c in video_id) or 7
    buf = bytearray()
    i = start
    while len(buf) < size:
        buf += bytes(((seed + i * 31) % 251, (seed * 7 + i) % 253, (i % 255), 0x4B))
        i += 4
    return bytes(buf[:size])


# --------------------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KaiosTubeProxy/1.0"

    # ---- ছোট ইউটিলিটি -------------------------------------------------------
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range,Content-Type")

    def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, message: str, status: int = 500, hint: Optional[str] = None) -> None:
        self._json({"ok": False, "error": message, "hint": hint}, status)

    def _static(self, rel: str) -> None:
        safe = os.path.normpath("/" + rel).lstrip("/")
        path = os.path.join(WEBAPP_DIR, safe)
        if not path.startswith(WEBAPP_DIR) or not os.path.isfile(path):
            self._error("ফাইল পাওয়া যায়নি: %s" % rel, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".webapp") or path.endswith(".webmanifest"):
            ctype = "application/x-web-app-manifest+json"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---- রাউটিং -------------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                return self._static("index.html")
            if route == "/api/health":
                return self._health()
            if route == "/api/search":
                return self._search(query)
            if route == "/api/player":
                return self._player(query)
            if route == "/api/resolve":
                return self._resolve(query)
            if route == "/api/transcript":
                return self._transcript(query)
            if route.startswith("/api/stream/"):
                return self._stream(route.rsplit("/", 1)[-1], query)
            return self._static(route.lstrip("/"))
        except BrokenPipeError:
            return                                    # ফোন থামিয়ে দিয়েছে — স্বাভাবিক
        except Exception as exc:  # noqa: BLE001
            return self._error("অভ্যন্তরীণ ত্রুটি: %s" % exc, 500,
                               "server/proxy.py-এর লগ দেখুন")

    # ---- এন্ডপয়েন্ট ---------------------------------------------------------
    def _height(self, query: Dict[str, list]) -> int:
        """প্রতি-রিকোয়েস্ট রেজোলিউশন (?h=240|360|480) — KaiOS-এ ইউজার বদলাতে পারে।"""
        try:
            value = int((query.get("h") or [ARGS.max_height])[0])
        except (TypeError, ValueError):
            return ARGS.max_height
        return max(144, min(1080, value))

    def _health(self) -> None:
        yt = ytdlp_bridge.status() if ytdlp_bridge else {"ready": False, "note": "ব্রিজ নেই"}
        self._json({
            "ok": True,
            "mode": "demo" if ARGS.demo else "live",
            "max_height": ARGS.max_height,
            "ytdlp_mode": ARGS.ytdlp,
            "ytdlp": yt,
            "clients": [c["name"] for c in clients.load_chain()],
            "allow_direct": bool(ARGS.allow_direct),
        })

    def _search(self, query: Dict[str, list]) -> None:
        q = (query.get("q") or [""])[0].strip()
        if not q:
            return self._error("q প্যারামিটার দিন (যেমন /api/search?q=গান)", 400)
        if ARGS.demo:
            return self._json({"ok": True, "demo": True, "items": demo_search_items(q)})
        try:
            items = innertube.search(q, hl=ARGS.hl, gl=ARGS.gl)
        except innertube.InnertubeError as exc:
            return self._error("সার্চ ব্যর্থ: %s" % exc, 502,
                               "ইন্টারনেট/locale দেখুন, অথবা --demo চালান")
        self._json({"ok": True, "items": items})

    def _player(self, query: Dict[str, list]) -> None:
        vid = video_id_of((query.get("id") or [""])[0])
        if not vid:
            return self._error("বৈধ video id/URL দিন (?id=…)", 400)

        cached = meta_cached(vid)
        if cached:
            return self._json(cached)

        if ARGS.demo:
            resp = innertube.demo_player_response(vid, height=self._height(query))
            client_name = "DEMO"
        else:
            try:
                resp, client = innertube.player(vid, hl=ARGS.hl, gl=ARGS.gl)
                client_name = client["name"]
            except innertube.InnertubeError as exc:
                return self._error("player ব্যর্থ: %s" % exc, 502,
                                   "--ytdlp force দিন (yt-dlp ইনস্টল থাকলে) বা --demo ব্যবহার করুন")

        details = resp.get("videoDetails") or {}
        streaming = resp.get("streamingData") or {}
        pick = formats.pick_for_kaios(streaming, max_height=self._height(query))

        payload = {
            "ok": True,
            "client": client_name,
            "video": {
                "id": vid,
                "title": details.get("title") or "শিরোনাম নেই",
                "author": details.get("author") or "",
                "duration": int(details.get("lengthSeconds") or 0),
                "description": (details.get("shortDescription") or "")[:280],
                "thumb": best_thumb(details),
            },
            "playability": (resp.get("playabilityStatus") or {}).get("status"),
            # ফোন কখনোই raw googlevideo URL পায় না — শুধু আমাদের প্রোক্সি পাথ
            "stream": {
                "url": "/api/stream/%s" % vid,
                "mode": pick["mode"],
                "height": pick["height"],
                "mime": pick["mime"],
                "itag": (pick["format"] or {}).get("itag"),
                "kaios_safe": bool(pick["mode"] == "progressive"),
                "reason": pick["reason"],
            },
            "direct_url": raw_url(pick) if ARGS.allow_direct else None,
        }
        meta_store(vid, payload)
        self._json(payload)

    def _resolve(self, query: Dict[str, list]) -> None:
        """ডায়াগনস্টিক: সব ক্লায়েন্ট + ফরম্যাট টেবিল (ডিভাইসে নয়, ব্রাউজারে দেখার জন্য)।"""
        vid = video_id_of((query.get("id") or [""])[0])
        if not vid:
            return self._error("?id= দিন", 400)
        if ARGS.demo:
            return self._json({"ok": True, "demo": True,
                               "streamingData": innertube.demo_player_response(vid)["streamingData"]})
        try:
            resp, client = innertube.player(vid, hl=ARGS.hl, gl=ARGS.gl)
        except innertube.InnertubeError as exc:
            return self._error("resolve ব্যর্থ: %s" % exc, 502)
        streaming = resp.get("streamingData") or {}
        return self._json({
            "ok": True, "client": client["name"],
            "formats": [{k: f.get(k) for k in ("itag", "mimeType", "bitrate", "height", "qualityLabel")}
                        for f in (streaming.get("formats") or [])],
            "adaptiveFormats": [{k: f.get(k) for k in ("itag", "mimeType", "bitrate", "height", "qualityLabel")}
                                for f in (streaming.get("adaptiveFormats") or [])],
        })

    def _transcript(self, query: Dict[str, list]) -> None:
        """সাবটাইটেল ডেমো/লাইভ — টেক্সট, তাই KaiOS-এ সহজে দেখানো যায় (ভিডিও ডিকোডার লাগে না)।"""
        lang = (query.get("lang") or ["bn"])[0]
        if ARGS.demo:
            return self._json({"ok": True, "lang": lang, "lines": [
                {"t": 0, "text": "এটি অফলাইন ডেমো সাবটাইটেল।"},
                {"t": 5, "text": "Innertube API থেকে আসল ট্র্যাক আনা যায় /api/transcript দিয়ে।"},
            ]})
        return self._error("লাইভ transcript এখনো যুক্ত হয়নি", 501,
                           "yt-dlp --write-auto-subs ব্যবহার করুন (ডক §৩.১)")

    # ---- স্ট্রিম ------------------------------------------------------------
    def _stream(self, video_id: str, query: Optional[Dict[str, list]] = None) -> None:
        query = query or {}
        height = self._height(query)
        vid = video_id_of(video_id)
        if not vid:
            return self._error("বৈধ video id দিন", 400)

        range_header = self.headers.get("Range")

        # ১) ডেমো মোড — অফলাইন সিন্থেটিক বাইট
        if ARGS.demo:
            return self._send_demo(vid, range_header)

        # ২) yt-dlp বাধ্যতামূলক হলে আগে সেটাই
        if ARGS.ytdlp == "force" and ytdlp_bridge and ytdlp_bridge.ytdlp_path():
            return self._send_ytdlp(vid, height)

        # ৩) Innertube ডিরেক্ট (প্রগ্রেসিভ হলে সেরা: কোনো ট্রান্সকোড নেই)
        pick = None
        try:
            cached = meta_cached(vid)
            if cached:
                pick = {"mode": cached["stream"]["mode"],
                        "format": {"url": None}, "reason": cached["stream"]["reason"]}
            resp, _client = innertube.player(vid, hl=ARGS.hl, gl=ARGS.gl)
            pick = formats.pick_for_kaios(resp.get("streamingData") or {}, max_height=height)
        except innertube.InnertubeError as exc:
            print("[innertube] %s → yt-dlp fallback" % exc)

        if pick and pick["mode"] == "progressive" and (pick["format"] or {}).get("url"):
            return self._proxy_upstream(pick["format"]["url"], range_header, vid)
        if pick and pick["mode"] == "adaptive":
            print("[formats] adaptive only → yt-dlp দিয়ে mux করা হচ্ছে (%s)" % vid)

        # ৪) শেষ ভরসা: yt-dlp পাইপ
        if ARGS.ytdlp != "off" and ytdlp_bridge and ytdlp_bridge.ytdlp_path():
            return self._send_ytdlp(vid, height)

        return self._error(
            "এই ভিডিওর প্লেয়েবল MP4 পাওয়া যায়নি", 409,
            "কারণ: %s | সমাধান: yt-dlp ইনস্টল করুন (pip install -U yt-dlp) ও ffmpeg রাখুন, "
            "তারপর --ytdlp force; অথবা --demo দিয়ে UI টেস্ট করুন" % (pick["reason"] if pick else "SABR/PO Token"),
        )

    def _send_demo(self, vid: str, range_header: Optional[str]) -> None:
        total = 512 * 1024
        status, start, end = formats.resolve_range(range_header, total)
        if status == 416:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % total)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return
        body = demo_bytes(vid, start, end)
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy_upstream(self, url: str, range_header: Optional[str], vid: str) -> None:
        """
        googlevideo → আমাদের সার্ভার → ফোন।
        একই IP থেকে URL ব্যবহৃত হচ্ছে বলে IP-lock-এ পড়বে না; Range অক্ষত রাখা হয়।
        """
        client = (clients.load_chain() or [{}])[0]
        headers = {
            "User-Agent": client.get("userAgent", "Mozilla/5.0"),
            "Accept": "*/*",
            "Accept-Language": "%s,en;q=0.8" % ARGS.hl,
            "Origin": "https://www.youtube.com",
        }
        if range_header:
            headers["Range"] = range_header
        req = urllib.request.Request(url, headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as exc:
            # 403 → প্রায় নিশ্চিতভাবে টোকেন/নতুন URL দরকার → yt-dlp-তে ছেড়ে দিই
            print("[proxy] upstream HTTP %s — yt-dlp fallback" % exc.code)
            if ARGS.ytdlp != "off" and ytdlp_bridge and ytdlp_bridge.ytdlp_path():
                return self._send_ytdlp(vid, ARGS.max_height)
            return self._error("আপস্ট্রিম %s — URL মেয়াদোত্তীর্ণ বা PO Token দরকার" % exc.code, 502)

        status = upstream.getcode() or 200
        up_headers = {k: v for k, v in upstream.headers.items()}
        out_headers = formats.proxy_headers(status, up_headers)
        self.send_response(status if status in (200, 206) else 200)
        for key, value in out_headers.items():
            self.send_header(key, value)
        self._cors()
        self.end_headers()
        if self.command == "HEAD":
            upstream.close()
            return
        try:
            while True:
                chunk = upstream.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                                   # ফোন থামিয়ে/seek করেছে
        finally:
            upstream.close()

    def _send_ytdlp(self, vid: str, height: int = 0) -> None:
        """
        yt-dlp → stdout (fragmented MP4) → ফোন।
        সীমাবদ্ধতা: ফরওয়ার্ড-অনলি স্ট্রিম, তাই seek কাজ করে না — তাই প্লেয়ারে
        'ডাউনলোড করে দেখুন' অপশনটাই সেরা (webapp/app.js দেখুন)।
        """
        assert ytdlp_bridge is not None
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "none")
        self._cors()
        self.end_headers()
        try:
            for chunk in ytdlp_bridge.stream(vid, max_height=height or ARGS.max_height,
                                             cookies=ARGS.cookies):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def best_thumb(details: Dict[str, Any]) -> Optional[str]:
    thumbs = ((details.get("thumbnail") or {}).get("thumbnails") or [])
    if thumbs:
        return thumbs[0].get("url")
    return None


def raw_url(pick: Optional[Dict[str, Any]]) -> Optional[str]:
    if not pick:
        return None
    fmt = pick.get("format") or {}
    return fmt.get("url")


def demo_search_items(q: str) -> list:
    """--demo: UI ডেমো আইটেম (বাংলা UI টেস্টের জন্য)।"""
    samples = [
        ("প্রথম ডেমো ভিডিও — ৩৬০p", "Channel One", "3:32"),
        ("বাংলা গান: নদীর কূল", "গানের দল", "4:10"),
        ("KaiOS টিপস ও ট্রিকস", "Feature Phone Bangla", "7:45"),
        ("নিউজ হেডলাইন — আজকের", "শিরোনাম ২৪", "2:05"),
        ("রান্না: ভাত ও ডাল", "রান্নাঘর", "6:20"),
    ]
    return [{"id": "demo%05d" % i, "title": t, "channel": c, "duration": d,
             "views": "%dK views" % (i + 1), "thumb": None}
            for i, (t, c, d) in enumerate(samples)]


def main() -> int:
    global ARGS
    ap = argparse.ArgumentParser(description="KaiOS Tube প্রোক্সি — Innertube + yt-dlp")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (ডিফল্ট 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--demo", action="store_true",
                    help="নেটওয়ার্ক ছাড়াই চলে: ডেমো সার্চ/player/স্ট্রিম (UI টেস্টের জন্য)")
    ap.add_argument("--max-height", type=int, default=360, help="KaiOS-এর জন্য সর্বোচ্চ উচ্চতা (ডিফল্ট 360)")
    ap.add_argument("--hl", default="bn", help="ভাষা (bn, en, hi …)")
    ap.add_argument("--gl", default="BD", help="অঞ্চল (BD, IN, US …)")
    ap.add_argument("--ytdlp", choices=("auto", "off", "force"), default="auto",
                    help="auto=আগে Innertube, দরকারে yt-dlp | force=সবসময় yt-dlp | off=শুধু Innertube")
    ap.add_argument("--cookies", default=os.environ.get("YT_COOKIES"),
                    help="cookies.txt পাথ (লগইন-সীমিত ভিডিওর জন্য; কখনো কমিট করবেন না)")
    ap.add_argument("--allow-direct", action="store_true",
                    help="ডিবাগ: /api/player-এ raw googlevideo URL দিন (IP-lock-এর কারণে সাধারণত কাজ করবে না)")
    ARGS = ap.parse_args()

    httpd = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    print("KaiOS Tube প্রোক্সি → http://%s:%d  (mode=%s, max_height=%dp, ytdlp=%s)"
          % (ARGS.host, ARGS.port, "demo" if ARGS.demo else "live", ARGS.max_height, ARGS.ytdlp))
    if ARGS.demo:
        print("ডেমো মোড: নেটওয়ার্ক কল হবে না — UI/ডাউনলোড ফ্লো টেস্ট করুন (\"/api/health\" দেখুন)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nবন্ধ করা হচ্ছে…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
