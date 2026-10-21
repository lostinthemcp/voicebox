#!/bin/bash
# shellcheck disable=SC2015  # ok/warn always succeed, so A && B || C is safe here
# VoiceBox installer for Linux Mint 22.x (Cinnamon). Run ONCE with internet, as the
# normal user (not root):   bash install.sh
# Safe to run again: it skips what is already done and refreshes the app files.
set -u
if [ "$(id -u)" = 0 ]; then echo "Run this as the normal user (it asks for sudo when needed), not as root."; exit 1; fi

SRC="$(cd "$(dirname "$0")" && pwd)"
VB="$HOME/.local/share/voicebox"
APP="$VB/app"
PIPER="$VB/piper"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
HF="https://huggingface.co/rhasspy/piper-voices/resolve/main/en"
# the voice list lives in voices.txt (the in-app updater reads the same file)
VOICES="$(grep -v '^#' "$SRC/voices.txt" 2>/dev/null | cut -s -d'|' -f2 | tr -d ' ' | grep '^en_')"
[ -n "$VOICES" ] || VOICES="en_US/ryan/medium/en_US-ryan-medium en_US/joe/medium/en_US-joe-medium"
KOK="$VB/kokoro"
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# file and sha256 (the same pins are in server.py)
KOKORO_FILES="kokoro-v1.0.onnx:7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5 voices-v1.0.bin:bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
RUSTDESK_VER="1.5.0"
RUSTDESK_URL="https://github.com/rustdesk/rustdesk/releases/download/$RUSTDESK_VER/rustdesk-$RUSTDESK_VER-x86_64.deb"
RUSTDESK_SHA256="4e2b9701d25a7ef373f3d09bda584a37d6902f97a6ab15f2e86d4a5783e2a0ec"  # of the 1.5.0 deb, checked 2026-10-21
SUMMARY=()
ok()   { SUMMARY+=("OK    $*"); echo "  ok: $*"; }
warn() { SUMMARY+=("CHECK $*"); echo "  !! $*"; }
step() { echo; echo "== $*"; }

step "Packages (needs your password)"
sudo -v || { echo "sudo is needed for packages and auto-login."; exit 1; }
sudo apt-get update -qq || warn "apt update failed (no internet?)"
sudo apt-get install -y python3 curl >/dev/null && ok "python3 + curl" || { echo "python3/curl failed to install"; exit 1; }
for p in pulseaudio-utils x11-xserver-utils xinput libinput-tools speech-dispatcher espeak-ng fonts-noto-color-emoji unclutter; do
  dpkg -s "$p" >/dev/null 2>&1 || sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "optional package $p did not install"
done

browser=""
for b in chromium chromium-browser google-chrome-stable; do
  if command -v "$b" >/dev/null && [ "$(readlink -f "$(command -v "$b")")" != /usr/bin/snap ]; then browser="$b"; break; fi
done
if [ -z "$browser" ]; then
  # Mint ships a real (non-snap) chromium .deb in its own repo
  if apt-cache policy chromium 2>/dev/null | grep -q 'Candidate: [0-9]' && sudo apt-get install -y chromium >/dev/null; then
    command -v chromium >/dev/null && browser=chromium
  fi
fi
if [ -z "$browser" ] && command -v flatpak >/dev/null; then
  flatpak install -y --user flathub org.chromium.Chromium >/dev/null 2>&1 || sudo flatpak install -y flathub org.chromium.Chromium >/dev/null 2>&1
  flatpak info org.chromium.Chromium >/dev/null 2>&1 && browser="flatpak chromium"
fi
if [ -z "$browser" ]; then
  tmp="$(mktemp -d)"
  if curl -fsSL -o "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
     && sudo apt-get install -y "$tmp/chrome.deb" >/dev/null; then browser=google-chrome-stable; fi
  rm -rf "$tmp"
fi
[ -n "$browser" ] && ok "browser: $browser" || warn "NO BROWSER INSTALLED - install Chromium from the Software Manager, then run this again"

step "Piper voice (offline speech)"
mkdir -p "$PIPER"
if [ -x "$PIPER/piper/piper" ]; then
  ok "piper already installed"
else
  tmp="$(mktemp -d)"
  if curl -fL --retry 3 -o "$tmp/piper.tgz" "$PIPER_URL" && tar -xzf "$tmp/piper.tgz" -C "$tmp"; then
    rm -rf "$PIPER/piper" && mv "$tmp/piper" "$PIPER/piper" && ok "piper downloaded"
  else
    warn "piper download failed - VoiceBox will use the browser voice until you rerun install.sh"
  fi
  rm -rf "$tmp"
