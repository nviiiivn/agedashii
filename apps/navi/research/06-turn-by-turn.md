# Section 6 — Turn-by-turn guidance and voice

Researched 2026-09-26. Research only; nothing in `CHOICES.md` was edited.

**Rule applied throughout: no LICENSE file = ruled out.** Every licence below was
read by opening the actual file (`gh api repos/<r>/license`, or the file it
points at), not taken from a badge or a search summary. Where a figure could not
be confirmed it says **unverified**.

---

## 0. What is actually being replaced

Read first, because it changes what "fit" means.

`agedashii_navi/guidance.py` (393 lines) plus `live.py` is a **complete, tested
guidance layer**. The decided behaviour, with the constant that holds it:

| Rule | Where it lives now | Value |
|---|---|---|
| Prompt bands, time-to-turn | `guidance.PROMPT_BANDS` | `((60.0,"far"),(20.0,"near"),(6.0,"now"))` |
| Crawl floor | `guidance.CRAWL_MS` | `3.0` m/s |
| Crawl distance bands | `guidance.CRAWL_BANDS_M` | `far 250.0, near 90.0, now 25.0` m |
| Off-route threshold | `Follower.offroute_m` (from `prefs.offroute_m()`) | `45` m default |
| Off-route confirmation | `guidance.CONFIRM_FIXES` | `3` consecutive fixes |
| Return hysteresis | `guidance.RETURN_FRACTION` | `0.6` × threshold |
| Re-route cap | `live.REROUTE_EVERY` | `12.0` s |
| Arrival | `Follower.update`, hardcoded | `left_m < 25.0` m |
| Prompt fires once | `Follower._said` set of `(index, band)` | nearest band first |
| Off-route suppresses prompts | `Follower.update` | `if not self.off_route and ...` |
| Prompt wording | `guidance.spoken`, `distance_text` | `"In 400 ft, turn left onto Grand"`; `"now"` band drops the distance |

Two things about the current state that matter for the section:

1. **There is no voice at all today.** `spoken()` returns a string; `live.py`
   parks it in `LIVE["prompt"]` and the HUD draws it. Grepping `apps/navi` for
   `tts|speak|speech` finds nothing but prose in docstrings. So the *text* is
   specified and must survive; the *speaking* is a greenfield choice with no
   behaviour to preserve.
2. `_maybe_say` walks bands **nearest-first** and, on firing one, marks every
   wider band for that maneuver as said. That is a subtle, correct rule (see its
   docstring) and no library below reproduces it. It has to be ported whatever
   wins.

The re-route cap semantics are worth pinning down because a library's equivalent
may differ: `live.py:132` sets `LIVE["last_route_at"] = time.time()` **after**
`routing.route()` returns, and `live.py:280` gates on
`t - last_route_at >= 12.0`. So the clock starts when the previous route
*arrives*, not when it was asked for.

---

## 1. Ferrostar

<https://github.com/stadiamaps/ferrostar> · book: <https://stadiamaps.github.io/ferrostar/>

| | |
|---|---|
| Licence | **BSD-3-Clause.** Verified by opening `LICENSE.txt`: "Copyright (c) 2023, Stadia Maps, Inc." with the three clauses (retain notice / reproduce notice / no endorsement). GitHub reports `NOASSERTION` only because the file has a trailing attribution note for Mapbox Directions Icons (CC0). |
| Latest release | `0.57.0`, **2026-09-22** (four days before this was written) |
| Release cadence | 0.52 → 0.57 between 2026-06-17 and 2026-09-22 — roughly weekly |
| Last commit | 2026-09-22 |
| Stars / forks / open issues | 428 / 81 / 143 |
| Created | 2023-04-27 |
| Primary language | Kotlin (Rust core, uniffi bindings) |
| Android | Yes. `minSdk 25` |
| Fully offline | **Yes**, with a `CustomRouteProvider`. Nothing in `:core` reaches the network. |
| Speed | Rust core, one pass per fix. The Kotlin binding is **uniffi over JNA** (`net.java.dev.jna:jna:5.18.1@aar` in `core/build.gradle`), so each call crosses a JNA boundary rather than hand-written JNI. Irrelevant at 1 Hz; worth knowing before anyone runs it at 25 Hz. |

### Modules, and the Material problem

`android/` contains `core`, `ui-compose`, `ui-maplibre`, `ui-formatters`,
`ui-shared`, `google-play-services`, `car-app`, `ui-maplibre-car-app`,
`demo-app`.

Read the gradle files directly:

- **`ui-compose/build.gradle` line 49: `implementation libs.androidx.compose.material3`.** The
  bundled UI is Material 3. That half is unusable under the alola rule.
- **`core/build.gradle` has no Compose and no Material dependency at all** —
  only `androidx.ktx`, `appcompat`, `kotlinx.coroutines`, `kotlinx.datetime`,
  `lifecycle.viewmodel.ktx`, `kotlinx.serialization` and the JNA aar.

So the shape is: take `:core`, throw away `ui-compose` and `ui-maplibre`, draw
the banner yourself. That is not a workaround — `:core` is a clean non-UI module
and the book lists it as "the navigation engine bindings, location service APIs,
and non-UI Android integration".

### Bring-your-own-router: the claim, verified

The book's Route Providers page was opened and read. Verified:

> On Android, implement the `CustomRouteProvider` interface with a single method:
> ```kotlin
> override suspend fun getRoutes(
>   userLocation: UserLocation,
>   waypoints: List<Waypoint>
> ): List<Route>
> ```

and, verbatim from the page: custom providers are "most commonly used for local
route generation", and Ferrostar "has no mandatory dependency on specific routing
services… you're free to define your own in your application code".

Two more things that de-risk section 5:

- `createRouteFromOsrm` / `createRouteFromOsrmRoute` build a Ferrostar `Route`
  from OSRM-shaped JSON. `valhalla-mobile` can answer `route` in OSRM format
  on-device, so Valhalla-on-device → Ferrostar is a two-line adapter.
- Because the provider is arbitrary Kotlin, **her own `routing.py` A\*, ported,
  also satisfies it.** Ferrostar does not care where a `Route` came from.

**Verdict on the compatibility question: no router lock-in. Confirmed from docs
and from source. This is the strongest option on that axis.**

### Against her actual rules

Read from source, not docs:

`common/ferrostar/src/deviation_detection.rs`:

```rust
pub enum RouteDeviationTracking {
    None,
    StaticThreshold {
        minimum_horizontal_accuracy: u16,   // argument ONE
        max_acceptable_deviation: f64,      // argument TWO
    },
    Custom { detector: Arc<dyn RouteDeviationDetector> },
}

pub trait RouteDeviationDetector: Send + Sync {
    fn check_route_deviation(&self, route: Route, trip_state: TripState) -> RouteDeviation;
}
```

- **45 m threshold: settable** (`max_acceptable_deviation = 45.0`). Note the
  argument order — accuracy gate first. Getting it backwards is a known
  foot-gun; a 15 m accuracy gate silently means fixes under trees are never
  evaluated at all.
- **3 consecutive fixes: NOT available.** `StaticThreshold` is a bare per-fix
  test. It flickers exactly the way her module docstring says a bare threshold
  flickers.
- **0.6× return hysteresis: NOT available.** Same reason.
- **But `Custom { detector }` exists**, `&self` + `Send + Sync`, exposed to
  Kotlin as a callback interface. Her `_track_off_route` state machine — the
  `_bad_run` counter and the deadband — ports into a Kotlin
  `RouteDeviationDetector` essentially verbatim. This is the intended escape
  hatch, not an abuse of one.

`android/core/.../FerrostarCore.kt`:

```kotlin
var minimumTimeBeforeRecalculation: Duration = 5.seconds
var minimumMovementBeforeRecalculation = 50.0
```

