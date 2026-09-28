# Agedashii Navi

Turn-by-turn navigation and telemetry for a motorcycle, built to work with no
signal. Its own app, its own theme, its own bundle id — nothing here imports
Moto.

> "i WANT/NEED a proper navigation app for my moto journeys."
> "i need everything to work with no signal. no exception."

## The five surfaces

| Surface | What it is |
|---|---|
| **Ride** | The map and the HUD. Next turn at the top, speed and lean at the bottom, the map filling everything between. |
| **Where to** | Destinations: street search off the device, pasted coordinates, saved trips and rides, GPX import. |
| **Around** | Fuel, coffee, water, viewpoints, bike shops. The one surface that needs signal, and it says so on the button. |
| **Rides** | Every recording on this phone, with its numbers, and CSV / GPX / JSON export. |
| **Settings** | The choices — and an honest account of which sensors this build actually reads. |

Help — titled *How this works* — hangs under Settings.

## APK or web?

Both build. They are not the same product.

The **APK** is the real one. The offline map archive and the on-device road graph
both need a real filesystem and a real Python process, so that is where "works
in a canyon" actually happens.

The **web** build is for planning at a desk. In a browser the app runs under
Pyodide: `tileserver` cannot bind a loopback socket, and the road graph is an
86 MB read that a browser tab has no business doing — so the map falls back to
online tiles and routing falls back to the server. Everything else (the
destination search by coordinate, GPX import, reading an exported ride) works.
Settings says which of the two you are looking at rather than leaving you to
guess.

## What works with the radio off

Everything except four things. The map draws from an MBTiles archive on the
phone, served to the map widget by a loopback HTTP server
(`agedashii_washi.tileserver`). The router is a real A\* search over a packed
road graph on the phone (`agedashii_washi.routing`), so turn-by-turn works in a
canyon. GPS never needed a network. Recording never does. Every export is local.

Needs signal: **Places** (the geocoder), **Around** (Overpass), the saved
trips/rides list on its first load, and pushing a finished ride to the server.

Every route carries `source` — `offline` or `server` — and it is never guessed
at. A route silently produced by a server while she believes the phone is
self-sufficient is the failure that gets someone stranded.

## Telemetry: three kinds of channel, and no pretending

`telemetry.CHANNELS` labels every channel as one of three things, and Settings
prints that table rather than a sentence someone has to remember to update.

**measured** — straight off the GPS receiver: `lat`, `lon`, `speed_ms`,
`heading`, `alt_m`, `acc_m`. Speed and heading are the receiver's own Doppler
solution, not positions subtracted, which is why they are usable while barely
moving.

**derived** — computed from those, with the arithmetic written down:

| Channel | How |
|---|---|
| `g_long` | `dv/dt / g` — braking negative, drive positive |
| `yaw_dps` | `d(heading)/dt`, wrapped across north |
| `g_lat` | `v · ω / g` — the cornering load |
| `lean_deg` | `atan(g_lat)` — see below |
| `grade_pct` | rise over run between fixes |
| `dist_m` | distance travelled |

The lean angle is not an approximation. A bike in a steady corner leans until
the resultant of gravity and centripetal acceleration runs through the contact
patch, so `θ = atan(v·ω/g)` *is* lean, for any bike, with no calibration and no
mount orientation to get wrong. It is meaningless below about 11 mph, so it
reports `None` there rather than a number.

**absent** — `g_vert`, `tilt_deg`, `rpm_est`. Flet has no accelerometer,
gyroscope or live-audio API and there is no package for any of them, so these
need a Flutter extension compiled into the APK. The maths that consumes them is
written and tested against synthetic input — `rpm_from_samples` recovers
3000 rpm from a 50 Hz tone — and `Ride.feed_motion()` / `Ride.feed_audio()` are
the two doors a backend comes in through. Nothing else changes when it lands.

## The ride log

JSON lines, appended as the ride happens, under
`$FLET_APP_STORAGE_DATA/rides/ride-YYYYmmdd-HHMMSS.jsonl`:

```
{"kind":"header","name":"2026-09-26 11:40","started":1790448000.0,"hz":1,"channels":[...]}
{"t":1790448001.0,"lat":37.804400,"lon":-122.271200,"speed_ms":12.3,...}
{"kind":"incident","t":1790448042.1,"what":"hard brake"}
{"kind":"footer","stopped":1790451600.0,"samples":3600,"distance_m":48213.7}
```

Flushed every five samples, so a battery death costs seconds rather than the
ride. The header is a line like any other, so a file torn mid-write is still a
readable file — `read_log` skips the torn line and keeps everything before it.
There is a test that lops the tail off a real log and proves it.

