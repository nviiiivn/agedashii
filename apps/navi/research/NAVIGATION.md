# Why Navi's pages won't open: shell navigation, Navi vs Moto

Read-only audit, 2026-09-27, of `apps/navi` at VERSION_NAME 1.6.0
(`version.py:30`) against `apps/moto`. No source was edited.

**Labels.** **VERIFIED** means I read it in the file at the line given.
**BELIEVED** means it is inference and has not been observed on a phone or an
emulator. Nothing here has been run on her Pixel 8 Pro.

The question kept in front the whole time: why can she not *open* a page at
all? Slowness alone doesn't explain that.

---

## 1. Navi's only way off the map is two round trips through Flutter

**VERIFIED.**

- **The bottom bar is off by default.** `prefs.py:127` has `"bar": False`, and `main.py:344` only adds the bar when `prefs.bar()` is true.
- **The only route to another page is the round menu button:**
  1. `menu_button()` (`main.py:218-224`) runs `on_click=lambda e: page.run_task(open_menu)`.
  2. `open_menu` does `await page.show_drawer()` (`main.py:226-227`). That's an `invoke_method` to Flutter, which calls `openDrawer()` (`flet-0.86.5/lib/src/controls/view.dart:76-77`).
  3. A drawer row runs `on_click=lambda e, k=key: page.run_task(drawer_go, k)` (`main.py:246`).
  4. `drawer_go` does `await page.close_drawer()` and **only then** `go(key)` (`main.py:265-267`).
- **`close_drawer` waits for Flutter's reply with no timeout.** `session.invoke_method` does `asyncio.wait_for(evt.wait(), timeout=None)` (`flet/messaging/session.py`, the `invoke_method` body). The page change can't happen until Flutter's reply to `close_drawer` has come back in and been processed.

**Moto's primary path has none of that.** The bottom bar is on by default:
`washi prefs.py:452` has `_as_bool(_V.get(KEY_BOTTOM_ENABLED), True)`, and a tap
goes straight to `render(PAGES[idx])` (`apps/moto/main.py:639-642`). Moto's
*drawer* path awaits `close_drawer` exactly like Navi's (`apps/moto/main.py:419-421`),
but it's the secondary path.

So in Navi every page change needs, in order:

- the menu tap to come in,
- `show_drawer` to go out,
- the row tap to come in,
- `close_drawer` to go out,
- its reply to come in.

In Moto it needs one incoming tap.

## 2. From launch, Navi has a constant stream of incoming events; Moto has none until she opens its GPS page

**VERIFIED**, except where marked.

- **Launch always lands on Ride:** `main.py:130`, `main.py:195`, `main.py:378`.
- **Building Ride attaches the motion sensors whenever the gauges are on**, and the gauges are on by default:
  - `ride.py:229` is `if prefs.dash() or live.recording() or not prefs.sensors_on_demand(): sensors.attach(page)`.
  - `prefs.py:107` has `"dash": True`, and `prefs.hud()` defaults to `"ribbon"` (`prefs.py:260-261`), so `prefs.dash()` is True.
  - **So v1.5.0's `sensors_on_demand` changes nothing with default settings.** The first term of that `or` is already True, and the sensors attach on the first frame of every launch. The v1.5.0 fix is real code, but it's unreachable with her defaults.
- **What gets attached** (`sensors.py:266-314`):
  - Accelerometer, UserAccelerometer, Gyroscope and Magnetometer at `INTERVAL_MS = 400` (`sensors.py:70`).
  - Barometer at 1000 ms (`sensors.py:74`).
  - Battery and ScreenBrightness.
  - That comes to roughly **11 readings a second** by rate. The actual delivered rate on her phone isn't measured.
- **They are never released when she leaves Ride.**
  - `main.py` has no reference to geo, services, sensors or detach anywhere; the only sensor mention is `boot.stages_for(skip_telemetry=...)` at `main.py:178`.
  - `ride.py:216-219` keeps them on purpose ("they keep running once attached, so a ride recorded while she is looking at the Rides list still gets its sensor channels").
- **The GPS also outlives Ride.**
  - The Geolocator is kept in `ride._M["geo"]` (`ride.py:82`), and `on_position_change` is re-pointed only inside `ride.build()` (`ride.py:526-532`). After a switch it still calls the last Ride build's `on_position`.
  - That handler runs `paint()`, which rebuilds the gauge overlay (about 40 controls) every `DASH_EVERY_S = 1.0` into a holder no longer on screen (`ride.py:380-390`). It then calls `_update(page)` (`ride.py:483-484`), which is a bare `try: page.update() except Exception: pass` (`ride.py:85-89`).
  - It also runs `page.run_task(_apply_daylight)` on every fix, because `auto_theme` defaults True (`prefs.py:122`; `ride.py:479-480`).