- **12 s re-route cap: settable.** `minimumTimeBeforeRecalculation = 12.seconds`.
  And the clock is set inside a `finally` block after the route request
  completes (`_lastAutomaticRecalculation = System.nanoTime()`), which is the
  *same* semantics as her `last_route_at`. Exact match, not an approximation.
- There is a second, ANDed gate she does not have:
  `minimumMovementBeforeRecalculation = 50.0` m. Set it to `0.0` to match her
  rule, or keep it deliberately. Known problem: because the movement gate is not
  cleared when a request fails, one hung request can block re-routing for longer
  than the nominal cooldown.
- **`arrived at 25 m`:** handled by `waypointAdvance = WaypointWithinRange(…)`;
  her 25 m goes straight in.

`common/ferrostar/src/navigation_controller/mod.rs` lines 128-132 and the
`SpokenInstruction` model:

```rust
let spoken_instruction = current_route_step
    .get_current_spoken_instruction(progress.distance_to_next_maneuver)
```

with `SpokenInstruction { text, ssml, trigger_distance_before_maneuver: f64, utterance_id: Uuid }`.

- **60/20/6 s prompt bands: NOT expressible as-is.** Ferrostar's prompts fire on
  `trigger_distance_before_maneuver`, a **fixed distance in metres baked into
  each `RouteStep` when the route is built**, re-checked against live distance on
  every fix. Live speed never enters. Her bands are `speed_ms × seconds`,
  recomputed every fix.
- **What that costs:** at route-build time you do not know the rider's speed, so
  you cannot precompute her distances. The fix is not to fight it — it is to
  ignore Ferrostar's spoken-instruction emission entirely, read
  `progress.distance_to_next_maneuver` out of `TripState` on each fix, and run her
  ported `prompt_points(speed_ms)` + `_maybe_say()` against it. That is about
  forty lines and it keeps `guidance.py` as the spec. Her crawl floor and the
  nearest-band-first rule come along for free.
- **3.0 m/s crawl floor: not a concept Ferrostar has.** Ported, per above.
- One thing it gets right for her for free, from the same file: *"Off-route
  should suppress visual + spoken instructions"* — with tests asserting both are
  `None` while completely off-route and resume on return. That is her
  `if not self.off_route and man is not None and not self.arrived` rule, already
  in the core.
- Dedup is also already there: `_queuedUtteranceIds` in `FerrostarCore.kt`, and
  the `SpokenInstructionObserver` contract promises the observer "will never be
  called twice for the same instruction during a navigation session". Same idea
  as her `_said` set, one level up.

### Known problems (open issues, dates verified)

- **#964** "One location update can recurse hundreds of times and crash" —
  **open**, created 2026-09-17. Off the current step but near a much later step
  advances every step in between via nested calls. A crash, in the guidance core,
  nine days old.
- **#967** "Android ui-maplibre: puck and camera freeze on routes that overlap
  themselves" — **open**, updated 2026-09-24. UI layer only, which she would not
  use; navigation state stays correct.
- **#969** "visualInstruction goes null on route deviation, before recalculation
  starts" — **open**, created 2026-09-25. The banner blanks the instant
  deviation is detected, before a new route exists. On a bike that is the moment
  you most want the old instruction left on screen.
- **#875** "0.44.0 deviation detection changes causing misbehaviour on loops and
  overlapping routes" — **closed** 2026-09-25. Root cause is visible in the
  source read above: `StaticThreshold` scans *all* remaining steps and returns
  `OffStepOnRoute` if you are near any of them, so a self-intersecting route may
  never report deviation. Closed, but this is the argument for writing the custom
  detector anyway.
- Pre-1.0, 143 open issues, and releases weekly. Pin the version.

---

## 2. MapLibre Navigation Android

<https://github.com/maplibre/maplibre-navigation-android>

| | |
|---|---|
| Licence | **MIT.** Verified by opening `LICENSE`: "The MIT License (MIT) / Copyright (c) 2024 Kurviger / Copyright (c) 2021 Maplibre". |
| Latest release | **`5.0.0`, 2026-08-26 — stable, not a pre-release.** (The README still advertises `5.0.0-pre13`; it is out of date.) |
| Last commit | 2026-09-08; repo pushed 2026-09-14 |
| Stars / forks / open issues | 205 / 82 / 30 |
| Created | 2019-10-29. Lineage: fork of Mapbox Navigation SDK v0.19, 2018 |
| Android | Yes. `maplibre-navigation-core` `minSdk 23` |
| Fully offline | **Yes** for the guidance core — it is handed a route object, it makes no requests of its own. |
| Speed | Pure Kotlin Multiplatform, no native library, no FFI. `spatialk` geojson/turf/polyline + coroutines + kermit and nothing else. Lightest of the three. |

### The Material question

`maplibre-navigation-core/build.gradle.kts` was read. Dependencies:
`spatialk.geojson`, `spatialk.turf`, `spatialk.polyline`,
`kotlinx.serialization.json`, `kotlinx.coroutines`, `kermit`. **No Compose, no
Material, no Play Services.** The Android-only `libandroid-navigation-ui` module
is legacy Java Views and is skipped, same as Ferrostar's `ui-compose`.

### Bring-your-own-router

`MapLibreNavigation.kt`: `fun startNavigation(directionsRoute: DirectionsRoute)`,
guarded by `ValidationUtils.validDirectionsRoute(directionsRoute, options.defaultMilestonesEnabled)`.

