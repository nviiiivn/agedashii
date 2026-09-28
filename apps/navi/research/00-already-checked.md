# Already checked — candidates found before the section-by-section survey

Six repositories were looked at while answering "start from scratch or start
from something that already works". Recorded here so the survey does not spend
searches re-deriving them.

**Read the confidence note at the bottom before trusting any figure here.**
Several fields come from a summarising fetch of the repository page, not from
opening the LICENSE file. Those are marked unverified.

Checked 2026-09-26.

---

## OUT: navmaster — no licence

<https://github.com/francescobuzle-a11y/navmaster>

Offline Android navigator for trucks, buses and campers. Italian project,
Italian documentation, Italian voice.

**This was briefly recommended as the base. That was wrong, and the correction
is below.** Verified by xnav-2-0-e6 against the GitHub API and the repo tree.

| | |
|---|---|
| Language | Kotlin |
| Stack | MapLibre (render) + Valhalla 3.6.3 (routing) + Ferrostar (guidance) + PMTiles |
| Android | Yes |
| Offline | Yes, fully. Ships a Valhalla graph and an SQLite restrictions DB |
| Commits | 137 on main |
| Stars / forks | 0 / 0 |
| Created | **2026-09-23 — three days old** (verified) |
| Licence | **NONE (verified).** GitHub API reports no licence; there is no LICENSE or COPYING file anywhere in the tree; the README's "Licenze e dati" section says *"Codice NavMaster: © Francesco"* |

**Why it is out.** No licence file means all rights reserved by default. The
code cannot be forked, vendored or built on, whatever its quality. The
BSD-2 / BSD-3 / MIT licences in its README are its *dependencies'* licences, not
its own — that distinction is what the first pass got wrong.

**Second, independent reason.** It is three days old. "137 commits, CI on every
commit, actively maintained" described a brand-new repository committing
quickly, not a proven codebase. The 0 stars / 0 forks should have prompted a
check of the creation date before it was recommended.

**Worth keeping for reading.** It is still the closest working demonstration of
MapLibre + Valhalla + Ferrostar + PMTiles running fully offline on Android, and
of a data pipeline that builds Valhalla tiles on a schedule. Read how it is
wired; take none of it.

---

## Out: vialix

<https://github.com/igalmarino/vialix>

Turn-by-turn navigation for Android built entirely on open data and open
components.

| | |
|---|---|
| Language | Kotlin + Jetpack Compose |
| Stack | MapLibre + Valhalla + Ferrostar + Photon geocoding |
| Android | Yes (iOS planned) |
| Offline | **No.** On the roadmap ("offline maps (PMTiles) and offline routing"), not implemented |
| Commits | 36 |
| Stars / forks | 2 / 1 |
| Licence | GPL-3.0-or-later |
| Maturity | Proof of concept, v0.1 |

**Why it is out.** It fails the first hard rule — works with no signal, no
exception — because offline is not built yet. On paper it is closer to the
target than navmaster (English, Compose, general-purpose rather than trucks), so
it is worth watching, but it cannot be a base today. GPL-3.0-or-later is a
separate consideration if any of this is ever distributed.

---

## Partial: Kblack (map-offline-navigation)

<https://github.com/khoa083/map-offline-navigation>

| | |
|---|---|
| Stack | GraphHopper (routing) + MapLibre (render) |
| Android | Yes |
| Offline | Yes |
| Data | **Vietnam only** |
| Profiles | Car, motorcycle, foot |
| Behaviour of note | Auto-recalculates when off track by more than 30 m |
| Licence | **GPL-3.0 (verified via the GitHub API)** |
| Stars / activity | Not verified |

**Why it is not the pick.** Region-locked to Vietnam, and it commits to
GraphHopper where `CHOICES.md` §5 is still open. Useful as a second worked
example of an offline Android nav app, and as evidence that GraphHopper on
Android still works despite offline Android no longer being officially
supported.

