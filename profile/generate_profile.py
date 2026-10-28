#!/usr/bin/env python3
"""
Generates the Savant Blueprint component profile for Matchstick, the Fire TV bridge.

    python3 generate_profile.py      -> writes, in Blueprint's library layout (as in Savant's own library):
                                        componentProfiles/"amazon_fire tv (matchstick).xml"
                                        componentProfiles/"amazon_fire tv (matchstick keyboard).xml" (Pro app text entry)
                                        documentation/<same names>.htm (Documentation tab, built from docs/MANUAL.md)
                                        (images/<same names>.icns, the icons, are kept as they are)

Blueprint finds a profile's file by its name, "<manufacturer>_<model>.xml" in lower case
(like Savant's "apple_apple tv (atv-bridge).xml"). Any other file name shows in the
component list but fails with "Component not found" when dragged into the design.

Edit this generator, not the XML. Command and app lists come from the bridge,
so the profile always matches what the bridge understands.

The Keyboard profile copies the shape of Savant's Apple TV (atv-bridge)
profile instead: resource AV_APPLEREMOTEMEDIASERVER_SOURCE with ui_map "atv-bridge",
its action names (SendText/TextMessage, PairStart, PairSubmitPin/PIN ...) and its
states (KeyboardFocused, PairingState ...). serviceRules.xml routes the Savant Pro
app's keyboard and pairing requests to those actions, so the app's text and pairing
pop-ups work for a Fire TV (tested with the Savant Pro app). That service has fewer standard buttons
(no Back, Stop, Scan, Skip ...), so those are custom actions there.

Structure follows Savant's own Roku IP profile (Media_server):
  - internal media source with resource AV_EXTERNALMEDIASERVER_SOURCE, tied to the
    HDMI output in <implementation> -> Blueprint generates a Fire TV service
  - standard remote buttons in <resource_component_actions> with Savant's names
    (Select, OSDCursor*, ScanUp/ScanDown, Exit, Back, Home, Menu, Info ...)
  - everything else in <custom_component_actions>
TCP framing, status parsers, the user-editable IP state variable and
execute_on_schedule come from the Unique Weather Bridge profile.
"""
import base64
import html as htmllib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
# Blueprint's library layout: a component's Documentation tab shows documentation/<profile name>.htm
# and its icon is images/<profile name>.icns, both looked up by the profile's file name.
PROFILES = HERE / "componentProfiles"
DOCS = HERE / "documentation"
sys.path.insert(0, str(HERE.parent / "bridge"))
from firetv_bridge import VERSION, BUILTIN_APPS, FRIENDLY  # noqa: E402

# Blueprint shows rpm_xml_version as the profile's version; Savant uses major.minor (1.4, 1.12).
PROFILE_VERSION = ".".join(VERSION.split(".")[:2])

LC = "Media_server"
PORT = 2360
CRLF = "&#13;&#10;"
IPVAR = "Fire TV IP Address"
HOMEVAR = "Double Home Action"
ETH = "ETHERNET"
HDMI = "HDMI"
SOURCE = "Fire TV"

# Standard Savant service buttons: (Savant action name, bridge command, note)
# Play and Pause both send Play/Pause, like the Fire TV remote (and Savant's Roku
# profile); discrete PlayOnly/PauseOnly are in the custom actions.
RESOURCE_ACTIONS = [
    ("PowerOn", "POWERON", "Wake the Fire TV"),
    ("PowerOff", "POWEROFF", "Put the Fire TV to sleep"),
    ("Home", "HOME", ""),
    ("Select", "SELECT", ""),
    ("OSDCursorUp", "UP", ""),
    ("OSDCursorDown", "DOWN", ""),
    ("OSDCursorLeft", "LEFT", ""),
    ("OSDCursorRight", "RIGHT", ""),
    ("Play", "PLAYPAUSE", "Play/Pause toggle, same as the Fire TV remote"),
    ("Pause", "PLAYPAUSE", "Play/Pause toggle, same as the Fire TV remote"),
    ("Stop", "STOP", ""),
    ("ScanUp", "FF", "Fast forward"),
    ("ScanDown", "REW", "Rewind"),
    ("SkipUp", "NEXT", "Next track/chapter"),
    ("SkipDown", "PREV", "Previous track/chapter"),
    ("Exit", "BACK", "Fire TV Back button"),
    ("Back", "BACK", "Fire TV Back button"),
    ("Menu", "MENU", "Fire TV options (three-line) button"),
    ("Info", "MENU", "Fire TV options (three-line) button"),
    ("Setup", "SETTINGS", "Fire TV Settings"),
]

