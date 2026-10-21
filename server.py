#!/usr/bin/env python3
"""VoiceBox local server: static page, Piper speech, and one JSON data file with backups.

Stdlib only. Binds to 127.0.0.1 so nothing outside this machine can reach it.
"""
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HOME = os.environ.get("VOICEBOX_HOME") or os.path.expanduser("~/.local/share/voicebox")
PORT = int(os.environ.get("VOICEBOX_PORT", "8765"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA = os.path.join(HOME, "data.json")
BACKUPS = os.path.join(HOME, "backups")
DAILY = os.path.join(BACKUPS, "daily")
PIPER_DIR = os.path.join(HOME, "piper")
PIPER_BIN = os.path.join(PIPER_DIR, "piper", "piper")
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
tts_cache = {}  # (text, rate, voice) -> wav bytes; repeated phrases play instantly


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
        shutil.copy2(DATA, os.path.join(BACKUPS, now.strftime("data-%Y%m%d-%H%M.json")))
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
            pass
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
                sys.stderr.write("data.json was corrupt; restored from %s\n" % b)
                return d
        d = json.loads(json.dumps(DEFAULT_DATA))
        write_atomic(DATA, json.dumps(d))
        return d


def voices():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(PIPER_DIR, "*.onnx")))


def piper_wav(text, rate, voice):
    names = voices()
    if not os.path.exists(PIPER_BIN) or not names:
        return None
    if voice not in names:
        voice = "en_US-ryan-medium" if "en_US-ryan-medium" in names else names[0]
    rate = min(max(float(rate or 1.0), 0.5), 2.0)
    key = (text, rate, voice)
    if key in tts_cache:
        return tts_cache[key]
    r = subprocess.run(
        [PIPER_BIN, "-q", "-m", os.path.join(PIPER_DIR, voice + ".onnx"), "-f", "-",
         "--length_scale", "%.3f" % (1.0 / rate)],
        input=text.replace("\n", " ").encode(), capture_output=True, timeout=30)
    if r.returncode != 0 or r.stdout[:4] != b"RIFF":
        return None
    if len(tts_cache) > 300:
        tts_cache.clear()
    tts_cache[key] = r.stdout
    return r.stdout


def kill_browser():
    for args in (["pkill", "-f", "voicebox-profile"], ["flatpak", "kill", "org.chromium.Chromium"]):
        try:
            subprocess.run(args, timeout=5, capture_output=True)
        except Exception:
            pass


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
            return self.send_json(voices() if os.path.exists(PIPER_BIN) else [])
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
            return self.send_json({"ok": True})
        if self.path == "/speak":
            text = str(req.get("text") or "").strip()[:1000]
            if not text:
                return self.send_json({"error": "no text"}, 400)
            try:
                wav = piper_wav(text, req.get("rate"), str(req.get("voice") or ""))
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
            return self.send_json({"ok": True})
        if self.path == "/exit-kiosk":
            os.makedirs(HOME, exist_ok=True)
            open(EXIT_FLAG, "w").close()
            self.send_json({"ok": True})
            threading.Timer(0.5, kill_browser).start()
            return
        if self.path == "/ai":
            # Hook for an optional online helper later. Offline core never depends on it.
            if not os.path.exists(AI_KEY):
                return self.send_json({"error": "AI helper not set up"}, 501)
            return self.send_json({"error": "AI helper not built yet"}, 501)
        return self.send_error(404)


def main():
    os.makedirs(HOME, exist_ok=True)
    load()  # recover a corrupt file at startup, not on first tap
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("VoiceBox on http://127.0.0.1:%d (data in %s)" % (PORT, HOME), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
