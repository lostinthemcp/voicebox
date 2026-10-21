#!/usr/bin/env python3
"""VoiceBox local server: static page, Piper speech, and one JSON data file with backups.

Stdlib only. Binds to 127.0.0.1 so nothing outside this machine can reach it.
"""
import datetime
import glob
import hashlib
import json
import os
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HOME = os.environ.get("VOICEBOX_HOME") or os.path.expanduser("~/.local/share/voicebox")
PORT = int(os.environ.get("VOICEBOX_PORT", "8765"))
APP = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP, "static")
DATA = os.path.join(HOME, "data.json")
BACKUPS = os.path.join(HOME, "backups")
DAILY = os.path.join(BACKUPS, "daily")
PIPER_DIR = os.path.join(HOME, "piper")
PIPER_BIN = os.path.join(PIPER_DIR, "piper", "piper")
PIPER_DEFAULT = "en_US-ryan-medium"  # the voice until the family picks another
KOKORO_DIR = os.path.join(HOME, "kokoro")
KOKORO_PY = os.path.join(KOKORO_DIR, "venv", "bin", "python3")
KOKORO_WORKER = os.environ.get("VOICEBOX_KOKORO_WORKER") or os.path.join(APP, "kokoro_worker.py")
KOKORO_FILES = {"kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
                "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"}
CACHE = os.path.join(HOME, "cache")
CACHE_CAP = 300 * 1024 * 1024
CORE = ("Yes", "No", "I'm not sure, ask me another way.", "I need help")  # the always-there buttons
EXIT_FLAG = os.path.join(HOME, "exit-kiosk")
AI_KEY = os.path.join(HOME, "ai_key")
KEEP_ROLLING = 200
KEEP_DAILY_DAYS = 60
ROLLING_EVERY = datetime.timedelta(minutes=10)
MAX_BODY = 20 * 1024 * 1024

DEFAULT_DATA = {
    "version": 1,
    "categories": [
        {"id": "needs", "name": "Needs", "icon": "cup", "phrases": [
            "I need the bathroom", "I'm thirsty", "I'm hungry", "I'm cold", "I'm hot",
            "Please reposition me", "I'm tired"]},
        {"id": "feelings", "name": "Feelings", "icon": "heart", "phrases": [
            "I love you", "I'm frustrated", "Give me a minute", "I'm okay", "Thank you",
            "Let me try again", "Please slow down",
            "I'm still in here. I understand everything.", "I'm tired, ask me later.",
            "I'm scared.", "This is really hard.", "I'm still me.", "Let me do it.",
            "I know what I want to say. Give me time.",
            "I'm angry, not at you.", "This sucks."]},
        {"id": "medical", "name": "Medical", "icon": "cross", "phrases": [
            "Call the nurse", "I'm in pain", "I can't breathe well", "I'm dizzy",
            "I'm sick to my stomach", "Something is wrong"]},
        {"id": "staff", "name": "Hospital Staff", "icon": "hospital", "phrases": [
            "Please call my nurse", "When is the doctor coming?", "What is this medicine for?",
            "Please explain that again, slowly.", "Please call my family", "Please raise the bed",
            "Please lower the bed", "Please turn the light off", "Please turn the TV on",
            "Please turn the TV off", "I need suction", "I need my glasses", "What time is it?",
            "What day is it?", "Am I having a test today?", "I want to talk to the doctor",
            "Please write it down for me", "Please wash your hands", "I need to rest now",
            "Please talk to me, not about me.", "Please tell me what's happening.",
            "Please explain what you are doing before you do it."]},
        {"id": "conversation", "name": "Talk", "icon": "talk", "phrases": [
            "Good morning", "I agree", "I don't agree", "Tell me more", "Not now",
            "Please ask me yes or no questions.", "Tell me about your day.", "Sit with me.",
            "Hold my hand.", "I'm fine. Quit fussing.", "Knock it off.", "Get me out of here.",
            "What's the score?", "How's the crew?", "Tell the guys I said hi.", "You look like hell.",
            "Love you guys."]},
        {"id": "ribbing", "name": "Ribbing", "icon": "smile", "phrases": [
            "Who let you in here?", "Did you bring food, or just your face?",
            "Nice of you to finally show up.", "Don't make me laugh. It hurts.",
            "I'd get up, but I'm busy.", "You're blocking the TV.", "Don't touch my remote.",
            "Still better looking than you.", "Go home. You look worse than I do.",
            "Did you bring coffee? Then why are you here?", "Who's paying for all this?",
            "Real heroes run into burning buildings.",
            "Firefighters save the day. Cops show up for the paperwork.",
            "Shouldn't you be at a donut shop?", "I can't believe I needed a cop to rescue me.",
            "Don't tell the guys I got rescued by a cop."]},
        {"id": "people", "name": "People", "icon": "people", "phrases": []},
        {"id": "about", "name": "About Me", "icon": "card", "phrases": []},
    ],
    "words": {},
    "bigrams": {},
    "history": [],
    "draft": "",
    "settings": {"textSize": 1.0, "rate": 1.0, "voice": "", "side": "left"},
}

