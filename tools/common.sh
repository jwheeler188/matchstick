# Shared by the test scripts in tools/: find the bridge and make sure one is listening.
#
# On a host with Matchstick installed, the scripts talk to the running service (it already has
# the pairing). Without it, they start a private bridge from this release for the length of the
# test. They never start a second bridge beside the service: a bridge shuts down the host's shared
# adb server when it stops, which would cut the service off.
#
# Sets MS_PORT (the bridge port) and MS_BRIDGE (path to firetv_bridge.py).

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HOME/firetv-adb-test"          # private bridge config and log
MS_PORT="${MS_PORT:-2360}"
for c in "$TOOLS/../bridge/firetv_bridge.py" ~/matchstick/firetv_bridge.py ~/adb-test/firetv_bridge.py; do
  [ -f "$c" ] && { MS_BRIDGE="$c"; break; }
done
export MS_PORT MS_BRIDGE

bridge_answers() {
  python3 - "$MS_PORT" <<'PY' 2>/dev/null
import socket, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2)
s.sendall(b"PING\r\n"); s.settimeout(2)
sys.exit(0 if b"PONG" in s.recv(256) else 1)
PY
}

# Use the installed service if it answers; otherwise start a private bridge (needs adb_test.sh run once).
start_bridge() {
  if bridge_answers; then
    echo "== Using the Matchstick service on port $MS_PORT"
    return
  fi
  [ -f "$WORK/config.json" ] || { echo "No Matchstick service running. Run tools/adb_test.sh <ip> once first (it pairs and writes a test config)."; exit 1; }
  [ -n "$MS_BRIDGE" ] || { echo "Can't find firetv_bridge.py"; exit 1; }
  : > "$WORK/bridge.log"
  python3 "$MS_BRIDGE" -c "$WORK/config.json" > "$WORK/stdout.txt" 2>&1 &
  BPID=$!
  trap 'kill -TERM $BPID 2>/dev/null; wait $BPID 2>/dev/null' EXIT
  for i in $(seq 1 30); do bridge_answers && break; sleep 0.5; done
  bridge_answers || { echo "Bridge failed to start:"; cat "$WORK/stdout.txt"; exit 1; }
  echo "== Started a private bridge ($("$MS_BRIDGE" --version 2>/dev/null || python3 "$MS_BRIDGE" --version))"
}

# Warnings and errors from the private bridge's log, or from the service's log.
show_warnings() {
  local log="$WORK/bridge.log"
  if [ -z "${BPID:-}" ]; then            # the service: its log is in the install folder
    local inst; inst="$(dirname "$(readlink /usr/local/bin/firetvctl 2>/dev/null || echo ~/matchstick/x)")"
    log="$inst/logs/firetv_bridge.log"
  fi
  [ -f "$log" ] || return
  if [ -n "${BPID:-}" ]; then
    grep -E "WARNING|ERROR" "$log" || echo "== Bridge log: no warnings/errors"
  else
    echo "== Service log, last warnings/errors (if any):"; grep -E "WARNING|ERROR" "$log" | tail -5
  fi
}
