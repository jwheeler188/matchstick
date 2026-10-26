#!/bin/bash
# Grid walk: Down 6, Right 4, then wanders around, one key every 0.8 s. No Home first.
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
. "$(dirname "$0")/common.sh"
start_bridge
PYTHONUNBUFFERED=1 python3 - "$IP" <<'PY'
import os, socket, sys, time
ip = sys.argv[1]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"])))
plan = [
    ("Down 6",            ["DOWN"] * 6),
    ("Right 4",           ["RIGHT"] * 4),
    ("Wander: a square",  ["DOWN", "RIGHT", "UP", "LEFT"]),
    ("Wander: zig-zag",   ["RIGHT", "DOWN", "RIGHT", "UP", "LEFT", "LEFT"]),
    ("Wander: up and back", ["UP", "UP", "DOWN", "DOWN"]),
]
for i in range(3, 0, -1):
    print("\r   Starting in %d... " % i, end="", flush=True); time.sleep(1)
print()
n = 0
for label, keys in plan:
    print("\n== %s" % label)
    for k in keys:
        n += 1
        s.sendall(("%s %s\r\n" % (k, ip)).encode())
        print("   %2d. %s" % (n, k))
        time.sleep(0.8)
time.sleep(1)
print("\nDone. Net from start: 6 down, 4 right.")
PY
show_warnings
