#!/bin/bash
# Opens Settings through the edited bridge and sends nothing else, then watches status for 20 s.
IP="${1:?Usage: bash $0 <fire-tv-ip>}"
. "$(dirname "$0")/common.sh"
start_bridge
PYTHONUNBUFFERED=1 python3 - "$IP" <<'PY'
import os, socket, sys, time
ip = sys.argv[1]
s = socket.create_connection(("127.0.0.1", int(os.environ["MS_PORT"]))); s.settimeout(0.2)
t0 = time.time()
print(">> LAUNCH settings   [watch: Settings opens and stays open; no other commands follow]")
s.sendall(("LAUNCH %s settings\r\n" % ip).encode())
end, buf = time.time() + 20, b""
while time.time() < end:
    try:
        d = s.recv(4096)
        if d:
            for l in d.decode(errors="ignore").split("\r\n"):
                if l: print("   %6.1fs  %s" % (time.time() - t0, l))
    except socket.timeout:
        pass
print("\nDone - Settings should still be on screen.")
PY
show_warnings
