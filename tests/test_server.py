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


def wav_bytes(n=4800):
    import wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(bytes(n))
    return b.getvalue()


def put_cached(home, text, voice="en_US-ryan-medium", rate=1.0):
    import hashlib
    p = os.path.join(home, "cache", voice, hashlib.sha1(("%s\n%.2f" % (text, rate)).encode()).hexdigest() + ".wav")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pathlib.Path(p).write_bytes(wav_bytes())


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
        self.assertFalse(glob.glob(os.path.join(self.home, "update-tmp-*")), "temp files cleaned")
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
        self.assertFalse(glob.glob(os.path.join(self.home, "update-tmp-*")))
        pathlib.Path(self.tgz).write_bytes(b"not a tarball")
        self.assertEqual(self.req("/update/install", {})[0], 502)
        os.remove(self.tgz)
        self.assertIn("Nothing was changed", self.req("/update/install", {})[1].decode())
        self.assertEqual(self.req("/update/undo", {})[0], 400)  # nothing to undo yet
        self.assertEqual(tree(self.app), app)
        self.assertEqual(pathlib.Path(self.home, "data.json").read_bytes(), data)

    def test_update_must_load_his_data(self):
        """The test run uses a copy of the real data.json, so a version that chokes on it is refused."""
        crash = b"import os, sys\nif 'crashme' in open(os.path.join(os.environ['VOICEBOX_HOME'], 'data.json')).read(): sys.exit(1)\n"
        self.make_tarball({"server.py": crash + pathlib.Path(ROOT, "server.py").read_bytes()})
        self.start()
        _, d = self.req("/data")
        self.req("/data", dict(d, words={"crashme": 1}))
        app = tree(self.app)
        code, j = self.req("/update/install", {})
        self.assertEqual(code, 502)
        self.assertIn(b"did not start in a test run", j)
        self.assertEqual(tree(self.app), app)

    def test_health_is_tied_to_its_version(self):
        self.make_tarball()
        put_cached(self.home, "Yes")
        self.start()
        pending = os.path.join(self.home, "update-pending")
        self.assertEqual(self.req("/update/install", {})[0], 200)
        self.assertEqual(pathlib.Path(pending).read_text().split(), [self.NEW, "0"])  # written before the swap
        # the OLD server (still up for 0.5 s) must not clear the NEW version's marker
        self.req("/speak", {"text": "Yes", "rate": 1}, raw=True)
        self.req("/healthy", {})
        self.wait_exit()
        self.assertTrue(os.path.exists(pending))
        self.start()
        self.assertFalse(self.req("/healthy", {})[1]["cleared"], "no /speak yet: not healthy")
        self.assertTrue(os.path.exists(pending))
        self.assertEqual(self.req("/speak", {"text": "Yes", "rate": 1}, raw=True)[0], 200)
        self.assertTrue(self.req("/healthy", {})[1]["cleared"])
        self.assertFalse(os.path.exists(pending))

    def guard(self, *args, path=None):
        g = os.path.join(self.home, "bootguard.sh")
        shutil.copy2(os.path.join(ROOT, "bootguard.sh"), g)
        env = dict(os.environ, PATH=path) if path else None
        subprocess.run(["bash", g, *args], check=True, env=env, stderr=subprocess.DEVNULL)

    def test_bootguard_rolls_back_a_crashing_update(self):
        self.make_tarball()
        self.start()
        old_app = tree(self.app)
        self.assertEqual(self.req("/update/install", {})[0], 200)
        self.wait_exit()
        new_app, pending = tree(self.app), os.path.join(self.home, "update-pending")
        # systemd keeps starting the broken one; bootguard.sh puts app.prev back on the 4th start
        for n in (1, 2, 3):
            self.guard()
            self.assertEqual(tree(self.app), new_app, n)
        self.assertEqual(pathlib.Path(pending).read_text().split(), [self.NEW, "3"])
        self.guard()
        self.assertEqual(tree(self.app), old_app)
        self.assertEqual(tree(self.app + ".prev"), new_app)
        self.assertFalse(os.path.exists(pending))
        self.guard()  # no marker: does nothing
        self.assertEqual(tree(self.app), old_app)

    def test_bootguard_survives_an_interrupted_rollback(self):
        self.make_tarball()
        self.start()
        old_app = tree(self.app)
        self.assertEqual(self.req("/update/install", {})[0], 200)
        self.wait_exit()
        new_app, pending = tree(self.app), os.path.join(self.home, "update-pending")
        # power cut right after step 1 (app -> app.bad): the next start finishes the job
        os.rename(self.app, os.path.join(self.home, "app.bad"))
        self.guard("--now")
        self.assertEqual(tree(self.app), old_app)
        self.assertEqual(tree(self.app + ".prev"), new_app)
        self.assertFalse(os.path.exists(pending))
        # a rollback whose second rename fails: nothing is deleted, the marker stays, the next start retries
        os.rename(self.app, self.app + ".tmp")  # make app = new (bad), app.prev = old (good) again
        os.rename(self.app + ".prev", self.app)
        os.rename(self.app + ".tmp", self.app + ".prev")
        fakebin = os.path.join(self.home, "fakebin")
        os.makedirs(fakebin)
        pathlib.Path(fakebin, "mv").write_text('#!/bin/bash\ncase "$1" in *app.prev) exit 1;; esac\nexec /bin/mv "$@"\n')
        os.chmod(os.path.join(fakebin, "mv"), 0o755)
        pathlib.Path(pending).write_text("bad 3\n")
        self.guard("--now", path=fakebin + ":" + os.environ["PATH"])
        self.assertFalse(os.path.exists(self.app), "step 1 done")
        self.assertTrue(os.path.isdir(os.path.join(self.home, "app.bad")) and os.path.isdir(self.app + ".prev"), "nothing deleted")
        self.assertTrue(os.path.exists(pending), "marker kept until app/ is back")
        self.assertIn("ROLLBACK FAILED", pathlib.Path(self.home, "bootguard.log").read_text())
        self.guard()  # the next start, with a working mv
        self.assertEqual(tree(self.app), old_app)
        self.assertFalse(os.path.exists(pending))

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
        self.assertEqual((p["voice"], p["total"]), ("en_US-ryan-medium", 7))  # 5 big buttons + 2 phrases, in Ryan
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 7)
        code, wav = self.req("/speak", {"text": "Something new.", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)
        self.assertEqual((code, wav[:4]), (200, b"RIFF"))
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 8)
        self.stop()
        os.remove(os.path.join(self.home, "piper"))  # no Piper at all now: the disk cache still speaks
        self.start()
        self.assertEqual(self.req("/speak", {"text": "Alpha one", "rate": 1, "voice": "kokoro-am_michael"}, raw=True)[0], 200)
        self.assertEqual(self.req("/speak", {"text": "Yes", "rate": 1}, raw=True)[0], 200)
        self.assertEqual(self.req("/speak", {"text": "Never said before", "rate": 1})[0], 503)

    def test_kokoro_runs_only_when_chosen(self):
        kdir = os.path.join(self.home, "kokoro")
        os.makedirs(os.path.join(kdir, "venv", "bin"))
        os.symlink(sys.executable, os.path.join(kdir, "venv", "bin", "python3"))
        for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            pathlib.Path(kdir, f).write_text("fake")
        worker = os.path.join(self.home, "fake_worker.py")
        pathlib.Path(worker).write_text("import os, sys\nopen(os.path.join(sys.argv[1], 'started.pid'), 'w').write(str(os.getpid()))\n" + FAKE_WORKER)
        self.extra_env = {"VOICEBOX_KOKORO_WORKER": worker, "VOICEBOX_PIPER_WORKER": "/nonexistent"}
        self.start()
        ryan = dict(SMALL, settings={"voice": "", "rate": 1.0})
        self.req("/data", ryan)
        self.wait_prepared()
        time.sleep(0.5)
        pid_file = os.path.join(kdir, "started.pid")
        self.assertFalse(os.path.exists(pid_file), "Ryan chosen: no Kokoro process (no 600 MB)")
        self.req("/data", SMALL)  # Michael
        for _ in range(100):
            p = self.req("/prepare")[1]
            if p["voice"] == "kokoro-am_michael" and p["done"] == p["total"]:
                break
            time.sleep(0.1)
        pid = int(pathlib.Path(pid_file).read_text())
        self.assertTrue(os.path.exists("/proc/%d" % pid))
        self.req("/data", ryan)  # back to Ryan: Kokoro stops
        for _ in range(50):
            if not os.path.exists("/proc/%d" % pid) or "Z" in pathlib.Path("/proc/%d/stat" % pid).read_text().split()[2]:
                break
            time.sleep(0.1)
        else:
            self.fail("Kokoro worker still running after switching to Ryan")

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
        self.assertEqual((p["voice"], p["total"]), ("kokoro-am_michael", 12))  # + Ryan copies of the 5 big buttons
        self.assertEqual(len(self.cached("kokoro-am_michael")), 7)
        self.assertEqual(len(self.cached("en_US-ryan-medium")), 5)
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