fi
for v in $VOICES; do
  name="$(basename "$v")"
  for ext in onnx.json onnx; do  # json first: the server lists a voice as soon as its .onnx appears
    f="$PIPER/$name.$ext"
    if [ -s "$f" ] && { [ "$ext" = onnx.json ] || [ "$(stat -c %s "$f")" -gt 1000000 ]; }; then continue; fi
    curl -fL --retry 3 -o "$f.part" "$HF/$v.$ext?download=true" && mv "$f.part" "$f" || { rm -f "$f.part"; warn "could not download $name.$ext"; }
  done
  [ -s "$PIPER/$name.onnx" ] && [ -s "$PIPER/$name.onnx.json" ] && ok "voice $name"
done
if [ -x "$PIPER/piper/piper" ] && [ -s "$PIPER/en_US-ryan-medium.onnx" ]; then
  if echo "Voice box is ready." | "$PIPER/piper/piper" -q -m "$PIPER/en_US-ryan-medium.onnx" -f "$VB/test.wav" >/dev/null 2>&1 && [ -s "$VB/test.wav" ]; then
    ok "piper speaks (test file $VB/test.wav)"
  else
    warn "piper is installed but failed a test run"
  fi
fi

step "App files"
mkdir -p "$APP"
# the in-app updater compares VERSION with the stable branch; anything but its commit means "update available"
NEWVER="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo "unknown-$(date +%s)")"
# outside app/, so neither this copy nor the in-app updater replaces the thing that undoes a bad version
cp -f "$SRC/bootguard.sh" "$VB/bootguard.sh.new" && mv -f "$VB/bootguard.sh.new" "$VB/bootguard.sh" && chmod +x "$VB/bootguard.sh" \
  || { echo "Could not copy bootguard.sh (disk full?). The app was not updated."; exit 1; }
# the service with bootguard goes in BEFORE app/ changes: if this install stops half way (Ctrl-C,
# closed terminal, power cut), the next start can still roll back to app.prev
mkdir -p "$HOME/.config/systemd/user"
cat >"$HOME/.config/systemd/user/voicebox.service" <<EOF
[Unit]
Description=VoiceBox speech server
# never give up restarting: this is his voice (bootguard.sh rolls back a broken update)
StartLimitIntervalSec=0

[Service]
ExecStartPre=-/bin/bash $VB/bootguard.sh
ExecStart=/usr/bin/python3 $APP/server.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload 2>/dev/null
if [ -s "$APP/server.py" ]; then
  # Keep the working app as app.prev and write update-pending BEFORE touching app/: if this copy stops
  # half way, or the new version never gets healthy, bootguard.sh puts app.prev back.
  # While an earlier version is still unproven (update-pending), app.prev is the last good one: keep it.
  if [ ! -f "$VB/update-pending" ] || [ ! -d "$VB/app.prev" ]; then
    rm -rf "$VB/app.bad" "$VB/app.prev.new"
    if cp -a "$APP" "$VB/app.prev.new" && diff -rq "$APP" "$VB/app.prev.new" >/dev/null && [ -s "$VB/app.prev.new/server.py" ]; then
      rm -rf "$VB/app.prev" && mv "$VB/app.prev.new" "$VB/app.prev" \
        || { echo "Could not keep a copy of the current app. The app was not updated."; exit 1; }
    else
      rm -rf "$VB/app.prev.new"; echo "Could not keep a copy of the current app (disk full?). The app was not updated."; exit 1
    fi
  fi
  { echo "$NEWVER 0" >"$VB/update-pending.tmp" && mv -f "$VB/update-pending.tmp" "$VB/update-pending"; } \
    || { echo "Could not write $VB/update-pending (disk full?). The app was not updated."; exit 1; }
  ok "the current app is kept in $VB/app.prev (put back by itself if the new one does not work)"
fi
copy_failed() {
  echo "Could not copy $1 (disk full?)."
  if [ -f "$VB/update-pending" ] && bash "$VB/bootguard.sh" --now && [ ! -f "$VB/update-pending" ]; then
    echo "The previous version was put back, so the app is as it was. Fix that and run install.sh again."
  else
    echo "The app may be half updated. Run install.sh again once there is space; see $VB/bootguard.log."
  fi
  exit 1
}
# copy to .new, then rename: a running kiosk.sh or server.py is never half-overwritten
for f in server.py kiosk.sh kokoro_worker.py piper_worker.py voices.txt; do
  cp -f "$SRC/$f" "$APP/$f.new" && mv -f "$APP/$f.new" "$APP/$f" || copy_failed "$f"
