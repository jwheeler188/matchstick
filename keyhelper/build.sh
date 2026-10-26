#!/bin/bash
# Builds bridge/matchstick-keys.dex from Keys.java. Needs a JDK and the Android SDK
# (platforms/android-NN/android.jar and build-tools/NN/d8). Only needed when Keys.java changes;
# the built .dex is shipped with the bridge.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
JAR="$(ls -d "$SDK"/platforms/android-*/android.jar | tail -1)"
D8="$(ls -d "$SDK"/build-tools/*/d8 | tail -1)"
TMP="$(mktemp -d)"
javac --release 8 -cp "$JAR" -d "$TMP" "$HERE/Keys.java"
"$D8" --min-api 21 --lib "$JAR" --output "$TMP" "$TMP"/com/matchstick/*.class
cp "$TMP/classes.dex" "$HERE/../bridge/matchstick-keys.dex"
rm -rf "$TMP"
echo "built bridge/matchstick-keys.dex ($(wc -c < "$HERE/../bridge/matchstick-keys.dex") bytes)"
