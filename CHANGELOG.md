# Changelog

## Planned

- ADB recovery: a small companion app on the Fire TV, installed when pairing, that restarts its
  ADB service when it hangs, so the Fire TV comes back without a restart or the ADB toggle.
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
