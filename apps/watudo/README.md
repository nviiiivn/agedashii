# Agedashii Watudo

What to do today, and where to go someday. Two surfaces from the Agedashii
dashboard — Wat2Do and Future Finds — built and shipped as an app of their
own.

They are one app because they are one question. Wat2Do plans the day; Future
Finds is the list of places worth a ride someday, and Wat2Do is what imports
it: the mission generator on the Present tab draws its subjects from the same
spots. "What shall I do" and "where might I go" are the same question asked
twice, so they live behind the same icon.

Built with [Flet](https://flet.dev) 0.86.5 (Python → Flutter). One codebase
builds an Android APK, a Linux x64 desktop app, a macOS app, an unsigned iOS
simulator build, and a web bundle.

## What is in it

**Wat2Do** — the day, with date navigation. The checklist with sub-tasks,
pain and spoons sliders, the template editor, the mission generator, the
weather strip, and a map that will route from home to a spot. It is the
phone's half of the website's Wat2Do and speaks the same four endpoints, so
the browser and the app show the same day rather than two opinions about it.
The cards can be reordered, resized and hidden — the Customize button in the
topbar.

**Future Finds** — places I might go someday. Radius chips (25 / 50 / 100
miles), 17 categories over a server-side table of ~31k California gems, a
someday list, Wikimedia photos with credits, and the cached street view frame
where there is one.

The topbar carries the Watudo pill, the outbox chip, Customize, the theme
picker and Settings. The drawer and the bottom bar both reach the two
surfaces; the drawer additionally carries the theme picker and Settings,
which is what stops it from being a second copy of the bar.

## Offline, and what that actually means

Offline-first is the product here, not a feature. Her words: *"ONLY THE DB
stuff needs to sync everything else should be stored locally and not need
internet access."*

### Works with the radio off

| | Where it lives |
|---|---|
| All 24 themes, and the one you picked last | `agedashii_qi`, plus one SharedPreferences key |
| Corner radius, accent glow, CRT overlay and its intensity | SharedPreferences |
| Card order, width, height and which are hidden | SharedPreferences |
| Bottom bar on/off | SharedPreferences |
| The server address | SharedPreferences |
| Whatever the device has already fetched | the local SQLite store |
| Any change you make | the outbox, until it can be sent |

None of the above touches the network. There is a test that asserts it
(`test_offline.py::TheLocalHalfNeverAsksTheNetwork`): a full launch plus a
repaint with every appearance knob changed makes requests for the day's
record and nothing else.

### Needs the server

The day's record (`/api/wat2do/state`, `/save`, `/template`, `/missions`),
the weather strip, the Overpass POI scan, and Future's scan, gems, photos and
street view. All of it goes through `agedashii_washi.store`, which:

* answers a read from this device's own copy when the server is not there,
  and says so rather than showing a plausible number;
* **queues a write instead of dropping it.** A tick made in a tunnel is
  applied to the local copy and put in the outbox in one transaction, and the
  next read sees it immediately;
* **coalesces Wat2Do ticks per day.** An afternoon of ticking boxes with no
  signal becomes one queued write carrying the final state of that day, not
  forty — 40 ticks, 1 pending item, asserted in the tests;
* never charges a queued write one of its retry attempts for "no signal",
  so a week with no reception does not park everything.

The chip in the topbar says what is waiting. It is drained on launch, on
return to the foreground, on a tap, and by a watcher that probes every 20
seconds *while something is queued* and costs nothing the rest of the time.

Nothing is invented. Offline with an empty cache, Future Finds says "Not
connected to the server" and shows an empty list, because that is the truth;
it does not use the shared "showing sample data" banner, whose wording would
be false on both counts here.

## Pointing it at a server

**Settings** (the gear in the topbar, or the drawer) — address, a Check
button, and the reason it is not answering if it is not: "unreachable",
"blocked by auth — redirects to … (use the VPN)", "blocked by Cloudflare at
the edge". Those are different problems and it says which. The same dialog
carries the bottom bar switch, accent glow, the CRT overlay and corner
radius; the theme has its own picker on the palette button.

Watudo has its own bundle id, so on a device it gets its own
SharedPreferences store and its own app-private data directory. It does
**not** inherit an address set in the Agedashii dashboard, and its outbox is
its own. First launch falls back to the shipped candidates in
`agedashii_washi/config.py` (`192.0.2.21:6900`, then `aitp.local:6900`).

Off-LAN needs the VPN. `iamprettyfamous.online` will not work as the app's
address: VoidAuth forward-auth 302s every path, `/api/config` included.

## Running it from source

```bash
pip install -e packages/agedashii-qi -e packages/agedashii-washi -e apps/watudo
python apps/watudo/main.py
```

With a display it opens a window. With none it serves over HTTP on port
8553. One port per app, all distinct — the dashboard 8550, Moto and FoodZen
8552, Watudo 8553 — because on the Pi they are all headless and expected to
be up at once, and a shared default means the second one to start dies with
"address already in use".

```
AGEDASHII_HEADLESS=1   force the HTTP server even with a display
AGEDASHII_PORT=8553    pin the port; 0 lets the OS pick a free one
```

Serving headless also needs Flet's web bundle
(`site-packages/flet_web/web`). If it is missing the Python side starts and
the browser gets a 500 from the static file handler — that is the bundle, not
the app, and it affects the dashboard's `main.py` identically.

## Building it

From `apps/watudo/`:

```bash
flet build apk
flet build linux
flet build macos
flet build web
```

`pyproject.toml` is what makes those builds say "Agedashii Watudo" and
install under `online.iamprettyfamous.agedashii.watudo` instead of inventing
both from the directory name.

Two things in there are easy to get wrong and are commented at length in the
file:

* **`[project] dependencies` is the list `flet build` installs.** flet only
  falls back to `requirements.txt` when that list is empty. `flet-map` is in
  it because Wat2Do imports it behind a `try/except` — leave it out and the
  build succeeds and ships an app with no map and no complaint.
* **`[tool.flet.dev_packages]`** points `agedashii-qi` and `agedashii-washi`
  at this checkout. They are not on PyPI, so without it a build stops at
  *No matching distribution found for agedashii-washi*.

### Permissions

`ACCESS_FINE_LOCATION` and `ACCESS_COARSE_LOCATION`, foreground only. GPS and
network-location hardware are declared **not required**, so a device without
a fix can still install and falls back to coarse location.

`ACCESS_BACKGROUND_LOCATION` and `FOREGROUND_SERVICE_LOCATION` are
deliberately absent. Watudo records no tracks — the dashboard declares them
because XNAV writes a ride while the screen is off, and this app has no
equivalent. Asking anyway would mean an all-the-time prompt, a Play listing
that needs a written justification, and a permission nothing would use.

Honest caveat: **no code in this app asks the device where it is today.**
Every distance is measured from a fixed origin (`_wat2do_parts.HOME`, Vulcan
Lofts) and `future.scan()` takes a `centre` that nothing currently passes.
The permission is declared ahead of the feature, which costs a line in the
app's permission listing and nothing at runtime — Android only prompts when a
permission is requested.

## Tests

```bash
/home/nvii/venvs/flet/bin/python -m unittest discover -s apps/watudo/tests -t .
```

62 tests, about two seconds, no socket and no display. `config._opener` is
replaced with one that refuses every request, so "offline" is a boolean
rather than a firewall rule, and the store is a tempfile thrown away with
each test.

| File | What it pins |
|---|---|
| `test_shell.py` | the shell builds headlessly; both surfaces render cold and offline; the drawer and the bottom bar each reach both; which one is lit is read back out of the page's own tab index rather than kept twice; the theme reaches the palette, Material's own mode and the next repaint, and survives a relaunch; the outbox chip says what is queued and nothing when nothing is; SafeArea wraps the chrome and the CRT overlay goes over the top of it with every layer still ignoring taps |
| `test_offline.py` | nothing local goes to the wire; a tick with no signal is queued, is visible to the very next read, and 40 ticks coalesce to 1 |
| `test_isolation.py` | no file under `apps/watudo/` imports `agedashii_tofu`, `agedashii_moto` or `agedashii_foodzen`, statically or at runtime; the build manifest declares what it needs and not what it does not |

## What it is built on

* `agedashii_qi` — the look. 24 palettes, `PAL()`, `RAD()`, `apply_theme()`,
  the accent glow and the CRT overlay.
* `agedashii_washi` — the system. The offline store and outbox, the server
  client, the 60-column panel layout engine, preferences, tabs, dialogs,
  forms and the widget vocabulary.

And on nothing else of ours. It does not import the dashboard (`agedashii_tofu`)
or either sibling app (`agedashii_moto`, `agedashii_foodzen`); `flet-map` is
the only Flet plugin.

## Known limits

* The theme is remembered under a key this app writes itself
  (`agedashii.theme`). `agedashii_washi.prefs` does not own a theme key —
  in the dashboard the picker is per-session and resets every launch.
* Settings here is a dialog, not a page: server address, bottom bar, accent
  glow, CRT overlay, corner radius, plus the theme picker on its own button.
  Everything else in the dashboard's Settings (page order, units, sodium
  target, refresh interval) either does not apply to these two surfaces or
  is not exposed yet.
* CRT intensity is stored and honoured but has no control in this app; the
  overlay is on or off here, at whatever intensity was last set.
* Future Finds is reachable from the bottom bar, the drawer and the page's
  own tab bar. They are three handles on one control, not three copies —
  one `future._S`, one scan, one someday list, and which one is lit is read
  back out of the page's tab index rather than kept a second time. The cost
  is that on a phone the bottom bar and the tab bar show the same two
  choices one above the other, which is why the bottom bar has a switch.
* Every render builds both tabs, because `wat2do.build()` hands `_tabs` two
  already-constructed bodies rather than callables. That is the page's own
  shape and this shell does not change it; it does catch a failure in
  either, since `_tabs` cannot.
