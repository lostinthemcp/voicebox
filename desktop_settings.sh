# VoiceBox: desktop settings that keep the screen on and never lock, for every desktop Mint ships.
# Sourced by install.sh (vb_desktop_apply) and uninstall.sh (vb_desktop_reset). Bash, no "set -e".
# Every setting is only written when its schema/key (or xfconf channel) exists on this machine, so a
# missing desktop never fails; the caller gets the list of what was really set (VB_SET) and an empty
# list means CHECK. Found on the real Surface: no Cinnamon power schema at all, GNOME + light-locker
# keys instead, and closing the Type Cover went to the LightDM greeter (light-locker's lock).

# schema key value   (values in gsettings syntax)
VB_GSETTINGS="
org.gnome.settings-daemon.plugins.power lid-close-ac-action 'nothing'
org.gnome.settings-daemon.plugins.power lid-close-battery-action 'nothing'
org.gnome.settings-daemon.plugins.power power-button-action 'nothing'
org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
org.gnome.settings-daemon.plugins.power idle-dim false
org.gnome.settings-daemon.plugins.power idle-brightness 100
org.gnome.desktop.screensaver lock-enabled false
org.gnome.desktop.screensaver idle-activation-enabled false
org.gnome.desktop.screensaver ubuntu-lock-on-suspend false
org.gnome.desktop.session idle-delay 0
org.gnome.desktop.lockdown disable-lock-screen true
apps.light-locker lock-on-suspend false
apps.light-locker lock-after-screensaver 0
apps.light-locker late-locking false
apps.light-locker lock-on-lid false
org.cinnamon.desktop.screensaver lock-enabled false
org.cinnamon.desktop.screensaver idle-activation-enabled false
org.cinnamon.desktop.session idle-delay 0
org.cinnamon.settings-daemon.plugins.power sleep-display-ac 0
org.cinnamon.settings-daemon.plugins.power sleep-display-battery 0
org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
org.cinnamon.settings-daemon.plugins.power lock-on-suspend false
org.cinnamon.settings-daemon.plugins.power idle-dim-ac false
org.cinnamon.settings-daemon.plugins.power idle-dim-battery false
org.cinnamon.settings-daemon.plugins.power idle-brightness 100
org.cinnamon.settings-daemon.plugins.power lid-close-ac-action 'nothing'
org.cinnamon.settings-daemon.plugins.power lid-close-battery-action 'nothing'
org.cinnamon.settings-daemon.plugins.power lid-close-suspend-with-external-monitor false
org.cinnamon.settings-daemon.plugins.power button-power 'nothing'
org.cinnamon.settings-daemon.plugins.power button-suspend 'nothing'
org.cinnamon.settings-daemon.peripherals.touchscreen orientation-lock true
org.cinnamon hotcorner-layout ['expo:false:0', 'scale:false:0', 'scale:false:0', 'desktop:false:0']
org.mate.power-manager button-lid-ac 'nothing'
org.mate.power-manager button-lid-battery 'nothing'
org.mate.power-manager button-power 'nothing'
org.mate.power-manager button-suspend 'nothing'
org.mate.power-manager sleep-computer-ac 0
org.mate.power-manager sleep-computer-battery 0
org.mate.power-manager sleep-display-ac 0
org.mate.power-manager sleep-display-battery 0
org.mate.power-manager backlight-idle-enable false
org.mate.screensaver lock-enabled false
org.mate.screensaver idle-activation-enabled false
org.mate.session idle-delay 0
"
# Xfce (the real Surface: Mint 22.3 Xfce): xfconf channel xfce4-power-manager. The numbers differ by
# version, checked in the xfce4-power-manager source (github.com/xfce-mirror/xfce4-power-manager):
#  4.18.x (Mint 22 / Ubuntu 24.04), common/xfpm-enum-glib.h: XfpmLidTriggerAction LID_TRIGGER_NOTHING=0,
#    SUSPEND=1, HIBERNATE=2, LOCK_SCREEN=3; src/xfpm-manager.c: inactivity-on-ac/battery == 14 means
#    "never" (uint, src/xfpm-xfconf.c default 14).
#  4.19+/4.20: LID_TRIGGER_DPMS=0, SUSPEND=1, HIBERNATE=2, LOCK_SCREEN=3, NOTHING=4; inactivity 0 = never.
#  Both: XfpmShutdownRequest XFPM_DO_NOTHING=0 (power/sleep/hibernate-button-action, uint);
#    blank-on-ac/battery int, 0 = never (src/xfpm-power.c); dpms-enabled, lock-screen-suspend-hibernate
#    and logind-handle-lid-switch bool (true: xfpm lets logind handle the lid, and logind ignores it).
vb_xfconf_list() {
  local v lid=0 never=14
  v="$(xfce4-power-manager --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
  if [ -n "$v" ] && [ "$(printf '%s\n4.19\n' "$v" | sort -V | head -1)" = 4.19 ]; then lid=4; never=0; fi
  cat <<EOX
xfce4-power-manager /xfce4-power-manager/logind-handle-lid-switch bool true
xfce4-power-manager /xfce4-power-manager/lid-action-on-ac uint $lid
xfce4-power-manager /xfce4-power-manager/lid-action-on-battery uint $lid
xfce4-power-manager /xfce4-power-manager/lock-screen-suspend-hibernate bool false
xfce4-power-manager /xfce4-power-manager/dpms-enabled bool false
xfce4-power-manager /xfce4-power-manager/blank-on-ac int 0
xfce4-power-manager /xfce4-power-manager/blank-on-battery int 0
xfce4-power-manager /xfce4-power-manager/inactivity-on-ac uint $never
xfce4-power-manager /xfce4-power-manager/inactivity-on-battery uint $never
xfce4-power-manager /xfce4-power-manager/power-button-action uint 0
xfce4-power-manager /xfce4-power-manager/sleep-button-action uint 0
xfce4-power-manager /xfce4-power-manager/hibernate-button-action uint 0
xfce4-screensaver /saver/enabled bool false
xfce4-screensaver /lock/enabled bool false
EOX
}
VB_SET=()