- **Moto has no motion sensors anywhere.** Grepping `apps/moto` and `packages/*/agedashii_*` for Accelerometer, Gyroscope, Magnetometer and Barometer finds nothing. It opens on Trips (`apps/moto/main.py:177`).

## 3. What Flet does with each of those events

**VERIFIED in the Flet 0.86.5 source**, except the one inference marked.

- **Every incoming event becomes a task on the single asyncio loop:** `asyncio.create_task(self.session.dispatch_event(...))` (`flet/messaging/flet_socket_server.py:403-404`).
- **Sync handlers run directly on that loop:** `event_handler(e)` (`flet/controls/base_control.py:524-528`). Every sensor handler, `on_position` and `render()` itself block the loop while they run.
- **After every event, Flet auto-updates:**
  - `session.after_event` calls `__auto_update(control)` when the handler didn't call `update()` itself (`session.py:545-581`).
  - That walks up from the control to the nearest *isolated* ancestor and calls `.update()` on it.
  - Auto-update is on by default (`controls/context.py:192`), and neither app turns it off (grepped for `disable_auto_update`).
- **Services sit under the Page:**
  - They register into `Page._services`, a `ServiceRegistry` (`controls/page.py:96-165`, field at `page.py:626`).
  - Only `Page` and `BasePage` are marked isolated (`page.py:395`, `base_page.py:135`).
  - **BELIEVED:** so each sensor reading ends in a **full `page.update()`**. The parent chain service → registry → Page is inferred from the field layout, not observed.
- The sensor handlers only write `STATE` (`sensors.py:136-200`) and never call `update()`, so the auto-update does fire for every reading.
- **Cost per reading on her phone: NOT MEASURED.** I tried a headless timing on the Pi, but my harness didn't mount the page tree (6 controls indexed), so its numbers are meaningless and are not reported.

## 4. The two shells compared

**VERIFIED.**

| | Navi | Moto |
|---|---|---|
| Primary navigation | drawer only; bar off by default | bottom bar, on by default |
| Tap → new page | tap, `show_drawer` round trip, tap, `close_drawer` round trip, `render` | tap, `render` |
| First surface | Ride: map + GPS + 5 sensors | Trips: no GPS |
| Motion sensors | ~11 readings/s, from the first frame, never released | none |
| GPS | on from the first frame; handler outlives Ride | only after she opens vnav, and then it outlives vnav too (below) |
| `render()` itself | build body, new drawer, `controls.clear()`, add `SafeArea`, `page.update()` (`main.py:298-350`) | the same shape (`moto main.py:672-717`) |

The two `render()` functions are structurally the same, including replacing
`page.drawer` on every render. **BELIEVED:** so the ratio of `page.update()` to
rerender call sites isn't where the difference is. The difference is:

- (a) Navi's only way off the map goes through two Flutter round trips, and
- (b) Navi is flooded with incoming sensor events from the first frame, and Moto never is.

## 5. Checks on AGEDASHII's GPS claim

The claim: the Ride GPS handler keeps calling `page.update()` while another page is showing, and that is the cause.

1. **"main.py never detaches or re-points the geolocator": CONFIRMED.** See the grep in §2.
2. **"This is the difference from Moto": NOT CONFIRMED. Moto does the same thing.**
   - `apps/moto/agedashii_moto/pages/vnav.py:219-225` builds a NEW `Geolocator` on every vnav build, appends it to `page.services`, and never removes it.
   - Its `on_position` ends in `redraw_stats(); _update(page)` (`vnav.py:212-213`), with the same bare-except `_update` (`vnav.py:91-95`).
   - On the Flutter side, each geolocator subscribes to its own position stream (`flet_geolocator/lib/src/geolocator.dart:28-50`).
   - So once she has opened vnav in Moto, Moto has the same "GPS repaints a page that isn't showing" pattern, several times over. Moto survives it.
   - **Conclusion:** the GPS handler is real waste and worth fixing, but by the test "Moto does the same and is fine", it isn't sufficient on its own.
   - What Moto never has is the sensor stream, and a bar-free, drawer-only way to navigate.
