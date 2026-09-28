# How Agedashii Moto works: reference for fixing Navi

Written 2026-09-27 by session nvii-51, read-only. No source was edited.
Every claim is tagged **[verified]** with a file:line, or **[unverified]**.
Paths are relative to `NVII-DASH/`.

## The headline

**Moto's surface switch is not cheap, and Moto doesn't cache its map.** Moto
rebuilds its whole page on every navigation, and its first surface builds two
brand-new `fmap.Map` controls every time it renders. Moto still works. So
whatever stops Navi from opening a page is **not** "rebuilding is expensive" or
"the map gets rebuilt". It's something Navi does that Moto doesn't. The
differences are listed at the end, unranked and unproven.

## 1. The shell: how Moto switches surfaces

- **[verified]** One function does every navigation: `render(key)` in
  `apps/moto/main.py:672-717`. The bottom bar (`bottom_pick`, :639), drawer rows
  (`_drawer_go` → `page.run_task(_drawer_go_async)` → `close_drawer()` then
  `render`, :416-421), drawer sub-rows (`go_sub`/`go_surface`, :395-414), the
  theme picker (:550), Arrange (:604), `S.rerender` (:725) and `S.goto` (:734-739)
  all end in `render`.
- **[verified]** `render` rebuilds everything, every time, synchronously:
  1. `panels.begin_render()`, `paint_sync()` (:680-681)
  2. `body.content = build_surface(page, key)`: the whole surface is built from scratch (:682)
  3. `page.drawer = nav_drawer()`: a new drawer, or `None` when the hamburger is hidden (:688)
  4. `page.controls.clear()` (:689)
  5. new topbar row + `body` + new `NavigationBar` in a new `ft.Column` (:690-700)
  6. wrapped in a new `ft.SafeArea` (:708); wrapped in an `ft.Stack` with the CRT
     overlay **only if** `prefs.crt_overlay()` is on (:714-715)
  7. `page.add(shell)`, `page.update()` (:716-717)
- **[verified]** The only objects reused across renders are the `body` Container
  (:251, only its `.content` changes) and the sync chip controls (:288-294).
- **[verified]** There's no subtree `update()`; it's always `page.update()` after a
  full rebuild.
- **[verified]** One place deliberately avoids `render`: the drawer chevron
  (`_toggle_drawer_section`, :423-437) swaps only `page.drawer` and calls
  `page.update()`. Its comment says a full render "leaves the old frame up and
  drops the taps that land during it".
- **[verified]** The bottom bar's `selected_index` is always a valid index
  (:651-652, with the comment "Flet asserts on it").

## 2. The map: how Moto creates and updates it

- **[verified]** The XNAV surface has **no map**. `apps/moto/agedashii_moto/pages/xnav.py`
  (68 lines) is a text stub ("NOT BUILT YET"). The maps she pans in Moto are
  on the **Trips** surface.
- **[verified]** `trips.build` builds **all four tabs eagerly**:
  `_tabs(page, [("Selekt", _selekt(page)), ("Produce", _produce(page)), ("Rides", _rides(page)), ("vNav", vnav.build(page))], ...)`
  (`pages/trips.py:85-87`). They're passed as built controls, not callables, so
  `surface._tabs` builds them all at once (`packages/agedashii-washi/agedashii_washi/surface.py:50-52`).
  Trips is `FIRST_PAGE` (`main.py:177`), so this happens at launch and on every
  render of Trips.
- **[verified]** Each Trips render therefore constructs:
  - a **new** `fmap.Map` in `_planner` (`pages/trips.py:431`, reached from `_selekt` :307)
  - a **new** `fmap.Map` in `vnav.build` (`pages/vnav.py:154-162`)
  - a **new** `fgeo.Geolocator` in `vnav.build` (`pages/vnav.py:219-225`), appended
    to `page.services` every time and never removed. vNav doesn't cache it.
- **[verified]** Nothing about either map is cached at module level. What
  survives a rebuild is **state, not controls**: `_track` and `_NAV` dicts
  (`vnav.py:61-69`), `_FOLLOW` in trips.
