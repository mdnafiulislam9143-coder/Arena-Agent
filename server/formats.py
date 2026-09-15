# -*- coding: utf-8 -*-
"""
KaiOS-এর জন্য ফরম্যাট বাছাই (এবং কেন এটা এত কঠিন)।

KaiOS:
  * Gecko 48 / 84 → MSE (DASH) নেই বা অচল, VP9/AV1 হার্ডওয়্যার ডিকোড সাধারণত নেই।
  * তাই দরকার: single-file progressive MP4 (H.264 + AAC-LC) = itag 18 (360p) বা 22 (720p)।
  * যদি progressive না থাকে → adaptive (video-only + audio-only) বেছে সার্ভারে ffmpeg-মার্জ করা।
    (এটাই yt-dlp fallback করে: bv*[vcodec^=avc1][height<=360]+ba[ext=m4a])

এখানে কোনো নেটওয়ার্ক কল নেই → ইউনিট টেস্টে মক করা যায় (tests/test_formats.py দেখুন)।
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# H.264/AAC (KaiOS-safe) আগে, VP9/AV1 পরে
SAFE_VCODEC_PREFIXES = ("avc1", "avc3", "h264")
RISKY_VCODEC_PREFIXES = ("vp8", "vp9", "av01", "av1")
SAFE_ACODEC_PREFIXES = ("mp4a", "aac")


def _vcodec(fmt: Dict[str, Any]) -> str:
    return str(fmt.get("mimeType", "")).split("codecs=")[-1].strip('"') or ""


def is_progressive(fmt: Dict[str, Any]) -> bool:
    """muxed = ভিডিও+অডিও একসাথে → <video> সরাসরি চালাতে পারে।"""
    return bool(fmt.get("audioQuality")) and bool(fmt.get("qualityLabel") or fmt.get("height"))


def is_kaios_safe(fmt: Dict[str, Any]) -> bool:
    mime = str(fmt.get("mimeType", ""))
    if "mp4" not in mime:
        return False
    codec = _vcodec(fmt)
    return codec.startswith(SAFE_VCODEC_PREFIXES)


def estimate_kbps(fmt: Dict[str, Any]) -> int:
    """256MB ডিভাইসে ১ Mbps-এর বেশি হলে বাফার-স্ট্রাগ প্রায় নিশ্চিত।"""
    for key in ("bitrate", "averageBitrate"):
        if fmt.get(key):
            return int(int(fmt[key]) / 1000)
    return 0


def pick_for_kaios(
    streaming_data: Dict[str, Any],
    max_height: int = 360,
    prefer_progressive: bool = True,
    max_kbps: int = 1500,
) -> Dict[str, Any]:
    """
    রিটার্ন করে এমন dict:
      {
        "mode": "progressive" | "adaptive" | "none",
        "format": {...},                 # progressive হলে বা নির্বাচিত video
        "audio": {...} | None,           # adaptive হলে
        "mime": "video/mp4",
        "height": 360,
        "reason": "…বাংলায় ব্যাখ্যা…"
      }
    """
    formats: List[Dict[str, Any]] = list(streaming_data.get("formats") or [])
    adaptive: List[Dict[str, Any]] = list(streaming_data.get("adaptiveFormats") or [])

    # কিছু ক্লায়েন্ট muxed ফরম্যাট adaptive-লিস্টেও পাঠায় → দুটোই দেখি (itag দিয়ে ডিডুপ)
    muxed = [f for f in formats if is_progressive(f)]
    seen = set(f.get("itag") for f in muxed)
    muxed += [f for f in adaptive if is_progressive(f) and f.get("itag") not in seen]

    if prefer_progressive and muxed:
        safe = [f for f in muxed if is_kaios_safe(f)] or muxed
        # ১) itag 18 (360p) — সবচেয়ে নিরাপদ  ২) হাইট/বিটরেট সীমার ভেতরে সেরা
        def score(f: Dict[str, Any]):
            itag = f.get("itag")
            h = int(f.get("height") or 0)
            kbps = estimate_kbps(f)
            penalty = 0
            if itag == 18:
                penalty -= 100          # ইউনিভার্সাল ফেভারিট
            if kbps and kbps > max_kbps:
                penalty += 50
            if h > max_height:
                penalty += 20
            return (penalty, -h)

        best = sorted(safe, key=score)[0]
        return {
            "mode": "progressive",
            "format": best,
            "audio": None,
            "mime": str(best.get("mimeType", "video/mp4")).split(";")[0],
            "height": int(best.get("height") or 0),
            "reason": "প্রগ্রেসিভ MP4 (muxed) — KaiOS-এ MSE ছাড়া সরাসরি চলে",
        }

    # ---- adaptive path (MSE নেই → সার্ভারে mux করতে হবে) ----
    videos = [f for f in adaptive if _vcodec(f) and not is_progressive(f)]
    audios = [f for f in adaptive if _vcodec(f) and not f.get("height")]

    safe_v = [f for f in videos if is_kaios_safe(f)] or videos
    safe_v = [f for f in safe_v if int(f.get("height") or 0) <= max_height] or safe_v
    safe_v = sorted(safe_v, key=lambda f: (-int(f.get("height") or 0), estimate_kbps(f)))
    safe_a = [f for f in audios if _vcodec(f).startswith(SAFE_ACODEC_PREFIXES)] or audios

    if not safe_v:
        return {"mode": "none", "format": None, "audio": None, "mime": None,
                "height": 0, "reason": "কোনো প্লেয়েবল ফরম্যাট নেই (সম্ভবত PO Token/SABR) → yt-dlp fallback দরকার"}

    video = safe_v[0]
    audio = safe_a[0] if safe_a else None
    return {
        "mode": "adaptive",
        "format": video,
        "audio": audio,
        "mime": "video/mp4",
        "height": int(video.get("height") or 0),
        "reason": "adaptive (আলাদা ভিডিও+অডিও) — KaiOS সরাসরি চালাতে পারবে না, "
                  "সার্ভারে ffmpeg দিয়ে mux/remux করতে হবে",
    }


def proxy_headers(upstream_status: int, upstream_headers: Dict[str, str]) -> Dict[str, str]:
    """
    googlevideo-র হেডার থেকে ফোনে পাঠানোর মতো নিরাপদ সাবসেট।
    Range/206 না থাকলে KaiOS প্লেয়ার seek করতে গিয়ে থেমে যায়।
    """
    out = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Type": "application/octet-stream",
    }
    for src, dst in (
        ("Content-Type", "Content-Type"),
        ("Content-Range", "Content-Range"),
        ("Content-Length", "Content-Length"),
        ("ETag", "ETag"),
        ("Last-Modified", "Last-Modified"),
    ):
        if upstream_headers.get(src):
            out[dst] = upstream_headers[src]
    if upstream_status == 206:
        out["Accept-Ranges"] = "bytes"
    return out


def parse_range(header: Optional[str], total: Optional[int] = None):
    """
    'bytes=0-1023' → (0, 1023) | 'bytes=500-' → (500, None) | 'bytes=-500' → (total-500, None)
    চুক্তি: end=None মানে "ফাইলের শেষ পর্যন্ত" (caller clamp করবে)। ব্যর্থ হলে None (=পুরো ফাইল)।
    """
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            if not end_s:
                return None
            length = int(end_s)
            if total is None:
                return (None, length)          # suffix range: caller হিসাব করবে
            return (max(0, total - length), None)
        start = int(start_s)
        end = int(end_s) if end_s else None
        if end is not None and end < start:
            return None
        return (start, end)
    except ValueError:
        return None


def resolve_range(header: Optional[str], total: int):
    """
    → (status, start, end): status = 200 (পুরো), 206 (আংশিক), 416 (সীমার বাইরে)।
    KaiOS-এর মিডিয়া প্লেয়ার seek করার সময় এই তিনটাই পাঠায় — তাই তিনটেই ঠিকভাবে দরকার।
    """
    rng = parse_range(header, total)
    if rng is None:
        return 200, 0, total - 1
    start, end = rng
    if start is None:                      # suffix range, total জানা থাকলে এখানে আসবে না
        start = max(0, total - (end or 0))
        end = None
    if start >= total:
        return 416, start, total - 1
    if end is None or end >= total:
        end = total - 1
    return 206, start, end