CUSTOM_ACTIONS = [
    ("PowerToggle", "POWERTOGGLE", ""),
    ("PlayOnly", "PLAY", "Discrete play (most apps honour it)"),
    ("PauseOnly", "PAUSE", "Discrete pause (most apps honour it)"),
    ("VolumeUp", "VOLUP", "Sent to the TV/AVR over HDMI-CEC"),
    ("VolumeDown", "VOLDOWN", "Sent to the TV/AVR over HDMI-CEC"),
    ("MuteToggle", "MUTE", "Sent to the TV/AVR over HDMI-CEC"),
    ("Search", "SEARCH", ""),
    ("LiveTV", "LIVETV", "Live TV channel guide (bridge config live_tv). A quick double Home does the same"),
    ("Enter", "ENTER", "Submit typed text (send after TypeText); on the search screen opens the results"),
    ("HomeLongPress", "HOLD|HOME", "Long-press Home: quick settings / sleep menu"),
    ("Refresh", "POLL", "Refresh status now"),
    ("RequestAllStates", "STATUS", "Resend every status value"),
]

STATES = [
    # name, type, initial, note
    ("ONLINE", "integer", "0", "1 = Fire TV reachable"),
    ("ADB", "string", "", "CONNECTED, UNAUTHORIZED (approve on TV) or OFFLINE"),
    ("POWER", "string", "", "ON or OFF"),
    ("APP_NAME", "string", "", "Friendly name of the app on screen (Netflix, Home ...)"),
    ("APP_PACKAGE", "string", "", "Android package of the app on screen"),
    ("PLAYBACK", "string", "", "PLAYING, PAUSED or STOPPED"),
    ("MODEL", "string", "", "Fire TV model code"),
    ("FIREOS", "string", "", "Fire OS version"),
    ("LAST_ERROR", "string", "", "Blank when healthy; otherwise what to fix"),
    ("BRIDGE_VERSION", "string", "", "Bridge software version"),
]

# ---- Keyboard profile (Savant Apple TV service: Pro app text entry and pairing) ------
# Action names and argument names must match SVC_AV_APPLEREMOTEMEDIASERVER in
# serviceRules.xml. (Savant action, bridge command, note[, (argument, note)])
KEYBOARD_RESOURCE_ACTIONS = [
    ("PowerOn", "POWERON", "Wake the Fire TV"),
    ("PowerOff", "POWEROFF", "Put the Fire TV to sleep"),
    ("RepeatStop", "PING", "Sent when a held button is released; nothing to do on a Fire TV"),
    ("Select", "SELECT", ""),
    ("OSDCursorUp", "UP", ""),
    ("OSDCursorDown", "DOWN", ""),
    ("OSDCursorLeft", "LEFT", ""),
    ("OSDCursorRight", "RIGHT", ""),
    ("Menu", "BACK", "Apple TV Menu = back, so this is the Fire TV Back button"),
    ("Home", "HOME@" + HOMEVAR, "The app's TV button. A quick second press does the Double Home Action"),
    ("CommandPlay", "PLAYPAUSE", "Play/Pause toggle, same as the Fire TV remote"),
    ("CommandPause", "PLAYPAUSE", "Play/Pause toggle, same as the Fire TV remote"),
    ("PairStart", "PAIRSTART", "Shows 'Allow USB debugging?' on the TV"),
    ("PairSubmitPin", "PAIRPIN", "Fire TV has no PIN; re-checks the approval on the TV",
     ("PIN", "Ignored: approve on the TV screen")),
    ("Unpair", "UNPAIR", "Ignored by the bridge so a stray tap can't cut off control; the Fire TV stays paired"),
    ("Discover", "DISCOVER", "Check the Fire TV is reachable"),
    ("ListApps", "LISTAPPS", "Publishes AvailableApps"),
    ("LaunchApp", "LAUNCH", "Open an app by package name (from AvailableApps) or alias",
     ("AppID", "Package name, e.g. com.netflix.ninja, or an alias such as netflix")),
    ("SendText", "SENDTEXT", "Replace the text box contents and submit (Savant Pro app keyboard)",
     ("TextMessage", "Text to put in the box")),
    ("ClearText", "CLEARTEXT", "Empty the text box"),
]
# Buttons the Apple TV service has no request for, kept for workflows and custom buttons.
KEYBOARD_EXTRA_CUSTOM = [
    ("Back", "BACK", "Fire TV Back button"),
    ("OptionsMenu", "MENU", "Fire TV options (three-line) button"),
    ("Stop", "STOP", ""),
    ("FastForward", "FF", ""),
    ("Rewind", "REW", ""),
    ("NextTrack", "NEXT", "Next track/chapter"),
    ("PreviousTrack", "PREV", "Previous track/chapter"),
    ("Setup", "SETTINGS", "Fire TV Settings"),
]
# Same names and values Savant's atv-bridge service posts (see its atv-bridge.log).
KEYBOARD_STATES = [
    ("KeyboardFocused", "boolean", "false", "true while a text box is on screen"),
    ("Discovered", "boolean", "false", "true when the Fire TV answers on the network"),
    ("PairingState", "string", "unpaired", "unpaired, awaiting_pin (approve on the TV) or paired"),
    ("PairingError", "string", "", "Why pairing is waiting or failed"),
    ("AvailableApps", "string", "", 'JSON list of [{"name", "identifier"}] after ListApps'),
]

