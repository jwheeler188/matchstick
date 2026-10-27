#!/usr/bin/env python3
"""
Matchstick
==========
Fire TV bridge for Savant: lets a Savant system control Amazon Fire TV devices (Fire OS) over IP.

Savant  --TCP 2360-->  this bridge  --ADB (TCP 5555)-->  Fire TV

Savant profiles can't speak ADB (it needs RSA keys and an on-screen approval),
so this service holds the ADB connection and exposes a plain-text line protocol.

Protocol (one command per line; CR, LF, CRLF or a short pause all end a line):

    <COMMAND> <fire-tv-ip> [argument]

    Navigation   UP DOWN LEFT RIGHT SELECT BACK HOME MENU
    Transport    PLAYPAUSE PLAY PAUSE STOP FF REW NEXT PREV
    Volume       VOLUP VOLDOWN MUTE        (sent to the TV/AVR over HDMI-CEC)
    Power        POWERON POWEROFF POWERTOGGLE
    Other        SEARCH SETTINGS
                 ENTER <ip>                submit typed text (send after TEXT); on the Fire TV
                                           search screen this opens results for the top suggestion
                 HOLD <ip> <KEY>           long-press a key (e.g. HOLD ... HOME)
                 LIVETV <ip>               open Live TV (config live_tv). Two HOMEs within
                                           home_double_press_seconds also do this, so the
                                           Savant app's TV button double-pressed opens Live TV
                 HOME <ip> [double]        Home; [double] overrides what a quick second press
                                           does: livetv (default), off, an app, or key:<code>
                 LAUNCH <ip> <app>         alias from config (netflix) or package name
                 TEXT <ip> <text>          type into the focused search box
                 POLL <ip>                 refresh + subscribe this connection to status
                 STATUS <ip>               resend every status value
    Keyboard     SENDTEXT <ip> <text>      replace the text box contents and submit (TEXT + ENTER)
                 CLEARTEXT <ip>            empty the focused text box
    Pairing      PAIRSTART <ip>            show 'Allow USB debugging?' on the TV and wait for it
                 PAIRPIN <ip> <pin>        Fire OS has no PIN; re-checks the approval
                 UNPAIR <ip>               ignored (logged): the Fire TV stays connected. The Savant
                                           app's Unpair is easy to tap by mistake; to really
                                           disconnect, remove the Fire TV from Savant
                 DISCOVER <ip>             check the Fire TV is reachable now
                 LISTAPPS <ip>             publish AvailableApps
    Bridge       PING                      -> PONG

Status lines pushed back to every connection that has sent a command for that IP:

    ONLINE=1|0  ADB=CONNECTED|UNAUTHORIZED|OFFLINE  POWER=ON|OFF
    APP_PACKAGE=com.netflix.ninja  APP_NAME=Netflix  PLAYBACK=PLAYING|PAUSED|STOPPED
    MODEL=...  FIREOS=...  LAST_ERROR=...  BRIDGE_VERSION=...

Plus the states Savant's own Apple TV service (atv-bridge) publishes, with the same names
and values, so the Savant Pro app's keyboard and pairing pop-ups can drive a Fire TV:

    KeyboardFocused=true|false  Discovered=true|false
    PairingState=unpaired|awaiting_pin|paired  PairingError=...
    AvailableApps=[{"name": "Netflix", "identifier": "com.netflix.ninja"}, ...]

Python 3.6+ standard library only (Savant Mac mini host ships Python 3.9).
Requires Android platform-tools `adb` (the installer puts it next to this file).
"""
import asyncio
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import resource
import shlex
import shutil
import signal
import socket
import struct
import sys
import time

VERSION = "1.4.1"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
RELAYS_FILE = os.path.join(HERE, "relays.json")   # Fire TV IP -> relay serial and recent ports

DEFAULTS = {
    "listen_host": "0.0.0.0",
    "listen_port": 2360,
    "adb_path": "",                 # blank = ./platform-tools/adb, then PATH
    "adb_port": 5555,
    "poll_seconds": 5,              # status refresh while a Savant component is connected
    "idle_disconnect_minutes": 30,  # drop ADB to a Fire TV nobody has polled in this long
    "stall_hold_seconds": 600,      # a Fire TV that stops answering (asleep, Wi-Fi nap) keeps its
                                    # connection this long before Matchstick gives up on it
    "relay_base_port": 20000,       # local ports for the per-Fire TV ADB relays (see Relay)    "allowed_devices": [],          # empty = any private (LAN) address
    "allowed_clients": [],          # empty = anyone who can reach the port
    "memory_limit_mb": 150,         # watchdog: exit (launchd restarts us) above this
    "log_file": os.path.join(HERE, "logs", "firetv_bridge.log"),
    "log_level": "INFO",
    "app_aliases": {},
    "live_tv": "key:172",           # LIVETV (172 = Guide: the Live TV channel guide): "key:<android keycode>" or an app/package for LAUNCH
    "home_double_press_seconds": 0.6,  # 0 turns off HOME double-press = LIVETV
}

# Android keycodes. Names are the bridge command names.
KEYS = {
    "UP": 19, "DOWN": 20, "LEFT": 21, "RIGHT": 22, "SELECT": 23,
    "BACK": 4, "HOME": 3, "MENU": 82,
    "PLAYPAUSE": 85, "PLAY": 126, "PAUSE": 127, "STOP": 86,
    "FF": 90, "REW": 89, "NEXT": 87, "PREV": 88,
    "VOLUP": 24, "VOLDOWN": 25, "MUTE": 164,
    "POWERON": 224, "POWEROFF": 223, "POWERTOGGLE": 26,
    "SEARCH": 84, "SETTINGS": 176,
}
# The Fire TV search keyboard has no submit key (Enter just presses the highlighted letter),
# so ENTER there reads the screen and taps the top suggestion, which opens the results page.
# Suggestions load a moment after typing, so it looks twice. Elsewhere ENTER is the Enter key.
ENTER_SCRIPT = (
    "if dumpsys window windows | grep -m1 mCurrentFocus | grep -q TextSearch; then "
    "for t in 1 2; do uiautomator dump /sdcard/matchstick.xml >/dev/null 2>&1; "
    "b=$(grep -o 'resource-id=\"com.amazon.tv.launcher:id/title\"[^>]*bounds=\"[^\"]*\"' /sdcard/matchstick.xml"
    " | head -n1 | sed 's/.*bounds=\"\\[\\([0-9]*\\),\\([0-9]*\\)\\]\\[\\([0-9]*\\),\\([0-9]*\\)\\]\"/\\1 \\2 \\3 \\4/'); "
    "rm -f /sdcard/matchstick.xml; [ -n \"$b\" ] && break; sleep 1; done; "
    "if [ -n \"$b\" ]; then set -- $b; input tap $((($1+$3)/2)) $((($2+$4)/2)); else echo NOSUGGEST; fi; "
    "else input keyevent 66; fi"
)
# A text box is up when the Fire TV search screen is open (its keyboard is part of the home
# screen app) or when an app has the on-screen keyboard showing.
KEYBOARD_TEST = ("{ dumpsys window windows | grep -m1 mCurrentFocus | grep -q TextSearch || "
                 "dumpsys input_method 2>/dev/null | grep -q 'mInputShown=true'; }")
