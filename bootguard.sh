#!/bin/bash
# VoiceBox boot guard: runs before every server start (ExecStartPre in voicebox.service).
# install.sh keeps it next to app/ (never inside it), so neither updates nor installs replace it
# while they run. update-pending holds "<version> <starts>": written BEFORE app/ is touched, and
# deleted by that version's server once the page has loaded and a /speak worked. If that version
# is started 3 times without getting there, it is broken: put app.prev (the last good one) back.
#   bootguard.sh         count a start, roll back on the 4th
#   bootguard.sh --now   roll back right away (install.sh, when its copy failed)
# Each step is checked and can be resumed after a power cut; the marker stays until app/ is back.
VB="$(cd "$(dirname "$0")" && pwd)"
P="$VB/update-pending"
LOG="$VB/bootguard.log"
say() { echo "voicebox bootguard: $*" >&2; echo "$(date '+%F %T') $*" >>"$LOG" 2>/dev/null; }
[ -f "$P" ] || exit 0
read -r ver n <"$P" 2>/dev/null
n="$(( ${n//[^0-9]/} + 1 ))"
if [ "${1:-}" != --now ]; then
  if [ "$n" -le 3 ]; then
    { echo "$ver $n" >"$P.tmp" && mv -f "$P.tmp" "$P"; } || say "could not count this start"
    exit 0
  fi
fi
if [ ! -d "$VB/app.prev" ] && [ ! -d "$VB/app.bad" ]; then
  say "version $ver keeps failing, but there is no earlier version to go back to"
  exit 0
fi
say "version $ver did not get healthy after $((n - 1)) starts: putting the previous version back"
# 1) the broken one out of the way (skipped when a cut-off rollback already did it)
if [ ! -d "$VB/app.bad" ]; then
  mv "$VB/app" "$VB/app.bad" || { say "ROLLBACK FAILED at step 1 (app -> app.bad); nothing changed"; exit 0; }
fi
# 2) the good one back
if [ ! -d "$VB/app" ]; then
  mv "$VB/app.prev" "$VB/app" || { say "ROLLBACK FAILED at step 2 (app.prev -> app); app.bad and app.prev left as they are"; exit 0; }
fi
if [ ! -s "$VB/app/server.py" ]; then
  say "ROLLBACK FAILED: app/server.py is missing after the swap; marker kept"
  exit 0
fi
rm -f "$P"  # app/ is good again
# 3) keep the broken one as app.prev (so Undo can re-try it); harmless if this fails
if [ -d "$VB/app.bad" ] && [ ! -d "$VB/app.prev" ]; then
  mv "$VB/app.bad" "$VB/app.prev" || say "note: could not rename app.bad to app.prev (app/ is fine)"
fi
say "rolled back: app/ is the previous version again"
exit 0