MANUFACTURER = "Amazon"

VARIANTS = {
    "standard": dict(
        model="Fire TV (Matchstick)",
        resource="AV_EXTERNALMEDIASERVER_SOURCE", ui_map=None, atv_service=False,
        res=RESOURCE_ACTIONS, custom=CUSTOM_ACTIONS, states=STATES),
    "keyboard": dict(
        model="Fire TV (Matchstick Keyboard)",
        resource="AV_APPLEREMOTEMEDIASERVER_SOURCE", ui_map="atv-bridge", atv_service=True,
        res=KEYBOARD_RESOURCE_ACTIONS, custom=CUSTOM_ACTIONS + KEYBOARD_EXTRA_CUSTOM,
        states=STATES + KEYBOARD_STATES,
        settings=[(HOMEVAR, "livetv",
                   "What a quick double press of Home (the app's TV button) does: livetv (Live TV guide), "
                   "off, an app alias or package (netflix, com.hulu.plus), or key:<android keycode>")]),
}


def a(s):
    return escape(s, {'"': "&quot;"})


def ip_params(extra=""):
    """IP from the state variable, optional literal/argument parts, then CRLF."""
    return f"""
                        <parameter_list>
                            <parameter parameter_data_type="character" state_variable="{IPVAR}"></parameter>{extra}
                            <parameter parameter_data_type="character">{CRLF}</parameter>
                        </parameter_list>"""


def action(name, cmd, note="", arg=None, schedules=()):
    arg_xml, extra = "", ""
    if "@" in cmd:                      # state variable argument, e.g. HOME@Var -> "HOME <ip> <Var>"
        cmd, var = cmd.split("@", 1)
        extra = ('\n                            <parameter parameter_data_type="character"> </parameter>'
                 f'\n                            <parameter parameter_data_type="character" state_variable="{a(var)}"></parameter>')
    if "|" in cmd:                      # fixed argument, e.g. HOLD|HOME -> "HOLD <ip> HOME"
        cmd, fixed = cmd.split("|", 1)
        extra = f'\n                            <parameter parameter_data_type="character"> {a(fixed)}</parameter>'
    if arg:
        aname, anote = arg
        arg_xml = f'\n                <action_argument name="{aname}" note="{a(anote)}"></action_argument>'
        extra = ('\n                            <parameter parameter_data_type="character"> </parameter>'
                 f'\n                            <parameter parameter_data_type="character" action_argument="{aname}"></parameter>')
    sched = "".join(f'\n                <execute_on_schedule period_ms="{ms}"></execute_on_schedule>' for ms in schedules)
    comment = f"\n                <!-- {escape(note)} -->" if note else ""
    return f"""
            <action name="{name}">{comment}{arg_xml}
                <command_interface interface="ip">
                    <command response_required="no">
                        <command_string type="character">{cmd} </command_string>{ip_params(extra)}
                    </command>
                </command_interface>{sched}
            </action>"""


