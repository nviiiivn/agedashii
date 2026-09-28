# Section 5 — Offline routing on the phone

Survey date: **2026-09-26**. Research only; nothing here is a decision.

Every licence below was read from the actual licence file (raw URL or the GitHub
licence endpoint pointing at the file), not from a search summary. Every star
count, commit date and release date came from the GitHub REST API on the survey
date. Anything that could not be verified says **unverified** in place of a
number. Nothing is estimated silently.

`00-already-checked.md` covers navmaster, vialix, Kblack, Ride-Track, MOTO-HUB
and MotorcycleLeanAngleSensor and is not re-derived here. navmaster matters to
this section only as evidence: it ships Valhalla **3.6.3** via valhalla-mobile,
which is the same version this survey independently found pinned.

---

## The four tests

Each option is judged against these, in this order, because that is the order
the rules are stated in.

1. **No signal, no exception.** The route must compute on the phone with the
   radio off. A server fallback is a bonus, never the mechanism.
2. **Fast.** Her Python baseline is the bar, and it is a high one (below).
3. **Motorcycle.** A costing/profile that knows a bike is not a car.
4. **Posted limits from OSM `maxspeed`, offline, per segment.** Already decided
   in §4. This is the test that eliminates the most candidates, and it is the
   one nobody's README answers.

Plus two build questions: what builds the offline data, and where does the
version-coupling trap bite.

---

## The baseline: what her Python already does

Measured, from her own code and disk. This is the number every candidate has to
beat or match, and it is why "just port the A\*" is a serious option and not a
sentimental one.

| | |
|---|---|
| File | `packages/agedashii-washi/agedashii_washi/routing.py`, 1,669 lines |
| Algorithm | Pure-CPython A\* over a packed CSR graph; `math`, `struct`, `heapq`, `array` only |
| Graph | `assets/maps/bayarea.rg` = **86,090,847 bytes** (82 MiB), 725,626 nodes |
| Load | ~72 ms (`array.fromfile`, lazy, on a background thread) |
| Route | ~180 ms for 40 km **on the Pi** |
| Map tiles beside it | `assets/maps/bayarea.mbtiles` = 38,801,408 bytes (37 MiB) |
| Motorcycle | `SPEED_KMH` is least-squares fitted to **Valhalla's motorcycle costing over 45 Bay Area routes** |
| Turn restrictions | `type=restriction` relations packed as a trie; RGRAPH02 only |
| Snapping | `snap_edge` projects onto the nearest *edge*, with part-edge legs stitched on |
| Speed limits | **None.** `guidance.TYPICAL_MPH` exists precisely because "the graph has no maxspeed" |
| Server fallback | `_valhalla_payload(..., costing="motorcycle")` with `use_hills` — so the remote router is already Valhalla motorcycle |

Two facts about this baseline that bear on the decision:

