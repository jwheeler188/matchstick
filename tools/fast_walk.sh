#!/bin/bash
# Fast walk from the current spot: one key every 0.35 s; every move cancels out, so it should end where it started.
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
. "$(dirname "$0")/common.sh"
start_bridge
PYTHONUNBUFFERED=1 python3 - "$IP" <<'PY'
import os, socket, sys, time
ip = sys.argv[1]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"])))
plan = [
    ("Right 3, Left 3",   ["RIGHT"] * 3 + ["LEFT"] * 3),
    ("Down 3, Up 3",      ["DOWN"] * 3 + ["UP"] * 3),
    ("Square",            ["DOWN", "RIGHT", "UP", "LEFT"]),
    ("Zig-zag",           ["RIGHT", "DOWN", "RIGHT", "UP", "LEFT", "LEFT"]),
    ("Square the other way", ["UP", "LEFT", "DOWN", "RIGHT"]),
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
        time.sleep(0.35)
time.sleep(1.5)
print("\nDone. Every move cancels out: it should be back where it started.")
PY
show_warnings