done
chmod +x "$APP/kiosk.sh"
rm -rf "$APP/static.new" && cp -r "$SRC/static" "$APP/static.new" && rm -rf "$APP/static.old" \
  && { [ ! -d "$APP/static" ] || mv "$APP/static" "$APP/static.old"; } && mv "$APP/static.new" "$APP/static" && rm -rf "$APP/static.old" \
  || copy_failed "the app pages"
[ -s "$APP/static/index.html" ] || copy_failed "the app page"
{ echo "$NEWVER" >"$APP/VERSION.new" && mv -f "$APP/VERSION.new" "$APP/VERSION"; } || copy_failed VERSION
ok "app copied to $APP (your phrases and data in $VB/data.json are not touched)"

step "Speech engines: fast Piper worker + Kokoro voices (optional; the piper program still works without them)"
mkdir -p "$KOK"
if { dpkg -s python3-venv >/dev/null 2>&1 || sudo apt-get install -y python3-venv >/dev/null 2>&1; } \
   && { [ -x "$KOK/venv/bin/python3" ] || python3 -m venv "$KOK/venv"; } \
   && "$KOK/venv/bin/pip" install -q --disable-pip-version-check kokoro-onnx==0.6.1 piper-tts==1.8.0 onnxruntime==1.31.0 numpy==2.4.6; then  # tested on LOKI
  ok "speech engines (kokoro-onnx 0.6.1, piper-tts 1.8.0)"
else
  warn "speech engines did not install - the piper program still speaks (slower); run install.sh again later"
fi
if [ -x "$KOK/venv/bin/python3" ] && [ -s "$PIPER/en_US-ryan-medium.onnx" ]; then
  rm -f "$VB/piper-test.wav"
  if printf '{"text": "Voice box is ready.", "voice": "en_US-ryan-medium", "speed": 1, "out": "%s"}\n' "$VB/piper-test.wav" \
       | timeout 120 "$KOK/venv/bin/python3" "$APP/piper_worker.py" "$PIPER" 2>/dev/null | grep -q '"ok"' && [ -s "$VB/piper-test.wav" ]; then
    ok "fast Piper worker speaks (test file $VB/piper-test.wav)"
  else
    warn "fast Piper worker failed a test run - the slower piper program is used"
  fi
fi
for fs in $KOKORO_FILES; do
  f="${fs%%:*}"; sum="${fs#*:}"
  [ -f "$KOK/$f" ] && [ "$(sha256sum "$KOK/$f" | cut -d' ' -f1)" = "$sum" ] && continue
  if curl -fL --retry 3 -o "$KOK/$f.part" "$KOKORO_URL/$f" && [ "$(sha256sum "$KOK/$f.part" | cut -d' ' -f1)" = "$sum" ]; then
    mv -f "$KOK/$f.part" "$KOK/$f"
  else
    rm -f "$KOK/$f.part"; warn "could not download $f (Kokoro voices stay off)"
  fi
done
if [ -x "$KOK/venv/bin/python3" ] && [ -f "$KOK/kokoro-v1.0.onnx" ] && [ -f "$KOK/voices-v1.0.bin" ]; then
  rm -f "$VB/kokoro-test.wav"
  if printf '{"text": "Voice box is ready.", "voice": "am_michael", "speed": 1, "out": "%s"}\n' "$VB/kokoro-test.wav" \
       | timeout 300 "$KOK/venv/bin/python3" "$APP/kokoro_worker.py" "$KOK" 2>/dev/null | grep -q '"ok"' && [ -s "$VB/kokoro-test.wav" ]; then
    ok "kokoro speaks (test file $VB/kokoro-test.wav)"
  else
    warn "kokoro is installed but failed a test run - the Piper voices still work"
  fi
fi

step "Voice packs (every default phrase, ready-made, in every voice)"
if [ -f "$SRC/voicepacks.json" ]; then
  # same code as the in-app updater: sha256 checked, safe unpack, skipped under 1 GB free
  if packs="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import server; print("; ".join(server.fetch_packs(sys.argv[2])))' "$APP" "$SRC")"; then
    case "$packs" in *"could not"*|*skipped*) warn "voice packs: $packs (VoiceBox makes the missing clips itself)" ;; *) ok "voice packs ${packs:-already in}" ;; esac
  else
    warn "voice packs did not download - VoiceBox makes the clips itself (slower at first)"
  fi
fi

