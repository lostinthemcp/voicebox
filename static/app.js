// VoiceBox page. Plain JS, no build step. All state lives in D and is saved to /data.
"use strict";

const $ = (s) => document.querySelector(s);
let D = null;               // data from server
let msg = "";               // the message being built
let lastCleared = "";       // for CLEAR -> UNDO
let tab = "abc";
let painLevel = null;
let kbMode = "abc";
let skMode = "abc";
let dict = [];              // starter words, most common first
let dictRank = new Map();
let audio = null;
let lastSpoken = { text: "", at: 0 };
let settingsCat = null;
let settingsTarget = null;  // the settings input the on-screen keyboard types into

// ---------- saving ----------
let saveTimer = null, saving = false, saveAgain = false;
function saveSoon(ms = 600) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, ms);
}
async function saveNow() {
  if (saving) { saveAgain = true; return; }
  saving = true;
  try {
    const r = await fetch("/data", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(D) });
    if (!r.ok) throw new Error(r.status);
  } catch (e) {
    toast("Not saved yet - retrying", true);
    saveSoon(5000);
  }
  saving = false;
  if (saveAgain) { saveAgain = false; saveSoon(50); }
}
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode etc. */ } }

// ---------- speech ----------
async function play(text) {
  if (audio) { audio.pause(); audio = null; }
  try { speechSynthesis.cancel(); } catch (e) { /* none */ }
  const s = D.settings;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 15000);
    const r = await fetch("/speak", { method: "POST", signal: ctl.signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, rate: s.rate, voice: s.voice }) });
    clearTimeout(t);
    if (!r.ok) throw new Error(r.status);
    const url = URL.createObjectURL(await r.blob());
    audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch (e) {
    fallbackSpeak(text);
  }
}
function fallbackSpeak(text) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = D.settings.rate || 1;
    u.volume = 1;
    speechSynthesis.speak(u);
  } catch (e) { toast("No voice available", true); }
}
function say(text, { learnIt = true } = {}) {
  text = (text || "").trim();
  if (!text) return;
  const now = Date.now();
  if (text === lastSpoken.text && now - lastSpoken.at < 700) return; // shaky double tap
  lastSpoken = { text, at: now };
  play(text);
  D.history.unshift({ t: new Date().toISOString(), text });
  if (D.history.length > 1000) D.history.length = 1000;
  if (learnIt) learn(text);
  $("#lastsaid").textContent = "Said: " + text;
  saveSoon();
  if (tab === "history") renderPanel();
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
  box.innerHTML = "";
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
function renderKeyboard(box, mode, onKey, onMode) {
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
  box.appendChild(r);
}
function key(val, label, onKey, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  if (cls) b.className = cls;
  b.onclick = () => onKey(val);
  return b;
}

// ---------- tabs + panels ----------
function renderTabs() {
  const nav = $("#tabs");
  nav.innerHTML = "";
  const list = [["abc", "ABC"], ...D.categories.map((c) => [c.id, c.name]), ["pain", "Pain"], ["history", "History"]];
  if (!list.some(([id]) => id === tab)) tab = "abc";
  for (const [id, name] of list) {
    const b = document.createElement("button");
    b.textContent = name;
    b.className = id === tab ? "on" : "";
    b.onclick = () => { tab = id; painLevel = null; renderTabs(); renderPanel(); };
    nav.appendChild(b);
  }
}
function renderPanel() {
  const p = $("#panel");
  p.innerHTML = "";
  p.scrollTop = 0;
  if (tab === "abc") {
    const kb = document.createElement("div");
    kb.className = "keyboard";
    p.appendChild(kb);
    renderKeyboard(kb, kbMode, typeKey, (m) => { kbMode = m; renderPanel(); });
    return;
  }
  if (tab === "pain") return renderPain(p);
  if (tab === "history") return renderHistory(p);
  const cat = D.categories.find((c) => c.id === tab);
  if (!cat.phrases.length) {
    p.innerHTML = '<div class="empty">Nothing here yet. Family: hold the gear (top corner) for 3 seconds to add some.</div>';
    return;
  }
  const g = document.createElement("div");
  g.className = "grid phrases"; // 3 across, ~9 per screen
  for (const ph of cat.phrases) {
    const b = document.createElement("button");
    b.textContent = ph;
    b.onclick = () => say(ph);
    g.appendChild(b);
  }
  p.appendChild(g);
}
const PLACES = ["head", "chest", "stomach", "back", "left arm", "right arm", "left leg", "right leg"];
function renderPain(p) {
  const t = document.createElement("div");
  t.className = "title";
  p.appendChild(t);
  const g = document.createElement("div");
  g.className = "pain";
  p.appendChild(g);
  if (painLevel === null) {
    t.textContent = "How bad is the pain? (0 = none, 10 = worst)";
    for (let i = 0; i <= 10; i++) {
      const b = document.createElement("button");
      b.textContent = i;
      b.style.background = `hsl(${120 - i * 12}, 85%, 50%)`;
      b.onclick = () => { painLevel = i; renderPanel(); };
      g.appendChild(b);
    }
    return;
  }
  t.textContent = `Pain ${painLevel} out of 10. Where?`;
  g.classList.add("loc");
  const base = `My pain is ${painLevel} out of 10`;
  for (const place of PLACES) {
    const b = document.createElement("button");
    b.textContent = place[0].toUpperCase() + place.slice(1);
    b.style.color = "#fff";
    b.onclick = () => { say(`${base}, in my ${place}.`); painLevel = null; renderPanel(); };
    g.appendChild(b);
  }
  const just = document.createElement("button");
  just.textContent = "Just say it";
  just.className = "speak";
  just.onclick = () => { say(base + "."); painLevel = null; renderPanel(); };
  const back = document.createElement("button");
  back.textContent = "Back";
  back.style.color = "#fff";
  back.onclick = () => { painLevel = null; renderPanel(); };
  g.appendChild(just);
  g.appendChild(back);
}
function renderHistory(p) {
  if (!D.history.length) { p.innerHTML = '<div class="empty">Nothing said yet.</div>'; return; }
  const box = document.createElement("div");
  box.className = "hist";
  for (const h of D.history.slice(0, 200)) {
    const row = document.createElement("div");
    row.className = "hrow";
    const d = new Date(h.t);
    const when = document.createElement("div");
    when.className = "when";
    const today = new Date().toDateString() === d.toDateString();
    when.textContent = (today ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " ") +
      d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const s = document.createElement("button");
    s.className = "say";
    s.textContent = h.text;
    s.onclick = () => say(h.text, { learnIt: false });
    const st = document.createElement("button");
    st.className = "star";
    st.textContent = "★";
    st.setAttribute("aria-label", "Save as phrase");
    st.onclick = () => pickCategory(h.text);
    row.append(when, s, st);
    box.appendChild(row);
  }
  p.appendChild(box);
}
function pickCategory(text) {
  const m = $("#modal");
  m.innerHTML = "";
  const box = document.createElement("div");
  box.className = "box";
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
      g.gain.exponentialRampToValueAtTime(0.6, s + 0.02);
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
function closeSettings() { $("#settings").hidden = true; settingsTarget = null; applySettings(); renderTabs(); renderPanel(); renderMsg(); saveSoon(50); }
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

  body.append(el("h2", { text: "Screen and voice" }));
  body.append(stepper("Text size", () => s.textSize, (x) => { s.textSize = x; }, 0.1, 0.7, 1.6, (x) => Math.round(x * 100) + "%"));
  body.append(stepper("Voice speed", () => s.rate, (x) => { s.rate = x; }, 0.1, 0.5, 2, (x) => x.toFixed(1) + "x"));
  const sideBar = el("div", { class: "bar" }, el("span", { text: "Big buttons on:" }));
  for (const side of ["left", "right"]) {
    sideBar.append(el("button", { class: s.side === side ? "on" : "", text: side === "left" ? "Left side" : "Right side",
      onclick: () => { s.side = side; applySettings(); saveSoon(); renderSettings(); } }));
  }
  body.append(sideBar);
  const vbar = el("div", { class: "bar" }, el("span", { text: "Voice:" }));
  body.append(vbar);
  body.append(el("div", { class: "bar" }, el("button", { text: "Test voice", onclick: () => play("Hello. This is my voice. Is this speed okay?") })));
  fetch("/voices").then((r) => r.json()).then((vs) => {
    if (!vs.length) vbar.append(el("span", { class: "small", text: "Piper not found - using the browser voice." }));
    for (const v of vs) {
      const on = s.voice ? s.voice === v : v === "en_US-ryan-medium";
      vbar.append(el("button", { class: on ? "on" : "", text: v.replace(/^en_US-|-medium$/g, ""), onclick: () => { s.voice = v; saveSoon(); renderSettings(); } }));
    }
  }).catch(() => {});

  const people = D.categories.find((c) => c.id === "people");
  if (people) {
    body.append(el("h2", { text: "Family names (People tab)" }));
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
    body.append(el("div", { class: "bar" }, el("span", { text: "Tab name:" }), textInput(cat.name, (v) => { cat.name = v || cat.name; saveSoon(); })));
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
      el("button", { text: "◀ Move tab", onclick: () => moveCat(-1) }),
      el("button", { text: "Move tab ▶", onclick: () => moveCat(1) }),
      el("button", { class: "danger", text: `Delete the "${cat.name}" tab`, onclick: () => {
        if (confirm(`Delete the whole "${cat.name}" tab and its ${cat.phrases.length} phrases? (A backup is kept.)`)) {
          D.categories.splice(ci, 1); settingsCat = D.categories[0] && D.categories[0].id; saveSoon(50); renderSettings();
        }
      } })));
  }
  const newCat = textInput("", () => {}, "New tab name");
  body.append(el("div", { class: "bar" }, newCat, el("button", { text: "Add tab", onclick: () => {
    const v = newCat.value.trim(); if (!v) return;
    const c = { id: slug(v), name: v, phrases: [] }; D.categories.push(c); settingsCat = c.id; saveSoon(50); renderSettings();
  } })));

  body.append(el("h2", { text: "Data and backups" }));
  const file = el("input", { type: "file", accept: ".json,application/json", style: "display:none" });
  file.onchange = async () => {
    try {
      const d = JSON.parse(await file.files[0].text());
      if (!d || !Array.isArray(d.categories)) throw new Error("not VoiceBox data");
      if (!confirm("Replace everything with this file? (The current data is backed up first.)")) return;
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
        clearTimeout(saveTimer);
        const r = await fetch("/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: b.name }) });
        if (r.ok) { await reloadData(); toast("Restored " + b.time); } else toast("Restore failed", true);
      } })));
    }
  }).catch(() => { blist.textContent = "Could not list backups."; });

  body.append(el("h2", { text: "Leave VoiceBox" }));
  body.append(el("div", { class: "bar" }, el("button", { class: "danger", text: "Exit to desktop", onclick: async () => {
    if (!confirm("Close VoiceBox and go to the desktop? Use the Start VoiceBox icon to come back.")) return;
    await saveNow();
    fetch("/exit-kiosk", { method: "POST" }).catch(() => {});
  } })));
  body.append(el("div", { class: "small", text: "Tip: tap a box above, then type with the keys below or the keyboard cover." }));
  body.scrollTop = keepScroll;
}
function renderSKeys() {
  renderKeyboard($("#skeys"), skMode, (k) => {
    const i = settingsTarget;
    if (!i || !document.body.contains(i)) { toast("Tap a box first"); return; }
    if (k === "BS") i.value = i.value.slice(0, -1); else i.value += k;
    i.dispatchEvent(new Event("input"));
  }, (m) => { skMode = m; renderSKeys(); });
  // keep focus on the input so the on-screen keys don't steal it
  $("#skeys").onpointerdown = (e) => e.preventDefault();
}
async function reloadData() {
  D = normalize(await (await fetch("/data")).json());
  applySettings();
  renderTabs(); renderPanel(); renderMsg();
  if (!$("#settings").hidden) renderSettings();
}

