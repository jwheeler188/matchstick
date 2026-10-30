#!/bin/bash
# Builds the device check kit to send to someone testing Matchstick on another Android TV device:
#   ../release/matchstick-devicecheck-<version>.tar.gz
# It holds run.sh, device_check.py, README.txt and the bridge with its key helper.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VER="$(python3 "$ROOT/bridge/firetv_bridge.py" --version)"
NAME="matchstick-devicecheck-$VER"
OUT="$(dirname "$ROOT")/release"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/$NAME/bridge"
cp "$HERE/devicecheck/run.sh" "$HERE/devicecheck/README.txt" "$HERE/device_check.py" "$TMP/$NAME/"
cp "$ROOT/bridge/firetv_bridge.py" "$ROOT/bridge/matchstick-keys.dex" "$TMP/$NAME/bridge/"
cp "$ROOT/LICENSE" "$TMP/$NAME/"
chmod 755 "$TMP/$NAME/run.sh" "$TMP/$NAME/device_check.py"
mkdir -p "$OUT"
COPYFILE_DISABLE=1 tar -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
(cd "$OUT" && shasum -a 256 "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "Built $OUT/$NAME.tar.gz"
tar -tzvf "$OUT/$NAME.tar.gz"