def build(v):
    app_list = ", ".join(sorted(BUILTIN_APPS))
    resource, states_list = v["resource"], v["states"]
    if v["atv_service"]:
        intro = f"""
        KEYBOARD VERSION: the Savant Pro app's text entry and pairing pop-ups work for this Fire TV. Built
        like Savant's Apple TV (atv-bridge) profile, without the atv-bridge service: the Pro app's SendText,
        ClearText, PairStart and PairSubmitPin go to the Matchstick bridge. Pairing a Fire TV is the 'Allow
        USB debugging?' prompt on the TV; there is no PIN. Unpair in the app is ignored (the Fire TV stays
        connected). Back, Stop, Scan, Skip and Setup are custom actions.
        The app's TV button is Home; a quick double press does the "Double Home Action" (default: Live TV).
        The app shows this service as Apple TV: set Brand to Display = Amazon Fire TV and a service alias.
"""
        extra_props = f"""
    <component_properties>
        <custom_properties>
            <property name="hasOSD">true</property>
            <property name="defaultDisableOSD">true</property>
        </custom_properties>
    </component_properties>
    <resource_ui_mappings>
        <resource_map resource_type="{resource}" ui_map="{v['ui_map']}"/>
    </resource_ui_mappings>"""
    else:
        intro, extra_props = "", ""
    head = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Matchstick Fire TV profile for bridge v{VERSION}. Generated by generate_profile.py: edit the generator, not this file. -->