// ---------- startup ----------
function normalize(d) {
  d.words = d.words || {}; d.bigrams = d.bigrams || {}; d.history = d.history || [];
  d.settings = Object.assign({ textSize: 1, rate: 1, voice: "", side: "left" }, d.settings || {});
  for (const c of d.categories) c.phrases = c.phrases || [];
  return d;
}
function applySettings() {
  document.documentElement.style.setProperty("--scale", D.settings.textSize);
  document.body.classList.toggle("side-right", D.settings.side === "right");
  document.body.classList.toggle("side-left", D.settings.side !== "right");
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
  for (;;) {
    try { D = normalize(await (await fetch("/data")).json()); break; } catch (e) { toast("Starting...", true); await new Promise((r) => setTimeout(r, 1000)); }
  }
  try {
    dict = (await (await fetch("words.txt")).text()).split("\n").filter(Boolean);
    dict.forEach((w, i) => dictRank.set(w, i));
  } catch (e) { /* prediction still uses learned words */ }
  const ls = lsGet("vb-draft");
  msg = ls !== null ? ls : (D.draft || "");
  applySettings();
  renderTabs(); renderPanel(); renderMsg();

  $("#speak").onclick = $("#speak2").onclick = () => { unlockAudio(); say(msg); };
  $("#yes").onclick = () => say("Yes");
  $("#no").onclick = () => say("No");
  $("#notsure").onclick = () => say("I'm not sure, ask me another way.");
  $("#back").onclick = () => typeKey("BS");
  $("#clear").onclick = () => {
    if (!msg && lastCleared) { setMsg(lastCleared); lastCleared = ""; }
    else if (msg) { lastCleared = msg; setMsg(""); }
  };
  holdButton($("#help"), 1000, helpAlarm);
  holdButton($("#gear"), 3000, openSettings);

  document.addEventListener("keydown", (e) => {
    if (!$("#settings").hidden || !$("#modal").hidden || e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key === "Enter") { e.preventDefault(); say(msg); }
    else if (e.key === "Backspace") { e.preventDefault(); typeKey("BS"); }
    else if (e.key.length === 1) { e.preventDefault(); typeKey(e.key === " " ? " " : e.key); }
  });
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveNow(); });
}
start();
