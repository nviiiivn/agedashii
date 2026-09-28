# §1 Start from scratch, or start from something that already works?

Researched 2026-09-26/27. Research only, no code. The pick is nvii's.

**How the facts were checked.** Licence, stars, forks, creation date and last
push for every GitHub repo come from the GitHub API (`gh api repos/...`), and
every licence GitHub could not classify (`NOASSERTION`) was read from the
LICENSE/COPYING file itself. Cardinal Maps was checked through the GitLab API on
gitlab.e.foundation. READMEs were read with a summarising fetch. **Nothing was
cloned, built or run, and no speed was measured on her phone.** Any speed claim
below is the project's own or the §5 file's, and is labelled as such.

Builds on `00-already-checked.md` (navmaster, vialix, Kblack, Ride-Track,
MOTO-HUB, MotorcycleLeanAngleSensor). Those are reused, not re-researched, apart
from licences re-verified via the API.

---

## The question §1 actually has to answer

Her app's differentiators are **the telemetry** (lean, g, RPM from the exhaust
note, gears, the ride log) and **her own look** (alola.lol / css.alola.lol, not
Material). Navigation is the commodity part: every candidate below does it.

Forking a full nav app hands you the commodity and fights you hardest on the
differentiator, because someone else's UI is the most expensive thing to rip out.
With OsmAnd it is also a licensing problem: its UI/UX is under a **NoDerivatives**
licence (see below).

---

## Comparison

| Option | Licence (verified) | Last activity | Android | Fully offline today | Motorcycle | Look | Verdict |
|---|---|---|---|---|---|---|---|
| **OsmAnd** fork | GPLv3 code **+ CC-BY-NC-ND 4.0 on UI/UX** | push 2026-09-26, 6,040★ | yes | yes | yes, a built-in profile (off by default) | theirs; NoDerivatives on the UI | poor fit |
| **Organic Maps** fork | Apache-2.0 + a required visible credit | push 2026-09-26, 15,506★ | yes | yes | **no** profile | theirs, in a C++ drape style system | poor fit |
| **CoMaps** fork | Apache-2.0 | commit 2026-09-27 (Codeberg), 560★ on mirror | yes | yes | **no** profile | theirs | poor fit |
| **Cardinal Maps / Murena Maps** fork | GPL-3.0 | commit 2026-09-21, GitLab activity 2026-09-25 | yes | yes, once an area is downloaded | none (general maps app) | Material 3 throughout | workable, but the UI has to go |
| **PocketMaps** fork | MIT | last commit **2024-10-31** | yes | yes | not stated | old View-based UI | stale |
| **Assemble from libraries** (her code as the spine) | MapLibre BSD-2, valhalla-mobile MIT, Ferrostar BSD-3 | all active | yes | yes | Valhalla motorcycle costing (§5) | **hers from line one** | best fit |
| navmaster | **none: "© Francesco"** | created 2026-09-23 | yes | yes | trucks | – | **ruled out: no licence** |
| WunderLINQ | GPL-3.0 | 2026-06-18 | yes | no, needs a Google Maps key | BMW only, needs their hardware | – | out |
| femto-car-launcher | Apache-2.0 | 2026-09-27 | yes | **no**: OpenFreeMap online tiles, no routing | – | Material 3 | reference for §11 only |

---

## Each option

### Fork a full offline nav app

#### OsmAnd: <https://github.com/osmandapp/OsmAnd>

- **Licence:** read from the LICENSE file. The code is GPLv3, but:
  - "UI Design and UX work, such as layout and icons, are covered by CC-BY-NC-ND 4.0" (`OsmAnd/res` and others).
  - "Publishing applications using the OsmAnd UI/UX code to Google Play, Amazon Market, or Apple Store must be done with written permission."
  - "Generated voice files … are covered by a proprietary license."
  - "some artwork uses a proprietary license."
