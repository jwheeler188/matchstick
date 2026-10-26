#!/bin/bash
# Matchstick (Fire TV bridge for Savant) - installer for the Savant Mac mini host.
# Run as the user Savant runs as (normally RPM):   bash install.sh [install-folder]
# The folder defaults to ~/matchstick.
# Safe to re-run for upgrades; config.json and ADB keys are kept.
# The service is a system LaunchDaemon that runs as this user. A login LaunchAgent would be
# blocked from the Fire TVs by macOS 15's Local Network privacy; launchd daemons are not.
# Installing it needs your password once (sudo).
if [ -z "$BASH_VERSION" ]; then exec /bin/bash "$0" "$@"; fi
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${USER:-$(id -un)}"
DEST="${1:-$HOME/matchstick}"
LABEL="com.matchstick.bridge"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
OLD_AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"   # versions before 1.2
MANIFEST="$DEST/.install-manifest"
BIN_LINK="/usr/local/bin/firetvctl"
PT_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || die "This installer is for macOS (the Savant host)."
[ "$(id -u)" -ne 0 ] || die "Run as the Savant user (e.g. RPM), not with sudo."
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 not found."
[ -t 0 ] || die "Run this in a terminal: installing the service needs your password (sudo)."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' || die "Python 3.6+ needed."

FRESH=0
[ -d "$DEST" ] || FRESH=1
mkdir -p "$DEST/logs"
[ -f "$MANIFEST" ] || : > "$MANIFEST"
note() { grep -qxF "$1" "$MANIFEST" || echo "$1" >> "$MANIFEST"; }
[ "$FRESH" -eq 1 ] && note "dir:$DEST"

say "Installing the service needs your password (sudo)"
sudo -v || die "sudo is needed to install the service."

# Stop a running copy before replacing files. Older versions ran as a LaunchAgent: remove it.
if [ -f "$OLD_AGENT" ]; then
  say "Removing the old login-item service (LaunchAgent)"
  launchctl unload "$OLD_AGENT" 2>/dev/null || true
  rm -f "$OLD_AGENT"
  sed -i '' "\|^file:$OLD_AGENT\$|d" "$MANIFEST"
fi
if sudo launchctl print "system/$LABEL" >/dev/null 2>&1; then
  say "Stopping running bridge"
  sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
fi

say "Copying bridge files to $DEST"
cp "$SRC/bridge/firetv_bridge.py" "$DEST/"
cp "$SRC/bridge/matchstick-keys.dex" "$DEST/"   # key helper the bridge copies to each Fire TV
cp "$SRC/bridge/config.example.json" "$DEST/"
cp "$SRC/scripts/firetvctl" "$DEST/"
cp "$SRC/uninstall.sh" "$DEST/"
chmod +x "$DEST/firetv_bridge.py" "$DEST/firetvctl" "$DEST/uninstall.sh"
if [ ! -f "$DEST/config.json" ]; then
  cp "$SRC/bridge/config.example.json" "$DEST/config.json"
  say "Created config.json (defaults: port 2360, any LAN Fire TV)"
fi

# Android platform-tools (adb). Use a zip shipped next to this script if present (offline install).
if [ ! -x "$DEST/platform-tools/adb" ]; then
  ZIP="$SRC/platform-tools-latest-darwin.zip"
  TMPZ=""
  if [ ! -f "$ZIP" ]; then
    say "Downloading Android platform-tools (adb) from Google"
    TMPZ="${TMPDIR:-/tmp}/matchstick-platform-tools.$$.zip"
    curl -fL --retry 2 -o "$TMPZ" "$PT_URL" || die "Download failed. Put platform-tools-latest-darwin.zip next to install.sh and re-run."
    ZIP="$TMPZ"
  fi
  ( cd "$DEST" && unzip -q -o "$ZIP" 'platform-tools/*' )
  [ -n "$TMPZ" ] && rm -f "$TMPZ"
  note "dir:$DEST/platform-tools"
fi
ADBV="$("$DEST/platform-tools/adb" version 2>&1)" || die "adb would not run: $ADBV"
echo "$ADBV" | head -1

say "Writing launchd service $LABEL (runs as $USER_NAME)"
TMPP="$(mktemp)"
cat > "$TMPP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>$USER_NAME</string>
  <key>GroupName</key><string>$(id -gn)</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DEST/firetv_bridge.py</string>
    <string>-c</string><string>$DEST/config.json</string>
  </array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>USER</key><string>$USER_NAME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ExitTimeOut</key><integer>15</integer>
  <key>StandardOutPath</key><string>$DEST/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$DEST/logs/launchd.err</string>
</dict>
</plist>
EOF
sudo install -o root -g wheel -m 644 "$TMPP" "$PLIST"
rm -f "$TMPP"
note "sudofile:$PLIST"

# Convenience link so `firetvctl` works from any folder. /usr/local/bin normally belongs to root,
# so it is made with sudo (already authorised above).
if [ -L "$BIN_LINK" ] || [ -e "$BIN_LINK" ]; then
  [ "$(readlink "$BIN_LINK")" = "$DEST/firetvctl" ] || say "Left $BIN_LINK alone (it points somewhere else)"
elif [ ! -d /usr/local/bin ]; then
  say "Skipped the firetvctl shortcut (no /usr/local/bin). Use $DEST/firetvctl"
elif [ -w /usr/local/bin ]; then
  ln -s "$DEST/firetvctl" "$BIN_LINK" && note "link:$BIN_LINK"
elif sudo ln -s "$DEST/firetvctl" "$BIN_LINK"; then
  note "sudolink:$BIN_LINK"
else
  say "Skipped the firetvctl shortcut. To add it:  sudo ln -s $DEST/firetvctl $BIN_LINK"
fi

# An adb server left over from a login session or SSH would keep the old network restrictions;
# stop it so the service starts its own.
"$DEST/platform-tools/adb" kill-server >/dev/null 2>&1 || true
sudo launchctl bootstrap system "$PLIST"
sleep 2
VER="$("$PY" "$DEST/firetv_bridge.py" --version)"
if sudo launchctl print "system/$LABEL" 2>/dev/null | grep -q "state = running"; then
  say "Matchstick $VER installed and running on port $("$PY" -c "import json;print(json.load(open('$DEST/config.json')).get('listen_port',2360))")"
else
  die "Service did not start. Check $DEST/logs/launchd.err"
fi
cat <<EOF

Next steps
  1. On each Fire TV: Settings > My Fire TV > Developer Options > ADB debugging ON
     (no Developer Options? Settings > My Fire TV > About > click the device name 7 times)
  2. Pair each one:   $DEST/firetvctl pair <fire-tv-ip>
     then choose "Always allow from this computer" and OK on the TV.
  3. In Blueprint add "Amazon Fire TV (Matchstick)" or "(Matchstick Keyboard)" from profile/,
     set its Device IP to this host and its "Fire TV IP Address" state variable to the Fire TV.
  Full instructions: docs/MANUAL.md
EOF
