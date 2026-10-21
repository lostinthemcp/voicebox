// VoiceBox page. Plain JS, no build step. All state lives in D and is saved to /data.
"use strict";

const $ = (s) => document.querySelector(s);
let D = null;               // data from server
let msg = "";               // the message being built
let lastCleared = "";       // for CLEAR -> UNDO
let screen = "home";
let shown = "";           // the screen drawn last (to keep a list's scroll place on redraw)
let painLevel = null;
let kbMode = "abc";
let skMode = "abc";
let dict = [];              // starter words, most common first
let dictRank = new Map();
let audio = null;
let lastSpoken = { text: "", at: 0 };
let settingsCat = null;
let settingsTarget = null;  // the settings input the on-screen keyboard types into
let ignoreUntil = 0;        // taps on a freshly drawn screen are ignored briefly (shaky double tap)
let suggestUntil = 0;       // same for the word guesses, which redraw on every key
let speechSeq = 0;          // only the newest speech request may play
const DEFAULT_SETTINGS = { textSize: 1, rate: 1, voice: "", side: "left" };
const settings = () => (D ? D.settings : DEFAULT_SETTINGS);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- saving ----------
let saveTimer = null, saving = false, saveAgain = false;
function saveSoon(ms = 600) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, ms);
}
async function saveNow() {
  if (saving) { saveAgain = true; return; }
  saving = true;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000); // a hung save must not block saving forever
  try {
    const r = await fetch("/data", { method: "POST", signal: ctl.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(D) });
    if (!r.ok) throw new Error(r.status === 507 ? "disk full or not writable" : "error " + r.status);
    saveProblem("");
  } catch (e) {
    saveProblem(e.name === "AbortError" ? "no answer from VoiceBox server" : e.message);
    saveSoon(5000);
  }
  clearTimeout(t);
  saving = false;
  if (saveAgain) { saveAgain = false; saveSoon(50); }
}
// Quiet for him, visible to family: the gear turns red and Settings says why.
let saveError = "";
function saveProblem(why) {
  saveError = why;
  $("#gear").classList.toggle("warn", !!why);
  const n = $("#savenote");
  if (n) n.textContent = why ? "NOT SAVED: " + why + ". Still trying. Everything is kept on this screen." : "";
}
async function stopSaves() {
  clearTimeout(saveTimer);
  saveAgain = false;
  while (saving) await sleep(100);
}
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode etc. */ } }

