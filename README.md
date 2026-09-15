# KaiOS Tube — KaiOS webapp + Innertube + yt-dlp

KaiOS (২.৫ / ৩.০ / ৪.x) ফিচার-ফোনের জন্য একটি হালকা YouTube প্লেয়ার, তার সাথে একটি
**Innertube প্রোক্সি** এবং **yt-dlp ফালব্যাক ব্যাকএন্ড**। পুরো ডকুমেন্টেশন বাংলায় আছে
(`docs/kaios-youtube-handbook-bn.md`) — এটাই এই রিপোর "এক্সপার্ট গাইড"।

```
webapp/     KaiOS hosted app (ES5-only, D-pad + সফটকি, 240×320)
server/     stdlib-only Python প্রোক্সি: Innertube (fallback chain) + Range প্রোক্সি + yt-dlp পাইপ
docs/       বাংলা হ্যান্ডবুক + Cloudflare Worker বিকল্প (Innertube প্রোক্সি)
tools/      আইকন জেনারেটর (শূন্য ডিপেন্ডেন্সি)
tests/      ইউনিট + এন্ড-টু-এন্ড টেস্ট (নেটওয়ার্ক ছাড়াই চলে)
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
| `GET /api/health` | মোড, ক্লায়েন্ট চেইন, yt-dlp/ffmpeg/JS-রানটাইম অবস্থা |
| `GET /api/search?q=…` | Innertube সার্চ (উপরে ছেঁটে হালকা JSON) |
| `GET /api/player?id=…&h=360` | মেটাডেটা + নির্বাচিত স্ট্রিম (`/api/stream/…`) + কেন বাছা হল তার ব্যাখ্যা |
| `GET /api/stream/<id>?h=…` | Range/206 পাস-থ্রু (Innertube ডিরেক্ট, নইলে yt-dlp পাইপ) |
| `GET /api/resolve?id=…` | ডায়াগনস্টিক: সব ক্লায়েন্টের ফরম্যাট টেবিল |

## পরিবেশ ভেরিয়েবল (গুরুত্বপূর্ণ)

```bash
export YT_PO_TOKEN="web.gvs+XXX"     # PO Token (২০২৬-এ প্রায়ই দরকার) — ডক §২.১.১
export YT_VISITOR_DATA="Cgto..."     # টোকেন একই ভিজিটরের সাথে বাঁধা
export YT_COOKIES=/etc/kaios-tube/cookies.txt   # chmod 600, কখনো কমিট নয়
export YT_CLIENTS_JSON='[{"name":"TV","clientName":"TVHTML5","clientVersion":"7.20250205.16.00"}]'
export YTDLP_BIN=/usr/local/bin/yt-dlp
```

## ডিপ্লয়

- **KaiOS**: `webapp/` ফোল্ডার যেকোনো HTTPS হোস্টে (Cloudflare Pages) রাখুন → KaiOS Simulator/WebIDE-এ `manifest.webapp` URL দিয়ে ইনস্টল। `manifest.webmanifest` KaiOS 3.0-এর জন্য।
- **প্রোক্সি**: VPS/কন্টেইনার (Python 3.8+), অথবা `docs/worker-inertube-proxy.js` → Cloudflare Worker (ffmpeg নেই, তাই শুধু প্রগ্রেসিভ ফরম্যাট)।
- **গোপনীয়তা**: `cookies.txt` ও API key কখনো কমিট করবেন না (`.gitignore` দেখুন)।

## দায়মুক্তি

Innertube একটি অনানুষ্ঠানিক API এবং YouTube-এর শর্তাবলি অনুযায়ী পাবলিক কন্টেন্ট ডাউনলোড/স্ট্রিমিং সীমাবদ্ধ।
এই কোডটি শিক্ষামূলক ও ব্যক্তিগত ব্যবহারের জন্য; DRM কন্টেন্ট সমর্থিত নয়। KaiStore-এ YouTube ব্র্যান্ড ব্যবহার করে
জমা দিলে রিজেক্ট হতে পারে — আপনার দায়িত্ব আপনার।
