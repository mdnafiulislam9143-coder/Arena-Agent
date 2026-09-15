/* KaiOS Tube — ফ্রন্টএন্ড (ES5-only, কোনো ফ্রেমওয়ার্ক নেই)
 *
 * কেন ES5: KaiOS 2.5 = Gecko 48 (Firefox 48, 2016) → optional chaining/nullish coalescing নেই,
 * Array.flat/String.replaceAll নেই। এখানে সেগুলোর একটাও ব্যবহার করা হয়নি।
 * নেভিগেশন: sendiri key handler (D-pad + সফটকি), কারণ manifest-এ "cursor": false দেওয়া আছে।
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ কনফিগ */
  var CFG_KEY = 'kaiosTube.cfg.v1';
  var RECENT_KEY = 'kaiosTube.recent.v1';
  var DEFAULTS = { server: '', maxHeight: 360, demo: false, hl: 'bn', gl: 'BD' };

  function loadJSON(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      if (!raw) { return fallback; }
      var obj = JSON.parse(raw);
      return obj || fallback;
    } catch (e) { return fallback; }
  }
  function saveJSON(key, obj) {
    try { window.localStorage.setItem(key, JSON.stringify(obj)); } catch (e) { /* কোটা ভরেছে */ }
  }

  var cfg = loadJSON(CFG_KEY, {});
  var i;
  for (i in DEFAULTS) {
    if (typeof cfg[i] === 'undefined') { cfg[i] = DEFAULTS[i]; }
  }
  var recent = loadJSON(RECENT_KEY, []);

  /* -------------------------------------------------------------------- DOM */
  function $(id) { return document.getElementById(id); }
  var elTitle = $('title');
  var elBadge = $('badge');
  var elScreen = $('screen');
  var elLeft = $('sk-left');
  var elRight = $('sk-right');
  var elCenter = $('sk-center');

  /* ------------------------------------------------------------------ স্টেট */
  var state = {
    screen: 'home',
    focus: 0,
    rows: [],
    items: [],          /* সার্চ ফলাফল */
    query: '',
    video: null,        /* /api/player পেলোড */
    quality: cfg.maxHeight,
    health: null,
    busy: false,
    status: ''
  };

  var videoEl = null;
  var progressTimer = null;

  function api(path, cb) {
    var url = (cfg.server ? cfg.server.replace(/\/+$/, '') : '') + path;
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.timeout = 20000;
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (xhr.status >= 200 && xhr.status < 300) {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { return cb(new Error('JSON ভাঙা'), null); }
        cb(null, data);
      } else {
        var msg = 'HTTP ' + (xhr.status || 0);
        try {
          var j = JSON.parse(xhr.responseText);
          if (j && j.error) { msg = j.error + (j.hint ? ' — ' + j.hint : ''); }
        } catch (e2) { /* ignore */ }
        cb(new Error(msg), null);
      }
    };
    xhr.ontimeout = function () { cb(new Error('সময় শেষ — সার্ভার ঠিকানা/নেটওয়ার্ক দেখুন'), null); };
    xhr.onerror = function () { cb(new Error('নেটওয়ার্ক ত্রুটি — সার্ভার চালু আছে? (সেটিংসে ঠিকানা দেখুন)'), null); };
    xhr.send();
  }

  /* --------------------------------------------------------------- হেল্পার */
  function setSoftkeys(left, center, right) {
    elLeft.textContent = left || '';
    elCenter.textContent = center || '';
    elRight.textContent = right || '';
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function tag(name, cls, text) {
    var n = document.createElement(name);
    if (cls) { n.className = cls; }
    if (text !== null && typeof text !== 'undefined') { n.textContent = text; }
    return n;
  }
  function fmtDur(sec) {
    sec = parseInt(sec || 0, 10);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    function p(x) { return (x < 10 ? '0' : '') + x; }
    return (h > 0 ? h + ':' + p(m) + ':' + p(s) : m + ':' + p(s));
  }
  function setStatus(text) {
    state.status = text || '';
    var node = $('pstat') || $('statusline');
    if (node) { node.textContent = state.status; }
  }

  /* -------------------------------------------------------- স্ক্রিন: হোম */
  function renderHome() {
    clear(elScreen);
    state.rows = [];

    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'KaiOS Tube — হোম'));
    var st = tag('div', 'hint', state.health
      ? ('সার্ভার: ' + (state.health.mode === 'demo' ? 'ডেমো মোড (নেটওয়ার্ক ছাড়া)' : 'লাইভ') +
         ' · সর্বোচ্চ ' + state.health.max_height + 'p · yt-dlp: ' +
         (state.health.ytdlp && state.health.ytdlp.ready ? 'প্রস্তুত' : 'নেই'))
      : 'সার্ভারের অবস্থা জানা যাচ্ছে…');
    wrap.appendChild(st);
    elScreen.appendChild(wrap);

    addRow('🔍 সার্চ করুন', 'নাম লিখে ভিডিও খুঁজুন', function () { go('search'); });
    if (recent.length) {
      for (var r = 0; r < Math.min(recent.length, 3); r++) {
        (function (q) {
          addRow('🕘 ' + q, 'আগের সার্চ', function () { state.query = q; doSearch(q); });
        })(recent[r]);
      }
    }
    addRow('📥 লিংক/ID দিয়ে চালান', 'youtu.be/… বা ১১ অক্ষরের ID পেস্ট করুন', function () { go('link'); });
    addRow('⚙ সেটিংস', 'সার্ভার ঠিকানা, গুণমান, ভাষা', function () { go('settings'); });
    addRow('❓ সহায়তা', 'KaiOS-এ কেন ভিডিও নাও চলতে পারে', function () { go('help'); });

    setSoftkeys('বেরিয়ে যান', '', 'রিফ্রেশ');
    focus(0);
  }

  function addRow(title, sub, onOk, extraClass) {
    var row = tag('div', 'row' + (extraClass ? ' ' + extraClass : ''));
    var meta = tag('div', 'meta');
    meta.appendChild(tag('div', 't', title));
    if (sub) { meta.appendChild(tag('div', 's', sub)); }
    row.appendChild(meta);
    row.addEventListener('click', onOk);
    elScreen.appendChild(row);
    state.rows.push({ node: row, onOk: onOk });
    return row;
  }

  function focus(idx) {
    if (!state.rows.length) { state.focus = 0; return; }
    if (idx < 0) { idx = state.rows.length - 1; }
    if (idx >= state.rows.length) { idx = 0; }
    for (var r = 0; r < state.rows.length; r++) {
      if (r === idx) { state.rows[r].node.className = state.rows[r].node.className.replace(/ ?focused/, '') + ' focused'; }
      else { state.rows[r].node.className = state.rows[r].node.className.replace(/ ?focused/, ''); }
    }
    state.focus = idx;
    var node = state.rows[idx].node;
    var top = node.offsetTop, bottom = top + node.offsetHeight;
    if (top < elScreen.scrollTop) { elScreen.scrollTop = top - 2; }
    else if (bottom > elScreen.scrollTop + elScreen.clientHeight) {
      elScreen.scrollTop = bottom - elScreen.clientHeight + 2;
    }
  }

  /* ------------------------------------------------------ স্ক্রিন: সার্চ */
  function renderSearch() {
    clear(elScreen);
    state.rows = [];
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'ভিডিও খুঁজুন'));
    var input = document.createElement('input');
    input.type = 'text';
    input.value = state.query || '';
    input.placeholder = 'যেমন: বাংলা গান';
    input.id = 'q';
    if (cfg.demo) { input.value = 'বাংলা গান (ডেমো)'; }
    wrap.appendChild(input);
    wrap.appendChild(tag('div', 'hint',
      'OK চাপলে ফোনের কীবোর্ড খুলবে · লিখে শেষে SoftRight ("খুঁজুন") চাপুন'));
    var b = tag('div', 'btn', 'খুঁজুন');
    b.id = 'btnSearch';
    wrap.appendChild(b);
    wrap.appendChild(tag('div', 'hint', 'টিপস: ইনপুট ফাঁকা রাখলে ডেমো সার্চ চলবে।'));
    elScreen.appendChild(wrap);

    input.addEventListener('focus', function () { markInputFocused(input, b); });
    input.addEventListener('blur', function () { markInputFocused(input, b); });
    b.addEventListener('click', function () { submitSearch(input.value); });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { submitSearch(input.value); ev.preventDefault(); }
    });

    state.rows = [
      { node: input, onOk: function () { input.focus(); } },
      { node: b, onOk: function () { submitSearch(input.value); } }
    ];
    setSoftkeys('ফিরে যান', '', 'খুঁজুন');
    focus(0);
    input.focus();
  }

  function markInputFocused(input, button) {
    var active = document.activeElement === input;
    input.className = active ? 'focused' : '';
    button.className = 'btn' + (active ? '' : ' focused');
  }

  function submitSearch(q) {
    q = (q || '').replace(/^\s+|\s+$/g, '');
    if (!q) {
      if (cfg.demo) { q = 'বাংলা গান (ডেমো)'; } else { setStatus('আগে কিছু লিখুন'); return; }
    }
    state.query = q;
    recent.unshift(q);
    recent = recent.slice(0, 5);
    saveJSON(RECENT_KEY, recent);
    doSearch(q);
  }

  function doSearch(q) {
    go('loading');
    state.screen = 'loading';
    api('/api/search?q=' + encodeURIComponent(q), function (err, data) {
      if (err) { toastError(err.message); go('home'); return; }
      state.items = (data && data.items) || [];
      go('results');
    });
  }

  /* ---------------------------------------------------- স্ক্রিন: ফলাফল */
  function renderResults() {
    clear(elScreen);
    state.rows = [];
    if (!state.items.length) {
      var w = tag('div', 'pad');
      w.appendChild(tag('h2', null, 'কিছু পাওয়া যায়নি'));
      w.appendChild(tag('div', 'warn', 'কারণ হতে পারে: ইন্টারনেট নেই, locale ভুল, বা YouTube সার্চ ব্লক করেছে। সেটিংসে ঠিকানা/ডেমো মোড দেখুন।'));
      elScreen.appendChild(w);
      setSoftkeys('ফিরে যান', '', 'আবার সার্চ');
      return;
    }
    for (var i = 0; i < state.items.length; i++) {
      (function (item) {
        var row = tag('div', 'row');
        var thumb = tag('div', 'thumb', '▶');
        if (item.thumb) {
          var img = document.createElement('img');
          img.src = item.thumb;
          img.alt = '';
          img.onerror = function () { };
          thumb.appendChild(img);
        }
        row.appendChild(thumb);
        var meta = tag('div', 'meta');
        meta.appendChild(tag('div', 't', item.title || '(শিরোনাম নেই)'));
        meta.appendChild(tag('div', 's',
          (item.channel || '') + (item.duration ? ' · ' + item.duration : '') +
          (item.views ? ' · ' + item.views : '')));
        row.appendChild(meta);
        row.addEventListener('click', function () { openVideo(item.id); });
        elScreen.appendChild(row);
        state.rows.push({ node: row, onOk: function () { openVideo(item.id); } });
      })(state.items[i]);
    }
    elTitle.textContent = '"' + state.query + '" — ফলাফল';
    setSoftkeys('ফিরে যান', '', 'নতুন সার্চ');
    focus(0);
  }

  /* ------------------------------------------------- স্ক্রিন: ভিডিও লোড */
  function openVideo(idOrUrl) {
    go('loading');
    api('/api/player?id=' + encodeURIComponent(idOrUrl) + '&h=' + state.quality,
      function (err, data) {
        if (err) { toastError(err.message); go('results'); return; }
        state.video = data;
        go('player');
      });
  }

  /* ------------------------------------------------------ স্ক্রিন: প্লেয়ার */
  function renderPlayer() {
    clear(elScreen);
    var v = state.video || {};
    var wrap = tag('div', '');
    wrap.id = 'playerWrap';

    videoEl = document.createElement('video');
    videoEl.setAttribute('playsinline', 'playsinline');
    videoEl.setAttribute('preload', 'metadata');
    videoEl.controls = false;
    var streamUrl = v.stream ? v.stream.url : '';
    videoEl.src = (cfg.server ? cfg.server.replace(/\/+$/, '') : '') + streamUrl + '?h=' + state.quality;
    videoEl.addEventListener('error', onVideoError);
    videoEl.addEventListener('timeupdate', updateProgress);
    videoEl.addEventListener('playing', function () { setStatus('চলছে · ' + Math.round(videoEl.volume * 100) + '% ভলিউম'); });

    wrap.appendChild(videoEl);
    var info = tag('div', '', v.video ? v.video.title : '');
    info.id = 'pinfo';
    wrap.appendChild(info);
    var stat = tag('div', '', 'প্রস্তুত হচ্ছে… (ক্লায়েন্ট: ' + (v.client || '?') + ' · ' +
      (v.stream ? v.stream.mode : '?') + ' · ' + (v.stream ? v.stream.height : '?') + 'p)');
    stat.id = 'pstat';
    wrap.appendChild(stat);
    var bar = tag('div', ''); bar.id = 'bar';
    var fill = tag('div', ''); fill.id = 'barFill';
    bar.appendChild(fill);
    wrap.appendChild(bar);
    elScreen.appendChild(wrap);
    elScreen.scrollTop = 0;

    if (v.stream && v.stream.reason) { setStatus(v.stream.reason); }

    /* প্লেব্যাক শুরু: KaiOS-এ autoplay নিষিদ্ধ, তাই ইউজার-অ্যাকশনে (Enter) কল করি */
    try {
      var p = videoEl.play();
      if (p && p['catch']) { p['catch'](function () { setStatus('চালাতে OK চাপুন'); }); }
    } catch (e) { setStatus('চালাতে OK চাপুন'); }

    elTitle.textContent = 'প্লেয়ার';
    setSoftkeys('ফিরে যান', 'OK = Play/Pause', 'অপশন');
  }

  function onVideoError() {
    var err = videoEl ? videoEl.error : null;
    var code = err ? err.code : 0;
    var map = {
      1: 'লোড করা থামানো হয়েছে (ABORTED)',
      2: 'নেটওয়ার্ক ত্রুটি — প্রোক্সি সার্ভার বন্ধ বা Range কাজ করছে না',
      3: 'ডিকোডিং ব্যর্থ — ফাইল নষ্ট অথবা ২৫৬MB ডিভাইসে বেশি রেজোলিউশন',
      4: 'ফরম্যাট সাপোর্টেড নয় — সম্ভবত VP9/AV1 বা DASH। ৩৬০p H.264 দরকার'
    };
    setStatus('ত্রুটি: ' + (map[code] || ('অজানা (' + code + ')')) +
      ' | SoftRight → "360p-এ নামান" চেষ্টা করুন');
  }

  function updateProgress() {
    var fill = $('barFill');
    if (!fill || !videoEl || !videoEl.duration) { return; }
    fill.style.width = Math.min(100, (videoEl.currentTime / videoEl.duration) * 100) + '%';
  }

  function playerOptions() {
    clear(elScreen);
    state.rows = [];
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'অপশন'));
    elScreen.appendChild(wrap);

    addRow('রেজোলিউশন: ' + state.quality + 'p', 'KaiOS-এ ২৪০/৩৬০ সবচেয়ে নিরাপদ', function () {
      state.quality = state.quality === 360 ? 240 : (state.quality === 240 ? 480 : 360);
      cfg.maxHeight = state.quality; saveJSON(CFG_KEY, cfg);
      setStatus('রেজোলিউশন ' + state.quality + 'p — পুনরায় লোড হচ্ছে');
      renderPlayer();
    });
    addRow('📥 ডাউনলোড করে ফোনে সেভ', 'অফলাইনে দেখতে (KaiOS-এ সবচেয়ে নির্ভরযোগ্য)', downloadStart);
    addRow('🔁 রিলোড', 'মেটাডেটা ও স্ট্রিম আবার আনুন', function () {
      var vid = state.video && state.video.video ? state.video.video.id : '';
      state.video = null; openVideo(vid);
    });
    addRow('ℹ স্ট্রিম তথ্য', state.video && state.video.stream
      ? (state.video.stream.mode + ' · itag ' + state.video.stream.itag + ' · ' + state.video.stream.mime)
      : '', function () { setStatus(state.video && state.video.stream ? state.video.stream.reason : ''); });
    addRow('← প্লেয়ারে ফিরুন', '', function () { go('player'); });

    setSoftkeys('প্লেয়ার', '', 'বন্ধ করুন');
    focus(0);
  }

  /* -------------------------------------------------------- ডাউনলোড ফ্লো */
  function downloadStart() {
    var v = state.video;
    if (!v || !v.stream || !v.stream.url) { return; }
    var url = (cfg.server ? cfg.server.replace(/\/+$/, '') : '') + v.stream.url + '?h=' + state.quality;
    clear(elScreen);
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'ডাউনলোড হচ্ছে…'));
    var line = tag('div', 'hint', '০%');
    wrap.appendChild(line);
    wrap.appendChild(tag('div', 'hint', 'বড় ভিডিওতে সময় লাগবে; অ্যাপ বন্ধ করবেন না।'));
    var cancel = tag('div', 'btn', 'বাতিল করুন');
    wrap.appendChild(cancel);
    elScreen.appendChild(wrap);
    setSoftkeys('বাতিল', '', '');

    var xhr = new XMLHttpRequest();
    xhr.open('GET', url, true);
    xhr.responseType = 'arraybuffer';
    xhr.onprogress = function (ev) {
      if (ev.lengthComputable) {
        line.textContent = Math.round((ev.loaded / ev.total) * 100) + '% (' +
          Math.round(ev.loaded / 1024) + ' KB)';
      } else {
        line.textContent = Math.round(ev.loaded / 1024) + ' KB';
      }
    };
    xhr.onload = function () {
      if (xhr.status < 200 || xhr.status >= 300) {
        line.textContent = 'ব্যর্থ: HTTP ' + xhr.status;
        return;
      }
      saveToDevice(v.video.id, xhr.response, line);
    };
    xhr.onerror = function () { line.textContent = 'নেটওয়ার্ক ত্রুটি'; };
    cancel.addEventListener('click', function () {
      try { xhr.abort(); } catch (e) { }
      go('player');
    });
    state.abortDownload = function () { try { xhr.abort(); } catch (e) { } };
    xhr.send();
  }

  function saveToDevice(videoId, buffer, line) {
    var blob = new Blob([buffer], { type: 'video/mp4' });
    var name = 'KaiosTube-' + videoId + '.mp4';

    /* KaiOS (B2G) DeviceStorage — manifest.webapp-এ device-storage:videos readwrite দরকার */
    if (navigator.getDeviceStorage) {
      var store = null;
      try { store = navigator.getDeviceStorage('videos'); } catch (e) { store = null; }
      if (!store) { try { store = navigator.getDeviceStorage('sdcard'); } catch (e2) { store = null; } }
      if (store) {
        var req = store.addNamed(blob, name);
        req.onsuccess = function () { line.textContent = 'সেভ হয়েছে: ' + name + ' (Videos ফোল্ডার)'; };
        req.onerror = function () {
          line.textContent = 'সেভ ব্যর্থ: ' + (req.error && req.error.name) +
            ' — অনুমতি (device-storage:videos) আছে কি?';
        };
        return;
      }
    }
    /* ফলব্যাক: ব্রাউজার ডাউনলোড */
    try {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      line.textContent = 'ডাউনলোড শুরু: ' + name;
    } catch (e) {
      line.textContent = 'সেভ করা যায়নি (' + e.message + ')';
    }
  }

  /* ----------------------------------------------------- স্ক্রিন: লিংক */
  function renderLink() {
    clear(elScreen);
    state.rows = [];
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'লিংক বা ভিডিও ID'));
    var input = document.createElement('input');
    input.type = 'text';
    input.id = 'q';
    input.value = '';
    input.placeholder = 'youtu.be/XXXXXXXXXXX';
    wrap.appendChild(input);
    wrap.appendChild(tag('div', 'hint', 'OK → কীবোর্ড · SoftRight → চালান'));
    var b = tag('div', 'btn', 'চালান');
    b.addEventListener('click', function () { openVideo(input.value); });
    wrap.appendChild(b);
    elScreen.appendChild(wrap);
    state.rows = [
      { node: input, onOk: function () { input.focus(); } },
      { node: b, onOk: function () { openVideo(input.value); } }
    ];
    setSoftkeys('ফিরে যান', '', 'চালান');
    focus(0);
    input.focus();
  }

  /* -------------------------------------------------- স্ক্রিন: সেটিংস */
  function renderSettings() {
    clear(elScreen);
    state.rows = [];
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'সেটিংস'));
    wrap.appendChild(tag('div', 'hint', 'সার্ভার ফাঁকা রাখলে একই হোস্ট (KaiOS Tube প্রোক্সি) ব্যবহার হবে।'));
    var srv = document.createElement('input');
    srv.type = 'url';
    srv.value = cfg.server || '';
    srv.placeholder = 'https://আপনার-প্রোক্সি.example.com';
    wrap.appendChild(srv);
    elScreen.appendChild(wrap);

    var demoBtn = tag('div', 'btn', 'ডেমো মোড: ' + (cfg.demo ? 'চালু' : 'বন্ধ'));
    elScreen.appendChild(demoBtn);
    var q360 = tag('div', 'btn', 'সর্বোচ্চ গুণমান: ' + cfg.maxHeight + 'p');
    elScreen.appendChild(q360);
    var saveBtn = tag('div', 'btn', 'সংরক্ষণ করুন');
    elScreen.appendChild(saveBtn);
    var note = tag('div', 'hint', 'ভাষা: ' + cfg.hl + ' · অঞ্চল: ' + cfg.gl);
    elScreen.appendChild(note);

    var step = function () {
      cfg.maxHeight = cfg.maxHeight === 360 ? 240 : (cfg.maxHeight === 240 ? 480 : 360);
      state.quality = cfg.maxHeight;
      q360.textContent = 'সর্বোচ্চ গুণমান: ' + cfg.maxHeight + 'p';
    };
    var toggleDemo = function () {
      cfg.demo = !cfg.demo;
      demoBtn.textContent = 'ডেমো মোড: ' + (cfg.demo ? 'চালু' : 'বন্ধ');
    };
    var commit = function () {
      cfg.server = srv.value.replace(/^\s+|\s+$/g, '');
      saveJSON(CFG_KEY, cfg);
      state.quality = cfg.maxHeight;
      setStatus('সেটিংস সংরক্ষিত');
      checkHealth(function () { go('home'); });
    };

    srv.addEventListener('focus', function () { srv.className = 'focused'; });
    srv.addEventListener('blur', function () { srv.className = ''; });
    demoBtn.addEventListener('click', toggleDemo);
    q360.addEventListener('click', step);
    saveBtn.addEventListener('click', commit);
    srv.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { commit(); ev.preventDefault(); } });

    state.rows = [
      { node: srv, onOk: function () { srv.focus(); } },
      { node: demoBtn, onOk: toggleDemo },
      { node: q360, onOk: step },
      { node: saveBtn, onOk: commit }
    ];
    setSoftkeys('ফিরে যান', '', 'সংরক্ষণ');
    focus(0);
  }

  /* ---------------------------------------------------- স্ক্রিন: সহায়তা */
  function renderHelp() {
    clear(elScreen);
    state.rows = [];
    var wrap = tag('div', 'pad');
    wrap.appendChild(tag('h2', null, 'কেন ভিডিও নাও চলতে পারে?'));
    var lines = [
      '১) কোডেক: KaiOS-এ VP9/AV1 সাধারণত চলে না। তাই H.264 (AVC) + AAC-LC দরকার — itag 18 (360p) সবচেয়ে নিরাপদ।',
      '২) DASH/MSE: KaiOS-এ Media Source Extensions নেই, তাই ভিডিও+অডিও আলাদা স্ট্রিম সরাসরি চলবে না। সার্ভারে ffmpeg দিয়ে mux করতে হবে।',
      '৩) IP-lock: googlevideo URL যে IP-তে তৈরি, সেই IP থেকেই চালাতে হয় — তাই এই অ্যাপ URL না দিয়ে প্রোক্সি (/api/stream/…) ব্যবহার করে।',
      '৪) PO Token/SABR: ২০২৬-এ WEB/IOS ক্লায়েন্ট প্রায়ই ফরম্যাট দেয় না। সার্ভারে yt-dlp (tv क्लायंट) চালালে সমস্যা কমে।',
      '৫) HTTPS: KaiOS 2.5-এ Let\'s Encrypt সার্টিফিকেট "insecure" দেখায় — Cloudflare/ACM/GTS ব্যবহার করুন।',
      '৬) মেমরি: ২৫৬MB ডিভাইসে ৩৬০p-র বেশি খারাপ বাফার করে; ২৪০p-এ নামান (SoftRight → অপশন)।',
      '৭) Back কী: Backspace চাপলে আগের স্ক্রিনে ফেরা হয় — হার্ডওয়্যার Back হ্যান্ডেল করা আছে।'
    ];
    for (var i = 0; i < lines.length; i++) {
      wrap.appendChild(tag('div', 'hint', lines[i]));
    }
    wrap.appendChild(tag('div', 'warn',
      'সতর্কতা: KaiStore-এ YouTube নাম/লোগো ব্যবহার করে অ্যাপ জমা দেওয়া সাধারণত রিজেক্ট হয়; ব্যক্তিগত ব্যবহারে রাখাই নিরাপদ।'));
    elScreen.appendChild(wrap);
    setSoftkeys('ফিরে যান', '', 'সেটিংস');
    elTitle.textContent = 'সহায়তা';
  }

  /* ----------------------------------------------------------- নেভিগেশন */
  function go(name) {
    if (state.screen === 'player' && name !== 'player') { stopPlayback(); }
    state.screen = name;
    elTitle.textContent = 'KaiOS Tube';
    if (name === 'home') { renderHome(); }
    else if (name === 'search') { renderSearch(); }
    else if (name === 'results') { renderResults(); }
    else if (name === 'player') { renderPlayer(); }
    else if (name === 'playerOptions') { playerOptions(); }
    else if (name === 'link') { renderLink(); }
    else if (name === 'settings') { renderSettings(); }
    else if (name === 'help') { renderHelp(); }
    else if (name === 'loading') { renderLoading(); }
  }

  function renderLoading() {
    clear(elScreen);
    state.rows = [];
    var w = tag('div', 'pad');
    w.appendChild(tag('h2', null, 'লোড হচ্ছে…'));
    w.appendChild(tag('div', 'hint', 'সার্ভার থেকে তথ্য আনা হচ্ছে'));
    elScreen.appendChild(w);
    setSoftkeys('ফিরে যান', '', '');
  }

  function stopPlayback() {
    if (videoEl) {
      try { videoEl.pause(); } catch (e) { }
      videoEl.removeAttribute('src');
      try { videoEl.load(); } catch (e2) { }
      videoEl = null;
    }
    if (state.abortDownload) { state.abortDownload(); state.abortDownload = null; }
  }

  function toastError(msg) {
    alert(msg);
  }

  function back() {
    if (state.screen === 'player' || state.screen === 'playerOptions') { go('playerOptions' === state.screen ? 'player' : 'results'); }
    else if (state.screen === 'results' || state.screen === 'search' || state.screen === 'settings' || state.screen === 'help' || state.screen === 'link') { go('home'); }
    else { try { window.close(); } catch (e) { } }
  }

  /* -------------------------------------------------------- কী হ্যান্ডলিং */
  document.addEventListener('keydown', function (ev) {
    var k = ev.key;
    var screen = state.screen;

    /* প্লেয়ার: ফোকাসড কী-গুলো আগে হ্যান্ডল করি */
    if (screen === 'player' && videoEl) {
      if (k === 'Enter') {
        if (videoEl.paused) { videoEl.play(); } else { videoEl.pause(); }
        ev.preventDefault(); return;
      }
      if (k === 'ArrowLeft') { videoEl.currentTime = Math.max(0, videoEl.currentTime - 10); ev.preventDefault(); return; }
      if (k === 'ArrowRight') { videoEl.currentTime = Math.min(videoEl.duration || 1e9, videoEl.currentTime + 10); ev.preventDefault(); return; }
      if (k === 'ArrowUp') { videoEl.volume = Math.min(1, videoEl.volume + 0.1); setStatus('ভলিউম ' + Math.round(videoEl.volume * 100) + '%'); ev.preventDefault(); return; }
      if (k === 'ArrowDown') { videoEl.volume = Math.max(0, videoEl.volume - 0.1); setStatus('ভলিউম ' + Math.round(videoEl.volume * 100) + '%'); ev.preventDefault(); return; }
      if (k === 'SoftRight') { go('playerOptions'); ev.preventDefault(); return; }
      if (k === 'SoftLeft' || k === 'Backspace') { stopPlayback(); go('results'); ev.preventDefault(); return; }
      return;
    }

    switch (k) {
      case 'ArrowUp': focus(state.focus - 1); ev.preventDefault(); break;
      case 'ArrowDown': focus(state.focus + 1); ev.preventDefault(); break;
      case 'Enter':
        if (state.rows[state.focus] && state.rows[state.focus].onOk) { state.rows[state.focus].onOk(); }
        ev.preventDefault(); break;
      case 'SoftLeft':
      case 'Backspace': back(); ev.preventDefault(); break;
      case 'SoftRight':
        if (screen === 'home') { checkHealth(function () { renderHome(); }); }
        else if (screen === 'results') { go('search'); }
        else if (screen === 'search') { submitSearch(($('q') || {}).value); }
        else if (screen === 'link') { openVideo(($('q') || {}).value || ''); }
        else if (screen === 'settings') { cfg.server = ($('q') || {}).value || cfg.server; saveJSON(CFG_KEY, cfg); go('home'); }
        else if (screen === 'playerOptions') { stopPlayback(); go('results'); }
        else if (screen === 'help') { go('settings'); }
        ev.preventDefault(); break;
      default: break;
    }
  }, false);

  /* --------------------------------------------------------- হেলথ চেক */
  function checkHealth(cb) {
    api('/api/health', function (err, data) {
      if (err) {
        state.health = null;
        elBadge.textContent = 'সার্ভার নেই';
        elBadge.className = 'bad';
        if (cb) { cb(); }
        return;
      }
      state.health = data;
      if (data.mode === 'demo') { cfg.demo = true; }
      elBadge.textContent = (data.mode === 'demo' ? 'ডেমো' : 'লাইভ') + ' · ' + data.max_height + 'p';
      elBadge.className = '';
      if (cb) { cb(); }
    });
  }

  /* ------------------------------------------------------------ শুরু */
  function start() {
    state.quality = cfg.maxHeight;
    checkHealth(function () { go('home'); });
  }

  window.addEventListener('load', start, false);
  /* ডিভাইস ঘোরানো/রিসাইজে লেআউট ঠিক থাকে */
  window.addEventListener('resize', function () {
    if (state.screen === 'home') { return; }
  }, false);
}());
