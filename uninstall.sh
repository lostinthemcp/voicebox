#!/bin/bash
# Removes VoiceBox start-up pieces (service, kiosk at login, auto-login, icons).
# KEEPS all phrases, names, history and backups in ~/.local/share/voicebox.
set -u
if [ "$(id -u)" = 0 ]; then echo "Run as the normal user, not root."; exit 1; fi
VB="$HOME/.local/share/voicebox"

touch "$VB/exit-kiosk" 2>/dev/null
pkill -f voicebox-profile 2>/dev/null
systemctl --user disable --now voicebox.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/voicebox.service"
systemctl --user daemon-reload 2>/dev/null
rm -f "$HOME/.config/autostart/voicebox-kiosk.desktop" "$HOME/.local/share/applications/voicebox.desktop"
desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
rm -f "$desk/start-voicebox.desktop"
sudo rm -f /etc/lightdm/lightdm.conf.d/70-voicebox.conf /etc/systemd/logind.conf.d/70-voicebox.conf
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null

for k in "org.cinnamon.desktop.screensaver lock-enabled" "org.cinnamon.desktop.screensaver idle-activation-enabled" \
         "org.cinnamon.desktop.session idle-delay" "org.cinnamon.settings-daemon.plugins.power sleep-display-ac" \
         "org.cinnamon.settings-daemon.plugins.power sleep-display-battery" \
         "org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout" \
         "org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-timeout" \
         "org.cinnamon.settings-daemon.plugins.power lock-on-suspend" \
         "org.cinnamon.settings-daemon.plugins.power idle-dim-ac" \
         "org.cinnamon.settings-daemon.plugins.power idle-dim-battery" \
         "org.cinnamon.settings-daemon.plugins.power idle-brightness" \
         "org.cinnamon.settings-daemon.plugins.power lid-close-ac-action" \
         "org.cinnamon.settings-daemon.plugins.power lid-close-battery-action" \
         "org.cinnamon.settings-daemon.plugins.power lid-close-suspend-with-external-monitor" \
         "org.cinnamon.settings-daemon.plugins.power button-power" \
         "org.cinnamon.settings-daemon.plugins.power button-suspend" \
         "org.cinnamon hotcorner-layout"; do
  # shellcheck disable=SC2086  # schema and key are two words on purpose
  gsettings reset $k 2>/dev/null
done

echo "VoiceBox start-up removed. Auto-login off; screen lock, sleep, lid and power button back to defaults (lid fully after a restart)."
echo "Kept: $VB (data.json, backups, voices, app). Delete that folder yourself only if you are sure."
echo "RustDesk is still installed. To remove it: sudo apt remove rustdesk"
