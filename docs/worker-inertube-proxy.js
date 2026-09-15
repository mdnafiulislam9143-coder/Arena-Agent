/**
 * Cloudflare Worker — Innertube বিনামূল্যের প্রোক্সি (আর্কিটেকচার অপশন B)
 *
 * কেন Worker:
 *   ১) KaiOS 2.5-এ Let's Encrypt সার্টিফিকেট "insecure" দেখায় — Cloudflare-এর সার্টিফিকেট ট্রাস্টেড।
 *   ২) youtubei.googleapis.com-এ ব্রাউজার থেকে সরাসরি POST করলে CORS/preflight সমস্যা হয়।
 *   ৩) googlevideo URL-এর IP-lock: ফোনকে কখনো raw URL দেব না, বরং EDGE থেকে Range প্রোক্সি করব।
 *
 * ডেপ্লয়:  npx wrangler deploy docs/worker-inertube-proxy.js --name kaios-tube --compatibility-date=2026-01-01
 * রুট:
 *   GET /api/health
 *   GET /api/search?q=…
 *   GET /api/player?id=…
 *   GET /api/stream/<videoId>     (Range/206 পাস-থ্রু)
 *
 * সীমা: Worker-এ ffmpeg নেই → শুধু progressive (itag 18/22) ফরম্যাট পাওয়া গেলেই চলে।
 *       adaptive-only ভিডিওর জন্য server/proxy.py (yt-dlp) ব্যবহার করুন।
 */

const BASE = "https://www.youtube.com/youtubei/v1";
const FALLBACK_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8";

/** ২০২৬-এর নিরাপদ চেইন: বিনা PO Token-এ যেগুলো এখনো ফরম্যাট দেয় */
const CLIENTS = [
  {
    name: "ANDROID_VR",
    clientName: "ANDROID_VR",
    clientVersion: "1.62.27",
    clientId: "28",
    userAgent: "com.google.android.apps.youtube.vr.oculus/1.62.27 (Linux; U; Android 12; GB) gzip",
    extra: { deviceMake: "Oculus", deviceModel: "Quest 3", osName: "Android", osVersion: "12", androidSdkVersion: 32 },
  },
  {
    name: "TV_SIMPLY",
    clientName: "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
    clientVersion: "2.0",
    clientId: "85",
    userAgent: "Mozilla/5.0 (PlayStation; PlayStation 4/12.00) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
    extra: { clientScreen: "EMBED" },
  },
  {
    name: "TV",
    clientName: "TVHTML5",
    clientVersion: "7.20250205.16.00",
    clientId: "7",
    userAgent: "Mozilla/5.0 (ChromiumStylePlatform) Cobalt/25.lts.1.0-qa (unlike Gecko) gles Starboard/16 YouTubeTV/7.20250205.16.00",
    extra: {},
  },
];

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,HEAD,OPTIONS",
  "Access-Control-Allow-Headers": "Range,Content-Type",
};

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS },
  });

function context(client, hl, gl, poToken) {
  const c = { clientName: client.clientName, clientVersion: client.clientVersion, hl, gl, ...client.extra };
  if (poToken) c.poToken = poToken;
  return { context: { client: c, user: { lockedSafetyMode: false } } };
}

async function innertube(endpoint, payload, client, hl, gl, key) {
  const resp = await fetch(`${BASE}/${endpoint}?key=${key}&prettyPrint=false`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-YouTube-Client-Name": client.clientId,
      "X-YouTube-Client-Version": client.clientVersion,
      "User-Agent": client.userAgent,
      Origin: "https://www.youtube.com",
      "Accept-Language": `${hl},en;q=0.8`,
    },
    body: JSON.stringify({ ...context(client, hl, gl), ...payload }),
  });
  if (!resp.ok) throw new Error(`innertube ${endpoint} → HTTP ${resp.status}`);
  return resp.json();
}

async function playerWithChain(videoId, hl, gl, key) {
  const failures = [];
  for (const client of CLIENTS) {
    try {
      const data = await innertube(
        "player",
        { videoId, contentCheckOk: true, racyCheckOk: true, params: "CgIQBg==" },
        client, hl, gl, key,
      );
      const has = data?.streamingData?.formats?.length || data?.streamingData?.adaptiveFormats?.length;
      if (has) return { data, client: client.name };
      failures.push(`${client.name}: ফরম্যাট নেই (SABR/PO Token)`);
    } catch (err) {
      failures.push(`${client.name}: ${err.message}`);
    }
  }
  throw new Error(failures.join(" | ") || "সব ক্লায়েন্ট ব্যর্থ");
}

