# VoiceBox

A talker for someone who cannot speak right now. Tap a button and the tablet says it out loud.
It runs on the Surface by itself, with no internet needed after it is set up.

## What is on the screen

- **Down one side (left by default):** YES, NO, NOT SURE, HOME and HOLD FOR HELP.
  These are on every screen.
- **Across the top:** the message he is typing, a big **SPEAK** button, backspace, and CLEAR.
  If CLEAR was tapped by mistake it turns into **UNDO**. Tap that to get the message back.
- **Home screen:** big tiles: Needs, Feelings, Medical, Hospital Staff, Talk, People, Keyboard,
  Pain, History, About Me. Tap a tile, then tap a phrase to say it straight away.
  **More ▶** shows the next page when a tile has more than 9 phrases.
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

## Install (once, with internet)

1. Copy this folder onto the Surface (USB stick or `git clone`).
2. Open a Terminal in the folder and run: `bash install.sh`
3. Type the password when asked. At the end it prints a summary. Lines marked `CHECK` need a look.
4. Restart the Surface. It logs in by itself and VoiceBox fills the screen.

The installer sets up Chromium, the Piper voice (Ryan, plus Joe as a second male voice), auto-login,
keeps the screen from locking or sleeping, and turns the volume up to 100%.
It is safe to run again, for example after copying in a newer version. Phrases and data are never touched.

**Keep the Surface plugged in.** Sleep is turned off, so it will run the battery down.

## Settings (for family)

**Press and hold the small gear ⚙ in the top corner for 3 seconds.** (A quick tap does nothing,
so he will not open it by accident.)

In Settings you can:

- change the text size and the voice speed, pick the voice, and test it
- choose which side the big buttons are on (**Left side / Right side**)
- **add family names**: type the name, tap **Add name**. Each name becomes a button on the People tile.
- add, change, delete or reorder phrases on any tile (▲ ▼ moves a phrase)
- rename tiles, pick a tile's picture, add a new tile or delete one
- export everything to a file, import a file, or go back to an earlier backup
- **Exit to desktop**

Tap a box, then type with the keys at the bottom of the Settings screen (or the keyboard cover).
Tap **Done** when finished. Changes save by themselves.

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
   VoiceBox sets the volume to 100% at every login.
2. In Settings tap **Test voice**.
3. If the voice sounds different (more robotic) than usual, the Piper voice is not working and the
   backup voice is being used. Run `bash install.sh` again with internet.
4. Restart the Surface.
5. For Joe: `systemctl --user status voicebox` and `journalctl --user -u voicebox` show the server;
   `~/.local/share/voicebox/kiosk.log` shows the browser loop.

## Remove

`bash uninstall.sh` stops auto-login, the kiosk and the service, and puts screen lock and sleep back.
It **keeps** all data and backups in `~/.local/share/voicebox`.

## For developers

- `server.py`: Python 3 standard library only. It listens only on 127.0.0.1:8765. Endpoints:
  `GET/POST /data`, `POST /speak` (WAV from Piper; 503 means the page uses the browser voice),
  `GET /backups`, `POST /restore`, `GET /voices`, `POST /exit-kiosk`, and `POST /ai`. `/ai` is an
  unused hook that returns 501 and calls nothing. The offline core never depends on it.
- `static/`: the page (plain HTML, CSS and JS, no build step). `static/words.txt` is the starter word list.
- `kiosk.sh`: the browser loop. `install.sh` / `uninstall.sh`: setup.
- Tests: `python3 tests/test_server.py` (data, backups, recovery, security checks, Piper), and
  `tests/ui_test.js` (Playwright click-through; takes the screenshots in `screenshots/`).
