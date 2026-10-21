#!/usr/bin/env python3
"""Kokoro speech worker for VoiceBox. server.py starts ONE of these with the kokoro venv's python,
so the model loads once. Reads JSON lines {"text", "voice", "speed", "out"} on stdin, writes a WAV
to "out" (via .tmp + rename) and answers one JSON line: {"ok": true} or {"error": "..."}.

Usage: kokoro_worker.py <kokoro dir with kokoro-v1.0.onnx and voices-v1.0.bin>
"""
import json
import os
import sys
import wave

# server.py starts this under `nice -n 10`, so it never competes with the page or Piper for the small CPU
answers = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)  # anything the libraries print goes to the log, not into our answers

import numpy as np  # noqa: E402
import onnxruntime as rt  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402

d = sys.argv[1]
so = rt.SessionOptions()
so.intra_op_num_threads = 2  # the Surface has 2 cores; leave room for the page
so.inter_op_num_threads = 1
k = Kokoro.from_session(rt.InferenceSession(os.path.join(d, "kokoro-v1.0.onnx"), sess_options=so,
                                            providers=["CPUExecutionProvider"]), os.path.join(d, "voices-v1.0.bin"))
answers.write(json.dumps({"ready": True}) + "\n")

for line in sys.stdin:
    try:
        r = json.loads(line)
        voice = r["voice"]
        a, sr = k.create(r["text"], voice=voice, speed=float(r.get("speed") or 1.0),
                         lang="en-gb" if voice.startswith("b") else "en-us")
        tmp = r["out"] + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
        os.replace(tmp, r["out"])
        answers.write(json.dumps({"ok": True}) + "\n")
    except Exception as e:
        answers.write(json.dumps({"error": str(e)}) + "\n")
