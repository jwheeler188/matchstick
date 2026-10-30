#!/usr/bin/env python3
"""Matchstick device check: tries Matchstick on an Android TV device it wasn't built for (an
NVIDIA Shield, say) and collects what's needed to tell whether it works there or what to change.

Run on a Linux or Mac computer on the same network as the device:
    bash run.sh <device-ip>          (or: python3 device_check.py <device-ip>)
    bash run.sh <device-ip> --unattended       no questions; judged from the device itself

It downloads Android platform-tools (adb) into this folder if needed, pairs with the device,
reads its setup, then sends Matchstick's commands one at a time. Where there's a plain Android
way to do the same thing (keys, search, typing, Enter), it tries that too, to tell a Matchstick
problem from a device one. After each step it checks what the device reports (focus, keyboard,
power, playback, on-screen text, whether the screen changed), saves a screenshot, and asks what
happened on the TV. Everything goes into a report folder and one .tar.gz file to send back.

Nothing is installed on this computer; it runs its own adb on port 5038, so an adb already in
use here is left alone. On the device, Matchstick's key helper is copied to /data/local/tmp
for the test and removed at the end.
"""
import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime

KIT = os.path.dirname(os.path.abspath(__file__))
ADB_SERVER_PORT = "5038"
PT_URL = "https://dl.google.com/android/repository/platform-tools-latest-%s.zip"

# Read-only facts about the device, collected once after pairing. Nothing account-related:
# no Wi-Fi names, serial numbers or accounts.
DIAGNOSTICS = [
    ("build", "for p in ro.product.manufacturer ro.product.brand ro.product.model ro.product.device "
              "ro.build.version.release ro.build.version.sdk ro.build.version.name "
              "ro.build.display.id ro.build.version.incremental ro.build.characteristics "
              "ro.product.cpu.abi; do echo \"$p=$(getprop $p)\"; done"),
    ("apps_tv_launcher", "cmd package query-activities -a android.intent.action.MAIN "
                         "-c android.intent.category.LEANBACK_LAUNCHER 2>&1 | "
                         "grep -E 'packageName=|nonLocalizedLabel=|^[^ ]'"),
    ("home_app", "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                 "-c android.intent.category.HOME 2>&1"),
    ("search_handlers", "for a in android.search.action.GLOBAL_SEARCH android.intent.action.SEARCH "
                        "android.intent.action.ASSIST android.intent.action.VOICE_ASSIST "
                        "android.intent.action.WEB_SEARCH; do echo \"== $a\"; "
                        "cmd package resolve-activity --brief -a $a 2>&1; done"),
    ("settings_handler", "cmd package resolve-activity --brief -a android.settings.SETTINGS 2>&1"),
    ("focus", "echo '== dumpsys window windows'; dumpsys window windows 2>/dev/null | "
              "grep -m2 -E 'mCurrentFocus|mFocusedApp'; echo '== dumpsys window'; "
              "dumpsys window 2>/dev/null | grep -m2 -E 'mCurrentFocus|mFocusedApp'"),
    ("power", "dumpsys power 2>/dev/null | grep -E 'mWakefulness=|Display Power|mHoldingDisplaySuspendBlocker|mStayOn'"),
    ("input_method", "dumpsys input_method 2>/dev/null | grep -E 'mCurMethodId|mInputShown|mShowRequested|mCurId' | head -n 8"),
    ("media_session", "dumpsys media_session 2>/dev/null | grep -E 'package=|active=|state=' | head -n 40"),
    ("input_devices", "getevent -pl 2>/dev/null | grep -E '^add device|name:' | head -n 40"),
    ("keylayouts", "ls /system/usr/keylayout /vendor/usr/keylayout 2>/dev/null | head -n 60"),
    ("shell", "echo abi=$(getprop ro.product.cpu.abi); type print 2>&1; type sendevent 2>&1; "
              "ls -l /system/bin/app_process* 2>&1; id"),
    ("network_interfaces", "ip -o -4 addr 2>/dev/null | awk '{print $2}'"),
    ("logcat_tags", "logcat -d -v brief 2>/dev/null | tail -n 400 | "
                    "sed -E 's/^[VDIWEF]\\/([^(]*)\\(.*/\\1/' | sort | uniq -c | sort -rn | head -n 40"),
]

# Taken after each step, so the report shows what the device itself reported at that moment.
SNAPSHOT = ("echo \"$(dumpsys window 2>/dev/null | grep -m1 mCurrentFocus)\"; "
            "dumpsys input_method 2>/dev/null | grep -m1 -o 'mInputShown=[a-z]*'; "
            "dumpsys power 2>/dev/null | grep -m1 -o 'mWakefulness=[A-Za-z]*'; "
            "dumpsys media_session 2>/dev/null | grep -E 'package=|active=true|state=PlaybackState' | head -n 6")

YOUTUBE = ["com.google.android.youtube.tv", "com.amazon.firetv.youtube"]
APP_PREFERENCE = ["com.google.android.youtube.tv", "com.netflix.ninja", "com.google.android.youtube.tvmusic",
                  "com.plexapp.android", "com.amazon.amazonvideo.livingroom", "com.disney.disneyplus"]


def say(msg=""):
    print(msg, flush=True)


def heading(msg):
    say("\n" + "=" * 70 + "\n" + msg + "\n" + "=" * 70)


class Adb:
    def __init__(self, path):
        self.path = path
        self.env = dict(os.environ, ANDROID_ADB_SERVER_PORT=ADB_SERVER_PORT)

    def run(self, *args, serial=None, timeout=20, binary=False):
        cmd = [self.path] + (["-s", serial] if serial else []) + list(args)
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout, env=self.env)
        except subprocess.TimeoutExpired:
            return b"" if binary else "(timed out after %ds)" % timeout
        if binary:
            return r.stdout
        return (r.stdout + r.stderr).decode(errors="replace").replace("\r\n", "\n")

    def sh(self, serial, cmd, timeout=20):
        return self.run("shell", cmd, serial=serial, timeout=timeout)

    def state(self, serial):
        for line in self.run("devices").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == serial:
                return parts[1]
        return ""