vb_desktop_apply() {
  local schema key value ch prop typ
  VB_SET=()
  while read -r schema key value; do
    [ -n "$schema" ] || continue
    if gsettings writable "$schema" "$key" >/dev/null 2>&1 && gsettings set "$schema" "$key" "$value" 2>/dev/null; then
      VB_SET+=("$schema $key=$value")
    fi
  done <<<"$VB_GSETTINGS"
  if command -v xfconf-query >/dev/null 2>&1 && xfconf-query -l >/dev/null 2>&1; then
    while read -r ch prop typ value; do
      [ -n "$ch" ] || continue
      [ "$ch" = xfce4-screensaver ] && ! command -v xfce4-screensaver >/dev/null 2>&1 && continue
      if xfconf-query -c "$ch" -p "$prop" -n -t "$typ" -s "$value" 2>/dev/null || xfconf-query -c "$ch" -p "$prop" -s "$value" 2>/dev/null; then
        VB_SET+=("xfconf $ch $prop=$value")
      fi
    done <<<"$(vb_xfconf_list)"
  fi
  # light-locker is the LightDM greeter lock (closing the cover showed the greeter): never start it
  mkdir -p "$HOME/.config/autostart"
  printf '[Desktop Entry]\nType=Application\nName=Screen Locker (off for the VoiceBox kiosk)\nExec=light-locker\nHidden=true\n' \
    >"$HOME/.config/autostart/light-locker.desktop" && VB_SET+=("autostart light-locker: off")
  pkill -u "$USER" -x light-locker 2>/dev/null && VB_SET+=("light-locker stopped now")
  return 0
}

vb_desktop_reset() {
  local schema key value ch prop typ
  while read -r schema key value; do
    [ -n "$schema" ] || continue
    gsettings writable "$schema" "$key" >/dev/null 2>&1 && gsettings reset "$schema" "$key" 2>/dev/null
  done <<<"$VB_GSETTINGS"
  if command -v xfconf-query >/dev/null 2>&1 && xfconf-query -l >/dev/null 2>&1; then
    while read -r ch prop typ value; do
      [ -n "$ch" ] || continue
      xfconf-query -c "$ch" -p "$prop" -r 2>/dev/null
    done <<<"$(vb_xfconf_list)"
  fi
  rm -f "$HOME/.config/autostart/light-locker.desktop"
  return 0
}