# Names stay out of the public repo: pass them at install, e.g. VOICEBOX_PEOPLE="Ann,Bob" bash install.sh
if [ -z "${VOICEBOX_PEOPLE:-}" ] && [ -t 0 ]; then
  read -rp "Family names for the People tile, separated by commas (or just press Enter to skip): " VOICEBOX_PEOPLE
fi
if [ -n "${VOICEBOX_PEOPLE:-}" ]; then
  systemctl --user stop voicebox.service 2>/dev/null  # only one writer of data.json at a time
  VOICEBOX_PEOPLE="$VOICEBOX_PEOPLE" python3 -c '
import os, sys
sys.path.insert(0, sys.argv[1]); import server
d = server.load()
people = next(c for c in d["categories"] if c["id"] == "people")["phrases"]
for n in (x.strip() for x in os.environ["VOICEBOX_PEOPLE"].split(",")):
    if n and n not in people: people.append(n)
server.save(d)' "$APP" && ok "family names added to the People tile" || warn "could not add family names (add them in Settings)"
fi

step "Background service"
systemctl --user daemon-reload && systemctl --user enable voicebox.service >/dev/null 2>&1 && systemctl --user restart voicebox.service \
  && ok "voicebox.service running" || warn "could not start voicebox.service (log out and in, then run install.sh again)"
sleep 2
python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/data', timeout=5); assert b'<' in urllib.request.urlopen('http://127.0.0.1:8765/', timeout=5).read()" 2>/dev/null \
  && ok "server answers on http://127.0.0.1:8765" || warn "server not answering yet: journalctl --user -u voicebox"

step "Kiosk start at login + Start VoiceBox icon"
mkdir -p "$HOME/.config/autostart" "$HOME/.local/share/applications"
cat >"$HOME/.config/autostart/voicebox-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VoiceBox kiosk
Exec=$APP/kiosk.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
NoDisplay=true
EOF
icon_file="$HOME/.local/share/applications/voicebox.desktop"
cat >"$icon_file" <<EOF
[Desktop Entry]
Type=Application
Name=Start VoiceBox
Comment=Open the VoiceBox talker full screen
Exec=$APP/kiosk.sh
Icon=audio-speakers
Terminal=false
Categories=Accessibility;
EOF
chmod +x "$icon_file"
desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$desk"
cp -f "$icon_file" "$desk/start-voicebox.desktop"
chmod +x "$desk/start-voicebox.desktop"
gio set "$desk/start-voicebox.desktop" metadata::trusted true 2>/dev/null || true
ok "autostart + 'Start VoiceBox' on the desktop and in the menu"

step "Auto-login"
sudo mkdir -p /etc/lightdm/lightdm.conf.d
printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$USER" | sudo tee /etc/lightdm/lightdm.conf.d/70-voicebox.conf >/dev/null
sudo groupadd -f autologin && sudo usermod -aG autologin "$USER"
ok "LightDM auto-login for $USER"
if [ -f /etc/lightdm/lightdm.conf ] && grep -Eq '^\s*autologin-user=\s*$' /etc/lightdm/lightdm.conf; then
  warn "/etc/lightdm/lightdm.conf has an empty autologin-user= line that overrides ours: set it in Menu > Login Window > Users"
fi

step "Screen stays on, no lock, no sleep"
gs() { if gsettings writable "$1" "$2" >/dev/null 2>&1; then gsettings set "$1" "$2" "$3"; fi; }
gs org.cinnamon.desktop.screensaver lock-enabled false
gs org.cinnamon.desktop.screensaver idle-activation-enabled false
gs org.cinnamon.desktop.session idle-delay 0
gs org.cinnamon.settings-daemon.plugins.power sleep-display-ac 0
gs org.cinnamon.settings-daemon.plugins.power sleep-display-battery 0
gs org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
gs org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
gs org.cinnamon.settings-daemon.plugins.power lock-on-suspend false
gs org.cinnamon.settings-daemon.plugins.power idle-dim-ac false
gs org.cinnamon.settings-daemon.plugins.power idle-dim-battery false
gs org.cinnamon.settings-daemon.plugins.power idle-brightness 100
# closing the Type Cover or bumping the power button must never put him to sleep
gs org.cinnamon.settings-daemon.plugins.power lid-close-ac-action "'nothing'"
gs org.cinnamon.settings-daemon.plugins.power lid-close-battery-action "'nothing'"
gs org.cinnamon.settings-daemon.plugins.power lid-close-suspend-with-external-monitor false
gs org.cinnamon.settings-daemon.plugins.power button-power "'nothing'"
gs org.cinnamon.settings-daemon.plugins.power button-suspend "'nothing'"
sudo mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\nHandleSuspendKey=ignore\nHandlePowerKey=ignore\n' \
  | sudo tee /etc/systemd/logind.conf.d/70-voicebox.conf >/dev/null