So: **not locked to one router, but locked to one route *format*** — the
Mapbox/OSRM Directions shape, and if default milestones are on, the route must
carry `voiceInstructions` / `bannerInstructions`. Any router that can emit OSRM
JSON works (Valhalla can, on-device, via `valhalla-mobile`'s OSRM output). Her
own ported A\* would have to be taught to emit that shape, or the milestones
turned off and driven manually.

Milder strike than a hard coupling, real strike compared to Ferrostar's
`List<Route>`.

### Against her actual rules — and this is where it gets interesting

`MapLibreNavigationOptions.kt` read in full. Defaults, verbatim from
`object Defaults`:

```
MAX_TURN_COMPLETION_OFFSET = 30.0        MANEUVER_ZONE_RADIUS = 40.0
DEAD_RECKONING_TIME_INTERVAL = 1.0       MAX_MANIPULATED_COURSE_ANGLE = 25.0
USER_LOCATION_SNAPPING_DISTANCE = 10.0   SECONDS_BEFORE_REROUTE = 3
METERS_REMAINING_TILL_ARRIVAL = 40.0     OFF_ROUTE_THRESHOLD_RADIUS_METERS = 50.0
OFF_ROUTE_MINIMUM_DISTANCE_METERS_AFTER_REROUTE = 50.0
OFF_ROUTE_MINIMUM_DISTANCE_METERS_BEFORE_WRONG_DIRECTION = 50.0
OFF_ROUTE_MINIMUM_DISTANCE_METERS_BEFORE_RIGHT_DIRECTION = 20.0
LOCATION_ACCEPTABLE_ACCURACY_IN_METERS_THRESHOLD = 100
FASTER_ROUTE_CHECK_INTERVAL_SECONDS = 120
```

- **45 m threshold: settable** — `offRouteThresholdRadiusMeters = 45.0`.
- **3 consecutive fixes: near-native.** `secondsBeforeReroute = 3` is already 3,
  and at her 1 Hz fix rate three seconds *is* three fixes. Semantically it is a
  clock not a counter, so it behaves differently if fixes drop out — but it is
  the same intent, shipped, with the same number.
- **0.6× return hysteresis: no direct equivalent.**
  `offRouteMinimumDistanceMetersBeforeRightDirection = 20.0` is hysteresis by a
  different mechanism — distance travelled back towards the maneuver, not a
  fraction of the threshold.
- **12 s re-route cap: NOT available as a time.** The gate here is
  `offRouteMinimumDistanceMetersAfterReroute = 50.0` — distance travelled, not
  elapsed seconds. To honour her rule you would gate it yourself upstream.
  **Strike.**
- **Arrival at 25 m: settable** — `metersRemainingTillArrival = 25.0`.
- **Off-route engine is swappable, cleanly.** `offroute/OffRoute.kt` is a
  `fun interface`:
  ```kotlin
  fun interface OffRoute {
      fun isUserOffRoute(location: Location, routeProgress: RouteProgress,
                         options: MapLibreNavigationOptions): Boolean
  }
  ```
  and `MapLibreNavigation.kt` exposes `var offRouteEngine: OffRoute = OffRouteDetector()`.
  A single-method SAM taking a `Location` and returning `Boolean` — her
  `_track_off_route` fits it better than it fits Ferrostar's trait.

**The prompt bands, and the one genuinely surprising find.**
`milestone/TriggerProperty.kt` defines, among others:

```
STEP_DURATION_REMAINING_SECONDS = 0x00000000
STEP_DISTANCE_REMAINING_METERS  = 0x00000001
```

with `Trigger.kt`, `Operation.kt`, `StepMilestone`, `RouteMilestone` and
`MapLibreNavigation.addMilestone(milestone: Milestone)`. **A time-to-maneuver
trigger property exists as a first-class concept.** Her 60/20/6 s bands can be
declared as three `StepMilestone`s rather than reimplemented. No other candidate
offers this.

The catch, and it is a real one. `routeprogress/RouteStepProgress.kt`:

```kotlin
val durationRemaining: Double
    get() = (1 - fractionTraveled) * step.duration
```

That is the **router's** duration for the step scaled by progress along it — not
`distance / current_GPS_speed`. So a rider doing 70 mph on a step the router
costed at 45 mph gets prompted late; her formula would not. Conversely it does
*not* blow up when stopped at a light, so her `CRAWL_MS` floor is less needed
here. Either way it is not her spec, and getting her spec means a custom
`Milestone` over `STEP_DISTANCE_REMAINING_METERS` computing the threshold from
live speed.

### Known problems

- README documents a version two releases stale; trust the releases page.
- KMP conversion is mid-flight — recent commits are iOS-side. Churn risk.
- 0.19-era Mapbox internals underneath, which is a decade of accumulated Java in
  the UI module (skipped) and a `DirectionsRoute` model shaped by Mapbox's API.
- Legacy sibling `maplibre-navigation-android-legacy` is **archived** (MIT, 37
  stars, last pushed 2022-02-22). Do not start there.

---

## 3. GraphHopper navigation example

<https://github.com/graphhopper/graphhopper-navigation-example> (default branch `graphhopper`)

| | |
|---|---|
| Licence | **MIT.** Verified by opening `LICENSE`: "Copyright (c) 2021 Maplibre / Copyright (c) 2019 Flitsmeister". |
| Last push | 2026-05-26 |
| Stars / forks / open issues | 41 / 6 / 1 |
| Created | 2023-11-25 |
| Language | Kotlin |
| Android | Yes |
| Fully offline | **Unverified — and the README does not claim it.** It was opened and read; it says nothing about offline operation or API keys either way. |

It is **a demo app plus a Capacitor plugin, not a library.** The README's own
framing, quoted: *"this code can be used as a start for a kotlin multiplatform
integration without any capacitor dependency"*, and *"the latest Maplibre
Navigation SDK is KMP-ready too this code can be easily ported to iOS"*.

So it is not a third option — it is **a worked example of option 2**, and its
README also notes GraphHopper can be used with Ferrostar, i.e. an example of
option 1 too. Its value is as reading, and as evidence that MapLibre Nav 5.x is
being wired up by someone other than MapLibre.

Its legacy sibling `graphhopper-navigation-example-legacy` is **archived**
(Apache-2.0, 92 stars, last pushed 2023-11-25). Out.

**Out as a pick, in as reading.** No guidance logic of its own to evaluate
against her rules.

---

## 4. Port her own `guidance.py`

No repository, no licence question, so the table is different.

| | |
|---|---|
| Android | Yes — pure Kotlin, no dependencies |
| Fully offline | Yes, by construction. It already is. |
| Speed | Fastest possible. It is arithmetic on one fix. No FFI, no JNA, no native `.so`. |
| Fit with her rules | **Perfect, definitionally.** It *is* the rules. |
| Router coupling | **None.** It consumes `(route, position, speed_ms)`. Anything can supply those. |

**What it already has, that no library has:** the nearest-band-first prompt walk
with wider-band suppression; the crawl floor; the 0.6× deadband; the
`_said.clear()` on re-route (whose docstring records the bug it fixes: a
re-routed rider never being told about the turn they are now approaching, because
a maneuver with the same index was already announced on the replaced route). That
last one is the kind of detail a library will not have and a rewrite will forget.

**What it does not have, and would have to be built:** nothing. There are tests
(`tests/test_core.py` exercises `should_reroute`). Port the tests, port the code.

**The real cost is elsewhere.** `guidance.py` does not do map-matching, snapping
the puck to the line, step advance, or the foreground-service plumbing that keeps
guidance alive when the screen sleeps. It leans on `agedashii_washi.routing`
(`progress`, `next_maneuver`, `remaining`) for all of it. A from-scratch port
means porting that too, or getting it from whatever wins section 5.

**Known problem:** `routing.py`'s A\* is the untested assumption here. Ferrostar
and MapLibre Nav both bring snapping and step advance that are battle-tested on
real roads with real GPS noise; her `progress()` is a Python A\* helper that has
seen her rides and no one else's.

---

## 5. Others found, and why each is out

| Name | Link | Licence (opened) | Activity | Verdict |
|---|---|---|---|---|
| **navmaster** | <https://github.com/francescobuzle-a11y/navmaster> | **NONE. No LICENSE file anywhere in the repo; GitHub's `license` field is `null` and `repos/…/license` returns 404** | pushed 2026-09-26, 0 stars | **RULED OUT AS CODE — hard disqualifier.** This settles the biggest open question in `00-already-checked.md`, which flagged its licence as unconfirmed and said "everything else about it is moot if it is not licensed for reuse". It is not. All rights reserved by default: it cannot be forked or vendored. Still the best available *reading* on Ferrostar + on-device Valhalla + PMTiles, same standing as Ride-Track. |
| **kettenblatt** | <https://github.com/Kellenintemperate373/kettenblatt> | **Apache-2.0** (opened) | created 2026-08-07, pushed 2026-09-25, 1 star | Out as a guidance option. Genuinely offline (Valhalla map-matching + MBTiles + osmdroid), auto-recalculates when you stray, Kotlin + Compose — but it is a **GPX-follower with no voice and no TTS at all**, and osmdroid is raster, against the §4 pick. Useful as the second Apache-licensed worked example of offline Valhalla map-matching on Android. |
| **Kblack / map-offline-navigation** | <https://github.com/khoa083/map-offline-navigation> | **GPL-3.0** (opened — this was listed as unverified in `00-already-checked.md`, now confirmed) | — | Unchanged verdict: Vietnam-only, commits to GraphHopper. GPL-3.0 now confirmed, which is a second reason to keep it as reading only. |
| **astro-phage/ferrostar-valhalla** | <https://github.com/astro-phage/ferrostar-valhalla> | BSD-3-Clause — byte-identical `LICENSE.txt` to Ferrostar's, i.e. an unmodified fork | last pushed **2025-04-30**, ~17 months stale, 1 star, 0 forks | Out. A stale personal fork of Ferrostar with a suggestive name. Use upstream. |
| **nextbillion-ai/offline-navigation-demo-android** | <https://github.com/nextbillion-ai/offline-navigation-demo-android> | **Proprietary.** `LICENSE.md` opened: "Copyright © NextBillion.ai - All rights reserved… licensed under the NextBillion.ai TOS for use only with the relevant product(s)… This license terminates automatically if a developer no longer has a NextBillion.ai account in good standing" | created 2026-09-22 | **Out.** Requires an active commercial account; licence terminates with it; modifying the billing/telemetry code is explicitly unauthorised. Fails the offline-forever rule at the legal layer, never mind the technical one. |
| **luish88/Turn-by-Turn-Navigation-Module-for-Android** | <https://github.com/luish88/Turn-by-Turn-Navigation-Module-for-Android> | MIT | **last pushed 2019-05-09** — 7 years dead, 14 stars | Out on abandonment. Interesting only for the idea (offline fallback when the remote API is unreachable, ships a Portugal graph). |
| **Project-OSRM/osrm-text-instructions.java** | <https://github.com/Project-OSRM/osrm-text-instructions.java> | **BSD-2-Clause** (opened) | **last pushed 2020-06-15** — 6 years dead, 13 stars | Out. It solves the phrasing half (OSRM route → localised instruction text), which is real work — but it is abandoned, and it is redundant: Valhalla generates its own narrative offline (see §7), and `guidance.spoken()` already does her phrasing. |
| **mapzen/on-the-road_android** | <https://github.com/mapzen/on-the-road_android> | Apache-2.0 (opened) | last pushed 2020-07-07, 51 stars | Out. Client for the defunct hosted Mapzen Turn-by-Turn service, not an on-device engine. |
| **Graphmasters/nunav-navigation-sdk-example** | <https://github.com/Graphmasters/nunav-navigation-sdk-example> | Apache-2.0 (example code) | pushed 2025-10-14, 4 stars | Out. Examples only; the SDK itself is a closed commercial product tied to the Graphmasters routing service. Hard router coupling plus a network dependency. |
| **amias-samir/GraphHooperMapRouteNavigation** | <https://github.com/amias-samir/GraphHooperMapRouteNavigation> | BSD-3-Clause | pushed 2025-06-16, 17 stars | Out. Flutter plugin wrapping MapLibre + GraphHopper. Wrong language layer for a Kotlin native rebuild, and couples to GraphHopper. |
| **OsmAnd** | <https://github.com/osmandapp/OsmAnd> | **GPLv3 for code + CC-BY-NC-ND 4.0 for artwork** (`LICENSE` opened; it also warns "some artwork uses a proprietary license") | pushed 2026-09-26, 6040 stars, 3515 open issues | Out as a library — it is an application, not an SDK, and the artwork licensing is a trap for anything redistributed. **But its voice-prompt algorithm is the single most valuable document found in this whole section.** See §7. |
| **Organic Maps** | <https://github.com/organicmaps/organicmaps> | Apache-2.0 (opened) | pushed 2026-09-26, 15506 stars | §1's business, not §6's. Its guidance is welded into a C++ app core; there is no extractable guidance library. |

Also checked and rejected from the topic-page sweeps without needing a licence
read, because they fail on category: `graphhopper/graphhopper-maps` (web route
planner UI), `ffwdme.js` (deprecated, JavaScript), `3v1n0/GMapsParser` (scrapes
Google Maps notifications — needs Google Maps, therefore needs signal),
`2gis/dgis_mobile_sdk_*` (Flutter, commercial), `vietmap-*` (Flutter, commercial
tiles), `googlemaps/flutter-navigation-sdk` (Google, online),
`Andriiseer/react-native-here-maps` (HERE, commercial), `nico359/loci` (GPL-3,
mobile Linux, "vibe coded" per its own description), `scriptwriter13/bikenavi_esp32`
and `OrangeBaron/eink_maps` (ESP32 display firmware — relevant to §8 hardware if
ever, not §6).

---

## 6. The compatibility question, side by side

Section 5 is undecided, so what each option demands of the router is the
deciding axis.

| Option | What it accepts as a route | Router lock-in |
|---|---|---|
| **Ferrostar** | `List<Route>` from any `suspend fun getRoutes(...)` you write. Helpers exist for OSRM-shaped JSON but are optional. | **None.** Verified in docs and source. Works with on-device Valhalla, GraphHopper, OSRM, or her ported A\*. |
| **MapLibre Nav Android** | `DirectionsRoute` — the Mapbox/OSRM Directions object, validated on entry, needing `voiceInstructions`/`bannerInstructions` if default milestones are on. | **Format lock-in, not vendor lock-in.** Any router that emits OSRM JSON is fine; her own A\* would need teaching. |
| **GraphHopper example** | n/a — demo app | n/a. Its *own* code path is GraphHopper, but it is not a library. |
| **Port `guidance.py`** | `(route, position, speed_ms)` | **None.** It is her own interface. |

---

## 7. Voice

### 7a. Does Android TextToSpeech genuinely work offline?

**Yes, and here is the authoritative test.** Read from AOSP source
(`aosp-mirror/platform_frameworks_base`, `core/java/android/speech/tts/`), not
from a blog:

`Voice.java`:

```java
/** @return Does the Voice require a network connection to work. */
public boolean isNetworkConnectionRequired() { ... }
```

Quality and latency constants, with AOSP's own javadoc:

| Constant | Value | Javadoc |
|---|---|---|
| `QUALITY_VERY_LOW` … `QUALITY_VERY_HIGH` | 100, 200, 300, 400, 500 | very low → "almost human-indistinguishable" |
| `LATENCY_VERY_LOW` | 100 | "Very low expected synthesizer latency (< 20ms)" |
| `LATENCY_LOW` | 200 | "Low expected synthesizer latency (~20ms)" |
| `LATENCY_NORMAL` | 300 | "Normal expected synthesizer latency (~50ms)" |
| `LATENCY_HIGH` | 400 | **"Network based expected synthesizer latency (~200ms)"** |
| `LATENCY_VERY_HIGH` | 500 | "Very slow network based expected synthesizer latency (> 200ms)" |

So latency itself is a tell: `LATENCY_HIGH` and above are documented as network
voices.

`TextToSpeech.java`, the feature keys that matter:

- `KEY_FEATURE_NOT_INSTALLED = "notInstalled"` — **the trap.** Its javadoc:
  until the download completes, "each synthesis request will either report
  `ERROR_NOT_INSTALLED_YET` error, **or use a different voice to synthesize the
  request**". A voice can be listed by `getVoices()` and still not be on the
  device. Silent substitution, not an exception.
- `KEY_FEATURE_NETWORK_SYNTHESIS = "networkTts"` and
  `KEY_FEATURE_EMBEDDED_SYNTHESIS = "embeddedTts"` — both **deprecated at API
  21**, with the javadoc redirecting to `Voice.isNetworkConnectionRequired()`.
  Any tutorial still using these is pre-2014.
- `KEY_FEATURE_NETWORK_TIMEOUT_MS`, `KEY_FEATURE_NETWORK_RETRIES_COUNT` —
  irrelevant here except as further proof that network voices are a distinct
  class.

**The correct offline-voice test, therefore:**

```
voice.isNetworkConnectionRequired() == false
  && !voice.getFeatures().contains(Engine.KEY_FEATURE_NOT_INSTALLED)
```

run over `getVoices()` **at app start, not mid-ride**, with the chosen voice
pinned via `setVoice(Voice)` — never `setLanguage(Locale)`, which lets the engine
pick and may hand back a network voice. If nothing passes, say so on screen
before she sets off rather than going silent in a canyon.

**How voice data is installed.** Two paths:

- By hand: Settings → System → Languages & input → Text-to-speech output → cog
  next to the preferred engine → *Install voice data*.
- Programmatically: `ACTION_CHECK_TTS_DATA` returns `EXTRA_AVAILABLE_VOICES` and
  `EXTRA_UNAVAILABLE_VOICES` (formatted `lang-COUNTRY-variant`, e.g.
  `eng-USA-FEMALE`); `ACTION_INSTALL_TTS_DATA` launches the engine's installer.
  Installation can be declined or interrupted, so re-check with
  `ACTION_CHECK_TTS_DATA` afterwards — never assume success on return.
  *(These two intents and the extras come from the Android docs surfaced in
  search, not from the AOSP source I opened — treat as documented-but-not-source-verified
  here.)*

**Which engines.** Licences all opened via the GitHub licence API and confirmed:

| Engine | Licence | Stars | Last push | Note |
|---|---|---|---|---|
| eSpeak NG | **GPL-3.0** | 6885 | 2026-09-22 | Tiny, ~100 languages, robotic. The bootstrap engine on GMS-free ROMs. On F-Droid as `com.reecedunn.espeak`. |
| RHVoice | **GPL-2.0** (not GPL-3, as is often claimed) | 1844 | 2026-09-21 | More natural than eSpeak, far fewer languages. **This is what CoMaps officially recommends.** Known failure: on LineageOS-type ROMs it can fail to init and crash the host app if no TTS engine has ever been used — the documented fix is to install eSpeak first and select it as preferred engine. |
| `rhasspy/piper` | MIT, **ARCHIVED** | 11291 | 2025-08-26 | The permissively-licensed Piper is frozen. |
| `OHF-Voice/piper1-gpl` | **GPL-3.0** | 5691 | 2026-09-17 | Where Piper maintenance went. Neural, local, good. GPL-3. The Open Home Foundation is seeking maintainers. |
| `k2-fsa/sherpa-onnx` | **Apache-2.0** | 14981 | 2026-09-22 | **The licence-clean route to neural offline TTS.** Runs Piper/VITS/Kokoro ONNX models on Android. |
| SherpaTTS (`woheller69/ttsEngine`) | GPL-3.0 | 790 | 2026-07-20 | Registers sherpa-onnx as a system TTS engine. |
| NekoSpeak | MIT | 172 | 2026-05-19 | Ships Piper + a voice, ~135 MB universal APK. |

**The licence point that decides this, and it is not obvious.** Bundling eSpeak
NG, RHVoice, Piper-GPL or SherpaTTS *inside her APK* pulls GPL onto the whole
app. Having the rider install one as a **separate system TTS engine app** does
not: Android's TTS engine interface is IPC across a process boundary to a
separately-distributed program, so there is no linking and no contamination. Her
app just names the engine package. Ferrostar already supports exactly this —
`AndroidTtsObserver(context, engine = "com.reecedunn.espeak")` — the `engine`
constructor parameter is a package name, verified in `Speech.kt`.

If she wants a good voice *bundled*, **sherpa-onnx (Apache-2.0) is the only
candidate that does not force a licence change.**

### 7b. Bundled audio clips

Verified against the only 15-year production implementation of the idea, OsmAnd
(both its technical contributor doc and its user doc were opened).

How it works: one config file per language, `voice/xx[-yy]-tts/xx[-yy]_tts.js`,
containing vocabulary, word order and grammar. The same script has an output
selector toggled by `setMode(tts)` that either returns **a string** (for a TTS
engine) or **a sequence of .ogg filenames** (for clip playback). Recorded voices
additionally need a `voice/` subfolder of .ogg files.

OsmAnd's own verdict on clips, and this is decisive:

- In the download list they are labelled **"Voice prompts (recorded, limited features)"**.
- They are **"limited to pre-recorded phrases and cannot pronounce specific names
  or numbers"**, providing **"only basic turn instructions"**.
- The docs recommend them **"only as a fallback if your device is not capable of
  supporting TTS"**, and maintainers have discussed relabelling them "Fallback
  voices (Pre-recorded)".
- A contributor notes the clips are themselves generated by requesting an online
  TTS service — which is why they do not sound natural either.

**Applied to her spec this is fatal.** `guidance.spoken()` produces
*"In 400 ft, turn left onto Grand"*. That is a **number** (400) and a **street
name** (Grand) — the two things clips provably cannot say. Clips would force her
to throw away `distance_text()` and the `onto {street}` clause, i.e. to downgrade
the one part of the guidance layer that is finished and correct.

**Verdict: bundled clips are a fallback for a phone with no working TTS engine,
not a design. Not the primary path.** If wanted later, the OsmAnd file layout is
the pattern to copy, and Ferrostar's `SpokenInstructionObserver` is the seam
(§7c).

### 7c. The seam, whichever wins

`android/core/.../Speech.kt` read in full. `SpokenInstructionObserver` is an
interface with `onSpokenInstructionTrigger(SpokenInstruction)`,
`stopAndClearQueue()`, `setMuted(Boolean)`, `muteState: StateFlow<Boolean>`.
`AndroidTtsObserver` is one implementation of it.

So bundled clips, a Piper/sherpa-onnx path, or anything else is a class
implementing that interface. The plumbing is not the hard part in any option.

Two details from that file worth carrying into whatever gets built:

- **`AudioFocusManager` is already there**, requesting focus per utterance and
  releasing it 500 ms after the last one via a timer, and stopping speech on
  `AUDIOFOCUS_LOSS`. On a bike with music in the helmet this is not a nicety, and
  it is the sort of thing a from-scratch port forgets until the first ride.
- **It speaks with `TextToSpeech.QUEUE_ADD`, not `QUEUE_FLUSH`.** A prompt can
  therefore play *behind* a still-speaking earlier one and land after the turn.
  Her nearest-band-first rule mitigates this by never queueing two bands for the
  same maneuver, but it is a live hazard for back-to-back maneuvers and argues
  for `QUEUE_FLUSH` on the `"now"` band.

### 7d. OsmAnd's prompt-timing algorithm — the best cross-check on her spec

`osmand.net/docs/technical/algorithms/voice-prompt-triggering/` opened and read.
OsmAnd computes `lead distance = lead time (s) × profile default speed (m/s)`,
then for close-up prompts scales that by `actual speed / default speed`. Driving
default speed is 12.5 m/s (45 km/h).

| OsmAnd prompt | Lead time | Distance @ default | Notes |
|---|---|---|---|
| Prepare to turn | 115 s | 1438 m | skipped if < 150 m before "turn in", or default speed < 8 km/h |
| Turn in X m | 22 s | 275 m | skipped if < 15 s before the turn |
| Turn now | `max(8, √(default speed km/h))` s | 83 m | floor 12 m |
| Arrival | 5 s | 63 m | floor 12 m |

Plus: a 12 m floor everywhere "to account for position inaccuracy"; arrival
multipliers Early 1.5× / Normal 1× / Late 0.5× / Very late 0.25×; a "long prepare"
at 300 s only for default speeds above 108 km/h; "go ahead" filler when gaps
exceed 300 s.

**Against her numbers:**

| | Hers | OsmAnd | Read |
|---|---|---|---|
| far / prepare | **60 s** | 115 s | Hers is half. Defensible — she has a visual banner OsmAnd cannot assume — but it is the one band where she is the outlier. |
| near / turn in | **20 s** | 22 s | Independent convergence on the same number by two unrelated projects. Strong validation. |
| now | **6 s** | ~6.7–8 s | Same. Strong validation. |
| slow-speed floor | `< 3.0 m/s` → fixed 250/90/25 m | scale by actual/default + 12 m floor, skip prepare below 8 km/h | Different mechanism, same intent. Her 25 m "now" floor is more conservative than OsmAnd's 12 m. |

**Two things OsmAnd has that her spec does not, and both are worth stealing:**

1. **Utterance-length correction.** OsmAnd has a configurable "voice prompt
   delay" specifically to stop TTS being clipped by phone audio routing, and the
   patent literature surfaced in the same search describes the stronger version:
   shift playback so the *end* of the phrase lands at the intended point, adding
   `utterance_duration × speed` to the trigger distance. `"In 400 ft, turn left
   onto Grand"` takes ~2.5 s to say; at 70 mph that is 78 m. Her bands trigger on
   the phrase's *start*.
2. **Active staleness muting.** OsmAnd "mutes prompts immediately once they refer
   to outdated events" or when travel direction stops matching the route. Her
   `_said` set prevents a prompt repeating; it does not cancel one already
   speaking that has become wrong. Combined with `QUEUE_ADD` above, this is the
   gap to close.

### 7e. Where the instruction *text* comes from, offline

Worth settling because it is the half of "voice" that is not TTS.

Valhalla generates its own narrative on-device. `valhalla/valhalla` is **MIT** —
verified by opening `LICENSE.md`, which points at `COPYING`, which is
"The MIT License (MIT) / Copyright (c) 2018 Valhalla contributors". Its Odin
component builds maneuvers and narrative from per-locale JSON
(`locales/en-US.json`), and those files are **compiled into the binary** — the
repo has `cmake/ValhallaBin2Header.cmake` and a `locales_json` symbol in
`src/odin/util.cc`. So a `valhalla-mobile` `.so` carries the en-US narrative
dictionary; street names come from the routing tiles. Valhalla emits several
verbal variants per maneuver (succinct transition, transition alert,
pre-transition, post-transition, multi-cue).

Consequence: **if section 5 picks Valhalla, the instruction strings are already
offline and already localised, and `osrm-text-instructions` is unnecessary.** Her
`spoken()` then chooses between Valhalla's phrasing and her own; her own is
better tuned for a helmet ("now" drops the distance) and should win.

`Rallista/valhalla-mobile` itself: **MIT**, 78 stars, 51 forks, last pushed
2026-09-06.

---

## 8. Rule-by-rule conformance matrix

`set` = a public setting exists. `port` = must be supplied as a custom
implementation, and the library provides the interface to do so. `no` = no seam.

| Her rule | Ferrostar | MapLibre Nav | Port `guidance.py` |
|---|---|---|---|
| Prompt bands 60/20/6 **s** of travel | **port** — prompts are fixed metres per step (`trigger_distance_before_maneuver`); drive TTS off `TripState.distance_to_next_maneuver` instead | **port**, but `TriggerProperty.STEP_DURATION_REMAINING_SECONDS` exists — nearest native fit of anything found; caveat, it is the router's duration not live GPS speed | **native** |
| Crawl floor 3.0 m/s → 250/90/25 m | **port** | **port** | **native** |
| Off-route 45 m | **set** `max_acceptable_deviation = 45.0` | **set** `offRouteThresholdRadiusMeters = 45.0` | **native** |
| Confirmed over 3 consecutive fixes | **port** via `Custom { detector }` | **near-native** — `secondsBeforeReroute = 3`, already 3; seconds not fixes | **native** |
| Return hysteresis 0.6× | **port** via same detector | **port** via `fun interface OffRoute`; its own `…BeforeRightDirection = 20.0` is a different mechanism | **native** |
| Re-route ≤ 1 per 12 s | **set** `minimumTimeBeforeRecalculation = 12.seconds`, and its clock starts at request *completion* — same semantics as `live.py` | **no** — the gate is `…AfterReroute = 50.0` **metres**; gate it yourself upstream | **native** |
| Arrival at 25 m | **set** `waypointAdvance` | **set** `metersRemainingTillArrival = 25.0` | **native** |
| Prompt fires once, nearest band first | already dedupes by `utteranceId`; the nearest-first walk is **port** | **port** | **native** |
| Off-route suppresses prompts | **native** — in the Rust core, with tests | **port** | **native** |
| Not Material | **yes**, if you take `:core` only (`ui-compose` is Material 3) | **yes** — `maplibre-navigation-core` has no Compose or Material at all | **yes** |
| Works with no signal | **yes** via `CustomRouteProvider` | **yes** — it is handed a route and makes no requests | **yes** |

Neither library lets her express the time-based bands natively against live GPS
speed. Both let her plug in every off-route rule she has. Ferrostar wins the
re-route cap outright; MapLibre Nav loses it outright.

---

## 9. Comparison table

| | Ferrostar | MapLibre Nav Android | GraphHopper example | Port `guidance.py` |
|---|---|---|---|---|
| Licence (file opened) | **BSD-3-Clause** | **MIT** | **MIT** | n/a |
| Latest release / commit | 0.57.0, 2026-09-22 / 2026-09-22 | 5.0.0 stable 2026-08-26 / 2026-09-08 | — / 2026-05-26 | n/a |
| Stars / forks / open issues | 428 / 81 / 143 | 205 / 82 / 30 | 41 / 6 / 1 | n/a |
| Android | Yes, minSdk 25 | Yes, minSdk 23 | Yes | Yes |
| Fully offline | Yes | Yes | **Unverified; README silent** | Yes |
| Speed | Rust core, uniffi/**JNA** bridge | Pure Kotlin KMP, **no native, no FFI** | n/a | Arithmetic |
| Material-free path | `:core` only | core has none | n/a | Yes |
| Router lock-in | **None** (verified) | Format: OSRM/Mapbox `DirectionsRoute` | n/a (demo) | None |
| Her rules: set / port / no | 5 set, 5 port, 0 no | 4 set, 6 port, **1 no** | n/a | all native |
| Biggest problem | Pre-1.0, weekly releases, **#964 open crash in guidance core** | Time triggers use router duration not GPS speed; **no time-based re-route cap**; KMP churn; stale README | Not a library | No map-matching, no snapping, no step advance — leans on an untested A\* |

---

## 10. Complete search log

Fourteen web searches and four GitHub repository searches. Every licence,
release date, star count, commit date and default value quoted in this document
was read from the file or API response named — the search summaries were used for
discovery and for the Android-docs items explicitly marked as such.

### Search 1 — `ferrostar custom route provider` (the `search:` line from CHOICES.md §6)

| # | Result | In / out |
|---|---|---|
| 1 | stadiamaps.github.io/ferrostar/route-providers.html | **IN. Opened and read in full.** The primary evidence for bring-your-own-router. |
| 2 | docs.rs/ferrostar/latest/ferrostar/routing_adapters | IN. Confirms `RouteAdapter` vs `CustomRouteProvider` split. |
| 3 | stadiamaps.com/products/routing-navigation/ferrostar-navigation-sdk/ | Out — vendor marketing page; no technical facts not better sourced. |
| 4 | stadiamaps.github.io/ferrostar/ios-getting-started.html | Out — iOS. Confirms the same custom-provider initialiser exists there. |
| 5 | stadiamaps.github.io/ferrostar/android-getting-started.html | **IN. Opened.** Source of the module list and the Gradle block. |

### Search 2 — `maplibre-navigation-android` (the second `search:` line)

| # | Result | In / out |
|---|---|---|
| 1 | github.com/maplibre/maplibre-navigation-android README | **IN.** Repo opened via API; README's version numbers found to be stale. |
| 2 | maplibre.org/news/2023-03-27-android-navigation-version-2/ | IN as background — records that you can integrate your own routing backend, Kurviger cited as a custom implementation. |
| 3 | maplibre/maplibre-navigation-android-legacy | **OUT — archived** 2022-02-22, MIT, 37 stars. Do not start here. |
| 4 | CHANGELOG.md (main) | IN. Records the UI removal and the Mapbox→MapLibre resource rename. |
| 5 | Releases page | **IN.** The single most useful hit: 5.0.0 stable shipped 2026-08-26, contradicting the README. |

### Search 3 — `offline turn by turn navigation android open source library kotlin`

| # | Result | In / out |
|---|---|---|
| 1 | docs.here.com HERE SDK navigation | Out — commercial closed-source. |
| 2 | igalmarino/vialix | Out — already covered in `00-already-checked.md` (offline not implemented, GPL-3-or-later). Not re-derived. |
| 3 | **Kellenintemperate373/kettenblatt** | **NEW. IN as reference** — README opened; Apache-2.0 opened; offline Valhalla map-matching but no voice at all. |
| 4 | maplibre/maplibre-navigation-android | Duplicate of search 2. |
| 5 | github.com/topics/offline-maps?l=kotlin | IN as a browsing surface; already listed in `00-already-checked.md`. |

### Search 4 — `android TextToSpeech offline voice data install which voices work without internet`

| # | Result | In / out |
|---|---|---|
| 1 | Play Store "Offline - Text To Speech" | Out — consumer app, not an API fact. |
| 2 | learn.microsoft.com `TextToSpeech.Engine.ActionCheckTtsData` | **IN** — source of `ACTION_CHECK_TTS_DATA`, `EXTRA_AVAILABLE_VOICES`, `EXTRA_UNAVAILABLE_VOICES` and the `lang-COUNTRY-variant` format. Marked in §7a as documented-not-source-verified. |
| 3 | learn.microsoft.com `ActionInstallTtsData` | **IN** — the installer intent, and the warning not to assume success on return. |
| 4 | micmonster.com "Does Google TTS work offline?" | **OUT — wrong.** Claims Google TTS "cannot work offline". Contradicted by AOSP source and by every shipping reader app. Recorded here so it is not believed later. |
| 5 | MIT App Inventor "TextToSpeech stop working after Android 11" | Out as a source, in as a symptom: corrupted voice data after an OS update, fixed by reinstalling it. Argues for the startup check in §7a. |

This search's weakness (blogs, not docs) is why §7a was rebuilt from AOSP source
instead. `developer.android.com/reference/android/speech/tts/Voice` was fetched
and returned only the navigation shell; `learn.microsoft.com/…/android.speech.tts.voice`
failed outright. `aosp-mirror/platform_frameworks_base` was then read directly.

### Search 5 — `graphhopper navigation example android maplibre` (§6 candidate)

| # | Result | In / out |
|---|---|---|
| 1 | graphhopper/graphhopper-navigation-example | **IN. README and LICENSE opened.** Demo + Capacitor plugin, MIT, not a library. |
| 2 | README on branch `graphhopper` | **IN, opened.** Silent on offline and API keys; explicitly frames itself as a starting point. |
| 3 | graphhopper-navigation-example-legacy | **OUT — archived**, Apache-2.0, 92 stars. |
| 4 | CHANGELOG (branch `graphhopper`) | Out — nothing decision-relevant. |
| 5 | `com.graphhopper.navigation:navigation-android` → maplibre legacy | IN as lineage: GraphHopper's fork became MapLibre's. Notes GraphHopper ships no tiles and recommends MVT vector for mobile — consistent with the §4 pick. |

### Search 6 — `ferrostar off route deviation detection configuration threshold`

| # | Result | In / out |
|---|---|---|
| 1 | ferrostar issue **#875** | **IN.** Verified via API: **closed** 2026-09-25. Loops/overlapping routes; the whole-route scan means deviation may never fire. |
| 2 | vialix PR #5 "faster, more reliable rerouting" | **IN** — a real-world tuning table (40/30 driving, 30/20 cycling, 30/25 walking) and the argument-order foot-gun. |
| 3 | stadiamaps.github.io/ferrostar/configuring-the-navigation-controller.html | **IN. Opened and read in full.** |
| 4 | ferrostar issue **#964** | **IN.** Verified: **open**, 2026-09-17. Recursion crash. The most serious finding against Ferrostar. |
| 5 | ferrostar issue **#969** | **IN.** Verified: **open**, 2026-09-25. Banner blanks on deviation before recalculation. |

Followed by reading `common/ferrostar/src/deviation_detection.rs` and
`navigation_controller/mod.rs` directly, because the docs page omitted the
`StaticThreshold` field names and the absence of any confirm-count.

### Search 7 — `github topics turn-by-turn-navigation android offline repos`

| # | Result | In / out |
|---|---|---|
| 1 | github.com/topics/turn-by-turn | IN as a surface; swept properly via `gh search` below. |
| 2 | github.com/topics/turn-by-turn-navigation | IN as a surface; swept properly below. |
| 3 | **luish88/Turn-by-Turn-Navigation-Module-for-Android** | **OUT** — MIT, but last push 2019-05-09. Seven years dead. |
| 4 | **nextbillion-ai/offline-navigation-demo-android** | **OUT** — `LICENSE.md` opened: proprietary NextBillion TOS, terminates with the account. |
| 5 | Project-OSRM/osrm-backend issue #440 "turn-by-turn navigation?" | Out — 2015 discussion thread; superseded. |

### Search 8 — `open source offline TTS android espeak-ng RHVoice Piper apk voice guidance navigation`

| # | Result | In / out |
|---|---|---|
| 1 | RTranslator issue #93 suggesting eSpeak + RHVoice | IN — corroborates the eSpeak-as-bootstrap / RHVoice-as-voice pattern. |
| 2 | voiceping-ai/android-offline-tts-eval | Out — not opened; an eval harness, not an engine. **Unverified.** |
| 3 | siva-sub/NekoSpeak | IN — MIT, 172 stars, ~135 MB APK. Licence and metadata verified via API. |
| 4 | OHF-Voice/piper1-gpl | **IN** — GPL-3.0 verified; the MIT `rhasspy/piper` is archived. The licence fork matters. |
| 5 | **comaps.app/support/tts-configuration-guide-for-android/** | **IN, and the most useful hit** — CoMaps officially recommends RHVoice, and documents the LineageOS init-crash workaround (install eSpeak first). |

### Search 9 — `valhalla offline navigation android turn instructions library ferrostar valhalla-mobile`

| # | Result | In / out |
|---|---|---|
| 1 | **francescobuzle-a11y/navmaster** | **RULED OUT AS CODE.** Repo contents listed via API: no LICENSE, no COPYING, no NOTICE anywhere in the root; `license` field `null`; `repos/…/license` returns 404. This closes the biggest open question in `00-already-checked.md`. |
| 2 | navmaster release pages (app-62, app-30, app-20 …) | Out individually; together they confirm active CI releases. Moot given the licence. |
| 3 | **astro-phage/ferrostar-valhalla** | **OUT** — byte-identical `LICENSE.txt` to Ferrostar's; a stale unmodified fork, last pushed 2025-04-30. |
| 4 | **Rallista/valhalla-mobile** | **IN** — MIT verified, 78 stars, pushed 2026-09-06. The seam: on-device Valhalla, OSRM output format, `trace_route`/`trace_attributes`. |
| 5 | mapzen/on-the-road_android | **OUT** — Apache-2.0 opened, but a client for a service that shut down; last push 2020-07-07. |

### Search 10 — `OsmAnd recorded voice prompts vs TTS voice navigation offline which languages bundled clips`

| # | Result | In / out |
|---|---|---|
| 1 | **osmand.net/docs/technical/contributions/voice-prompts/** | **IN. Opened.** The `_tts.js` / `setMode(tts)` / .ogg packaging, and "only as a fallback if your device is not capable of supporting TTS". |
| 2 | osmand.net/docs/user/navigation/guidance/voice-navigation/ | IN — the user-facing label "Voice prompts (recorded, limited features)". |
| 3 | **OsmAnd issue #17683 "Favor TTS over Recorded voices"** | **IN** — maintainers moving to relabel recorded voices as "Fallback". The clearest statement of direction. |
| 4 | wiki.openstreetmap.org "Record your voice for OsmAnd" | IN — the contributor note that clips are themselves generated from an online TTS service. |
| 5 | boringphone.com OsmAnd voice setup | IN as the counter-case: a GMS-free phone that cannot do TTS and therefore must use clips. The one scenario where clips win. |

### Search 11 — `awesome list openstreetmap navigation libraries android awesome-osm awesome-maps`

| # | Result | In / out |
|---|---|---|
| 1 | **osmlab/awesome-openstreetmap** | IN — the canonical list. App- and tool-oriented; contains no §6 guidance library not already found here. |
| 2 | xoellijo/awesome-openstreetmap | Out — a fork of #1. |
| 3 | trackawesomelist.com mirror | Out — a mirror with diffs. |
| 4 | **OSM wiki "Comparison of Android applications"** | IN as a surface for §1; nothing new for §6. |
| 5 | maplibre/awesome-maplibre (surfaced in search 13) | IN — lists Ferrostar and MapLibre Navigation Android as the two navigation entries. Confirms the shortlist is the shortlist. |

**Conclusion of the awesome-list sweep: no fifth guidance option exists.** The
field is Ferrostar, MapLibre Navigation Android, a fork of a whole app, or your
own code.

### Search 12 — `navigation app voice prompt timing seconds before turn instead of fixed distance speed adaptive`

| # | Result | In / out |
|---|---|---|
| 1 | osmand.net/docs/user/navigation/guidance/voice-navigation/ | IN — user-side view of the same algorithm. |
| 2 | **pixeldrift/ShortStop PR #85 "Time-based navigation prompting foundation"** | IN — an independent two-tier design (~12 s warning + ~4 s terse confirmation) that degrades to fixed distance when GPS is unusable. Corroborates her approach. |
| 3 | Ride with GPS "Voice Navigation" | IN — chimes 10–12 s ahead depending on riding speed, with an auto-adjusting Alert Distance. A shipping cycling product doing the same thing. |
| 4 | **US 5904728 "Voice guidance timing in a vehicle navigation system"** | IN — states the case against fixed distance outright: a distance tuned for 65 mph is far too long at 25 mph, and may span more than one similar junction. This is her module docstring, in a 1999 patent. |
| 5 | **osmand.net/docs/technical/algorithms/voice-prompt-triggering/** | **IN. Opened and read in full.** §7d. The best cross-check on her 60/20/6 s numbers that exists. |

Also surfaced: US 9470541 / 9551589 / 9217644 "Natural navigational guidance" —
the utterance-length correction (shift playback so the phrase *ends* at the
target point), which became the first of the two recommendations in §7d.

### Search 13 — `ferrostar vs maplibre navigation android which should I use comparison`

| # | Result | In / out |
|---|---|---|
| 1 | maplibre/awesome-maplibre | IN, see search 11. |
| 2 | **ferrostar issue #967** | **IN.** Verified: **open**, updated 2026-09-24. Puck/camera freeze on self-overlapping routes — `ui-maplibre` only. |
| 3 | stadiamaps.com/blog/ferrostar-building-a-cross-platform-navigation-sdk-in-rust-part-1/ | IN — Stadia's own admission that MapLibre Navigation Android was the most mature open-source option when they started in late 2023. |
| 4 | ferrostar discussion #35 | Out — wishlist thread. |
| 5 | zleptnig.com "Ferrostar 0.50/0.51 and the MapLibre Compose migration" | **IN** — the migration cost warning: manageable if you only use high-level UI components, real work if you customised the map at a lower level. Directly relevant, since she will customise everything. |

### Search 14 — `valhalla narrative instructions locales offline street names spoken instruction generation on device`

| # | Result | In / out |
|---|---|---|
| 1 | mapzen.com/blog/valhalla-intro/ | Out — 2015, defunct Mapzen; describes intent. |
| 2 | mapzen.com/products/mobility/turn-by-turn/ | Out — same. |
| 3 | **github.com/valhalla/valhalla** | **IN.** `LICENSE.md` → `COPYING` opened: **MIT**. `cmake/ValhallaBin2Header.cmake` and `locales_json` in `src/odin/util.cc` confirm locale JSON is compiled into the binary. |
| 4 | valhalla.github.io/valhalla/api/route/overview/ | IN — the five verbal instruction variants per maneuver. |
| 5 | deepwiki "Turn-by-Turn Directions / valhalla" | IN — Odin, the narrative dictionary, `locales/en-US.json`. Third-party wiki; corroborated against the repo above rather than trusted alone. |

### GitHub repository searches

- **`gh search repos --topic=turn-by-turn-navigation --sort=stars --limit=15`** — top by stars: `graphhopper/graphhopper-maps` (169, web UI, out), `ffwdme.js` (153, **deprecated**, JS, out), `3v1n0/GMapsParser` (58, scrapes Google Maps notifications → needs signal, out), `Andriiseer/react-native-here-maps` (21, HERE commercial, out), `amias-samir/GraphHooperMapRouteNavigation` (17, BSD-3, Flutter, out).
- **`gh search repos --topic=turn-by-turn-navigation --sort=updated --limit=15`** — most recent: `graphhopper-maps` (2026-09-25), `2gis/dgis_mobile_sdk_full` and `_map` (2026-09-22, Flutter commercial), `OrangeBaron/eink_maps` (2026-09-15, **no licence**, ESP32 display), `scriptwriter13/bikenavi_esp32` (2026-09-04, GPL-3, ESP32 firmware). **Nothing new for §6.** The two ESP32 finds belong to §8/§11 if ever.
- **`gh search repos "offline navigation android" --sort=stars`** — one hit, `Pierre-Olivier49/AeroTrail-project`, **no licence**, out.
- **`gh search repos --topic=navigation-sdk --sort=updated`** — `googlemaps/flutter-navigation-sdk` (Google, online, out), `plotnikov228/qorvia_maps_sdk` (out), `maplibre-navigation-android-legacy` (archived, out).
- **`gh search repos --topic=turn-by-turn-navigation + topic filters`** also surfaced `Project-OSRM/osrm-text-instructions.java` (BSD-2 opened, **last push 2020-06-15**, out) and `Graphmasters/nunav-navigation-sdk-example` (Apache-2.0 examples, closed SDK, out).
- Code searches used to locate specific files: `maximumDistanceOffRoute`, `MapLibreNavigationOptions`, `triggerDistanceBeforeManeuver`, `locales_json`, each within its own repo.

---

## 11. Confidence note

**Read from the actual file or API response, treat as verified:** every licence
in this document (each opened via `gh api repos/<r>/license`, following pointers
where a `LICENSE.md` referred to a `COPYING`); every star, fork, open-issue,
`pushed_at` and release date; every default constant quoted from
`MapLibreNavigationOptions.kt`, `deviation_detection.rs`, `FerrostarCore.kt`,
`Speech.kt`, `RouteStepProgress.kt`, `Voice.java` and `TextToSpeech.java`; the
absence of a licence file in navmaster; the Material 3 dependency in Ferrostar's
`ui-compose` and its absence from `:core` and from `maplibre-navigation-core`;
the state and dates of Ferrostar issues #875, #964, #967, #969.

**Read from a rendered documentation page, treat as documented:** the Ferrostar
book's Route Providers, Navigation Behavior and Android pages; OsmAnd's
voice-prompt-triggering and voice-prompts pages; the GraphHopper example README;
the kettenblatt README.

**Unverified, and marked as such in the text:** whether the GraphHopper
navigation example works offline (its README does not say either way, and it was
not built); `ACTION_CHECK_TTS_DATA` / `ACTION_INSTALL_TTS_DATA` /
`EXTRA_AVAILABLE_VOICES` semantics, which came from the Android docs surfaced in
search rather than from the AOSP source read for the rest of §7a;
`voiceping-ai/android-offline-tts-eval`, which was not opened.

**Not done:** nothing was cloned, compiled, or run on a device. No APK was built.
The three prompt-timing claims about live behaviour — that Ferrostar's
`trigger_distance_before_maneuver` cannot be made speed-adaptive without
regenerating the route, that MapLibre Nav's `STEP_DURATION_REMAINING_SECONDS`
reflects the router's duration rather than GPS speed, and that `QUEUE_ADD` can
let a prompt land after its turn — are read off source and are sound as readings
of the code, but none has been observed on a moving motorcycle.

**Deliberately not re-derived:** navmaster's stack, vialix, Kblack, Ride-Track,
MOTO-HUB and MotorcycleLeanAngleSensor, per `00-already-checked.md`. Two of its
open items were closed in passing because they were one API call each and both
bear on §6: **navmaster has no licence file** (hard disqualifier) and **Kblack is
GPL-3.0**.
