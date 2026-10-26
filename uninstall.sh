#!/bin/bash
# Matchstick - uninstaller. Removes only what install.sh created (see .install-manifest).
#   bash uninstall.sh                 remove everything
#   bash uninstall.sh --keep-config   save config.json to ~/matchstick-config.backup.json first
# ADB keys in ~/.android are left alone (other Android tools may use them).
if [ -z "$BASH_VERSION" ]; then exec /bin/bash "$0" "$@"; fi
set -uo pipefail

# Runs from the install folder (install.sh copies it there).
DEST="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.matchstick.bridge"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
OLD_AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"   # versions before 1.2
MANIFEST="$DEST/.install-manifest"

if [ "${1:-}" = "--keep-config" ] && [ -f "$DEST/config.json" ]; then
  cp "$DEST/config.json" "$HOME/matchstick-config.backup.json"
  echo "Saved config to ~/matchstick-config.backup.json"
fi

if [ ! -f "$MANIFEST" ]; then
  echo "No install manifest found in $DEST - nothing removed."; exit 1
fi
# The loop below reads the manifest on stdin, so decide now whether someone can type a password.
INTERACTIVE=0; [ -t 0 ] && INTERACTIVE=1

[ -f "$OLD_AGENT" ] && launchctl unload "$OLD_AGENT" 2>/dev/null && echo "Stopped service"
if [ -f "$PLIST" ]; then
  if [ "$INTERACTIVE" -eq 1 ]; then
    echo "Stopping the service needs your password (sudo)"
    sudo launchctl bootout "system/$LABEL" 2>/dev/null && echo "Stopped service"
  else
    echo "Run this in a terminal: removing the service needs your password (sudo)."; exit 1
  fi
fi
[ -x "$DEST/platform-tools/adb" ] && "$DEST/platform-tools/adb" kill-server 2>/dev/null
# Reverse order: links and files first, directories last.
tail -r "$MANIFEST" 2>/dev/null > /tmp/.matchstick.$$ || tac "$MANIFEST" > /tmp/.matchstick.$$
while IFS= read -r entry; do
  kind="${entry%%:*}"; path="${entry#*:}"
  case "$kind" in
    link|file) [ -e "$path" ] || [ -L "$path" ] && rm -f "$path" && echo "Removed $path" ;;
    sudolink)  # made with sudo by install.sh; only remove it if it is still our link
      if [ -L "$path" ] && [ "$(readlink "$path")" = "$DEST/firetvctl" ]; then
        if [ "$INTERACTIVE" -eq 1 ]; then
          echo "Removing $path needs your password (sudo)"
          sudo rm -f "$path" </dev/tty && echo "Removed $path"
        else
          echo "Left $path in place. To remove it:  sudo rm $path"
        fi
      fi ;;
    sudofile)  [ -e "$path" ] && sudo rm -f "$path" </dev/tty && echo "Removed $path" ;;
    dir)       [ -d "$path" ] && rm -rf "$path" && echo "Removed $path" ;;
  esac
done < /tmp/.matchstick.$$
rm -f /tmp/.matchstick.$$
echo "Matchstick uninstalled. Nothing in Savant/RacePoint was changed."