- **Activity:** 6,040★, 1,235 forks, created 2011-01-15, pushed 2026-09-26. The GitHub "latest release" (2.0.0, 2019) is stale metadata; they ship through stores.
- **Android:** yes. Java app plus a native C++ core (OsmAnd-core).
- **Offline:** yes, for maps, routing and search. The docs say "OsmAnd offline routing mechanism uses predownloaded maps as data source for route calculation."
- **Motorcycle:** yes. The docs say the profile "is disabled by default … Menu → Settings → App profiles". Routing rules live in `routing.xml` and can be edited. It also has trip recording.
- **Speed:** the §5 file reports its Java routing engine as roughly 10× slower than the alternatives. That's their figure and I haven't verified it.
- **Fit with her rules:**
  - Offline: yes.
  - Her look: the worst of the candidates. The ND terms forbid sharing adapted versions of exactly the part she would replace. Private, never-shared changes are a grey area, not a licence to do it.
  - Posted speed limits: OsmAnd does read `maxspeed`, but it's in their `.obf` format, not the vector tiles picked in §4.
  - Map format: `.obf`, not the vector tiles (MapLibre style per theme) chosen in §4.
- **Known problems:** huge codebase, a heavy build with the native core, and the licence split above.
- **Community motorcycle add-ons:**
  - [OsmAnd-Rendering/Motorcycle](https://github.com/OsmAnd-Rendering/Motorcycle): GPL-3.0, 51★, pushed 2026-09-05. It's an off-road render style plus routing profiles you import into stock OsmAnd, not a fork.
  - [markec12345678/OsmAndmarkec](https://github.com/markec12345678/OsmAndmarkec): GPLv3 + CC-BY-NC-ND, the same LICENSE as upstream. 0★, created 2026-05-29. Its README says: "the full OsmAnd project has NOT been compiled end-to-end." That rules it out as a base. The idea of a lean/G/crash plugin inside OsmAnd is readable, but its code has never run.
  - [schmidi2/OsmAnd-Motorbike](https://github.com/schmidi2/OsmAnd-Motorbike): self-described alpha, "cannot just be installed and used". Not opened beyond the search summary.

#### Organic Maps: <https://github.com/organicmaps/organicmaps>

- **Licence:** Apache-2.0, read from LICENSE. Bundled third-party code is under BSD-2/3, BSL-1.0, MIT, Zlib, SGI-B-2.0 and BSD-Protection, and the map data is ODbL (the `LICENSES/` folder).
- **Condition on forks** (from the README): "include a visible, human-readable mention of the 'Organic Maps Project' and a clickable link to https://organicmaps.app … in the product's 'About' and 'Main Menu' screens."
- **Activity:** 15,506★, 1,607 forks, pushed 2026-09-26, release 2026.08.27.
- **Android:** yes. C++ core with a Java UI.
- **Offline:** yes; offline is the whole point of the app.
- **Motorcycle:** **no profile.** There's an open feature request ([discussion #11932](https://github.com/orgs/organicmaps/discussions/11932)); I didn't open it beyond the search summary. In the US, car routing is mostly usable on a motorcycle because motorcycles are allowed on freeways, but there's no motorcycle costing.
- **Speed:** "very fast" per CHOICES.md. Not measured by me.
- **Fit with her rules:**
  - Map format: MWM, with its own style system (drules). That isn't MapLibre style JSON, so the §4 pick ("vector tiles, a map style per theme") would be done their way, not the standard way.
  - Posted speed limits: I didn't verify whether its navigation screen shows a posted limit.
- **Known problems:**
  - The build needs about 30 GB free and a C++/NDK toolchain. The map data is "tightly integrated with … the maps generator", so adding a vehicle profile can mean changing the generator (a CoMaps maintainer said this about swapping the router).
  - Building on the Pi is not known to work.

#### CoMaps: <https://codeberg.org/comaps/comaps> (GitHub mirror <https://github.com/comaps/comaps>)

- **Licence:** Apache-2.0 (API and Codeberg).
- **Activity:** 3,083 commits, latest 2026-09-27, 36 releases, 560★ on the mirror, which was created 2025-05-09.
- **Android:** yes. Also iOS, macOS and Linux.
- **Offline:** yes.
- **Motorcycle:** **no profile.** Car, foot and bike only (the OSM wiki and CoMaps FAQ, via the search summary).
- **Fit and known problems:** the same as Organic Maps. It's a community fork of the same C++ core and MWM format. CoMaps issue #552 asks for a new routing engine; I saw it only in the search results and didn't open it.

#### PocketMaps: <https://github.com/junjunguo/PocketMaps>

- **Licence:** MIT.
- **Activity:** 250★, created 2015. The API's `pushed_at` shows 2026-07-08, but the **last commit is 2024-10-31** ("Fix URL encoded strings"). Last release 4.0, 2023-10-26.
- **Stack** (from `app/build.gradle`): GraphHopper **0.13.0** (a 2019-era version), Mapsforge **VTM 0.12.0**, compile/target SDK 33.
- **Offline:** yes, maps and routing.
- **Motorcycle:** not stated.
- **Fit with her rules:** the permissive licence is its only strength. The router is five years behind, the §5 file finds that GraphHopper has no Android target after 1.0, and the map is VTM, not MapLibre vector tiles.
- **Known problems:** stale. Kblack started from it and was rewritten into GPL-3.0 Kotlin/Compose.

### Start from a working stack example

#### Cardinal Maps, now Murena Maps (`e/os/maps`): <https://gitlab.e.foundation/e/os/cardinal>

This is the find of this section: the only open-licensed Android app that already runs **MapLibre + on-phone Valhalla + Ferrostar + offline search** today, in Kotlin/Compose.

- **Licence:** GPL-3.0 (read from LICENSE.md via the GitLab API; the GitHub copy also says GPL-3.0).
- **Activity:**
  - GitLab project created 2025-10-01 and now renamed `e/os/maps`. Last activity 2026-09-25, recent commits 2026-09-14 to 2026-09-21 (UI work). 10★ on GitLab.
  - Backed by the /e/ Foundation (Murena), so it's not a one-person project.
  - The old GitHub repo (ellenhp/cardinal, 47★) stopped at 2025-10-06 when development moved.
- **Stack** (from `cardinal-android/gradle/libs.versions.toml`):
  - maplibre-compose 0.12.1
  - valhalla-mobile 0.3.0
  - Ferrostar 0.45.0 (core, maplibreui, composeui)
  - Room 2.8.4
  - Material 3 1.5.0-alpha10
  - Hilt
  - a Rust offline geocoder (`cardinal-geocoder`: tantivy + mvt-reader + uniffi) that searches POIs out of the vector tiles themselves
- **Android:** yes.
- **Offline:** yes, through "Offline Areas: manage downloaded map regions" (AGENTS.md).
  - An area is a bounding box downloaded from a tile server: PMTiles served by `pmtiles serve`, and Valhalla tiles from object storage.
  - The docs describe how to generate both (planetiler-openmaptiles fork; `valhalla_build_tiles`).
  - For a no-SIM phone, she would host the Bay Area files on Tower and download them once over home wifi. I haven't verified this against the code.
- **Motorcycle:** none. It's a general maps app. Valhalla underneath has motorcycle costing (§5).
- **Speed:** MapLibre Native (GPU) plus Valhalla in C++. Not measured.
- **Fit with her rules:**
  - Offline: yes.
  - Stack: matches `NATIVE.md` almost exactly.
  - Look: **fails.** "Material 3 and Material 3 Expressive components are used throughout", and it's now being moved to /e/'s own "ETheme".
  - Licence: GPL-3.0 is fine for her own phone. It matters if the app is ever distributed, and it applies to any Cardinal code copied into her app.
- **Known problems:**
  - Self-described as "in active development … regressions or data loss".
  - Forks must change the user agent and either get permission for, self-host, or remove the Transitous transit API (ARCHITECTURE.md). Transit isn't needed here anyway.
  - Its library versions trail current ones (Ferrostar 0.45 vs 0.57 per §6; valhalla-mobile 0.3.0 vs 0.6.3 per §5).

#### navmaster: <https://github.com/francescobuzle-a11y/navmaster>

- **Ruled out: no licence.**
  - The GitHub API reports none, and the full repo tree contains no LICENSE, COPYING or NOTICE file.
  - The README's "Licenze e dati" section says "Codice NavMaster: © Francesco", which means all rights reserved.
  - Created 2026-09-23, 0★.
  - Read-only reference at most.

#### Kblack (map-offline-navigation): <https://github.com/khoa083/map-offline-navigation>

- GPL-3.0 (API), 12★, created from PocketMaps, last commit 2026-09-02.
- GraphHopper + MapLibre, Compose with Material 3, Vietnam data only.
- Partial fit: a second worked example, but it commits §5 to GraphHopper and its look is Material.

#### WunderLINQ Android: <https://github.com/blackboxembedded/WunderLINQ-Android>

- GPL-3.0 (COPYING), 11★, release 2.99.8 on 2026-06-18.
- It's a companion app for the WunderLINQ **hardware** on BMW Navigation Prep, and its build needs a Google Maps API key.
- **Out.** Not an offline nav base. Its BMW bus telemetry isn't relevant to either of her bikes, which the §8 file found have no ECU.

#### femto-car-launcher: <https://github.com/seijikohara/femto-car-launcher>

- Apache-2.0, 23★, created 2026-04-23, pushed 2026-09-27. 491 commits, CI, stable and nightly channels.
- A home-screen launcher with a live map, but it uses **OpenFreeMap online tiles** (or Google), has **no routing**, uses **Material 3**, and needs Android 13+.
- **Out as a §1 base.** Good §11 reference for the HOME-launcher and dashboard layout, and Apache-2.0 so it's copyable.

### Assemble from libraries, keeping her code as the spine

- **Libraries** (licences read from files or the API):
  - MapLibre / maplibre-compose (BSD-2 per navmaster's list; not re-opened here)
  - [valhalla-mobile](https://github.com/Rallista/valhalla-mobile): MIT, 78★, pushed 2026-09-06
  - [Ferrostar](https://github.com/stadiamaps/ferrostar): BSD-3-Clause, read from LICENSE.txt; 428★, pushed 2026-09-22
- All three are permissive, so her app can be any licence she chooses.
- **Offline:** yes; each piece is offline-capable and Cardinal proves they work together offline.
- **Motorcycle:** Valhalla motorcycle costing, with posted limits from `trace_attributes` (§5).
- **Look:** entirely hers. Ferrostar `:core` has no Material; only its optional compose UI module does (§6).
- **Speed:** the same engines as Cardinal. Not measured.
- **Known problems:** she writes the glue herself: offline area storage, the route provider hook, and the camera/puck. That's exactly the code Cardinal already has.
  - Reading Cardinal for how it's done is fine.
  - **Copying Cardinal's code makes that part of her app GPL-3.0.**

### Looked at and out

| Repo | Licence (API) | Created / last push | Why out |
|---|---|---|---|
| [vialix](https://github.com/igalmarino/vialix) | GPL-3.0 | – / 2026-09-21 | offline not implemented (00 file) |
| [open-dash](https://github.com/subtlesayak/open-dash) | Apache-2.0 | 2026-06-17 / 2026-09-21, 74★ | no own routing (Mapbox online), Material 3; garage/expense app. Reference for garage features at most |
| [gangefors/moto](https://github.com/gangefors/moto) | AGPL-3.0 | **2026-09-22** / 2026-09-27 | 5 days old; a route planner that exports GPX, not a nav app. Its twisty-road routing idea belongs to §5 |
| [twistaway](https://github.com/RadNotRed/twistaway) | GPL-3.0 | 2026-05-13 / 2026-09-21, 2★ | Dart route planner; offline unverified |
| [kettenblatt](https://github.com/nils-fl/kettenblatt) | Apache-2.0 | 2026-08-06 / 2026-08-08, 3★ | GPX follower with offline cues; too small and quiet to be a base. Read only via search summary |
| [GeoNav](https://github.com/anshlabs716/GeoNav) | GPL-3.0 | **2026-09-24**, 0★ | 3 days old |
| [MotoTrack](https://github.com/juliannof/MotoTrack) | **none** | 2026-09-23 | **no licence: ruled out** |
| [KTM-Nav-GEN3](https://github.com/Pavanayi1/KTM-Nav-GEN3) | MIT | 2026-07-07 | mirrors Google Maps, so it's online |
| [Navit](https://github.com/navit-gps/navit) | GPL-2 with LGPL parts (COPYING) | v0.5.7, 2026-02-08 | C with its own map format and UI; doesn't match the stack |
| [libosmscout](https://github.com/Framstag/libosmscout) | LGPL with app-store exceptions | pushed 2026-09-27 | C++/Qt; a Qt UI stack on Android |
| [NavOSS](https://github.com/yassinsolim/NavOSS) | MPL-2.0 (mobile) / AGPL-3.0 (backend) | 2026-07-15 | iPhone-first, Calgary only |
| [osm-navigator](https://github.com/siddharth2798/osm-navigator) | MIT | 2026-08-12, 0★ | JavaScript web app |
| [osmand-api-demo](https://github.com/osmandapp/osmand-api-demo) | **none** | – | no licence file; the "drive OsmAnd from another app" route is ruled out on that alone |
| [organicmaps/api-android](https://github.com/organicmaps/api-android) | **none** (no LICENSE in root) | pushed 2026-05-20 | no licence file: ruled out |
| [Ride-Track](https://github.com/shivam-uxdesign/Ride-Track), [MotorcycleLeanAngleSensor](https://github.com/braydenzhao/MotorcycleLeanAngleSensor) | **none** | – | no licence (00 file, re-verified) |
| [MOTO-HUB](https://github.com/vincenzobpt/MOTO-HUB) | AGPL-3.0 | – | no GPS (00 file) |

---

## Search log

### Web searches (10)

"Opened" means the page itself was fetched or read through the API. "Summary"
means it was seen only through the search engine's summary.

1. **"OsmAnd fork motorcycle"**
   1. OsmAnd-Rendering/Motorcycle installation.osf: summary. In as an add-on, not a base.
   2. markec12345678/OsmAndmarkec: opened (README + LICENSE). Out: never compiled end-to-end.
   3. OsmAnd issue #14074, motorcycle navigation type: summary. Background only.
   4. OsmAnd-Rendering/Motorcycle README: summary. The same add-on.
   5. OsmAnd-Rendering/Motorcycle repo: opened via API (GPL-3.0, 51★). Add-on only.
2. **"Organic Maps fork android build"**
   1. cbkii/organicmaps (In-Car fork): API only (created 2026-08-07, 0★). Out: head-unit variant.
   2. Tiamat-Tech/organicmaps: summary. Plain mirror, out.
   3. organicmaps docs/INSTALL.md: summary (30 GB, CMake, JDK 17+). Build facts used above.
   4. organicmaps wiki: summary. Background.
   5. comaps/comaps mirror: opened (API + Codeberg page). In as a candidate.
3. **"offline navigation android open source"**
   1. F-Droid navigation category: summary. A list, not a candidate.
   2. XDA article on Organic Maps: summary. Background.
   3. F-Droid Organic Maps: summary. The same app.
   4. MakeUseOf "8 best free offline GPS": summary. It lists closed apps (MAPS.ME, MapFactor, Magic Earth); out as not open source.
   5. osmand.net: opened (routing docs page). In.
4. **"open source offline motorcycle navigation app android github"**
   1. hishizuka/pizero_bikecomputer: summary. Out: Raspberry Pi bike computer, not Android.
   2. patrick-mahnkopf/bike-gps: summary. Out: Flutter, needs Mapbox or a tile server, so not offline.
   3. GitHub topic offline-maps (Kotlin): searched through the API instead, see below.
   4. MOTO-HUB: already in the 00 file. Out.
   5. organicmaps: opened. Candidate.
   - Also surfaced: gangefors/moto (API; out), Welcome-Bikers (summary; out, needs an OpenRouter proxy).
5. **"maplibre valhalla ferrostar android offline app github"**
   - All top 5 were navmaster release pages or its repo. Opened the repo, README and file tree. Out: no licence.
6. **"open source android navigation app kotlin jetpack compose offline routing"**
   - First set:
     - Kotlin "Navigation and routing" docs, Kotlin "Navigation in Compose", Wikipedia "Jetpack Compose": summary. Out, because this is screen-to-screen navigation, the wrong meaning of the word.
     - Medium, "Offline Navigation with Mapsforge in Jetpack Compose": summary. Out: map drawing only, no routing, and Mapsforge.
   - Second set:
     1. Kblack: API. Partial (see above).
     2. graphhopper topic: summary. Browsing surface.
     3. Kblack PR #25: summary (maplibre-compose 0.16.0, compose-bom 2026.09.00). Shows it's active.
     4. graphhopper-navigation-example: API (MIT, 41★, pushed 2026-05-26). A §5/§6 example.
     5. Its README: summary. Notes it also works with Ferrostar.
7. **"Cardinal Maps android offline routing valhalla ferrostar"**
   1. navmaster: out.
   2. vialix: out.
   3. Ferrostar Book, Android: summary. The `CustomRouteProvider` exists for on-device routing; §6 detail.
   4. Valhalla discussion #4746: summary. Points to valhalla-mobile.
   5. valhalla issue #1530: summary. Background.
   6. ellenhp/cardinal: opened, then followed to GitLab and read through the API. **In.**
8. **"open source motorcycle dashboard android app github kotlin gps lean angle"**
   1. OsmAndmarkec: out.
   2. MotorcycleLeanAngleSensor: out, no licence.
   3. Ride-Track: out, no licence.
   4. motorcycle topic: summary. Browsing surface.
   5. juliannof/MotoTrack: API. Out, no licence.
   - Also stark-dashboard: 00 file, UI reference only.
9. **"CoMaps android motorcycle routing profile build from source"**
   1. F-Droid CoMaps: summary.
   2. comaps.app: summary.
   3. CoMaps "how can I build a route": summary. Car/bike/foot only.
   4. Wikipedia CoMaps: summary.
   5. The comaps mirror: opened.
   - Also Organic Maps discussion #11932 (motorcycle profile request) and CoMaps issue #552: summary only.
10. **"awesome openstreetmap android navigation apps list github"**
    1. osmlab/awesome-openstreetmap: summary. Its navigation entries are OsmAnd, Organic Maps, CoMaps, Magic Earth (closed) and Komoot (closed), so nothing new.
    2. xoellijo fork: the same list.
    3. enhansome-openstreetmap: the same list with stats.
    4. Wikipedia, FOSS iOS apps: out, iOS.
    5. OsmAnd repo: covered.

### GitHub repository searches (API, 6 queries × sorted by stars AND by recently updated)

Top 5 of each, with the reason:

- **`offline navigation android`**
  - By stars: organicmaps (in); Trail-Sense (MIT, 2.9k★; a sensor survival app, not nav, possibly a §8 reference); FTC-Skystone (irrelevant); cebian (gesture navigation, irrelevant); **ellenhp/cardinal (in)**.
  - By updated: cebian (irrelevant); kavita-android (irrelevant); seacheck-mobile (nautical, TypeScript; out); organicmaps; Trail-Sense.
- **`topic:offline-navigation`**
  - By stars: **valhalla-mobile (library, in for assemble)**; Attadipa (ESP32 watch, out); astro-nav-int (celestial, out); argonaut (Swift 2018, out); SOS-Mobile-App (no licence, out).
  - By updated: Car-launcher-pro (TypeScript, NOASSERTION, 1★, out); Attadipa; polskie-slady (irrelevant); valhalla-mobile; astro-nav-int.
- **`topic:turn-by-turn-navigation`**
  - By stars: graphhopper-maps (web UI, out); ffwdme.js (deprecated); GMapsParser (Google, out); react-native-here-maps (HERE, out); GraphHooperMapRouteNavigation (Dart plugin, out).
  - By updated: graphhopper-maps; 2gis SDKs (proprietary service, out); eink_maps (ESP32, out); bikenavi_esp32 (out).
- **`topic:offline-maps language:Kotlin`**
  - By stars: GlanceMap (Wear OS, out); expo-yandex-mapkit (out); VeloSpot (bike parking, out); RoueLibre (bike share, out); WTAK (out).
  - By updated: VeloTrack/fablab-tools (MIT, created 2026-09-07; MapLibre + Protomaps bike tracker, too new); CourseMapper (out); kettenblatt (out as a base); GlanceMap; RoueLibre.
- **`motorcycle navigation android`**
  - By stars: open-dash (out); DMD2 public files (no licence, closed app); TET files (out); KTM-Nav-GEN3 (online, out); wazetripper (patches Waze, out).
  - By updated: MotoNavTW (no licence, out); a KTM-Nav-GEN3 fork; rever-moto (API profile, out); open-dash; twistaway (out).
- **`ferrostar android`**: only 4 results in either order.
  - **cardinal (in)**; vialix (out); navmaster (out); commutescout-app (MIT, Swift/Compose on Ferrostar with live road data, so online; out).

### Licence files opened directly

OsmAnd, Organic Maps (plus the `LICENSES/` folder and the README fork-credit
section), OsmAndmarkec, Ferrostar, Navit, libosmscout, NavOSS, Cardinal
(LICENSE.md via GitLab), and the navmaster tree and README.

---

## What would change this

- **If she will ever publish the app:**
  - OsmAnd's ND/UI terms and Cardinal's GPL-3.0 both bite.
  - Assembling from permissive libraries keeps every option open.
- **If Cardinal's offline-area download turns out to depend on Murena's servers** in a way that can't be pointed at Tower, that weakens it as a reference for the data pipeline. Unverified; it needs reading `cardinal-android/app`.
- **Nobody has measured anything on her phone.** Which phone it is (NATIVE.md open question 1) still isn't answered.