/** KaiOS-বান্ধব ফরম্যাট: single-file MP4 (H.264+AAC) = itag 18/22 */
function pickProgressive(formats = []) {
  const muxed = formats.filter((f) => f.audioQuality && /avc1|avc3/.test(f.mimeType || "") && /mp4/.test(f.mimeType));
  if (!muxed.length) return null;
  muxed.sort((a, b) => {
    const itagA = a.itag === 18 ? -100 : 0;
    const itagB = b.itag === 18 ? -100 : 0;
    return itagA - itagB || (b.height || 0) - (a.height || 0);
  });
  return muxed[0];
}

function texts(node, out = []) {
  if (node && typeof node === "object") {
    if (node.simpleText) out.push(node.simpleText);
    else if (node.runs) node.runs.forEach((r) => out.push(r.text || ""));
    else Object.values(node).forEach((v) => texts(v, out));
  }
  return out.join("");
}

function parseSearch(resp) {
  const items = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.videoRenderer) {
      const v = node.videoRenderer;
      if (v.videoId) {
        items.push({
          id: v.videoId,
          title: texts(v.title),
          channel: texts(v.ownerText || v.longBylineText),
          duration: texts(v.lengthText),
          views: texts(v.shortViewCountText),
        });
      }
      return;
    }
    Object.values(node).forEach(walk);
  };
  walk(resp);
  return items.slice(0, 20);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = env?.YT_API_KEY || FALLBACK_KEY;
    const hl = url.searchParams.get("hl") || "bn";
    const gl = url.searchParams.get("gl") || "BD";

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    try {
      if (url.pathname === "/api/health") {
        return json({ ok: true, mode: "worker", clients: CLIENTS.map((c) => c.name), max_height: 360 });
      }

      if (url.pathname === "/api/search") {
        const q = url.searchParams.get("q");
        if (!q) return json({ ok: false, error: "q দিন" }, 400);
        const data = await innertube("search", { query: q, params: "EgIQAQ%3D%3D" }, CLIENTS[0], hl, gl, key);
        return json({ ok: true, items: parseSearch(data) });
      }

      if (url.pathname === "/api/player") {
        const videoId = url.searchParams.get("id") || "";
        if (!/^[\w-]{11}$/.test(videoId)) return json({ ok: false, error: "বৈধ ১১ অক্ষরের video id দিন" }, 400);
        const { data, client } = await playerWithChain(videoId, hl, gl, key);
        const fmt = pickProgressive(data.streamingData?.formats || []);
        return json({
          ok: true,
          client,
          video: {
            id: videoId,
            title: data.videoDetails?.title,
            author: data.videoDetails?.author,
            duration: Number(data.videoDetails?.lengthSeconds || 0),
          },
          // ফোন raw googlevideo URL পাবে না (IP-lock) — শুধু আমাদের প্রোক্সি পাথ
          stream: {
            url: `/api/stream/${videoId}`,
            mode: fmt ? "progressive" : "none",
            itag: fmt?.itag ?? null,
            height: fmt?.height ?? 0,
            mime: fmt ? "video/mp4" : null,
            kaios_safe: Boolean(fmt),
            reason: fmt ? "প্রগ্রেসিভ MP4 — MSE ছাড়া চলে" : "শুধু adaptive ফরম্যাট; yt-dlp ব্যাকএন্ড দরকার",
          },
        });
      }

      if (url.pathname.startsWith("/api/stream/")) {
        const videoId = url.pathname.split("/").pop();
        if (!/^[\w-]{11}$/.test(videoId)) return json({ ok: false, error: "bad id" }, 400);
        const { data } = await playerWithChain(videoId, hl, gl, key);
        const fmt = pickProgressive(data.streamingData?.formats || []);
        if (!fmt?.url) return json({ ok: false, error: "প্রগ্রেসিভ ফরম্যাট নেই", hint: "yt-dlp ব্যাকএন্ড ব্যবহার করুন" }, 409);

        const upstream = await fetch(fmt.url, {
          headers: {
            "User-Agent": CLIENTS[0].userAgent,
            Range: request.headers.get("Range") || "bytes=0-",
            Origin: "https://www.youtube.com",
          },
        });
        const headers = new Headers(CORS);
        headers.set("Content-Type", "video/mp4");
        headers.set("Accept-Ranges", "bytes");
        headers.set("Cache-Control", "no-store");
        for (const h of ["Content-Range", "Content-Length", "ETag"]) {
          if (upstream.headers.get(h)) headers.set(h, upstream.headers.get(h));
        }
        return new Response(request.method === "HEAD" ? null : upstream.body, {
          status: upstream.status === 206 ? 206 : 200,
          headers,
        });
      }

      return json({ ok: false, error: "রুট পাওয়া যায়নি" }, 404);
    } catch (err) {
      return json({ ok: false, error: String(err?.message || err), hint: "ক্লায়েন্ট চেইন/টোকেন দেখুন" }, 502);
    }
  },
};
