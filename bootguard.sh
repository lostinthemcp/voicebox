#!/bin/bash
# VoiceBox boot guard: runs before every server start (ExecStartPre in voicebox.service).
# install.sh puts it next to the app; the in-app updater never replaces it.
# After an update the new server deletes update-pending once it has run 30 seconds. If it is
# started 3 times without getting that far, the new version is broken: put the previous one back.
VB="$(cd "$(dirname "$0")" && pwd)"
P="$VB/update-pending"
[ -f "$P" ] || exit 0
n=$(( $(head -c 10 "$P" 2>/dev/null | tr -cd 0-9) + 1 ))
if [ "$n" -le 3 ] || [ ! -d "$VB/app.prev" ]; then echo "$n" >"$P"; exit 0; fi
rm -rf "$VB/app.bad"
mv "$VB/app" "$VB/app.bad" && mv "$VB/app.prev" "$VB/app" && mv "$VB/app.bad" "$VB/app.prev"
rm -f "$P"
echo "voicebox bootguard: the new version kept failing, so the previous version was put back" >&2
exit 0
