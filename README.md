# KaiOS Tube — KaiOS webapp + Innertube + yt-dlp

KaiOS (২.৫ / ৩.০ / ৪.x) ফিচার-ফোনের জন্য একটি হালকা YouTube প্লেয়ার, তার সাথে একটি
**Innertube প্রোক্সি** এবং **yt-dlp ফালব্যাক ব্যাকএন্ড**। পুরো ডকুমেন্টেশন বাংলায় আছে
(`docs/kaios-youtube-handbook-bn.md`) — এটাই এই রিপোর "এক্সপার্ট গাইড"।

```
webapp/     KaiOS hosted app (ES5-only, D-pad + সফটকি, 240×320) — সার্চ, প্লেয়ার, ডাউনলোড কিউ
server/     stdlib-only Python সার্ভার
  ├── proxy.py       HTTP রাউট, Range/206 স্ট্রিমিং, স্ট্যাটিক অ্যাসেট
  ├── dlqueue.py     ডাউনলোড কিউ (MeTube মডেল) — worker pool + persistence + cancel/retry
  ├── config.py      env-ড্রিভেন Config (MeTube-এর _DEFAULTS/%% প্যাটার্ন)
  ├── state_store.py AtomicJsonStore (atomic rename + fsync + quarantine + 0600)
  ├── url_guard.py   SSRF গার্ড (loopback/RFC1918/NAT64 → ব্লক, fail-closed)
  ├── innertube.py   Innertube fallback chain + PO Token সাপোর্ট
  ├── formats.py     KaiOS-নিরাপদ ফরম্যাট বাছাই (H.264/itag 18) + Range লজিক
  └── ytdlp_bridge.py CLI ফালব্যাক (pipe/stream/প্রোব/সেভ)
docs/       বাংলা হ্যান্ডবুক · MeTube আর্কিটেকচার বিশ্লেষণ · Cloudflare Worker বিকল্প
tools/      আইকন জেনারেটর (শূন্য ডিপেন্ডেন্সি)
tests/      ৭৩টি টেস্ট (unittest, নেটওয়ার্ক ছাড়াই চলে)
```

## ৬০ সেকেন্ডে চালান

```bash
# ১) নেটওয়ার্ক ছাড়াই UI টেস্ট (ডেমো ডেটা)
python3 server/proxy.py --port 8080 --demo
#    ব্রাউজারে খুলুন → http://localhost:8080
#    কীবোর্ড ম্যাপিং: Arrow = D-pad · Enter = OK · [ = SoftLeft · ] = SoftRight · Backspace = Back

# ২) লাইভ মোড (Innertube, তারপর দরকার হলে yt-dlp)
pip install -U yt-dlp            # + ffmpeg বাইনারি, + JS রানটাইম (deno/node) — ডক §৩ দেখুন
python3 server/proxy.py --port 8080 --hl bn --gl BD --max-height 360

# ৩) টেস্ট
python3 -m unittest discover -s tests -v
```

## কেন এই আর্কিটেকচার (সংক্ষেপে)

| সমস্যা | সমাধান |
|---|---|
| KaiOS-এ MSE/DASH নেই, VP9 চলে না | শুধু প্রগ্রেসিভ MP4 (H.264+AAC) — `formats.py` নিরাপদ itag বাছাই করে |
| googlevideo URL **IP-locked** | ফোন কখনো raw URL পায় না; `/api/stream/<id>` দিয়ে Range/206 প্রোক্সি |
| ২০২৬-এ WEB/IOS ক্লায়েন্ট ফরম্যাট দেয় না (SABR + PO Token) | ক্লায়েন্ট fallback chain: `ANDROID_VR → TV_SIMPLY → TV → IOS → WEB_EMBEDDED → MWEB` |
| adaptive-only ভিডিও | `--ytdlp auto` → yt-dlp `bv*[vcodec^=avc1][height<=360]+ba[ext=m4a]` → ffmpeg mux → stdout পাইপ |
| ২৫৬MB RAM, ২.৫MB স্টোরেজ কোটা | ES5 কোড, কোনো ফ্রেমওয়ার্ক নেই, মেটাডেটা ৩০ মিনিট ক্যাশ, `sessionStorage`-বান্ধব কনফিগ |
| KaiOS 2.5-এ Let's Encrypt "insecure" | ব্যাকএন্ড Cloudflare/ACM/GTS সার্টিফিকেটে রাখুন (ডক §১.৩) |

## API (প্রোক্সি)

| রুট | কাজ |
|---|---|
| `GET /api/health` | মোড, ক্লায়েন্ট চেইন, কিউ কাউন্টার, yt-dlp/ffmpeg/JS-রানটাইম অবস্থা |
| `GET /api/search?q=…` | Innertube সার্চ (উপরে ছেঁটে হালকা JSON) |
| `GET /api/player?id=…&h=360` | মেটাডেটা + নির্বাচিত স্ট্রিম (`/api/stream/…`) + কেন বাছা হল তার ব্যাখ্যা |
| `GET /api/stream/<id>?h=…` | **ডাউনলোড করা ফাইল থাকলে সেটাই** (Range/206, seek কাজ করে), নইলে Innertube ডিরেক্ট → yt-dlp |
| `POST /api/downloads` | `{url\|id, height?, audio_only?}` → সার্ভারে ডাউনলোড কিউতে যোগ (SSRF-যাচাই সহ) |
| `GET /api/downloads` | `{queue, completed, stats}` — KaiOS-বান্ধব পোলিং (socket.io নয়) |
| `POST /api/downloads/delete` | `{ids, where: queue\|done}` — বাতিল / completed থেকে সরানো |
| `POST /api/downloads/start` | `{ids?}` — এরর/পেন্ডিং আইটেম রিট্রাই |
| `GET /api/file/<id>` | সম্পন্ন ফাইল Range/206 সহ (path-containment যাচাই করা) |
| `GET /api/resolve?id=…` | ডায়গনস্টিক: সব ক্লায়েন্টের ফরম্যাট টেবিল |

