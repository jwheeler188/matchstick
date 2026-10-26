#!/bin/bash
# Key-press check: starts the bridge, sends Home then the navigation keys with pauses to watch the TV.
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
. "$(dirname "$0")/common.sh"
start_bridge
PYTHONUNBUFFERED=1 python3 - "$IP" <<'PY'
import os, socket, sys, time
ip = sys.argv[1]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"])))
def send(*names):
    s.sendall(b"".join(("%s %s\r\n" % (n, ip)).encode() for n in names))
def countdown(n, msg):
    for i in range(n, 0, -1):
        print("\r   %s in %d... " % (msg, i), end="", flush=True); time.sleep(1)
    print("\r" + " " * 60 + "\r", end="")
steps = [
    (("HOME",), "Home screen; note where the highlight is"),
    (("DOWN", "DOWN", "UP"), "BURST: highlight should end up ONE row below where it started"),
    (("DOWN",), "single Down: one row down"),
    (("UP",), "single Up: one row up"),
    (("RIGHT",), "one tile right"),
    (("RIGHT",), "one more tile right"),
    (("LEFT",), "one tile left"),
    (("RIGHT", "RIGHT", "RIGHT"), "BURST: three tiles right"),
    (("LEFT", "LEFT", "LEFT"), "BURST: three tiles back left"),
]
countdown(3, "Starting")
for i, (keys, watch) in enumerate(steps, 1):
    t = time.time(); send(*keys)
    print("%d. >> %-20s [watch: %s]  (sent in %.0f ms)" % (i, " | ".join(keys), watch, (time.time() - t) * 1000))
    time.sleep(4)
print("\nDone.")
PY
show_warnings
