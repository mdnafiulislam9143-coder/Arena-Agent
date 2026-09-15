# -*- coding: utf-8 -*-
"""
Innertube ক্লায়েন্ট প্রোফাইল — ২০২৬ সালের অবস্থা।

গুরুত্বপূর্ণ: YouTube প্রায় প্রতি ৪-৮ সপ্তাহে যা কাজ করে তা বদলায়।
তাই clientVersion/chain কোড নয়, কনফিগ থেকে পড়া হয় (clients.json / env)।

নোট:
  * WEB/MWEB এখন SABR-এর কারণে প্রায়ই streamingData ছাড়া ফেরে (PO Token লাগে)।
  * android (পুরোনো 19.xx) ব্যবহারযোগ্য নয় — yt-dlp নিজেও -android_sdkless এক্সক্লুড করে।
  * বিনা টোকেনে বেস্ট ব্যাংক: ANDROID_VR > TV_SIMPLY > TV > IOS > WEB_EMBEDDED > MWEB
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

# X-YouTube-Client-Name হেডারে যে সংখ্যা যায় (YouTube-এর অভ্যন্তরীণ ক্লায়েন্ট আইডি)
CLIENT_IDS: Dict[str, int] = {
    "WEB": 1,
    "MWEB": 2,
    "ANDROID": 3,
    "IOS": 5,
    "TVHTML5": 7,
    "TVHTML5_SIMPLY_EMBEDDED_PLAYER": 85,
    "WEB_EMBEDDED_PLAYER": 56,
    "ANDROID_VR": 28,
    "WEB_CREATOR": 62,
    "VISIONOS": 101,
}

# ডিফল্ট চেইন — প্রথম যেটা streamingData দেয়, সেটাই ব্যবহার হয়।
DEFAULT_CHAIN: List[Dict[str, Any]] = [
    {
        "name": "ANDROID_VR",
        "clientName": "ANDROID_VR",
        "clientVersion": "1.62.27",
        "userAgent": "com.google.android.apps.youtube.vr.oculus/1.62.27 (Linux; U; Android 12; GB) gzip",
        "extra": {
            "deviceMake": "Oculus",
            "deviceModel": "Quest 3",
            "osName": "Android",
            "osVersion": "12",
            "androidSdkVersion": 32,
        },
        "needs_po_token": False,
    },
    {
        "name": "TV_SIMPLY",
        "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
        "clientVersion": "2.0",
        "userAgent": "Mozilla/5.0 (PlayStation; PlayStation 4/12.00) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
        "extra": {"clientScreen": "EMBED"},
        "needs_po_token": False,
    },
    {
        "name": "TV",
        "clientName": "TVHTML5",
        "clientVersion": "7.20250205.16.00",
        "userAgent": "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/25.lts.1.0-qa (unlike Gecko) v8/8.8.278.8-jit gles Starboard/16, "
                     "YouTubeTV/7.20250205.16.00 (All,Wireless) v8/8.8.278.8 Starboard/16",
        "extra": {},
        "needs_po_token": False,
    },
    {
        "name": "IOS",
        "clientName": "IOS",
        "clientVersion": "20.10.4",
        "userAgent": "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X)",
        "extra": {"deviceMake": "Apple", "deviceModel": "iPhone16,2", "osName": "iPhone", "osVersion": "18.3.2.22D82"},
        "needs_po_token": False,
    },
    {
        "name": "WEB_EMBEDDED",
        "clientName": "WEB_EMBEDDED_PLAYER",
        "clientVersion": "1.20250205.01.00",
        "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "extra": {"clientScreen": "EMBED"},
        "needs_po_token": True,   # টোকেন ছাড়া ফরম্যাট URL 403 দিতে পারে
    },
    {
        "name": "MWEB",
        "clientName": "MWEB",
        "clientVersion": "2.20250205.01.00",
        "userAgent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36",
        "extra": {},
        "needs_po_token": True,
    },
]

# ওয়েব পেজে যে key পাওয়া যায় — এটাই এখনো সবচেয়ে কম-ঝুঁকির ডিফল্ট।
FALLBACK_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"


def load_chain() -> List[Dict[str, Any]]:
    """চেইন লোড: (১) YT_CLIENTS_JSON env (২) server/clients.json (৩) DEFAULT_CHAIN."""
    raw = os.environ.get("YT_CLIENTS_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clients.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_CHAIN


def api_key() -> str:
    return os.environ.get("YT_API_KEY") or FALLBACK_API_KEY
