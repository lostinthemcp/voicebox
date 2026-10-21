#!/bin/bash
# VoiceBox kiosk loop: started at login (autostart) and by the "Start VoiceBox" icon.
# Keeps the browser full screen on the VoiceBox page; relaunches it if it closes,
# unless the family used Settings -> Exit to desktop (that writes the exit flag).
set -u
VB="$HOME/.local/share/voicebox"
FLAG="$VB/exit-kiosk"
URL="http://127.0.0.1:8765"
LOG="$VB/kiosk.log"
mkdir -p "$VB"
# keep the log small
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1000000 ]; then mv -f "$LOG" "$LOG.old"; fi

# only one loop at a time (the icon may be tapped while it is already running)
exec 9>"$VB/kiosk.lock"
if ! flock -n 9; then
  echo "$(date) kiosk already running" >>"$LOG"
  exit 0
fi

rm -f "$FLAG"   # a new login or a tap on the icon means "show VoiceBox again"

find_browser() {
  local b
  for b in chromium chromium-browser google-chrome-stable google-chrome; do
    if command -v "$b" >/dev/null 2>&1; then
      # skip Ubuntu's snap stub when snapd is not installed
      if [ "$(readlink -f "$(command -v "$b")")" = "/usr/bin/snap" ] && ! command -v snap >/dev/null; then continue; fi
      echo "$b"; return 0
    fi
  done
  if command -v flatpak >/dev/null 2>&1 && flatpak info org.chromium.Chromium >/dev/null 2>&1; then
    echo "flatpak"; return 0
  fi
  return 1
}

screen_awake() {
  xset s off s noblank -dpms 2>/dev/null || true
  pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true
  pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true
}

# every 30 s, for the whole session: no screen blanking, every speaker at 100% and unmuted,
# and the built-in speaker as the output (never an HDMI/DisplayPort screen, never taken from Bluetooth)
LOUD_EVERY="${VOICEBOX_LOUD_EVERY:-30}"
loud_once() {
  xset s off -dpms s noblank 2>/dev/null
  local sinks name desc pick="" analog="" other="" def
  sinks="$(LC_ALL=C timeout 5 pactl list sinks 2>/dev/null | awk -F': ' '/^[ \t]*Name: /{n=$2} /^[ \t]*Description: /{print n "\t" $2}')"
  while IFS=$'\t' read -r name desc; do
    [ -n "$name" ] || continue
    case "$name" in *bluez*) continue ;; esac  # Bluetooth headphones/speakers: leave their volume alone
    timeout 5 pactl set-sink-mute "$name" 0 2>/dev/null
    timeout 5 pactl set-sink-volume "$name" 100% 2>/dev/null
    case "$name $desc" in
      *[Hh][Dd][Mm][Ii]*|*DisplayPort*) ;;
      *[Ss]peaker*) [ -n "$pick" ] || pick="$name" ;;
      *[Aa]nalog*) [ -n "$analog" ] || analog="$name" ;;
      *) [ -n "$other" ] || other="$name" ;;
    esac
  done <<<"$sinks"
  pick="${pick:-${analog:-$other}}"
  def="$(timeout 5 pactl get-default-sink 2>/dev/null)"
  case "$def" in *bluez*) return 0 ;; esac  # the family plugged in headphones on purpose
  if [ -n "$pick" ] && [ "$def" != "$pick" ]; then timeout 5 pactl set-default-sink "$pick" 2>/dev/null; fi
  return 0
}
keep_loud() { while kill -0 $$ 2>/dev/null; do loud_once; sleep "$LOUD_EVERY"; done; }  # stops if the kiosk dies hard
keep_loud 9>&- >/dev/null 2>&1 &
LOUD_PID=$!
trap 'kill "$LOUD_PID" 2>/dev/null' EXIT

wait_for_server() {
  local i
  for i in $(seq 1 90); do
    if python3 -c "import urllib.request,sys; urllib.request.urlopen('$URL/data', timeout=2)" 2>/dev/null; then return 0; fi
    [ "$i" = 5 ] && systemctl --user start voicebox.service 2>/dev/null
    sleep 1
  done
  return 1
}

if command -v unclutter >/dev/null 2>&1 && ! pgrep -u "$USER" -x unclutter >/dev/null; then
  unclutter -idle 2 -root >/dev/null 2>&1 9>&- &
fi

while true; do
  screen_awake
  # never open a dead "can't be reached" page he cannot get out of; the desktop stays usable meanwhile
  if ! wait_for_server; then
    echo "$(date) server not answering yet, still waiting" >>"$LOG"
    [ -f "$FLAG" ] && break
    continue
  fi
  BROWSER="$(find_browser)" || { echo "$(date) no browser found" >>"$LOG"; sleep 10; continue; }

  case "$BROWSER" in
    flatpak) PROFILE="$HOME/.var/app/org.chromium.Chromium/voicebox-profile" ;;
    *) if [ "$(readlink -f "$(command -v "$BROWSER")")" = "/usr/bin/snap" ]; then
         PROFILE="$HOME/snap/chromium/common/voicebox-profile"
       else
         PROFILE="$VB/voicebox-profile"
       fi ;;
  esac
  mkdir -p "$PROFILE/Default"
  # after a kill, stop Chromium offering to "restore pages"
  if [ -f "$PROFILE/Default/Preferences" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PROFILE/Default/Preferences"
  fi

  ARGS=(--kiosk "--app=$URL" "--user-data-dir=$PROFILE" --noerrdialogs --disable-infobars
        --disable-session-crashed-bubble --disable-features=TranslateUI --overscroll-history-navigation=0
        --disable-pinch --check-for-update-interval=31536000 --password-store=basic
        --autoplay-policy=no-user-gesture-required --no-first-run --no-default-browser-check
        --disable-component-update)
  echo "$(date) starting $BROWSER" >>"$LOG"
  if [ "$BROWSER" = flatpak ]; then
    flatpak run org.chromium.Chromium "${ARGS[@]}" >>"$LOG" 2>&1 9>&-
  else
    "$BROWSER" "${ARGS[@]}" >>"$LOG" 2>&1 9>&-  # child must not hold the lock
  fi

  if [ -f "$FLAG" ]; then
    echo "$(date) exit to desktop requested" >>"$LOG"
    break
  fi
  sleep 2
done
