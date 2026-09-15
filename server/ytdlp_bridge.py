# -*- coding: utf-8 -*-
"""
yt-dlp ব্রিজ — "সব ভিডিও চলে" পথ।

কেন এটা দরকার: ২০২৬-এ WEB/IOS-এর মতো ক্লায়েন্ট SABR+PO Token-এর কারণে ফরম্যাট দেয় না।
yt-dlp (`player_client=tv,tv_simply,default`) এখনো প্রগ্রেসিভ/AVC ফরম্যাট বের করে।
KaiOS-এর জন্য আমরা ৩৬০p AVC + m4a-কে ffmpeg দিয়ে মার্জ করে **progressive MP4** বানিয়ে stdout-এ পাইপ করি,
যাতে ফোনের <video> ট্যাগে MSE ছাড়াই চলে।
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, Iterator, List, Optional


def ytdlp_path() -> Optional[str]:
    return os.environ.get("YTDLP_BIN") or shutil.which("yt-dlp")


def ffmpeg_path() -> Optional[str]:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")


def js_runtime() -> Optional[str]:
    """yt-dlp-ejs-এর জন্য JS রানটাইম — এটা ছাড়া nsig/challenge ভাঙে।"""
    for name in ("deno", "node", "bun", "qjs", "quickjs"):
        path = shutil.which(name)
        if path:
            return path
    return None


def status() -> Dict[str, Any]:
    return {
        "ytdlp": ytdlp_path(),
        "ffmpeg": ffmpeg_path(),
        "js_runtime": js_runtime(),
        "ready": bool(ytdlp_path()),
        "note": None if ytdlp_path() else "yt-dlp ইনস্টল নেই — শুধু Innertube ডিরেক্ট মোড চলবে",
    }


def format_selector(max_height: int = 360, audio_codec: str = "m4a") -> str:
    """
    অগ্রাধিকার:
      ১) AVC ভিডিও + m4a অডিও (KaiOS-সেফ, সার্ভারে mux হবে)
      ২) যেকোনো MP4 যেটার হাইট সীমার ভেতরে
      ৩) শেষ ভরসা: সবচেয়ে ভালো
    """
    return (
        "bv*[vcodec^=avc1][height<=%(h)d]+ba[ext=%(a)s]/"
        "b[ext=mp4][height<=%(h)d]/"
        "bv*[height<=%(h)d]+ba/"
        "b" % {"h": max_height, "a": audio_codec}
    )


DEFAULT_EXTRACTOR_ARGS = (
    "youtube:player_client=default,tv,tv_simply,tv_downgraded,ios,-android_sdkless,-web_safari"
)


def build_cmd(
    video_id: str,
    max_height: int = 360,
    to_stdout: bool = True,
    extractor_args: str = DEFAULT_EXTRACTOR_ARGS,
    cookies: Optional[str] = None,
    extra: Optional[List[str]] = None,
) -> List[str]:
    exe = ytdlp_path()
    if not exe:
        raise RuntimeError("yt-dlp পাওয়া যায়নি (pip install -U yt-dlp অথবা YTDLP_BIN সেট করুন)")
    cmd: List[str] = [
        exe,
        "--extractor-args", extractor_args,
        "--no-playlist", "--no-warnings", "--quiet", "--no-cache-dir", "--no-part",
        "--socket-timeout", "15", "--retries", "5", "--fragment-retries", "5",
        "-f", format_selector(max_height),
        "--merge-output-format", "mp4",
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    if to_stdout:
        # ffmpeg mux → MP4 fragmented stream (moov স্ট্রিমের শুরুতে থাকে, তাই প্লেব্যাক দ্রুত শুরু হয়)
        cmd += ["--postprocessor-args", "ffmpeg:-movflags frag_keyframe+empty_moov+faststart"]
        cmd += ["-o", "-"]
    cmd += extra or []
    cmd.append("https://www.youtube.com/watch?v=%s" % video_id)
    return cmd


def stream(video_id: str, max_height: int = 360, cookies: Optional[str] = None,
           chunk_size: int = 65536) -> Iterator[bytes]:
    """yt-dlp stdout → হোস্ট → ফোন। মেমরি-বান্ধব (পুরো ফাইল বাফার করে না)।"""
    cmd = build_cmd(video_id, max_height=max_height, to_stdout=True, cookies=cookies)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    assert proc.stdout is not None
    try:
        while True:
            chunk = proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.poll() is None:
            proc.terminate()
        err = b""
        if proc.stderr is not None:
            try:
                err = proc.stderr.read() or b""
            except Exception:  # noqa: BLE001
                err = b""
        if err:
            # ডিভাইসে কখনো stderr পাঠাই না; সার্ভার লগেই যথেষ্ট
            print("[yt-dlp] %s" % err.decode("utf-8", "replace")[-800:].strip())


def probe(video_id: str) -> Dict[str, Any]:
    """মেটাডেটা + ফরম্যাট টেবিল (ডায়াগনস্টিক; ভারী — ক্যাশে করুন)।"""
    exe = ytdlp_path()
    if not exe:
        raise RuntimeError("yt-dlp পাওয়া যায়নি")
    cmd = [exe, "--dump-single-json", "--no-playlist", "--no-warnings", "--skip-download",
           "--extractor-args", DEFAULT_EXTRACTOR_ARGS,
           "https://www.youtube.com/watch?v=%s" % video_id]
    out = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[-500:])
    return json.loads(out.stdout.decode("utf-8", "replace"))


def save(video_id: str, outdir: str, max_height: int = 360, cookies: Optional[str] = None) -> str:
    """ডিভাইসের জন্য অফলাইন সেভ (KaiOS-এ হোস্ট করা ফাইলই সবচেয়ে নির্ভরযোগ্য প্লেব্যাক)।"""
    exe = ytdlp_path()
    if not exe:
        raise RuntimeError("yt-dlp পাওয়া যায়নি")
    os.makedirs(outdir, exist_ok=True)
    cmd = [
        exe, "--extractor-args", DEFAULT_EXTRACTOR_ARGS,
        "--no-playlist", "--no-part", "--quiet",
        "-f", format_selector(max_height),
        "--merge-output-format", "mp4",
        "-o", os.path.join(outdir, "%(id)s.%(ext)s"),
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append("https://www.youtube.com/watch?v=%s" % video_id)
    subprocess.run(cmd, check=True, timeout=900)
    return os.path.join(outdir, "%s.mp4" % video_id)