# Empty the text box: jump to the end, then delete. Only when a text box is up, because
# Delete on other screens can act like Back.
CLEAR_SCRIPT = ("if %s; then input keyevent 123 %s; else echo NOKEYBOARD; fi"
                % (KEYBOARD_TEST, " ".join(["67"] * 40)))
# Apps that show on the Fire TV home screen, for AvailableApps (package names, and the
# app's name where the app gives it as text rather than a resource).
APPS_SCRIPT = ("cmd package query-activities -a android.intent.action.MAIN "
               "-c android.intent.category.LEANBACK_LAUNCHER 2>/dev/null | grep -E 'packageName=|nonLocalizedLabel='")
# Keys that change power state: poll right after so POWER updates quickly.
POWER_KEYS = {"POWERON", "POWEROFF", "POWERTOGGLE"}
# Key helper (keyhelper/Keys.java, shipped as matchstick-keys.dex next to this file): a small
# program kept running on the Fire TV that injects keys like `input keyevent` without starting
# Java for every press (about 0.05 s a press instead of 0.3-1.2 s). Used for every key while it
# runs; if it can't start or stops answering, the slower methods below take over.
HELPER_DEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchstick-keys.dex")
HELPER_REMOTE = "/data/local/tmp/matchstick-keys.dex"
HELPER_CMD = "CLASSPATH=%s exec app_process /system/bin com.matchstick.Keys" % HELPER_REMOTE
QUIET_COMMANDS = {"PING", "POLL", "VERSION", "REFRESH"}   # not logged at INFO
# Fast keys: written straight to the remote's input device, so the Fire TV sees a real button
# press. `input keyevent` starts a Java program per call (about 0.3 s on an idle Fire OS 6
# stick, over a second on a busy one). The fastest way is the shell's built-in `print` writing
# the raw input events (no program started, under 0.1 s); `sendevent` is the fallback (one small
# program per event, about 0.35 s a press on a busy stick), then `input keyevent`. The scan codes
# come from the stick's own key layout file; keys it can't map, and volume/power (HDMI-CEC),
# always use `input keyevent`.
FAST_KEYS = {
    "UP": "DPAD_UP", "DOWN": "DPAD_DOWN", "LEFT": "DPAD_LEFT", "RIGHT": "DPAD_RIGHT",
    "SELECT": "DPAD_CENTER", "BACK": "BACK", "HOME": "HOME", "MENU": "MENU",
    "PLAYPAUSE": "MEDIA_PLAY_PAUSE", "PLAY": "MEDIA_PLAY", "PAUSE": "MEDIA_PAUSE",
    "STOP": "MEDIA_STOP", "FF": "MEDIA_FAST_FORWARD", "REW": "MEDIA_REWIND",
    "NEXT": "MEDIA_NEXT", "PREV": "MEDIA_PREVIOUS",
}
# Lists each input device with its key codes, vendor and product (parse_input_devices).
INPUT_DEVICES_SCRIPT = "for d in /dev/input/event*; do echo \"@@DEV $d\"; getevent -i $d 2>/dev/null; done"
KEYLAYOUT_SCRIPT = ("for f in /vendor/usr/keylayout/%(n)s.kl /system/usr/keylayout/%(n)s.kl "
                    "/system/usr/keylayout/Generic.kl; do [ -f $f ] && { cat $f; break; }; done")

# Fire TV package names. Verify on a given unit with: firetvctl apps <ip>
# Anything here can be overridden or extended in config.json "app_aliases".
BUILTIN_APPS = {
    "netflix": "com.netflix.ninja",
    "primevideo": "com.amazon.firebat",
    "youtube": "com.amazon.firetv.youtube",
    "youtubetv": "com.amazon.firetv.youtube.tv",
    "disneyplus": "com.disney.disneyplus",
    "hulu": "com.hulu.plus",
    "max": "com.wbd.stream",
    "appletv": "com.apple.atve.amazon.appletv",
    "paramountplus": "com.cbs.ott",
    "peacock": "com.peacocktv.peacockandroid",
    "plex": "com.plexapp.android",
    "spotify": "com.spotify.tv.android",
    "pluto": "tv.pluto.android",
    "sling": "com.sling",
    "kodi": "org.xbmc.kodi",
    # Fire TV Settings has no launcher entry (it lives inside the home screen app),
    # so open it with the standard Android intent. "action:<name>" = `am start -a <name>`.
    "settings": "action:android.settings.SETTINGS",
}
# Friendly names for APP_NAME (reverse of the aliases, plus the Fire TV home screen).
FRIENDLY = {
    "com.netflix.ninja": "Netflix", "com.amazon.firebat": "Prime Video",
    "com.amazon.firetv.youtube": "YouTube", "com.amazon.firetv.youtube.tv": "YouTube TV",
    "com.disney.disneyplus": "Disney+", "com.hulu.plus": "Hulu", "com.wbd.stream": "Max",
    "com.apple.atve.amazon.appletv": "Apple TV", "com.cbs.ott": "Paramount+",
    "com.peacocktv.peacockandroid": "Peacock", "com.plexapp.android": "Plex",
    "com.spotify.tv.android": "Spotify", "tv.pluto.android": "Pluto TV", "com.sling": "Sling TV",
    "org.xbmc.kodi": "Kodi", "com.amazon.tv.settings.v2": "Settings",
    "com.peacock.peacockfiretv": "Peacock", "com.amazon.bueller.music": "Amazon Music",
    "com.amazon.bueller.photos": "Amazon Photos",
    "action:android.settings.SETTINGS": "Settings",
    "com.amazon.tv.launcher": "Home", "com.amazon.firebat.home": "Home",
}

STATUS_SCRIPT = (
    "dumpsys power | grep -m1 mWakefulness=; "
    "dumpsys window windows | grep -m1 -E 'mCurrentFocus|mFocusedApp'; "
    "dumpsys media_session | grep -m1 -E 'state=PlaybackState|state=[0-9]'; "
    "dumpsys input_method 2>/dev/null | grep -m1 -o 'mInputShown=[a-z]*'"
)

