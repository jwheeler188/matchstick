# Matchstick 1.4: Fire TV control for Savant

IP control of Amazon Fire TV devices from Savant: navigation, apps, power, Live TV and, with the
Keyboard profile, the Savant Pro app's text entry and pairing. A small service on the Savant host
speaks ADB to each Fire TV; the Blueprint profile talks to it over TCP.

```
Savant (profile)  --TCP 2360-->  Matchstick on the Savant host  --ADB 5555-->  Fire TV
```

**Full instructions: [docs/MANUAL.md](docs/MANUAL.md).** What changed: [CHANGELOG.md](CHANGELOG.md).

## Quick start

1. **Fire TV:** Settings → My Fire TV → Developer Options → **ADB debugging ON**. Reserve its IP.
   Works with Fire OS models (Stick 4K / 4K Max / 4K Plus, Cube, Fire TV TVs), not Vega OS ones.
2. **Host:** copy this folder to the Savant host and run, as the Savant user (not sudo):
   `bash install.sh`. Enter the password once.
3. **Pair:** `firetvctl pair <fire-tv-ip>`, then on the TV tick **Always allow from this
   computer** and choose **OK**. Check: `firetvctl test <fire-tv-ip> HOME`.
4. **Blueprint:** copy a profile's three files from `profile/` (`componentProfiles`,
   `documentation` and `images`) into the same folders of your custom profiles folder **without
   renaming them** (the `.htm` is this manual, for the Documentation tab), add one
   component per Fire TV, wire its ETHERNET port to the switch and set the
   Device IP to the **Savant host**, then set the **Fire TV IP Address** state variable to the Fire TV.
   - `amazon_fire tv (matchstick).xml`: standard Fire TV service and remote.
   - `amazon_fire tv (matchstick keyboard).xml`: Pro app text entry and pairing, TV button double
     press for Live TV. Set Brand to Display = Amazon Fire TV and a service alias (manual, section 7).

## In this folder

| Path | What |
|---|---|
| `install.sh`, `uninstall.sh` | Install or remove on the Savant host |
| `bridge/` | The service, its example config and the Fire TV key helper |
| `scripts/firetvctl` | Management command (installed as `firetvctl`) |
| `profile/` | Blueprint profiles, their Documentation tab pages and icons (in Blueprint's folder layout), and their generator |
| `tools/` | Test scripts for troubleshooting on site |
| `docs/MANUAL.md` | Installer manual |
| `keyhelper/` | Source of the key helper |

## Development

`profile/generate_profile.py` builds both profiles from the bridge's command list, and their
Documentation tab pages from `docs/MANUAL.md`: edit the generator or the manual and re-run it, not
the XML or `.htm`. The profiles' version comes from `VERSION` in the bridge
(major.minor); bump it for any profile change, or Blueprint won't update existing components. `keyhelper/build.sh` rebuilds `bridge/matchstick-keys.dex`
from `Keys.java` (needs a JDK and the Android SDK); the built file is shipped.

## Licence

MIT: see [LICENSE](LICENSE). Matchstick is an independent project, not made or endorsed by
Savant or Amazon. Savant, Fire TV and Amazon are trademarks of their owners.