# strongest guard: the system itself can no longer sleep, whatever any setting says (uninstall.sh unmasks)
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 \
  && ok "sleep, suspend and hibernate switched off for the whole system" || warn "could not mask the sleep targets"
gs org.cinnamon hotcorner-layout "['expo:false:0', 'scale:false:0', 'scale:false:0', 'desktop:false:0']"
[ "$(gsettings get org.cinnamon.desktop.screensaver lock-enabled 2>/dev/null)" = false ] \
  && ok "lock, sleep and lid-close suspend turned off (lid setting fully applies after a restart)" || warn "could not change Cinnamon power settings (run install.sh from the desktop, not over SSH)"
mkdir -p "$HOME/.linuxmint/mintwelcome" && touch "$HOME/.linuxmint/mintwelcome/norun.flag"

step "Sound"
if pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null && pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null; then
  ok "volume 100% (also set at every login)"
else
  warn "could not set the volume - check the speaker icon"
fi

step "RustDesk (remote help)"
# Pinned version. No password or settings are set here: Joe sets the permanent password in the RustDesk window.
if [ "$(dpkg-query -W -f='${Version}' rustdesk 2>/dev/null)" = "$RUSTDESK_VER" ]; then
  ok "rustdesk $RUSTDESK_VER already installed"
else
  tmp="$(mktemp -d)"
  if curl -fL --retry 3 -o "$tmp/rustdesk-$RUSTDESK_VER-x86_64.deb" "$RUSTDESK_URL" \
     && [ "$(sha256sum "$tmp/rustdesk-$RUSTDESK_VER-x86_64.deb" | cut -d' ' -f1)" = "$RUSTDESK_SHA256" ] \
     && (cd "$tmp" && sudo apt-get install -y "./rustdesk-$RUSTDESK_VER-x86_64.deb" >/dev/null); then
    ok "rustdesk $RUSTDESK_VER installed"
  else
    warn "rustdesk did not install (no internet, or the download did not match its checksum) - run install.sh again later"
  fi
  rm -rf "$tmp"
fi
if dpkg -s rustdesk >/dev/null 2>&1; then
  sudo systemctl enable --now rustdesk >/dev/null 2>&1 && ok "rustdesk service on (starts at boot)" || warn "could not start the rustdesk service"
fi
# The root service (rustdesk --service) does the remote access. RustDesk also writes a tray-icon
# autostart (~/.config/autostart/rustdesk.desktop, "rustdesk --tray") into the session; it is only a
# panel icon, but nothing extra should start over the kiosk, so it is switched off (XDG Hidden=true).
# RustDesk only creates that file when it is missing, so it stays off. Remote access is not affected.
mkdir -p "$HOME/.config/autostart"
printf '[Desktop Entry]\nType=Application\nName=RustDesk tray (off for the VoiceBox kiosk)\nExec=rustdesk --tray\nHidden=true\n' \
  >"$HOME/.config/autostart/rustdesk.desktop"
rustdesk_id="$(timeout 10 rustdesk --get-id 2>/dev/null || timeout 10 sudo -n rustdesk --get-id 2>/dev/null || true)"

step "Touchscreen"
if xinput list 2>/dev/null | grep -iqE 'touch(screen)?|elan|ipts|ithc'; then
  ok "touchscreen found: $(xinput list --name-only 2>/dev/null | grep -iE 'touch|elan|ipts|ithc' | head -1)"
elif sudo libinput list-devices 2>/dev/null | grep -A3 -i 'touch' | grep -q 'Capabilities:.*touch'; then
  ok "touchscreen found (libinput)"
else
  warn "NO touchscreen found. On a Surface the linux-surface kernel usually fixes this:
        https://github.com/linux-surface/linux-surface/wiki/Installation-and-Setup
        (not installed automatically - ask Joe first)"
fi

echo
echo "================ VoiceBox install summary ================"
printf '%s\n' "${SUMMARY[@]}"
echo "Data and backups: $VB (data.json, backups/)"
if dpkg -s rustdesk >/dev/null 2>&1; then
  [ -n "$rustdesk_id" ] && echo "RustDesk ID: $rustdesk_id"
  echo "RustDesk: open it from the menu, Settings > Security > Set permanent password, and write the ID down."
fi
echo "Restart the Surface (or log out and in) and VoiceBox opens by itself."
echo "=========================================================="