---

## Reference only, cannot be used: Ride-Track

<https://github.com/shivam-uxdesign/Ride-Track>

Motorcycle telemetry tracker: records GPS and motion sensors, shows a live
screen, and gives post-ride analysis with charts, route map and replay.

| | |
|---|---|
| Language | Kotlin |
| Android | Yes |
| Licence | **NONE. No licence file in the repository** |
| Commits | 9 on the featured branch |
| Stars / forks | 0 / 0 |
| Activity | Recent |

**Why it is out as code.** No licence file means all rights reserved by default.
Its code cannot be copied or vendored, whatever its quality. This is the single
most important finding in this file, because on quality alone it would otherwise
be the pick for the telemetry half.

**Why it is in as reading.** Its architecture independently confirms the one in
the Navi spec:

- `core/telemetry` is **pure Kotlin with no Android dependencies, and unit
  tested** — the same "core with no platform imports" rule.
- A `TelemetryPipeline` turning raw readings into derived telemetry at **5 Hz
  for the UI and 1 Hz for persistence**. Navi's Python instead merges motion
  into the latest GPS sample; two different answers to the same problem, and the
  split rate is worth considering.
- Processors: `GpsProcessor`, `LeanEstimator`, `DynamicsProcessor`,
  `AutoPauseDetector`, `EventDetector`.
- A `TelemetrySource` abstraction over raw readings, leaving room for Bluetooth
  OBD or an external GPS later.
- Room + DataStore for storage and export.
- A **demo mode** feeding a simulated ride through the real processing pipeline,
  so the whole UI can be exercised without riding. Navi has no equivalent and
  badly needs one.
- Data-quality rules: fixes worse than 25 m do not add distance; speeds from
  low-accuracy fixes never become max speed; lean and G maxima only come from
  good-confidence estimates while moving.

**The one substantive technical difference.** Ride-Track estimates lean with a
**gyroscope/accelerometer complementary filter plus per-bike calibration**.
Navi's Python derives it from the GPS as `atan(v·ω/g)`. These fail in opposite
places: the GPS method needs no calibration and no known mount orientation, but
is noise below walking pace and cannot see lean while stationary; the filter
method works stopped and at low speed, but needs calibration and a fixed mount.
Running both and cross-checking is better than either alone, and that is not in
the spec doc.

---

## Out: MOTO-HUB

<https://github.com/vincenzobpt/MOTO-HUB>

Projects Android Auto or the phone screen onto a motorcycle's own TFT dashboard,
driven from the handlebar buttons. Supports CFMOTO, Voge, Zontes, Moto Morini,
Benelli, QJ Motor, Morbidelli and other EasyConn/Carbit dashes.

| | |
|---|---|
| Licence | AGPL-3.0 |
| Android | Yes |
| Offline | Local-first |
| GPS | **None.** The repo states it has no GPS features, requests no location updates and sends no coordinates anywhere |

**Why it is out.** A navigation app with no GPS is not a base for this.
AGPL-3.0 is a second, independent reason to keep it at arm's length.

**Worth knowing anyway.** It records rides with full sensor telemetry and
replays them on a map, in a 3D chase-cam view, or as a Google Earth KMZ
flyover, with post-ride analysis of speed, altitude, lean angle and G-forces.
The KMZ flyover is an export idea Navi does not have. Its location-dependent
features appear to live in a separate closed-source APK release (ADV-SOLO), so
the open repo is not the whole product.

---

## Reference: MotorcycleLeanAngleSensor

<https://github.com/braydenzhao/MotorcycleLeanAngleSensor>

Single-purpose: live lean angle on a semicircular ±60° gauge.

| | |
|---|---|
| Android | Yes |
| Licence | **NONE (verified).** Reference only — its code cannot be used |
| Permissions | None at all — no location, internet, camera or storage |

**Why it is worth reading.** It is the smallest possible correct
`LeanEstimator`, and it answers three questions the Navi spec leaves open:

- Uses the **game rotation vector** sensor, falling back to the ordinary
  rotation vector when absent. That choice matters: the game rotation vector
  excludes the magnetometer, so a tank-bag clasp or the bike's frame cannot
  corrupt it.
- Calibrates upright by **averaging one second of samples** — the per-bike
  calibration step Navi's GPS-derived lean avoids needing.
- Smooths to stop engine vibration making the readout flicker, and records max
  left and right lean per ride.

---

## Surfaced but not yet opened

Listed so the survey knows they exist. None of these has been read.

| Name | Link | Note |
|---|---|---|
| maplibre-navigation-android | <https://github.com/maplibre/maplibre-navigation-android> | MapLibre's own nav SDK, a fork of Mapbox Navigation v0.19, moving to Kotlin Multiplatform. Has a pre-configured Valhalla example. §6. |
| graphhopper-navigation-example | <https://github.com/graphhopper/graphhopper-navigation-example> | Official GraphHopper + MapLibre example; notes GraphHopper also works with Ferrostar. §5, §6. |
| NavOSS | <https://github.com/yassinsolim/NavOSS> | MapLibre + Valhalla, privacy-first. iPhone-first, Calgary-only, Android later. Probably out. |
| osm-navigator | <https://github.com/siddharth2798/osm-navigator> | OpenStreetMap / openfreemaps navigator. Unknown. |
| atoid/Dash | <https://github.com/atoid/Dash> | Motorcycle **ECU** dashboard. Honda-specific tables 0x11 and 0xD1, default gear ratios for a stock CB500F. §8, if OBD/ECU is ever pursued. |
| xemniz/stark-dashboard | <https://github.com/xemniz/stark-dashboard> | Kotlin + Compose motorcycle dashboard, but it loads a bundled telemetry JSON through a Ktor MockEngine. UI reference only, not a working sensor app. Possibly §10. |
| `offline-maps` GitHub topic | <https://github.com/topics/offline-maps?l=kotlin&o=asc&s=updated> | Kotlin projects tagged openstreetmap, offline-maps, mbtiles, valhalla, gps-navigation. A browsing surface for §1, §3, §4. |

---

## Where this leaves section 1

Every nav-app candidate found so far is unusable as a base:

| Repo | Why not |
|---|---|
| navmaster | No licence. Three days old |
| vialix | Offline not implemented. GPL-3.0 |
| Kblack | GPL-3.0. Vietnam-only data |
| Ride-Track | No licence |
| MotorcycleLeanAngleSensor | No licence |
| MOTO-HUB | AGPL-3.0, and no GPS at all |

That does not settle section 1 — the full survey has not run, and OsmAnd,
Organic Maps / CoMaps, PocketMaps, WunderLINQ and femto-car-launcher have not
been examined at all. But it does mean "assemble from libraries, with her own
code as the spine" is looking stronger than it did, and that **every candidate
must have its licence checked before anything else about it is discussed.**

## Confidence note

The first pass came from two web searches plus a summarising fetch of each
repository page, with nothing cloned and no LICENSE file opened. That method
produced a wrong recommendation — navmaster was proposed as the base on the
strength of its dependencies' licences, and it has none of its own.

**Verified since, via the GitHub API and the repo trees:** navmaster has no
licence and was created 2026-09-23; Kblack is GPL-3.0; Ride-Track has no
licence; MotorcycleLeanAngleSensor has no licence.

**Still unverified, and must not decide anything until checked:** every star
count, commit count and last-activity date in this file; MOTO-HUB's and
vialix's figures; Kblack's activity.

**The lesson for the survey.** Check the licence first, from the GitHub API or
the LICENSE file itself, never from a README's dependency list. A repository
with no licence is ruled out no matter how good it is. And check the creation
date — a high commit count means nothing on a three-day-old project.

This file is a head start, not a substitute for section 1's own searches.