<component xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="racepoint_component_profile.xsd" manufacturer="{MANUFACTURER}" model="{v['model']}" device_class="Media_server" minimum_component_engine_version="0.0" rpm_xml_version="{PROFILE_VERSION}" unique_identifier="none">
    <notes>
        Amazon Fire TV (Fire OS models) over IP, through the Matchstick bridge service.
        Not supported: Fire TV models running Vega OS (Fire TV Stick 4K Select, Fire TV Stick HD 2026 and newer sticks).
{intro}
        SETUP
        1. Install the bridge on the Savant host (install.sh) and pair the Fire TV once: firetvctl pair [fire-tv-ip]
        2. Set this component's IP address to the machine running the bridge (normally this Savant host). Port {PORT}.
        3. Inspector, State Variables: set "{IPVAR}" to the Fire TV's IP (give it a DHCP reservation).
        4. Wire the HDMI output to the display or switcher like any source.

        One component per Fire TV; all of them point at the same bridge.
        LaunchApp takes an alias ({app_list}) or any Android package name.
        Volume/Mute custom actions go to the TV/AVR over HDMI-CEC; normally route volume to the AVR/TV in Savant.
        Gate automations on ONLINE = 1. If LAST_ERROR is not blank it says what to fix.

        ====================== Change Log ==================
        Version: 1.4 Change: Keyboard version (Pro app text entry and pairing), LiveTV action, Double Home Action,
                     file names Blueprint can find (amazon_fire tv (...).xml). Needs Matchstick bridge 1.4.
        Version: 1.1 Change: Rebuilt on Savant's Roku profile structure (service generation, standard button names).
        Version: 1.0 Change: Initial release.
    </notes>{extra_props}
    <control_interfaces preferred="ip">
        <ip port="{PORT}" response_time_length_ms="1000" protocol="tcp" name_on_component="{ETH}">
            <receive_end_condition test_condition="data" type="hex">0A</receive_end_condition>
        </ip>
    </control_interfaces>
    <media_interfaces>
        <output name_on_component="{HDMI}">
            <combined_media>
                <audio_media type="hdmi_audio"></audio_media>
                <video_media type="hdmi"></video_media>
            </combined_media>
        </output>
        <data name_on_component="{ETH}">
            <combined_media>
                <data_media type="ethernet"></data_media>
                <control port="{PORT}"></control>
            </combined_media>
        </data>
        <internal name_on_component="{SOURCE}">
            <audio_media></audio_media>
            <video_media></video_media>
            <resource resource_type="{resource}"></resource>
        </internal>
    </media_interfaces>
    <state_variable_list>
        <state_variable name="{IPVAR}" state_center_type="string" note="IP address of this Fire TV, e.g. 192.168.1.50" owning_logical_component="{LC}" user_editable="yes"></state_variable>"""
    states = "".join(
        f'\n        <state_variable name="{n}" state_center_binding="{n}" state_center_type="{t}" '
        f'owning_logical_component="{LC}" note="{a(note)}">{init}</state_variable>'
        for n, t, init, note in states_list)
    states += "".join(
        f'\n        <state_variable name="{a(n)}" state_center_type="string" owning_logical_component="{LC}" '
        f'note="{a(note)}" user_editable="yes">{init}</state_variable>'
        for n, init, note in v.get("settings", ()))
    implementation = f"""        <implementation>
            <output name_on_component="{HDMI}"></output>
            <internal name_on_component="{SOURCE}"></internal>
        </implementation>"""
    status = "".join(f"""
            <status_message name="{n}">
                <constant type="character">{n}=</constant>
                <data type="character" terminator="0D" terminator_type="hex">
                    <update state="{n}" type="{t}"></update>
                </data>
            </status_message>""" for n, t, _, _ in states_list)

    res = [action(*r) for r in v["res"]]
    cus = [action(n, c, note) for n, c, note in v["custom"]]
    if not any(r[0] == "LaunchApp" for r in v["res"]):
        cus.append(action("LaunchApp", "LAUNCH", "Open an app by alias or package name",
                          arg=("App", f"Alias ({app_list}) or package name, e.g. com.netflix.ninja")))
    cus.append(action("TypeText", "TEXT", "Type into the focused text box (e.g. a search field)",
                      arg=("Text", "Text to type")))
    for alias, pkg in sorted(BUILTIN_APPS.items()):
        nice = FRIENDLY.get(pkg, alias).replace("+", "Plus").replace(" ", "")
        cus.append(action(nice, f"LAUNCH|{alias}", f"Open {FRIENDLY.get(pkg, alias)}"))
    cus.append(action("Connect", "POLL", "Runs at startup and every 10 s: subscribes to status, keeps the link alive",
                      schedules=(0, 10000)))

    xml = "\n".join([
        head + states,
        "    </state_variable_list>",
        f'    <logical_component logical_component_name="{LC}">',
        implementation,
        "        <status_messages>" + status,
        "        </status_messages>",
        f'        <resource_component_actions resource_type="{resource}">' + "".join(res),
        "        </resource_component_actions>",
        "        <custom_component_actions>" + "".join(cus),
        "        </custom_component_actions>",
        "    </logical_component>",
        "</component>",
        "",
    ])
    out = PROFILES / f"{MANUFACTURER}_{v['model']}.xml".lower()
    out.parent.mkdir(exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    print(f"wrote {out.name}: {len(res)} service actions, {len(cus)} custom actions, {len(states_list)} states")
    docs_html(v, out.with_suffix(".htm").name)


# ---------------------------------------------------------------------------------------------
# Documentation tab: the installer manual (docs/MANUAL.md) as one self-contained page, converted
# by the small Markdown converter below, which covers only what the manual uses (the same one as
# OpenWx's). Screenshots are embedded as data: URIs. The page is ASCII: other characters become
# &#...; references.

ROOT = HERE.parent
MANUAL = ROOT / "docs" / "MANUAL.md"
WEB_IMAGES = ROOT / "docs" / "images" / "web"
DOC_IMAGE_WIDTH = 900  # px; screenshots are scaled down to this for the embedded copies
IMG_TAG = re.compile(r'<img\s+src="([^"]+)"[^>]*?(?:alt="([^"]*)")?[^>]*>')


def doc_image(src):
    """data: URI for a manual image (a path relative to docs/). On macOS, sips makes a smaller
    JPEG copy in docs/images/web/ (committed, so other platforms reuse it); otherwise the
    original file is embedded."""
    orig = MANUAL.parent / src
    web = WEB_IMAGES / (orig.stem + ".jpg")
    if shutil.which("sips") and (not web.exists() or web.stat().st_mtime < orig.stat().st_mtime):
        web.parent.mkdir(exist_ok=True)
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70",
                        "--resampleWidth", str(DOC_IMAGE_WIDTH), str(orig), "--out", str(web)],
                       check=True, stdout=subprocess.DEVNULL)
    f = web if web.exists() else orig
    mime = "image/jpeg" if f.suffix in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode("ascii")


def slug(text):
    """GitHub's heading anchors, so the manual's table-of-contents links work unchanged."""
    text = re.sub(r"<[^>]+>", "", text).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def inline(text):
    codes = []

    def keep(m):
        codes.append(f"<code>{htmllib.escape(m.group(1), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = htmllib.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def cells(row):
    row = row.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


LIST_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+")


def md_blocks(lines):
    out, i = [], 0
    while i < len(lines):
        line, s = lines[i], lines[i].strip()
        if not s:
            i += 1
        elif s.startswith("```"):
            pad = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            code = "\n".join(l[pad:] if l[:pad].isspace() else l.lstrip() for l in lines[i + 1:j])
            out.append(f"<pre>{htmllib.escape(code, quote=False)}</pre>")
            i = j + 1
        elif s == "---":
            out.append("<hr>")
            i += 1
        elif s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            text = inline(s[level:].strip())
            out.append(f'<h{level} id="{slug(text)}">{text}</h{level}>')
            i += 1
        elif s.startswith(">"):
            j = i
            while j < len(lines) and lines[j].strip().startswith(">"):
                j += 1
            quote = [l.strip()[1:].lstrip() for l in lines[i:j]]
            out.append(f'<div class="note">{md_blocks(quote)}</div>')
            i = j
        elif s.startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            head, rows = cells(lines[i]), [cells(r) for r in lines[i + 2:j]]
            th = "" if not any(head) else "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>\n"
            td = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>\n" for r in rows)
            out.append(f"<table>\n{th}{td}</table>")
            i = j
        elif IMG_TAG.fullmatch(s):     # the manual's screenshots: <img src="images/..." alt="...">
            src, alt = IMG_TAG.fullmatch(s).groups()
            alt = htmllib.escape(alt or "")
            out.append(f'<div class="figure"><img src="{doc_image(src)}" alt="{alt}">'
                       f'<div class="caption">{alt}</div></div>')
            i += 1
        elif LIST_ITEM.match(line):
            i = md_list(lines, i, out)
        else:
            j = i
            while (j < len(lines) and lines[j].strip() and not LIST_ITEM.match(lines[j])
                   and not lines[j].strip().startswith(("#", "|", "```", ">", "<img"))):
                j += 1
            out.append("<p>" + inline(" ".join(l.strip() for l in lines[i:j])) + "</p>")
            i = j
    return "\n".join(out)


def md_list(lines, i, out):
    """A list at one indent. Lines indented past the marker, including fenced code and nested
    lists, belong to the item above them."""
    m = LIST_ITEM.match(lines[i])
    indent, ordered = len(m.group(1)), m.group(2)[0].isdigit()
    items = []
    while i < len(lines):
        m = LIST_ITEM.match(lines[i])
        if not m or len(m.group(1)) != indent:
            break
        cut = m.end()  # continuation lines are indented to the item's text
        body, i = [lines[i][cut:]], i + 1
        while i < len(lines):
            l = lines[i]
            if l.strip() and len(l) - len(l.lstrip()) <= indent:
                break
            if not l.strip() and not (i + 1 < len(lines) and len(lines[i + 1]) - len(lines[i + 1].lstrip()) > indent):
                break
            body.append(l[cut:] if l[:cut].isspace() else l.lstrip())
            i += 1
        html = md_blocks(body)
        if html.startswith("<p>") and html.count("<p>") == 1:
            html = html[3:].replace("</p>", "", 1)
        items.append(f"<li>{html}</li>")
    tag = "ol" if ordered else "ul"
    out.append(f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>")
    return i


DOC_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 13px; line-height: 1.45; margin: 16px 20px; color: #222; max-width: 980px; }
h1 { font-size: 22px; margin: 0 0 6px; }
h2 { font-size: 17px; margin: 26px 0 8px; border-bottom: 1px solid #ccc; padding-bottom: 3px; }
h3 { font-size: 14px; margin: 18px 0 6px; }
table { border-collapse: collapse; margin: 6px 0 10px; }
td, th { font-size: 13px; border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
code, pre { font-family: Menlo, Monaco, monospace; font-size: 12px; }
pre { background: #f4f4f4; padding: 6px 10px; overflow-x: auto; }
hr { border: 0; border-top: 1px solid #ddd; margin: 18px 0; }
.note { background: #fff8e0; border: 1px solid #e8d48a; padding: 2px 10px; margin: 8px 0; }
.figure { margin: 10px 0 14px; }
.figure img { max-width: 100%; max-height: 560px; border: 1px solid #ddd; }
.caption { color: #666; font-size: 12px; margin-top: 3px; }
"""


def docs_html(v, name):
    """The whole installer manual as this profile's Documentation tab page."""
    body = md_blocks(MANUAL.read_text(encoding="utf-8").splitlines())
    page = f"""<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=us-ascii">
<title>{MANUFACTURER} {htmllib.escape(v['model'])} - Matchstick {VERSION}</title>
<style>{DOC_CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""
    out = DOCS / name
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(page.encode("ascii", "xmlcharrefreplace"))
    print(f"wrote documentation/{name}: Documentation tab ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    for variant in VARIANTS.values():
        build(variant)