lock = threading.Lock()
piper_lock = threading.Lock()


def valid(d):
    """True only for data the page can actually load (a bad import must never brick it)."""
    if not isinstance(d, dict) or not isinstance(d.get("categories"), list):
        return False
    for c in d["categories"]:
        if not (isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("name"), str)
                and isinstance(c.get("phrases", []), list) and all(isinstance(p, str) for p in c.get("phrases", []))):
            return False
    for key, typ in (("settings", dict), ("words", dict), ("bigrams", dict), ("history", list), ("draft", str)):
        if key in d and not isinstance(d[key], typ):
            return False
    if not all(isinstance(v, dict) for v in d.get("bigrams", {}).values()):
        return False
    if not all(isinstance(h, dict) and isinstance(h.get("text"), str) for h in d.get("history", [])):
        return False
    return True


def read_json(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not valid(d):
        raise ValueError("not VoiceBox data")
    return d


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dfd = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def list_backups():
    files = glob.glob(os.path.join(BACKUPS, "data-*.json")) + glob.glob(os.path.join(DAILY, "data-*.json"))
    return sorted(files, key=os.path.getmtime, reverse=True)


def backup_current(force=False):
    """Copy the current data.json into the backup folders before it is replaced."""
    if not os.path.exists(DATA):
        return
    try:
        read_json(DATA)
    except Exception:
        return  # never rotate a corrupt file over good backups
    os.makedirs(DAILY, exist_ok=True)
    now = datetime.datetime.now()
    rolling = sorted(glob.glob(os.path.join(BACKUPS, "data-*.json")))
    newest = None
    if rolling:
        try:
            newest = datetime.datetime.strptime(os.path.basename(rolling[-1])[5:18], "%Y%m%d-%H%M")
        except ValueError:
            newest = None
    if force or newest is None or now - newest >= ROLLING_EVERY:
        shutil.copy2(DATA, os.path.join(BACKUPS, now.strftime("data-%Y%m%d-%H%M%S.json")))
        rolling = sorted(glob.glob(os.path.join(BACKUPS, "data-*.json")))
        for old in rolling[:-KEEP_ROLLING]:
            os.remove(old)
    daily = os.path.join(DAILY, now.strftime("data-%Y%m%d.json"))
    if not os.path.exists(daily):
        shutil.copy2(DATA, daily)
    cutoff = (now - datetime.timedelta(days=KEEP_DAILY_DAYS)).strftime("data-%Y%m%d.json")
    for old in glob.glob(os.path.join(DAILY, "data-*.json")):
        if os.path.basename(old) < cutoff:
            os.remove(old)


def save(d):
    with lock:
        os.makedirs(HOME, exist_ok=True)
        backup_current()
        write_atomic(DATA, json.dumps(d, ensure_ascii=False))


def load():
    with lock:
        os.makedirs(HOME, exist_ok=True)
        try:
            return read_json(DATA)
        except FileNotFoundError:
            pass  # a missing file also recovers from backups below
        except Exception:
            # keep the broken file for a human to look at, then recover
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(DATA, DATA + ".corrupt-" + stamp)
        for b in list_backups():
            try:
                d = read_json(b)
            except Exception:
                continue
            write_atomic(DATA, json.dumps(d, ensure_ascii=False))
            sys.stderr.write("data.json was missing or corrupt; restored from %s\n" % b)
            return d
        d = json.loads(json.dumps(DEFAULT_DATA))
        write_atomic(DATA, json.dumps(d))
        return d


VOICE_LINE = re.compile(r"^[a-z]{2}_[A-Z]{2}/[a-z0-9_]+/(x_low|low|medium|high)/[a-z]{2}_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)$")
KOKORO_VOICE = re.compile(r"^[a-z]{2}_[a-z]+$")


def voice_table(path=os.path.join(APP, "voices.txt")):
    """[(friendly name, id, piper path or None)] from voices.txt lines "Name | en_US/.../x" or "Name | kokoro am_x"."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, _, spec = line.partition("|")
                name, spec = name.strip(), spec.strip()
                if line.startswith("#") or not name:
                    continue
                if VOICE_LINE.match(spec):
                    out.append((name, spec.rsplit("/", 1)[1], spec))
                elif spec.startswith("kokoro ") and KOKORO_VOICE.match(spec[7:].strip()):
                    out.append((name, "kokoro-" + spec[7:].strip(), None))
    except OSError:
        pass
    return out


def piper_voices():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(PIPER_DIR, "*.onnx"))) if os.path.exists(PIPER_BIN) else []


def kokoro_ready():
    return os.path.exists(KOKORO_PY) and os.path.exists(KOKORO_WORKER) \
        and all(os.path.exists(os.path.join(KOKORO_DIR, f)) for f in KOKORO_FILES)


def voices():
    """The voices that work right now, in voices.txt order: [{"id", "name"}]."""
    pv, kok = piper_voices(), kokoro_ready()
    out = [{"id": i, "name": n} for n, i, _ in voice_table() if (i in pv if not i.startswith("kokoro-") else kok)]
    known = {v["id"] for v in out}
    return out + [{"id": v, "name": v} for v in pv if v not in known]


def pick_voice(voice):
    ids = [v["id"] for v in voices()]
    if voice in ids:
        return voice
    pv = piper_voices()
    return PIPER_DEFAULT if PIPER_DEFAULT in pv else (pv[0] if pv else None)


def piper_wav(text, rate, voice, low=False):
    if voice not in piper_voices():
        return None
    with piper_lock:  # one Piper at a time: rapid taps must not swamp the small CPU
        r = subprocess.run(
            [PIPER_BIN, "-q", "-m", os.path.join(PIPER_DIR, voice + ".onnx"), "-f", "-",
             "--length_scale", "%.3f" % (1.0 / rate)],
            input=text.replace("\n", " ").encode(), capture_output=True, timeout=30,
            preexec_fn=(lambda: os.nice(10)) if low else None)  # pre-render runs at low priority
    if r.returncode != 0 or r.stdout[:4] != b"RIFF":
        return None
    return r.stdout


class KokoroWorker:
    """ONE long-lived Kokoro process (the model loads once), one render at a time. A render that
    outlives the caller's wait still finishes and lands in the disk cache for next time."""

    def __init__(self):
        self.proc = None
        self.busy = threading.Lock()  # held from the request until the worker answers (or dies)
        self.answer = None
        self.mutex = threading.Lock()
        self.since = self.started = 0.0

    def _read(self, proc):
        for line in proc.stdout:
            if '"ready"' not in line:
                self._finish()
        proc.wait()
        self._finish()  # it died: free the slot; the next render starts a new one

    def _finish(self):
        with self.mutex:  # the reader and a failed request may both get here
            ev, self.answer = self.answer, None
        if ev:
            ev.set()
            self.busy.release()

    def render(self, text, voice, speed, out, wait):
        """'ok' (out written), 'busy', 'slow' (still running) or 'down'."""
        if not kokoro_ready():
            return "down"
        if self.busy.locked() and self.proc and time.time() - self.since > 120:
            self.proc.kill()  # stuck: its reader frees the slot
        if not self.busy.acquire(blocking=False):
            return "busy"
        ev = threading.Event()
        try:
            if self.proc is None or self.proc.poll() is not None:
                if time.time() - self.started < 60:
                    raise OSError("worker died less than a minute ago")  # no restart storm on a broken install
                self.started = time.time()
                self.proc = subprocess.Popen([KOKORO_PY, KOKORO_WORKER, KOKORO_DIR], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, text=True, bufsize=1)
                threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
            self.answer, self.since = ev, time.time()
            self.proc.stdin.write(json.dumps({"text": text, "voice": voice, "speed": speed, "out": out}) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            sys.stderr.write("kokoro: %s\n" % e)
            if self.answer is ev:
                self._finish()
            else:
                self.busy.release()
            return "down"
        if not ev.wait(wait):
            return "slow"
        return "ok" if os.path.exists(out) else "down"


kokoro = KokoroWorker()


def cache_path(voice, text, rate):
    return os.path.join(CACHE, voice, hashlib.sha1(("%s\n%.2f" % (text, rate)).encode()).hexdigest() + ".wav")


def read_cache(path):
    try:
        with open(path, "rb") as f:
            wav = f.read()
        os.utime(path)  # recently used files are pruned last
        return wav if wav[:4] == b"RIFF" else None
    except OSError:
        return None


def write_cache(path, wav):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(wav)
        os.replace(path + ".tmp", path)
    except OSError:
        pass  # a full disk must never stop speech


def prune_cache():
    files = []
    for p in glob.glob(os.path.join(CACHE, "*", "*.wav")):
        try:
            st = os.stat(p)
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= CACHE_CAP:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def speak(text, rate, voice, wait):
    """WAV bytes or None. Disk cache first; Kokoro if chosen and done within `wait` s; else Piper (Ryan)."""
    rate = min(max(float(rate or 1.0), 0.5), 2.0)
    voice = pick_voice(voice) or PIPER_DEFAULT  # no engine at all: the disk cache may still have it
    path = cache_path(voice, text, rate)
    wav = read_cache(path)
    if wav:
        return wav
    if voice.startswith("kokoro-"):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError:
            pass  # disk trouble: the worker fails and Piper answers
        if kokoro.render(text, voice[7:], rate, path, wait) == "ok":
            wav = read_cache(path)
            if wav:
                return wav
        # too slow or down: Piper answers now, and the Kokoro render still lands in the cache for next time
        voice = PIPER_DEFAULT if PIPER_DEFAULT in piper_voices() else (piper_voices() or [PIPER_DEFAULT])[0]
        path = cache_path(voice, text, rate)
        wav = read_cache(path)
        if wav:
            return wav
    wav = piper_wav(text, rate, voice)
    if wav:
        write_cache(path, wav)
    return wav


# ---------- pre-render: every phrase, in the chosen voice, on disk ----------
prep = {"voice": "", "done": 0, "total": 0}
prep_wake = threading.Event()
last_live = [0.0]  # time of the last tap; pre-render steps aside for it


def prerender_once():
    try:
        d = read_json(DATA)
    except Exception:
        return
    s = d.get("settings") or {}
    rate = min(max(float(s.get("rate") or 1.0), 0.5), 2.0)
    voice = pick_voice(str(s.get("voice") or ""))
    if not voice:
        return
    texts = list(dict.fromkeys([*CORE, *(p.strip() for c in d["categories"] for p in c.get("phrases", []) if p.strip())]))
    jobs = [(voice, t) for t in texts]
    if voice.startswith("kokoro-") and pick_voice(PIPER_DEFAULT) == PIPER_DEFAULT:
        jobs = [(PIPER_DEFAULT, t) for t in CORE] + jobs  # the fallback for YES/NO/HELP is instant too
    prep.update(voice=voice, done=0, total=len(jobs))
    for i, (v, t) in enumerate(jobs):
        if prep_wake.is_set():
            return  # phrases or voice changed: start over (done ones are skipped quickly)
        path = cache_path(v, t, rate)
        while not os.path.exists(path):
            if time.time() - last_live[0] < 3:
                time.sleep(0.5)  # a live tap goes first
                continue
            if v.startswith("kokoro-"):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                r = kokoro.render(t, v[7:], rate, path, 300)
                if r == "busy":
                    time.sleep(0.5)
                    continue
                if r != "ok":
                    prep["error"] = "The %s voice is not working; Ryan is used." % v[7:]
                    return
            else:
                wav = piper_wav(t, rate, v, low=True)
                if not wav:
                    return
                write_cache(path, wav)
            break
        prep.update(done=i + 1, error="")
    prune_cache()


def prerender_loop():
    while True:
        prep_wake.wait()
        prep_wake.clear()
        try:
            prerender_once()
        except Exception as e:
            sys.stderr.write("pre-render: %r\n" % e)
            time.sleep(5)


def kill_browser():
    for args in (["pkill", "-f", "voicebox-profile"], ["flatpak", "kill", "org.chromium.Chromium"]):
        try:
            subprocess.run(args, timeout=5, capture_output=True)
        except Exception:
            pass


# ---------- self-update ----------
# Updates come ONLY from the `stable` branch, never `main`: stable moves only after independent review,
# and this tablet is someone's voice. The URLs can be overridden only by whoever starts the server (tests).
PREV = APP + ".prev"
CHECK_URL = os.environ.get("VOICEBOX_UPDATE_CHECK_URL", "https://api.github.com/repos/lostinthemcp/voicebox/commits/stable")
UPDATE_URL = os.environ.get("VOICEBOX_UPDATE_URL", "https://github.com/lostinthemcp/voicebox/archive/refs/heads/stable.tar.gz")
VOICES_URL = os.environ.get("VOICEBOX_VOICES_URL", "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/")
MAX_TARBALL = 20 * 1024 * 1024
MAX_UNPACKED = 60 * 1024 * 1024
MAX_VOICE = 300 * 1024 * 1024
APP_FILES = ("server.py", "kiosk.sh", "kokoro_worker.py", "static", "voices.txt")
REQUIRED = ("server.py", "kiosk.sh", "static/index.html", "static/app.js")
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MAX_KOKORO = 400 * 1024 * 1024
update_lock = threading.Lock()


class UpdateError(Exception):
    """The message is shown to the family as it is, so it must be plain and true."""


def host_ok(url):
    u = urllib.parse.urlsplit(url)
    h = u.hostname or ""
    return u.scheme == "https" and (h in ("api.github.com", "github.com", "codeload.github.com", "huggingface.co",
                                          "release-assets.githubusercontent.com", "objects.githubusercontent.com")
                                    or h.endswith(".hf.co"))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    # github.com -> codeload / release assets, huggingface.co -> *.hf.co; anything else is refused
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_ok(newurl):
            raise UpdateError("A download tried to go to an unexpected site, so it was stopped. Nothing was changed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(SafeRedirect)


def open_url(url, accept="*/*"):
    return _opener.open(urllib.request.Request(url, headers={"User-Agent": "VoiceBox-updater", "Accept": accept}), timeout=30)


def download(url, path, cap, sha256=None):
    """Stream url to path.part, then rename; never leaves a half file at path."""
    part = path + ".part"
    h = hashlib.sha256()
    try:
        n = 0
        with open_url(url) as r, open(part, "wb") as f:
            while True:
                b = r.read(1 << 16)
                if not b:
                    break
                n += len(b)
                if n > cap:
                    raise UpdateError("The download was bigger than expected, so it was stopped. Nothing was changed.")
                f.write(b)
                h.update(b)
        if n == 0:
            raise UpdateError("The download was empty. Nothing was changed.")
        if sha256 and h.hexdigest() != sha256:
            raise UpdateError("The download did not match its checksum, so it was not used.")
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.remove(part)


def installed_version(app=APP):
    try:
        with open(os.path.join(app, "VERSION")) as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"  # an install without a VERSION counts as "update available"


def latest_version():
    with open_url(CHECK_URL, "application/vnd.github.sha") as r:
        sha = r.read(200).decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise UpdateError("GitHub gave an answer VoiceBox did not understand. Try again later.")
    return sha


def unpack(tgz, dest):
    """Extract only plain files and folders under one top folder. Returns (top folder, commit sha)."""
    unsafe = UpdateError("The update file has unsafe contents, so it was not used. Nothing was changed.")
    try:
        t = tarfile.open(tgz, "r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise UpdateError("The update file is damaged. Nothing was changed.")
    with t:
        members = t.getmembers()
        tops = {m.name.split("/")[0] for m in members}
        if len(tops) != 1 or sum(m.size for m in members) > MAX_UNPACKED:
            raise unsafe
        real = os.path.realpath(dest)
        for m in members:
            parts = m.name.split("/")
            out = os.path.realpath(os.path.join(dest, *parts))
            if m.name.startswith("/") or ".." in parts or "\\" in m.name or not (m.isfile() or m.isdir()) \
                    or not out.startswith(real + os.sep):
                raise unsafe  # no symlinks, hard links, devices or paths that climb out
        for m in members:
            out = os.path.join(dest, *m.name.split("/"))
            if m.isdir():
                os.makedirs(out, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with t.extractfile(m) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            os.chmod(out, 0o755 if m.mode & 0o111 else 0o644)
        sha = t.pax_headers.get("comment", "")  # GitHub puts the commit id here
    return os.path.join(dest, tops.pop()), (sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def check_app(root, scratch):
    """The new version must have its files, compile, and actually answer in a test run (with throwaway data)."""
    for f in REQUIRED:
        if not os.path.isfile(os.path.join(root, f)) or not os.path.getsize(os.path.join(root, f)):
            raise UpdateError("The update is missing %s, so it was not used. Nothing was changed." % f)
    try:
        py_compile.compile(os.path.join(root, "server.py"), cfile=os.path.join(scratch, "check.pyc"), doraise=True)
    except py_compile.PyCompileError:
        raise UpdateError("The update has a broken server.py, so it was not used. Nothing was changed.")
    port = free_port()
    env = dict(os.environ, VOICEBOX_HOME=os.path.join(scratch, "test-home"), VOICEBOX_PORT=str(port))
    p = subprocess.Popen([sys.executable, "-I", os.path.join(root, "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(120):  # up to 30 s: the Surface is slow
            time.sleep(0.25)
            if p.poll() is not None:
                break
            try:
                if valid(json.load(local.open("http://127.0.0.1:%d/data" % port, timeout=3))) \
                        and b"<" in local.open("http://127.0.0.1:%d/" % port, timeout=3).read():
                    return
            except Exception:
                continue
    finally:
        p.kill()
        p.wait()
    raise UpdateError("The new version did not start in a test run, so it was not used. Nothing was changed.")


def fetch_voices(root):
    """Download the voices in the update's voices.txt that are missing (Piper; Kokoro files only if
    install.sh set up its venv). Never blocks the update. Returns notes for the family."""
    notes = []
    table = voice_table(os.path.join(root, "voices.txt"))
    for name, vid, spec in table:
        if not spec or not os.path.isdir(PIPER_DIR):
            continue
        onnx, js = os.path.join(PIPER_DIR, vid + ".onnx"), os.path.join(PIPER_DIR, vid + ".onnx.json")
        if os.path.isfile(js) and os.path.isfile(onnx) and os.path.getsize(onnx) > 1000000:
            continue
        try:
            download(VOICES_URL + spec + ".onnx.json", js, 1024 * 1024)  # json first: Piper needs it once .onnx appears
            with open(js, encoding="utf-8") as f:
                json.load(f)
            download(VOICES_URL + spec + ".onnx", onnx, MAX_VOICE)
            if os.path.getsize(onnx) < 1000000:
                os.remove(onnx)
                raise ValueError("too small")
            notes.append("New voice: " + name)
        except Exception as e:
            sys.stderr.write("voice %s failed: %s\n" % (vid, e))
            if not os.path.exists(onnx) and os.path.exists(js):
                os.remove(js)  # no half voice left behind
            notes.append("Voice %s could not download (try again later)" % name)
    if any(not spec for _, _, spec in table) and os.path.isdir(os.path.join(KOKORO_DIR, "venv")):
        for f, sha in KOKORO_FILES.items():
            if os.path.isfile(os.path.join(KOKORO_DIR, f)):
                continue
            try:
                download(KOKORO_URL + f, os.path.join(KOKORO_DIR, f), MAX_KOKORO, sha)
                notes.append("Downloaded " + f)
            except Exception as e:
                sys.stderr.write("kokoro %s failed: %s\n" % (f, e))
                notes.append("Kokoro file %s could not download (try again later)" % f)
    return notes


def rm(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def swap_in(src, version):
    """Keep the current app as app.prev and put src's files in. All copying happens first
    (a full disk changes nothing); then only renames, like install.sh's .new pattern."""
    stage = PREV + ".new"
    news = [os.path.join(APP, n + ".new") for n in APP_FILES] + [os.path.join(APP, "VERSION.new")]
    try:
        rm(stage)
        shutil.copytree(APP, stage, symlinks=True)
        for n in APP_FILES:
            s, d = os.path.join(src, n), os.path.join(APP, n + ".new")
            rm(d)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            elif os.path.isfile(s):
                shutil.copy2(s, d)
        os.chmod(os.path.join(APP, "kiosk.sh.new"), 0o755)
        with open(os.path.join(APP, "VERSION.new"), "w") as f:
            f.write(version + "\n")
    except OSError as e:
        for p in news + [stage]:
            try:
                rm(p)
            except OSError:
                pass
        raise UpdateError("Could not copy the files (%s). Nothing was changed." % (e.strerror or e))
    try:
        for n in APP_FILES + ("VERSION",):
            new, cur = os.path.join(APP, n + ".new"), os.path.join(APP, n)
            if not os.path.lexists(new):
                rm(cur)  # not in the new version (e.g. undo to one without voices.txt)
                continue
            if os.path.isdir(new):
                rm(cur + ".old")
                if os.path.lexists(cur):
                    os.rename(cur, cur + ".old")
                os.rename(new, cur)
                rm(cur + ".old")
            else:
                os.replace(new, cur)  # a running kiosk.sh keeps reading its old copy
        rm(PREV)
        os.rename(stage, PREV)
    except OSError as e:
        raise UpdateError("The update stopped half way (%s). Tap Undo last update, or run install.sh." % (e.strerror or e))


def restart_soon():
    def bye():
        with lock:  # never in the middle of writing data.json
            os._exit(0)  # voicebox.service has Restart=always, so systemd starts the new version
    threading.Timer(0.5, bye).start()


def run_update(path):
    """Returns (json, code). Only one update at a time."""
    if os.path.realpath(APP) != os.path.realpath(os.path.join(HOME, "app")):
        return {"error": "Updates only work on the installed VoiceBox (in %s)." % os.path.join(HOME, "app")}, 400
    if not update_lock.acquire(blocking=False):
        return {"error": "An update is already running."}, 409
    scratch = None
    try:
        if path == "/update/check":
            cur, latest = installed_version(), latest_version()
            return {"current": cur, "latest": latest, "available": cur != latest,
                    "can_undo": os.path.isdir(PREV), "previous": installed_version(PREV)}, 200
        scratch = tempfile.mkdtemp(prefix="update-", dir=HOME)
        notes = []
        if path == "/update/undo":
            if not os.path.isdir(PREV):
                return {"error": "There is no earlier version to go back to."}, 400
            root, version = PREV, installed_version(PREV)
            check_app(root, scratch)
            swap_in(root, version)
        else:
            tgz = os.path.join(scratch, "update.tar.gz")
            download(UPDATE_URL, tgz, MAX_TARBALL)
            os.mkdir(os.path.join(scratch, "x"))
            root, version = unpack(tgz, os.path.join(scratch, "x"))
            check_app(root, scratch)
            notes = fetch_voices(root)
            swap_in(root, version)
        restart_soon()
        return {"ok": True, "version": version, "notes": notes}, 200
    except UpdateError as e:
        return {"error": str(e)}, 502
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        sys.stderr.write("update download failed: %r\n" % e)
        return {"error": "Could not reach GitHub (no internet?). Nothing was changed. Try again later."}, 502
    except Exception as e:  # before the swap (swap_in only raises UpdateError), so nothing changed
        sys.stderr.write("update failed: %r\n" % e)
        return {"error": "The update did not work (%s). Nothing was changed." % e}, 502
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        update_lock.release()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC, **kw)

    def log_message(self, fmt, *args):
        pass  # keep the journal quiet; errors still go to stderr

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_only(self, post=False):
        # Block other web pages and DNS rebinding: exact Host, exact Origin, JSON only.
        ok = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
        if self.headers.get("Host") not in ok:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in tuple("http://" + h for h in ok):
            return False
        if post and (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("too big")
        d = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(d, dict):
            raise ValueError("JSON object expected")
        return d

    def do_GET(self):
        if not self.local_only():
            return self.send_error(403)
        if self.path == "/data":
            return self.send_json(load())
        if self.path == "/backups":
            return self.send_json([{"name": os.path.relpath(b, BACKUPS),
                                    "time": datetime.datetime.fromtimestamp(os.path.getmtime(b)).strftime("%Y-%m-%d %H:%M"),
                                    "size": os.path.getsize(b)} for b in list_backups()])
        if self.path == "/voices":
            return self.send_json(voices())
        if self.path == "/prepare":
            return self.send_json(prep)
        if self.path == "/version":
            return self.send_json({"version": installed_version()})
        return super().do_GET()

    def do_POST(self):
        if not self.local_only(post=True):
            return self.send_error(403)
        try:
            req = self.body()
        except Exception:
            return self.send_json({"error": "bad request"}, 400)
        if self.path == "/data":
            if not valid(req):
                return self.send_json({"error": "invalid data"}, 400)
            try:
                save(req)
            except OSError as e:  # disk full or read-only: say so, the page keeps its copy and retries
                sys.stderr.write("save failed: %s\n" % e)
                return self.send_json({"error": "could not write data: %s" % e.strerror}, 507)
            prep_wake.set()  # phrases or voice may have changed
            return self.send_json({"ok": True})
        if self.path == "/speak":
            text = str(req.get("text") or "").strip()[:1000]
            if not text:
                return self.send_json({"error": "no text"}, 400)
            last_live[0] = time.time()
            try:  # Kokoro gets a share of the page's timeout; then Piper answers instead
                wav = speak(text, req.get("rate"), str(req.get("voice") or ""), min(8.0, 1.5 + 0.03 * len(text)))
            except Exception as e:
                sys.stderr.write("piper failed: %s\n" % e)
                wav = None
            if not wav:
                return self.send_json({"error": "piper unavailable"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
            return
        if self.path == "/restore":
            names = {os.path.relpath(b, BACKUPS): b for b in list_backups()}
            path = names.get(req.get("name"))
            if not path:
                return self.send_json({"error": "no such backup"}, 404)
            try:
                d = read_json(path)
            except Exception:
                return self.send_json({"error": "backup is damaged"}, 400)
            try:
                with lock:
                    backup_current(force=True)  # the state we are replacing stays recoverable
                    write_atomic(DATA, json.dumps(d, ensure_ascii=False))
            except OSError as e:
                return self.send_json({"error": "could not write data: %s" % e.strerror}, 507)
            prep_wake.set()
            return self.send_json({"ok": True})
        if self.path == "/exit-kiosk":
            os.makedirs(HOME, exist_ok=True)
            open(EXIT_FLAG, "w").close()
            self.send_json({"ok": True})
            threading.Timer(0.5, kill_browser).start()
            return
        if self.path in ("/update/check", "/update/install", "/update/undo"):
            return self.send_json(*run_update(self.path))
        if self.path == "/ai":
            # Hook for an optional online helper later. Offline core never depends on it.
            if not os.path.exists(AI_KEY):
                return self.send_json({"error": "AI helper not set up"}, 501)
            return self.send_json({"error": "AI helper not built yet"}, 501)
        return self.send_error(404)


def main():
    os.makedirs(HOME, exist_ok=True)
    load()  # recover a corrupt file at startup, not on first tap
    prune_cache()
    threading.Thread(target=prerender_loop, daemon=True).start()
    prep_wake.set()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("VoiceBox on http://127.0.0.1:%d (data in %s)" % (PORT, HOME), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