// ---------- speech ----------
// One generous timeout: a slow Surface must never lose short typed text to an early give-up
// (the browser voice on that box is silent). Only a real error or 503 falls back.
// The big buttons are always in the server's startup cache: if even they take 2 s, the server is hung,
// so they go straight to the browser voice / big text instead of waiting 20 s.
const CORE = new Set(["Yes", "No", "I'm not sure, ask me another way.", "Hold on, give me a minute.", "I need help"]);
let waitingFor = 0;
// "Fast voice for typing" (on unless the family turns it off): typed text always uses a quick Piper voice
// (Ryan, or Joe/Norman when one of those is chosen), even when a Kokoro voice is chosen for the buttons
const FAST = ["en_US-ryan-medium", "en_US-joe-medium", "en_US-norman-medium"];
const fastVoice = (v) => (FAST.includes(v) ? v : "en_US-ryan-medium");
// his own volume, 30-100%: the system volume is kept at 100% by kiosk.sh, so this is the only control,
// and it can never go so low that he is silent by accident
const vol = () => Math.min(100, Math.max(30, Number(settings().volume) || 100)) / 100;
function fetchWav(text, voice) {
  const s = settings(), ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), CORE.has(text) ? 2000 : 20000);
  document.body.classList.toggle("waiting", ++waitingFor > 0);
  return fetch("/speak", { method: "POST", signal: ctl.signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, rate: s.rate, voice: voice || s.voice }) })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.blob(); })
    .finally(() => { clearTimeout(t); document.body.classList.toggle("waiting", --waitingFor > 0); });
}
// battery, always in view next to the gear; red "PLUG IN" under 20% when not charging
async function showBattery() {
  const e = $("#battery");
  try {
    const b = await (await fetch("/battery")).json();
    if (b.percent == null) { e.hidden = true; return; }
    const low = b.percent < 20 && !b.plugged;
    e.hidden = false;
    e.classList.toggle("low", low);
    e.replaceChildren(el("span", { text: "\u{1F50B}" + b.percent + "%" + (b.plugged ? "\u26A1" : "") }));
    if (low) e.append(el("span", { class: "plug", text: "PLUG IN" }));
    e.setAttribute("aria-label", "Battery " + b.percent + " percent" + (b.plugged ? ", charging" : "") + (low ? ", plug in" : ""));
  } catch (err) { /* server restarting: keep the last reading */ }
}
// one clip: "ended", "paused" (a newer tap) or "error" (a broken or unplayable clip)
function playBlob(blob) {
  const url = URL.createObjectURL(blob);
  audio = new Audio(url);
  audio.volume = vol();
  const a = audio;
  return new Promise((res) => {
    a.onended = () => res("ended");
    a.onpause = () => res(a.ended ? "ended" : "paused");
    a.onerror = () => res("error");
    a.play().catch(() => res("error"));
  }).finally(() => URL.revokeObjectURL(url));
}
// split: typed text is spoken a sentence at a time, the next one rendering while this one plays.
// A sentence whose clip will not play is fetched once more; if it fails again it is shown big.
async function play(text, split = false) {
  const my = ++speechSeq;
  if (audio) { audio.pause(); audio = null; }
  try { speechSynthesis.cancel(); } catch (e) { /* none */ }
  const parts = split ? (text.match(/[^.!?]+[.!?]*/g) || [text]).map((x) => x.trim()).filter(Boolean) : [text];
  const voice = split && settings().fastTyping !== false ? fastVoice(settings().voice) : "";
  const fetchWavV = (t) => fetchWav(t, voice);
  let next = fetchWavV(parts[0]);
  for (let i = 0; i < parts.length; i++) {
    let blob;
    try { blob = await next; } catch (e) {
      if (my === speechSeq) fallbackSpeak(parts.slice(i).join(" ")); // server down, hung or 503
      return;
    }
    if (my !== speechSeq) return; // he already tapped something newer
    if (i + 1 < parts.length) { next = fetchWavV(parts[i + 1]); next.catch(() => {}); }
    let r = await playBlob(blob);
    if (r === "error" && my === speechSeq) {
      try { r = await playBlob(await fetchWavV(parts[i])); } catch (e) { r = "error"; }
    }
    if (my !== speechSeq || r === "paused") return;
    if (r === "error") showBig(parts[i]);
  }
}
function fallbackSpeak(text) {
  try {
    if (!window.speechSynthesis || !speechSynthesis.getVoices().length) throw new Error("no voices");
    const u = new SpeechSynthesisUtterance(text);
    u.rate = settings().rate || 1;
    u.volume = vol();
    u.onerror = (e) => { if (e.error !== "canceled" && e.error !== "interrupted") showBig(text); };
    // a stalled browser voice fires no error: if it has not started in 3 s, show the text
    const my = speechSeq; let started = false;
    u.onstart = () => { started = true; };
    setTimeout(() => { if (!started && my === speechSeq) showBig(text); }, 3000);
    speechSynthesis.speak(u);
  } catch (e) {
    showBig(text); // no voice at all: show it huge so he can point at it
  }
}
function showBig(text) {
  const m = $("#modal");
  m.innerHTML = "";
  m.append(el("div", { class: "box big" },
    el("p", { class: "small", text: "The voice is not working. Please read this:" }),
    el("p", { class: "bigtext", text }),
    el("button", { class: "speak", text: "OK", onclick: () => { m.hidden = true; } })));
  m.hidden = false;
}
function say(text, { learnIt = true, split = false } = {}) {
  text = (text || "").trim();
  if (!text) return;
  const now = performance.now();
  if (text === lastSpoken.text && now - lastSpoken.at < 700) return; // shaky double tap
  lastSpoken = { text, at: now };
  play(text, split);
  $("#lastsaid").textContent = "Said: " + text;
  if (!D) return; // data not loaded yet: still speak, nothing to record
  D.history.unshift({ t: new Date().toISOString(), text });
  if (D.history.length > 1000) D.history.length = 1000;
  if (learnIt) learn(text);
  saveSoon();
  if (screen === "history") renderPanel();
}

