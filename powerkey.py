#!/usr/bin/env python3
"""VoiceBox power key: a tap on the power button turns the SCREEN off or on. Nothing else.

The system ignores the power key (logind HandlePowerKey=ignore, Cinnamon button-power 'nothing'), so the
Surface never shuts down or sleeps from a bump. kiosk.sh runs this in the session. It reads the key from
/dev/input (the user must be in the "input" group; install.sh adds him, it works after the next login).
  press, screen on  -> `xset dpms force off`, and write the dark flag (kiosk.sh then leaves DPMS alone)
  press, screen off -> `xset dpms force on`, remove the flag
A touch also wakes the screen (X does that by itself); kiosk.sh clears the flag when it sees that.
If no power button can be opened it logs that and exits: VoiceBox is never affected.

Usage: powerkey.py <dark flag file>   (VOICEBOX_POWER_DEVICES=/dev/input/eventN:... to pick devices)
"""
import glob
import os
import select
import struct
import subprocess
import sys
import time

EV_KEY, KEY_POWER = 1, 116
EVENT = struct.Struct("llHHi")  # struct input_event: timeval, type, code, value (64-bit Linux)


def has_power_key(caps):
    """caps: /sys/class/input/eventN/device/capabilities/key, hex words, most significant first."""
    words = [int(w, 16) for w in caps.split()][::-1]
    bits = struct.calcsize("l") * 8  # the kernel prints unsigned longs
    i, b = divmod(KEY_POWER, bits)
    return i < len(words) and bool(words[i] >> b & 1)


def power_devices():
    if os.environ.get("VOICEBOX_POWER_DEVICES"):
        return os.environ["VOICEBOX_POWER_DEVICES"].split(":")
    out = []
    for cap in sorted(glob.glob("/sys/class/input/event*/device/capabilities/key")):
        try:
            with open(cap) as f:
                if has_power_key(f.read()):
                    out.append("/dev/input/" + cap.split("/")[4])
        except OSError:
            pass
    return out


def screen_is_off():
    try:
        q = subprocess.run(["xset", "q"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Monitor is Off" in q or "Monitor is in Standby" in q or "Monitor is in Suspend" in q


def toggle(flag):
    # Decide by our own dark flag, not by "xset q": X also sees the power button, and its press can wake the
    # screen before we look. Runs on the key's RELEASE, so that release can't wake a screen we just turned off.
    if os.path.exists(flag):
        try:
            os.remove(flag)
        except OSError:
            pass
        if screen_is_off():
            subprocess.run(["xset", "dpms", "force", "on"], timeout=5)
        log("screen on")
    else:
        open(flag, "w").close()  # first: kiosk.sh must not wake it up again
        time.sleep(0.5)  # let the finger and X settle before blanking
        subprocess.run(["xset", "dpms", "force", "off"], timeout=5)
        log("screen off")


def log(msg):
    print("%s powerkey: %s" % (time.strftime("%c"), msg), flush=True)


def main():
    flag = sys.argv[1]
    fds = {}
    for dev in power_devices():
        try:
            fds[os.open(dev, os.O_RDONLY | os.O_NONBLOCK)] = b""
        except OSError as e:
            log("cannot open %s (%s); is the user in the input group? (works after the next login)" % (dev, e.strerror))
    if not fds:
        log("no power button to listen to; the power key does nothing (the screen stays on)")
        return
    last = -1e9
    while fds:
        ready, _, _ = select.select(list(fds), [], [])
        for fd in ready:
            try:
                data = os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                os.close(fd)
                del fds[fd]
                continue
            buf = fds[fd] + data
            n = len(buf) // EVENT.size * EVENT.size
            fds[fd] = buf[n:]
            for off in range(0, n, EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(buf, off)
                if typ == EV_KEY and code == KEY_POWER and value == 0 and time.monotonic() - last > 0.7:
                    last = time.monotonic()  # one release = one tap, even if the button bounces
                    try:
                        toggle(flag)
                    except (OSError, subprocess.TimeoutExpired) as e:
                        log("could not switch the screen: %s" % e)
    log("power button device closed")


if __name__ == "__main__":
    main()
