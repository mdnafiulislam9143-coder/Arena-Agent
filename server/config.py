# -*- coding: utf-8 -*-
"""
Config — MeTube-এর `Config` ক্লাস প্যাটার্নের হুবহু অনুকরণ (app/main.py)।

MeTube-এর কনফিগ-নীতিগুলো আমরা ধরে রেখেছি, কারণ ওগুলো সত্যিই কাজ করে:
  * সব সেটিং `_DEFAULTS` ডিক্টে, প্রতিটি env ভেরিয়েবল দিয়ে ওভাররাইড করা যায়।
  * `%NAME` / `%%NAME` ইনডিরেকশন (Docker Compose-এ `%%` লাগে, তাই দুই ফর্মই গ্রহণ করি)।
  * `_BOOLEAN` তালিকায় থাকা ভেরিয়েবল কঠোরভাবে true/false/on/off/1/0 — ভুল হলে চালু না হয়ে মরে,
    কারণ নীরব ভুল-কনফিগ ডিবাগ করা সবচেয়ে কষ্টকর।
  * trailing slash নরমালাইজ, HOST='*' → '' (aiohttp-র মতো getaddrinfo '*' বোঝে না — ঠিক একই ফাঁদ
    আমাদের http.server-এও আছে)।
  * CLI ফ্ল্যাগ থাকলে সেটাই জেতে (MeTube env-only; আমাদের দুটোই দরকার — KaiOS ডেভে CLI সুবিধা)।
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Dict

log = logging.getLogger("config")

# `%%DOWNLOAD_DIR` বা `%DOWNLOAD_DIR/.state` — দুই ফর্মই ধরা পড়ে; `%(id)s` ধরা পড়ে না
_REFERENCE_RE = re.compile(r"^%+([A-Z_][A-Z0-9_]*)(.*)$", re.DOTALL)

DEFAULTS: Dict[str, str] = {
    # --- সার্ভার ---
    "HOST": "0.0.0.0",
    "PORT": "8080",
    "URL_PREFIX": "",
    "LOGLEVEL": "INFO",
    "ENABLE_ACCESSLOG": "false",
    # --- ডিরেক্টরি (MeTube-এর নামগুলোই) ---
    "DOWNLOAD_DIR": "downloads",
    "TEMP_DIR": "%DOWNLOAD_DIR/tmp",
    "STATE_DIR": "%DOWNLOAD_DIR/.state",
    "OUTPUT_TEMPLATE": "%(id)s.%(ext)s",
    # --- ডাউনলোড আচরণ ---
    "MAX_CONCURRENT_DOWNLOADS": "2",
    "DELETE_FILE_ON_TRASHCAN": "false",
    "CLEAR_COMPLETED_AFTER": "0",
    # --- yt-dlp ---
    "YTDLP_MODE": "auto",                 # auto | off | force
    "YTDLP_FORMAT_HEIGHT": "360",         # KaiOS-এর জন্য ডিফল্ট সিলিং
    "YTDLP_EXTRACTOR_ARGS": "youtube:player_client=default,tv,tv_simply,tv_downgraded,ios,-android_sdkless",
    "COOKIES": "",
    # --- Innertube ---
    "HL": "bn",
    "GL": "BD",
    "MAX_HEIGHT": "360",
    "PO_TOKEN": "",
    "VISITOR_DATA": "",
    # --- নিরাপত্তা ---
    "ALLOW_PRIVATE_ADDRESSES": "false",
    "ALLOWED_HOSTS": "",
    "ALLOW_DIRECT": "false",
    # --- ডেমো (টেস্টে ছোট/দ্রুত করতে env দিয়ে বদলানো যায়) ---
    "DEMO": "false",
    "DEMO_SIZE": "262144",
    "DEMO_DELAY": "0.15",
}

_BOOLEAN = ("ENABLE_ACCESSLOG", "DELETE_FILE_ON_TRASHCAN", "ALLOW_PRIVATE_ADDRESSES",
            "ALLOW_DIRECT", "DEMO")


def parse_log_level(value: str) -> int:
    return getattr(logging, str(value).upper(), logging.INFO)


class Config:
    def __init__(self, env: Dict[str, str] = None) -> None:
        env = env if env is not None else os.environ
        for key, default in DEFAULTS.items():
            setattr(self, key, env.get(key, default))

        # %%NAME / %NAME ইনডিরেকশন — MeTube-এর প্যাটার্ন, তবে সাফিক্সও সমর্থিত:
        #   "%%DOWNLOAD_DIR"        → পুরো মান রেফারেন্স
        #   "%DOWNLOAD_DIR/.state"  → রেফারেন্স + "/.state" (MeTube-এ এটা ছিল না, তাই
        #                             পাথ-টেমপ্লেট চুপচাপ "" হয়ে যায় — ক্লাসিক কনফিগ ফাঁদ)
        # `%(id)s.%(ext)s` টাইপের OUTPUT_TEMPLATE ছোঁয়া হয় না (regex-এ ( অক্ষর মেলে না)।
        for _ in range(3):                       # নেস্টেড রেফারেন্সও কাজ করবে
            changed = False
            for key, value in list(self.__dict__.items()):
                if isinstance(value, str) and value.startswith("%"):
                    match = _REFERENCE_RE.match(value)
                    if not match:
                        continue
                    target, rest = match.group(1), match.group(2)
                    resolved = str(getattr(self, target, ""))
                    if resolved != value:
                        setattr(self, key, resolved + rest)
                        changed = True
            if not changed:
                break

        for key in _BOOLEAN:
            value = getattr(self, key)
            if str(value) not in ("true", "false", "True", "False", "on", "off", "1", "0", ""):
                log.error('env ভেরিয়েবল "%s"-এর মান বুলিয়ান নয়: "%s"', key, value)
                sys.exit(1)
            setattr(self, key, str(value) in ("true", "True", "on", "1"))

        # getaddrinfo '*' বোঝে না; '' মানে সব ইন্টারফেস (দুই ফ্যামিলি)
        if str(self.HOST).strip() == "*":
            self.HOST = ""

        if self.URL_PREFIX and not self.URL_PREFIX.endswith("/"):
            self.URL_PREFIX += "/"

        for attr in ("DOWNLOAD_DIR", "TEMP_DIR", "STATE_DIR"):
            value = str(getattr(self, attr) or ".")
            if len(value) > 1 and value.endswith("/"):
                value = value.rstrip("/") or "/"
            setattr(self, attr, value)

        try:
            self.DEMO_SIZE = max(1024, int(self.DEMO_SIZE))
            self.DEMO_DELAY = max(0.0, float(self.DEMO_DELAY))
        except ValueError:
            log.error("DEMO_SIZE/DEMO_DELAY সংখ্যা হতে হবে")
            sys.exit(1)
        try:
            self.PORT = int(self.PORT)
        except ValueError:
            log.error("PORT একটি সংখ্যা হতে হবে")
            sys.exit(1)
        try:
            self.MAX_CONCURRENT_DOWNLOADS = max(1, int(self.MAX_CONCURRENT_DOWNLOADS))
        except ValueError:
            log.error("MAX_CONCURRENT_DOWNLOADS একটি সংখ্যা হতে হবে")
            sys.exit(1)
        try:
            self.MAX_HEIGHT = int(self.MAX_HEIGHT)
            self.YTDLP_FORMAT_HEIGHT = int(self.YTDLP_FORMAT_HEIGHT)
            self.CLEAR_COMPLETED_AFTER = int(self.CLEAR_COMPLETED_AFTER)
        except ValueError:
            log.error("MAX_HEIGHT/YTDLP_FORMAT_HEIGHT/CLEAR_COMPLETED_AFTER সংখ্যা হতে হবে")
            sys.exit(1)

        self.ALLOWED_HOSTS_LIST = [h.strip() for h in str(self.ALLOWED_HOSTS).split(",") if h.strip()]

    # ------------------------------------------------------------------ হেল্পার
    def ensure_dirs(self) -> None:
        for attr in ("DOWNLOAD_DIR", "TEMP_DIR", "STATE_DIR"):
            path = getattr(self, attr)
            if path and not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)

    def path(self, attr: str, *parts: str) -> str:
        return os.path.join(getattr(self, attr), *parts)

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in DEFAULTS if k not in ("PO_TOKEN", "VISITOR_DATA", "COOKIES")}

    def __repr__(self) -> str:
        return "Config(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in
                                        ("HOST", "PORT", "DOWNLOAD_DIR", "STATE_DIR",
                                         "MAX_CONCURRENT_DOWNLOADS", "DEMO"))
