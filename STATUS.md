# Agedashii — board

Updated 2026-09-20 17:50. Branch `app-config`, head `11e2ee9`.
**282 app tests green. 66 website tests green.**

Read this file first. It is the answer to "what has actually been done".

---

## READ THIS FIRST — one question for you

**Did you enter any food in EatZen, or any BP readings, today between about
11am and 5pm?**

If yes, they are gone. If no, nothing was lost. Two probes of the save
endpoint wrote empty lists over the FoodZen collections — a subagent's at
16:53 (eatzen, bpm, custom_foods, nutrition_overrides) and mine at 17:10,
which also caught `lists.json`, your grocery lists. No backup existed at the
time; the directory is gitignored and the disk has no snapshots.

That is now impossible: every save keeps the previous 20 versions in
`dashbored-foodzen/backups/`. Verified working under real writes.

---

## DONE — merged and verified

| # | Work | Evidence |
|---|---|---|
| M1 | Offline map ships | `assets/maps/bayarea.mbtiles` 38.8 MB, 11,716 tiles z10–15, rendered from your own `.pbf`. TMS flip proved three ways |
| M2 | Offline routing ships | `assets/maps/bayarea.rg` 86.09 MB, RGRAPH02. Loads 84 ms. Oakland→Fremont 42.7 km / 11 turns |
| M3 | Tile server | Loopback HTTP inside the app. 36 parallel tiles 1472 ms → **58 ms** after fixing Nagle + listen backlog |
| M4 | Offline store | SQLite cache + outbox. Offline burns no retry; failures park, never delete; cache scoped per server |
| M5 | XNAV | Live map, GPS, track recording, GPX round-tripped to your server |
| M6 | Wat2Do | 77 → 976 lines. Date nav, sub-tasks, pain/spoons, template editor, mission generator, routing |
| M7 | Weather + POI | 21 locations, real rain %. Fabricated "air quality" chip deleted. Overpass fixed — 47 POIs |
| M8 | Lab | Qbit / Services / Network on real endpoints |
| M9 | Writes everywhere | Journal, Garage, Trips, Vault, FoodZen, EatZen, BPM all write to the server |
| M10 | Settings | Layout editor, units, sodium target, refresh, diagnostics |
| M11 | Website POI fix | Live. Grouped bbox 504s; per-statement returns 18 stations |
| **W4** | **Route from where you are** | Edge-level snapping. Median snap error **72 m → 6.1 m**, worst 681 m → 8.0 m. First instruction vs Valhalla: 32.7° → **2.4°** off. Length vs Valhalla: 2.51% → **0.22%** |
| **W5** | **Turn restrictions** | 21,806 applied as a trie. Over 120 random routes: **9 banned turns → 0**, for +0.08% time. U-turns now cost 40 s — without that the router laundered banned turns by turning around |
| **W6** | **Wat2Do writes go through the outbox** | Ticks queue offline, coalesce, and land as one POST when signal returns |
| **W7** | **Outbox drains on resume** | Sync chip in the topbar; drains on launch, on RESUME, on tap, and every 20 s while anything is pending |
| **W8** | **App can save EatZen / BPM / custom foods** | The app's allowlist had 6 kinds, the server had 10, so every such write was refused before the outbox. All three now return `(True, 'saved')` and read back |
| **W9** | Future Finds in the app | Radius chips, 17 categories, someday list, photos, street view. Saves queue offline |
| **W11** | **FoodZen data is backed up** | 20 versions per collection, unchanged saves skipped, never blocks a save |
| **W12** | **Failed saves are visible** | Four call sites ignored HTTP status — a rejected save looked identical to a success |
| **W13** | **CI publishes what it built** | Uploads now run before the web build, which is the step that OOM-kills the runner |

### Bugs found and fixed — all silently dead, tests passed while they were broken

- `Dropdown.on_change` never fires in Flet 0.86 (it is `on_select`) — Garage + EatZen
- `FilledButton.text` does not exist — XNAV Record never said "Stop"
- `ft.Page.open/close` do not exist — the "add food from label" dialog crashed on open
- `Map.move_to` is a coroutine — did nothing un-awaited
- `rebuilder` wiped the topbar and nav bar after every write
- `api._post` swallowed the server's error text
- Wat2Do sub-task keys differed between app and website — ticking one never ticked the other
- CI was green while uploading **zero** artifacts
- CI built the APK and Linux target, then threw both away when the web step died
- The shipped AppImage contained the whole git history (97 objects) and a 296 MB copy of the APK
- `fzSave` ignored HTTP status; Wat2Do's save had `.catch(()=>{})`; power actions claimed "sent" on any response
- U-turns were free, so the router drove past a banned turn, turned round, and came back

---

## IN FLIGHT

| # | Work | State | Gate |
|---|---|---|---|
| W2 | Install the new AppImage on Void `.254` | build running (run 35548788918) | launches, Settings reports offline maps |
| W3 | Behavioural sweep of every control | agent asked to hand back | a reproducible defect, or pass, per screen |

## TODO

| # | Work | Gate |
|---|---|---|
| W10 | Sensors: accelerometer, gyro, lean, g-force | deferred by you |
| W14 | Snapping takes no heading | on a divided highway a fix nearer the opposite carriageway starts you the wrong way. Needs a `heading` argument from the caller |

## KNOWN LIMITS

- **Website wiring is verified; website *behaviour* is not fully.** 8 tabs, 26
  sub-view switches, 528 handlers and 150 functions all resolve with zero
  failed requests and zero JS errors. That proves nothing is dead. It does
  not prove every control does the *right* thing.
- Restrictions can legitimately strand a pocket. Rather than refuse, the
  router retries without them and says so — "the only way there goes through
  a turn the map says is banned, check the signs".
- Web/PWA excludes the map and graph: a browser build should not be a 117 MB
  download, and baking it in is what OOM-kills the runner.
- Overpass 504s on wide scans; the app names the categories it had to drop
  rather than presenting a short list as complete.
- `assets/maps/bayarea.rg` is 82 MB inside git, which GitHub warns about on
  every push. The repo is ~76 MB of history on top of that. Worth moving to
  LFS or a release asset if clone time starts to hurt.
