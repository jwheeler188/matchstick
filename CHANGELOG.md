# Changelog

## Planned

- ADB recovery: a small companion app on the Fire TV, installed when pairing, that restarts its
  ADB service when it hangs, so the Fire TV comes back without a restart or the ADB toggle.
  1.4.1 stops the usual cause of the hang; this stays planned as a backstop.
  - Pairing grants it permission to change secure settings; it switches ADB off and on itself,
    the same as the toggle in Developer Options. "Always allow" survives, so the bridge reconnects.
  - It runs from boot, outside ADB, so it keeps running when ADB stops. It acts when it finds ADB
    hung itself or when the bridge asks it to.
  - Ruled out: the remote's Select + Play/Pause restart can't be sent. The remote is Bluetooth,
    the stick has no IR receiver, HDMI-CEC sends one key at a time, and Matchstick's own key
    sending goes through ADB. Nothing else the stick has on the network (casting on port 8009)
    can restart it.
  - Until then, the fallback without touching the Fire TV is a Savant-controlled power outlet
    or relay on its power supply.
- Vega OS support: Fire TVs running Amazon's Vega OS (Linux-based, not Android) instead of Fire
  OS, such as the Fire TV Stick 4K Select. They don't use Android's ADB or key handling, so they
  need their own way to connect, send keys and launch apps. Researching that comes first.

## 1.5.0 (2026-10-27): manual in Blueprint

Profile version 1.5: import the profiles again and refresh existing components (Blueprint only
updates a component when the profile version goes up). The bridge is unchanged from 1.4.1 apart
from its version number.

**Profiles**
- The installer manual now shows on each component's **Documentation** tab in Blueprint, and
  the icon comes in with the profile. On import, Blueprint takes `<name>.htm` (the manual) and
  `<name>.icns` (the icon) from the same folder as `<name>.xml` and files them into its library
  itself; subfolders beside the XML are ignored. So the profiles now ship flat in
  `profile/blueprint-import/`, each with its `.htm` and `.icns`. Import from there.
- Clearer Notes tab: only send the Fire TV commands from workflows and scenes when ONLINE is 1,
  and check LAST_ERROR first when troubleshooting. The Notes tab points to the Documentation tab.
- `generate_profile.py` writes `blueprint-import/` and builds the Documentation tab pages from
  `docs/MANUAL.md`, screenshots embedded, so the tab and the manual always match.

**Manual**
- Section 8 shows what working state feedback looks like in Savant's State Values window, for
  the standard and Keyboard profiles, and how to use the states in buttons and workflows.
- Section 6 covers importing from `blueprint-import`; troubleshooting covers a greyed-out
  Documentation tab and a "?" icon.

## 1.4.1 (2026-10-27): ADB stays connected

Fixes the Fire TV getting stuck as **OFFLINE** until ADB debugging was turned off and on. No
profile changes (profile version stays 1.4), so Blueprint needs nothing.

**What was happening**
- With the TV off, a Fire TV goes quiet on Wi-Fi for a few seconds every few minutes (2 to 40 s
  in testing). Matchstick took each one as a lost connection and reconnected.
- The adb server on the host also drops a network device after about 10 s of silence, without
  telling the Fire TV.
- The Fire TV allows one ADB connection at a time and kept holding the dropped one, refusing
  every new connection until it timed the old one out (up to an hour) or ADB was toggled.

**Bridge**
- Each Fire TV now gets a small relay on the host (`127.0.0.1`, ports from 20000). adb connects
  through it, so adb's 10-second timeout only ever sees the host itself, and the relay holds the
  Fire TV's one connection through quiet spells. `firetvctl status` shows the relay next to the IP.
- When a Fire TV stops answering, Matchstick waits for it (up to `stall_hold_seconds`, 600)
  instead of reconnecting, and carries on when it answers. Savant shows LAST_ERROR "Fire TV not
  answering (asleep or off Wi-Fi?) - waiting for it" meanwhile; commands sent then are dropped,
  not replayed late.
- If a Fire TV still refuses connections because it's holding an old one, the relay tries to
  clear it by reconnecting from the old connection's port, which makes the Fire TV drop it.
  LAST_ERROR says so while it does.
- New settings: `stall_hold_seconds` (600) and `relay_base_port` (20000).
- `firetvctl pair` and `apps`, and `tools/adb_test.sh`, go through the relay when the service
  is running.

**Tested** on a Fire TV Stick 4K (Fire OS 6.7.1.1): a 12-hour overnight run with the TV off
held 42 quiet spells of up to 19 s with no reconnects and no time OFFLINE, and blocking the
Fire TV's traffic on the host for 2 minutes recovered by itself within seconds of unblocking.

## 1.4.0 (2026-10-26): first release

**Profiles**
- New **Amazon Fire TV (Matchstick Keyboard)** profile: the Savant Pro app's text pop-up and
  pairing (Settings → Devices) work for a Fire TV. Built on Savant's Apple TV service; rebrand it
  with Brand to Display and a service alias.
- Keyboard profile: the app's TV button double-pressed opens Live TV, configurable per component
  with the **Double Home Action** state variable (`livetv`, `off`, an app, or `key:<code>`).
- New **LiveTV** custom action in both profiles (Live TV channel guide).
- Both profiles have the Fire TV icon in Blueprint (`profile/images/`).
- Profile files are named the way Blueprint finds them (`amazon_fire tv (...).xml`), fixing
  "Component not found" when dragging them into a design.

**Bridge**
- Key presses about 20 times faster (about 0.05 s): a small key helper stays running on each
  Fire TV, with fallbacks to the remote's input device and Android's `input` command.
- Runs as a system service (LaunchDaemon) so macOS 15 Local Network privacy can't block it;
  always restarts if it stops.
- Commands Savant sends are logged, with the sender's port.
- Savant's Unpair is ignored, so a stray tap in the Pro app can't cut off control.
- Fixed a crash when several requests reached the same Fire TV at once.
- Text entry: SENDTEXT (replace and submit), CLEARTEXT (only when a text box is up), and the
  Fire TV search screen's Enter opens the results.
- Settings opens on Fire OS 6; app names for AvailableApps and APP_NAME improved.

**Tools and docs**
- `tools/` test scripts; they use the installed service when it's running.
- Installer manual in `docs/MANUAL.md`.
- `install.sh` asks for the password once, for the service and the `firetvctl` shortcut.

**Known issues**
- Now and then a Fire TV's ADB service hangs: it accepts connections and drops them at once, and
  the Fire TV shows as OFFLINE. It often clears on its own (within about an hour in testing) and
  Matchstick reconnects by itself. To clear it straight away, turn ADB debugging off and on, or
  restart the Fire TV (manual, section 11). Cause not yet known.
