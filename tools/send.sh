#!/bin/bash
# Sends bridge commands to one Fire TV, the way Savant would, and prints what comes back.
# Usage: bash send.sh <fire-tv-ip> "SEARCH" "TEXT stargate" "SELECT"
# Each command waits WAIT seconds (default 3) for status replies before the next one.
IP="${1:?Usage: bash $0 <fire-tv-ip> \"COMMAND [arg]\" ...}"; shift
. "$(dirname "$0")/common.sh"
start_bridge
PYTHONUNBUFFERED=1 python3 - "$IP" "${WAIT:-3}" "$@" <<'PY'
import os, socket, sys, time
ip, wait, cmds = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"]))); s.settimeout(0.2)
t0 = time.time()
for cmd in cmds:
    name, _, arg = cmd.partition(" ")
    print("\n>> %s" % cmd)
    s.sendall(("%s %s%s\r\n" % (name, ip, " " + arg if arg else "")).encode())
    end = time.time() + wait
    while time.time() < end:
        try:
            for l in s.recv(4096).decode(errors="ignore").split("\r\n"):
                if l: print("   %6.1fs  %s" % (time.time() - t0, l))
        except socket.timeout:
            pass
# keep the bridge up so queued commands finish on the TV (Savant leaves it running)
end = time.time() + 8
while time.time() < end:
    try:
        for l in s.recv(4096).decode(errors="ignore").split("\r\n"):
            if l: print("   %6.1fs  %s" % (time.time() - t0, l))
    except socket.timeout:
        pass
print("\nDone.")
PY
show_warnings
