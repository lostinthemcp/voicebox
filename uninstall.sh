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
sudo rm -f /etc/lightdm/lightdm.conf.d/70-voicebox.conf

for k in "org.cinnamon.desktop.screensaver lock-enabled" "org.cinnamon.desktop.screensaver idle-activation-enabled" \
         "org.cinnamon.desktop.session idle-delay" "org.cinnamon.settings-daemon.plugins.power sleep-display-ac" \
         "org.cinnamon.settings-daemon.plugins.power sleep-display-battery" \
         "org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout" \
         "org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-timeout" \
         "org.cinnamon.settings-daemon.plugins.power lock-on-suspend"; do
  # shellcheck disable=SC2086  # schema and key are two words on purpose
  gsettings reset $k 2>/dev/null
done

echo "VoiceBox start-up removed. Auto-login off, screen lock/sleep back to defaults."
echo "Kept: $VB (data.json, backups, voices, app). Delete that folder yourself only if you are sure."
