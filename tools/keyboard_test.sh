#!/bin/bash
# Checks the bridge side of the Savant Pro app keyboard and pairing support (bridge 1.1.0+):
# the states the app watches and the commands it sends, the way the Keyboard profile sends them.
# Usage: bash keyboard_test.sh <fire-tv-ip>      (on a host with Matchstick installed, or after adb_test.sh)
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
cd "$(dirname "$0")" || exit 1
step() { echo; echo "=================== $1"; echo "    watch: $2"; }

step "1. Status" "nothing on the TV; expect PairingState=paired, Discovered=true, KeyboardFocused=false"
WAIT=4 bash send.sh "$IP" "POLL" "HOME" | grep -E "Pairing|Discovered|KeyboardFocused|ONLINE|BRIDGE"

step "2. App list (ListApps)" "AvailableApps should list the installed apps"
WAIT=4 bash send.sh "$IP" "LISTAPPS" | grep -E "AvailableApps" | cut -c1-300

step "3. Search screen opens: Pro app keyboard should pop up" "KeyboardFocused=true"
WAIT=5 bash send.sh "$IP" "SEARCH" | grep -E "KeyboardFocused"

step "4. SendText stargate (what the Pro app sends on Enter)" "box shows stargate, then the results page; KeyboardFocused=false"
WAIT=8 bash send.sh "$IP" "SENDTEXT stargate" | grep -E "KeyboardFocused|LAST_ERROR=."

step "5. ClearText" "search opens, 'star trek' is typed, then the box empties"
WAIT=4 bash send.sh "$IP" "HOME" "SEARCH" "TEXT star trek" "CLEARTEXT" | grep -E "KeyboardFocused|LAST_ERROR=."

step "6. ClearText with no text box (safety)" "Home screen stays put; LAST_ERROR says no text box"
WAIT=4 bash send.sh "$IP" "HOME" "CLEARTEXT" | grep -E "LAST_ERROR=."

step "7. Unpair, then Pair (Devices menu)" "Unpair is ignored: stays PairingState=paired and ONLINE=1"
WAIT=6 bash send.sh "$IP" "POLL" "UNPAIR" "POLL" "PAIRSTART" | grep -E "Pairing|ONLINE"

step "8. LaunchApp by AppID" "Netflix opens, then Home"
WAIT=6 bash send.sh "$IP" "LAUNCH com.netflix.ninja" "HOME" | grep -E "APP_NAME|LAST_ERROR=."
echo; echo "Done."