log = logging.getLogger("firetv")


# --------------------------------------------------------------------------- config

def load_config(path):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    aliases = dict(BUILTIN_APPS)
    aliases.update({norm_alias(k): v for k, v in (cfg.get("app_aliases") or {}).items()})
    cfg["app_aliases"] = aliases
    return cfg


def norm_alias(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("+", "plus"))


def find_adb(cfg):
    candidates = [cfg.get("adb_path") or "", os.path.join(HERE, "platform-tools", "adb"),
                  shutil.which("adb") or ""]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


# --------------------------------------------------------------------------- parsing

def parse_status(text):
    """Turn the STATUS_SCRIPT output into {POWER, APP_PACKAGE, PLAYBACK, KEYBOARD}."""
    out = {}
    m = re.search(r"mWakefulness=(\w+)", text)
    if m:
        out["POWER"] = "ON" if m.group(1).lower() == "awake" else "OFF"
    m = re.search(r"(?:mCurrentFocus|mFocusedApp)=\S*\{[^}]*?\s([A-Za-z0-9_.]+)/", text)
    if m:
        out["APP_PACKAGE"] = m.group(1)
    m = re.search(r"state=PlaybackState \{state=(\d+)", text) or re.search(r"\bstate=(\d+)", text)
    if m:
        out["PLAYBACK"] = {"3": "PLAYING", "2": "PAUSED", "6": "PLAYING", "4": "PLAYING",
                           "5": "PLAYING"}.get(m.group(1), "STOPPED")
    else:
        out["PLAYBACK"] = "STOPPED"
    out["KEYBOARD"] = "TextSearch" in text or "mInputShown=true" in text
    return out


def friendly_name(pkg):
    if not pkg:
        return ""
    if pkg in FRIENDLY:
        return FRIENDLY[pkg]
    if "launcher" in pkg or pkg.endswith(".home"):
        return "Home"
    # Last meaningful part: com.haystack.android -> Haystack, not Android.
    words = [w for w in pkg.split(".") if w.lower() not in GENERIC_PKG_WORDS] or pkg.split(".")
    return words[-1].replace("_", " ").title()


GENERIC_PKG_WORDS = {"com", "net", "org", "tv", "dev", "android", "androidtv", "firetv", "fire",
                     "amazon", "client", "app", "apps", "ott", "main", "mobile", "aftv", "player"}


def parse_input_devices(text):
    """getevent -i output per device -> [{path, name, vendor, product, keys:set(int)}]."""
    devs, cur, in_keys = [], None, False
    for line in text.splitlines():
        if line.startswith("@@DEV "):
            cur = {"path": line[6:].strip(), "name": "", "vendor": "", "product": "", "keys": set()}
            devs.append(cur)
            in_keys = False
            continue
        if cur is None:
            continue
        m = re.search(r'name:\s+"([^"]*)"', line)
        if m:
            cur["name"] = m.group(1)
        for k in ("vendor", "product"):
            m = re.search(r"\b%s\s+([0-9a-fA-F]{4})\b" % k, line)
            if m:
                cur[k] = m.group(1).lower()
        if "KEY (0001):" in line:
            in_keys, line = True, line.split(":", 1)[1]
        elif "(" in line or ":" in line:
            in_keys = False
        if in_keys:
            cur["keys"].update(int(t, 16) for t in line.split() if re.fullmatch(r"[0-9a-fA-F]{4}", t))
    return devs


def pick_remote(devs):
    """The input device to send fast keys to: one with arrow keys (Linux 103 up, 108 down),
    preferring a remote by name, then the most keys."""
    ok = [d for d in devs if {103, 108} <= d["keys"]]
    ok.sort(key=lambda d: ("remote" in d["name"].lower(), len(d["keys"])), reverse=True)
    return ok[0] if ok else None


def parse_keylayout(text, have):
    """Android .kl text -> {android key name: scan code}, using codes the device has."""
    out = {}
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 3 and parts[0] == "key" and parts[1].isdigit():
            code, name = int(parts[1]), parts[2]
            if code in have and name not in out:
                out[name] = code
    return out


def parse_apps(text):
    """APPS_SCRIPT output -> [{"name", "identifier"}] sorted by name."""
    labels, pkg = {}, None
    for line in text.splitlines():
        m = re.search(r"packageName=(\S+)", line)
        if m:
            pkg = m.group(1)
            labels.setdefault(pkg, "")
        m = re.search(r"nonLocalizedLabel=(.+?)\s+icon=", line)
        if m and pkg and m.group(1) != "null" and not labels.get(pkg):
            labels[pkg] = m.group(1).strip()
    apps = [{"name": FRIENDLY.get(p) or l or friendly_name(p), "identifier": p} for p, l in labels.items()]
    return sorted(apps, key=lambda a: a["name"].lower())


def text_for_input(text):
    """Escape text for `input text`: spaces become %s, then shell-quote the rest."""
    text = text.replace("%", "\\%").replace(" ", "%s")
    return shlex.quote(text)


# --------------------------------------------------------------------------- ADB