def find_adb():
    """This kit's own adb, downloaded on first run."""
    adb = os.path.join(KIT, "platform-tools", "adb")
    if os.path.isfile(adb):
        return adb
    osname = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    if not osname:
        sys.exit("This kit runs on Linux or a Mac.")
    say("Downloading Android platform-tools (adb) from Google into %s ..." % KIT)
    zpath = os.path.join(KIT, "platform-tools.zip")
    try:
        urllib.request.urlretrieve(PT_URL % osname, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(KIT)
    except Exception as e:
        sys.exit("Download failed (%s). Check the internet connection and run again." % e)
    finally:
        if os.path.exists(zpath):
            os.remove(zpath)
    for name in os.listdir(os.path.join(KIT, "platform-tools")):
        p = os.path.join(KIT, "platform-tools", name)
        if os.path.isfile(p):
            os.chmod(p, 0o755)
    return adb


def find_bridge():
    for d in (os.path.join(KIT, "bridge"), os.path.join(KIT, "..", "bridge")):
        p = os.path.abspath(os.path.join(d, "firetv_bridge.py"))
        if os.path.isfile(p):
            return p
    sys.exit("Can't find bridge/firetv_bridge.py next to this script.")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    """One connection to the bridge, like Savant's: sends commands, keeps every status line."""

    def __init__(self, port, ip):
        self.ip = ip
        self.s = socket.create_connection(("127.0.0.1", port), 5)
        self.s.settimeout(0.3)
        self.lines = []                 # (seconds since start, line)
        self.state = {}
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        buf = b""
        while self.alive:
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            buf += d
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                with self.lock:
                    self.lines.append((time.time() - self.t0, line))
                    if "=" in line:
                        k, v = line.split("=", 1)
                        self.state[k] = v

    def send(self, cmd, arg=""):
        self.s.sendall(("%s %s%s\r\n" % (cmd, self.ip, " " + arg if arg else "")).encode())

    def mark(self):
        with self.lock:
            return len(self.lines)

    def since(self, mark):
        with self.lock:
            return list(self.lines[mark:])

    def get(self, key, default=""):
        with self.lock:
            return self.state.get(key, default)


class Check:
    def __init__(self, args):
        self.ip = args.ip
        self.screens = not args.no_screenshots
        self.unattended = args.unattended
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        self.work = os.path.join(KIT, "report-%s" % stamp)
        os.makedirs(os.path.join(self.work, "diag"))
        os.makedirs(os.path.join(self.work, "steps"))
        self.results = []
        self.facts = {}
        self.bridge = None
        self.client = None
        self.relay = ""
        self.search_how = None          # "bridge", "key" or None once SEARCH has been tried
        self.app_pkg = None

    # ---------------------------------------------------------------- setup
    def pair(self):
        ser = self.ip + ":5555"
        self.adb.run("start-server", timeout=30)
        say(self.adb.run("connect", ser).strip())
        say("\nOn the TV: if it asks to allow debugging from this computer, tick 'Always allow'"
            "\nand choose OK.")
        st = ""
        for i in range(60):
            st = self.adb.state(ser)
            if st == "device":
                break
            if not st:
                self.adb.run("connect", ser)
            time.sleep(2)
        if st != "device":
            sys.exit("\nCouldn't connect (state: %s). Check that Network debugging is on in Developer "
                     "options,\nthe IP is right, and you allowed the prompt on the TV. Then run again."
                     % (st or "no answer"))
        model = self.adb.sh(ser, "getprop ro.product.model").strip()
        say("Paired with %s." % model)
        return ser

    def collect(self, ser):
        heading("Reading the device's setup (read-only)")
        for name, cmd in DIAGNOSTICS:
            out = self.adb.sh(ser, cmd, timeout=30)
            with open(os.path.join(self.work, "diag", name + ".txt"), "w") as f:
                f.write(out)
            say("  %-20s %d lines" % (name, len(out.splitlines())))
        for line in open(os.path.join(self.work, "diag", "build.txt")):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                self.facts[k] = v

    def start_bridge(self, bridge_py):
        heading("Starting Matchstick (a private copy, just for this test)")
        port = free_port()
        cfg = {"adb_path": self.adb.path, "listen_host": "127.0.0.1", "listen_port": port,
               "poll_seconds": 3, "log_file": os.path.join(self.work, "bridge.log"), "log_level": "DEBUG"}
        cfg_path = os.path.join(self.work, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=1)
        self.relays_file = os.path.join(os.path.dirname(bridge_py), "relays.json")
        self.bridge = subprocess.Popen([sys.executable, bridge_py, "-c", cfg_path],
                                       stdout=open(os.path.join(self.work, "bridge-stdout.txt"), "w"),
                                       stderr=subprocess.STDOUT, env=self.adb.env)
        for i in range(40):
            try:
                self.client = Client(port, self.ip)
                break
            except OSError:
                time.sleep(0.5)
        if not self.client:
            sys.exit("Matchstick didn't start; see %s/bridge-stdout.txt" % self.work)
        self.client.send("POLL")
        for i in range(40):
            if self.client.get("ONLINE") == "1":
                break
            time.sleep(0.5)
        try:
            self.relay = json.load(open(self.relays_file))[self.ip]["serial"]
        except Exception:
            self.relay = ""
        say("Matchstick %s is connected: ONLINE=%s, MODEL=%s" % (
            self.client.get("BRIDGE_VERSION", "?"), self.client.get("ONLINE", "0"), self.client.get("MODEL")))

    # ---------------------------------------------------------------- steps
    def screencap(self):
        if not self.relay:
            return b""
        png = self.adb.run("exec-out", "screencap -p", serial=self.relay, timeout=20, binary=True)
        return png if png.startswith(b"\x89PNG") else b""

    def snapshot(self, n, name):
        """What the device reports at the end of a step, and a screenshot of the TV."""
        if not self.relay:
            return "", "", b""
        text = self.adb.sh(self.relay, SNAPSHOT, timeout=15).strip()
        png, shot = self.screencap(), ""
        if png and self.screens:
            shot = "steps/%02d-%s.png" % (n, name)
            with open(os.path.join(self.work, shot), "wb") as f:
                f.write(png)
        elif self.screens:
            shot = "none (the device gave none; apps like Netflix block screenshots)"
        return text, shot, png

    def screen_text(self):
        """Every piece of text on screen (from Android's UI dump), lower case."""
        out = self.adb.sh(self.relay, "uiautomator dump /sdcard/mc-ui.xml >/dev/null 2>&1; "
                                      "cat /sdcard/mc-ui.xml; rm -f /sdcard/mc-ui.xml", timeout=30)
        return " ".join(re.findall(r'(?:text|content-desc)="([^"]*)"', out)).lower()

    def ask(self, question):
        while True:
            a = input("\n  %s\n  [y] yes  [n] no  [s] skip / not sure: " % question).strip().lower()
            if a[:1] in ("y", "n", "s"):
                break
        note = ""
        if a[0] != "y":
            note = input("  What happened instead? (Enter to leave blank): ").strip()
        return {"y": "yes", "n": "no", "s": "skipped"}[a[0]], note

    def step(self, name, title, watch, send=(), question=None, before=None, keys=None,
             auto=None, changes=False, unattended_before=None):
        """Run one step: send commands (cmd, arg, seconds to wait) or call functions, show the
        status that came back, record what the device reports and a screenshot, judge it
        automatically where the device can tell, and (unless unattended) ask what happened."""
        n = len(self.results) + 1
        heading("Step %d: %s" % (n, title))
        if before and not self.unattended:
            input("  %s\n  Press Enter when ready... " % before)
        if unattended_before and self.unattended:
            unattended_before()
        say("  Watch the TV: %s" % watch)
        time.sleep(1.5)
        png_before = self.screencap() if changes else b""
        m = self.client.mark()
        sent = []
        for item in send:
            if callable(item):
                sent.append(item())
                continue
            cmd, arg, wait = item
            self.client.send(cmd, arg)
            sent.append(("%s %s" % (cmd, arg)).strip())
            time.sleep(wait)
        lines = self.client.since(m)
        shown = [l for t, l in lines if not keys or any(l.startswith(k + "=") for k in keys)]
        say("  Sent: %s" % ", ".join(sent or ["(nothing)"]))
        if shown:
            say("  Matchstick reported:")
            for l in shown[-12:]:
                say("    " + (l if len(l) < 160 else l[:157] + "..."))
        snap, shot, png = self.snapshot(n, name)
        ctx = {"states": [l for t, l in lines], "device": snap,
               "changed": (png != png_before) if (changes and png and png_before) else None}
        verdict = ""
        if changes and ctx["changed"] is not None:
            verdict = "pass: the screen changed" if ctx["changed"] else "fail: the screen didn't change"
        if auto:
            try:
                verdict = auto(ctx) or verdict
            except Exception as e:
                verdict = "can't tell: %s" % e
        if verdict:
            say("  Automatic check: %s" % verdict)
        if question and not self.unattended:
            answer, note = self.ask(question)
        else:
            answer, note = ("auto" if question else "n/a"), ""
        self.results.append({"n": n, "name": name, "title": title, "watch": watch, "sent": sent,
                             "answer": answer, "note": note, "auto": verdict,
                             "states": ["%.1fs %s" % (t, l) for t, l in lines],
                             "device": snap, "screenshot": shot})
        return answer if answer in ("yes", "no", "skipped") else ("yes" if verdict.startswith("pass") else "no")

    # Commands sent straight to the device with adb: the plain Android way, to compare with
    # Matchstick's. They show in the report as "(adb) ...".
    def adb_cmd(self, cmd, wait=3):
        def go():
            self.adb.sh(self.relay, cmd)
            time.sleep(wait)
            return "(adb) " + cmd
        return go

    def adb_key(self, *codes, wait=4):
        return self.adb_cmd("input keyevent %s" % " ".join(str(c) for c in codes), wait)

    def open_search(self):
        if self.search_how == "key":
            return [self.adb_key(84)]
        return [("SEARCH", "", 5)]

    def pick_app(self):
        try:
            apps = json.loads(self.client.get("AvailableApps", "[]"))
        except ValueError:
            apps = []
        ids = [a.get("identifier", "") for a in apps]
        for p in (YOUTUBE if self.unattended else []) + APP_PREFERENCE:
            if p in ids:
                return p, next(a["name"] for a in apps if a.get("identifier") == p)
        for a in apps:
            if "settings" not in a.get("identifier", "") and "launcher" not in a.get("identifier", ""):
                return a["identifier"], a.get("name", a["identifier"])
        return None, None

    # ---- automatic checks: each takes the step's context and returns "pass: ..." or "fail: ..."
    @staticmethod
    def last(ctx, key):
        vals = [l.split("=", 1)[1] for l in ctx["states"] if l.startswith(key + "=")]
        return vals[-1] if vals else None

    def expect_state(self, key, want):
        def check(ctx):
            v = self.last(ctx, key)
            if v is None:
                v = self.client.get(key, None)
                if v is None:
                    return "can't tell: Matchstick didn't report %s" % key
                return ("pass" if v == want else "fail") + ": %s=%s (unchanged)" % (key, v)
            return ("pass" if v == want else "fail") + ": %s=%s" % (key, v)
        return check

    def expect_focus(self, *words):
        def check(ctx):
            focus = ctx["device"].splitlines()[0] if ctx["device"] else ""
            ok = any(w.lower() in focus.lower() for w in words)
            return ("pass" if ok else "fail") + ": focus is %s" % (focus.strip() or "(unknown)")
        return check

    def expect_settings(self, ctx):
        """Settings focused, or (where Settings is part of the home app, as on Fire OS 6) the
        Settings handler's app focused and the screen changed."""
        focus = ctx["device"].splitlines()[0] if ctx["device"] else ""
        if "settings" in focus.lower():
            return "pass: focus is %s" % focus.strip()
        try:
            handler = open(os.path.join(self.work, "diag", "settings_handler.txt")).read().split()[-1]
        except (OSError, IndexError):
            handler = ""
        pkg = handler.split("/")[0]
        if pkg and pkg in focus and ctx["changed"]:
            return "pass: the screen changed; Settings here is part of %s (%s)" % (pkg, handler)
        return "fail: focus is %s%s" % (focus.strip() or "(unknown)",
                                        ", screen unchanged" if ctx["changed"] is False else "")

    def expect_text_box(self, ctx):
        kb = self.last(ctx, "KeyboardFocused")
        dev = ctx["device"]
        ok = "mInputShown=true" in dev or kb == "true" or "search" in dev.splitlines()[0].lower() if dev else kb == "true"
        return ("pass" if ok else "fail") + ": KeyboardFocused=%s, %s" % (
            kb, (re.search(r"mInputShown=\w+", dev) or [""])[0] or "mInputShown not reported")

    def expect_on_screen(self, word, present=True):
        def check(ctx):
            seen = word in self.screen_text()
            ok = seen == present
            return ("pass" if ok else "fail") + ": '%s' %s on screen" % (word, "is" if seen else "isn't")
        return check

    def expect_awake(self, awake):
        def check(ctx):
            w = (re.search(r"mWakefulness=(\w+)", ctx["device"]) or [None, ""])[1]
            p = self.last(ctx, "POWER")
            ok = (w == "Awake") == awake if w else (p == ("ON" if awake else "OFF"))
            return ("pass" if ok else "fail") + ": mWakefulness=%s, POWER=%s" % (w or "?", p)
        return check

    def expect_playback(self, ctx):
        seq = [l.split("=", 1)[1] for l in ctx["states"] if l.startswith("PLAYBACK=")]
        if not seq and self.client.get("PLAYBACK") in ("STOPPED", "", None):
            return "can't tell: no video was playing (PLAYBACK=%s)" % self.client.get("PLAYBACK", "unreported")
        ok = "PAUSED" in seq and "PLAYING" in seq
        return ("pass" if ok else "fail") + ": PLAYBACK went %s" % (" > ".join(seq) or "unreported")

    def start_video(self):
        """Unattended: start a video without anyone holding the remote (YouTube only). The app is
        closed first: an open YouTube is just brought to the front and ignores the link."""
        if self.app_pkg in YOUTUBE:
            # Big Buck Bunny, the Blender Foundation's open film, on its official channel
            self.adb.sh(self.relay, "am force-stop %s; am start -a android.intent.action.VIEW "
                                    "-d https://www.youtube.com/watch?v=aqz-KE-bpKQ %s" % (self.app_pkg, self.app_pkg))
            time.sleep(25)

    def run_steps(self):
        S = self.step
        S("status", "Status", "nothing should change on the TV",
          [("POLL", "", 3), ("STATUS", "", 3)],
          keys=["ONLINE", "ADB", "POWER", "APP_NAME", "APP_PACKAGE", "PLAYBACK", "MODEL", "FIREOS", "LAST_ERROR"],
          auto=self.expect_state("ONLINE", "1"))
        S("home", "Home", "the home screen",
          [("HOME", "", 4)], "Did the home screen show?", keys=["APP_NAME", "APP_PACKAGE", "LAST_ERROR"],
          auto=self.expect_state("APP_NAME", "Home"))

        # Keys: Matchstick's way (its key helper), then plain Android input
        S("arrows_burst", "Down, Down, Up (Matchstick, sent together)", "the highlight moves down 2, then up 1",
          [("DOWN", "", 0.1), ("DOWN", "", 0.1), ("UP", "", 3)], "Did the highlight move down 2 and up 1?",
          changes=True)
        S("arrows_adb", "Down, Down, Up (plain Android input)", "the highlight moves down 2, then up 1",
          [self.adb_key(20, 20, 19, wait=3)], "Did the highlight move down 2 and up 1?", changes=True)
        S("right_left", "Right, then Left", "the highlight moves right, then back left",
          [("RIGHT", "", 2), ("LEFT", "", 2)], "Did it move right, then back left?", changes=True)
        S("select_back", "Select, then Back", "the highlighted item opens, then Back returns",
          [("SELECT", "", 4), ("BACK", "", 3)], "Did Select open it and Back return?")
        S("menu", "Menu", "an options menu, if this screen has one (often nothing)",
          [("MENU", "", 3)], "Did Menu do what the remote's menu button does here?", changes=True)
        S("menu_back", "Back", "the menu closes", [("BACK", "", 2)])

        # Apps: the list, launching by package, and by Matchstick's built-in names (made for Fire TV)
        S("listapps", "App list", "nothing on the TV; the app list is read",
          [("LISTAPPS", "", 6)], keys=["AvailableApps", "LAST_ERROR"],
          auto=lambda ctx: ("pass" if self.client.get("AvailableApps", "[]") not in ("", "[]") else "fail")
          + ": %d apps" % len(json.loads(self.client.get("AvailableApps", "[]") or "[]")))
        try:
            names = [a.get("name", "?") for a in json.loads(self.client.get("AvailableApps", "[]"))]
        except ValueError:
            names = []
        say("\n  Apps found (%d): %s" % (len(names), ", ".join(names[:30]) or "none"))
        if not self.unattended:
            self.results[-1]["answer"], self.results[-1]["note"] = self.ask(
                "Does that look like the apps installed on the device?")
        pkg, label = self.pick_app()
        self.app_pkg = pkg
        if pkg:
            S("launch_app", "Launch %s (by package)" % label, "%s opens" % label,
              [("LAUNCH", pkg, 8)], "Did %s open?" % label, keys=["APP_NAME", "APP_PACKAGE", "LAST_ERROR"],
              auto=self.expect_state("APP_PACKAGE", pkg))
            S("playback", "Play / pause in %s" % label, "the video pauses, then plays again",
              [("POLL", "", 3), ("PLAYPAUSE", "", 4), ("PLAYPAUSE", "", 4)],
              "Did it pause, then play again (and PLAYBACK follow: PLAYING, PAUSED, PLAYING)?",
              before="With the device's own remote, start playing a video in %s." % label,
              unattended_before=self.start_video,
              keys=["PLAYBACK", "APP_NAME", "LAST_ERROR"], auto=self.expect_playback)
        for alias in ("netflix", "youtube"):
            S("launch_" + alias, "Launch '%s' (Matchstick's built-in name)" % alias,
              "%s opens, or nothing if Matchstick's name points at the Fire TV version" % alias.title(),
              [("HOME", "", 3), ("LAUNCH", alias, 8)], "Did %s open?" % alias.title(),
              keys=["APP_NAME", "APP_PACKAGE", "LAST_ERROR"], auto=self.expect_focus(alias))
        S("home_again", "Home", "back to the home screen",
          [("HOME", "", 4)], "Back on the home screen?", keys=["APP_NAME", "PLAYBACK"],
          auto=self.expect_state("APP_NAME", "Home"))

        # Settings: Matchstick's launch and the Settings key
        S("launch_settings", "Launch Settings (Matchstick)", "the device's Settings opens",
          [("LAUNCH", "settings", 5)], "Did Settings open?", keys=["APP_NAME", "APP_PACKAGE", "LAST_ERROR"],
          auto=self.expect_settings, changes=True)
        S("settings_key", "Settings key", "Settings (or a settings panel) opens",
          [("HOME", "", 3), ("SETTINGS", "", 4)], "Did Settings open?", keys=["APP_NAME", "LAST_ERROR"],
          auto=self.expect_settings, changes=True)

        # Search: Matchstick's way (which picks a method by Android version, built for Fire OS)
        # and the plain Android Search key, every time. Typing then uses whichever worked,
        # Matchstick's first.
        bridge_search = S("search", "Search (Matchstick's way)",
                          "the search screen opens with a keyboard or text box",
                          [("HOME", "", 3), ("SEARCH", "", 6)], "Did a search screen with a text box open?",
                          keys=["KeyboardFocused", "APP_NAME", "APP_PACKAGE", "LAST_ERROR"],
                          auto=self.expect_text_box)
        key_search = S("search_key", "Search key (plain Android)", "the search screen opens",
                       [("HOME", "", 3), self.adb_key(84)], "Did a search screen with a text box open?",
                       keys=["KeyboardFocused", "APP_NAME", "APP_PACKAGE"], auto=self.expect_text_box)
        self.search_how = "bridge" if bridge_search == "yes" else "key" if key_search == "yes" else None
        if self.search_how:
            how = "Matchstick's way" if self.search_how == "bridge" else "Search key"
            reopen = [("HOME", "", 3)] + self.open_search()
            S("search_reopen", "Open search again for typing (%s)" % how,
              "the search screen with its text box", reopen)
            # Typing: Matchstick's TEXT, then plain Android input
            S("text", "Type text (Matchstick)", "'stargate' is typed into the search box",
              [("TEXT", "stargate", 4)], "Did 'stargate' appear in the box?",
              keys=["KeyboardFocused", "LAST_ERROR"], auto=self.expect_on_screen("stargate"))
            # Submitting: Matchstick's ENTER, then the plain Enter key
            S("enter", "Enter (Matchstick)", "search results for stargate",
              [("ENTER", "", 6)], "Did results for stargate show?",
              keys=["KeyboardFocused", "APP_NAME", "LAST_ERROR"], changes=True)
            S("text_adb", "Type text (plain Android input)", "search opens again and 'matrix' is typed",
              reopen + [self.adb_cmd("input text matrix", 4)], "Did 'matrix' appear in the box?",
              auto=self.expect_on_screen("matrix"))
            S("enter_adb", "Enter key (plain Android)", "search results for matrix",
              [self.adb_key(66, wait=6)], "Did results for matrix show?", changes=True)
            S("cleartext", "Clear text (Matchstick)", "search opens, 'star trek' is typed, then the box empties",
              reopen + [("TEXT", "star trek", 4), ("CLEARTEXT", "", 4)],
              "Did 'star trek' appear and then get cleared?", keys=["KeyboardFocused", "LAST_ERROR"],
              auto=self.expect_on_screen("star trek", present=False))
            S("sendtext", "Send text (what the Savant app sends)", "the box is replaced with 'dune' and submitted",
              [("SENDTEXT", "dune", 8)], "Did it search for dune?", keys=["KeyboardFocused", "LAST_ERROR"],
              auto=self.expect_on_screen("dune"))
        S("cleartext_safety", "Clear text with no box", "Home stays put; nothing is deleted",
          [("HOME", "", 3), ("CLEARTEXT", "", 4)], "Did the home screen stay as it was?",
          keys=["LAST_ERROR", "KeyboardFocused"],
          auto=lambda ctx: ("pass" if "no text box" in (self.last(ctx, "LAST_ERROR") or "") else "fail")
          + ": LAST_ERROR=%s" % self.last(ctx, "LAST_ERROR"))
        S("volume", "Volume up, down, mute, unmute", "the TV's volume changes and mutes (over HDMI-CEC)",
          [("VOLUP", "", 2), ("VOLDOWN", "", 2), ("MUTE", "", 2), ("MUTE", "", 2)],
          "Did the volume change and mute? (fine if the TV ignores it)",
          auto=lambda ctx: "can't tell: only the TV knows (HDMI-CEC)")

        # Power: Matchstick's sleep and wake keys, then the plain power key toggling
        S("power_off", "Power off (Matchstick)", "the device goes to sleep (the TV may turn off too)",
          [("POWEROFF", "", 10)], "Did the device go to sleep?", keys=["POWER", "ONLINE", "LAST_ERROR"],
          auto=self.expect_awake(False))
        S("power_on", "Power on (Matchstick)", "the device wakes up (the TV may turn on and switch input)",
          [("POWERON", "", 12)], "Did it wake up?", keys=["POWER", "ONLINE", "APP_NAME", "LAST_ERROR"],
          auto=self.expect_awake(True))
        S("power_toggle_off", "Power toggle, to sleep (Matchstick)", "the device goes to sleep",
          [("POWERTOGGLE", "", 10)], "Did the device go to sleep?", keys=["POWER", "ONLINE", "LAST_ERROR"],
          auto=self.expect_awake(False))
        S("power_toggle_on", "Power toggle, to wake (Matchstick)", "the device wakes up",
          [("POWERTOGGLE", "", 12)], "Did it wake up?", keys=["POWER", "ONLINE", "LAST_ERROR"],
          auto=self.expect_awake(True))
        S("final", "Final status", "nothing", [("HOME", "", 3), ("STATUS", "", 3)])

    # ---------------------------------------------------------------- finish
    def stop(self):
        if self.client:
            self.client.alive = False
        if self.bridge and self.bridge.poll() is None:
            self.bridge.send_signal(signal.SIGTERM)
            try:
                self.bridge.wait(20)
            except subprocess.TimeoutExpired:
                self.bridge.kill()
        # Remove what Matchstick copied to the device, then stop this kit's adb.
        ser = self.ip + ":5555"
        self.adb.run("connect", ser, timeout=15)
        if self.adb.state(ser) == "device":
            self.adb.sh(ser, "rm -f /data/local/tmp/matchstick-keys.dex /sdcard/matchstick.xml /sdcard/mc-ui.xml")
        self.adb.run("disconnect", ser)
        self.adb.run("kill-server")
        try:
            os.remove(self.relays_file)
        except (OSError, AttributeError):
            pass

    def write_report(self, started, reason):
        f = self.facts
        lines = ["# Matchstick device check", "",
                 "- Device: %s %s (%s), Android %s (SDK %s), build %s" % (
                     f.get("ro.product.manufacturer", "?"), f.get("ro.product.model", "?"),
                     f.get("ro.product.device", "?"), f.get("ro.build.version.release", "?"),
                     f.get("ro.build.version.sdk", "?"), f.get("ro.build.display.id", "?")),
                 "- Matchstick: %s" % (self.client.get("BRIDGE_VERSION", "?") if self.client else "not started"),
                 "- Computer: %s %s, Python %s" % (platform.system(), platform.release(), platform.python_version()),
                 "- Run: %s, %d min, %s" % (started.strftime("%Y-%m-%d %H:%M"),
                                           (datetime.now() - started).seconds // 60, reason), "",
                 "- Mode: %s" % ("unattended" if self.unattended else "attended, answers from the tester"), "",
                 "| # | Step | Tester | Automatic check | Note |", "|---|---|---|---|---|"]
        for r in self.results:
            lines.append("| %d | %s | %s | %s | %s |" % (r["n"], r["title"], r["answer"],
                                                      r.get("auto", "").replace("|", "/"),
                                                      r["note"].replace("|", "/")))
        for r in self.results:
            lines += ["", "## %d. %s" % (r["n"], r["title"]), "",
                      "- Watch for: %s" % r["watch"], "- Sent: %s" % ", ".join(r["sent"]),
                      "- Tester: %s%s" % (r["answer"], (" (%s)" % r["note"]) if r["note"] else ""),
                      "- Automatic check: %s" % (r.get("auto") or "none"),
                      "- Screenshot: %s" % (r["screenshot"] or "none (turned off)"), "", "Matchstick reported:", "```"]
            lines += [s if len(s) < 400 else s[:397] + "..." for s in r["states"]] or ["(nothing)"]
            lines += ["```", "", "Device at the end of the step:", "```", r["device"] or "(not read)", "```"]
        with open(os.path.join(self.work, "report.md"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(os.path.join(self.work, "results.json"), "w") as fh:
            json.dump({"facts": f, "results": self.results}, fh, indent=1)

    def package(self):
        model = "".join(c if c.isalnum() else "-" for c in self.facts.get("ro.product.model", "device")).strip("-")
        out = os.path.join(KIT, "matchstick-check-%s-%s.tar.gz" % (model, os.path.basename(self.work)[7:]))
        with tarfile.open(out, "w:gz") as t:
            t.add(self.work, arcname=os.path.basename(self.work))
        return out


def main():
    ap = argparse.ArgumentParser(description="Try Matchstick on an Android TV device and collect a report.")
    ap.add_argument("ip", help="the device's IP address")
    ap.add_argument("--no-screenshots", action="store_true", help="don't save a screenshot after each step")
    ap.add_argument("--unattended", action="store_true",
                    help="don't ask after each step; judge each one from what the device reports")
    args = ap.parse_args()
    if sys.version_info < (3, 7):
        sys.exit("Needs Python 3.7 or newer.")
    if not sys.stdin.isatty():
        sys.exit("Run this in a terminal: it needs you to start it and approve the TV's prompt.")

    check = Check(args)
    heading("Matchstick device check")
    if args.unattended:
        say("Unattended: after you approve the prompt on the TV, it runs by itself (about 20 minutes)\n"
            "and judges each step from what the device reports. Leave the TV on and showing the device.")
    else:
        say("This takes about 20 minutes in front of the TV. After each step it asks whether the TV\n"
            "did what it says.")
    say("Ctrl-C stops at any time; the report so far is still saved.")
    if check.screens:
        say("\nScreenshots: it saves a screenshot of the device's screen after each step, so whatever\n"
            "the TV shows (home screen, apps, search) ends up in the report. They're saved in\n"
            "  %s/steps/\n"
            "so you can look at them, and delete any, before sending the report back.\n"
            "To take none, stop now (Ctrl-C) and run again with --no-screenshots." % check.work)
    input("\nTurn on the TV and the device, show its home screen, then press Enter... ")

    check.adb = Adb(find_adb())
    started, reason = datetime.now(), "finished"
    try:
        ser = check.pair()
        check.collect(ser)
        check.adb.run("disconnect", ser)
        check.start_bridge(find_bridge())
        check.run_steps()
    except KeyboardInterrupt:
        reason = "stopped early (Ctrl-C)"
        say("\nStopping...")
    except SystemExit as e:
        reason = "stopped: %s" % e
        say(str(e))
    finally:
        check.stop()
        check.write_report(started, reason)
        out = check.package()
    heading("Done")
    say("Report:      %s/report.md" % check.work)
    if check.screens:
        say("Screenshots: %s/steps/" % check.work)
    say("\nSend this file back: %s\n" % out)
    if check.screens:
        say("Before sending it, look through the screenshots in the steps folder above. To leave\n"
            "any out, delete them there, then rebuild the file to send with:\n"
            "  tar -czf \"%s\" -C \"%s\" \"%s\"\n" % (out, KIT, os.path.basename(check.work)))
    say("When you're finished testing, turn Network debugging off in Developer options.")


if __name__ == "__main__":
    main()
