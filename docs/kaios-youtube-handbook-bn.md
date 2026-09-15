# KaiOS + Innertube + yt-dlp হ্যান্ডবুক (বাংলা)
**সংস্করণ:** ২০২৬-০৯-১৫ · **প্রেক্ষাপট:** KaiOS ২.৫ / ৩.০ / ৪.x ডিভাইসে YouTube প্লেব্যাক, এবং ব্যাকএন্ডে Innertube + yt-dlp

> আমি এখানে তিনটে ভূমিকা একসাথে পালন করছি: (১) KaiOS webapp ডেভেলপার, (২) yt-dlp অপারেটর, (৩) Innertube (YouTube-এর প্রাইভেট API) রিভার্স-ইঞ্জিনিয়ারিং এক্সপার্ট।
> এই ডকুমেন্টটাই আমাদের "সত্যের একক সূত্র" — কোড (`server/`, `webapp/`) এর সাথে সিন্ক রাখা হয়েছে।

---

## সূচিপত্র
1. [KaiOS প্ল্যাটফর্মের কঠিন সত্য](#১-kaios-প্ল্যাটফর্মের-কঠিন-সত্য)
2. [Innertube API — ২০২৬ সালের বাস্তবতা](#২-innertube-api--২০২৬-সালের-বাস্তবতা)
3. [yt-dlp কুকবুক (KaiOS-এর জন্য টিউনড)](#৩-yt-dlp-কুকবুক-kaios-এর-জন্য-টিউনড)
4. [আর্কিটেকচার — ৪টি অপশন ও ডিসিশন ম্যাট্রিক্স](#৪-আর্কিটেকচার--৪টি-অপশন-ও-ডিসিশন-ম্যাট্রিক্স)
5. [প্লেব্যাক ট্রাবলশুটিং চেকলিস্ট](#৫-প্লেব্যাক-ট্রাবলশুটিং-চেকলিস্ট)
6. [নিরাপত্তা, লাইসেন্স ও টেকসইতা](#৬-নিরাপত্তা-লাইসেন্স-ও-টেকসইতা)
7. [টুলিং: সিমুলেটর, ডিভাইস, ডিবাগ](#৭-টুলিং-সিমুলেটর-ডিভাইস-ডিবাগ)

---

## ১. KaiOS প্ল্যাটফর্মের কঠিন সত্য

KaiOS আসলে একটা **বন্ধ-সোর্স Firefox OS (B2G) ফর্ক** — তাই আমাদের সব সিদ্ধান্ত Gecko ইঞ্জিনের বয়সের ওপর নির্ভর করে।

| বিষয় | KaiOS 2.5 | KaiOS 3.0 | KaiOS 4.x |
|---|---|---|---|
| Gecko ইঞ্জিন | **v48** (Firefox 48, ২০১৬) | **v84** | নতুন রেপো (যাচাই করে নিন) |
| UA-তে | `KAIOS/2.5.3` + `Firefox/48.0` | `KAIOS/3.2` + `Firefox/84.0` | `KAIOS/4.x` |
| অ্যাপ ফরম্যাট | `manifest.webapp` (hosted/privileged/packaged) | `manifest.webmanifest` (PWA-ধাঁচ) | PWA |
| র‍্যাম (সাধারণ) | 256–512 MB | 512 MB | ১ GB |
| স্ক্রিন | 240×320 পোর্ট্রেট | 240×320 / 320×480 | 320×480+ |

**গুরুত্বপূর্ণ রেফারেন্স:** [kaios.dev — History of KaiOS](https://kaios.dev/2023/01/history-of-kaios/), [Deep Dive on KaiOS User Agents](https://kaios.dev/2024/02/deep-dive-on-kaios-user-agents/), [Dos and Don'ts for KaiOS](https://kaios.dev/2023/02/dos-and-donts-for-kaios/)।

### ১.১ ভাষার সীমা (এটাই সবচেয়ে বেশি ভুল হয়)
Gecko 48 = **ES2015 আংশিক + `async/await` নেই**, optional chaining/nullish coalescing নেই:

```js
// ❌ KaiOS 2.5-এ ভাঙবে
const info = await fetch(url).then(r => r.json());
const h = info?.video?.height ?? 0;

// ✅ সব ভার্সনে চলবে (ES5 + Promise + XHR)
function getJSON(url, cb) {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', url, true);
  xhr.onload = function () {
    if (xhr.status >= 200 && xhr.status < 300) { cb(null, JSON.parse(xhr.responseText)); }
    else { cb(new Error('HTTP ' + xhr.status)); }
  };
  xhr.onerror = function () { cb(new Error('network')); };
  xhr.send();
}
```
- `fetch` আছে (Gecko ≥39), কিন্তু XHR ব্যবহার করলে KaiOS-এ ডিবাগিং সহজ এবং abort/টাইমআউট নিয়ন্ত্রণ ভালো।
- `Array.prototype.flat`, `String.replaceAll`, `Object.fromEntries`, `??`, `?.` — **নিষিদ্ধ** (Firefox 62/77/63/72/74-এ এসেছে)।
- `Promise`, `let/const`, arrow function, template literal, `class` — **চলে** (Gecko 48-এ আছে)।

### ১.২ মেমরি ও স্টোরেজ
- অ্যাপ প্যাকেজ: KaiStore-এ সর্বোচ্চ **20 MB** (গড় 2–4 MB)। প্রতিটি অ্যাসেট 240×320 মাপে রিসাইজ করুন।
- `localStorage` কোটা **~5 MB**; `sessionStorage` ডিস্ক-ব্যাকড ও অ্যাপ বন্ধ হলে মোছে → টোকেন/ক্যাশের জন্য আদর্শ।
- `IndexedDB`-তে কোটার এনফোর্সমেন্ট নেই — বেশি ভরলে ডিভাইস অস্বাভাবিক আচরণ করবে।
- র‍্যাম কম, তাই অ্যাপের ঘোষিত মেমরি (`manifest` → `memory`) বাড়াবেন না; ভিডিও বাফারে `<video preload="metadata">` দিন, `autoplay` নয়।

### ১.৩ সার্টিফিকেট ফাঁদ (খুব কম ডেভলপার জানে)
KaiOS 2.5 ডিভাইসে OTA আপডেট বন্ধ থাকায় **Let's Encrypt-এর নতুন রুট (ISRG X1) ট্রাস্ট-লিস্টে নেই** → যেকোনো LE সার্টিফিকেট "Insecure" দেখাবে এবং অ্যাপের নেটওয়ার্ক কল ফেল করবে। ব্যাকএন্ড রাখুন **Cloudflare / ACM (Amazon) / Google Trust Services**-এর সার্টিফিকেটে। HTTPS ছাড়া অনেক API (XHR/fetch) কাজই করবে না।

### ১.৪ ইনপুট মডেল (D-pad + সফটকি)
`manifest.webapp`-এ `"cursor": false` দিন, তারপর নিজে key event হ্যান্ডল করুন:

| ফিজিক্যাল | `event.key` | মন্তব্য |
|---|---|---|
| OK / সেন্টার | `Enter` | সিলেক্ট |
| D-pad | `ArrowUp/Down/Left/Right` | স্ক্রল/ফোকাস |
| বাঁ সফটকি | `SoftLeft` | "Back/Options" |
| ডান সফটকি | `SoftRight` | prime/side ডিভাইসে থাকে বা থাকে না |
| Back/End | `Backspace` | স্ক্রিন পপ; শেষে `history.back()` |

```js
document.addEventListener('keydown', function (ev) {
  switch (ev.key) {
    case 'ArrowUp':   move(-1); break;
    case 'ArrowDown': move(1);  break;
    case 'Enter':     select(); break;
    case 'SoftLeft':  goBack(); break;
    case 'SoftRight': openOptions(); break;
    case 'Backspace': goBack(); break;   // Back key
    default: return;
  }
  ev.preventDefault();   // ← না দিলে দ্রুত প্রেসে স্ক্রল/ডাবল-অ্যাকশন হবে
}, false);
```
- **ফোকাস ভিজিবল রাখুন:** `.focused` ক্লাসে `outline`/বর্ডার — KaiOS-এ `:focus` অনেক সময় আন্ডার-স্টাইল থাকে।
- টাচস্ক্রিন KaiOS (Smart Touch) আলাদা ডিভাইস-ক্লাস; D-pad-ই ডিফল্ট ধরে নিন।

### ১.৫ কোডেক ও প্লেব্যাক
KaiOS-এর সাপোর্টেড ফরম্যাট প্রায়: **MP4 (H.264/AVC + AAC-LC)**, 3GP/H.263, MP3, AMR, Ogg(সীমিত)। **VP8/VP9/AV1 = ঝুঁকি**, এবং **MSE (Media Source Extensions) সাধারণত নেই** ⇒ DASH adaptive stream অ্যাপে সরাসরি চালানো যায় না।
প্র্যাকটিসে: **≤360p (itag 18) সবচেয়ে নিরাপদ**, 480p সীমার কাছাকাছি, 720p (itag 22) শুধু শক্তিশালী ডিভাইসে।

---

## ২. Innertube API — ২০২৬ সালের বাস্তবতা

Innertube = `https://www.youtube.com/youtubei/v1/*` (ও মিরর `youtubei.googleapis.com`)। মূল এন্ডপয়েন্ট:

| এন্ডপয়েন্ট | কাজ | নোট |
|---|---|---|
| `/search?key=…` | সার্চ, suggestion-প্যারাম সহ | `params` দিয়ে ফিল্টার |
| `/browse?key=…` | হোম/চ্যানেল/প্লেলিস্ট | `browseId` (যেমন `FEwhat_to_watch`) |
| `/player?key=…` | `streamingData`, `videoDetails` | এখানেই PO-token/SABR যুদ্ধ |
| `/next?key=…` | রিলেটেড, কমেন্ট continuation | কম মেমরিতে দরকার নেই |
| `/get_transcript?key=…` | সাবটাইটেল সময়সহ | `params` দরকার |

সব রিকোয়েস্টে থাকে `context` — যেখানে `client.clientName` + `client.clientVersion` সবচেয়ে গুরুত্বপূর্ণ:

```json
{
  "context": {
    "client": {
      "clientName": "ANDROID_VR",
      "clientVersion": "1.62.27",
      "deviceMake": "Oculus", "deviceModel": "Quest 3",
      "osName": "Android", "osVersion": "12",
      "androidSdkVersion": 32, "hl": "bn", "gl": "BD"
    },
    "user": { "lockedSafetyMode": false }
  },
  "videoId": "…",
  "contentCheckOk": true,
  "racyCheckOk": true
}
```

### ২.১ ক্লায়েন্ট সিলেকশন — ২০২৬-এর অবস্থা
YouTube এখন **SABR** (server-side adaptive bitrate) চালু করেছে এবং **PO Token** (proof-of-origin) বাধ্যতামূলক করছে। ফলাফল:
- এমন ক্লায়েন্ট বেছে নিন যার **PO Token লাগে না**: `ANDROID_VR`, `TVHTML5_SIMPLY_EMBEDDED_PLAYER` (TV_SIMPLY), `IOS`/`VISIONOS` (পরে), `MWEB` (টোকেন লাগে)।
- `WEB` ক্লায়েন্ট এখন প্রায়ই `streamingData` ছাড়া ফেরে (SABR) → ব্রাউজার/Innertube-only অ্যাপে 403/ফরম্যাট-শূন্য।
- `ANDROID` (পুরোনো `19.xx`) বেশিরভাগ ভিডিওতে ব্লকড/ডিপ্রিকেটেড → yt-dlp-ও `-android_sdkless` এক্সক্লুড করে।
- **টোকেন লাগলে**, `poToken` ফিল্ড `context.client` বা serviceIntegrityDimensions-এ পাঠাতে হয়, নাহলে ফরম্যাট URL দিলেও 403।

👉 আমাদের কোডে **fallback chain** রাখা হয়েছে (নিচে §৪, এবং `server/innertube.py`): `ANDROID_VR → TV_SIMPLY → TV (HTML5) → IOS → WEB_EMBEDDED → MWEB`। প্রথম যেটা `streamingData` দেয়, সেটাই ব্যবহার হবে।

### ২.১.১ পরিবেশ ভেরিয়েবল (আমাদের কোডে যেভাবে টোকেন/ভিজিটর ঢোকানো হয়)
| ভেরিয়েবল | কাজ | উদাহরণ |
|---|---|---|
| `YT_PO_TOKEN` | PO Token (gvs বা player কনটেক্সট) | `web.gvs+AbC…` / `web.player+AbC…` |
| `YT_VISITOR_DATA` | ক্লায়েন্টের ভিজিটর আইডি (টোকেন এর সাথে বাঁধা) | `Cgto…` |
| `YT_API_KEY` | Innertube key (না দিলে ওয়াচ-পেজের ফলব্যাক key) | `AIza…` |
| `YT_CLIENTS_JSON` | ক্লায়েন্ট চেইন বদলাতে (কোড রিলিজ ছাড়াই) | `[{"name":"TV",…}]` |
| `YTDLP_BIN` / `FFMPEG_BIN` | বাইনারি পাথ | `/usr/local/bin/yt-dlp` |
| `YT_COOKIES` | `--cookies` ফাইল পাথ (chmod 600, কখনো কমিট নয়) | `/etc/kaios-tube/cookies.txt` |
| `INNERTUBE_TIMEOUT` | টাইমআউট সেকেন্ড | `12` |

gvs-টোকেন আপস্ট্রিম URL-এ লাগে (`pot=` প্যারাম), player-টোকেন রিকোয়েস্ট বডিতে
`serviceIntegrityDimensions.poToken` হিসেবে যায় — কোড দুটোই হ্যান্ডল করে।

### ২.২ CORS — ব্রাউজার থেকে সরাসরি কল করা যাবে?
`youtubei.googleapis.com` সাধারণত `Content-Type: application/json` POST-এ preflight (OPTIONS) চায় এবং সব origin-কে সম্মতি না দিতে পারে; এছাড়া API key + client context ক্লায়েন্টেই থাকলে কেউ সেটা চুরি করতে পারে। তাই:
- **ডেভ/সিমুলেটরে** সরাসরি কল করে দেখা যায় (কিছু ডিভাইসে `app://` origin-এ কাজ করে, অনেকটায় নয়)।
- **প্রোডাকশনে সবসময় নিজের প্রোক্সি** (Cloudflare Worker / Python backend) ব্যবহার করুন — এটি একইসাথে CORS, IP-lock এবং গোপনীয়তা সমাধান করে।

### ২.৩ IP-lock: এর চেয়ে বড় ফাঁদ আর নেই
`streamingData`-র `googlevideo.com` URL **যে IP রিকোয়েস্ট করেছে তার সাথে বাঁধা** (কখনো UA-তেও)। অর্থাৎ সার্ভার URL বের করে ফোনে পাঠালে ফোন থেকে প্লে হবে না → 403।
**সমাধান:** URL ফোনে না দিয়ে **বাইট প্রোক্সি** করুন — ফোন `https://your-server/api/stream/<id>` কল করবে, সার্ভার `Range` হেডার সহ googlevideo-তে ফরওয়ার্ড করে ২০৬ রেসপন্স স্ট্রিম করবে।
আমাদের `server/proxy.py` ঠিক এটাই করে (Range/206 pass-through, `Accept-Ranges: bytes`)।

### ২.৪ দরকারি হেডার
```
POST https://www.youtube.com/youtubei/v1/player?key=<INNERTUBE_API_KEY>&prettyPrint=false
Content-Type: application/json
X-YouTube-Client-Name: 28            # ANDROID_VR; ক্লায়েন্টভেদে আলাদা (TVHTML5=7)
X-YouTube-Client-Version: 1.62.27
Origin: https://www.youtube.com
Accept-Language: bn-BD,bn;q=0.9,en;q=0.8
```
`INNERTUBE_API_KEY` হার্ডকোডড পুরোনো কপি (`AIzaSyAO_FJ2Slq…`) এখনো চলে, কিন্তু সবচেয়ে ভালো: `https://www.youtube.com/sw.js_data` বা ওয়াচ-পেজ থেকে লাইভ key স্ক্র্যাপ করে ক্যাশে করা (২৪ ঘণ্টা TTL)।

---

## ৩. yt-dlp কুকবুক (KaiOS-এর জন্য টিউনড)

২০২৬-এর yt-dlp ডিফল্ট `player_client = visionos,web`; `tv_downgraded` ও `web_embedded` fallback হিসেবে যোগ হয়। কয়েকটি বাধ্যতামূলক বিষয়:

1. **JS রানটাইম অবশ্যই লাগবে** — `yt-dlp-ejs` প্যাকেজ + `deno`/`node`/`bun`/`quickjs`। এটা ছাড়া nsig/challenge decipher হবে না → ফরম্যাট URL ভাঙা আসবে।
2. **নিয়মিত আপডেট** — `yt-dlp -U` বা `--update-to nightly`; YouTube-এর সাথে এটা দৌড় প্রতিযোগিতা।
3. **PO Token লাগলে** `--extractor-args "youtube:player_client=web;po_token=web.gvs+XXX"` অথবা `fetch_pot=auto` + provider plugin (bgutil)।
4. **`ffmpeg` বাইনারি** (pip প্যাকেজ নয়) mux/remux-এর জন্য জরুরি।

### ৩.১ KaiOS-এর জন্য ফরম্যাট সিলেকশন
```bash
# ১) শুধু প্লেয়েবল прогреসিভ MP4 (দ্রুততম, ডিস্ক লাগে না)
yt-dlp -f "18/22/b[ext=mp4]/b" --no-playlist -g "https://youtu.be/VIDEO_ID"

# ২) KaiOS-এর জন্য সার্ভার-সাইড mux → stdout স্ট্রিম (সবচেয়ে স্থিতিশীল)
yt-dlp \
  --extractor-args "youtube:player_client=default,tv,tv_simply,tv_downgraded,-android_sdkless" \
  --no-playlist --no-part --quiet --no-warnings \
  -f "bv*[vcodec^=avc1][height<=360]+ba[ext=m4a]/b[ext=mp4][height<=360]/b[height<=360]" \
  --merge-output-format mp4 \
  -o - "https://youtu.be/VIDEO_ID"        # stdout → HTTP পাইপ

# ৩) শুধু মেটাডেটা/ফরম্যাট টেবিল (ডায়াগনস্টিক)
yt-dlp -F --extractor-args "youtube:player_client=tv" "https://youtu.be/VIDEO_ID"

# ৪) সাবটাইটেল (vn->বাংলা অনুবাদসহ) — কোডেক-মুক্ত টেক্সট
yt-dlp --skip-download --write-auto-subs --sub-langs "bn,en" --convert-subs srt \
  -o "%(id)s.%(ext)s" "https://youtu.be/VIDEO_ID"

# ৫) লাইভ স্ট্রিম → KaiOS-বান্ধব ফ্র্যাগমেন্টেড টুকরা
yt-dlp -f 93/94/95/b --hls-prefer-native --live-from-start -o - "URL"
```

> **আমাদের বাস্তব অভিজ্ঞতা:** KaiOS 2.5-এ `itag 18` (360p AVC+AAC, প্রগ্রেসিভ) দিয়ে সফলতার হার সবচেয়ে বেশি। DASH (`137+140`) মোবাইলে চালানো যায় না (MSE নেই), তাই হোস্টে ffmpeg-মার্জ করে progressive MP4 দিতে হবে — অথবা ফোনে ডাউনলোড করে সেভ করে দেখতে হবে।

### ৩.২ অপারেশনাল ফ্ল্যাগ
```bash
--extractor-args "youtube:player_client=…"   # ক্লায়েন্ট নিয়ন্ত্রণ
--no-playlist                                # প্লেলিস্টে দুর্ঘটনা এড়ায়
--no-part --no-mtime --no-cache-dir          # কন্টেইনার-বান্ধব
--socket-timeout 15 --retries 5 --fragment-retries 5
--limit-rate 1M                              # কাইওএস বাফারিং = ব্যান্ডউইথ স্পাইক
--cookies cookies.txt                        # লগইন-সীমিত ভিডিও (সাবধানে!)
--geo-bypass-country BD
--extractor-args "youtube:formats=missing_pot"  # টোকেন-বিহীন ক্লায়েন্টকে প্রাধান্য
```
`cookies.txt` **কখনো** ফ্রন্টএন্ডে/রিপোতে রাখবেন না — শুধু সার্ভারের এনভায়রনমেন্ট বা সিক্রেটে, ফাইল পারমিশন 600।

---

## ৪. আর্কিটেকচার — ৪টি অপশন ও ডিসিশন ম্যাট্রিক্স

### অপশন A — ক্লায়েন্ট-অনলি Innertube (সবচেয়ে হালকা, সবচেয়ে ভঙ্গুর)
`webapp/` → সরাসরি `youtubei` POST → `<video src=googlevideo>`।
➕ জিরো সার্ভার খরচ, দ্রুত ডেপ্লয়। ➖ CORS/403/IP-lock-এ প্রায়ই ভাঙবে।

### অপশন B — Worker প্রোক্সি (রেকমেন্ডেড MVP)
Cloudflare Worker: `/api/search`, `/api/player`, `/api/stream/<id>` — Innertube + Range-প্রোক্সি।
➕ ফ্রি টিয়ারে চলে, গ্লোবাল এজ, LE-বহির্ভূত CA, key লুকানো। ➖ Worker-এ ffmpeg নেই, তাই মার্জ করা যায় না (itag 18/22-এর ওপর নির্ভরতা)।

### অপশন C — yt-dlp ব্যাকএন্ড (একমাত্র "সব ভিডিও চলে" পথ)
VPS/কন্টেইনার: FastAPI/stdlib HTTP সার্ভার + yt-dlp + ffmpeg + JS রানটাইম।
➕ PO-token/SABR-এর বিরুদ্ধে সবচেয়ে শক্তিশালী, যেকোনো রেজোলিউশন, ডাউনলোড/সেভ, সাবটাইটেল।
➖ সার্ভার খরচ, IP ব্যান-ঝুঁকি (রেসিডেন্সিয়াল প্রোক্সি/rotating cookie দরকার হতে পারে), রক্ষণাবেক্ষণ।

### অপশন D — হাইব্রিড (যা আমি প্রোডাকশনে করি)
1. ফোন → নিজের প্রোক্সি (B/C) | 2. প্রোক্সি আগে Innertube (দ্রুত; কিছুই ডিস্কে যায় না) | 3. `streamingData` খালি/403 হলে → yt-dlp পাইপ (C) | 4. সব ফ্রন্টএন্ডে **রange-প্রোক্সি করা URL**, কখনো googlevideo URL নয় | 5. `sessionStorage`-এ ৩০ মিনিট মেটাডেটা ক্যাশ।

| মাপকাঠি | A | B | C | D |
|---|---|---|---|---|
| যেকোনো ভিডিও চলে | ✗ | ~60% | ✓ | ✓ |
| সার্ভার খরচ | 0 | ≈0 | $$$ | $$ |
| ব্যান-ঝুঁকি | কম | মাঝারি | উচ্চ | মাঝারি |
| বানানো সহজ | ★★★ | ★★ | ★ | ★ |
| KaiOS-এ স্থিতিশীলতা | ★ | ★★ | ★★★ | ★★★ |

আমাদের স্ক্যাফোল্ড: `server/proxy.py` (B-এর স্ট্রাকচার, কিন্তু Python-এ = C/D-এর হোস্ট) + `server/ytdlp_bridge.py` (ঐচ্ছিক C) + `webapp/` (A-এর ক্লায়েন্ট, প্রোক্সি-রেডি)।

---

## ৫. প্লেব্যাক ট্রাবলশুটিং চেকলিস্ট

| লক্ষণ | কারণ | সমাধান |
|---|---|---|
| `video` কালো/loading-এ আটকে | googlevideo URL IP-locked, বা `403` | সার্ভার-সাইড Range প্রোক্সি (§২.৩) |
| `MEDIA_ERR_SRC_NOT_SUPPORTED` (4) | VP9/AV1 বা DASH অডিও-ভিডিও আলাদা | `vcodec^=avc1` + `ext=m4a`, progressive MP4 |
| ৫-১০ সেকেন্ড পরে থেমে যায় | কম মেমরি/বাফার | `--limit-rate`, `preload="metadata"`, 360p |
| সার্চ খালি আসে | `context` অসম্পূর্ণ বা SABR ক্লায়েন্ট | `ANDROID_VR`/`TV_SIMPLY`-তে fallback chain |
| "Sign in to confirm you're not a bot" | ডেটাসেন্টার IP + PO token নেই | `tv` ক্লায়েন্ট, cookies, বা রেসিডেন্সিয়াল প্রোক্সি |
| অ্যাপ ইনস্টল হয় না | manifest/permission ভুল | `manifest.webapp` ভ্যালিডেট, `type: hosted` |
| HTTPS সতর্কবার্তা | Let's Encrypt (KaiOS 2.5) | Cloudflare/ACM/GTS সার্টিফিকেট |
| Back কী-তে অ্যাপ বন্ধ | `Backspace` হ্যান্ডেল করা হয়নি | `keydown`-এ `Backspace` → নিজের নেভিগেশন |

ডায়াগনস্টিক স্ক্রিপ্ট (রিপোতে আছে):
```bash
python3 server/proxy.py --port 8080 --demo      # অফলাইন ডেমো (নেটওয়ার্ক ছাড়াই চলে)
python3 server/proxy.py --port 8080             # লাইভ: Innertube → yt-dlp fallback
python3 -m pytest tests -q                       # ফরম্যাট-সিলেকশন ও Range লজিক টেস্ট
```

---

## ৬. নিরাপত্তা, লাইসেন্স ও টেকসইতা

- **YouTube ToS:** পাবলিক কন্টেন্ট স্ট্রিম/ডাউনলোড টুল অফিসিয়ালি অনুমোদিত নয়; ব্যক্তিগত/পরীক্ষামূলক ব্যবহারে সীমাবদ্ধ রাখুন, DRM-প্রোটেক্টেড কন্টেন্ট এড়িয়ে চলুন, 광고 ব্লক করে মনিটাইজেশন করবেন না। KaiStore-এ YouTube-ব্র্যান্ডেড অ্যাপ সাধারণত রিজেক্ট হয় — নিউট্রাল ব্র্যান্ডিং ("Video Player", "Tube Lite") ব্যবহার করুন এবং নিজের আইনি ঝুঁকি বিবেচনা করুন।
- **DRM-এ হাত দেবেন না** — yt-dlp DRM ডিক্রিপ্ট করে না, আমরাও করব না।
- **API key**: ক্লায়েন্টে হার্ডকোড নয়; সার্ভার-সাইড env + ঘোরানো।
- **ক্যাশিং**: মেটাডেটা ৩০ মিনিট, স্ট্রিম **কখনোই** ডিস্কে ক্যাশ নয় (ডিস্ক/ব্যান্ডউইথ); `Cache-Control: no-store` স্ট্রিমে।
- **রেট লিমিট**: প্রতি ডিভাইসে ২–৩ concurrent স্ট্রিম; `429` পেলে exponential backoff।
- **টেকসইতা**: `player_client` স্কিমা প্রতি ৪–৮ সপ্তাহে বদলায় → কনফিগ-ফাইল থেকে ক্লায়েন্ট চেইন পড়ুন, কোড রিলিজ ছাড়াই বদলাতে পারবেন (আমরা `server/clients.py`-তে সেটা রেখেছি)।

---

## ৭. টুলিং: সিমুলেটর, ডিভাইস, ডিবাগ

1. **KaiOS Simulator** (KaiOS Tech developer portal) — ওয়েবটুলে hosted app URL দিয়ে ইনস্টল; D-pad ম্যাপিং কি-বোর্ড অ্যারো।
2. **বাস্তব ডিভাইস**: 256MB (যেমন Nokia 8110/2720-শ্রেণি) ও 512MB — দুটোতেই টেস্ট করুন।
3. **ডিবাগ**: এনাবলড ডিভাইসে `adb`/WebIDE; `about:config`-এ `devtools.*`; সরাসরি `console.log` স্ক্রিনে (অ্যাপে একটি `?debug=1` ওভারলে আছে)।
4. **পার্ফ মাপকাঠি**: প্রথম ফ্রেম < ৩ সে, মেমরি < ৪০ MB, প্যাকেজ < ৪ MB।
5. **নেটওয়ার্ক সিমুলেশন**: 2G/3G থ্রটলে টেস্ট — বাংলাদেশ/ভারতের বাস্তব কন্ডিশন।

---

### উৎস (২০২৬-এ যাচাই করা)
- kaios.dev — History of KaiOS, User Agents, Dos/Don'ts (Gecko 48 vs 84, 5MB কোটা, 20MB প্যাকেজ, LE সার্টিফিকেট সমস্যা)
- yt-dlp Wiki `extractors` — ডিফল্ট `visionos,web`, ক্লায়েন্ট তালিকা, PO Token গাইড
- yt-dlp PO-Token/ SABR সংক্রান্ত সাম্প্রতিক ইস্যু (#12482) ও ২০২৬ রিলিজ নোট
- KaiOS Tech help center — সাপোর্টেড ভিডিও/অডিও ফরম্যাট