class ADB:
    """Thin async wrapper around the adb client binary."""

    def __init__(self, path):
        self.path = path

    async def run(self, *args, timeout=10):
        proc = await asyncio.create_subprocess_exec(
            self.path, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "timeout"
        return proc.returncode, out.decode(errors="ignore").strip()

    async def device_state(self, serial):
        rc, out = await self.run("devices")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == serial:
                return parts[1]          # device | unauthorized | offline
        return "missing"


# --------------------------------------------------------------------------- ADB relay

TCP_RXT_CONNDROPTIME = 0x80     # macOS: seconds of unanswered retransmits before TCP gives up
TCP_USER_TIMEOUT = 18           # the Linux equivalent, in milliseconds


def load_relays():
    try:
        with open(RELAYS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_relays(data):
    try:
        tmp = RELAYS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, RELAYS_FILE)
    except OSError as e:
        log.debug("relays file not saved: %s", e)


class Relay:
    """Holds the TCP connection to one Fire TV's adbd on adb's behalf.

    adb gives up on a network device after about 10 s without an answer (its own 1-second
    keepalive), but the Fire TV never hears about it, and a Fire TV's adbd takes one connection
    at a time: it resets every new one, so the Fire TV stays OFFLINE until ADB debugging is
    toggled. So adb connects to this relay on 127.0.0.1, where nothing stalls, and the relay
    holds the Fire TV connection through a nap for up to stall_hold_seconds.

    If the Fire TV still ends up holding a connection this host has dropped, the relay
    reconnects from that connection's local port: the Fire TV answers the clashing SYN with an
    ACK, this host answers that with a reset, and the Fire TV drops the stale connection
    (RFC 5961). Recent ports are kept in relays.json so this works after a restart too."""

    def __init__(self, ip, cfg):
        self.ip = ip
        self.target = (ip, cfg["adb_port"])
        self.hold = int(cfg["stall_hold_seconds"])
        o = [int(x) for x in ip.split(".")]
        self.port = cfg["relay_base_port"] + (o[2] % 64) * 256 + o[3]
        self.recent = list(load_relays().get(ip, {}).get("ports", []))[-4:]
        self.suspect = False            # the Fire TV reset a new connection straight away
        self.last_clear = 0.0
        self.server = None

    @property
    def serial(self):
        return "127.0.0.1:%d" % self.port

    async def start(self):
        try:
            self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.port)
        except OSError:
            self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
        log.info("%s ADB relay on %s", self.ip, self.serial)
        self.save()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    def save(self):
        data = load_relays()
        data[self.ip] = {"serial": self.serial, "ports": self.recent}
        save_relays(data)

    def remember(self, port):
        if port in self.recent:
            self.recent.remove(port)
        self.recent = (self.recent + [port])[-4:]
        self.save()

    def new_socket(self, port=0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if port and hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:                            # keep retransmitting through a nap instead of giving up
            if sys.platform == "darwin":
                s.setsockopt(socket.IPPROTO_TCP, TCP_RXT_CONNDROPTIME, self.hold)
            else:
                s.setsockopt(socket.IPPROTO_TCP, TCP_USER_TIMEOUT, self.hold * 1000)
        except OSError:
            pass
        if port:
            s.bind(("", port))
        s.setblocking(False)
        return s

    async def clear_stale(self):
        """Reconnect from each recent local port so the Fire TV drops a connection we've lost."""
        self.last_clear = time.monotonic()
        loop = asyncio.get_event_loop()
        for port in list(self.recent):
            try:
                s = self.new_socket(port)
            except OSError as e:
                log.info("%s can't reuse local port %d (%s)", self.ip, port, e)
                continue
            try:
                await asyncio.wait_for(loop.sock_connect(s, self.target), 2)
                outcome = "connected"
            except asyncio.TimeoutError:
                outcome = "no answer"
            except OSError as e:
                outcome = e.__class__.__name__
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.close()                   # abort: a reset, not a close handshake
            log.info("%s cleared old ADB connection from port %d (%s)", self.ip, port, outcome)

    async def handle(self, down_r, down_w):
        """adb connected to the relay: open the Fire TV connection and pass bytes both ways."""
        if self.suspect and time.monotonic() - self.last_clear > 20:
            self.suspect = False
            await self.clear_stale()
        loop = asyncio.get_event_loop()
        s = self.new_socket()
        try:
            await asyncio.wait_for(loop.sock_connect(s, self.target), 8)
        except (OSError, asyncio.TimeoutError) as e:
            s.close()
            down_w.close()
            log.debug("%s relay: can't reach the Fire TV (%s)", self.ip, e.__class__.__name__)
            return
        lport = s.getsockname()[1]
        up_r, up_w = await asyncio.open_connection(sock=s)
        started, got = time.monotonic(), 0

        async def pump(r, w, from_tv):
            nonlocal got
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        return "closed"
                    if from_tv:
                        if not got:
                            self.remember(lport)    # a working connection: worth clearing later
                        got += len(data)
                    w.write(data)
                    await w.drain()
            except (ConnectionError, OSError) as e:
                return e.__class__.__name__

        to_tv = asyncio.ensure_future(pump(down_r, up_w, False))
        from_tv = asyncio.ensure_future(pump(up_r, down_w, True))
        done, pending = await asyncio.wait({to_tv, from_tv}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if from_tv in done and not got and time.monotonic() - started < 3:
            self.suspect = True
            log.warning("%s Fire TV refused a new ADB connection (%s): it may be holding an old one",
                        self.ip, from_tv.result())
        else:
            log.info("%s ADB relay connection from port %d ended by the %s (%s) after %ds", self.ip,
                     lport, "Fire TV" if from_tv in done else "host",
                     (from_tv if from_tv in done else to_tv).result(), time.monotonic() - started)
        for w in (up_w, down_w):
            try:
                w.close()
            except Exception:
                pass


class FireTV:
    """One Fire TV: keeps a persistent `adb shell` open and serialises commands."""

    def __init__(self, bridge, ip):
        self.bridge = bridge
        self.ip = ip
        self.relay = Relay(ip, bridge.cfg)
        self.direct_serial = "%s:%d" % (ip, bridge.cfg["adb_port"])   # adb's own name before 1.4.1
        self.stalled_since = None       # when the Fire TV stopped answering the shell
        self.adb = bridge.adb
        self.queue = asyncio.Queue()
        self.shell = None
        self.marker_n = 0
        self.state = {}
        self.subscribers = set()
        self.last_wanted = time.monotonic()
        self.last_connect_try = 0.0
        self.info_fetched = False
        self.pair_task = None
        # The command worker, poller, refreshes and pairing all share one adb shell:
        # one connect attempt and one shell command at a time.
        self.connect_lock = asyncio.Lock()
        self.keydev = None              # remote input device path for fast keys
        self.scancodes = {}             # bridge key name -> Linux scan code
        self.key_method = None          # "print", "sendevent" or None (input keyevent)
        self.helper = None              # key helper process (HELPER_CMD) while it runs
        self.last_home = 0.0            # time of the last HOME, for double-press = LIVETV
        self.helper_lock = asyncio.Lock()
        self.event_fmt = "<llHHi"       # struct input_event for the stick's word size
        self.shell_lock = asyncio.Lock()
        self.worker = asyncio.ensure_future(self.run())
        self.poller = asyncio.ensure_future(self.poll_loop())

    @property
    def serial(self):
        """adb's name for this Fire TV: its relay on 127.0.0.1 (see Relay)."""
        return self.relay.serial

    # ---- state ---------------------------------------------------------
    def set(self, key, value):
        value = "" if value is None else str(value)
        if self.state.get(key) != value:
            self.state[key] = value
            self.bridge.publish(self.ip, key, value)

    def resend(self, writer):
        for k, v in sorted(self.state.items()):
            self.bridge.send(writer, "%s=%s" % (k, v))

    def set_pairing(self, st):
        """Publish atv-bridge style pairing states from an adb device state."""
        if st == "device":
            self.set("Discovered", "true")
            self.set("PairingState", "paired")
            self.set("PairingError", "")
        elif st == "unauthorized":
            self.set("Discovered", "true")
            self.set("PairingState", "awaiting_pin")
            self.set("PairingError", "Choose Allow on the TV (tick Always allow from this computer)")
        else:                           # unreachable: pairing unknown, leave it as it was
            self.set("Discovered", "false")

    # ---- connection ----------------------------------------------------
    async def ensure_connected(self, force=False):
        if self.shell and self.shell.returncode is None:
            return True
        async with self.connect_lock:
            return await self._connect(force)

    async def _connect(self, force):
        if self.shell and self.shell.returncode is None:
            return True             # another caller connected while we waited
        now = time.monotonic()
        if not force and now - self.last_connect_try < 5:
            return False
        self.last_connect_try = now
        await self.close_shell()
        if not self.relay.server:
            # A direct connection (older versions, firetvctl pair) would take the Fire TV's only
            # ADB slot; close it cleanly before the relay connects.
            await self.adb.run("disconnect", self.direct_serial, timeout=5)
            await self.relay.start()
        rc, out = await self.adb.run("connect", self.serial, timeout=8)
        log.debug("%s adb connect: %s", self.ip, out)
        st = await self.adb.device_state(self.serial)
        self.set_pairing(st)
        if st != "device":
            self.set("ONLINE", "1" if st == "unauthorized" else "0")
            self.set("ADB", "UNAUTHORIZED" if st == "unauthorized" else "OFFLINE")
            if st == "unauthorized":
                self.set("LAST_ERROR", "Accept 'Allow USB debugging' on the TV (tick Always allow)")
            elif "refused" in out.lower():
                self.set("LAST_ERROR", "Connection refused - turn on ADB debugging in Developer Options")
            elif self.relay.suspect or time.monotonic() - self.relay.last_clear < 60:
                self.set("LAST_ERROR", "Fire TV is holding an old ADB connection - clearing it; "
                         "if it stays offline, turn ADB debugging off and on")
            elif out:
                self.set("LAST_ERROR", out.splitlines()[-1][:120])
            # Leave an unauthorized connection open so the approval prompt stays on screen;
            # clear dead ones so the next try starts fresh.
            if st in ("offline", "missing"):
                await self.adb.run("disconnect", self.serial, timeout=5)
            return False
        self.shell = await asyncio.create_subprocess_exec(
            self.adb.path, "-s", self.serial, "shell",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        self.set("ONLINE", "1")
        self.set("ADB", "CONNECTED")
        self.set("LAST_ERROR", "")
        log.info("%s connected", self.ip)
        if not self.info_fetched:
            info = await self.sh("getprop ro.product.model; getprop ro.build.version.name")
            lines = [l.strip() for l in (info or "").splitlines() if l.strip()]
            if lines:
                self.set("MODEL", lines[0])
                self.set("FIREOS", lines[1] if len(lines) > 1 else "")
                self.info_fetched = True
        await self.find_remote()
        await self.start_helper()
        return True

    async def start_helper(self):
        """Copy the key helper to the Fire TV if needed and start it."""
        async with self.helper_lock:
            if self.helper and self.helper.returncode is None:
                return
            if self.stalled_since or not os.path.exists(HELPER_DEX):
                return
            size = os.path.getsize(HELPER_DEX)
            have = await self.sh("stat -c %%s %s 2>/dev/null" % HELPER_REMOTE) or ""
            if have.strip() != str(size):
                rc, out = await self.adb.run("-s", self.serial, "push", HELPER_DEX, HELPER_REMOTE, timeout=20)
                if rc != 0:
                    log.warning("%s key helper copy failed: %s", self.ip, out[-120:])
                    return
            proc = await asyncio.create_subprocess_exec(
                self.adb.path, "-s", self.serial, "shell", HELPER_CMD,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            try:
                first = await asyncio.wait_for(proc.stdout.readline(), 10)
            except asyncio.TimeoutError:
                first = b""
            if first.strip() == b"ready":
                self.helper = proc
                log.info("%s key helper running", self.ip)
            else:
                proc.kill()
                log.warning("%s key helper did not start: %s", self.ip, first.decode(errors="ignore").strip()[:120])

    async def stop_helper(self):
        if self.helper and self.helper.returncode is None:
            self.helper.kill()
            await self.helper.wait()
        self.helper = None

    async def helper_keys(self, line):
        """Send one request to the key helper; True when it pressed the keys."""
        if not self.helper or self.helper.returncode is not None or self.stalled_since:
            return False                # during a stall, keys are dropped rather than replayed later
        async with self.helper_lock:
            try:
                self.helper.stdin.write((line + "\n").encode())
                await self.helper.stdin.drain()
                ans = await asyncio.wait_for(self.helper.stdout.readline(), 5)
            except (asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError):
                ans = b""
        if ans.strip() == b"ok":
            return True
        log.warning("%s key helper stopped answering (%s); restarting it", self.ip, ans.strip()[:60])
        await self.stop_helper()
        self.bridge.later(2.0, self.start_helper)
        return False

    async def find_remote(self):
        """Find the remote's input device and its scan codes for fast keys (FAST_KEYS)."""
        self.keydev, self.scancodes, self.key_method = None, {}, None
        dev = pick_remote(parse_input_devices(await self.sh(INPUT_DEVICES_SCRIPT, timeout=10) or ""))
        if not dev:
            log.info("%s no remote input device (only matters if the key helper stops)", self.ip)
            return
        name = "Vendor_%s_Product_%s" % (dev["vendor"], dev["product"])
        layout = parse_keylayout(await self.sh(KEYLAYOUT_SCRIPT % {"n": name}) or "", dev["keys"])
        self.scancodes = {k: layout[a] for k, a in FAST_KEYS.items() if a in layout}
        if self.scancodes:
            self.keydev = dev["path"]
            # A raw input event holds a timeval, so its size follows the shell's word size.
            env = await self.sh("getprop ro.product.cpu.abi; type print") or ""
            self.event_fmt = "<qqHHi" if "64" in env.split("\n", 1)[0] else "<llHHi"
            self.key_method = "print" if "builtin" in env else "sendevent"
        log.info("%s fast keys via %s (%s, %s): %s", self.ip, dev["path"], dev["name"],
                 self.key_method, " ".join(sorted(self.scancodes)) or "none")

    def key_command(self, names):
        """Shell command pressing these keys on the remote device with the current method."""
        d = self.keydev
        if self.key_method == "print":
            ev = lambda t, c, v: struct.pack(self.event_fmt, 0, 0, t, c, v)
            raw = b"".join(ev(1, self.scancodes[n], 1) + ev(0, 0, 0) + ev(1, self.scancodes[n], 0)
                           + ev(0, 0, 0) for n in names)
            return 'print -n "%s" > %s' % ("".join("\\0%03o" % b for b in raw), d)
        return "; ".join("sendevent %s 1 %d 1; sendevent %s 0 0 0; sendevent %s 1 %d 0; sendevent %s 0 0 0"
                         % (d, self.scancodes[n], d, d, self.scancodes[n], d) for n in names)

    async def send_keys(self, names):
        """Press keys in order on the remote device when all are mapped, else one input keyevent.
        A failed method steps down (print, then sendevent) and the device is looked up again."""
        if await self.helper_keys("k " + " ".join(str(KEYS[n]) for n in names)):
            return
        while self.key_method and all(n in self.scancodes for n in names):
            out = await self.sh("{ %s; } 2>&1 | grep -q . && echo SENDFAIL" % self.key_command(names))
            if out is None:
                return                  # shell lost; the keys can't be sent either way
            if "SENDFAIL" not in out:
                return
            log.warning("%s %s keys failed on %s", self.ip, self.key_method, self.keydev)
            if self.key_method == "print":
                self.key_method = "sendevent"
            else:                       # device gone (remote re-paired?): look again next time
                self.key_method = None
                self.bridge.later(0.1, self.find_remote)
        await self.sh("input keyevent %s" % " ".join(str(KEYS[n]) for n in names))

    async def pair_start(self):
        """Put 'Allow USB debugging?' on the TV and watch for the answer for 90 s."""
        if self.shell and self.shell.returncode is None:
            self.set_pairing("device")
            return
        # A fresh connection re-shows the prompt if it was dismissed earlier.
        await self.adb.run("disconnect", self.serial, timeout=5)
        if await self.ensure_connected(force=True):
            await self.refresh()
            return
        if self.pair_task and not self.pair_task.done():
            return
        self.pair_task = asyncio.ensure_future(self.pair_watch())

    async def pair_watch(self):
        for _ in range(45):
            await asyncio.sleep(2)
            if await self.ensure_connected(force=True):
                await self.refresh()
                return
        if self.state.get("PairingState") == "awaiting_pin":
            self.set("PairingError", "Timed out waiting for Allow on the TV - start pairing again")

    async def pair_pin(self):
        # Fire OS 6/7 pairing is the on-screen prompt only; there is no PIN to submit.
        if not await self.ensure_connected(force=True) and self.state.get("PairingState") == "awaiting_pin":
            self.set("PairingError", "Fire TV has no PIN - choose Allow on the TV screen")

    async def unpair(self):
        """Savant's Unpair (Pro app Settings > Devices) is ignored so a stray tap can't cut off
        control. The pairing state is re-sent so the app keeps showing what is really true."""
        log.info("%s Unpair ignored: the Fire TV stays connected", self.ip)
        if await self.ensure_connected(force=True):
            self.set_pairing("device")

    async def close_shell(self):
        await self.stop_helper()
        if self.shell and self.shell.returncode is None:
            try:
                self.shell.stdin.write(b"exit\n")
                await asyncio.wait_for(self.shell.wait(), 2)
            except Exception:
                self.shell.kill()
        self.shell = None

    async def sh(self, cmd, timeout=8):
        """Run a command in the persistent shell and return its output (None on failure)."""
        async with self.shell_lock:
            return await self._sh(cmd, timeout)

    async def _sh(self, cmd, timeout):
        if not self.shell or self.shell.returncode is not None:
            return None
        if self.stalled_since:
            return None                 # waiting for the Fire TV to answer: send nothing more
        self.marker_n += 1
        marker = "__MATCHSTICK_%d__" % self.marker_n
        try:
            self.shell.stdin.write(("%s; echo %s\n" % (cmd, marker)).encode())
            await self.shell.stdin.drain()
            lines = []
            while True:
                raw = await asyncio.wait_for(self.shell.stdout.readline(), timeout)
                if not raw:
                    raise ConnectionError("shell closed")
                line = raw.decode(errors="ignore").rstrip("\r\n")
                if line.endswith(marker):
                    pre = line[: -len(marker)]
                    if pre:
                        lines.append(pre)
                    return "\n".join(lines)
                lines.append(line)
        except asyncio.TimeoutError:
            # Asleep or a Wi-Fi nap: tearing the connection down here is what leaves the Fire TV
            # holding a dead one, so hold it and wait for this command's answer instead.
            self.stalled_since = time.monotonic()
            log.warning("%s no answer in %ds (%s); holding the connection until it answers",
                        self.ip, timeout, cmd.split(None, 1)[0])
            self.set("LAST_ERROR", "Fire TV not answering (asleep or off Wi-Fi?) - waiting for it")
            asyncio.ensure_future(self.wait_out_stall(marker))
            return None
        except (ConnectionError, BrokenPipeError, OSError) as e:
            self.shell_lost(e)
            return None

    def shell_lost(self, e):
        log.warning("%s shell lost (%s)", self.ip, e.__class__.__name__)
        if self.shell:
            self.shell.kill()
        self.shell = None
        self.set("ONLINE", "0")
        self.set("ADB", "OFFLINE")

    async def wait_out_stall(self, marker):
        """Read the stalled command's answer whenever it comes; give up after stall_hold_seconds."""
        hold = self.bridge.cfg["stall_hold_seconds"]
        shell = self.shell
        try:
            while True:
                left = hold - (time.monotonic() - self.stalled_since)
                if left <= 0:
                    raise asyncio.TimeoutError()
                raw = await asyncio.wait_for(shell.stdout.readline(), left)
                if not raw:
                    raise ConnectionError("shell closed")
                if raw.decode(errors="ignore").rstrip("\r\n").endswith(marker):
                    break
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, asyncio.TimeoutError):
                log.warning("%s no answer for %ds; reconnecting", self.ip, hold)
            else:
                log.warning("%s connection dropped after %ds without an answer; reconnecting",
                            self.ip, time.monotonic() - self.stalled_since)
            self.stalled_since = None
            if self.shell is shell:
                self.shell_lost(e)
            return
        log.info("%s answering again after %ds", self.ip, time.monotonic() - self.stalled_since)
        self.stalled_since = None
        self.set("LAST_ERROR", "")
        await self.start_helper()       # in case it was stopped during the stall
        await self.refresh()

    # ---- work ------------------------------------------------------------
    async def run(self):
        while True:
            item = await self.queue.get()
            # Merge a burst of plain key presses into one `input keyevent A B C` call;
            # each `input` start costs a few hundred ms on the Fire TV.
            batch = [item]
            if item[0] == "KEY":
                while not self.queue.empty() and len(batch) < 10:
                    nxt = self.queue.get_nowait()
                    if nxt[0] != "KEY":
                        await self.execute_batch(batch)
                        batch = [nxt]
                        break
                    batch.append(nxt)
            await self.execute_batch(batch)

    async def execute_batch(self, batch):
        if not await self.ensure_connected(force=True):
            return
        kind = batch[0][0]
        if kind == "KEY":
            await self.send_keys([name for _, name in batch])
            if any(name in POWER_KEYS for _, name in batch):
                self.bridge.later(1.0, self.refresh)
                self.bridge.later(3.0, self.refresh)
            else:
                self.bridge.later(1.0, self.refresh)
            return
        for kind, arg in batch:
            if kind == "KEYCODE":
                if not await self.helper_keys("k %d" % arg):
                    await self.sh("input keyevent %d" % arg)
                self.bridge.later(1.0, self.refresh)
            elif kind == "HOLD":
                if not await self.helper_keys("l %d" % KEYS[arg]):
                    await self.sh("input keyevent --longpress %d" % KEYS[arg])
            elif kind == "LAUNCH":
                pkg = arg
                if pkg.startswith("action:"):
                    out = await self.sh("am start -a %s 2>&1 | grep -q '^Error' && echo NOLAUNCH"
                                        % shlex.quote(pkg[len("action:"):]), timeout=12)
                else:
                    q = shlex.quote(pkg)
                    out = await self.sh(
                        "c=$(cmd package resolve-activity --brief -c android.intent.category.LEANBACK_LAUNCHER %s"
                        " 2>/dev/null | tail -n1); case \"$c\" in */*) am start -n \"$c\" >/dev/null 2>&1;; "
                        "*) monkey -p %s -c android.intent.category.LAUNCHER 1 2>&1 | grep -q 'No activities'"
                        " && echo NOLAUNCH;; esac" % (q, q),
                        timeout=12)
                if out and "NOLAUNCH" in out:
                    self.set("LAST_ERROR", "Can't launch %s - not installed or has no launcher entry" % pkg)
                self.bridge.later(2.0, self.refresh)
            elif kind == "TEXT":
                await self.sh("input text %s" % text_for_input(arg))
            elif kind == "ENTER":
                await self.enter()
            elif kind == "SENDTEXT":
                # Like the Savant Pro app keyboard on Apple TV: the box ends up holding exactly
                # this text, then it is submitted.
                await self.sh(CLEAR_SCRIPT, timeout=10)
                await self.sh("input text %s" % text_for_input(arg))
                await self.enter()
            elif kind == "CLEARTEXT":
                out = await self.sh(CLEAR_SCRIPT, timeout=10)
                if out and "NOKEYBOARD" in out:
                    self.set("LAST_ERROR", "CLEARTEXT: no text box on screen")
                self.bridge.later(1.0, self.refresh)
            elif kind == "LISTAPPS":
                out = await self.sh(APPS_SCRIPT, timeout=10) or ""
                self.set("AvailableApps", json.dumps(parse_apps(out)))
            elif kind == "REFRESH":
                await self.refresh_now()

    async def enter(self):
        out = await self.sh(ENTER_SCRIPT, timeout=15)
        if out and "NOSUGGEST" in out:
            self.set("LAST_ERROR", "ENTER: no search suggestions on screen to open")
        self.bridge.later(2.0, self.refresh)

    async def refresh(self):
        await self.queue.put(("REFRESH", None))

    async def refresh_now(self):
        out = await self.sh(STATUS_SCRIPT)
        if out is None:
            return
        st = parse_status(out)
        for k in ("POWER", "PLAYBACK"):
            if k in st:
                self.set(k, st[k])
        if st.get("POWER") == "OFF":
            self.set("PLAYBACK", "STOPPED")
        if "APP_PACKAGE" in st:
            self.set("APP_PACKAGE", st["APP_PACKAGE"])
            self.set("APP_NAME", friendly_name(st["APP_PACKAGE"]))
        self.set("KeyboardFocused", "true" if st["KEYBOARD"] else "false")

    async def poll_loop(self):
        idle = self.bridge.cfg["idle_disconnect_minutes"] * 60
        while True:
            await asyncio.sleep(self.bridge.cfg["poll_seconds"])
            if self.stalled_since:
                continue                # wait_out_stall is listening for the Fire TV
            if self.subscribers:
                self.last_wanted = time.monotonic()
                if await self.ensure_connected():
                    await self.refresh()
            elif time.monotonic() - self.last_wanted > idle and self.shell:
                log.info("%s idle, closing ADB", self.ip)
                await self.close_shell()

    async def stop(self):
        self.worker.cancel()
        self.poller.cancel()
        await self.close_shell()
        await self.adb.run("disconnect", self.serial, timeout=3)
        await self.relay.stop()


# --------------------------------------------------------------------------- bridge

class Bridge:
    def __init__(self, cfg, adb):
        self.cfg = cfg
        self.adb = adb
        self.devices = {}
        self.writers = set()
        self.server = None
        self.stopping = asyncio.Event()
        self.allowed_devices = set(cfg.get("allowed_devices") or [])
        self.allowed_clients = set(cfg.get("allowed_clients") or [])

    # ---- output ---------------------------------------------------------
    def send(self, writer, line):
        try:
            writer.write((line + "\r\n").encode())
        except Exception:
            pass

    def publish(self, ip, key, value):
        dev = self.devices.get(ip)
        if not dev:
            return
        for w in list(dev.subscribers):
            if w.is_closing():
                dev.subscribers.discard(w)
            else:
                self.send(w, "%s=%s" % (key, value))

    def later(self, delay, coro_fn):
        async def _go():
            await asyncio.sleep(delay)
            await coro_fn()
        asyncio.ensure_future(_go())

    # ---- device lookup ----------------------------------------------------
    def device_ok(self, ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if self.allowed_devices:
            return ip in self.allowed_devices
        return addr.is_private and not addr.is_loopback

    def device(self, ip):
        dev = self.devices.get(ip)
        if not dev:
            dev = FireTV(self, ip)
            self.devices[ip] = dev
        return dev

    # ---- commands ----------------------------------------------------------
    async def command(self, line, writer):
        parts = line.split(None, 2)
        cmd = parts[0].upper()
        if cmd == "PING":
            self.send(writer, "PONG")
            return
        if cmd == "VERSION":
            self.send(writer, "BRIDGE_VERSION=%s" % VERSION)
            return
        if len(parts) < 2:
            self.send(writer, "LAST_ERROR=Missing Fire TV IP in '%s' - set the IP state variable" % cmd)
            return
        ip, arg = parts[1].strip(), (parts[2].strip() if len(parts) > 2 else "")
        if not self.device_ok(ip):
            self.send(writer, "LAST_ERROR=IP %s not allowed (private LAN addresses only, or see allowed_devices)" % ip)
            return
        dev = self.device(ip)
        new_sub = writer not in dev.subscribers
        dev.subscribers.add(writer)
        dev.last_wanted = time.monotonic()
        if new_sub:
            self.send(writer, "BRIDGE_VERSION=%s" % VERSION)
            dev.resend(writer)

        double = arg.strip().lower() if cmd == "HOME" else ""
        if cmd == "HOME" and double not in ("off", "none") and self.cfg["home_double_press_seconds"] > 0:
            # The Apple TV service only has Home (the app's TV button): a quick second press does
            # the double action (the Keyboard profile's Double Home Action, default Live TV). The
            # first press has already gone Home, so single presses aren't delayed.
            now = time.monotonic()
            if now - dev.last_home < self.cfg["home_double_press_seconds"]:
                dev.last_home = 0.0
                cmd, arg = "LIVETV", (arg.strip() if double not in ("", "livetv") else "")
            else:
                dev.last_home = now
        if cmd == "LIVETV":
            target = arg.strip()
            if target.lower() in ("", "livetv"):
                target = str(self.cfg["live_tv"])
            if target.lower().startswith("key:"):
                try:
                    await dev.queue.put(("KEYCODE", int(target[4:])))
                except ValueError:
                    self.send(writer, "LAST_ERROR=Bad key code in '%s'" % target)
            else:
                await dev.queue.put(("LAUNCH", self.resolve_app(target) or target))
        elif cmd in KEYS:
            await dev.queue.put(("KEY", cmd))
        elif cmd == "HOLD":
            key = arg.upper()
            if key in KEYS:
                await dev.queue.put(("HOLD", key))
            else:
                self.send(writer, "LAST_ERROR=Unknown key for HOLD: %s" % arg)
        elif cmd == "LAUNCH":
            pkg = self.resolve_app(arg)
            if pkg:
                await dev.queue.put(("LAUNCH", pkg))
            else:
                self.send(writer, "LAST_ERROR=Unknown app '%s' - add it to app_aliases or use its package name" % arg)
        elif cmd == "TEXT":
            if arg:
                await dev.queue.put(("TEXT", arg))
        elif cmd == "ENTER":
            await dev.queue.put(("ENTER", None))
        elif cmd == "SENDTEXT":
            await dev.queue.put(("SENDTEXT", arg))
        elif cmd in ("CLEARTEXT", "LISTAPPS"):
            await dev.queue.put((cmd, None))
        elif cmd == "PAIRSTART":
            await dev.pair_start()
        elif cmd == "PAIRPIN":
            await dev.pair_pin()
        elif cmd == "UNPAIR":
            await dev.unpair()
        elif cmd == "DISCOVER":
            if await dev.ensure_connected(force=True):
                await dev.refresh()
        elif cmd == "POLL":
            await dev.ensure_connected()
            await dev.refresh()
        elif cmd == "STATUS":
            dev.resend(writer)
        else:
            self.send(writer, "LAST_ERROR=Unknown command %s" % cmd)

    def resolve_app(self, name):
        if not name:
            return None
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+", name):
            return name
        return self.cfg["app_aliases"].get(norm_alias(name))

    # ---- connections ----------------------------------------------------------
    async def client(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("?", 0)
        if self.allowed_clients and peer[0] not in self.allowed_clients:
            log.warning("rejected client %s", peer[0])
            writer.close()
            return
        log.info("client connected %s:%s", *peer[:2])
        self.writers.add(writer)
        buf = b""
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=0.3 if buf else None)
                except asyncio.TimeoutError:
                    chunk = b"\n"      # no terminator arrived: a pause ends the command
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]+", buf)
                buf = parts.pop()
                if len(buf) > 4096:
                    buf = b""
                for part in parts:
                    line = part.decode(errors="ignore").strip()
                    if line:
                        # Log what each client asked for (the port tells Savant's lasting
                        # connections from one-off firetvctl ones); timer traffic only at debug.
                        quiet = line.split(None, 1)[0].upper() in QUIET_COMMANDS
                        (log.debug if quiet else log.info)("<- %s:%s %s", peer[0], peer[1], line)
                        try:
                            await self.command(line, writer)
                        except Exception:
                            log.exception("command failed: %s", line)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.writers.discard(writer)
            for dev in self.devices.values():
                dev.subscribers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            log.info("client disconnected %s:%s", *peer[:2])

    async def watchdog(self):
        limit = self.cfg.get("memory_limit_mb") or 0
        while limit:
            await asyncio.sleep(60)
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
            if mb > limit:
                log.error("memory %.0f MB over limit %d MB - exiting for restart", mb, limit)
                self.stopping.set()
                return

    async def serve(self):
        await self.adb.run("start-server", timeout=15)
        self.server = await asyncio.start_server(
            self.client, self.cfg["listen_host"], self.cfg["listen_port"])
        log.info("Fire TV bridge %s listening on %s:%s (adb: %s)", VERSION,
                 self.cfg["listen_host"], self.cfg["listen_port"], self.adb.path)
        wd = asyncio.ensure_future(self.watchdog())
        await self.stopping.wait()
        wd.cancel()
        log.info("shutting down")
        self.server.close()
        await self.server.wait_closed()
        for w in list(self.writers):
            w.close()
        for dev in list(self.devices.values()):
            await dev.stop()
        await self.adb.run("kill-server", timeout=5)
        log.info("stopped")


# --------------------------------------------------------------------------- main

def setup_logging(cfg, foreground):
    level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    log.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    path = cfg.get("log_file")
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
        h.setFormatter(fmt)
        log.addHandler(h)
    if foreground or not path:
        s = logging.StreamHandler()
        s.setFormatter(fmt)
        log.addHandler(s)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Matchstick - Fire TV bridge for Savant")
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    ap.add_argument("-f", "--foreground", action="store_true", help="also log to the terminal")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg, args.foreground)
    adb_path = find_adb(cfg)
    if not adb_path:
        log.error("adb not found. Run install.sh (it downloads Android platform-tools) "
                  "or set adb_path in config.json.")
        sys.exit(2)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bridge = Bridge(cfg, ADB(adb_path))
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bridge.stopping.set)
    try:
        loop.run_until_complete(bridge.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