// ---------- learning + prediction ----------
function tokens(text) { return (text.toLowerCase().match(/[a-z0-9']+/g) || []); }
function learn(text) {
  let prev = "^";
  for (const w of tokens(text)) {
    D.words[w] = (D.words[w] || 0) + 1;
    const b = D.bigrams[prev] || (D.bigrams[prev] = {});
    b[w] = (b[w] || 0) + 1;
    prev = w;
  }
}
function predict() {
  const m = msg.toLowerCase().match(/([a-z0-9']*)$/);
  const part = m ? m[1] : "";
  const before = tokens(msg.slice(0, msg.length - part.length));
  const prev = before.length && !/[.?!]\s*$/.test(msg) ? before[before.length - 1] : "^";
  const big = D.bigrams[prev] || {};
  const score = (w) => (big[w] || 0) * 50 + (D.words[w] || 0) * 10 + (dictRank.has(w) ? 5 - 5 * dictRank.get(w) / dict.length : 0);
  let cands;
  if (part) {
    const pool = new Set();
    for (const w of Object.keys(D.words)) if (w.startsWith(part) && w !== part) pool.add(w);
    for (const w of dict) { if (pool.size > 400) break; if (w.startsWith(part) && w !== part) pool.add(w); }
    cands = [...pool];
  } else {
    cands = Object.keys(big);
    const starters = prev === "^" ? ["i", "i'm", "please", "can", "where", "what", "the", "my"] : [];
    for (const w of [...starters, ...Object.keys(D.words), ...dict.slice(0, 60)]) if (!cands.includes(w)) cands.push(w);
  }
  cands.sort((a, b) => score(b) - score(a));
  return { part, words: cands.slice(0, 6) };
}
function renderSuggest() {
  const { part, words } = predict();
  const box = $("#suggest");
  if (!box) return;
  box.innerHTML = "";
  suggestUntil = performance.now() + 400;
  for (const w of words) {
    const b = document.createElement("button");
    b.textContent = w === "i" ? "I" : w.replace(/^i'/, "I'");
    b.onclick = () => { setMsg(msg.slice(0, msg.length - part.length) + b.textContent + " "); };
    box.appendChild(b);
  }
}

// ---------- message bar ----------
function setMsg(t) {
  msg = t;
  D.draft = msg;
  lsSet("vb-draft", msg);
  saveSoon(1500);
  renderMsg();
}
function renderMsg() {
  const el = $("#msg");
  el.textContent = "";
  const line = document.createElement("div");
  line.textContent = msg;
  const caret = document.createElement("span");
  caret.className = "caret";
  line.appendChild(caret);
  el.appendChild(line);
  el.scrollTop = el.scrollHeight; // newest words in view; he can scroll back up
  $("#clear").textContent = !msg && lastCleared ? "UNDO" : "CLEAR";
  renderSuggest();
}
function typeKey(k) {
  if (k === "BS") setMsg(msg.slice(0, -1));
  else if (k === " ") setMsg(msg.endsWith(" ") || !msg ? msg : msg + " ");
  else if (/^[.?!,]$/.test(k)) setMsg(msg.replace(/ $/, "") + k + " ");
  else setMsg(msg + k);
}

// ---------- keyboards ----------
const LAYOUTS = {
  abc: ["qwertyuiop", "asdfghjkl'", "zxcvbnm,"],
  "123": ["1234567890", "-/:;()$&@\"", "!#%+=*?_"],
};
function renderKeyboard(box, mode, onKey, onMode, withSpeak) {
  box.innerHTML = "";
  for (const row of LAYOUTS[mode]) {
    const r = document.createElement("div");
    r.className = "krow";
    for (const ch of row) r.appendChild(key(ch, ch.toUpperCase(), onKey));
    if (row === LAYOUTS[mode][2]) r.appendChild(key("BS", "⌫", onKey, "wide"));
    box.appendChild(r);
  }
  const r = document.createElement("div");
  r.className = "krow";
  const m = key(null, mode === "abc" ? "123" : "ABC", () => onMode(mode === "abc" ? "123" : "abc"), "wide");
  r.appendChild(m);
  r.appendChild(key(" ", "space", onKey, "space"));
  r.appendChild(key(".", ".", onKey));
  r.appendChild(key("?", "?", onKey));
  if (withSpeak) r.appendChild(key(null, "SPEAK", () => say(msg, { split: true }), "speak"));
  box.appendChild(r);
}
function key(val, label, onKey, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  if (cls) b.className = cls;
  b.onclick = () => onKey(val);
  return b;
}

// ---------- screens ----------
// screen: "home" | "keyboard" | "pain" | "history" | a category id
const SPECIAL = { keyboard: ["keyboard", "Keyboard"], pain: ["bolt", "Pain"], history: ["clock", "History"] };
// plain bold line icons (24x24), drawn in the page so they never depend on fonts
const ICONS = {
  cup: "M5 8h11v5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z M16 9h2a2 2 0 0 1 0 4h-2 M8 3v2 M12 3v2",
  heart: "M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10z",
  cross: "M9 3h6v6h6v6h-6v6H9v-6H3V9h6z",
  hospital: "M4 21V7l8-4 8 4v14 M2 21h20 M10 21v-4h4v4 M12 7v5 M9.5 9.5h5",
  talk: "M4 5h16v11H10l-5 4v-4H4z",
  people: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6 M16 5a3 3 0 0 1 0 6 M18 14c2 .7 3 2.8 3 6",
  smile: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M8 14c1 1.5 2.4 2.2 4 2.2s3-.7 4-2.2 M9 9.5h.01 M15 9.5h.01",
  card: "M3 5h18v14H3z M8 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M5 16c.5-1.8 1.6-2.5 3-2.5s2.5.7 3 2.5 M14 9h4 M14 13h4",
  keyboard: "M2 6h20v12H2z M6 10h1 M10 10h1 M14 10h1 M18 10h1 M7 14h10",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 3",
  star: "M12 3l2.8 5.8 6.2.9-4.5 4.4 1 6.2L12 17.4 6.5 20.3l1-6.2L3 9.7l6.2-.9z",
  house: "M3 11l9-8 9 8 M5 9v12h14V9 M10 21v-6h4v6",
  bed: "M3 18V7 M3 14h18v4 M21 14v-2a3 3 0 0 0-3-3h-7v5 M7 11.5h.01",
  fire: "M12 22c4 0 7-2.7 7-7 0-4-3-6-4-10-2 2-3 4-3 6-1-1-2-2-2-4-3 3-5 5.5-5 8 0 4.3 3 7 7 7z",
};
function icon(name) {
  if (!ICONS[name]) return el("span", { class: "icon", text: name || "" }); // older data may hold an emoji
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name]);
  svg.appendChild(path);
  return svg;
}
function go(s) { screen = s; painLevel = null; renderPanel(); }
function head(p, title, extra = []) {
  const h = el("div", { class: "head" }, el("h2", { class: "title", text: title }), ...extra);
  p.appendChild(h);
}
function renderPanel() {
  const p = $("#panel");
  const old = p.querySelector(".scroll"), keep = shown === screen && old ? old.scrollTop : 0;
  shown = screen;
  p.innerHTML = "";
  renderScreen(p);
  const list = p.querySelector(".scroll");
  if (list) list.scrollTop = keep; // History redraws after each tap: stay where he was
}
function renderScreen(p) {
  ignoreUntil = performance.now() + 400;
  $("#home").classList.toggle("here", screen === "home");
  if (screen === "home") return renderHome(p);
  if (screen === "keyboard") return renderKeys(p);
  if (screen === "pain") return renderPain(p);
  if (screen === "history") return renderHistory(p);
  const cat = D.categories.find((c) => c.id === screen);
  if (!cat) return go("home");
  head(p, cat.name);
  p.querySelector(".head").prepend(icon(cat.icon));
  if (!cat.phrases.length) {
    p.append(el("div", { class: "empty", text: "Nothing here yet. Family: hold the gear (top corner) for 3 seconds to add some." }));
    return;
  }
  const g = el("div", { class: "phrases scroll" }); // 3 across; rows sized so 2.5 show and the cut row says "more below"
  for (const ph of cat.phrases) g.append(el("button", { text: ph, onclick: () => say(ph) }));
  p.appendChild(g);
  sizeRows();
}
function sizeRows() {
  const g = $("#panel .phrases");
  if (!g) return;
  const gap = parseFloat(getComputedStyle(g).rowGap) || 0;
  g.style.gridAutoRows = Math.max(60, (g.clientHeight - 2 * gap) / 2.5) + "px";
}
function tileList() {
  const tiles = [{ id: "love", icon: "heart", name: "I love you", says: "I love you" }]; // one tap, no sub-screen
  const special = () => Object.entries(SPECIAL).forEach(([id, [icon, name]]) => tiles.push({ id, icon, name }));
  let placed = false;
  for (const c of D.categories) {
    if (c.id === "about" && !placed) { special(); placed = true; }
    tiles.push({ id: c.id, icon: c.icon || "⭐", name: c.name });
  }
  if (!placed) special();
  return tiles;
}
function renderHome(p) {
  const g = el("div", { class: "tiles" });
  const tiles = tileList();
  g.style.gridTemplateColumns = `repeat(${tiles.length > 10 ? 4 : 5}, minmax(0, 1fr))`;
  for (const t of tiles) {
    g.append(el("button", { class: t.says ? "love" : t.id === "keyboard" ? "kbd" : "", "data-tile": t.id, onclick: () => (t.says ? say(t.says) : go(t.id)) },
      icon(t.icon), el("span", { text: t.name })));
  }
  p.appendChild(g);
}
function renderKeys(p) {
  p.append(el("div", { id: "suggest" }));
  const kb = el("div", { class: "keyboard" });
  p.appendChild(kb);
  renderKeyboard(kb, kbMode, typeKey, (m) => { kbMode = m; renderPanel(); }, true);
  renderSuggest();
}
const PLACES = ["head", "chest", "stomach", "back", "left arm", "right arm", "left leg", "right leg"];
function renderPain(p) {
  const g = el("div", { class: "pain" });
  if (painLevel === null) {
    head(p, "How bad is the pain? 0 = none, 10 = worst");
    for (let i = 0; i <= 10; i++) {
      const b = el("button", { text: String(i), onclick: () => { painLevel = i; renderPanel(); } });
      b.style.background = `hsl(${120 - i * 12}, 85%, 50%)`;
      g.append(b);
    }
    p.append(g);
    return;
  }
  const base = `My pain is ${painLevel} out of 10`;
  head(p, `Pain ${painLevel} out of 10. Where?`, [el("button", { text: "◀ Back", onclick: () => { painLevel = null; renderPanel(); } })]);
  g.classList.add("loc");
  const done = (t) => { say(t); painLevel = null; renderPanel(); };
  for (const place of PLACES) {
    const b = el("button", { text: place[0].toUpperCase() + place.slice(1), onclick: () => done(`${base}, in my ${place}.`) });
    b.style.color = "#fff";
    g.append(b);
  }
  g.append(el("button", { class: "speak", text: "Just say the number", onclick: () => done(base + ".") }));
  p.append(g);
}
function renderHistory(p) {
  head(p, "History - tap to say again, ★ to save");
  if (!D.history.length) { p.append(el("div", { class: "empty", text: "Nothing said yet." })); return; }
  const box = el("div", { class: "hist scroll" });
  for (const h of D.history.slice(0, 200)) {
    const d = new Date(h.t);
    const today = new Date().toDateString() === d.toDateString();
    const when = (today ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " ") +
      d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    box.append(el("div", { class: "hrow" },
      el("div", { class: "when", text: when }),
      el("button", { class: "say", text: h.text, onclick: () => say(h.text, { learnIt: false }) }),
      el("button", { class: "star", text: "★", "aria-label": "Save as phrase", onclick: () => pickCategory(h.text) })));
  }
  p.appendChild(box);
}
function pickCategory(text) {
  const m = $("#modal");
  m.innerHTML = "";
  const box = document.createElement("div");
  box.className = "box scroll";
  const p = document.createElement("p");
  p.textContent = `Save "${text}" to:`;
  const g = document.createElement("div");
  g.className = "grid";
  for (const c of D.categories) {
    const b = document.createElement("button");
    b.textContent = c.name;
    b.onclick = () => {
      if (!c.phrases.includes(text)) c.phrases.push(text);
      saveSoon(50);
      m.hidden = true;
      toast("Saved to " + c.name);
    };
    g.appendChild(b);
  }
  const cancel = document.createElement("button");
  cancel.textContent = "Cancel";
  cancel.onclick = () => { m.hidden = true; };
  g.appendChild(cancel);
  box.append(p, g);
  m.appendChild(box);
  m.hidden = false;
}

// ---------- press-and-hold (HELP, gear) ----------
function holdButton(el, ms, fn) {
  let timer = null;
  el.style.setProperty("--hold-ms", ms + "ms");
  const stop = () => { clearTimeout(timer); timer = null; el.classList.remove("holding"); };
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    stop();
    void el.offsetWidth; // restart the fill animation
    el.classList.add("holding");
    unlockAudio();
    timer = setTimeout(() => { stop(); fn(); }, ms);
  });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) el.addEventListener(ev, stop);
}
let actx = null;
function unlockAudio() {
  try { if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === "suspended") actx.resume(); } catch (e) { /* none */ }
}
function chime() {
  unlockAudio();
  if (!actx) return 0;
  const t0 = actx.currentTime + 0.05;
  for (let rep = 0; rep < 3; rep++) {
    [880, 1320, 880, 1320].forEach((f, i) => {
      const o = actx.createOscillator(), g = actx.createGain();
      o.type = "square";
      o.frequency.value = f;
      const s = t0 + rep * 1.0 + i * 0.2;
      g.gain.setValueAtTime(0.0001, s);
      g.gain.exponentialRampToValueAtTime(0.6 * Math.max(0.8, vol()), s + 0.02); // HELP: never under 80%
      g.gain.exponentialRampToValueAtTime(0.0001, s + 0.19);
      o.connect(g).connect(actx.destination);
      o.start(s);
      o.stop(s + 0.2);
    });
  }
  return 3000;
}
function helpAlarm() {
  document.body.classList.remove("alarm");
  void document.body.offsetWidth;
  document.body.classList.add("alarm");
  const wait = chime();
  setTimeout(() => say("I need help"), wait);
}

// ---------- settings ----------
function openSettings() {
  $("#settings").hidden = false;
  settingsCat = settingsCat || (D.categories[0] && D.categories[0].id);
  renderSettings();
  renderSKeys();
}
// "Preparing voice: 37 of 120 phrases": the server renders every phrase ahead so taps are instant
let prepTimer = null;
async function showPrep() {
  clearTimeout(prepTimer);
  const n = $("#prep");
  if (!n || $("#settings").hidden) return;
  try {
    const p = await (await fetch("/prepare")).json();
    n.textContent = p.error || (p.total && p.done < p.total ? `Preparing voice: ${p.done} of ${p.total} phrases` : p.total ? "Voice ready: every phrase is prepared." : "");
  } catch (e) { n.textContent = ""; }
  prepTimer = setTimeout(showPrep, 2000);
}
function closeSettings() { clearTimeout(prepTimer); $("#settings").hidden = true; settingsTarget = null; applySettings(); renderPanel(); renderMsg(); saveSoon(50); }
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith("on")) e[k] = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  e.append(...kids);
  return e;
}
function textInput(value, onInput, placeholder = "") {
  const i = el("input", { type: "text", inputmode: "none", placeholder });
  i.value = value;
  i.oninput = () => onInput(i.value);
  i.onfocus = () => { settingsTarget = i; };
  return i;
}
function stepper(label, get, set, step, min, max, fmt) {
  const v = el("span", { class: "val", text: fmt(get()) });
  const change = (d) => { set(Math.round(Math.min(max, Math.max(min, get() + d)) * 100) / 100); v.textContent = fmt(get()); applySettings(); saveSoon(); };
  return el("div", { class: "bar" }, el("span", { text: label }), el("button", { text: "−", onclick: () => change(-step) }), v, el("button", { text: "+", onclick: () => change(step) }));
}
function slug(name) { return name.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + Date.now().toString(36); }
async function renderSettings() {
  const s = D.settings, body = $("#sbody");
  const keepScroll = body.scrollTop;
  body.innerHTML = "";
  body.append(el("div", { class: "stop" }, el("h1", { text: "Settings (family)" }), el("button", { class: "speak", text: "Done", onclick: closeSettings })));
  body.append(el("div", { id: "savenote" }));
  saveProblem(saveError);

  body.append(el("h2", { text: "Screen and voice" }));
  body.append(stepper("Text size", () => s.textSize, (x) => { s.textSize = x; }, 0.1, 0.7, 1.6, (x) => Math.round(x * 100) + "%"));
  body.append(stepper("Voice speed", () => s.rate, (x) => { s.rate = x; }, 0.1, 0.5, 2, (x) => x.toFixed(1) + "x"));
  const sideBar = el("div", { class: "bar" }, el("span", { text: "Big buttons on:" }));
  for (const side of ["left", "right"]) {
    sideBar.append(el("button", { class: s.side === side ? "on" : "", text: side === "left" ? "Left side" : "Right side",
      onclick: () => { s.side = side; applySettings(); saveSoon(); renderSettings(); } }));
  }
  body.append(sideBar);
  const fastBar = el("div", { class: "bar" }, el("span", { text: "Fast voice for typing:" }));
  for (const on of [true, false]) {
    fastBar.append(el("button", { class: (s.fastTyping !== false) === on ? "on" : "", text: on ? "On" : "Off",
      onclick: () => { s.fastTyping = on; saveSoon(); renderSettings(); } }));
  }
  fastBar.append(el("span", { class: "small", text: "On: typed messages use Ryan (or Joe/Norman) so they are quick." }));
  body.append(fastBar);
  const vbar = el("div", { class: "bar" }, el("span", { text: "Voice:" }));
  body.append(vbar);
  body.append(el("div", { class: "bar" }, el("button", { text: "Test voice", onclick: () => play("Hello. This is my voice. Is this speed okay?") })));
  body.append(el("div", { id: "prep", class: "small" }));
  fetch("/voices").then((r) => r.json()).then((vs) => {
    if (!vs.length) vbar.append(el("span", { class: "small", text: "Piper not found - using the browser voice." }));
    const ids = vs.map((v) => v.id);
    for (const v of vs) {
      const on = ids.includes(s.voice) ? s.voice === v.id : v.id === "en_US-ryan-medium";
      const st = v.total && v.ready >= v.total ? "Ready" : on ? `Preparing ${v.ready} of ${v.total}` : `${v.ready} of ${v.total} ready`;
      vbar.append(el("button", { class: on ? "on voice" : "voice", onclick: () => { s.voice = v.id; saveSoon(50); renderSettings(); } },
        el("span", { text: v.name }), el("span", { class: "vstat", text: v.total ? st : "" })));
    }
  }).catch(() => {});
  showPrep();

  const people = D.categories.find((c) => c.id === "people");
  if (people) {
    body.append(el("h2", { text: "Family names (People tile)" }));
    let name = "";
    const inp = textInput("", (v) => { name = v; }, "Type a name");
    body.append(el("div", { class: "bar" }, inp, el("button", { text: "Add name", onclick: () => {
      name = inp.value.trim();
      if (name && !people.phrases.includes(name)) { people.phrases.push(name); saveSoon(50); toast("Added " + name); }
      renderSettings();
    } })));
  }

  body.append(el("h2", { text: "Phrases" }));
  const cbar = el("div", { class: "bar" });
  for (const c of D.categories) cbar.append(el("button", { class: c.id === settingsCat ? "on" : "", text: c.name, onclick: () => { settingsCat = c.id; renderSettings(); } }));
  body.append(cbar);
  const cat = D.categories.find((c) => c.id === settingsCat);
  if (cat) {
    body.append(el("div", { class: "bar" }, el("span", { text: "Tile name:" }), textInput(cat.name, (v) => { cat.name = v || cat.name; saveSoon(); }),
      ));
    const pics = el("div", { class: "bar" }, el("span", { text: "Picture:" }));
    for (const n of Object.keys(ICONS)) {
      const b = el("button", { class: (cat.icon || "star") === n ? "on pic" : "pic", "aria-label": n, onclick: () => { cat.icon = n; saveSoon(); renderSettings(); } });
      b.append(icon(n));
      pics.append(b);
    }
    body.append(pics);
    cat.phrases.forEach((ph, i) => {
      const move = (d) => { const j = i + d; if (j < 0 || j >= cat.phrases.length) return; [cat.phrases[i], cat.phrases[j]] = [cat.phrases[j], cat.phrases[i]]; saveSoon(); renderSettings(); };
      body.append(el("div", { class: "row" },
        textInput(ph, (v) => { cat.phrases[i] = v; saveSoon(); }),
        el("button", { text: "▲", "aria-label": "Move up", onclick: () => move(-1) }),
        el("button", { text: "▼", "aria-label": "Move down", onclick: () => move(1) }),
        el("button", { class: "danger", text: "Delete", onclick: () => { if (confirm(`Delete "${ph}"?`)) { cat.phrases.splice(i, 1); saveSoon(50); renderSettings(); } } })));
    });
    const add = textInput("", () => {}, "New phrase");
    body.append(el("div", { class: "bar" }, add, el("button", { text: "Add phrase", onclick: () => {
      const v = add.value.trim(); if (v) { cat.phrases.push(v); saveSoon(50); renderSettings(); }
    } })));
    const ci = D.categories.indexOf(cat);
    const moveCat = (d) => { const j = ci + d; if (j < 0 || j >= D.categories.length) return; [D.categories[ci], D.categories[j]] = [D.categories[j], D.categories[ci]]; saveSoon(); renderSettings(); };
    body.append(el("div", { class: "bar" },
      el("button", { text: "◀ Move tile", onclick: () => moveCat(-1) }),
      el("button", { text: "Move tile ▶", onclick: () => moveCat(1) }),
      el("button", { class: "danger", text: `Delete the "${cat.name}" tile`, onclick: () => {
        if (confirm(`Delete the whole "${cat.name}" tile and its ${cat.phrases.length} phrases? (A backup is kept.)`)) {
          D.categories.splice(ci, 1); settingsCat = D.categories[0] && D.categories[0].id; saveSoon(50); renderSettings();
        }
      } })));
  }
  const newCat = textInput("", () => {}, "New tile name");
  body.append(el("div", { class: "bar" }, newCat, el("button", { text: "Add tile", onclick: () => {
    const v = newCat.value.trim(); if (!v) return;
    const c = { id: slug(v), name: v, icon: "star", phrases: [] }; D.categories.push(c); settingsCat = c.id; saveSoon(50); renderSettings();
  } })));

  body.append(el("h2", { text: "Data and backups" }));
  const file = el("input", { type: "file", accept: ".json,application/json", style: "display:none" });
  file.onchange = async () => {
    try {
      const d = JSON.parse(await file.files[0].text());
      if (!d || !Array.isArray(d.categories)) throw new Error("not VoiceBox data");
      if (!confirm("Replace everything with this file? (The current data is backed up first.)")) return;
      await stopSaves();
      const r = await fetch("/data", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) });
      if (!r.ok) throw new Error(r.status);
      await reloadData(); toast("Imported");
    } catch (e) { toast("Import failed: " + e.message, true); }
    file.value = "";
  };
  body.append(el("div", { class: "bar" },
    el("button", { text: "Export (download)", onclick: () => {
      const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(D, null, 1)], { type: "application/json" })),
        download: "voicebox-" + new Date().toISOString().slice(0, 10) + ".json" });
      document.body.append(a); a.click(); a.remove();
    } }),
    el("button", { text: "Import from file", onclick: () => file.click() }), file));
  const blist = el("div", {}, el("div", { class: "small", text: "Loading backups..." }));
  body.append(el("div", { class: "small", text: "Backups are saved automatically (every 10 minutes while in use, plus one a day). Tap one to go back to it." }), blist);
  fetch("/backups").then((r) => r.json()).then((bs) => {
    blist.innerHTML = "";
    if (!bs.length) blist.append(el("div", { class: "small", text: "No backups yet." }));
    for (const b of bs.slice(0, 30)) {
      blist.append(el("div", { class: "bar" }, el("button", { text: "Restore " + b.time + (b.name.startsWith("daily") ? " (daily)" : ""), onclick: async () => {
        if (!confirm("Go back to the backup from " + b.time + "? (Today's data is backed up first.)")) return;
        await stopSaves(); // an in-flight save must not undo the restore
        const r = await fetch("/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: b.name }) });
        if (r.ok) { await reloadData(); toast("Restored " + b.time); } else toast("Restore failed", true);
      } })));
    }
  }).catch(() => { blist.textContent = "Could not list backups."; });

  body.append(el("h2", { text: "Updates" }));
  const ustat = el("div", { id: "ustat", class: "small", text: "Needs the internet. Phrases, names and backups are never changed by an update." });
  const ubar = el("div", { class: "bar" });
  ubar.append(el("button", { text: "Check for updates", onclick: async () => {
    ustat.textContent = "Checking...";
    try {
      const j = await updatePost("/update/check");
      if (!j.available) { ustat.textContent = "Up to date (version " + j.current.slice(0, 7) + ")."; return; }
      ustat.textContent = "Update available.";
      if (!ubar.querySelector(".install")) ubar.prepend(el("button", { class: "speak install", text: "Install update", onclick: () => runUpdate("/update/install", ustat) }));
    } catch (e) { ustat.textContent = e.message; }
  } }));
  ubar.append(el("button", { text: "Undo last update", onclick: () => {
    if (confirm("Go back to the version before the last update?")) runUpdate("/update/undo", ustat);
  } }));
  body.append(ubar, ustat);

  body.append(el("h2", { text: "Leave VoiceBox" }));
  body.append(el("div", { class: "bar" }, el("button", { class: "danger", text: "Exit to desktop", onclick: async () => {
    if (!confirm("Close VoiceBox and go to the desktop? Use the Start VoiceBox icon to come back.")) return;
    await saveNow();
    fetch("/exit-kiosk", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  } })));
  body.append(el("div", { class: "small", text: "Tip: tap a box above, then type with the keys below or the keyboard cover." }));
  body.scrollTop = keepScroll;
}
function renderSKeys() {
  renderKeyboard($("#skeys"), skMode, (k) => {
    const i = settingsTarget;
    if (!i || !document.body.contains(i)) { toast("Tap a box first"); return; }
    if (k === "BS") i.value = i.value.slice(0, -1);
    else i.value += /(^|[.?!] )$/.test(i.value) ? k.toUpperCase() : k; // names and phrases start with a capital
    i.dispatchEvent(new Event("input"));
  }, (m) => { skMode = m; renderSKeys(); });
  // keep focus on the input so the on-screen keys don't steal it
  $("#skeys").onpointerdown = (e) => e.preventDefault();
}
async function updatePost(path) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "VoiceBox could not do that (error " + r.status + ").");
  return j;
}
// install or undo, then wait for the restarted server to report the new version and reload the page
async function runUpdate(path, ustat) {
  await stopSaves(); await saveNow();
  ustat.textContent = "Updating... this can take a few minutes. Keep the Surface on.";
  let j;
  try { j = await updatePost(path); } catch (e) { ustat.textContent = e.message; return; }
  ustat.textContent = "Restarting VoiceBox..." + (j.notes && j.notes.length ? " (" + j.notes.join(". ") + ")" : "");
  await sleep(2500); // the old server stops after 0.5 s; don't mistake it for the new one
  for (let i = 0; i < 90; i++) {
    try { if ((await (await fetch("/version")).json()).version === j.version) { location.reload(); return; } } catch (e) { /* still restarting */ }
    await sleep(1000);
  }
  ustat.textContent = "VoiceBox did not come back. Restart the Surface.";
}
async function reloadData() {
  D = normalize(await (await fetch("/data")).json());
  applySettings();
  renderPanel(); renderMsg();
  if (!$("#settings").hidden) renderSettings();
}

