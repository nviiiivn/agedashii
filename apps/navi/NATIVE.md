# Agedashii Navi, native: project brief

**Status: one draft path. Nothing here is built or chosen.** The options for
every piece are in `CHOICES.md`; this document gets redone once those are picked.

## What it is

A home-screen app for a no-SIM Android phone mounted on the bike: map,
offline turn-by-turn, telemetry, ride log. It is `apps/navi` rebuilt in
**Kotlin**, because Flet is too slow on the road. The features, the maths and
the look stay the same.

## The rules it keeps

These come from `README.md` and `apps/moto/XNAV.md`, and they don't change:

- Works with no signal. No exception.
- Navigation and telemetry go in one ride log: the route *and* what you actually did.
- Every channel is labelled measured / derived / estimated. Show a dash, never an invented number.
- Every route says whether it came from the phone or the server.
- The microphone is off until she turns it on.

## The stack

| Piece | Library | Replaces |
|---|---|---|
| Language | Kotlin | Python |
| Screens | Jetpack Compose | Flet |
| Map | MapLibre (maplibre-compose), reading tile files from storage | `tileserver.py` loopback server |
| Routing | valhalla-mobile (Valhalla on the phone, motorcycle costing) | `routing.py` A\* + server Valhalla |
| Turn-by-turn | Ferrostar | the guidance layer XNAV.md says is missing |
| Voice | Android TextToSpeech (works offline) | nothing (Flet had none) |
| Sensors | Android SensorManager, 50–100 Hz | Flet sensors at 400 ms |
| Mic | Android AudioRecord + FFT | `flet-audio-recorder` + pure-Python DFT |
| Recording | Foreground service (survives screen lock) | `live.py` |
| Local data | Room (SQLite): cache + outbox | `store.py` |
| Sync | WorkManager, when home wifi appears | the 20 s watcher |

## Layout

```
agedashii-navi/
  app/          launcher shell, bottom bar, boot into Ride     (main.py)
  core/
    telemetry/  lean, g-forces, grade, ride log, exports        (telemetry.py)
    rpm/        exhaust note -> rpm, band meter                 (dsp.py, audio.py)
    gears/      gear, shift light, bike profiles, learn()       (gears.py, bikes.py)
    guidance/   prompt timing, off-route deadband, re-route cap (guidance.py)
  sensors/      GPS, accel, gyro, baro, compass, mic, service   (sensors.py, live.py)
  map/          MapLibre + offline tiles                        (tiles.py)
  routing/      valhalla-mobile                                 (routing.py)
  data/         Room cache, outbox, dashbored sync              (store.py, api.py)
  ui/
    theme/      navi's 4 themes + agedashii palettes, radius,
                glow, CRT, her yellow/black bike colours        (theme.py, themes.py)
    gauges/     horizon, g-ball, meters, gear badge             (gauges.py)
    screens/    Ride, Where to, Around, Rides, Settings, Help
```

`core/` has no Android code in it, the same way `telemetry.py` and
`guidance.py` have no Flet in them, so all of it can be tested on a laptop.

## Phases

Each phase ends in something she can hold, and each has a test for "done".

| # | Phase | Done when |
|---|---|---|
| 0 | **Toolchain.** JDK 17 + Android SDK (Tower, or the Pi with arm64 aapt2), adb, phone in developer mode, adb over wifi | a hello-world APK builds and installs on the phone |
| 1 | **Map.** MapLibre + the Bay Area tiles from phone storage, GPS follow, bike marker | the map works in airplane mode |
| 2 | **Port the maths.** telemetry, gears, bikes, dsp to Kotlin, and the Python tests port *first* | the ported tests pass |
| 3 | **Recording.** foreground service, GPS + motion sensors, JSONL log, CSV/GPX/JSON export | a 30-minute ride records with the screen locked |
| 4 | **Tach + gear.** mic at 8 kHz, FFT, harmonic product, gear badge, shift light | at steady revs it reads within ~5% of the bike's own tach |
| 5 | **Routing.** Valhalla tiles for the Bay Area, built with the *same Valhalla version* valhalla-mobile ships | Oakland to Fremont offline lands near the server's 42.75 km / 38 min |
| 6 | **Turn-by-turn.** Ferrostar + her rules (60/20/6 s prompts, 3-fix deadband, 12 s re-route cap) + voice | follows a route in airplane mode, re-routes after a missed turn |
| 7 | **The dash.** gauges over the map, all themes, glove-sized targets | readable in the Sunlight theme at speed |
| 8 | **Launcher mode.** HOME app + kiosk (device owner), boots straight into Ride, screen stays on | the phone powers up into the app and can't fall out of it |
| 9 | **Sync.** rides, trips and garage up to dashbored over wifi | a ride recorded in a canyon shows on dashbored once home |
| 10 | **Around + extras.** offline POIs from an OSM extract, weather when online | fuel/coffee search works with no signal |

