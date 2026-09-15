# -*- coding: utf-8 -*-
"""
Innertube ক্লায়েন্ট (stdlib-only, fallback chain সহ)।

নীতি:
  ১) প্রতিটি ক্লায়েন্ট আলাদাভাবে চেষ্টা করা হয়; যেটা streamingData/վideoDetails দেয় সেটাই জেতে।
  ২) ব্যর্থতা ক্যাশে হয় (negative cache), যাতে প্রতি রিকোয়েস্টে সব ক্লায়েন্ট চেষ্টা না করে।
  ৩) ফোনে কখনোই googlevideo URL পাঠানো হয় না — শুধু আমাদের নিজের /api/stream/… পাথ (IP-lock, §২.৩)।
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from clients import CLIENT_IDS, api_key, load_chain

BASE = "https://www.youtube.com/youtubei/v1"
TIMEOUT = float(os.environ.get("INNERTUBE_TIMEOUT", "12"))
_NEGATIVE: Dict[str, float] = {}      # clientName -> ওই সময় পর্যন্ত চেষ্টা করব না
_NEGATIVE_TTL = 900                   # ১৫ মিনিট


class InnertubeError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _http(url: str, body: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:                     # 403, 429, …
        raise InnertubeError("HTTP %s: %s" % (exc.code, exc.reason))
    except urllib.error.URLError as exc:
        raise InnertubeError("নেটওয়ার্ক ত্রুটি: %s" % exc.reason)


def po_token() -> Optional[str]:
    """
    PO Token কোথা থেকে আসবে (২০২৬-এর বাস্তবতা):
      YT_PO_TOKEN=web.gvs+XXXX     → আপস্ট্রিম URL-এ (gvs=Google Video Server)
      YT_PO_TOKEN=web.player+XXXX  → player রিকোয়েস্টে (serviceIntegrityDimensions)
      YT_VISITOR_DATA=XXXX         → context.client.visitorData (টোকেন একই ভিজিটরের সাথে বাঁধা)
    টোকেন নিজে বানাতে হয় (ব্রাউজার-হুক/plugin/third-party provider) — yt-dlp-এর po_token গাইড দেখুন।
    """
    return os.environ.get("YT_PO_TOKEN") or None


def visitor_data() -> Optional[str]:
    return os.environ.get("YT_VISITOR_DATA") or None


def _context(client: Dict[str, Any], hl: str, gl: str, po_token_value: Optional[str] = None) -> Dict[str, Any]:
    c: Dict[str, Any] = {
        "clientName": client["clientName"],
        "clientVersion": client["clientVersion"],
        "hl": hl,
        "gl": gl,
        "timeZone": "Asia/Dhaka",
        "utcOffsetMinutes": 360,
    }
    c.update(client.get("extra") or {})
    visitor = visitor_data()
    if visitor:                                # টোকেন/লগইন ভিজিটর-আইডির সাথে বাঁধা
        c["visitorData"] = visitor
    if po_token_value and not po_token_value.startswith("web.player+"):
        c["poToken"] = po_token_value.split("+", 1)[-1]
    return {"context": {"client": c, "user": {"lockedSafetyMode": False}}}


def _headers(client: Dict[str, Any], hl: str) -> Dict[str, str]:
    name = client["clientName"]
    h = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "%s,en;q=0.8" % hl,
        "Origin": "https://www.youtube.com",
        "User-Agent": client.get("userAgent") or "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36",
        "X-YouTube-Client-Name": str(CLIENT_IDS.get(name, 1)),
        "X-YouTube-Client-Version": str(client["clientVersion"]),
    }
    if client.get("extra", {}).get("clientScreen") == "EMBED":
        h["Referer"] = "https://www.youtube.com/"
    return h


def _call(endpoint: str, payload: Dict[str, Any], client: Dict[str, Any], hl: str, gl: str) -> Dict[str, Any]:
    url = "%s/%s?key=%s&prettyPrint=false" % (BASE, endpoint, api_key())
    token = po_token()
    body = _context(client, hl, gl, token)
    body.update(payload)
    if endpoint == "player" and token:
        # নতুন (২০২৬) স্টাইল: টোকেন player-বডিতে serviceIntegrityDimensions হিসেবে যায়
        body["serviceIntegrityDimensions"] = {"poToken": token.split("+", 1)[-1]}
    return _http(url, body, _headers(client, hl))


# ---------------------------------------------------------------- public API

def player(video_id: str, hl: str = "bn", gl: str = "BD",
           chain: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    player রেসপন্স + যে ক্লায়েন্ট সফল হল তা ফেরত দেয়।
    প্রথমে সাধারণ প্রগ্রেসিভ ফরম্যাট-অনুরোধ, তারপর adaptive-অনুরোধ (SABR/Stringer কাজ করলে)।
    """
    chain = chain or load_chain()
    last_err: Optional[str] = None
    for client in chain:
        cname = client["clientName"]
        if _NEGATIVE.get(cname, 0) > _now():
            continue
        try:
            resp = _call("player", {
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True,
                "params": "CgIQBg==",          # "প্রগ্রেসিভ ফরম্যাট প্রেফার করুন"
            }, client, hl, gl)
        except InnertubeError as exc:
            last_err = "%s: %s" % (cname, exc)
            _NEGATIVE[cname] = _now() + _NEGATIVE_TTL
            continue

        status = str(resp.get("playabilityStatus", {}).get("status", ""))
        has_streams = bool((resp.get("streamingData") or {}).get("formats")
                           or (resp.get("streamingData") or {}).get("adaptiveFormats"))
        if has_streams or status == "OK":
            return resp, client
        last_err = "%s: playabilityStatus=%s" % (cname, status or "EMPTY")
        # ERROR/LOGIN_REQUIRED ক্লায়েন্টের দোষ নয় — তবে এই ক্লায়েন্টে কাজ হয়নি
        _NEGATIVE[cname] = _now() + 120
    raise InnertubeError(last_err or "সব ক্লায়েন্ট ব্যর্থ (PO Token/SABR সম্ভাব্য কারণ)")