class WorkerTest(Base):
    """The fast Piper worker (faked: no real model needed) and the battery reading."""

    def fake_piper(self, sleep, binary=False):
        pdir = os.path.join(self.home, "piper")
        os.makedirs(pdir)
        pathlib.Path(pdir, "en_US-ryan-medium.onnx").write_text("fake")
        if binary:  # a fake piper program: answers at once with a different (longer) clip
            os.makedirs(os.path.join(pdir, "piper"))
            b = os.path.join(pdir, "piper", "piper")
            pathlib.Path(b).write_text("#!%s\nimport sys, io, wave\nsys.stdin.read()\nb = io.BytesIO()\n"
                                       "w = wave.open(b, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)\n"
                                       "w.writeframes(bytes(9000)); w.close(); sys.stdout.buffer.write(b.getvalue())\n" % sys.executable)
            os.chmod(b, 0o755)
        os.makedirs(os.path.join(self.home, "kokoro", "venv", "bin"))
        os.symlink(sys.executable, os.path.join(self.home, "kokoro", "venv", "bin", "python3"))
        worker = os.path.join(self.home, "fake_piper.py")
        pathlib.Path(worker).write_text(FAKE_WORKER.replace("time.sleep(5)", "time.sleep(%d)" % sleep).replace('"slow" in', '"zzslow" in'))
        self.extra_env = {"VOICEBOX_PIPER_WORKER": worker}
        self.start()

    def test_slow_piper_worker_still_answers(self):
        self.fake_piper(4)  # no piper program: the worker is the only way
        t = time.time()
        code, wav = self.req("/speak", {"text": "hi zzslow", "rate": 1}, raw=True)  # not in any default phrase
        took = time.time() - t
        self.assertEqual(code, 200)
        self.assertEqual(len(wav), 44 + 4800)  # the worker's answer, after its 4 s
        self.assertGreater(took, 3.5)
        code, wav = self.req("/speak", {"text": "hi zzslow", "rate": 1}, raw=True)
        self.assertEqual((code, len(wav)), (200, 44 + 4800))  # now from the disk cache
        log = pathlib.Path(self.home, "speak.log").read_text().splitlines()
        self.assertRegex(log[-2], r" +[4-5]\d{3} ms 200 piper en_US-ryan-medium chars=9$")
        self.assertIn("ms 200 cache en_US-ryan-medium", log[-1])

    def test_stuck_piper_worker_falls_back_to_the_program(self):
        self.fake_piper(60, binary=True)
        t = time.time()
        code, wav = self.req("/speak", {"text": "hi zzslow", "rate": 1}, raw=True)
        took = time.time() - t
        self.assertEqual(code, 200)
        self.assertEqual(len(wav), 44 + 9000, "the piper program answered")
        self.assertLess(took, 9, "within the live budget (page waits 20 s)")
        self.assertIn("200 piper-binary after slow", pathlib.Path(self.home, "speak.log").read_text())
        # YES is in the startup cache: instant even while the worker is stuck
        t = time.time()
        self.assertEqual(self.req("/speak", {"text": "Yes", "rate": 1}, raw=True)[0], 200)
        self.assertLess(time.time() - t, 1)

    def test_nice_is_a_prefix_not_preexec_fn(self):
        src = pathlib.Path(ROOT, "server.py").read_text()
        self.assertNotIn("preexec_fn", src)  # it can deadlock a threaded server on fork
        os.environ["VOICEBOX_HOME"] = self.home
        sys.path.insert(0, ROOT)
        import importlib
        import server
        importlib.reload(server)
        os.makedirs(os.path.join(self.home, "piper", "piper"))
        pathlib.Path(self.home, "piper", "en_US-ryan-medium.onnx").write_text("fake")
        b = os.path.join(self.home, "piper", "piper", "piper")
        pathlib.Path(b).write_text("#!/bin/bash\ncat >/dev/null\nprintf 'RIFF%04d' \"$(nice)\"\nhead -c 3000 /dev/zero\n")
        os.chmod(b, 0o755)
        self.assertEqual(server.piper_wav("x", 1.0, "en_US-ryan-medium", low=True)[:8], b"RIFF%04d" % (os.nice(0) + 10))
        self.assertEqual(server.piper_wav("x", 1.0, "en_US-ryan-medium")[:8], b"RIFF%04d" % os.nice(0))

    def test_battery(self):
        power = os.path.join(self.home, "power")
        os.makedirs(os.path.join(power, "AC"))
        self.extra_env = {"VOICEBOX_POWER_DIR": power}
        self.start()
        self.assertEqual(self.req("/battery")[1], {})  # no battery: the page hides it
        os.makedirs(os.path.join(power, "BAT1"))
        pathlib.Path(power, "BAT1", "capacity").write_text("17\n")
        pathlib.Path(power, "BAT1", "status").write_text("Discharging\n")
        self.assertEqual(self.req("/battery")[1], {"percent": 17, "status": "Discharging", "plugged": False})
        pathlib.Path(power, "BAT1", "status").write_text("Charging\n")
        self.assertTrue(self.req("/battery")[1]["plugged"])