// ---------- startup ----------
function normalize(d) {
  d.words = d.words || {}; d.bigrams = d.bigrams || {}; d.history = d.history || [];
  d.settings = Object.assign({ textSize: 1, rate: 1, voice: "", side: "left", volume: 100, fastTyping: true }, d.settings || {});
  for (const c of d.categories) c.phrases = c.phrases || [];
  return d;
}
function applySettings() {
  document.documentElement.style.setProperty("--scale", D.settings.textSize);
  document.body.classList.toggle("side-right", D.settings.side === "right");
  document.body.classList.toggle("side-left", D.settings.side !== "right");
  $("#vol").value = Math.round(vol() * 100);
}
let toastT = null;
function toast(t, bad) {
  const e = $("#toast");
  e.textContent = t;
  e.className = "show" + (bad ? " bad" : "");
  clearTimeout(toastT);
  toastT = setTimeout(() => { e.className = ""; }, 2500);
}
async function start() {
  // YES / NO / NOT SURE / HELP work even before the data loads (or if the server is down)
  $("#yes").onclick = () => say("Yes");
  $("#no").onclick = () => say("No");
  $("#notsure").onclick = () => say("I'm not sure, ask me another way.");
  $("#holdon").onclick = () => say("Hold on, give me a minute.");
  holdButton($("#help"), 1000, helpAlarm);
  $("#vol").oninput = () => {
    const v = Math.max(30, Number($("#vol").value));
    if (audio) audio.volume = v / 100;
    if (D) { D.settings.volume = v; saveSoon(); }
  };
  showBattery();
  setInterval(showBattery, 60000);
  // a finger that moved past Chrome's own tap slop (~15 px) inside a list was scrolling, not pressing;
  // outside lists every tap counts, so a shaky press on a key or tile is never dropped
  let downAt = null, moved = 0;
  for (const area of [$("#panel"), $("#modal")]) {
    area.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; moved = 0; }, true);
    area.addEventListener("pointermove", (e) => { if (downAt) moved = Math.max(moved, Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1])); }, true);
    area.addEventListener("scroll", () => { if (downAt) moved = 99; }, true);
    area.addEventListener("click", (e) => { if (downAt && (moved === 99 || (moved >= 16 && e.target.closest(".scroll")))) { e.stopPropagation(); e.preventDefault(); } downAt = null; }, true);
  }
  addEventListener("resize", sizeRows);
  $("#panel").addEventListener("click", (e) => {
    const now = performance.now();
    if (now < ignoreUntil || (now < suggestUntil && e.target.closest("#suggest"))) { e.stopPropagation(); e.preventDefault(); }
  }, true);
  try { speechSynthesis.getVoices(); } catch (e) { /* starts loading the browser voices */ }
  for (;;) {
    try { D = normalize(await (await fetch("/data")).json()); break; } catch (e) { toast("Starting..."); await new Promise((r) => setTimeout(r, 1000)); }
  }
  try {
    dict = (await (await fetch("words.txt")).text()).split("\n").filter(Boolean);
    dict.forEach((w, i) => dictRank.set(w, i));
  } catch (e) { /* prediction still uses learned words */ }
  const ls = lsGet("vb-draft");
  msg = ls !== null ? ls : (D.draft || "");
  lastCleared = lsGet("vb-undo") || "";
  applySettings();
  renderPanel(); renderMsg();

  $("#speak").onclick = () => { unlockAudio(); say(msg, { split: true }); };
  $("#home").onclick = () => go("home");
  $("#back").onclick = () => typeKey("BS");
  $("#clear").onclick = () => {
    if (!msg && lastCleared) { const t = lastCleared; lastCleared = ""; lsSet("vb-undo", ""); setMsg(t); }
    else if (msg) { lastCleared = msg; lsSet("vb-undo", msg); setMsg(""); }
  };
  holdButton($("#gear"), 3000, openSettings);

  document.addEventListener("keydown", (e) => {
    if (!$("#settings").hidden || !$("#modal").hidden || e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key === "Enter") { e.preventDefault(); say(msg, { split: true }); }
    else if (e.key === "Backspace") { e.preventDefault(); typeKey("BS"); }
    else if (e.key.length === 1) {
      e.preventDefault();
      if (screen !== "keyboard") go("keyboard"); // show the word guesses while he types
      typeKey(e.key);
    }
  });
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveNow(); });
  // tell the server the page started and speech works, so a fresh update counts as healthy
  // (bootguard.sh rolls back a version that never gets here)
  (async function healthy(tries) {
    try {
      const r = await fetch("/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Yes", rate: D.settings.rate, voice: D.settings.voice }) });
      if (!r.ok) throw new Error(r.status);
      await fetch("/healthy", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (e) { if (tries > 1) setTimeout(() => healthy(tries - 1), 30000); }
  })(10);
}
start();
