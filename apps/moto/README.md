# Agedashii Moto

Trips + Garage. The motorcycle half of [Agedashii](../../README.md), split out
into an app of its own so it can be installed, opened and built without the
other eight dashboard pages coming with it.

Three surfaces:

| Surface | What it is |
|---|---|
| **Trips** | The trip library, a two-point route planner that works with no signal, the route editor (`Produce`), recorded rides with a replay scrubber, and xNav as its fourth tab. |
| **Garage** | Six bikes, their mileage / service / expense records, spec sheets, markdown notes, and `Flipper` — the server's Craigslist and Reddit scanner. |
| **XNAV** | The live map on its own, full height: GPS, track recording, and turn-by-turn guidance with speed limits, curves, elevation and POIs. |

XNAV appears twice on purpose — as a tab inside Trips, where you land after
planning a route, and as a top-level surface, where the whole screen is map.

## Offline is the point, not a fallback

> "ONLY THE DB stuff needs to sync everything else should be stored locally and
> not need internet access"

**Works with the radio off, always:**

- the 24 themes, the corner radius, the accent glow and the CRT overlay
- the per-surface card layouts (`Arrange` in the topbar)
- units, and every other preference
- the **offline router** — A\* over a packed OSM graph, pure CPython, no C
  extension. The Bay Area graph is 86 MB / 725,626 nodes and routes 40 km in
  about 180 ms on a Pi. Install one at `AGEDASHII_ROUTE_GRAPH=/path/to.rg`;
  with none installed, routing falls back to the server's Valhalla **and says
  on screen that that is what it did**.
- the **offline map tiles** — an MBTiles archive served to the map widget over
  loopback by a tiny HTTP server inside the app, so nothing leaves the device.
  Point it at one with `AGEDASHII_MBTILES=/path/to.mbtiles`; with none, the map
  uses `tile.openstreetmap.org` and the chip in the corner reads `online maps`
  instead of `offline maps`.
- **track recording** in XNAV. The points come from the GPS receiver, not from
  the network.

**Needs the server, and syncs through the outbox:**

- the trip list, saving a trip, editing a route
- recorded rides stored server-side
- every garage record: mileage, services, expenses, spec sheets, notes
- Flipper scans (they run *on* the server, against Craigslist and Reddit)

Server data goes through `agedashii_washi.store` — a SQLite cache plus an
outbox. A read answers from the device's own copy instantly, online or not, and
refreshes behind the screen. A write lands in the outbox first and is applied to
the cache in the same transaction, so the screen updates with no signal and the
queued change survives the app being killed. Nothing is dropped, and being
offline never costs a queued write one of its five retry attempts.

The chip in the topbar is the only place this is mentioned: it appears when the
server has not got something yet, says what is waiting, and drains on a tap, on
resume from the background, and whenever the 20-second watcher notices signal
came back. Nothing waiting means no chip — an always-present "all synced" badge
would be a claim about the server that the queue cannot make.

**Needs a live third party, and cannot be queued:** Valhalla routing, Overpass
POIs and speed limits, and the elevation service. There is nothing to replay
against these, so they refuse with the reason rather than queueing a request
that could never be sent. A distance that has not been routed shows as `—`,
never as `0 mi`.

### Where the local data lives

| | Path |
|---|---|
| cache + outbox | `$FLET_APP_STORAGE_DATA/store.sqlite3`, or `~/.agedashii/store.sqlite3` outside a packaged build |
| preferences | Flet `SharedPreferences`, shared with the other Agedashii apps — the theme picked in one is the theme the others open on |
| route graph | `$AGEDASHII_ROUTE_GRAPH`, else bundled, else none |
| map tiles | `$AGEDASHII_MBTILES`, else bundled, else none |

## Pointing it at a server

Moto has no Settings page — the dashboard app owns that, and the address is
stored under the same key, so setting it there sets it here. Absent a stored
address, the app probes the shipped candidates in `agedashii_washi/config.py`
in order and adopts the first that answers.

