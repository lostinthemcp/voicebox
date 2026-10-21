#!/usr/bin/env python3
"""Build the pre-rendered voice packs (run on LOKI, not on the Surface).

Every default phrase and big button, at speed 1.0, in EVERY voice of voices.txt, made with the same
engines, cache names and WAV format as server.py, so the Surface can use them as they are.

Usage:
  VOICEBOX_HOME=<build home> python3 tools/build_voicepacks.py <out dir>
<build home> needs piper/ (voices + the piper program) and kokoro/ (venv with kokoro-onnx + piper-tts,
plus the two Kokoro model files), like an installed Surface. It writes <out dir>/voicepack-<voice>.tar.gz
and <out dir>/voicepacks.json (copy that one into the repo once the release exists).
Packs hold only the default phrases: never family names.
"""
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server  # noqa: E402  (uses VOICEBOX_HOME from the environment)

EXTRA = ("I love you",)  # the first home tile speaks this


def texts():
    d = server.DEFAULT_DATA
    return list(dict.fromkeys([*server.CORE, *EXTRA, *(p for c in d["categories"] for p in c["phrases"])]))


def render(voice, text):
    path = server.cache_path(voice, text, 1.0)
    if server.read_cache(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = server.kokoro if voice.startswith("kokoro-") else server.piper
    r = w.render(text, voice[7:] if voice.startswith("kokoro-") else voice, 1.0, path, 600.0, queue=600.0)
    if r != "ok" or not server.read_cache(path):
        raise SystemExit("could not render %r in %s (%s)" % (text, voice, r))
    return path


def pack(voice, paths, out):
    """Deterministic tar (sorted, no times or owners), so the same clips give the same sha256."""
    file = "voicepack-%s.tar.gz" % voice
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for p in sorted(paths):
            data = open(p, "rb").read()
            ti = tarfile.TarInfo(os.path.basename(p))
            ti.size, ti.mode, ti.mtime = len(data), 0o644, 0
            t.addfile(ti, io.BytesIO(data))
    gz = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    with open(os.path.join(out, file), "wb") as f:
        f.write(gz)
    return {"file": file, "sha256": hashlib.sha256(gz).hexdigest(), "size": len(gz), "clips": len(paths)}


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    tx = texts()
    manifest = {"rate": 1.0, "packs": {}}
    for name, voice, _ in server.voice_table():
        t0 = time.time()
        paths = [render(voice, t) for t in tx]
        manifest["packs"][voice] = dict(pack(voice, paths, out), name=name)
        print("%-24s %3d clips  %6.1f MB  %5.0f s" % (voice, len(paths), manifest["packs"][voice]["size"] / 1e6, time.time() - t0), flush=True)
    server.kokoro.stop()
    # the release tag names the packs' content, so new clips always get a new tag
    digest = hashlib.sha1(json.dumps(manifest["packs"], sort_keys=True).encode()).hexdigest()
    manifest["tag"] = "voicepacks-" + digest[:7]
    with open(os.path.join(out, "voicepacks.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote", os.path.join(out, "voicepacks.json"))


if __name__ == "__main__":
    main()