def search(query: str, hl: str = "bn", gl: str = "BD",
           chain: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    সার্চ → হালকা লিস্ট [{id,title,channel,duration,views,thumb}]।
    KaiOS-এ বড় JSON পার্স করা ব্যয়বহুল, তাই ভেতরেই ছেঁটে দিই।
    """
    chain = chain or load_chain()
    last_err: Optional[str] = None
    for client in chain:
        try:
            resp = _call("search", {"query": query, "params": "EgIQAQ%3D%3D"}, client, hl, gl)
        except InnertubeError as exc:
            last_err = "%s: %s" % (client["clientName"], exc)
            continue
        return _parse_search(resp)
    raise InnertubeError(last_err or "সার্চ ব্যর্থ")


def _runs_text(node: Any) -> str:
    if isinstance(node, dict):
        if "simpleText" in node:
            return node["simpleText"]
        if "runs" in node:
            return "".join(r.get("text", "") for r in node["runs"])
    if isinstance(node, list):
        return "".join(_runs_text(n) for n in node)
    return ""


def _parse_search(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "videoRenderer" in node:
                v = node["videoRenderer"]
                vid = v.get("videoId")
                if vid:
                    thumbs = ((v.get("thumbnail") or {}).get("thumbnails") or [])
                    out.append({
                        "id": vid,
                        "title": _runs_text(v.get("title")),
                        "channel": _runs_text((v.get("ownerText") or v.get("longBylineText"))),
                        "duration": _runs_text(v.get("lengthText")),
                        "views": _runs_text(v.get("shortViewCountText")),
                        "thumb": thumbs[0].get("url") if thumbs else None,
                    })
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(resp)
    return out[:20]


def demo_player_response(video_id: str = "demo1234567", height: int = 360) -> Dict[str, Any]:
    """
    অফলাইন ডেমো/টেস্ট ডেটা — ঠিক Innertube-র আকারে।
    স্যান্ডবক্সে নেটওয়ার্ক না থাকলেও UI ও প্রোক্সি পরীক্ষা করা যায়।
    """
    base = "https://rr1---sn-demo.googlevideo.com/videoplayback"
    muxed = {
        "itag": 18, "mimeType": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
        "bitrate": 520000, "width": 640, "height": 360, "qualityLabel": "360p",
        "audioQuality": "AUDIO_QUALITY_LOW", "fps": 30, "contentLength": "1048576",
        "url": "%s?itag=18&demo=1&id=%s" % (base, video_id),
    }
    adaptive = [
        {"itag": 137, "mimeType": 'video/mp4; codecs="avc1.640028"', "bitrate": 2100000,
         "width": 1920, "height": 1080, "qualityLabel": "1080p", "contentLength": "4194304",
         "url": "%s?itag=137&demo=1" % base},
        {"itag": 140, "mimeType": 'audio/mp4; codecs="mp4a.40.2"', "bitrate": 128000,
         "audioQuality": "AUDIO_QUALITY_MEDIUM", "contentLength": "262144",
         "url": "%s?itag=140&demo=1" % base},
    ]
    if height >= 720:
        adaptive.insert(0, {"itag": 22, "mimeType": 'video/mp4; codecs="avc1.64001F, mp4a.40.2"',
                            "bitrate": 1300000, "width": 1280, "height": 720, "qualityLabel": "720p",
                            "audioQuality": "AUDIO_QUALITY_MEDIUM", "contentLength": "2097152",
                            "url": "%s?itag=22&demo=1" % base})
    return {
        "playabilityStatus": {"status": "OK"},
        "streamingData": {"formats": [muxed], "adaptiveFormats": adaptive,
                          "expiresInSeconds": "21540"},
        "videoDetails": {"videoId": video_id, "title": "ডেমো ভিডিও (অফলাইন)",
                         "author": "KaiOS Demo", "lengthSeconds": "212",
                         "shortDescription": "এটি অফলাইন ডেমো ডেটা — নেটওয়ার্ক ছাড়া UI টেস্টের জন্য।"},
    }
