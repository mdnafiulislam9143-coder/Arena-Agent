# MeTube আর্কিটেকচার — গভীর বিশ্লেষণ (বাংলা)
**রেফারেন্স:** [`alexta69/metube`](https://github.com/alexta69/metube) @ master (`7938837`, ২০২৬-০৯-১৫-এ যাচাই করা) · **উদ্দেশ্য:** ওদের স্ট্রাকচার হুবহু বোঝা, তারপর আমাদের KaiOS Tube-এ কোনটা কেন নেব/নেব না সেটা সিদ্ধান্ত নেওয়া।

---

## ১. এক নজরে কন্ট্রাক্ট

MeTube-এর নিজের `AGENTS.md`-এ লেখা সারমর্ম: **"give it a URL, it runs yt-dlp well, and correct files appear."**
এর বাইরের সব কিছু (ফাইল-পরে-সম্পাদনা, ট্যাগ এডিটর, MusicBrainz-এর মতো থার্ড-পার্টি API, মিডিয়া লাইব্রেরি অর্গানাইজেশন) তারা **সচেতনভাবে প্রত্যাখ্যান** করে, কোড কোয়ালিটি যত ভালোই হোক। এটা আমাদের জন্যও কাজের নিয়ম: KaiOS Tube-এর কন্ট্রাক্ট = **"ফোনে YouTube চালাও, যা চলে"** — বাকিটা (ট্রান্সকোড পাইপলাইন, লাইব্রেরি ম্যানেজমেন্ট) বাদ।

একই ফাইলে আরও দুটো কঠোর নিয়ম:
- `README.md` **২৫,০০০ ক্যারেক্টারের নিচে** থাকতেই হবে (Docker Hub সিঙ্ক হয়) → ডকুমেন্টেশন লেখার সময় বাজেট মাথায় রাখতে হয়।
- `master` **প্রতিটি পুশে রিলিজ হয়** → PR মার্জ মানে রিলিজ; "পরে ঠিক করব" বলে কিছু মার্জ করা যায় না।

---

## ২. রিপো ম্যাপ (সাইজ = জটিলতার মানচিত্র)

```
app/main.py          58 KB   HTTP সার্ভার, Socket.IO ইভেন্ট, REST রাউট, Config ক্লাস
app/ytdl.py          98 KB   ডাউনলোড কিউ: Download, PersistentQueue, yt-dlp ইন্টিগ্রেশন
app/subscriptions.py 41 KB   চ্যানেল/প্লেলিস্ট সাবস্ক্রিপশন ম্যানেজার
app/url_guard.py     14 KB   SSRF গার্ড: validate_url + install_socket_guard
app/state_store.py    9 KB   AtomicJsonStore (atomic write, quarantine, schema)
app/dl_formats.py     7 KB   ফরম্যাট/কোডেক/গুণমান → yt-dlp selector + postprocessor
app/bg_tasks.py      0.9 KB  ব্যাকগ্রাউন্ড asyncio টাস্কের রেফারেন্স ধরে রাখা (GC এড়াতে)
app/music_metadata.py 4.5 KB (in-scope: ডাউনলোডের সময় মেটাডেটা সমৃদ্ধ করা)
app/tests/           ~330 KB  pytest (test_download_queue 50KB, test_subscriptions 66KB, test_ytdl_utils 53KB …)
ui/src/app/          Angular 22 standalone components, services/, pipes/, interfaces/
Dockerfile           multi-stage (Node builder → Python runtime), multi-arch amd64/arm64
.github/workflows/   main.yml (lint+test+build), update-ytdl-update.yml (নাইটলি yt-dlp), readme-size.yml
metube.ai            143 KB (AI/এজেন্ট সংক্রান্ত সহায়ক ফাইল)
```

**লক্ষণীয়:** টেস্ট কোড (~330 KB) অ্যাপ্লিকেশন কোডের (~230 KB) চেয়ে **বড়**। এটাই সবচেয়ে বড় ডিজাইন সিগন্যাল — এটা এমন সফটওয়্যার যেখানে "ছোট টেক্সচুয়াল পরিবর্তন" প্রোডাকশনে না ভেঙে যায় সেটাই আসল কাজ।

---

## ৩. ব্যাকএন্ড আর্কিটেকচার

### ৩.১ টেক স্ট্যাক
Python 3.13+ · `aiohttp` (HTTP) · `python-socketio 5.x` (রিয়েল-টাইম) · `yt-dlp` · `watchfiles`। সিঙ্ক = `uv`, ফ্রন্টএন্ড = `pnpm`।

### ৩.২ `Config` ক্লাস — env-ড্রিভেন কনফিগের রেফারেন্স ইমপ্লিমেন্টেশন
```python
class Config:
    _DEFAULTS = { 'DOWNLOAD_DIR': '.', 'AUDIO_DOWNLOAD_DIR': '%%DOWNLOAD_DIR', 'STATE_DIR': '.',
                  'MAX_CONCURRENT_DOWNLOADS': '3', 'HOST': '0.0.0.0', 'PORT': '8081', ... }
    _BOOLEAN  = ('CUSTOM_DIRS', 'HTTPS', 'ALLOW_PRIVATE_ADDRESSES', ...)
```
যে কৌশলগুলো আসলেই মূল্যবান (আমরা সবগুলো নিয়েছি):
1. **`%%NAME` ইনডিরেকশন** — `AUDIO_DOWNLOAD_DIR` ডিফল্টে `DOWNLOAD_DIR`-কে রেফার করে; Docker Compose-এর `$` এস্কেপিংয়ের জন্য `%%`।
2. **কঠোর বুলিয়ান** — `true/false/on/off/1/0` ছাড়া অন্য কিছু হলে `sys.exit(1)`; নীরব ভুল-কনফিগ ডিবাগ করা অসম্ভব।
3. **`HOST='*'` → `''`** — `getaddrinfo` তারকাচিহ্ন বোঝে না; খালি স্ট্রিং = দুই অ্যাড্রেস ফ্যামিলিতে আলাদা সকেট। আমাদের `http.server`-এও একই ফাঁদ ছিল।
4. **trailing slash নরমালাইজ** — `DOWNLOAD_DIR=/downloads/` কাস্টম-ফোল্ডার ড্রপডাউনে `downloads` নামে ভুয়া অপশন তৈরি করত।
5. **লজিক্যাল কনফ্লিক্টে ওয়ার্ন, ক্র্যাশ নয়**: `DEFAULT_FOLDER` + `CUSTOM_DIRS=false` → ফিল্ডটি সরিয়ে দিয়ে ওয়ার্নিং।
6. **Timing ভ্যালিডেশন** (`YTDL_NIGHTLY_UPDATE_TIME` → `HH:MM` রেজেক্স)।
7. **রানটাইম ওভাররাইড** — `set_runtime_override('cookiefile', path)` কুকিজ আপলোডে; UI থেকে yt-dlp অপশন বদলানো যায় রিস্টার্ট ছাড়াই।

### ৩.৩ রাউট (aiohttp `RouteTableDef`) — API চুক্তি
| রুট | ধরন | কাজ |
|---|---|---|
| `/add` | POST | ডাউনলোড/সাবস্ক্রিপশন যোগ; `parse_download_options` কঠোর ভ্যালিডেশন |
| `/delete` | POST | `{ids, where: queue\|done}` → `cancel()` বা `clear()` |
| `/start` | POST | এরর/পেন্ডিং আইটেম রিট্রাই |
| `/history` | GET | কিউ + সম্পন্ন তালিকা |
| `/subscriptions*` | GET/POST | সাবস্ক্রিপশন CRUD + `check` |
| `/upload-cookies`, `/delete-cookies`, `/cookie-status` | POST/GET | 1MB সীমা, ফাইল `0o600`, `os.replace` |
| `/{PUBLIC_HOST_URL}*` | GET | ডাউনলোড ডিরেক্টরি থেকে ফাইল সার্ভ (`DOWNLOAD_DIRS_INDEXABLE` দিয়ে ডিরেক্টরি লিস্টিং) |

`parse_download_options` সব ইনপুট lowercase করে, `VALID_*` সেট দিয়ে ভ্যালিডেট করে (`download_type ∈ {video,audio,captions,thumbnail}`, `codec ∈ {auto,h264,h265,av1,vp9}`), আর **legacy স্কিমা মাইগ্রেট** করে (`format: m4a` → `download_type=audio, format=m4a`) — পুরোনো ক্লায়েন্ট যাতে না ভাঙে।

### ৩.৪ রিয়েল-টাইম: Notifier → Socket.IO
REST শুধু কমান্ড; **স্টেট পরিবর্তন আসে Socket.IO ইভেন্টে**: `added`, `updated`, `completed`, `canceled`, `cleared`, `subscription_*`, `ytdl_options_changed`। ক্লায়েন্ট (Angular) `ngx-socket-io` দিয়ে সাবস্ক্রাইব করে; পোলিং নেই।

> **আমাদের ইচ্ছাকৃত ডেভিয়েশন:** KaiOS ব্রাউজার (Gecko 48/84) + 256MB RAM-এ socket.io ক্লায়েন্ট (~50KB+) ও দীর্ঘ-জীবী WebSocket ব্যয়বহুল। আমরা **২.৫ সেকেন্ডের হালকা REST পোলিং** করি, এবং শুধু কিউ স্ক্রিন খোলা থাকলে (স্ক্রিন ছাড়লেই পোল বন্ধ)। কন্ট্রাক্ট একই থাকে: "added/updated/completed" ইভেন্ট → পোল রেসপন্সের স্টেটাস ফিল্ড।

### ৩.৫ Serialization
`serializer.encode()` কাস্টম: `bytes`/`datetime`-কে মার্কার-অবজেক্টে (`__metube_bytes__`, `__metube_datetime__`) রূপান্তর। আমাদের `state_store.to_json_compatible()` ঠিক এই প্যাটার্ন।

---

## ৪. ডাউনলোড কিউ (`app/ytdl.py`) — সবচেয়ে দামি অংশ

```
Download(dataclass)   → একটি আইটেম: url, status, progress, filename, error, per-download অপশন
PersistentQueue       → queue.json / pending.json / completed.json-এ স্টেট, রিস্টার্ট-সেফ
__extract_info()      → প্রথমে মেটাডেটা (শিরোনাম/দৈর্ঘ্য) — UI তৎক্ষণাৎ কিছু দেখাতে পারে
MAX_CONCURRENT_DOWNLOADS → সেমাফোর: অতিরিক্ত ডাউনলোড অপেক্ষা করে
__setstate__          → পুরোনো persisted রেকর্ডে নতুন ফিল্ড `hasattr` দিয়ে ব্যাকফিল
_compact_persisted_entry → completed এন্ট্রি থেকে ভারী ডেটা বাদ (স্টেট ফাইল স্ফীত হয় না)
```

শেখার মতো ডিটেইল:
- **মেটাডেটা আগে, ডাউনলোড পরে** — ব্যবহারকারী সাথে সাথে শিরোনাম/থাম্বনেইল/দৈর্ঘ্য দেখে।
- **ট্র্যাশক্যান সেমান্টিক্স** — completed থেকে সরানো ≠ ফাইল মুছে ফেলা; `DELETE_FILE_ON_TRASHCAN=false` ডিফল্ট।
- **নাইটলি আপডেট** — `YTDL_NIGHTLY_UPDATE_TIME`-এ গ্রেসফুল রিস্টার্ট (`GracefulExit`) ট্রিগার করে যাতে নতুন yt-dlp লোড হয়; কারণ YouTube-এর বিরুদ্ধে এটা দৌড়।
- **`watchfiles`** দিয়ে `YTDL_OPTIONS_FILE` হট-রিলোড → সেটিংস বদলে কন্টেইনার রিস্টার্ট লাগে না।

---

## ৫. `state_store.py` — ছোট ফাইল, বড় শিক্ষা

| কৌশল | কেন |
|---|---|
| `mkstemp` → `write` → `fsync` → `os.replace` | আধা-লেখা স্টেট কখনো পড়া যাবে না; `rename` POSIX-এ atomic |
| নির্দিষ্ট errno-তে সরল লিখনে ফলব্যাক | NFS-এ atomic কাজ নাও করতে পারে; কিন্তু **ENOSPC/EIO-তে ফলব্যাক নয়** (ভালো ফাইল নষ্ট হবে) |
| `kind` + `schema_version` | ভুল/পুরোনো ফাইল ধরা পড়ে |
| `.invalid.<ts>` quarantine | করাপ্ট ডেটা মুছে না ফেলে সরিয়ে রাখা — বাগ রিপোর্টে প্রমাণ থাকে |
| `0o600` + `fchmod` | স্টেটে URL/অপশন/টোকেন থাকতে পারে; শেয়ারড মাউন্টে ফাঁস নয় |
| ফাইল না থাকলে/ভাঙা হলে `None` | কলার সহজে ডিফল্টে ফিরতে পারে |

আমাদের `server/state_store.py` এই আটটাই রাখে (বাংলা কমেন্টসহ), এবং `tests/test_state_store.py` সাতটি কেস দিয়ে যাচাই করে (পারমিশন, quarantine, serialize-fail-safe সহ)।

---

## ৬. `dl_formats.py` — ফরম্যাট বাছাইয়ের শৃঙ্খলা

```python
CODEC_FILTER_MAP = {'h264': "[vcodec~='^(h264|avc)']", 'h265': …, 'av1': …, 'vp9': …}
get_format(type, codec, format, quality):
    audio     → "bestaudio[ext=m4a]/bestaudio/best"
    video     → "bestvideo{codec}{height<=Q}{ext=mp4}+bestaudio[ext=m4a]/bestvideo…+bestaudio/best{vcombo}"
    'ios'     → H.264/HEVC-স্পেসিফিক সিলেক্টর (Apple-বান্ধব), সেটাও progressive ফলব্যাক সহ
get_opts(...): postprocessors ক্রমে যোগ হয় — FFmpegExtractAudio → FFmpegMetadata → EmbedThumbnail, captions-এ FFmpegSubtitlesConvertor
```

আমাদের `formats.py` আসলে এই ফাংশনের **KaiOS-স্পেশালাইজেশন**: `itag 18 (360p AVC+AAC, muxed)` কে −১০০ পেনাল্টি দিয়ে আগে রাখা, bits>1.5Mbps-এ পেনাল্টি, VP9/AV1 বাদ, আর `mode: progressive|adaptive|none` + `reason` রিটার্ন করা — যাতে ফোনে **কেন** চলছে না সেটা দেখানো যায়। MeTube-এর কোডেক টেবিলটা আমরা `dlqueue.YtdlpRunner.format_selector()`-এ নিয়েছি (`vcodec^=avc1` + `height<=N` + `ext=m4a` + three-tier fallback)।

---

## ৭. `url_guard.py` — SSRF প্রতিরক্ষা (আমাদের জন্য বাধ্যতামূলক)

MeTube ইউজারের দেওয়া URL yt-dlp-কে দেয়; yt-dlp-র generic extractor **যেকোনো** `http(s)` URL ফেচ করে। গার্ড ছাড়া কেউ `http://169.254.169.254/` (ক্লাউড মেটাডেটা) বা `http://127.0.0.1:8080/` পাঠাতে পারে — এবং রেসপন্স ডাউনলোড ডিরেক্টরিতে গিয়ে সার্ভ হয়।

দুই স্তর:
1. **`validate_url`** (ইনগ্রেস): http/https only · `localhost`/`metadata.google.internal` ব্লক · DNS resolve করে **প্রতিটি** ঠিকানা `is_global` কি না · resolve না হলে **fail closed** · `allow_private` থাকলে ছাড় · `://` না থাকলে (বেয়ার ID, `ytsearch:`) হস্তক্ষেপ নয়।
2. **`install_socket_guard`** (কানেক্ট-টাইম): `socket.getaddrinfo` র্যাপ করে প্রতিটি resolved ঠিকানা পুনঃযাচাই → redirect, DNS rebinding, ম্যানিফেস্ট-ডিরাইভড মিডিয়া URL কভার করে। অপারেটরের কনফিগার করা প্রোক্সি/PO-token endpoint-কে allowlist করা হয় **host-স্ট্রিং মিলিয়ে** (ঠিকানা মিলিয়ে নয় — নাহলে হোস্টাইল URL সুবিধা নিতে পারত)।

যে সূক্ষ্ম ফাঁদগুলো তারা ধরেছে (আমরাও নিয়েছি):
- **IPv4-mapped IPv6**: `::ffff:169.254.169.254` — ভেতরের IPv4 নিজের গুণে বিচার করতে হয়।
- **NAT64 / 6to4 / Teredo / IPv4-compatible**: `64:ff9b::a9fe:a9fe` বাইরে থেকে global ঠিকানা মনে হয়, ভেতরে মেটাডেটা সার্ভার। তাই টানেল করা IPv4-ও আলাদা করে যাচাই।
- **Native resolver ছাড়**: `curl_cffi --impersonate` Python socket এড়িয়ে যায় → গার্ড কাজ করে না; সমাধান নেটওয়ার্ক আইসোলেশন (তারা সেটাই ডকুমেন্ট করে)।

আমাদের পোর্টে যোগ: `ALLOWED_HOSTS` allowlist + `guard_report()` ডায়াগনস্টিক। অসম্ভব-সৎ সীমাবদ্ধতা আমাদের ডকে লেখা আছে: yt-dlp সাবপ্রসেস Python নয়, তাই তার ভেতরে socket guard ইনস্টল করা যায় না — ইনগ্রেস যাচাই + allowlist + কন্টেইনার আইসোলেশনই ভরসা (MeTube-এর ডকুমেন্টেড সীমাবদ্ধতার সমতুল্য)।

---

## ৮. ফ্রন্টএন্ড (কেন আমরা হুবহু নিতে পারি না)

Angular 22 standalone (NgModule নেই) · `inject()` DI · **OnPush change detection + `cdr.markForCheck()`** · RxJS `Subject` স্টেট · `takeUntilDestroyed()` ক্লিনআপ · `ngx-socket-io` · pipes (`eta`, `file-size`, `speed`)।

- এটা সঠিক পছন্দ **ডেস্কটপ/বড় স্ক্রিনের জন্য**; বান্ডেল ~২০০KB+ JS, যা KaiOS-এর ২৫৬MB RAM-এ অ্যাপ স্টার্টআপকে ধীর করে দেয়, আর Gecko 48-এ Angular 22 চলে না (ES2022+ প্রয়োজন)।
- তাই আমাদের `webapp/`: **শূন্য ফ্রেমওয়ার্ক, ES5**, শুধু DOM API। যা আমরা ধার করেছি: স্টেট-সেন্ট্রিক ভিউ (`state.screen` → render ফাংশন), ফর্ম ভ্যালু কুকি-পার্সিস্ট (আমরা `localStorage`-এ কনফিগ), ব্যাচ আপডেট (আমরা সিগনেচার-তুলনা করে রি-রেন্ডার স্কিপ করি)।

---

## ৯. বিল্ড, CI, রিলিজ

- **Dockerfile multi-stage**: Node builder (Angular বিল্ড) → Python runtime; `amd64/arm64` দুই আর্ক।
- **CI (`main.yml`)**: `pnpm lint/build/test` + `python -m compileall app` + `pytest app/tests/`। দুটো গুরুত্বপূর্ণ gotcha তারা ডকে লিখে রেখেছে: বেকএন্ড টেস্ট **রিপো রুট থেকে** চালাতে হবে (স্ট্যাটিক অ্যাসেট পাথ cwd-নির্ভর), আর **ফ্রন্টএন্ড আগে বিল্ড** করতে হবে (আগে নয়তো ৫টি টেস্ট মডিউল ইমপোর্টে ফেল)।
- **`update-ytdl-update.yml`**: নাইটলি yt-dlp বিল্ড — এটি স্বীকার করে যে yt-dlp আপডেট = টিকে থাকার শর্ত।
- **`readme-size.yml`**: README-এর ২৫k সীমা CI-তে এনফোর্স করে (ডকুমেন্টেশনকেও টেস্ট করা যায় — দারুণ আইডিয়া)।

---

## ১০. ম্যাপিং: MeTube → KaiOS Tube (কী নিয়েছি, কী বাদ দিয়েছি, কেন)

| MeTube | আমাদের সমতুল্য | সিদ্ধান্ত |
|---|---|---|
| `Config` (`_DEFAULTS`, `%%`, boolean, `HOST='*'`) | `server/config.py` | ✅ হুবহু ধাঁচ (CLI ওভাররাইড যোগ) |
| `state_store.AtomicJsonStore` | `server/state_store.py` | ✅ পুরো কৌশল + টেস্ট |
| `dl_formats.get_format/get_opts` | `server/formats.py` + `dlqueue.YtdlpRunner.format_selector()` | ✅ KaiOS-স্পেশালাইজড (AVC বাধ্যতামূলক, itag 18 প্রাধান্য) |
| `url_guard.validate_url` + socket guard | `server/url_guard.py` | ✅ + `ALLOWED_HOSTS` |
| `DownloadQueue` (asyncio + semaphore) | `server/dlqueue.py` (threads + worker pool) | ✅ আচরণ একই, ইমপ্লিমেন্টেশন সরল (stdlib) |
| Socket.IO notifier | ২.৫s REST পোলিং (শুধু কিউ স্ক্রিনে) | ⚠️ ইচ্ছাকৃত ডেভিয়েশন — KaiOS-এ WS/socket.io ব্যয়বহুল |
| `/add`, `/delete`, `/start`, `/history` | `/api/downloads`, `/api/downloads/delete`, `/api/downloads/start`, `GET /api/downloads` | ✅ নাম আলাদা, সেমান্টিক্স এক |
| `PUBLIC_HOST_URL` ফাইল সার্ভিং | `/api/file/<id>` (Range/206 + path-containment) | ✅ + KaiOS-এর জন্য seek-critical Range |
| Subscriptions (৪১KB!) | নেই | ❌ স্কোপের বাইরে (ফোনে দরকার নেই; ৪১KB+ কিউ-জটিলতা) |
| Captions/thumbnail download types | আংশিক (`/api/transcript` ডেমো) | ⏳ পরের ধাপ |
| Angular UI | `webapp/` ES5 vanilla | ❌ বাদ (সাইজ + Gecko 48) |
| Multi-stage Docker/multi-arch | নেই (Python-only) | ⏳ দরকার হলে যোগ করা সহজ |
| নাইটলি yt-dlp আপডেট + GracefulExit | `yt-dlp -U` ম্যানুয়াল; ডকুমেন্টেড | ⏳ পরের ধাপ (cron + SIGHUP) |

### API compat টেবিল (ডিভাইস ↔ হোস্ট)

| কাজ | MeTube | আমাদের |
|---|---|---|
| ডাউনলোড যোগ | `POST /add {url, download_type, quality, …}` | `POST /api/downloads {url\|id, height, audio_only}` |
| তালিকা | `GET /history` | `GET /api/downloads` |
| বাতিল/মুছে ফেলা | `POST /delete {ids, where}` | `POST /api/downloads/delete {ids, where}` |
| রিট্রাই | `POST /start {ids}` | `POST /api/downloads/start {ids}` |
| ফাইল | `GET /download/<name>` | `GET /api/file/<id>` (Range/206) |
| আপডেট | Socket.IO | পোল (২.৫s) |

**টপোলজি (একসাথে চালানোর রেসিপি):** (ক) শুধু আমাদের প্রোক্সি — হালকা, streaming + queue দুটোই; (খ) MeTube-কে ভারী ডাউনলোডার হিসেবে আলাদা চালান (Playlist/Subscription/ট্রান্সকোড দরকার হলে), আর আমাদের প্রোক্সি শুধু প্লেব্যাক-প্রোক্সি; ডিভাইস মেমরি বাঁচাতে ডাউনলোড-ফোল্ডার একটাই ভলিউমে শেয়ার করতে পারেন। ভবিষ্যতে `--metube-url` অ্যাডাপ্টার দিয়ে MeTube-র `/add`-এ ডেলিগেট করা যায় (আমাদের `/api/downloads` চুক্তি অপরিবর্তিত রেখে)।

---

## ১১. টেস্ট কভারেজ তুলনা

| বিষয় | MeTube | আমাদের (৭৩ টেস্ট) |
|---|---|---|
| কিউ/স্টেট মেশিন | `test_download_queue.py` ৫০KB | `test_dlqueue.py` — অ্যাসেট, ক্যানসেল, কনকারেন্সি ক্যাপ, রিস্টার্ট-রিকভারি, কমপ্যাকশন |
| state store | `test_state_store.py` ৭.৭KB | `test_state_store.py` — atomic, quarantine, 0600, serialize-fail-safe |
| SSRF | `test_url_guard.py` ১৭.৮KB | `test_url_guard.py` — loopback/RFC1918/NAT64/6to4/allowlist/fail-closed |
| ফরম্যাট | `test_dl_formats.py` ৮KB | `test_formats.py` — itag 18 প্রাধান্য, VP9 বর্জন, bitrate পেনাল্টি, Range/416 |
| HTTP/API | `test_api.py` ২৪.৬KB | `test_proxy_demo.py` — E2E: হেলথ/সার্চ/player/কিউ/ফাইল রেঞ্জ/ট্রাভার্সাল/SSRF |

---

## ১২. ১০টি শিক্ষা (এক নজরে)

1. **প্রোডাক্ট কন্ট্রাক্ট লিখে ফেলুন, কোডের আগে** — `AGENTS.md`-এর "in scope / out of scope" তালিকা PR-ঝগড়া বাঁচায়।
2. **সার্ভারে নামিয়ে তারপর সার্ভ করা** (MeTube মডেল) = IP-lock/PO-token/SABR সমস্যার স্থায়ী সমাধান; streaming সরাসরি করার চেষ্টা সবসময় ভঙ্গুর থাকবে।
3. **পার্সিস্টেন্স atomic হতে হবে** — `mkstemp + fsync + os.replace`, ব্যর্থতায় quarantine।
4. **কনফিগ ওয়ার্ন করবে, চুপচাপ ভুল করবে না** — বুলিয়ান ভুল হলে মরে যাওয়া ভালো।
5. **ইনগ্রেসে SSRF যাচাই অ-নেগোশিয়েবল**, বিশেষ করে যখন ইউজার URL সাবপ্রসেসে/লাইব্রেরিতে যায়।
6. **প্রতি-ডাউনলোড অপশন যোগ করা = ৭ জায়গায় হাত দেওয়া** (তাদের চেকলিস্ট) — নতুন ফিল্ড persist, backfill, UI, retry path, subscription path।
7. **completed থেকে মুছে ফেলা ≠ ফাইল মুছে ফেলা** — ডিফল্টে ফাইল রাখুন।
8. **নাইটলি আপডেট + হট-রিলোড** — YouTube-এর বিরুদ্ধে টিকে থাকার অপারেশনাল শর্ত।
9. **টেস্ট কোড অ্যাপ্লিকেশন কোডের চেয়ে বড় হওয়া স্বাভাবিক** — এ ডোমেইনে।
10. **docs-ও টেস্টেবল** (`readme-size.yml`) — সীমা মেশিনে এনফোর্স করুন, মানুষের স্মৃতিতে নয়।
