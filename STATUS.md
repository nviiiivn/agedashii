# NVII-DASH — board

Updated 2026-09-20. Branch `app-config`. 226 tests green. Six artifacts built.

Read this file first. It is the answer to "what has actually been done".

---

## MERGED — done and verified

| # | Work | Evidence |
|---|---|---|
| M1 | Offline map ships | `assets/maps/bayarea.mbtiles` 38.8 MB, 11,716 tiles z10–15, rendered from your own `.pbf`. Served through the app's tile server; TMS flip proved 3 ways (97.5% agreement vs an independent coastline ray-cast, 68% when deliberately mirrored) |
| M2 | Offline routing ships | `assets/maps/bayarea.rg` 78.7 MB, 725,617 nodes. Loads 52 ms. Oakland→Fremont 26.5 mi / 176 ms, real turn-by-turn. 200/200 legality selftest, one-ways honoured, within 15% of Valhalla |
| M3 | Tile server | Loopback HTTP inside the app. 36 parallel tiles 1472 ms → **58 ms** after fixing Nagle + listen backlog |
| M4 | Offline store | SQLite cache + outbox. Offline burns no retry; failures park, never delete; head-of-line blocking is per-path. Cache scoped per server |
| M5 | XNAV | Live map, GPS, track recording, GPX upload round-tripped to your server |
| M6 | Wat2Do | 77 → 976 lines. Date nav, sub-task hierarchy, pain/spoons, template editor, full mission generator, routing |
| M7 | Weather + POI | 21 locations, real rain %. Fabricated "air quality" chip deleted. Overpass fixed — 47 POIs |
| M8 | Lab | Qbit / Services / Network on real endpoints. "Things" removed — no data source existed |
| M9 | Writes everywhere | Journal, Garage, Trips, Vault, FoodZen, EatZen, BPM all write to the server |
| M10 | Settings | Layout editor, units, sodium target, refresh, diagnostics |
| M11 | **Website POI fix** | **Live now.** Grouped bbox 504s, per-statement returns 18 stations |
| M12 | Browser MCP | `pw` registered against the bundled Chromium — works from next session |

### Bugs found and fixed (all silently dead — tests passed while they were broken)
- `Dropdown.on_change` never fires in Flet 0.86 (it is `on_select`) — Garage + EatZen
- `FilledButton.text` does not exist — XNAV Record never said "Stop"
- `ft.Page.open/close` do not exist — **the "add food from label" dialog crashed on open**
- `Map.move_to` is a coroutine — did nothing un-awaited
- `rebuilder` wiped the topbar and nav bar after every write
- `api._post` swallowed the server's error text
- Wat2Do sub-task keys differed between app and website — ticking one never ticked the other
- CI was green while uploading **zero** artifacts

---

## IN FLIGHT

| # | Work | Owner | State | Gate |
|---|---|---|---|---|
| W1 | Verify the APK actually bundles `assets/` | me | running | unzip shows `bayarea.*` inside the APK |
| W2 | Install new AppImage on Void | me | running | launches on `.254`, Settings reports offline maps |
| W3 | Visual pass: every page, tab, component | me | queued | a screenshot of each screen |

## TODO

| # | Work | Gate |
|---|---|---|
| W4 | Routing: snap to nearest road point, not nearest junction | reroute from mid-block is accurate |
| W5 | Routing: honour turn restrictions | no banned lefts |
| W6 | `_wat2do_parts.py` writes bypass the outbox | Wat2Do saves work offline |
| W7 | `sync()` on app resume | queue drains when wifi returns |
| W8 | Server rejects `eatzen`/`bpm`/`custom_foods` kinds | those save from the app |
| W9 | Future Finds sub-tab missing from the app | parity with the website |
| W10 | Sensors: accelerometer, gyro, lean, g-force | deferred by you |

## KNOWN LIMITS

- **Nothing has been visually verified.** No screen has been seen. This is where the remaining bugs are.
- Routing snaps to junctions — the first/last 100–200 m mid-block can be wrong. Guarded: beyond 2 km it refuses and falls back rather than routing you from somewhere you are not.
- Web/PWA excludes the map + graph — a browser build should not be a 117 MB download.
- Overpass 504s on wide scans; the app names the categories it had to drop instead of presenting a short list as complete.
