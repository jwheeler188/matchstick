#!/bin/sh
# Matchstick device check: bash run.sh <device-ip> [--no-screenshots]   (see README.txt)
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Needs Python 3: install it (e.g. sudo apt install python3) and run again."; exit 1; }
exec python3 device_check.py "$@"
