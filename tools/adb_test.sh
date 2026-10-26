#!/bin/bash
# Full check of one Fire TV, driven the way Savant would: pairing, keys, apps, sleep and wake.
# On a host with Matchstick installed it uses the running service; elsewhere it starts a private
# bridge from this release (nothing is installed; files go in ~/firetv-adb-test).
# Usage:  bash tools/adb_test.sh <fire-tv-ip>
if [ -z "$BASH_VERSION" ]; then exec /bin/bash "$0" "$@"; fi
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
. "$(dirname "$0")/common.sh"
mkdir -p "$WORK"

# --- find or fetch adb
ADB=""
# The installed service's adb first: a different adb version would restart the shared adb server.
for c in "$(dirname "$(readlink /usr/local/bin/firetvctl 2>/dev/null || echo /nonexistent/x)")/platform-tools/adb" \
         ~/matchstick/platform-tools/adb ~/adb-test/platform-tools/adb "$(command -v adb)" ~/.android-sdk-macosx/platform-tools/adb ~/Library/Android/sdk/platform-tools/adb "$WORK/platform-tools/adb"; do
  [ -n "$c" ] && [ -x "$c" ] && { ADB="$c"; break; }
done
if [ -z "$ADB" ]; then
  echo "== Downloading Android platform-tools (adb) into $WORK"
  curl -fsSL -o "$WORK/pt.zip" https://dl.google.com/android/repository/platform-tools-latest-darwin.zip \
    && (cd "$WORK" && unzip -q -o pt.zip 'platform-tools/*' && rm pt.zip) || { echo "Download failed"; exit 1; }
  ADB="$WORK/platform-tools/adb"
fi
echo "== adb: $ADB ($("$ADB" version | head -1))"

# --- pair (first time shows 'Allow USB debugging?' on the TV)
SER="$IP:5555"
"$ADB" connect "$SER"
echo "== If the TV asks 'Allow USB debugging?', tick 'Always allow from this computer' and press OK"
for i in $(seq 1 30); do
  ST="$("$ADB" devices | awk -v s="$SER" '$1==s{print $2}')"
  [ "$ST" = "device" ] && break
  [ -z "$ST" ] && "$ADB" connect "$SER" >/dev/null 2>&1
  sleep 2
done
[ "$ST" = "device" ] || { echo "Not authorized (state: ${ST:-none}). Approve on the TV and re-run."; exit 1; }
echo "== Paired: $("$ADB" -s "$SER" shell getprop ro.product.model | tr -d '\r') / $("$ADB" -s "$SER" shell getprop ro.build.version.release | tr -d '\r')"

# --- start a private bridge (unless the service is running)
if ! bridge_answers; then
  cat > "$WORK/config.json" <<EOF
{"adb_path": "$ADB", "listen_host": "127.0.0.1", "listen_port": $MS_PORT, "poll_seconds": 3,
 "log_file": "$WORK/bridge.log", "log_level": "DEBUG"}
EOF
fi
start_bridge

# --- drive it like Savant: one connection, commands, print what comes back
# (unbuffered so the output shows live when piped through tee)
PYTHONUNBUFFERED=1 python3 - "$IP" <<'PY'
import os, socket, sys, time
ip = sys.argv[1]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"]))); s.settimeout(0.2)
t0 = time.time()
def run(cmd, wait, watch=""):
    print("\n>> %-26s %s" % (cmd, ("[watch: %s]" % watch) if watch else ""))
    for c in cmd.split(" | "):
        name, _, arg = c.partition(" ")
        s.sendall(("%s %s%s\r\n" % (name, ip, " " + arg if arg else "")).encode())
    end, buf = time.time() + wait, b""
    while time.time() < end:
        try:
            d = s.recv(4096)
            if d: buf += d
        except socket.timeout:
            pass
    for l in buf.decode(errors="ignore").split("\r\n"):
        if l: print("   %6.1fs  %s" % (time.time() - t0, l))
steps = [
    ("POLL", 4, ""),
    ("HOME", 3, "home screen"),
    ("DOWN | DOWN | UP", 3, "cursor moves down 2, up 1 (burst batching)"),
    ("RIGHT", 2, "cursor moves right"),
    ("LEFT", 2, "cursor moves left"),
    ("LAUNCH netflix", 6, "Netflix opens (LAST_ERROR if not installed)"),
    ("BACK", 3, "back out"),
    ("HOME", 3, "home screen"),
    ("LAUNCH settings", 5, "Fire TV Settings opens (APP_NAME stays Home on Fire OS 6)"),
    ("HOME", 3, ""),
    ("POWEROFF", 6, "TV/stick goes to sleep"),
    ("POWERON", 8, "wakes back up"),
    ("STATUS", 2, "final state"),
]
for cmd, wait, watch in steps:
    run(cmd, wait, watch)
print("\nDone.")
PY

echo; show_warnings
