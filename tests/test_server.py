#!/usr/bin/env python3
"""Self-test for server.py: save/load, backups, corrupt-file recovery, restore, speak, safety checks.

Run: python3 tests/test_server.py   (uses a temp folder, never touches real data)
"""
import datetime
import glob
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_PIPER = os.path.expanduser("~/.local/share/voicebox/piper")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="vbtest-")
        self.port = free_port()
        self.proc = None

    def tearDown(self):
        self.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    script = os.path.join(ROOT, "server.py")
    extra_env = {}

    def start(self):
        env = dict(os.environ, VOICEBOX_HOME=self.home, VOICEBOX_PORT=str(self.port), **self.extra_env)
        self.proc = subprocess.Popen([sys.executable, self.script], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        self.fail("server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(5)
            self.proc = None

    def req(self, path, data=None, headers=None, raw=False):
        body = None if data is None else json.dumps(data).encode()
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=body,
                                   headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class ServerTest(Base):
    def test_defaults_save_load(self):
        self.start()
        code, d = self.req("/data")
        self.assertEqual(code, 200)
        names = [c["name"] for c in d["categories"]]
        self.assertIn("Needs", names)
        self.assertIn("About Me", names)
        next(c for c in d["categories"] if c["id"] == "people")["phrases"].append("Test Name")
        d["words"]["hello"] = 3
        self.assertEqual(self.req("/data", d)[0], 200)
        self.stop()
        self.start()  # survives restart
        code, d2 = self.req("/data")
        self.assertIn("Test Name", next(c for c in d2["categories"] if c["id"] == "people")["phrases"])
        self.assertEqual(d2["words"]["hello"], 3)

    def test_rejects_bad_data(self):
        self.start()
        self.assertEqual(self.req("/data", {"nope": 1})[0], 400)
        self.assertEqual(self.req("/data")[1]["categories"][0]["name"], "Needs")

    def test_valid_is_strict(self):
        self.start()
        _, d = self.req("/data")
        bad = [
            {"categories": [None]},
            {"categories": [{"id": "x", "name": "X", "phrases": [1]}]},
            {"categories": [{"id": 3, "name": "X", "phrases": []}]},
            {"categories": [{"name": "X", "phrases": []}]},
            dict(d, words=[]), dict(d, history={}), dict(d, history=[None]), dict(d, bigrams={"a": 1}),
            dict(d, settings="big"), dict(d, draft=5),
        ]
        for b in bad:
            self.assertEqual(self.req("/data", b)[0], 400, b)
        self.assertEqual(self.req("/data")[1], d)  # nothing bad got saved

    def test_non_object_body(self):
        self.start()
        for path in ("/data", "/restore", "/speak", "/exit-kiosk"):
            self.assertEqual(self.req(path, [1])[0], 400, path)
            self.assertEqual(self.req(path, "text")[0], 400, path)
        self.assertFalse(os.path.exists(os.path.join(self.home, "exit-kiosk")))

    def test_disk_error_is_507(self):
        self.start()
        _, d = self.req("/data")
        os.mkdir(os.path.join(self.home, "data.json.tmp"))  # the write fails, even as root
        self.assertEqual(self.req("/data", d)[0], 507)
        os.rmdir(os.path.join(self.home, "data.json.tmp"))
        self.assertEqual(self.req("/data", d)[0], 200)

    def test_backups_and_corrupt_recovery(self):
        self.start()
        _, d = self.req("/data")
        d["words"]["first"] = 1
        self.req("/data", d)            # backs up the defaults
        d["words"]["second"] = 2
        self.req("/data", d)            # within 10 min: no new rolling backup
        rolling = glob.glob(os.path.join(self.home, "backups", "data-*.json"))
        daily = glob.glob(os.path.join(self.home, "backups", "daily", "data-*.json"))
        self.assertEqual(len(rolling), 1)
        self.assertEqual(len(daily), 1)
        code, bl = self.req("/backups")
        self.assertEqual(len(bl), 2)
        # make the backup hold something recognisable, then corrupt the live file
        good = dict(d, words={"rescued": 9})
        with open(rolling[0], "w") as f:
            json.dump(good, f)
        os.utime(rolling[0], (time.time() + 5, time.time() + 5))  # newest
        self.stop()
        with open(os.path.join(self.home, "data.json"), "w") as f:
            f.write('{"categories": [ broken')
        self.start()
        code, d3 = self.req("/data")
        self.assertEqual(d3["words"], {"rescued": 9})
        self.assertTrue(glob.glob(os.path.join(self.home, "data.json.corrupt-*")), "corrupt file kept")
        # a deleted data.json also comes back from the newest backup, not from defaults
        self.stop()
        os.remove(os.path.join(self.home, "data.json"))
        self.start()
        code, d4 = self.req("/data")
        self.assertEqual(d4["words"], {"rescued": 9})

    def test_restore(self):
        self.start()
        _, d = self.req("/data")
        self.req("/data", dict(d, words={"v1": 1}))
        name = self.req("/backups")[1][0]["name"]
        self.assertEqual(self.req("/restore", {"name": "../../etc/passwd"})[0], 404)
        self.assertEqual(self.req("/restore", {"name": name})[0], 200)
        self.assertEqual(self.req("/data")[1]["words"], {})  # back to defaults

    def test_rotation_limits(self):
        os.environ["VOICEBOX_HOME"] = self.home
        sys.path.insert(0, ROOT)
        import importlib
        import server
        importlib.reload(server)
        bdir = os.path.join(self.home, "backups")
        os.makedirs(os.path.join(bdir, "daily"))
        base = datetime.datetime(2026, 1, 1)
        for i in range(205):
            pathlib.Path(os.path.join(bdir, (base + datetime.timedelta(minutes=10 * i)).strftime("data-%Y%m%d-%H%M.json"))).write_text("{}")
        for i in range(70):
            pathlib.Path(os.path.join(bdir, "daily", (datetime.datetime.now() - datetime.timedelta(days=i + 1)).strftime("data-%Y%m%d.json"))).write_text("{}")
        server.save(json.loads(json.dumps(server.DEFAULT_DATA)))  # first save: nothing to back up yet
        server.save(json.loads(json.dumps(server.DEFAULT_DATA)))
        self.assertEqual(len(glob.glob(os.path.join(bdir, "data-*.json"))), 200)
        days = glob.glob(os.path.join(bdir, "daily", "data-*.json"))
        self.assertLessEqual(len(days), 62)
        self.assertTrue(os.path.exists(os.path.join(bdir, "daily", datetime.datetime.now().strftime("data-%Y%m%d.json"))))

    def test_foreign_origin_blocked(self):
        self.start()
        self.assertEqual(self.req("/data", {"categories": []}, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("/data", {"categories": []}, {"Origin": "http://127.0.0.1:1"})[0], 403)
        self.assertEqual(self.req("/data", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("/data", headers={"Host": "127.0.0.1:%d.evil.example" % self.port})[0], 403)
        self.assertEqual(self.req("/data", {"categories": []}, {"Content-Type": "text/plain"})[0], 403)
        ok = self.req("/data", headers={"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(ok[0], 200)

    def test_ai_stub_and_speak_without_piper(self):
        self.start()
        self.assertEqual(self.req("/ai", {"text": "hi"})[0], 501)
        self.assertEqual(self.req("/speak", {"text": "hello", "rate": 1})[0], 503)

    @unittest.skipUnless(os.path.exists(os.path.join(REAL_PIPER, "piper", "piper")), "Piper not downloaded")
    def test_speak_with_piper(self):
        os.symlink(REAL_PIPER, os.path.join(self.home, "piper"))
        self.start()
        code, wav = self.req("/speak", {"text": "My pain is 7 out of 10.", "rate": 1.2}, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertGreater(len(wav), 20000)
        self.assertIn({"id": "en_US-ryan-medium", "name": "Ryan"}, self.req("/voices")[1])


def tree(d):
    out = {}
    for base, _, files in os.walk(d):
        for f in files:
            p = os.path.join(base, f)
            out[os.path.relpath(p, d)] = pathlib.Path(p).read_bytes()
    return out


class UpdateTest(Base):
    """Self-update from local fake tarballs (file:// URLs), never the internet."""
    OLD = "a" * 40
    NEW = "b" * 40

    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.home, "app")
        os.makedirs(self.app)
        for f in ("server.py", "kiosk.sh", "kokoro_worker.py"):
            shutil.copy2(os.path.join(ROOT, f), self.app)
        shutil.copytree(os.path.join(ROOT, "static"), os.path.join(self.app, "static"))
        pathlib.Path(self.app, "VERSION").write_text(self.OLD + "\n")
        os.makedirs(os.path.join(self.home, "piper"))
        self.work = tempfile.mkdtemp(prefix="vbupd-")
        self.vdir = os.path.join(self.work, "voices")
        os.makedirs(os.path.join(self.vdir, "en_US/fake/medium"))
        pathlib.Path(self.vdir, "en_US/fake/medium/en_US-fake-medium.onnx").write_bytes(b"x" * 1500000)
        pathlib.Path(self.vdir, "en_US/fake/medium/en_US-fake-medium.onnx.json").write_text('{"audio": {}}')
        self.tgz = os.path.join(self.work, "stable.tar.gz")
        pathlib.Path(self.work, "sha").write_text(self.NEW + "\n")
        self.script = os.path.join(self.app, "server.py")
        self.extra_env = {"VOICEBOX_UPDATE_URL": "file://" + self.tgz,
                          "VOICEBOX_UPDATE_CHECK_URL": "file://" + os.path.join(self.work, "sha"),
                          "VOICEBOX_VOICES_URL": "file://" + self.vdir + "/"}

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.work, ignore_errors=True)

    def make_tarball(self, change=None, extra=None):
        """A GitHub-like tarball of the repo with index.html marked; change/extra tweak it."""
        files = {"server.py": pathlib.Path(ROOT, "server.py").read_bytes(),
                 "kiosk.sh": pathlib.Path(ROOT, "kiosk.sh").read_bytes(),
                 "kokoro_worker.py": pathlib.Path(ROOT, "kokoro_worker.py").read_bytes(),
                 "voices.txt": b"# test\nFake | en_US/fake/medium/en_US-fake-medium\nBad | ../../evil\n"}
        for f in os.listdir(os.path.join(ROOT, "static")):
            files["static/" + f] = pathlib.Path(ROOT, "static", f).read_bytes()
        files["static/index.html"] += b"<!-- v2 -->"
        files.update(change or {})
        with tarfile.open(self.tgz, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": self.NEW}) as t:
            for name, data in files.items():
                if data is None:
                    continue
                ti = tarfile.TarInfo("voicebox-stable/" + name)
                ti.size = len(data)
                ti.mode = 0o755 if name.endswith(".sh") else 0o644
                t.addfile(ti, io.BytesIO(data))
            for ti in extra or []:
                t.addfile(ti, None)

    def wait_exit(self):
        self.proc.wait(10)  # the server restarts itself by exiting (systemd Restart=always)
        self.proc = None

    def test_check(self):
        self.make_tarball()
        self.start()
        code, j = self.req("/update/check", {})
        self.assertEqual(code, 200)
        self.assertEqual((j["current"], j["latest"], j["available"], j["can_undo"]), (self.OLD, self.NEW, True, False))
        self.assertEqual(self.req("/version")[1], {"version": self.OLD})
        pathlib.Path(self.app, "VERSION").write_text(self.NEW)
        self.assertFalse(self.req("/update/check", {})[1]["available"])
        os.remove(os.path.join(self.app, "VERSION"))
        self.assertTrue(self.req("/update/check", {})[1]["available"], "unknown version means update available")

    def test_install_then_undo(self):
        self.make_tarball()
        self.start()
        _, d = self.req("/data")
        self.req("/data", dict(d, words={"keep": 1}))
        before_data = pathlib.Path(self.home, "data.json").read_bytes()
        before_backups = tree(os.path.join(self.home, "backups"))
        old_app = tree(self.app)
        code, j = self.req("/update/install", {})
        self.assertEqual(code, 200, j)
        self.assertEqual(j["version"], self.NEW)
        self.assertEqual(j["notes"], ["New voice: Fake"])
        self.wait_exit()
        self.assertIn(b"<!-- v2 -->", pathlib.Path(self.app, "static/index.html").read_bytes())
        self.assertEqual(pathlib.Path(self.app, "VERSION").read_text().strip(), self.NEW)
        self.assertEqual(tree(self.app + ".prev"), old_app, "app.prev is the old app")
        self.assertTrue(os.access(os.path.join(self.app, "kiosk.sh"), os.X_OK))
        self.assertEqual(os.path.getsize(os.path.join(self.home, "piper", "en_US-fake-medium.onnx")), 1500000)
        self.assertFalse(glob.glob(os.path.join(self.home, "update-*")), "temp files cleaned")
        self.assertFalse(glob.glob(os.path.join(self.home, "piper", "*.part")))
        self.start()
        self.assertEqual(self.req("/version")[1], {"version": self.NEW})
        self.assertEqual(self.req("/data")[1]["words"], {"keep": 1})
        self.assertEqual(pathlib.Path(self.home, "data.json").read_bytes(), before_data, "data.json untouched")
        self.assertEqual(tree(os.path.join(self.home, "backups")), before_backups, "backups untouched")
        new_app = tree(self.app)
        code, j = self.req("/update/undo", {})
        self.assertEqual(code, 200, j)
        self.wait_exit()
        self.assertEqual(tree(self.app), old_app, "undo restores the old app exactly")
        self.assertEqual(tree(self.app + ".prev"), new_app, "undo keeps the newer one, so it can be redone")
        self.start()
        self.assertEqual(self.req("/version")[1], {"version": self.OLD})
        self.assertEqual(pathlib.Path(self.home, "data.json").read_bytes(), before_data)

    def test_bad_updates_change_nothing(self):
        sym = tarfile.TarInfo("voicebox-stable/static/link")
        sym.type, sym.linkname = tarfile.SYMTYPE, "/etc/passwd"
        climb = tarfile.TarInfo("voicebox-stable/../../evil.txt")
        other = tarfile.TarInfo("other/evil.txt")
        cases = [
            ("missing app.js", {"static/app.js": None}, None),
            ("broken python", {"server.py": b"def (:\n"}, None),
            ("server that does not start", {"server.py": b"import sys\nsys.exit(1)\n"}, None),
            ("symlink", None, [sym]),
            ("path traversal", None, [climb]),
            ("second top folder", None, [other]),
        ]
        self.start()
        app, data = tree(self.app), pathlib.Path(self.home, "data.json").read_bytes()
        for why, change, extra in cases:
            self.make_tarball(change, extra)
            code, j = self.req("/update/install", {})
            self.assertEqual(code, 502, why)
            self.assertIn("Nothing was changed", j.decode(), why)
            self.assertIsNone(self.proc.poll(), why + ": server must keep running")
            self.assertEqual(tree(self.app), app, why)
            self.assertFalse(os.path.exists(self.app + ".prev"), why)
            self.assertEqual(os.listdir(os.path.join(self.home, "piper")), [], why + ": no voices before checks pass")
        self.assertFalse(glob.glob(os.path.join(self.home, "..", "evil.txt")))
        self.assertFalse(glob.glob(os.path.join(self.home, "update-*")))
        pathlib.Path(self.tgz).write_bytes(b"not a tarball")
        self.assertEqual(self.req("/update/install", {})[0], 502)
        os.remove(self.tgz)
        self.assertIn("Nothing was changed", self.req("/update/install", {})[1].decode())
        self.assertEqual(self.req("/update/undo", {})[0], 400)  # nothing to undo yet
        self.assertEqual(tree(self.app), app)
        self.assertEqual(pathlib.Path(self.home, "data.json").read_bytes(), data)

    def test_update_endpoints_guarded(self):
        self.make_tarball()
        self.start()
        for path in ("/update/check", "/update/install", "/update/undo"):
            self.assertEqual(self.req(path, {}, {"Origin": "http://evil.example"})[0], 403, path)
            self.assertEqual(self.req(path, {}, {"Host": "evil.example"})[0], 403, path)
            self.assertEqual(self.req(path, {}, {"Content-Type": "text/plain"})[0], 403, path)
        self.assertIsNone(self.proc.poll())
        self.assertFalse(os.path.exists(self.app + ".prev"))

    def test_redirects_only_to_known_sites(self):
        sys.path.insert(0, ROOT)
        import server
        self.assertTrue(server.host_ok("https://codeload.github.com/x"))
        self.assertTrue(server.host_ok("https://us.aws.cdn.hf.co/x"))
        for bad in ("http://github.com/x", "https://evil.example/x", "https://github.com.evil.example/x",
                    "https://evilhf.co/x", "file:///etc/passwd"):
            self.assertFalse(server.host_ok(bad), bad)


FAKE_WORKER = """import json, os, sys, time, wave
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    if "crash" in r["text"]:
        sys.exit(1)
    if "slow" in r["text"]:
        time.sleep(5)
    with wave.open(r["out"] + ".tmp", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(bytes(4800))
    os.replace(r["out"] + ".tmp", r["out"])
    print(json.dumps({"ok": True}), flush=True)
"""
SMALL = {"categories": [{"id": "people", "name": "People", "phrases": ["Alpha one", "Bravo two"]}],
         "settings": {"voice": "kokoro-am_michael", "rate": 1.0}}


@unittest.skipUnless(os.path.exists(os.path.join(REAL_PIPER, "piper", "piper")), "Piper not downloaded")
class SpeechTest(Base):
    """Disk cache, pre-render, and Kokoro with a fake worker (the real model is too big for a unit test)."""

    def setUp(self):
        super().setUp()
        os.symlink(REAL_PIPER, os.path.join(self.home, "piper"))

    def cached(self, voice):
        return glob.glob(os.path.join(self.home, "cache", voice, "*.wav"))

    def wait_prepared(self):
        for _ in range(300):
            p = self.req("/prepare")[1]
            if p["total"] and p["done"] == p["total"]:
                return p
            time.sleep(0.1)
        self.fail("pre-render did not finish: %s" % p)

    def test_disk_cache_and_fallback_without_kokoro(self):
        self.start()
        self.assertEqual(self.req("/data", SMALL)[0], 200)  # Michael picked, but Kokoro is not installed
        self.assertFalse([v for v in self.req("/voices")[1] if v["id"].startswith("kokoro-")])
        p = self.wait_prepared()
        self.assertEqual((p["voice"], p["total"]), ("en_US-ryan-medium", 6))  # 4 big buttons + 2 phrases, in Ryan
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 6)
        code, wav = self.req("/speak", {"text": "Something new.", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)
        self.assertEqual((code, wav[:4]), (200, b"RIFF"))
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 7)
        self.stop()
        os.remove(os.path.join(self.home, "piper"))  # no Piper at all now: the disk cache still speaks
        self.start()
        self.assertEqual(self.req("/speak", {"text": "Alpha one", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)[0], 200)
        self.assertEqual(self.req("/speak", {"text": "Yes", "rate": 1}, raw=True)[0], 200)
        self.assertEqual(self.req("/speak", {"text": "Never said before", "rate": 1})[0], 503)

    def test_kokoro_worker(self):
        kdir = os.path.join(self.home, "kokoro")
        os.makedirs(os.path.join(kdir, "venv", "bin"))
        os.symlink(sys.executable, os.path.join(kdir, "venv", "bin", "python3"))
        for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            pathlib.Path(kdir, f).write_text("fake")
        worker = os.path.join(self.home, "fake_worker.py")
        pathlib.Path(worker).write_text(FAKE_WORKER)
        self.extra_env = {"VOICEBOX_KOKORO_WORKER": worker}
        self.start()
        self.assertIn({"id": "kokoro-am_michael", "name": "Michael"}, self.req("/voices")[1])
        self.req("/data", SMALL)
        p = self.wait_prepared()
        self.assertEqual((p["voice"], p["total"]), ("kokoro-am_michael", 10))  # + Ryan copies of the 4 big buttons
        self.assertEqual(len(self.cached("kokoro-am_michael")), 6)
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 4)
        fake_len = 44 + 4800
        self.assertEqual(len(self.req("/speak", {"text": "Alpha one", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)[1]), fake_len)
        # too slow: Piper answers within the wait, and the Kokoro version lands in the cache afterwards
        t = time.time()
        code, wav = self.req("/speak", {"text": "slow one", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)
        self.assertEqual(code, 200)
        self.assertLess(time.time() - t, 4.5)
        self.assertNotEqual(len(wav), fake_len)
        time.sleep(4)
        self.assertEqual(len(self.req("/speak", {"text": "slow one", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)[1]), fake_len)
        # the worker dies: Piper still answers, every time
        for text in ("crash now", "after the crash"):
            code, wav = self.req("/speak", {"text": text, "rate": 1, "voice": "kokoro-am_michael"}, raw=True)
            self.assertEqual((code, wav[:4]), (200, b"RIFF"), text)
            self.assertNotEqual(len(wav), fake_len, text)


class RepoCopyTest(Base):
    def test_no_update_from_repo_checkout(self):
        self.start()  # server.py run from the repo, not ~/.local/share/voicebox/app
        self.assertEqual(self.req("/update/install", {})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