Export writes beside the log: `.csv` (every channel, one row per sample),
`.gpx` (the track, derived channels in an extensions block), `.json` (samples
*plus* the channel dictionary that explains the columns — an export that has to
be read next to this source tree is not an export).

## The speed limit

Typical for the road class, **not** a reading of the sign, and labelled that way
everywhere it appears. The graph knows the OSM class — motorway, residential,
service — and `guidance.TYPICAL_MPH` says what that class is normally posted at
in California.

The graph's own `SPEED_KMH` table is a different quantity: fitted door-to-door
travel speeds used to *pick* a route. Showing those as limits would be wrong by
10 mph on a residential street, so they are not used here. A test asserts the
two tables disagree.

## Guidance behaviour worth knowing

- **Prompts are timed, not fixed**: ~60 s, 20 s and 6 s before a turn, so the
  same instruction lands correctly at 25 mph and at 70. Each band fires once.
  Crossing all three in one fix says the nearest thing only.
- **Off route has a deadband**: three consecutive fixes beyond the threshold to
  go off, and it must come back to 60 % of it to clear. A bare threshold
  flickers on one noisy fix, and each flicker is an announcement and a re-route
  for a corner taken correctly.
- **Re-routes are rate-limited** to one every 12 s, and stop after arrival.
- **No text-to-speech.** There is none on this platform. A prompt is shown
  large at the top for 8 s; `guidance.spoken()` already produces the sentence a
  TTS backend will read. Settings says so on the switch.

## Her two bike colours

Yellow `#ffd93b` or black `#101418`, chosen in Settings, and deliberately *not*
themed — a marker that changed colour with the theme is one she cannot find by
colour. A Material `TWO_WHEELER` glyph rather than the emoji, because the emoji
renders in whatever colour the platform font decides and faces different ways on
different Android versions.

## Themes

Four, not twenty-four: **Night**, **Day**, **Sunlight**, **Cluster**. All four
answer one question Qi's themes were never asked — *can you read this, in a
helmet, in the sun, at 60 mph?* Sunlight is deliberately ugly indoors. No glow,
no CRT, no gradient behind text; the brand gradient survives only in the
identity pill. The warning colour is never the accent, so off-route is findable
by colour alone — there is a test for that.

## Running it

```bash
cd apps/navi
flet run                       # a window, if there is a display
AGEDASHII_HEADLESS=1 flet run  # HTTP on :8554
```

Tests — 95 of them, no device, no display, no network:

```bash
cd apps/navi
PYTHONPATH=../../packages/agedashii-washi:../../packages/agedashii-qi:. \
  python -m unittest tests.test_core tests.test_shell
```

`tests/test_core.py` pins the arithmetic — heading wrap across north, the lean
physics, the off-route deadband, one prompt per band, GPX/CSV/JSON export, a
torn log. `tests/test_shell.py` builds every surface in every theme and in both
unit systems, checks every fixed-height tap target is at least 44 px, and proves
a recording survives switching to all five other surfaces.

## Building

```bash
bash tools/build_app.sh navi apk
bash tools/build_app.sh navi web
```

APKs build on Tower, not on the Pi: Google publishes no `linux-arm64` host
`gen_snapshot` for the Android AOT targets, so an arm64 host can only ever make
a debug APK.

## Where things are

| File | What it owns |
|---|---|
| `main.py` | The shell: theme, bar, which surface is showing. Owns no ride state. |
| `agedashii_navi/live.py` | The live ride — module level, so switching surfaces cannot end a recording. |
| `agedashii_navi/telemetry.py` | Channels, derivation, the log, the exports. No Flet import. |
| `agedashii_navi/guidance.py` | Following a route: off-route, prompts, speed limits, wording. No Flet import. |
| `agedashii_navi/places.py` | The four ways of naming a destination, and GPX. |
| `agedashii_navi/tiles.py` | Which tiles, and the archive-wins rule. |
| `agedashii_navi/theme.py` | The four palettes, and her bike colours. |
| `agedashii_navi/prefs.py` | Every `navi.*` preference, and the storage-service holder. |
| `agedashii_navi/ui.py` | Glove-sized widgets. |

`telemetry` and `guidance` import no Flet at all, and `live` only reaches it
through `prefs`/`theme` — which is why a whole route can be ridden through them
in a test with a list of coordinates.

## Later

Named in `apps/moto/XNAV.md`, not built here: cheapest-fuel search (needs
signal), a sensor extension for the three absent channels, text-to-speech, and
folding this into Moto as its xNav tab once it has earned it.