- **[verified]** Trips' guidance geolocator *is* cached: `_geolocator(page)`
  creates it on first use only (`trips.py:674-712`, "every rebuild would otherwise
  register another service"). Its `on_position` calls `_FOLLOW["render"]`,
  a re-render callback (:691-693).
- **[verified]** On a GPS fix, vNav's `on_position` (`vnav.py:182-213`) mutates
  existing controls (`position_marker.coordinates`, `track_line.coordinates`,
  text values) and calls `page.update()`. It doesn't rebuild the map.
- **[verified]** Framing a route uses `await the_map.move_to(...)` (`vnav.py:784-800`).
- **[verified]** The tile server is started idempotently: `tileserver.start()`
  returns the existing URL when the same archive is already served
  (`agedashii_washi/tileserver.py:205-207`), so rebuilding the map doesn't
  start a new server.
- **[unverified]** Whether vNav's Geolocator streams positions as soon as it's
  registered, or only after "find me" is tapped. Its config is
  `GeolocatorConfiguration(accuracy=BEST)` with no interval (`vnav.py:220-221`).

## 3. What runs on a timer or subscription

AGEDASHII's belief was "nothing". **That's wrong**, though the timer is cheap:

| What | Where | Rate | Cost |
|---|---|---|---|
| `watch_outbox` loop | `main.py:357-377`, started `:748` | every **20 s** (`SYNC_POLL_SECS`, :173) | [verified] checks `M.pending_count()`; does nothing more unless writes are queued, then drains on a thread + `page.update()` |
| App lifecycle handler | `main.py:379-387` | on resume/show | [verified] `sync_now()` → `page.run_thread(drain_and_paint)` |
| `fill_the_rest` | `surface.py:73-83` | once per `_tabs` with callable tabs | [verified] Trips/Garage pass controls, so it doesn't run for them; Weather passes callables (`weather.py:1622`) |
| vNav GPS `on_position` | `vnav.py:219-223` | [unverified] platform default | mutate + `page.update()` |
| Tile server | `tileserver.py:221-235` | daemon thread | serves tiles |
| Background refresh threads | `agedashii_washi/store.py:534`, `api.py:506` | per read / probe | [verified] they exist; per-call cost not measured |
| Routing graph loader | `agedashii_washi/routing.py:241-245` | thread, on demand | [verified] only when routing is requested |

**[verified]** Moto attaches no sensors (accelerometer, gyro, barometer,
magnetometer) and no audio recorder anywhere. A grep for those, and for
`services.append`, finds only the two Geolocators.

## 4. Launch: `main()` to the first frame, in order

All **[verified]**, `apps/moto/main.py`:

1. `page.title`, `apply_theme(page)`, `page.padding = 0` (:215-224)
2. `S.prefs = ft.SharedPreferences()` (:229)
3. `await config.load(S.prefs)`: restores the server address and checks it still answers (:233)
4. `await prefs.load(S.prefs)`, `prefs.apply_runtime()` (:237-238)
5. `await motoprefs.load(S.prefs)`, `apply_theme(page, motoprefs.theme())` (:243-249)
6. creates `body`, the sync chip, and the handlers; sets `page.on_app_lifecycle_state_change` (:251-387)
7. `S.rerender`, `S.goto` (:725-739)
8. **`render(FIRST_PAGE)`**, i.e. Trips: all four tabs, both maps, one Geolocator,
   drawer, topbar, bar, then `page.update()`. This is the first frame (:741).
9. `sync_now()` on a thread; `S.sync_watcher = page.run_task(watch_outbox)` (:745-748)

There's no loading screen, no staged boot, and no worker threads receiving
`page`.

## 5. Deliberate performance decisions in Moto (as commented in the source)

- **[verified]** The chevron path skips `render` (`main.py:423-437`).
- **[verified]** Storage writes go *behind* the frame via `run_task` (:550-555, :435-437).
- **[verified]** The drawer isn't built at all when the hamburger is hidden (:684-688).
- **[verified]** The CRT overlay is 0 controls when off, 2 when on (:709-715).
- **[verified]** Tab bodies passed as callables build lazily (`surface.py:28-41`);
  Trips doesn't use this.
- **[verified]** The tile server uses `request_queue_size = 128` and silences
  client-disconnect errors (`tileserver.py:215-231`).
- **[verified]** The guidance geolocator is created once and reused (`trips.py:674-712`).

## 6. Where Navi's shell differs from Moto's

None of these has been proven to be the cause. They're here so a second
session can test each one.

- **[verified]** Navi's shell also does a full `page.controls.clear()` →
  `page.add()` → `page.update()` per navigation (`apps/navi/main.py:298-350`).
  Structurally it's the same as Moto.
- **[verified]** Navi **always** wraps the body in an `ft.Stack` with a
  positioned menu button (`apps/navi/main.py:340-342`). Moto only uses a Stack
  when CRT is on. Navi's own comment (:333-339) says a Stack sizing problem
  "froze the app on both of her phones" before.
- **[verified]** Navi's menu button is a `Container(on_click=lambda e: page.run_task(open_menu))`
  floating over the map (:218-224). Moto's is an `IconButton` with an async handler
  in the topbar row (:623-624).
- **[verified]** Navi caches its geolocator in module `_M` across renders and
  re-points its handlers each render (`agedashii_navi/pages/ride.py:490-532`).
  That `on_position` calls `paint()` + `page.update()` per fix, plus
  `page.run_task(_apply_daylight)` per fix when `auto_theme` is on (:479-484).
  **[unverified]** whether it keeps firing and repainting Ride's detached
  controls while another surface is on screen.
- **[verified]** Navi runs boot stages on worker threads and hands them `page`
  (`apps/navi/main.py:185`, `agedashii_navi/boot.py` `run_stage(key, page)`).
  Moto never passes `page` to a thread.
- **[verified]** Navi re-renders on `page.on_resized` when orientation flips
  (`apps/navi/main.py:357-371`). Moto has no resize handler.
- **[verified]** Navi v1.6.0 caches the `fmap.Map` in `_MAP` and reuses the same
  control instance across renders (`agedashii_navi/pages/ride.py:902`). **Moto
  never does this.** **[unverified]**, needs checking: whether Flet 0.86 copes
  with a control instance that was mounted, removed by `page.controls.clear()`,
  and added again in a new parent tree. If it doesn't, v1.6.0 could make things
  worse, not better.