- **The builder is missing.** `routing.py` credits `build_graph.py` ("on the
  Pi") for the `.rg` file. `find` over `/home/nvii` to depth 6 and a grep of the
  whole repo turn up **no `build_graph.py` anywhere** — only references to it.
  The 82 MiB graph exists; the thing that made it does not, at least not here.
  Porting her A\* therefore means porting a router *and* rebuilding its data
  pipeline from the format spec in the docstring.
- **Her known-wrong list is honest and short**: no heading on the snap (wrong
  carriageway on divided highways), multi-via turn restrictions dropped,
  conditional restrictions dropped.

---

## IN: Valhalla via valhalla-mobile

<https://github.com/Rallista/valhalla-mobile> — the binding
<https://github.com/valhalla/valhalla> — the engine

| | valhalla-mobile | valhalla (engine) |
|---|---|---|
| Licence | **MIT**, read from `LICENSE.md`: "The MIT License (MIT) / Copyright (c) 2024 Adventure Consortium Inc (dba Rallista)" | **MIT**, read from `COPYING` (`LICENSE.md` is a one-line pointer to it): "Copyright (c) 2018 Valhalla contributors" |
| Stars / forks | 78 / 51 | 6,262 / 993 |
| Commits | 342 on main | — |
| Last commit | **2026-09-06** ("Correct what the deep-stack change says about the tile getter (#112)") | last push **2026-09-23** |
| Latest release | **0.6.3, 2026-08-28** (0.6.2 same day, 0.6.1 2026-08-23, 0.6.0 2026-08-20, 0.5.1 2026-04-10) | **3.9.0, 2026-09-19** |
| Open issues | 8 | 939 |
| Created | 2024-02-08 | — |
| Android | **Yes**, published to **Maven Central** under `io.github.rallista` | C++ core |
| Fully offline | **Yes.** "All of them run entirely against the tiles on the device." | — |

**Gradle, from the README** (versions as documented there; 0.6.3 is the newest
tag, so the README's 0.6.1 is one release behind):

```
io.github.rallista:valhalla-mobile:0.6.1
io.github.rallista:valhalla-models:0.5.2
io.github.rallista:valhalla-models-config:0.5.2
com.stadiamaps:osrm-openapi:0.0.10   # only for the OSRM response branch
```

**What it exposes on device** (verified from the README's own table and the
Kotlin source tree):

| Action | On device |
|---|---|
| `route` | yes |
| `trace_route` (map matching) | yes |
| `trace_attributes` | **yes** |
| `height` (needs elevation tiles) | yes |
| `sources_to_targets` (matrix) | yes |
| `locate`, `isochrone`, `optimized_route`, `expansion` | **no** |

`locate` is absent. That matters: `locate` is the other API that returns
`speed_limit`, so the posted-limit path on this binding **must** be
`trace_attributes`, exactly as `CHOICES.md` §4 already assumes. PBF responses
are unsupported and error.

### Motorcycle

Valhalla ships a `motorcycle` costing, documented **BETA**, with (verified in
`docs/docs/api/route/api-reference.md`):

- `use_highways` 0–1, default **0.5** — "a rider's propensity to prefer the use of
  highways". *Corrected 2026-09-27: this said 1.0, taken from the docs. The source
  at `src/sif/motorcyclecost.cc` @3.6.3 says 0.5.*
- `use_trails` 0–1, default **0.0** — "a rider's desire for adventure in their
  routes". **0.5 sets the gravel and dirt penalty to ZERO**; the 0.0 default applies
  it eightfold. Her server currently sends 0.5, which on a CB750 on street tyres is
  almost certainly not what she wants.
- `use_hills` and `use_primary` **do not exist** for motorcycle costing, though
  `routing.py:1609` sends both. They are silently ignored.
- `use_tracks` default **0.5** for motorcycles (0 for cars)
- `use_living_streets` default 0.1, `service_penalty` 15, plus every auto option, `top_speed`, and `disable_hierarchy_pruning`

Known problem, from upstream issues: `use_highways = 0` is a **bias, not a
constraint** — the docs say so, and riders report it still returns highways.
Issue **#3119** says `use_primary` "usually has no effect" in motorcycle and
motor_scooter costing. So the knobs are real but blunt.

Her `SPEED_KMH` table was fitted against this exact costing, so switching to it
is a *return* to the model her numbers already describe, not a new behaviour to
re-validate.

### Posted limits — the reason this option wins on §4

Verified in `docs/docs/concepts/speeds/`:

> "If a `maxspeed` tag is available from OSM, that speed is used as the routing
> speed and the `speed_limit` is set to that value."
> "If there is no `maxspeed` or `maxspeed=none` tag, then `speed` is based on the
> OSM `highway` tag."
> "The `speed_limit` contains the posted speed limit, if available, and can be
> used by mobile navigation applications to display the speed limit."
> "The `speed_type` attribute defines whether the assigned routing speed is from
> a speed limit or based on the highway tag."

So the tiles carry **posted and typical, already separated, with a flag saying
which** — that is her posted/typical distinction implemented upstream, not
approximated. `maxspeed=none` serialises as `"speed_limit": "unlimited"`.

Verified in `docs/docs/api/map-matching.md`, `trace_attributes` returns, per
edge: `edge.speed_limit` ("Edge speed limit in the units specified. The default
is kilometers per hour"), `edge.speed`, `edge.speeds_faded`, `edge.road_class`
(motorway…service_other), `edge.way_id`, `edge.names`, and separately
`shape_attributes.speed_limit` per shape segment. A `filters` object with
`{"attributes":[...],"action":"include"}` trims the response to just those keys
— the documented way to keep the payload and the work small, which is what a
1 Hz "what is the limit here" poll needs.

Caveat the docs state plainly: these are Valhalla's normalised attributes, not
raw OSM tags. Recovering a raw tag needs `edge.way_id` + Overpass, i.e. a
network, i.e. not available to her. `speed_limit` itself is fine offline; the
`speed_type` flag distinguishing posted from derived is what the UI needs and it
is in the tile.

### Known problems

- **Two open issues worth reading before starting:** #115 (opened 2026-09-26)
  "Valhalla Dependency chusitoo/flatbrush Fails To Build on 32-bit Android" —
  arm64-only ABI is a workaround, and is what a modern phone wants anyway; #113
  (2026-09-23) `mjolnir.tile_url_gz: true` "cannot work — both HTTP clients
  deliver the wrong encoding", which only bites if tiles are fetched over HTTP
  rather than shipped.
- **Config drift is silent, by their own warning.** `docs/src/bumping-valhalla.md`:
  a config key the generated models don't cover "is dropped in silence —
  `JSONDecoder` and Moshi both skip what they do not recognise — so the config
  that reaches the engine quietly loses that setting."
- **Three minors behind upstream:** binding pins Valhalla **3.6.3**; upstream is
  **3.9.0** (2026-09-19). Not a blocker, but it fixes which version must build
  the tiles (see below).
- **NDK 29.0.14206865** per `docs/development.md` (that is what CI uses).
- **No on-device millisecond benchmark exists.** Searched for one; did not find
  one. The only figures found are a user in valhalla discussion #4509 —
  *"I use valhalla offline routing daily on my 2017 phone which is now really
  old"*, computing 4 alternatives with a 500 km limit — and server-side
  "tens of milliseconds for typical routes". On-device speed is therefore
  **unverified** and has to be measured against her 180 ms / 40 km.

### Fit with §6

`route` can answer in **OSRM format** on Android. Ferrostar's
`CustomRouteProvider` is a single-method interface intended for local route
generation and its parser already eats OSRM JSON. That is a clean seam between
§5 and §6 with nothing custom in between.

---

## IN, as the fallback: her own A\*, ported

Not a library, so no licence question — it is hers.

| | |
|---|---|
| Offline | Yes, by construction. No C extension, no wheel, nothing but the stdlib |
| Speed | **180 ms / 40 km on the Pi**, in *Python*. A Kotlin port over the same packed arrays should be faster, not slower |
| Motorcycle | Already, via costing fitted to Valhalla motorcycle |
| Posted limits | **No.** The graph has no `maxspeed`. This is the one hard gap |
| Turn restrictions | Yes (RGRAPH02 trie) |
| Data | 82 MiB for the Bay Area — versus an estimated 80–200 MB of Valhalla tiles for the same area |

**Why it is in.** It already passes tests 1, 2 and 3, on her hardware, with her
behaviour, and it is the *specification* for whatever replaces it. Zero
integration risk, zero version coupling, no NDK.

**Why it might not win.** Test 4. Posted limits would mean extending the `.rg`
format with a per-edge maxspeed field and rebuilding the pipeline — and
`build_graph.py` is not in the repo or under `/home/nvii`. That is two pieces of
work (format + builder) to reach where Valhalla's tiles already are. Add her own
listed defects (no heading on snap, multi-via restrictions dropped) and the
missing map-matcher, which posted-limit-at-current-position wants anyway.

---

## OUT on the posted-limit test: BRouter

<https://github.com/abrensch/brouter>

| | |
|---|---|
| Licence | **MIT**, read from `LICENSE`: "The MIT License (MIT) / Copyright (c) 2019 BRouter contributors" |
| Stars / forks | 723 / 166 |
| Last push | **2026-09-09** |
| Latest release | **v1.7.10, 2026-07-17** (v1.7.9 2026-04-20, v1.7.8 2025-07-12) |
| Language | Java + Android |
| Android | **Yes**, natively — it *is* an Android project |
| Fully offline | **Yes**, rd5 segment files on disk |

**Embeddable after all.** brouter.de/brouter/offline.html says it "assumes that
at least one of OsmAnd, Locus or OruxMaps is installed — it will not work
otherwise", and `docs/developers/android_service.md` documents an AIDL service
(`IBRouterService.aidl`, package `btools.routingapp`). That is the *supported*
path and it would be unacceptable here — a separate app to install. But two
currently-maintained apps vendor the engine in-process instead:

- **GlanceMap** <https://github.com/GlanceMap/GlanceMap> — MIT, 13★, pushed
  2026-09-25, Wear OS + phone companion, BRouter at `third_party/brouter/`.
- **VeloSpot** <https://github.com/drzeeb/VeloSpot> — MIT, 7★, pushed
  2026-09-25, Kotlin + Compose + MapLibre, `brouter-upstream` as a submodule,
  "routes calculated entirely on-device… no internet needed after the one-time
  segment download".

The repo is modular (`brouter-core`, `brouter-codec`, `brouter-expressions`,
`brouter-mapaccess`, `brouter-map-creator`, `brouter-util`) and ships a
`jitpack.yml`, so in-process use is practical. Good news that costs it nothing.

**Why it is out anyway — `maxspeed` is bucketed, and the buckets straddle the
numbers she needs to show.** rd5 segments *do* carry maxspeed: it is in
`misc/profiles2/lookups.dat`, and profiles use it (`car-vario.brf`,
`moped.brf`). But the lookup format aliases values into one binary code.
`docs/developers/profile_developers_guide.md`: *"Each value can have optional
aliases, these alias values are encoded into the same binary value as the
associated primary value"* and *"A profile must use the primary value in
expressions, as aliases trigger a parse error."* The actual maxspeed lines:

```
maxspeed;0000195097 40 45 25_mph 25mph
maxspeed;0000204646 60 65 35_mph 35mph 40_mph 40mph
maxspeed;0000130108 70 75 45_mph 45mph
maxspeed;0000134522 100 60_mph 60mph 65_mph 65mph
```

**35 mph and 40 mph are the same code.** So are 60 and 65 mph. A Bay Area
arterial posted 35 and one posted 40 are indistinguishable in the data, and the
value a profile can read back is the primary, `60` (km/h). A readout that says
"posted" has to be right; this cannot be.

Other problems, each independently serious:

- **Quadratic cost.** brouter.de/brouter/offline.html: *"processing time that
  scales quadratic with distance, leading to a limit at about 150km in
  air-distance, which is enough for a bikers daytrip."* A 150 km ceiling on a
  motorcycle is the wrong shape of limit.
- **No map matching.** Nothing equivalent to `trace_attributes`, so
  "what is the limit on the road I am on" has no API even if the data were
  precise.
- **Motorcycle = `moped`.** Verified in `docs/users/android_quickstart.md`: the
  default mapping is `motorcar_fast → car-test`, `motorcar_short → moped`. The
  car profile is literally named *car-test*. The often-repeated claim that
  BRouter's car routing ignores turn restrictions now looks **outdated** —
  `brouter-mapaccess/src/main/java/btools/mapaccess/TurnRestriction.java`
  exists — but "car-test" is still what the project calls it.
- Speed is its strong suit (an OsmAnd comparison of <1 s versus 14.5 s
  circulates widely; **unverified**, third-party).
- `poutnikl/Brouter-profiles` (88★) is the community profile collection and is
  the usual starting point for a custom profile — but **no licence file**
  (checked the contents listing; there is a README and `.brf` files, no LICENSE)
  and last pushed **2021-03-30**. Ruled out as a source of copyable material.

---

## OUT on Android: GraphHopper

<https://github.com/graphhopper/graphhopper>

| | |
|---|---|
| Licence | **Apache-2.0** (GitHub licence field; `LICENSE.txt` present) |
| Stars / forks | 6,708 / 1,961 |
| Last push | 2026-09-25 (master is active) |
| Latest release | **11.0, 2025-10-14** — no release in the 11 months since |
| Fully offline | Yes, in principle: a built graph directory on disk |
| Android | **Effectively no, on any current version** |

Three verified findings, any one of which is disqualifying:

1. **The Android module is gone.** Listing the repo tree: `android/` exists at
   tag **1.0** and is **absent from master**. The only official Android demo is
   GraphHopper 1.0, from 2020.
2. **Master targets Java 25.** `pom.xml`:
   `<maven.compiler.target>25</maven.compiler.target>` and `<release>25</release>`.
   Android's toolchain does not consume that. The "it still works, Android
   supports most of Java" line in their README dates from the Java-7-core era.
3. **The maintainers said so.** Issue **#1940** ("Future of 'Offline' Routing",
   opened 2020-02-28, closed): the maintainer would not keep investing; offline
   support forced "avoid updating dependencies… stick to Java 7 in the core";
   Android has "a strange, per-process virtual memory limit" restricting large
   graphs; cross-graph routing needs merging or tiling that was never built.

Worth recording because it is the *only* engine besides Valhalla that answers
the posted-limit test properly, if it ran:

- `max_speed` is a first-class path detail. `docs/web/api-doc.md`: details may
  include `average_speed, street_name, edge_id, road_class, road_environment,
  max_speed and time`, returned as `[fromRef, toRef, value]` triples.
- Precision is good. `core/.../routing/ev/MaxSpeed.java`:
  `new DecimalEncodedValueImpl(KEY, 7, 0, 2, …)` — 7 bits, **factor 2**, i.e.
  2 km/h resolution, with the comment *"if we would store only km/h we could
  live with a factor of 5 … but there is also mph and we don't want to store the
  unit"*. 35 mph survives that round trip; it does not survive BRouter's.
  `MAXSPEED_150` is the autobahn sentinel, `MAXSPEED_MISSING` is `+∞`.
- `map-matching` is a module on master, so a Meili-equivalent exists.
- **No stock motorcycle profile.** `docs/core/profiles.md` + `config-example.yml`:
  profiles are custom-model JSON files (`car.json`, `bike_elevation.json`), and
  turn restrictions are opt-in per profile (`turn_costs.vehicle_types`). A
  motorcycle profile is expressible, not shipped.
- Version-coupling trap of its own, widely reported on their forum: the
  importer that builds the graph and the library that reads it must be the
  **same GraphHopper version**, or `nodes`/`edges` fail to load.

---

## PARTIAL, then out: MassifMaps `routing-lib` (the maintained CARTO fork)

<https://github.com/massif-maps/MassifMaps>

| | |
|---|---|
| Licence | **BSD-3-Clause**, read from `LICENSE`: "BSD 3-Clause License / Copyright (c) 2018, CARTO" |
| Stars / forks | 5 / 2 (renamed in 2026, so the count is not a maturity signal) |
| Last push | **2026-09-26** |
| Releases | **v6.0.2 2026-09-07**, v6.0.1 2026-08-18, v6.0.0 2026-08-16 |
| Android | Yes. JitPack: `com.github.massif-maps:MassifMaps-valhalla-routing:5.0.0` |
| Fully offline | Yes, Valhalla tiles packed as MBTiles (`.vtiles`) |

The honest find of this section: CARTO's Mobile SDK is unmaintained, and this is
its live fork, with a **standalone `routing-lib`** ("the Valhalla offline and
online routing engine without the full rendering stack", no dependency on the
renderer). `routing-lib/{android,ios,native,assets}` is a real directory.
Returns raw Valhalla JSON.

**Why it is out.**

- **No posted limits, no map matching.** A code search across the repo for
  `speed_limit` returns **0 results**; so does `trace_attributes`. Test 4 fails
  outright.
- **It vendors a patched Valhalla 3.4.0.** `massif-external-libs/valhalla/README.txt`:
  *"Hacked version of Valhalla routing engine… Current version is based on
  Valhalla 3.4.0 release"*, with changes to `baldr/graphtileheader.h`,
  `baldr/graphtile.h`, `baldr/directededge.h` among others. Patched tile structs
  mean its tiles are not stock Valhalla tiles and its tile pipeline is its own
  (`nutiteq/mobile-sdk-scripts`). Two incompatible tile worlds, on an older
  engine.

---

## OUT: OsmAnd's router

<https://github.com/osmandapp/OsmAnd>

| | |
|---|---|
| Licence | `LICENSE`, read directly: *"The code in this repository is covered by **GPLv3** (for code) and the artwork is covered by CC-BY-NC-ND 4.0 (for artwork)"*, plus "some artwork uses a proprietary license" |
| Stars / forks | 6,040 / 1,235 |
| Last push | 2026-09-26 (very active) |
| Android | Yes |
| Fully offline | Yes, `.obf` files |
| Posted limits | Yes — OsmAnd displays them from OBF |

Technically the strongest of the "comes with a fork" options: their
**Highway Hierarchy** routing (blog 2025-06-11) claims a **100× speedup** over
bidirectional A\*, adds only **0.5–1 %** to map size, and puts *planet* car
routing in ~800 MB. Their worked example: a 200 km+ route in **13 s** versus
36 s — which, note, is two orders of magnitude slower than her 180 ms/40 km,
though the route is 5× longer and the device is unstated.

**Why it is out.**

- **GPLv3 code plus non-free artwork.** Vendoring the router means GPLv3 on an
  app whose whole point is her own look; the artwork carve-out is a second trap.
- **No standalone artifact.** There is no Maven `osmand-routing`; the practical
  distribution is the JARs inside **OsmAndMapCreator**, and you would be pinning
  internal modules (`RoutePlannerFrontEnd`, `RoutingContext`,
  `BinaryMapIndexReader`) and riding their API churn.
- **Pure-Java engine is the slow one.** Their own docs: the Java engine is
  "approximately 10 times slower than the native engine" with "strict memory
  limitations". The fast path is the C++ one, i.e. the whole native core.
- **OBF format lock-in**, and their own note that HH × Java and HH × C++ "may
  handle the same maps differently".

---

## OUT: Organic Maps' router

<https://github.com/organicmaps/organicmaps>

| | |
|---|---|
| Licence | **Apache-2.0** for code (`LICENSE` points at `LICENSES/Apache-2.0.txt`, read and confirmed) — **but** `DATA_LICENSE.txt`: "compiled binary data files (Data Files), including but not limited to `.mwm` map files… under a separate license from the source code" |
| Stars / forks | 15,506 / 1,607 |
| Last push | 2026-09-26 |
| Android | Yes (C++ core + Java layer) |
| Fully offline | Yes |
| Posted limits | **Yes** — speed limits ship from `maxspeed` in `.mwm`; Android Auto speed-limit display landed in the 2025-10-07 release |

**Why it is out.**

- **Not a library.** The project's own answer to "can I use just the routing?"
  is that *in theory* you can run the C++ core on map data and call its API from
  another language. That is embedding the whole core, not linking a router.
- **`.mwm` lock-in plus a data licence and a mandatory attribution** ("a visible,
  human-readable mention of the Organic Maps Project and a clickable link… in
  About and Main Menu"). Her map data is already built by her own pipeline.
- **No motorcycle profile** (car / bike / foot / transit).
- **`maxspeed:conditional` ignored** (issue #11918, closed as duplicate of
  #12063).
- CoMaps, the Codeberg fork, inherits all of the above.

---

## OUT, briefly, with the reason

| Option | Link | Licence | Activity | Why out |
|---|---|---|---|---|
| **OSRM** | <https://github.com/Project-OSRM/osrm-backend> | BSD-2-Clause | 8,111★, push 2026-09-13 | No maxspeed anywhere in the API — `docs/http.md` annotations are `nodes, distance, duration, datasources, weight, speed` only. No Android target; CH/MLD preprocessing is server-shaped. Test 4 impossible |
| **libosmscout** | <https://github.com/Framstag/libosmscout> | **LGPL** with an app-store linking exception (read from `LICENSE`) | 305★, push 2026-09-26; last *dated* release v2024.06.02.1, plus a rolling `latest` tag 2026-09-26 | Has `MaxSpeedFeature` and a navigation `SpeedAgent`, so limits are possible; but `RoutingProfile.h` offers only `ParametrizeForFoot/Bicycle/Car` — **no motorcycle** — and Android is Qt-first with the JNI path self-described as work in progress. LGPL on a phone app is workable but is extra rope |
| **motis / osr** | <https://github.com/motis-project/osr> | MIT | 59★ (osr) / 593★ (motis), push 2026-09-22 | Genuinely memory-efficient multi-profile C++ router, but pedestrian/bike/car only, **no Android or mobile bindings documented**, and no maxspeed/speed-limit exposure documented |
| **routx** | <https://github.com/MKuranowski/routx> | MIT | 9★, v1.1.0 2026-06-28 | Rust A\* over OSM with profiles and turn restrictions — i.e. a stranger's version of what she already has, minus the Bay Area tuning. Code search for `maxspeed`: **0 hits**. C/C++ FFI only, no JNI/Kotlin |
| **osm4routing2** | <https://github.com/rust-transit/osm4routing2> | MIT | 94★, push 2026-03-20 | Extractor, not a router. Possible input stage only |
| **kernmod/offline-routing-demo** | <https://github.com/kernmod/offline-routing-demo> | licence field `NOASSERTION` — **not verified**, treat as out | 0★, push 2026-09-14 | Rust CCH on-device demo, fixture is Sydney CBD only. Reference at best |
| **mayfourth/graphhopper-android** | <https://github.com/mayfourth/graphhopper-android> | **NO LICENCE** (`license: null`) — **ruled out** | 8★, last push **2013-01-06** | Unlicensed and thirteen years stale |
| **lassana/offline-routing-sample** | <https://github.com/lassana/offline-routing-sample> | BSD-2-Clause | 10★, last push 2016-04-08 | Dead GraphHopper sample |
| **jgrunert/Osm-Routing-App** | <https://github.com/jgrunert/Osm-Routing-App> | GPL-3.0 | 7★, last push 2016-12-20 | Dead, GPL |
| **poutnikl/Brouter-profiles** | <https://github.com/poutnikl/Brouter-profiles> | **NO LICENCE FILE** — **ruled out as copyable material** | 88★, last push 2021-03-30 | Read for ideas only, copy nothing |
| **Kellenintemperate373/kettenblatt** | <https://github.com/Kellenintemperate373/kettenblatt> | Apache-2.0 | 1★, push 2026-09-25 | Advertises "Valhalla" + offline + mbtiles. Opened it: `app/build.gradle.kts` has **no Valhalla dependency at all** and a repo code search for `valhalla` returns **0 hits**. The claim is not in the code |
| **HERE SDK offline routing** | docs.here.com | Proprietary, Navigate licence | — | Closed source, licence-gated |
| **CARTO Mobile SDK** | CartoDB/mobile-sdk | — | Repo now **404 via the API**; wiki only | Unmaintained; superseded by MassifMaps above |

---

## Tile / graph build: what it costs and where it bites

### Valhalla tiles

**What builds them.** `valhalla_build_tiles` from an OSM PBF, plus
`valhalla_build_admins` and the timezone DB (admins matter: a small extract can
clip an admin relation and lose parent-admin info), optionally elevation tiles
for `height`. Never on the device — upstream discussion #4746 is explicit that
on-device tile generation is out (admin DBs, timezone DBs, elevation, storage);
the mobile app "would just need a valhalla config that points it at the
pregenerated tiles".

**On the Pi, without Docker or a C++ build.** Verified on PyPI today:
`pyvalhalla` ships **manylinux_2_28_aarch64** wheels, and the version list
includes **3.6.3, uploaded 2026-02-19** — the exact Valhalla the binding pins.
`python -m valhalla valhalla_build_tiles` then runs the real C++ binaries on
arm64. Official Valhalla **Docker images are x86-only** (issues #3424 and #5124,
with a TODO in the Dockerfile); `nilsnolde/docker-valhalla` is the community
workaround. Latest pyvalhalla is 3.9.0 (2026-09-19), so pinning is a deliberate
act, not the default.

**Input size, measured today by range request:**

| Extract | Bytes | |
|---|---|---|
| Geofabrik `norcal-latest.osm.pbf` | 652,455,872 | 622 MiB |
| Geofabrik `california-latest.osm.pbf` | 1,330,038,779 | 1.24 GiB |

A Bay-Area-only cut with `osmium extract` (already on the Pi) is far smaller
than either.

**Output size — real published tiles, read from GitHub release asset sizes**
(`librescoot/valhalla-tiles`, an offline-nav scooter project, tiles regenerated
2026-09-26):

| Region | `.tar` | `.tar.zst` |
|---|---|---|
| Graz (city) | 25 MB | 8 MB |
| Vienna | 31 MB | 10 MB |
| Luxembourg | 25 MB | — |
| Islas Baleares | 36 MB | 11 MB |
| Alsace | 74 MB | 25 MB |
| Île-de-France (greater Paris) | 158 MB | — |
| Belgium | 345 MB | — |
| Netherlands | 516 MB | — |
| Bayern | 688 MB | — |

**A Bay Area tile set is therefore probably in the 80–200 MB range by analogy
with Île-de-France — this is an ESTIMATE and is UNVERIFIED.** Nobody publishes a
Bay Area figure; the only way to know is to build it. For scale at the other
end, a Europe tile set was reported as 31.4 GB as a tar versus 14 GB packed as
gzipped MBTiles (community figure in discussion #4509, unverified).
`valhalla_build_tiles` handles running out of disk badly — issue #1067, "fails
with cryptic error if disk space runs out", surfacing as an OSMWayNode error
during "Parsing nodes…".

On the phone, `tile_extract` is supported alongside `tile_dir` — verified in
`android/valhalla/src/androidTest/assets/config.json`:
`"tile_extract": ".../files/valhalla_tiles.tar"`. One file, memory-mapped, which
is the right shape for a phone and for her 200–300 MB budget.

### The version-coupling trap, verified in the source

`CHOICES.md` §5 and `NATIVE.md` step 5 both say tiles must match the binding's
Valhalla version. That is correct, and the failure mode is worse than expected.
From `src/baldr/graphtile.cc`:

```cpp
if (int current_version = header_->version()[0] - '0'; current_version != VALHALLA_VERSION_MAJOR) {
  LOG_WARN("Tiles were built with version " + std::to_string(current_version) +
           ", current process runs version " + std::to_string(VALHALLA_VERSION_MAJOR));
}
```

Only the **major** digit is compared, and a mismatch is a **`LOG_WARN`, not a
throw**. The surrounding code `reinterpret_cast`s the mapped bytes straight onto
`GraphTileHeader`, `NodeInfo`, `DirectedEdge` and friends. So tiles built with
**3.9.0** and read by the binding's **3.6.3** produce **no warning at all**
(both are major 3) while any struct-layout change between those minors is read
as garbage. The only hard errors are a too-small file and an end-offset
mismatch.

Practical consequence: pin `pyvalhalla==3.6.3` to the binding's
`valhalla-mobile` version, rebuild tiles in lockstep whenever the binding bumps,
and put the version in the tile filename so a stale pair cannot be shipped by
accident.

### Her own `.rg`

82 MiB for the Bay Area, one file, `array.fromfile` in ~72 ms. Built by
`build_graph.py` **which is not in the repo and was not found under
`/home/nvii`**. Adding posted limits means changing the format (a third version,
RGRAPH03) and the builder.

---

## The two tests nobody's README answers, side by side

| Option | Motorcycle profile | Posted `maxspeed`, offline, per segment | Precision of the limit |
|---|---|---|---|
| **Valhalla / valhalla-mobile** | Yes, `motorcycle` (BETA) + `use_highways`, `use_trails`, `use_tracks` | **Yes** — `edge.speed_limit` and `shape_attributes.speed_limit` via `trace_attributes`, plus `speed_type` saying posted vs derived | Exact tag value, km/h or mph |
| **GraphHopper** | No stock profile; expressible as a custom model | Yes — `max_speed` path detail | 2 km/h buckets (factor 2) |
| **BRouter** | `moped` (car profile is `car-test`) | Data present but **aliased into buckets**; no map matching to ask "here" | **35 mph ≡ 40 mph.** Unusable for a "posted" readout |
| **OsmAnd router** | Yes (OsmAnd has a motorcycle profile) | Yes, from OBF | Unverified |
| **Organic Maps router** | No | Yes, from `.mwm`; conditional limits ignored | Unverified |
| **libosmscout** | No | `MaxSpeedFeature` exists | Unverified |
| **OSRM** | No | **No** | — |
| **motis/osr, routx** | No | Not documented | — |
| **Her A\*** | Yes, fitted to Valhalla motorcycle | **No** — graph has no maxspeed | n/a |

---

## Search log

Seventeen web searches, five GitHub searches (topics, and repo search sorted by
stars *and* by recently updated), plus direct reads of licence files, release
lists, source files and package indexes. Top five results of each search below,
each with the reason it is in or out. Results were opened, not judged from
titles — which is how kettenblatt and BRouter's maxspeed buckets were caught.

**S1 — "valhalla-mobile android"** (the §5 `search:` line)
1. Cruncho420/valhalla-mobile — fork, pinned old. Out.
2. **Rallista/valhalla-mobile** — the upstream binding. **Opened, read, IN.**
3. kdbrooks/valhalla-mobile — fork. Out.
4. tristanpinto/valhalla-mobile — fork. Out.
5. valhalla/valhalla discussion #4746 "How would Valhalla be used on a mobile device?" — **opened**; gives the "pregenerated tiles only" answer. In as evidence.
(Also returned: a Netflix Vikings game and Ragnarok Online. Out, obviously.)

**S2 — "graphhopper android offline routing"** (the §5 `search:` line)
1. mayfourth/graphhopper-android — **no licence, 2013**. Out, hard.
2. mapsforge-dev thread — historical. Out.
3. **graphhopper issue #1940 "Future of 'Offline' Routing"** — **opened**; the maintainers' own verdict. In as evidence.
4. graphhopper/graphhopper — **opened**; Java 25 target, `android/` gone from master. Out on Android, in as evidence.
5. GraphHopper forum "Start an Android App for offline routing with custom data" — version-mismatch pitfalls. In as evidence.

**S3 — "brouter motorcycle profile offline routing android"** (the §5 `search:` line)
1. poutnikl/Brouter-profiles wiki — profile collection; repo has **no licence**. Out as material.
2. poutnikl/Brouter-profiles — same. Out.
3. osmand.net BRouter page — documents BRouter-as-external-engine. In as context.
4. **brouter.de/brouter/offline.html** — **opened**; the 150 km quadratic limit and the "needs OsmAnd/Locus/Orux" requirement. In as evidence.
5. **abrensch/brouter** — **opened**; MIT, v1.7.10, modular, `lookups.dat` read. In, then out on bucketing.

**S4 — "on-device offline routing engine android open source library 2026"**
1. kernmod/offline-routing-demo — Rust CCH, Sydney-only, licence unverified. Out.
2. OsmAnd (Wikipedia) — pointer to the fork option. Context.
3. CoMaps (Wikipedia) — Organic Maps fork; same limits. Out.
4. graphhopper/graphhopper — as S2. Out.
5. `offline-maps` GitHub topic (Kotlin) — already listed in `00-already-checked.md`. Browsed.
(This search surfaced GlanceMap, VeloSpot and kettenblatt, all three opened below.)

**S5 — "valhalla trace_attributes speed_limit offline map matching"**
1. valhalla-docs map-matching api-reference — deprecated repo; superseded by the in-tree docs. Out as a source.
2. valhalla issue #5215 "More speed information in trace_attributes" — can't get all speed flavours in one request. In as a known limitation.
3. valhalla map-matching docs — redirect; **followed and read in-tree instead**. In.
4. **`docs/docs/api/map-matching.md`** (read raw) — `edge.speed_limit`, `shape_attributes.speed_limit`, filters. **In, decisive.**
5. **`docs/docs/concepts/speeds/`** — **opened**; maxspeed → `speed_limit`, `speed_type` flag. **In, decisive.**

**S6 — "valhalla_build_tiles tile size disk space regional extract requirements"**
1. **librescoot/valhalla-tiles** — **opened, release assets measured.** The only real per-region tile sizes found. In.
2. valhalla issue #1067 — cryptic out-of-disk failure. In as a known problem.
3. Interline Valhalla Tilepacks — legacy hosted tilepacks. Out.
4. valhalla discussion #3879 (planet tile tars) — Europe tar-vs-MBTiles figures. In, unverified.
5. Valhalla "Why tiles?" docs — the design rationale for mobile. In as context.

**S7 — "valhalla motorcycle costing options use_highways use_trails"**
1. **valhalla issue #1258 "Add motorcycle costing"** — origin of `use_highways`/`use_trails`. In.
2. valhalla-docs turn-by-turn api-reference — deprecated copy. Out as a source.
3. **valhalla issue #3119** — `use_primary` has no effect on motorcycle. In as a known problem.
4. valhalla-http-client-go — irrelevant client. Out.
5. **`docs/docs/api/route/api-reference.md`** (read raw) — motorcycle costing BETA, defaults. **In, decisive.**

**S8 — "OsmAnd routing engine standalone java library obf routing offline reuse"**
1. osmand.net navigation troubleshooting — Java engine ~10× slower, memory-limited. In as evidence.
2. osmand group "routing in external app by using Osmand" — external-app pattern. Context.
3. osmand.net "Debug OBF Routing on a PC" — OsmAndMapCreator is the practical distribution. In.
4. **osmand.net/blog/fast-routing** — **opened**; HH 100×, +0.5–1 % map size, 200 km in 13 s. In.
5. OSM wiki OsmAnd — GPL + OBF lock-in. In as evidence. Verdict: out on licence and packaging.

**S9 — "Organic Maps routing engine C++ reuse library separate from app mwm"**
1. organicmaps GSoC-2022 ideas — confirms routing lives in the core. Context.
2. organicmaps/organicmaps — **licence files opened**: Apache-2.0 code, separate `DATA_LICENSE.txt` for `.mwm`. In as evidence, out as a library.
3. organicmaps discussion #8081 (map generation for tests) — mwm pipeline. Context.
4. **organicmaps discussion #8041** — the project's own "in theory you can run the C++ core… and use its API" answer. In, decisive: not a library.
5. Project-OSRM/osrm-backend — suggested as the embeddable alternative; checked separately and out.

**S10 — "awesome openstreetmap list routing engines offline mobile awesome-list"**
1. OSM wiki *Routing* — the de-facto list; no `awesome-*` repo exists for this. Browsed.
2. valhalla/valhalla — mobile-by-design claims. In.
3. graphhopper/graphhopper — as S2. Out.
4. OSM wiki *Open Source Routing Machine* — OSRM's offline consumers are desktop/server. Out.
5. OSM wiki *Using OpenStreetMap offline* — surfaced **libosmscout** and Spatialite VirtualNetwork; libosmscout followed up in S12, Spatialite out (§7 material, not a road router).

**S11 — "Ferrostar offline routing valhalla on device local route provider android"**
1. igalmarino/vialix — already in `00-already-checked.md`; hosted Valhalla, no offline. Out.
2. **valhalla discussion #4509 "Official Support for Valhalla Build Artifacts"** — **opened**; the 2017-phone datum, Europe tile sizes, valhalla-mobile named as the path. In.
3. **Ferrostar Book, Android** — `CustomRouteProvider`, "if you are doing offline route calculation". In, decisive for the §5/§6 seam.
4. **Ferrostar Book, Route Providers** — `RouteAdapter` vs `CustomRouteProvider`; OSRM-shaped JSON. In.
5. valhalla issue #4704 "Calculating routes Offline in Android" — people hand-rolling JNI; the reason to take the binding. In as evidence.

**S12 — "libosmscout android routing library offline support status"**
1. libosmscout `Documentation/Android_Qt.txt` — Qt is the supported Android path. In as evidence.
2. libosmscout JNI sources — present but WIP. In as evidence.
3. **Framstag/libosmscout** — **licence opened**: LGPL + app-store linking exception. In, then out on no-motorcycle.
4. vyskocil/ and Karry/ forks — downstream forks. Out.
5. janbar/osmin wiki — a real Android app on a libosmscout fork; proof it works. In as context.

**S13 — "CARTO mobile SDK offline valhalla routing deprecated MassifMaps SDK android"**
1. **massif-maps/MassifMaps** — **opened**; BSD-3, v6.0.2, `routing-lib`, patched Valhalla 3.4.0, no `speed_limit`. In, then out.
2. jackfrignani05-ai/MassifMaps — fork. Out.
3. CartoDB/mobile-sdk wiki "Valhalla offline" — `.vtiles`, Valhalla 2 vs 3 history. Context only; repo 404s via API. Out.
4. `ValhallaOfflineRoutingService` javadoc — the deprecated API shape. Out.
5. carto.com/blog/mobile-offline-routing — marketing for the dead SDK. Out.

**S14 — "valhalla_build_tiles aarch64 arm64 raspberry pi docker image build tiles"**
1. **valhalla issue #3424** — ARM64 container ask, 2021. In as evidence.
2. **valhalla issue #5124 "Docker multi-arch builds"** — official image is x86-only, TODO in the Dockerfile. In, decisive for the Pi.
3. nilsnolde/docker-valhalla — the community image people are pointed at. In as an option.
4. valhalla/docker (master) — upstream Dockerfile. In as evidence.
5. **pyvalhalla on PyPI** (followed from this search, then **verified directly against the PyPI JSON API**) — aarch64 wheels, and **3.6.3 exists**. In, decisive.
(Noise: a Valheim game server for Pi clusters, and a Raspbian image tool also called "valhalla". Out.)

**S15 — "Organic Maps speed limit maxspeed display offline mwm routing"**
1. organicmaps issue #11918 — `maxspeed:conditional` ignored. In as a known problem.
2. Organic Maps Facebook post — speed-limit signs announcement. Weak source; not relied on.
3. OSM wiki *Speed limits* — tagging background. Context.
4. **organicmaps.app/news/2025-10-07** — Android Auto speed limits shipped. In.
5. organicmaps discussion #2301 "Speed Limits" — "the data is already used by the routing algorithm". In as evidence.

**S16 — "rust osm routing engine library contraction hierarchies mobile uniffi bindings offline"**
1. offline-protocol-uniffi — unrelated (mesh messaging). Out.
2. lib.rs geospatial index — where a pure-Rust CCH crate is listed; no mobile bindings. Out for now.
3. **MKuranowski/routx** — **opened**; MIT, v1.1.0, A\*, no maxspeed, C/C++ FFI only. Out.
4. CCH survey (arXiv 2502.10519) — algorithm reading, not a library. Out.
5. **rust-transit/osm4routing2** — MIT extractor, not a router. Out as a router.

**S17 — "valhalla offline routing on phone route calculation time benchmark milliseconds android tiles"**
1. valhalla issue #4704 — no numbers. Out for this purpose.
2. valhalla/valhalla README — "cross compilation… to enable routing on offline portable devices". In as context.
3. valhalla issue #1530 "Offline (and online) routing for mobile devices assistance" — no numbers. Out.
4. **valhalla discussion #3743 "Routing computation performance"** — server-side targets ≤500 ms, tuning advice. In as weak context.
5. Interline "Intro to Valhalla" — design claims, no phone numbers. Context.
**Conclusion of S17: no published on-device benchmark exists. On-device speed is unverified and must be measured.**

**GitHub searches**

- **G1 — `topic:offline-routing`, sorted by stars.** Only 2 repos: atak-vns-offline-routing-generator (MIT, 15★, tile generator for ATAK — out, not a phone router) and v2rmp (Apache-2.0, 0★, Rust — out, nothing routing-shaped). A near-empty topic.
- **G2 — `topic:routing-engine`, sorted by stars.** Top 12: ai-dynamo/dynamo (LLM router, out), **Project-OSRM/osrm-backend** (checked, out), **graphhopper** (checked, out), **valhalla** (IN), JLRoutes (iOS URL router, out), freerouting (PCB, out), GIScience/openrouteservice (GPL-3 server on GraphHopper, out), route-composer (iOS nav, out), simple-php-router (out), **motis-project/motis** (MIT, 593★ — led to **osr**, checked, out), fasthttp/router (out), openrouteservice-py (out).
- **G3 — repo search "offline routing android", sorted by stars.** abrensch/brouter (checked), poutnikl/Brouter-profiles (no licence, out), **GlanceMap** (opened — vendors BRouter at `third_party/brouter/`), lassana/offline-routing-sample (2016, out), mayfourth/graphhopper-android (no licence, out).
- **G4 — same query, `pushed:>2025-06-01` sorted by recently updated.** **kettenblatt** (opened — the Valhalla claim is not in its build file, out), **VeloSpot** (opened — `brouter-upstream` submodule, in-process BRouter), GlanceMap (as above), RoueLibre (GPL-3 bike-share, out), plus noise (flight trackers, SMS forwarders, an NVR). No new router.
- **G5 — `topic:valhalla`, `pushed:>2026-01-01`, sorted by recently updated.** 71 repos. Relevant: **librescoot/valhalla-tiles** (tile sizes, used above), **RashadAnsari/amgraph** (MIT, "one routing graph for AM-licence vehicles in the Netherlands" — moped routing, NL-only, Python; out but interesting), jakevig-design/drivelog (local map matching, Python; out), NavOSS and kettenblatt (already covered). Remainder unrelated.

---

## What is unverified, and would change an answer

- **Valhalla tile size for a Bay Area extract.** Estimated 80–200 MB by analogy
  with Île-de-France's measured 158 MB. Not published anywhere. Build it to know.
- **On-device route latency for Valhalla**, against her 180 ms / 40 km. No
  published figure exists on any phone. Must be measured; it is the one number
  that could still disqualify the front-runner.
- **`valhalla_build_tiles` peak RAM and wall time for a Bay Area cut on a Pi 5.**
  Only planet-scale guidance (16 GB+, SSD) and one third-party "≈1 GB peak disk
  for an Austrian city extract" figure were found.
- **OsmAnd and Organic Maps speed-limit precision** (bucketed or exact). Not
  checked, because both are out on licence/packaging first.
- **BRouter's <1 s versus OsmAnd's 14.5 s** comparison — third-party, unverified.
- **`build_graph.py`** — referenced by her own router, not present in the repo
  and not found under `/home/nvii` to depth 6. It may exist elsewhere; if it
  does, the cost of the port-her-A\* option drops a lot.
- **MassifMaps `routing-lib` JitPack coordinate** is documented as `5.0.0` while
  the repo is releasing `v6.0.2`. Not chased, since it is out on `speed_limit`.