### কেন "সার্ভারে নামাও, তারপর সার্ভ করো" (MeTube মডেল)
স্ট্রিমিং-অনলি পথে তিনটি সমস্যা একসাথে আসে: googlevideo URL-এর IP-lock, PO-token/SABR-এ ফরম্যাট বাদ পড়া, আর KaiOS-এ MSE না থাকায় adaptive স্ট্রিম। ডাউনলোড করে ফেললেই তিনটাই শেষ: ফাইল সার্ভারে, Range/206 দিয়ে seek-সহ প্লেব্যাক, ডিভাইসের RAM/ডেটা বাঁচে, আর `completed.json`-এ কিউ বেঁচে থাকে রিস্টার্টেও। বিস্তারিত: `docs/metube-architecture-bn.md`।

## পরিবেশ ভেরিয়েবল (MeTube-এর নামগুলোই, তাই চেনা লাগবে)

```bash
# --- ডিরেক্টরি ও কিউ (MeTube-সামঞ্জস্যপূর্ণ নাম) ---
export DOWNLOAD_DIR=./downloads          # ডাউনলোড কোথায় যাবে
export TEMP_DIR='%DOWNLOAD_DIR/tmp'      # %NAME ইনডিরেকশনও কাজ করে (MeTube-এর %% প্যাটার্ন)
export STATE_DIR='%DOWNLOAD_DIR/.state'  # queue.json / completed.json (atomic লেখা)
export MAX_CONCURRENT_DOWNLOADS=2        # ২৫৬MB ডিভাইস + হোস্ট বাঁচাতে ২
export DELETE_FILE_ON_TRASHCAN=false     # completed থেকে মুছলে ফাইল থাকবে (ডিফল্ট)
export OUTPUT_TEMPLATE='%(id)s.%(ext)s'
# --- সার্ভার ---
export HOST=0.0.0.0 PORT=8080 LOGLEVEL=INFO
# --- yt-dlp / Innertube ---
export YTDLP_MODE=auto                   # auto | off | force
export YTDLP_FORMAT_HEIGHT=360           # KaiOS সিলিং
export YTDLP_EXTRACTOR_ARGS='youtube:player_client=default,tv,tv_simply,tv_downgraded,ios,-android_sdkless'
export COOKIES=/etc/kaios-tube/cookies.txt      # chmod 600, কখনো কমিট নয়
export YT_PO_TOKEN="web.gvs+XXX"         # PO Token (২০২৬-এ প্রায়ই দরকার) — ডক §২.১.১
export YT_VISITOR_DATA="Cgto..."         # টোকেন একই ভিজিটরের সাথে বাঁধা
export YT_CLIENTS_JSON='[{"name":"TV","clientName":"TVHTML5","clientVersion":"7.20250205.16.00"}]'
export YTDLP_BIN=/usr/local/bin/yt-dlp
# --- নিরাপত্তা (SSRF) ---
export ALLOWED_HOSTS=                # ফাঁকা = যেকোনো গ্লোবাল হোস্ট; কমা-দিয়ে allowlist
export ALLOW_PRIVATE_ADDRESSES=false # true → লোকাল/প্রাইভেট ঠিকানায় ছাড় (Fake-IP/প্রক্সি সেটআপ)
```

CLI ফ্ল্যাগ থাকলে সেটাই জেতে: `--download-dir`, `--state-dir`, `--max-concurrent`, `--socket-guard`, `--demo`, `--ytdlp` ইত্যাদি।

## ডিপ্লয়

- **KaiOS**: `webapp/` ফোল্ডার যেকোনো HTTPS হোস্টে (Cloudflare Pages) রাখুন → KaiOS Simulator/WebIDE-এ `manifest.webapp` URL দিয়ে ইনস্টল। `manifest.webmanifest` KaiOS 3.0-এর জন্য।
- **প্রোক্সি**: VPS/কন্টেইনার (Python 3.8+), অথবা `docs/worker-inertube-proxy.js` → Cloudflare Worker (ffmpeg নেই, তাই শুধু প্রগ্রেসিভ ফরম্যাট)।
- **গোপনীয়তা**: `cookies.txt` ও API key কখনো কমিট করবেন না (`.gitignore` দেখুন)।

## দায়মুক্তি

Innertube একটি অনানুষ্ঠানিক API এবং YouTube-এর শর্তাবলি অনুযায়ী পাবলিক কন্টেন্ট ডাউনলোড/স্ট্রিমিং সীমাবদ্ধ।
এই কোডটি শিক্ষামূলক ও ব্যক্তিগত ব্যবহারের জন্য; DRM কন্টেন্ট সমর্থিত নয়। KaiStore-এ YouTube ব্র্যান্ড ব্যবহার করে
জমা দিলে রিজেক্ট হতে পারে — আপনার দায়িত্ব আপনার।