Off the LAN this means the VPN. `iamprettyfamous.online` will not work as the
address: Cloudflare 403s at the edge and VoidAuth 302s every path, including
`/api/config`. The app reports those three failures differently ("blocked by
auth", "blocked by Cloudflare at the edge", "unreachable") because they need
completely different fixes.

## Running it from source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # from the repo root
.venv/bin/pip install -e packages/agedashii-qi -e packages/agedashii-washi
.venv/bin/pip install -e apps/moto
.venv/bin/python apps/moto/main.py
```

With a display it opens a real window. With none it serves over HTTP on
**:8552** — a different default port from the dashboard's 8550, so both can run
at once. Either can be forced:

```bash
AGEDASHII_HEADLESS=1 .venv/bin/python apps/moto/main.py   # serve, no window
AGEDASHII_PORT=8600  .venv/bin/python apps/moto/main.py   # pin the port
```

`AGEDASHII_PORT=0`, or leaving it unset with a display attached, lets the OS
pick a free port, so two copies cannot collide.

## Building it

`flet build` reads `apps/moto/pyproject.toml` and nothing else for the app's
identity, its dependencies and its permissions — see the comments in that file
for why each block is there. The path is the positional argument, not a flag:

```bash
flet build apk   apps/moto     # Android
flet build linux apps/moto
flet build macos apps/moto
flet build ipa   apps/moto     # needs a signing identity
```

Installs as `online.iamprettyfamous.agedashii.moto`, beside the dashboard's
`online.iamprettyfamous.agedashii` rather than over the top of it.

Two things about the dependency list that are easy to get wrong. flet uses
`[project].dependencies` whenever it is non-empty and only falls back to
`requirements.txt` when it is empty — so Moto's list, not the repo-root
`requirements.txt`, is what goes into the bundle, and `flet-map` and
`flet-geolocator` are named there explicitly. They are imported behind a
`try`/`except`, so omitting them would not fail the build; it would ship an app
whose XNAV reads "Map unavailable". And `agedashii-qi` / `agedashii-washi` are
not on PyPI, so `[tool.flet.dev_packages]` points them at `packages/` in this
checkout.

None of the four platform builds has been run from here — a Pi has neither the
Android SDK nor an Xcode. What is verified is that the file parses, that every
block flet reads is present and says what it should, and that the app runs from
source.

The Android build declares `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`,
`ACCESS_BACKGROUND_LOCATION` and `FOREGROUND_SERVICE_LOCATION`. The last two
are not optional for this app: without them a ride stops recording the moment
the screen locks, which is most of every ride. GPS and network-location
*hardware* are declared **not required**, so a device without GPS can still
install and use the trip library, the garage and the router.

It does not ask for the camera. That belongs to FoodZen.

## Tests

```bash
python3 -m unittest discover -s apps/moto/tests -t .
```

61 tests in about a second, no network and no real store — `config._opener` is
swapped for something that always refuses and the database is a tempfile, so
"the server is down" is a boolean rather than a firewall rule. They run `main()`
for real against a stub page and check that it comes up offline, that all three
surfaces render and say what they cannot know instead of inventing it, that the
drawer and the bottom bar each reach all three, that a theme change actually
repaints, that the CRT overlay sits over the SafeArea and takes no taps, that
the headless/window guard picks the right mode and port, that `pyproject.toml`
carries every block `flet build` reads, and that nothing in the app imports
`flet_app`, `agedashii_watudo` or `agedashii_foodzen`.

## Known: writes that repaint

`agedashii_washi/forms.py` has `from .. import api as M` where it means
`from . import api as M`. That is a relative import past the top of the package,
so `forms.rebuilder(...)` raises `ImportError` the moment it is called. It is
reached from Trips' tab-to-tab jumps and from Garage's Add-record and New-note
dialogs, so those repaints fail. It is a one-word fix in the shared package,
which is outside this app — nothing in `apps/moto/` can work around it.

## Flet 0.86.5

Four traps in this version that fail **silently** — no exception, no log, the
control simply does nothing:

- `Dropdown` fires `on_select`, never `on_change`
- `FilledButton` takes `content=`, not `text=`
- dialogs open with `page.show_dialog` / `page.pop_dialog`, not `open` / `close`
- `Map.move_to` is a coroutine and must be awaited

And one that is not silent but is worse: Flet runs a synchronous `on_click` on
its event loop, so a slow handler leaves the old frame on screen and **drops
every tap that lands during it**. That is why the drawer chevron repaints only
`page.drawer`, and why tabs are built one at a time rather than all at once.
