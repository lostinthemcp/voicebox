#!/usr/bin/env python3
"""Piper speech worker for VoiceBox. server.py starts ONE of these with the venv's python, so each
voice model loads once (the piper binary reloads it on every call: seconds on the Surface).
Same protocol as kokoro_worker.py: JSON lines {"text", "voice", "speed", "out"} on stdin, a WAV
written to "out" (via .tmp + rename), one answer line {"ok": true} or {"error": "..."}.

Usage: piper_worker.py <piper dir with <voice>.onnx and <voice>.onnx.json>
"""
import json
import os
import sys
import wave

answers = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)  # anything the libraries print goes to the log, not into our answers

from piper import PiperVoice, SynthesisConfig  # noqa: E402

d = sys.argv[1]
loaded = {}  # voice -> PiperVoice, kept: switching back is instant
answers.write(json.dumps({"ready": True}) + "\n")

for line in sys.stdin:
    try:
        r = json.loads(line)
        voice = os.path.basename(r["voice"])
        if voice not in loaded:
            loaded[voice] = PiperVoice.load(os.path.join(d, voice + ".onnx"))
        v = loaded[voice]
        tmp = r["out"] + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(v.config.sample_rate)
            cfg = SynthesisConfig(length_scale=1.0 / float(r.get("speed") or 1.0))
            for chunk in v.synthesize(r["text"].replace("\n", " "), syn_config=cfg):
                w.writeframes(chunk.audio_int16_bytes)
        os.replace(tmp, r["out"])
        answers.write(json.dumps({"ok": True}) + "\n")
    except Exception as e:
        answers.write(json.dumps({"error": str(e)}) + "\n")
