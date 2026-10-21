# VoiceBox

A talker for someone who cannot speak right now. Tap a button and the tablet says it out loud.
It runs on the Surface by itself, with no internet needed after it is set up.

## Quick install

On the Surface, with internet: open Terminal (Ctrl+Alt+T), copy this line in, press Enter.
It asks for the computer's password and for the family names.

```
cd ~ && wget -qO- https://github.com/lostinthemcp/voicebox/archive/refs/heads/main.tar.gz | tar xz && cd voicebox-main && bash install.sh
```

## What is on the screen

- **Down one side (left by default):** YES, NO, NOT SURE, **HOLD ON** (orange: says "Hold on. Give me a
  minute, I'm typing."), HOME and HOLD FOR HELP.
  These are on every screen.
- **Top corner, next to the gear:** the battery (for example 🔋87%⚡ while charging). Under 20% and not
  charging it turns red and says **PLUG IN**.
- **Across the top:** a big **SPEAK** button with backspace and CLEAR right next to it, then the message
  he is typing (a long message scrolls).
  If CLEAR was tapped by mistake it turns into **UNDO**. Tap that to get the message back.
- **Home screen:** big tiles. The first one, **I love you**, says it straight away. The others (Needs,
  Feelings, Medical, Hospital Staff, Talk, Ribbing, People, Keyboard, Pain, History, About Me) open a
  page of phrases: tap a phrase to say it straight away. A quick double tap never hits the next screen.
  A long list scrolls: swipe up and down, or drag the wide yellow bar on the right edge. A row cut off at
  the bottom means there is more. A swipe never says anything; only a tap does.
- **Keyboard:** type a word, and the yellow buttons above the keys guess the word he is spelling.
  When no word is started, they guess the next word. It learns his words as he uses it.
  The keyboard cover works too. **Enter = SPEAK**.
- **Pain:** pick 0 to 10, then where it hurts (or "Just say the number").
  It says, for example, "My pain is 7 out of 10, in my right leg."
- **History:** everything he said, with the time. Tap one to say it again.
  Tap ★ to save it as a phrase on a tile.
- **HOLD FOR HELP:** hold it for 1 second (it fills up as you hold). It plays a loud chime three
  times, then says "I need help". A quick bump does nothing.

A half-typed message is kept, even if the tablet restarts. Nothing clears by itself.
YES, NO, NOT SURE, HOLD ON and HELP work even while VoiceBox is still starting.
If no voice works at all, the words are shown big on the screen with an OK button, so he can point at them.

## Install (once, with internet)

1. Copy this folder onto the Surface (USB stick or `git clone`).
2. Open a Terminal in the folder and run: `bash install.sh`. To fill the People tile at the same time: `VOICEBOX_PEOPLE="Name1,Name2" bash install.sh` (names stay on the Surface only).
3. Type the password when asked. At the end it prints a summary. Lines marked `CHECK` need a look.
4. Restart the Surface. It logs in by itself and VoiceBox fills the screen.

The installer sets up Chromium, the voices (Piper: Ryan, Ryan sharp, Norman, Joe; Kokoro: Michael, Fenrir,
Puck, George), auto-login,
keeps the screen from locking or sleeping, and turns the volume up to 100%.
It is safe to run again, for example after copying in a newer version. Phrases and data are never touched.

**Keep the Surface plugged in.** Sleep is turned off for the whole system (and the screen never dims
or blanks), so it will run the battery down.
Closing the Type Cover does not put it to sleep, and a bump on the power button does nothing. To shut down, exit to the desktop and use the menu. If the screen is ever stuck or blank, hold the power button for 10 seconds, then press it again: it starts back up into VoiceBox by itself.

## Settings (for family)

**Press and hold the small gear ⚙ in the top corner for 3 seconds.** (A quick tap does nothing,
so he will not open it by accident.)

In Settings you can:

- change the text size and the voice speed, pick the voice, and test it. Ryan is the voice until you pick another.
  After a change of voice (or new phrases) Settings shows "Preparing voice: 37 of 120 phrases": VoiceBox
  is recording every phrase ahead in that voice, so the buttons answer at once. Until it is done, a phrase
  not ready yet is said in Ryan's voice. Typed messages are spoken a sentence at a time.
- choose which side the big buttons are on (**Left side / Right side**)
- **add family names**: type the name, tap **Add name**. Each name becomes a button on the People tile.
- add, change, delete or reorder phrases on any tile (▲ ▼ moves a phrase)
- rename tiles, pick a tile's picture, add a new tile or delete one
- export everything to a file, import a file, or go back to an earlier backup
- **Exit to desktop**

Tap a box, then type with the keys at the bottom of the Settings screen (or the keyboard cover).
Tap **Done** when finished. Changes save by themselves. If the gear turns red, saving is failing
(for example the disk is full). Settings says why at the top, and everything stays on screen while it keeps trying.

## Updating

VoiceBox can update itself. It needs the internet (Wi-Fi) for a few minutes.

1. Hold the gear 3 seconds to open Settings, scroll down to **Updates**.
2. Tap **Check for updates**. It says **Up to date** or **Update available**.
3. If there is one, tap **Install update**. It says "Updating...", then VoiceBox restarts by itself
   and the screen comes back. Keep the Surface on and plugged in while it works.
4. If the new version is worse, tap **Undo last update**. It goes back to the version before.

Phrases, names, history, settings and backups are never changed by an update.
If something goes wrong before the new version is in place, nothing changes and Settings says so.
If a new version is put in but keeps failing to start, VoiceBox goes back to the previous version by itself
(after 3 tries, about 10 seconds).
Updates only come from the reviewed `stable` version on GitHub, never from work in progress.

## Getting out and back in

- **To get to the desktop:** hold the gear 3 seconds, then Settings → **Exit to desktop** → OK.
- If the screen ever freezes: **Alt+F4** closes the window, but VoiceBox opens again by itself
  after 2 seconds. That is on purpose. Use "Exit to desktop" to really leave.
  **Ctrl+Alt+T** opens a Terminal on top.
- **To go back in:** double-tap **Start VoiceBox** on the desktop (it is also in the menu).
  Restarting the Surface also brings it back.

## Backups

Everything (phrases, names, learned words, history, settings) lives in one file:
`~/.local/share/voicebox/data.json` (the `~` is the home folder; `.local` is hidden, press Ctrl+H in the file manager to see it).

- A backup is made every 10 minutes while it is in use (the last 200 are kept) in
  `~/.local/share/voicebox/backups/`, plus one each day for 60 days in `backups/daily/`.
- If the main file is ever damaged, VoiceBox uses the newest good backup by itself.
- **To copy to a USB stick:** Settings → **Export (download)** saves a file to Downloads; copy that
  file to the stick. Or copy the whole `~/.local/share/voicebox/backups` folder.
- **To bring a file back:** Settings → **Import from file**, or pick a backup under
  "Data and backups" and tap Restore. The current data is backed up first, so nothing is lost.

## No sound?

1. Check the volume: the speaker icon at the bottom right of the desktop, or the Surface volume buttons.
   VoiceBox sets every speaker to 100% and unmuted every 30 seconds, and keeps the built-in speaker as
   the output (not a TV/HDMI screen). If you turn it down, it goes back up within 30 seconds.
2. In Settings tap **Test voice**.
3. If the voice sounds different (more robotic) than usual, the Piper voice is not working and the
   backup voice is being used. Run `bash install.sh` again with internet.
4. Restart the Surface.
5. While a voice is being made, SPEAK shows a small pulsing "speaking…". It waits up to 20 seconds.
6. For Joe: `~/.local/share/voicebox/speak.log` has one line per spoken message with how long it took
   (ms) and which voice/engine answered (last 500 lines). `systemctl --user status voicebox` and `journalctl --user -u voicebox` show the server;
   `~/.local/share/voicebox/kiosk.log` shows the browser loop.

## Remote help (RustDesk)

The installer adds RustDesk so Joe can see and fix the Surface from home. After installing, open RustDesk
from the menu, go to Settings > Security > Set permanent password, and write down the ID number it shows.
Joe connects with that ID and password. Nobody can connect without the password.

## Remove

`bash uninstall.sh` stops auto-login, the kiosk and the service, and puts screen lock and sleep back.
It **keeps** all data and backups in `~/.local/share/voicebox`.

## For developers

- `server.py`: Python 3 standard library only. It listens only on 127.0.0.1:8765. Endpoints:
  `GET/POST /data`, `POST /speak` (WAV from Piper; 503 means the page uses the browser voice),
  `GET /backups`, `POST /restore`, `GET /voices`, `GET /prepare`, `POST /exit-kiosk`, `GET /version`,
  `POST /update/check|install|undo` (self-update from the `stable` branch tarball), and `POST /ai`. `/ai` is an
  unused hook that returns 501 and calls nothing. The offline core never depends on it.
- `voices.txt`: the voices shown in Settings (friendly name | Piper path or `kokoro <voice>`); `install.sh`
  and the updater download the missing ones.
- Speech: every answer is cached on disk in `~/.local/share/voicebox/cache/<voice>/` (up to 300 MB, oldest
  removed first), and a low-priority thread pre-renders every phrase in the chosen voice (`GET /prepare` shows
  progress). Kokoro runs as one long-lived `kokoro_worker.py` and Piper as one long-lived `piper_worker.py` (models
  load once; both in `~/.local/share/voicebox/kokoro/venv`); the `piper` program, which reloads the model on
  every call, is only the last resort. If Kokoro is busy, slow or down, `/speak` answers with Piper Ryan.
  `GET /battery` reads `/sys/class/power_supply/BAT*`.
- `static/`: the page (plain HTML, CSS and JS, no build step). `static/words.txt` is the starter word list.
- `bootguard.sh`: runs before every server start (installed outside `app/`, so updates never replace it);
  rolls back to `app.prev` if an updated server fails 3 starts in a row.
- `kiosk.sh`: the browser loop. `install.sh` / `uninstall.sh`: setup.
- Tests: `python3 tests/test_server.py` (data, backups, recovery, security checks, Piper), and
  `tests/ui_test.js` (Playwright click-through; takes the screenshots in `screenshots/`).
