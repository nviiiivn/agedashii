# Navi rebuild: the choices

**Status: nothing is chosen.** One section per thing the app structurally
needs, with the options for it. Go through them one at a time, pick, and write
the pick at the bottom of the section. `NATIVE.md` is one possible path through
this list, not the decision.

Each option gets one line. The `search:` line is what to paste into Google or
GitHub.

---

## 1. Start from scratch, or start from something that already works?

The biggest choice, because it decides how much of the rest is already done.

- **Fork a full offline nav app** and add your dash and telemetry on top:
  - [OsmAnd](https://github.com/osmandapp/OsmAnd): Java. Huge: offline maps, routing, a motorcycle profile, trip recording, plugins. GPLv3.
  - [Organic Maps](https://github.com/organicmaps/organicmaps) / [CoMaps](https://en.wikipedia.org/wiki/CoMaps): C++ core, very fast and clean, offline search and routing. Apache 2.0 (the app code).
  - [PocketMaps](https://github.com/junjunguo/PocketMaps): Java, Mapsforge-vtm + GraphHopper. Small and easy to read.
- **Start from a working stack example:**
  - [navmaster](https://github.com/francescobuzle-a11y/navmaster): MapLibre + Valhalla + Ferrostar, fully offline.
  - [Kblack](https://github.com/khoa083/map-offline-navigation): MapLibre + GraphHopper, offline.
  - [femto-car-launcher](https://github.com/seijikohara/femto-car-launcher): Compose home-screen launcher with a map dashboard.
  - [WunderLINQ](https://github.com/blackboxembedded/WunderLINQ-Android): motorcycle dashboard app, Java.
- **Assemble from libraries** (sections 3 to 9), keeping your own code as the spine.
- `search:` "OsmAnd fork motorcycle", "Organic Maps fork android build", "offline navigation android open source"

**Pick: assemble from libraries** (MapLibre + valhalla-mobile + Ferrostar),
with her own code as the spine and Cardinal/Murena Maps
(gitlab.e.foundation/e/os/cardinal) as the worked example to read, not copy.
Why: it's the only option where the screens are her alola look from line one,
not someone's Material UI to rip out; it stays fully offline; the libraries are
permissive (MIT/BSD), so the app stays hers. Research:
`research/01-start-from.md`. Picked 2026-09-27.

---

## 2. Language and app framework

What the app is written in. It can be more than one: a fast core in one
language and the screens in another is normal.

- **Kotlin + Jetpack Compose**: Android's own. Fastest, but Material by default (see section 10 for dropping it).
- **Kotlin Multiplatform / Compose Multiplatform**: the same, plus desktop and iOS later.
- **Java**: most of the offline map/routing libraries (OsmAnd, Mapsforge, BRouter, GraphHopper) are Java, and Kotlin uses them directly.
- **Flutter / Dart**: agedashii already lives as Flutter widgets underneath Flet. Fully custom widgets, no Material needed.
- **Rust**: for a heavy core (DSP, telemetry). Ferrostar's core is Rust; `uniffi` makes Kotlin/Swift bindings.
- **C++ (Android NDK)**: what Valhalla and Organic Maps are written in.
- **Keep Python**:
  - **Chaquopy** runs your existing `.py` inside a native Kotlin app, so the math moves over untouched.
  - **BeeWare/Briefcase** and **Kivy/Buildozer** build native-ish Python apps.
- **Web tech with native underneath**: Capacitor, Tauri 2 (mobile), React Native/Expo. See section 10, because this is how alola.css itself could be the UI.
- `search:` "Chaquopy kotlin app python module", "Tauri 2 android", "Compose Multiplatform android", "uniffi kotlin android"

**Pick:**

---

## 3. Map drawing (the renderer)

OSM is the **data**. A renderer is the library that **draws** that data on
screen. It isn't a choice between them: every option here draws OSM.

- **MapLibre Native** (+ maplibre-compose): GPU vector maps, the most common base. Reads MBTiles and PMTiles.
- **Mapsforge**: compact offline `.map` files, Java, long-proven offline.
- **VTM** (Mapsforge's OpenGL one): faster than Mapsforge, rotation/tilt, 3D buildings. LGPL.
- **osmdroid**: classic raster tiles, simple, Java.
- **CARTO Mobile SDK**: map + offline Valhalla routing + offline geocoding in one. Check whether it's still maintained (wiki last edited 2022).
- **MassifMaps**: newer all-in-one SDK with embedded Valhalla. Check its maturity.
- `search:` "maplibre native android offline pmtiles", "mapsforge vtm android", "CARTO mobile sdk valhalla offline"

**Pick:**

---

## 4. Map data and the tools that make it

What gets baked into the 200–300 MB, and how.

- **Tile formats:** MBTiles (what you use now), PMTiles (one file, fast), Mapsforge `.map`, raster vs vector.
- **Basemap schemas:** OpenMapTiles, Protomaps basemaps, Shortbread.
- **Tile builders:** planetiler (Java, fast), tilemaker (C++), osmium (already on the Pi) for cutting extracts.
- **Map styles:** Maputnik (visual style editor) to put alola colours on the map itself.
- **Elevation:** DEM tiles (SRTM, Copernicus) inside Valhalla's tile build, or separate hillshade.
- **Speed limits:** OSM `maxspeed` tags; Valhalla's `trace_attributes` reads them offline.
- `search:` "planetiler custom basemap", "protomaps pmtiles android", "maputnik custom style"

**Pick: vector tiles.** Not raster. Three reasons, in the order they matter:

1. **The map wears the theme.** Colours live in a style file, so Night, Day,
   Sunlight, Red night and the rest each get a matching map instead of one fixed
   dark raster sitting under eight different chromes. This is the "THEMED UI TO
   MY STYLES" requirement applied to the half of the screen that is the map.
2. **Sharp at riding zoom.** Geometry built to z14 redraws cleanly at z18. The
   raster archive stopped at z15 and went blank above it, which is exactly the
   zoom she rides at.
3. **Street names are real text**, so they can be sized for a helmet rather than
   baked in at whatever size the renderer chose.

Still open: the container, **PMTiles** (one file, range-read) or **vector
MBTiles** (the SQLite shape already in use). Independent of the renderer, and
can be decided late.

**Pick: posted speed limits first.** Wherever OSM has `maxspeed` on the road,
show that number and label it **posted**. The routing tiles carry it per segment
so it works with no signal; with Valhalla, map-matching the current position via
`trace_attributes` returns each segment's `speed_limit`. Where there is no tag,
fall back to the typical speed for the road class in `guidance.TYPICAL_MPH` and
label it **typical**. The two labels never share a style, so a glance tells a
sign reading from an estimate.

This supersedes the blanket disclaimer the Flet app shows on every road -
"typical for this road class, not a reading of the sign". That was honest, but
it threw away real data the extract already contains and made every limit look
like a guess.

---

## 5. Routing (offline, on the phone)

- **Valhalla** via [valhalla-mobile](https://github.com/Rallista/valhalla-mobile): the router you already use, with motorcycle costing, height and map matching. C++, MIT.
- **GraphHopper**: Java, custom models for motorcycle. Offline Android isn't officially supported anymore, but it still works.
- **BRouter**: Java, offline, elevation-aware, great custom profiles. Car routing is flagged experimental.
- **OsmAnd's router / Organic Maps' router**: come with a fork (section 1).
- **Your own** `routing.py` A\*, ported.
- `search:` "valhalla-mobile android", "graphhopper android offline", "brouter motorcycle profile"

**Pick:**

---

## 6. Turn-by-turn guidance

The layer that turns a route into "in 300 m, turn left", off-route detection
and re-routing.

- **Ferrostar**: Rust core, Compose UI, bring-your-own router. BSD.
- **MapLibre Navigation Android**: fork of the old Mapbox nav SDK, moving to Kotlin Multiplatform. Works with GraphHopper/OSRM.
- **GraphHopper navigation example**: GraphHopper + MapLibre nav.
- **Your own** `guidance.py`, ported (60/20/6 s prompts, deadband, re-route cap).
- **Voice:** Android TextToSpeech (offline voices) or bundled voice clips.
- `search:` "ferrostar custom route provider", "maplibre-navigation-android"

**Pick:**

---

## 7. Places, search, POIs (offline)

- **An OSM extract into SQLite + FTS5** (osmium tags-filter → SQLite): small, yours, fast.
- **SpatiaLite / GeoPackage**: SQLite with real geo queries ("fuel within 5 km of the route").
- **Photon / Nominatim**: full geocoders. Heavy, better on a server.
- **libpostal**: address parsing.
- **Organic Maps / OsmAnd search**: comes with a fork.
- `search:` "offline geocoding android sqlite fts5 osm", "spatialite android"

**Pick:**

---

## 8. Telemetry and sensors

Your math in `apps/navi` is the spec whatever gets picked here.

- **Motion sensors:** Android SensorManager, raw or its built-in fused `ROTATION_VECTOR`. Or your own fusion: a Madgwick/Mahony AHRS filter for pitch/roll/lean.
- **Audio → RPM:**
  - **TarsosDSP**: Java audio DSP with YIN/MPM pitch detection and band-pass filters.
  - **JTransforms / KissFFT / Oboe** for your own FFT pipeline.
  - [rpm-boss](https://github.com/AregShahbazian/rpm-boss) takes another route: band-pass → rectify → envelope → autocorrelate. It uses Android's UNPROCESSED mic source and has real test recordings.
- **Exact RPM from hardware:** an inductive pickup on the ignition lead → ESP32 → Bluetooth to the phone. It's reliable at any revs, where the mic struggles at speed.
- **OBD-II** (bikes that have it): kotlin-obd-api + an ELM327 dongle.
- **Bluetooth:** Kable (Kotlin BLE) or the Nordic Android-BLE-Library, for sensors, TPMS, remotes.
- **Loggers to learn from:** Sensor Logger, Biker-Log, MotoLogger (already in XNAV.md), RaceChrono (commercial).
- `search:` "TarsosDSP android kotlin", "madgwick filter android kotlin", "ESP32 ignition rpm bluetooth", "kotlin-obd-api"

**Pick:**

---

## 9. Database and sync

- **On the phone:**
  - **Room**: Android's standard SQLite layer.
  - **SQLDelight**: you write the SQL and it generates Kotlin (Kotlin Multiplatform).
  - **Plain SQLite**, SpatiaLite for geo.
  - **ObjectBox**, **Couchbase Lite** (object/document stores).
  - **DuckDB**: fast analysis over rides.
- **Ride log format:** JSONL (now), SQLite, Parquet, GPX, FIT (Garmin's format, with an SDK).
- **Sync to dashbored:**
  - your own outbox (the `store.py` pattern) + WorkManager
  - **PowerSync** (Postgres ↔ SQLite)
  - **ElectricSQL**
  - **Couchbase / CouchDB replication**
  - **CRDTs** (Automerge) if two devices edit the same thing
- `search:` "SQLDelight android", "PowerSync kotlin", "offline-first sync android sqlite"

**Pick:**

---

## 10. Your look (not Material)

The requirement is alola.lol / css.alola.lol / agedashii, not Google's
Material.

- **One source of truth for the style:** design tokens (JSON) → **Style Dictionary** (or Tokens Studio) → generates alola.css *and* Kotlin/Compose *and* Dart. Your CSS stays the master copy.
- **Compose with no Material:**
  - build on `compose.foundation` with your own theme ([Google's custom design system guide](https://developer.android.com/develop/ui/compose/designsystems/custom))
  - [Compose Unstyled](https://composeunstyled.com/): behaviour-only components you style yourself
  - its [no-Material starter](https://github.com/composablehorizons/jetpack-compose-boilerplate)
- **Flutter with no Material:** `WidgetsApp` + your own widgets. The closest to what agedashii already is.
- **Your actual CSS as the UI:** a native core (map, sensors, routing) with the screens as HTML using alola.css, in a WebView, Capacitor or Tauri 2. Pixel-for-pixel alola, at some cost in speed.
- **Effects:** the CRT overlay, glow and scanlines as shaders (Android 13+ RuntimeShader/AGSL), or drawn layers.
- **Fonts:** bundle yours. Every option supports that.
- `search:` "style dictionary jetpack compose tokens", "compose unstyled", "flutter without material WidgetsApp", "capacitor native map plugin"

**Pick:**

---

## 11. Launcher / kiosk (the phone *is* the dash)

- **HOME app**: one intent filter; set as the default home screen.
- **Kiosk / lock task mode**: device owner via adb, so it can't fall out to Android. No Google account needed.
- **Boot straight in**, screen always on while riding, auto-start recording on motion.
- **Handlebar remotes:** Bluetooth media-button / keyboard remotes (DMD2-style) mapped to actions.
- **Examples:** femto-car-launcher, WunderLINQ Launcher, DMD2 (commercial, look-and-feel reference).
- `search:` "android kiosk mode device owner adb", "android home launcher intent filter", "motorcycle handlebar bluetooth remote android"

**Pick:**

---

## 12. Building and testing

- **Where it builds:**
  - **Tower** (x86_64, standard).
  - **The Pi**: point Gradle at a native arm64 aapt2 with `android.aapt2FromMavenOverride`. Get one from:
    - Debian's `aapt` package
    - [android-arm-build-tools](https://android-arm-build-tools.commit451.com/)
    - [OpenMoTa3/android-sdk-linux-arm64](https://github.com/OpenMoTa3/android-sdk-linux-arm64)
    - [jagones84/android-arm64-devkit-skill](https://github.com/jagones84/android-arm64-devkit-skill), an agent skill for exactly this
    - or [the Box64 route](https://gist.github.com/felix021/e7179596244ee81852c646f904adddf6)
  - **On the phone** (Termux, Android Code Studio).
  - **CI**: Gitea/Forgejo Actions on Tower, or GitHub Actions.
- **Testing on a real phone:** adb over wifi, and [mobile-mcp](https://github.com/mobile-next/mobile-mcp) so Claude can screenshot and tap it.
- **Emulator** for quick checks (runs on Tower).
- `search:` "android aapt2 arm64 raspberry pi gradle", "forgejo actions android build"

**Pick:**

---

## 13. Moving your Python over

- **By hand/Claude**, with your Python tests as the spec: port the tests first, then the code, until they pass.
- **Chaquopy**: don't move it at all, call the Python from Kotlin.
- **py2many**: Python → Kotlin/Rust/Dart transpiler. Experimental; good for a first draft of pure-math files.
- **quicktype**: turns dashbored's JSON payloads into typed Kotlin/Dart classes.
- **OpenAPI generator**: if dashbored gets an OpenAPI spec, the whole client is generated.
- `search:` "py2many kotlin", "quicktype kotlin", "chaquopy"

**Pick:**

---

## 14. Project management

- **In the repo, markdown:** your BOARD.md / GAPS.md / STATUS.md style, or Backlog.md (a git-native task board).
- **Self-hosted:** Gitea issues + projects (already running), Plane, Vikunja.
- **Hosted:** GitHub Projects, Linear.
- **Decision records (ADRs):** one short file per pick in this document, so the "why" survives. There's a skill for it: `ecc:architecture-decision-records`.
- `search:` "Backlog.md", "Plane self-hosted", "architecture decision records template"

**Pick:**

---

## 15. Claude tooling for this project

- **Installed already:**
  - planning / TDD / debugging skills (superpowers)
  - Kotlin, Android and Gradle skills and agents (ecc)
  - context7 (library docs)
  - serena (code navigation)
- **Add, depending on the picks above:**
  - `kotlin-lsp` / `jdtls-lsp` plugins (Kotlin / Java)
  - mobile-mcp
  - the arm64 devkit skill
  - a Dart/Flutter setup if Flutter wins
- **Drop:** the flet MCP, once Flet is gone.