class InstallTest(unittest.TestCase):
    """install.sh re-run with every system tool faked (no sudo, no network): it keeps the working app
    as app.prev and writes update-pending BEFORE touching app/, and puts it back if its copy fails."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="vbinst-")
        self.vb = os.path.join(self.home, ".local", "share", "voicebox")
        self.app = os.path.join(self.vb, "app")
        os.makedirs(self.app)
        for f in ("server.py", "kiosk.sh"):
            shutil.copy2(os.path.join(ROOT, f), self.app)
        shutil.copytree(os.path.join(ROOT, "static"), os.path.join(self.app, "static"))
        pathlib.Path(self.app, "VERSION").write_text("old\n")
        pathlib.Path(self.vb, "data.json").write_text('{"categories": []}')
        kv = os.path.join(self.vb, "kokoro", "venv", "bin")
        os.makedirs(kv)
        self.bin = os.path.join(self.home, "fakebin")
        os.makedirs(self.bin)
        fakes = {"id": 'echo 1000', "curl": "exit 1", "pactl": "exit 1", "xinput": "exit 1", "flatpak": "exit 1",
                 "gsettings": "exit 1", "xdg-user-dir": 'echo "$HOME/Desktop"', "dpkg-query": "exit 1"}
        for name in ("sudo", "apt-get", "apt-cache", "dpkg", "systemctl", "gio", "chromium", "groupadd", "usermod"):
            fakes[name] = "exit 0"
        for name, body in fakes.items():
            self.fake(name, body)
        for name in ("python3", "pip"):  # the speech-engine venv: present, but pip fails (no network)
            pathlib.Path(kv, name).write_text("#!/bin/bash\nexit 1\n")
            os.chmod(os.path.join(kv, name), 0o755)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def fake(self, name, body):
        pathlib.Path(self.bin, name).write_text("#!/bin/bash\n%s\n" % body)
        os.chmod(os.path.join(self.bin, name), 0o755)

    def install(self):
        env = dict(os.environ, HOME=self.home, PATH=self.bin + ":" + os.environ["PATH"])
        env.pop("VOICEBOX_PEOPLE", None)
        return subprocess.run(["bash", os.path.join(ROOT, "install.sh")], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)

    def test_rerun_keeps_the_old_app_first(self):
        old, data = tree(self.app), pathlib.Path(self.vb, "data.json").read_bytes()
        r = self.install()
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(tree(self.app + ".prev"), old, r.stdout)
        self.assertEqual(pathlib.Path(self.vb, "update-pending").read_text().split(), [sha, "0"])
        self.assertEqual(pathlib.Path(self.app, "VERSION").read_text().strip(), sha)
        self.assertTrue(os.path.exists(os.path.join(self.app, "piper_worker.py")))
        self.assertTrue(os.access(os.path.join(self.vb, "bootguard.sh"), os.X_OK))
        self.assertEqual(pathlib.Path(self.vb, "data.json").read_bytes(), data)
        self.assertNotIn("not updated", r.stdout)

    def test_failed_copy_puts_the_old_app_back(self):
        self.fake("cp", 'case "${@: -1}" in *piper_worker.py.new) exit 1;; esac\nexec /usr/bin/cp "$@"')
        old = tree(self.app)
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("The previous version was put back", r.stdout)
        self.assertEqual(tree(self.app), old, "server.py was already replaced; the rollback undid it")
        self.assertFalse(os.path.exists(os.path.join(self.vb, "update-pending")))


class RepoCopyTest(Base):
    def test_no_update_from_repo_checkout(self):
        self.start()  # server.py run from the repo, not ~/.local/share/voicebox/app
        self.assertEqual(self.req("/update/install", {})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
