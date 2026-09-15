# -*- coding: utf-8 -*-
"""
ডাউনলোড কিউ — MeTube-এর `app/ytdl.py`-র কোর ডিজাইন ধরে (stdlib-only, থ্রেড-ভিত্তিক)।

MeTube থেকে যা নেওয়া হলো:
  * **সার্ভার-সাইড ডাউনলোড, তারপর সার্ভ করা** — এটাই KaiOS-এর জন্য আসল সমাধান:
    প্লেব্যাকের সময় IP-lock/PO-token/adaptive সমস্যা থাকে, কিন্তু ডাউনলোড হলে ফাইলটা
    সার্ভারেই থাকে → Range/206 দিয়ে seek-সহ প্লেব্যাক, অফলাইন ক্যাশ, বারবার বাফার নেই।
  * `MAX_CONCURRENT_DOWNLOADS` (MeTube ডিফল্ট ৩; আমাদের ২ — ২৫৬MB ডিভাইস + হোস্ট বাঁচাতে)।
  * state persistence: `queue.json` + `completed.json` (AtomicJsonStore, atomic rename + fsync)।
  * completed এন্ট্রি **কম্প্যাক্ট** রাখা (MeTube-এর `_compact_persisted_entry`) — ভারী ফিল্ড বাদ।
  * `Download.__setstate__`-এর `hasattr` ব্যাকফিল প্যাটার্ন → পুরোনো স্টেট ফাইলেও ক্র্যাশ নয়।
  * API নাম মিলিয়ে রাখা: `cancel()`, `clear()`, `start_pending()`।

MeTube-এর সাথে পার্থক্য (ডকুমেন্টেড): MeTube `asyncio` + aiohttp; আমরা থ্রেড রাখি যাতে
`http.server`-এর সাথে শূন্য-ডিপেন্ডেন্সি থাকে। আচরণ একই: ফিক্সড ওয়ার্কার পুল = কনকারেন্সি ক্যাপ।
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from state_store import AtomicJsonStore, load_list, save_list

log = logging.getLogger("queue")

STATUS_QUEUED = "queueing"
STATUS_DOWNLOADING = "downloading"
STATUS_FINISHED = "finished"
STATUS_ERROR = "error"
STATUS_CANCELED = "canceled"

# স্টেটে যেগুলো রাখি (MeTube-এর কমপ্যাকশন নীতির ছোট সংস্করণ)
PERSIST_FIELDS = ("id", "url", "video_id", "title", "author", "duration", "height",
                  "status", "percent", "filename", "filepath", "filesize", "error",
                  "audio_only", "created_at", "started_at", "finished_at")

HAVE_YTDLP = importlib.util.find_spec("yt_dlp") is not None


def now() -> float:
    return time.time()


def compact(item: Dict[str, Any]) -> Dict[str, Any]:
    """স্টেটে লেখার আগে শুধু দরকারি ফিল্ড (MeTube: `_compact_persisted_entry`)।"""
    return {k: item.get(k) for k in PERSIST_FIELDS if k in item}


def youtube_id(value: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", value or "")
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", (value or "").strip()):
        return value.strip()
    return ""


def new_item(url: str, height: int, audio_only: bool = False,
             title_hint: str = None) -> Dict[str, Any]:
    vid = youtube_id(url)
    return {
        "id": vid or uuid.uuid4().hex[:11],
        "url": url if "://" in url else "https://www.youtube.com/watch?v=%s" % vid,
        "video_id": vid,
        "title": title_hint or (("ভিডিও %s" % vid) if vid else "অজানা শিরোনাম"),
        "author": "",
        "duration": 0,
        "height": height,
        "audio_only": bool(audio_only),
        "status": STATUS_QUEUED,
        "percent": 0.0,
        "speed": "",
        "eta": "",
        "downloaded": 0,
        "filesize": 0,
        "filename": None,
        "filepath": None,
        "error": None,
        "created_at": now(),
        "started_at": None,
        "finished_at": None,
        "cancel_requested": False,
    }


def backfill(item: Dict[str, Any]) -> Dict[str, Any]:
    """পুরোনো/অসম্পূর্ণ স্টেট রেকর্ড ঠিক করা (MeTube-এর `__setstate__` ব্যাকফিলের মতো)।"""
    item.setdefault("status", STATUS_QUEUED)
    item.setdefault("percent", 0.0)
    item.setdefault("cancel_requested", False)
    item.setdefault("audio_only", False)
    for key in ("speed", "eta", "error"):
        item.setdefault(key, None)
    for key in ("downloaded", "filesize", "duration", "height"):
        item.setdefault(key, 0)
    return item


# --------------------------------------------------------------------------- রানার

class Runner:
    """রানারের চুক্তি — টেস্টে/ডেমোতে বদলে দেওয়া যায় (dependency injection)।"""

    def probe(self, url: str) -> Dict[str, Any]:            # pragma: no cover - interface
        raise NotImplementedError

    def download(self, item: Dict[str, Any], on_progress: Callable[[Dict[str, Any]], None],
                 should_cancel: Callable[[], bool]) -> Dict[str, Any]:
        raise NotImplementedError


class DemoRunner(Runner):
    """অফলাইন রানার: নেটওয়ার্ক ছাড়াই পুরো কিউ/সার্ভ-ফাইল ফ্লো পরীক্ষা করা যায়।"""

    def __init__(self, download_dir: str, size: int = 256 * 1024, delay: float = 0.15):
        self.download_dir = download_dir
        self.size = size
        self.delay = delay

    def probe(self, url: str) -> Dict[str, Any]:
        vid = youtube_id(url) or "demo0000000"
        return {"video_id": vid, "title": "ডেমো ডাউনলোড %s" % vid,
                "author": "KaiOS Demo", "duration": 213}

    def download(self, item: Dict[str, Any], on_progress, should_cancel) -> Dict[str, Any]:
        filename = "%s.mp4" % item["id"]
        path = os.path.join(self.download_dir, filename)
        os.makedirs(self.download_dir, exist_ok=True)
        written = 0
        seed = sum(ord(c) for c in item["id"]) or 7
        with open(path, "wb") as fh:
            while written < self.size:
                if should_cancel():
                    fh.close()
                    raise Canceled()
                step = max(1024, min(8192, self.size // 20))       # ~২০ ধাপে শেষ হবে
                chunk = bytes(((seed + written) % 251, (written * 31) % 253, 0x4B, 0x00)) * (step // 4)
                chunk = chunk[:self.size - written]                # ঠিক size বাইট, বেশি নয়
                fh.write(chunk)
                written += len(chunk)
                on_progress({"percent": round(written * 100.0 / self.size, 2),
                             "downloaded": written, "filesize": self.size,
                             "speed": "1.2MiB/s", "eta": "0:02"})
                time.sleep(self.delay)
        return {"filepath": path, "filename": filename, "filesize": written}


class Canceled(Exception):
    """ইউজার ডাউনলোড বাতিল করেছে।"""


class YtdlpRunner(Runner):
    """
    আসল রানার: আগে `yt_dlp` পাইথন মডিউল (MeTube-এর মতোই), না থাকলে yt-dlp CLI।
    KaiOS-সেফ ফরম্যাট: AVC (H.264) ভিডিও + m4a অডিও, height সিলিং সহ; সার্ভারে mux হয়।
    """

    def __init__(self, download_dir: str, temp_dir: str, output_template: str = "%(id)s.%(ext)s",
                 height: int = 360, cookies: str = "", extractor_args: str = "",
                 audio_only: bool = False):
        self.download_dir = download_dir
        self.temp_dir = temp_dir
        self.output_template = output_template
        self.height = height
        self.cookies = cookies or ""
        self.extractor_args = extractor_args or ""
        self.audio_only = audio_only
        self._bin = shutil.which("yt-dlp") or os.environ.get("YTDLP_BIN")

    # ------------------------------------------------------------------ ফরম্যাট
    def format_selector(self, audio_only: bool = False) -> str:
        if audio_only:
            return "bestaudio[ext=m4a]/bestaudio"
        h = self.height
        return ("bv*[vcodec^=avc1][height<=%d]+ba[ext=m4a]/b[ext=mp4][height<=%d]/"
                "bv*[height<=%d]+ba/b") % (h, h, h)

    def _extractor_args_dict(self) -> Dict[str, Dict[str, List[str]]]:
        """'youtube:player_client=a,b' → {'youtube': {'player_client': ['a','b']}} (Python API ফর্ম)।"""
        out: Dict[str, Dict[str, List[str]]] = {}
        for part in filter(None, (p.strip() for p in self.extractor_args.split(";"))):
            if ":" not in part:
                continue
            extractor, _, rest = part.partition(":")
            bucket: Dict[str, List[str]] = out.setdefault(extractor.strip(), {})
            current_key: str = ""
            for token in (t.strip() for t in rest.split(",")):
                if not token:
                    continue
                if "=" in token:                      # নতুন key=value শুরুর টোকেন
                    current_key, _, value = token.partition("=")
                    current_key = current_key.strip()
                    bucket[current_key] = [v.strip() for v in value.split(",") if v.strip()]
                elif current_key:                     # `a,b` স্টাইলে key-এর পরের মান
                    bucket[current_key].append(token)
        return out

    # ------------------------------------------------------------------- probe
    def probe(self, url: str) -> Dict[str, Any]:
        if HAVE_YTDLP:
            import yt_dlp
            with yt_dlp.YoutubeDL(self._ydl_opts(skip_download=True)) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            return {"video_id": info.get("id"), "title": info.get("title"),
                    "author": info.get("uploader") or info.get("channel"),
                    "duration": int(info.get("duration") or 0),
                    "height": info.get("height") or 0}
        if not self._bin:
            raise RuntimeError("yt-dlp পাওয়া যায়নি (pip install -U yt-dlp)")
        cmd = [self._bin, "--dump-single-json", "--skip-download", "--no-warnings",
               "--no-playlist", "-f", self.format_selector(self.audio_only)]
        if self.cookies:
            cmd += ["--cookies", self.cookies]
        if self.extractor_args:
            cmd += ["--extractor-args", self.extractor_args]
        cmd.append(url)
        out = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode("utf-8", "replace")[-400:].strip() or "probe ব্যর্থ")
        info = json.loads(out.stdout.decode("utf-8", "replace"))
        return {"video_id": info.get("id"), "title": info.get("title"),
                "author": info.get("uploader") or info.get("channel"),
                "duration": int(info.get("duration") or 0),
                "height": info.get("height") or 0}

    # ---------------------------------------------------------------- download
    def _ydl_opts(self, skip_download: bool = False, audio_only: bool = None) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "format": self.format_selector(self.audio_only if audio_only is None else audio_only),
            "outtmpl": os.path.join(self.download_dir, self.output_template),
            "paths": {"home": self.download_dir, "temp": self.temp_dir},
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "noplaylist": True,
            "merge_output_format": "mp4",
            "extractor_args": self._extractor_args_dict(),
            "retries": 5,
            "socket_timeout": 15,
        }
        if skip_download:
            opts["skip_download"] = True
        if self.cookies:
            opts["cookiefile"] = self.cookies
        return opts

    def download(self, item: Dict[str, Any], on_progress, should_cancel) -> Dict[str, Any]:
        audio_only = bool(item.get("audio_only", self.audio_only))
        if HAVE_YTDLP:
            return self._download_python(item, on_progress, should_cancel, audio_only)
        return self._download_cli(item, on_progress, should_cancel, audio_only)

    def _download_python(self, item, on_progress, should_cancel, audio_only=False) -> Dict[str, Any]:
        import yt_dlp

        result: Dict[str, Any] = {"filepath": None, "filename": None, "filesize": 0}
        last = {"t": 0.0}

        def hook(status: Dict[str, Any]) -> None:
            if should_cancel():
                raise Canceled()
            if status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                done = status.get("downloaded_bytes") or 0
                if not last["t"] or now() - last["t"] > 0.4:
                    last["t"] = now()
                    on_progress({
                        "percent": round(done * 100.0 / total, 2) if total else 0.0,
                        "downloaded": done, "filesize": total,
                        "speed": status.get("_speed_str", "").strip(),
                        "eta": status.get("_eta_str", "").strip(),
                    })
            elif status.get("status") == "finished":
                result["filepath"] = status.get("filename")
                result["filename"] = os.path.basename(status.get("filename") or "")
                result["filesize"] = int(status.get("total_bytes") or 0)

        opts = self._ydl_opts(audio_only=audio_only)
        opts["progress_hooks"] = [hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item["url"], download=True)
            path = None
            if info:
                path = (info.get("requested_downloads") or [{}])[0].get("filepath")
                if not path:
                    path = ydl.prepare_filename(info)
            if path and os.path.exists(path):
                result.update({"filepath": path, "filename": os.path.basename(path),
                               "filesize": os.path.getsize(path)})
        if not result["filepath"]:
            raise RuntimeError("ডাউনলোড শেষ হলেও ফাইল পাওয়া যায়নি")
        return result

    def _download_cli(self, item, on_progress, should_cancel, audio_only=False) -> Dict[str, Any]:
        if not self._bin:
            raise RuntimeError("yt-dlp নেই: pip install -U yt-dlp  (অথবা YTDLP_BIN সেট করুন)")
        template = ("download:%(progress._percent_str)s|%(progress._speed_str)s|"
                    "%(progress._eta_str)s|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s")
        cmd = [self._bin, "--no-playlist", "--newline", "--no-warnings",
               "--progress-template", template,
               "-f", self.format_selector(audio_only),
               "--merge-output-format", "mp4",
               "-P", self.download_dir, "-P", "temp:%s" % self.temp_dir,
               "-o", self.output_template]
        if self.cookies:
            cmd += ["--cookies", self.cookies]
        if self.extractor_args:
            cmd += ["--extractor-args", self.extractor_args]
        cmd.append(item["url"])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
        filesize = 0
        assert proc.stdout is not None
        for line in proc.stdout:
            if should_cancel():
                proc.terminate()
                raise Canceled()
            line = line.strip()
            if line.startswith("download:"):
                payload = line[len("download:"):]
                parts = payload.split("|")
                percent = 0.0
                try:
                    percent = float(parts[0].replace("%", "").strip() or 0)
                except (ValueError, IndexError):
                    percent = 0.0
                downloaded = int(float(parts[3])) if len(parts) > 3 and parts[3].replace(".", "").isdigit() else 0
                if len(parts) > 4 and parts[4].replace(".", "").isdigit():
                    filesize = int(float(parts[4]))
                on_progress({"percent": percent, "speed": parts[1].strip() if len(parts) > 1 else "",
                             "eta": parts[2].strip() if len(parts) > 2 else "",
                             "downloaded": downloaded, "filesize": filesize})
        code = proc.wait()
        err = (proc.stderr.read() or "").strip() if proc.stderr else ""
        if code != 0:
            raise RuntimeError(err[-400:] or ("yt-dlp exit %d" % code))

        # আউটপুট ফাইল খোঁজা: আগে video_id, নাহলে সবচেয়ে নতুন mp4/mkv/webm
        candidates = []
        for name in sorted(os.listdir(self.download_dir)):
            if not name.endswith((".mp4", ".mkv", ".webm", ".m4a")):
                continue
            full = os.path.join(self.download_dir, name)
            candidates.append((os.path.getmtime(full), full))
        if not candidates:
            raise RuntimeError("ডাউনলোড শেষে কোনো মিডিয়া ফাইল পাওয়া যায়নি")
        candidates.sort(reverse=True)
        newest = candidates[0][1]
        return {"filepath": newest, "filename": os.path.basename(newest),
                "filesize": os.path.getsize(newest)}


# ---------------------------------------------------------------------------- কিউ

class DownloadQueue:
    def __init__(self, config, runner: Runner, notifier: Callable[[str, Dict[str, Any]], None] = None):
        self.config = config
        self.runner = runner
        self.notifier = notifier
        self._lock = threading.RLock()
        self._queue: List[Dict[str, Any]] = []
        self._completed: List[Dict[str, Any]] = []
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_persist = 0.0
        self.queue_store = AtomicJsonStore(os.path.join(config.STATE_DIR, "queue.json"), kind="queue")
        self.completed_store = AtomicJsonStore(os.path.join(config.STATE_DIR, "completed.json"),
                                               kind="completed")

    # ---------------------------------------------------------------- লাইফসাইকেল
    def initialize(self) -> None:
        """স্টেট লোড; `downloading`-এ আটকে থাকা রেকর্ড (ক্র্যাশের পর) আবার কিউতে ফেরানো।"""
        self.config.ensure_dirs()
        with self._lock:
            self._queue = [backfill(i) for i in load_list(self.queue_store)]
            self._completed = [backfill(i) for i in load_list(self.completed_store)]
            for item in self._queue:
                if item["status"] == STATUS_DOWNLOADING:
                    item["status"] = STATUS_QUEUED
                    item["percent"] = 0.0
        for _ in range(max(1, int(self.config.MAX_CONCURRENT_DOWNLOADS))):
            thread = threading.Thread(target=self._worker, name="dl-worker", daemon=True)
            thread.start()
            self._workers.append(thread)
        self._persist(force=True)
        log.info("কিউ প্রস্তুত — %d অপেক্ষমাণ, %d সম্পন্ন, কনকারেন্সি %d",
                 len(self._queue), len(self._completed), self.config.MAX_CONCURRENT_DOWNLOADS)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._workers:
            thread.join(timeout=2)
        self._workers = []

    # -------------------------------------------------------------------- পাবলিক
    def add(self, url: str, height: int = None, audio_only: bool = False) -> Dict[str, Any]:
        height = int(height or self.config.YTDLP_FORMAT_HEIGHT)
        item = new_item(url, height, audio_only=audio_only)
        if not item["video_id"] and "://" not in url:
            raise ValueError("বৈধ ভিডিও URL বা ১১ অক্ষরের ID দিন")
        with self._lock:
            existing = [i for i in self._queue if i["id"] == item["id"]]
            if existing:
                return {"status": "error", "msg": "এটি ইতিমধ্যে কিউতে আছে", "item": existing[0]}
            self._queue.append(item)
            self._persist(force=True)
        self._notify("added", item)
        self._wake.set()
        return {"status": "ok", "item": self.public(item)}

    def list_all(self) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            return {"queue": [self.public(i) for i in self._queue],
                    "completed": [self.public(i) for i in self._completed]}

    def cancel(self, ids: List[str]) -> Dict[str, str]:
        """MeTube-এর `cancel()` — অ্যাকটিভ আইটেমকে বাতিল, অপেক্ষমাণকে সরাসরি বাদ।"""
        found = False
        with self._lock:
            for item in self._queue:
                if item["id"] in ids:
                    found = True
                    if item["status"] == STATUS_DOWNLOADING:
                        item["cancel_requested"] = True
                    else:
                        item["status"] = STATUS_CANCELED
            self._queue = [i for i in self._queue if i["status"] != STATUS_CANCELED]
            self._persist(force=True)
        return {"status": "ok" if found else "error",
                "msg": None if found else "কোনো আইটেম পাওয়া যায়নি"}

    def clear(self, ids: List[str]) -> Dict[str, str]:
        """MeTube-এর `clear()` — completed থেকে সরানো (চাইলে ফাইলও মুছে ফেলা)।"""
        removed = []
        with self._lock:
            for item in list(self._completed):
                if item["id"] in ids or not ids:
                    self._completed.remove(item)
                    removed.append(item)
            self._persist(force=True)
        for item in removed:
            if self.config.DELETE_FILE_ON_TRASHCAN and item.get("filepath"):
                try:
                    os.remove(item["filepath"])
                except OSError as exc:
                    log.warning("ফাইল মোছা যায়নি %s: %s", item["filepath"], exc)
        return {"status": "ok" if removed else "error"}

    def start_pending(self, ids: List[str] = None) -> Dict[str, str]:
        with self._lock:
            for item in self._queue:
                if item["status"] == STATUS_ERROR and (not ids or item["id"] in ids):
                    item["status"] = STATUS_QUEUED
                    item["error"] = None
            self._persist(force=True)
        self._wake.set()
        return {"status": "ok"}

    def find_file(self, video_id: str) -> Optional[str]:
        """সম্পন্ন ডাউনলোডের ফাইল (থাকলে) — `/api/stream` এটাই আগে ব্যবহার করে।"""
        with self._lock:
            for item in self._completed:
                if item["id"] == video_id and item.get("filepath") and os.path.exists(item["filepath"]):
                    return item["filepath"]
        return None

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for item in self._queue + self._completed:
                if item["id"] == item_id:
                    return self.public(item)
        return None

    def public(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """ডিভাইসে পাঠানোর জন্য হালকা রূপ (cancel_requested-এর মতো অভ্যন্তরীণ ফিল্ড বাদ)।"""
        out = {k: v for k, v in item.items() if k != "cancel_requested"}
        out["progress"] = round(float(item.get("percent") or 0), 1)
        out["playable"] = bool(item.get("filepath") and item["status"] == STATUS_FINISHED)
        out["file_url"] = ("/api/file/%s" % item["id"]) if out["playable"] else None
        return out

    # ------------------------------------------------------------------- ইন্টার্নাল
    def _notify(self, event: str, item: Dict[str, Any]) -> None:
        if self.notifier:
            try:
                self.notifier(event, self.public(item))
            except Exception as exc:  # noqa: BLE001
                log.warning("নোটিফায়ার ব্যর্থ: %s", exc)

    def _persist(self, force: bool = False) -> None:
        """স্টেট লেখা — প্রগ্রেস আপডেটে থ্রটল, স্টেটাস বদলে সাথে সাথে (MeTube-এর মতো)।"""
        if not force and now() - self._last_persist < 1.0:
            return
        self._last_persist = now()
        try:
            save_list(self.queue_store, [compact(i) for i in self._queue])
            save_list(self.completed_store, [compact(i) for i in self._completed])
        except OSError as exc:
            log.error("স্টেট লেখা যায়নি: %s", exc)

    def _next_item(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            for item in self._queue:
                if item["status"] == STATUS_QUEUED:
                    item["status"] = STATUS_DOWNLOADING
                    item["started_at"] = now()
                    self._persist(force=True)
                    self._notify("updated", item)
                    return item
        return None

    def _worker(self) -> None:
        while not self._stop.is_set():
            item = self._next_item()
            if not item:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            try:
                self._process(item)
            except Canceled:
                with self._lock:
                    item["status"] = STATUS_CANCELED
                    self._queue = [i for i in self._queue if i["id"] != item["id"]]
                log.info("বাতিল: %s", item["id"])
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    item["status"] = STATUS_ERROR
                    item["error"] = str(exc)[:400]
                log.error("ডাউনলোড ব্যর্থ (%s): %s", item["id"], exc)
                self._notify("updated", item)
            self._persist(force=True)

    def _process(self, item: Dict[str, Any]) -> None:
        # ১) মেটাডেটা (শিরোনাম/দৈর্ঘ্য) — UI-তে সুন্দর দেখানোর জন্য
        try:
            meta = self.runner.probe(item["url"])
            with self._lock:
                item["title"] = meta.get("title") or item["title"]
                item["author"] = meta.get("author") or item.get("author") or ""
                item["duration"] = int(meta.get("duration") or 0)
                if meta.get("video_id"):
                    item["video_id"] = meta["video_id"]
        except Exception as exc:  # noqa: BLE001
            log.info("probe ব্যর্থ (%s) — শিরোনাম ছাড়াই এগোচ্ছি: %s", item["id"], exc)

        self._notify("updated", item)

        def on_progress(data: Dict[str, Any]) -> None:
            with self._lock:
                item.update({k: v for k, v in data.items() if v not in (None, "")})
            self._persist()

        result = self.runner.download(item, on_progress, lambda: bool(item.get("cancel_requested")))

        with self._lock:
            item["filepath"] = result.get("filepath")
            item["filename"] = result.get("filename")
            item["filesize"] = int(result.get("filesize") or 0)
            item["percent"] = 100.0
            item["status"] = STATUS_FINISHED
            item["finished_at"] = now()
            if item in self._queue:
                self._queue.remove(item)
            self._completed.append(item)
            self._completed = self._completed[-200:]        # স্টেট ফাইল সীমিত রাখা
        self._notify("completed", item)
