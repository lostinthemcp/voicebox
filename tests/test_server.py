#!/usr/bin/env python3
"""Self-test for server.py: save/load, backups, corrupt-file recovery, restore, speak, safety checks.

Run: python3 tests/test_server.py   (uses a temp folder, never touches real data)
"""
import datetime
import glob
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
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


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="vbtest-")
        self.port = free_port()
        self.proc = None

    def tearDown(self):
        self.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def start(self):
        env = dict(os.environ, VOICEBOX_HOME=self.home, VOICEBOX_PORT=str(self.port))
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env,
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

    def test_defaults_save_load(self):
        self.start()
        code, d = self.req("/data")
        self.assertEqual(code, 200)
        names = [c["name"] for c in d["categories"]]
        self.assertIn("Needs", names)
        self.assertIn("About me", names)
        d["categories"][4]["phrases"].append("Test Name")
        d["words"]["hello"] = 3
        self.assertEqual(self.req("/data", d)[0], 200)
        self.stop()
        self.start()  # survives restart
        code, d2 = self.req("/data")
        self.assertIn("Test Name", d2["categories"][4]["phrases"])
        self.assertEqual(d2["words"]["hello"], 3)

    def test_rejects_bad_data(self):
        self.start()
        self.assertEqual(self.req("/data", {"nope": 1})[0], 400)
        self.assertEqual(self.req("/data")[1]["categories"][0]["name"], "Needs")

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
        self.assertEqual(self.req("/data", headers={"Host": "evil.example"})[0], 403)

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
        self.assertIn("en_US-ryan-medium", self.req("/voices")[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