## Offline data

- Map tiles and Valhalla tiles live in the app's storage folder, copied over
  once with adb. They don't go in the APK, so app updates stay small.
- Expect 200–300 MB all in. Both engines read only what's on screen or on the
  route, so size costs storage, not speed or memory.
- Real sizes get measured in phases 1 and 5.

## Where it builds

On **Tower** or on **the Pi**. Google publishes aapt2 for x86_64 only, but
native arm64 builds exist (see `CHOICES.md` §12). Gradle is pointed at one with
`android.aapt2FromMavenOverride`.

## Tools for Claude on this project

**Already installed, use them:**

| What | For |
|---|---|
| `superpowers:writing-plans` / `executing-plans` | one plan per phase |
| `superpowers:test-driven-development` | phase 2: port the tests before the code |
| `superpowers:systematic-debugging` | anything that breaks on the phone |
| `ecc:kotlin-patterns`, `ecc:kotlin-coroutines-flows` | sensor streams are coroutine Flows |
| `ecc:kotlin-testing` | JUnit + the ported tests |
| `ecc:android-clean-architecture` | keeping `core/` free of Android |
| `ecc:compose-multiplatform-patterns` | Compose screens and gauges |
| `ecc:gradle-build`, `ecc:kotlin-build-resolver` agent | build errors |
| `ecc:kotlin-review`, `ecc:kotlin-reviewer` agent | review after each phase |
| context7 MCP | current docs for Compose, MapLibre, Ferrostar, Room |
| serena MCP | finding code across the project (it speaks Kotlin) |

**To install:**

| What | Why | How |
|---|---|---|
| `kotlin-lsp` plugin | Claude sees Kotlin errors and types as it edits | `/plugin install kotlin-lsp@claude-plugins-official` |
| mobile-mcp | Claude can screenshot and tap the phone to check a screen | `claude mcp add mobile-mcp -- npx -y @mobilenext/mobile-mcp@latest` (needs adb) |
| adb on the Pi | install and inspect over wifi | `sudo apt install adb` |
| JDK 17 + Android SDK on Tower | building | Android Studio, or the command-line tools |

**Not needed here:** the flet MCP (leaving Flet), playwright / chrome-devtools
(web only).

## Open questions

1. Which phone: model and Android version? This decides kiosk mode, whether
   there's a barometer, and the GPS rate.
2. Same app id as the Flet navi (installing replaces it) or a new one (both
   side by side while this is built)?
3. Is the phone mount fixed in one orientation? Pitch and roll calibration
   depend on it.

## Prior art

- [navmaster](https://github.com/francescobuzle-a11y/navmaster): fully offline MapLibre + Valhalla + Ferrostar on Android. The closest working example of this stack.
- [valhalla-mobile](https://github.com/Rallista/valhalla-mobile) and [Ferrostar](https://stadiamaps.github.io/ferrostar/android-getting-started.html)
- [femto-car-launcher](https://github.com/seijikohara/femto-car-launcher): a Compose + MapLibre home-screen launcher
- [DMD2](https://www.dmdnavigation.com/dmd2-app/): the commercial moto launcher, as a reference for look and feel
- [mobile-mcp](https://github.com/mobile-next/mobile-mcp)
- Everything already listed in `apps/moto/XNAV.md`. Check the licence before taking code.