3. **v1.6.0 caching and re-adding the same `fmap.Map` instance: no Flet 0.86 objection found. VERIFIED:**
   - Removing and re-adding a control in one patch is handled: `will_unmount` is skipped for controls that are also added, and the session index is refilled after the send (`session.py:269-293`).
   - A control re-added later is serialized in full, which rewrites its diff snapshots `__prev_lists/__prev_dicts/__prev_classes` (`messaging/protocol.py:142-179`), so there's no stale-diff desync.
   - **BELIEVED:** it probably does NOT keep flutter_map's state, either. `render()` wraps the map in a brand-new `SafeArea/Column/Stack` every time (`main.py:340-349`), so Flutter receives it as an *add* inside a new subtree and builds a new widget.
   - So v1.6.0 is likely harmless but may not achieve its aim. Only the phone can show whether tiles refetch.

## 6. The hypothesis

**BELIEVED, not verified.**

One asyncio loop is serving ~11–12 events a second, each a sync handler plus a
full-page auto-update, and the GPS handler rebuilds 40 gauge controls once a
second. If per-event cost × rate gets near one second per second on her phone,
the backlog grows without bound.

The drawer path needs three incoming messages processed in order, the last being
Flutter's `close_drawer` reply, so it's the first thing to stall completely. From
her side, the page never opens.

This fits every fact so far:

- Moto works: no sensor stream, and a one-message bar path.
- Fixing the map cache (v1.6.0) didn't help.
- Fixing sensors-at-boot (v1.5.0) didn't help, because the sensors still attach with default prefs.

## 7. How to confirm, cheapest first

1. **A/B on her phone, no build.**
   - On Ride, tap the TUNE button (`ride.py:827`) and switch **Gauges** off. That calls `_toggle_dash`, which rerenders, and `ride.build` then reaches `sensors.detach()` (`ride.py:231-234`).
   - Then switch **Follow the bike** off. `_toggle_gps` drops the Geolocator (`ride.py:491-501`).
   - Now try the menu. If pages open, the event stream is the cause.
   - Caveat: `_toggle_dash` doesn't save prefs (`ride.py:609-611`), so this lasts only until the app restarts, which is fine for a test.
   - If the TUNE tap itself won't respond, that's evidence too: it's the same incoming queue.
   - **Split the two toggles** (Gauges off alone first) to tell the sensors from the GPS.
2. **Emulator, counting events.** Set the `flet_controls` logger to DEBUG. `base_control.py:491` logs `"Trigger event %s.%s %s"` for every event, so counting those lines per second in logcat gives the real rate. Timestamping the menu tap against when `render` runs shows the queue delay directly.
3. **Emulator, measuring the backlog.** Log `len(asyncio.all_tasks())` once a second. A number that climbs and never falls is a backlog.

## 8. Fix candidates for the owner of `apps/navi` (not applied)

- **Stop the per-reading page update.** Call `ft.context.disable_auto_update()` at the top of each `sensors.on_*` handler; they only write `STATE`. Do the same on `ride.on_position`'s early returns (`ride.py:413-414`, `ride.py:420-421`), where no `update()` is called.
- **Release the sensors, and stop the GPS handler from painting, when she leaves Ride**, unless a ride is recording. The recording exception is her stated reason for keeping them (`ride.py:216-219`), and it must survive the fix.
- **Make `sensors_on_demand` mean what it says:** `ride.py:229`'s `prefs.dash()` term defeats it.
- **Navigate before awaiting the drawer.** In `drawer_go`, call `go(key)` first, then close the drawer, or don't await the close at all. `render()` replaces the drawer anyway (`main.py:329`), so the page change stops depending on a Flutter reply.
- **Consider the bar on by default**, as in Moto. That's her decision: the no-bar default came from her "not ALWAYS in my face" instruction (`main.py:209-211`).
- **Minor, the same family:** at build, `track_line` gets the *whole* track (`ride.py:248-249`), while on each fix it gets only the last `TRACK_POINTS` (`ride.py:469-470`). On a long ride every rebuild ships the full track.

## Not the cause

**VERIFIED.**

- **The routing graph lock.** Settings' `live.graph_status()` takes `routing._LOCK`, but the loader does not hold that lock while it builds the graph (`washi routing.py:247-266`). Opening Settings cannot block on a graph load.
- **Page builds.** None of Settings, Rides, Where to, Around or Help does network I/O in `build()`. Rides lists a folder (`log.py:60-67`), which is cheap.
