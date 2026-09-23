# Agedashii Watudo

What to do today, and where to go someday. Two surfaces from the Agedashii
dashboard — Wat2Do and Future Finds — built and shipped as an app of their
own, with its own Settings, its own help and its own navigation.

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
weather strip, and a map that will route from the origin to a spot. It is the
phone's half of the website's Wat2Do and speaks the same four endpoints, so
the browser and the app show the same day rather than two opinions about it.
The cards can be reordered, resized and hidden — the Customize button in the
topbar.

**Future Finds** — places I might go someday. Radius chips (25 / 50 / 100
miles), 17 categories over a server-side table of ~31k California gems, a
someday list, Wikimedia photos with credits, and the cached street view frame
where there is one.

**Settings** — a surface, not a dialog. Server address and Check; all 24
themes; corner radius, accent glow, CRT overlay and its intensity; the bottom
bar and the hamburger trigger; and the settings that only mean something
here — how a day is labelled, whether finished tasks stay on screen, whether
pain and spoons are tracked and whether they steer the day, what the template
editor writes for a sub-task, how many subjects a mission rolls, and where
distances are measured from. Every control carries the sentence that says
what it does.

**How this works** — the wiki, inside the app. What each surface does, how
pain and spoons work, what the template editor and the mission generator are
for, what queues offline and what needs the server. Plain sentences, no
marketing, and it reads the same in a tunnel because it is static text.

## Getting around

The bar along the bottom is the navigation: **Wat2Do · Future Finds ·
Settings**. Her words: *"Each of these smaller apps (not tofu) work better
with the bottom navigation bar and less with the hamburger menu."*

* **The hamburger is on** and is switched *off* in Settings > Navigation.
  The ask was for a switch, and one that starts off just reads as a missing
  button. Hidden, its slot in the topbar is *gone* rather than blank —
  an empty IconButton still takes its 48px, and a gap where a control used to
  be reads as a control that failed to draw.
* **How this works is not on the bar.** It is one screen you read once, and
  it lives under Settings the way a manual lives in a drawer. A fourth stop
  would push the three used daily closer together.
* **One rail:** something must reach every surface. Switch the bottom bar
  off and the hamburger comes back whether or not it is switched on, and the
  switch in Settings says so rather than letting the app quietly disagree
  with it. Her own setting is overridden, not rewritten, so the bar coming
  back restores the choice she made.
* **Exactly one control offers Present and Future at a time.** They are two
  tabs of one page, and with the bottom bar carrying both, the page's own tab
  bar over the top of them was the same two choices in two rows — which is
  what the bottom bar switch existed to escape. So the tab bar is drawn only
  when the bottom bar is off, and then it is how you get between them.
  Either way it is one control, not two copies: one `future._S`, one scan,
  one someday list, and which of the two is lit is read back out of the
  page's tab index rather than stored a second time. Settings and How this
  works have no tab bar, so the shell remembers those two — and forgets them
  the moment a tab page is shown again.
* A side effect worth having: only the destination on screen is built. With
  a tab bar, `_tabs` is handed two already-constructed bodies, so every
  repaint built Future as well as Present — and left Customize in the topbar
  on Future, which has no cards to arrange.

The topbar carries the Watudo pill, the outbox chip, Customize on a surface
that has cards to arrange, and the theme button. There is no gear: Settings
is on the bar, and a third route to the same screen is what made the old
drawer a copy of the old bar.

## Offline, and what that actually means

Offline-first is the product here, not a feature. Her words: *"ONLY THE DB
stuff needs to sync everything else should be stored locally and not need
internet access."*

### Works with the radio off

| | Where it lives |
|---|---|
| All 24 themes, and the one you picked last | `agedashii_qi`, plus one SharedPreferences key |
| Corner radius, accent glow, CRT overlay and its intensity | SharedPreferences (`agedashii_washi.prefs`) |
| Card order, width, height and which are hidden | SharedPreferences |
| Bottom bar on/off, hamburger on/off | SharedPreferences |
| The day, pain/spoons, template and mission settings | SharedPreferences (`agedashii_watudo.prefs`) |
| Where distances are measured from | SharedPreferences |
| The server address | SharedPreferences |
| Whatever the device has already fetched | the local SQLite store |
| Any change you make | the outbox, until it can be sent |

None of the above touches the network. Settings and How this works are
entirely local — there are tests that assert building either one makes no
request at all, and that a full launch plus a repaint with every appearance
knob changed asks the wire for the day's record and nothing else.

Two preference stores, on purpose. Appearance and the bottom bar are
`agedashii_washi.prefs`, shared with the dashboard and the sibling apps so
they mean the same thing everywhere. The rest is `agedashii_watudo.prefs`,
because washi has no key for them and adding one there would make every other
Agedashii app carry a setting about a checklist it does not have.

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

**Settings > Server** — address, a Check button, and the reason it is not
answering if it is not: "unreachable", "blocked by auth — redirects to …
(use the VPN)", "blocked by Cloudflare at the edge". Those are different
problems and it says which. The probe runs on a worker thread, because
`config.set_url` opens a socket with a 3 second timeout and three seconds
inside a synchronous Flet handler is three seconds of dropped taps.

Watudo has its own bundle id, so on a device it gets its own
SharedPreferences store and its own app-private data directory. It does
**not** inherit an address set in the Agedashii dashboard, and its outbox is
its own. First launch falls back to the shipped candidates in
`agedashii_washi/config.py` (`192.0.2.21:6900`, then `aitp.local:6900`).

Off-LAN needs the VPN. `iamprettyfamous.online` will not work as the app's
address: VoidAuth forward-auth 302s every path, `/api/config` included.

## Where distances come from

**This app has no location permission and asks the device nothing.**

It used to declare `ACCESS_FINE_LOCATION` and `ACCESS_COARSE_LOCATION` ahead
of a feature that never arrived: every distance was measured from a fixed
origin (`_wat2do_parts.HOME`, Vulcan Lofts) and `future.scan()` took a
`centre` that nothing passed. A permission declared for a feature that does
not exist is what teaches an owner to stop reading permission lists, so it
has been removed — plugin, manifest entries, iOS usage strings and all.

What replaced it is a setting, not a sensor. **Settings > Measure distances
from** takes a latitude and longitude and moves the map's home marker, the
Overpass query, the mission radius, the thrift list and every distance column
together — and it is what `scan()`'s `centre` parameter now carries. It
starts at home, it works offline, it prompts for nothing, and it is honest
about what the app knows: what she told it.

If Watudo ever does ask for a fix, the permission comes back in the same
commit as the code that requests it. Not before.

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

## What the chrome is made of

* Themed Material icons throughout, tinted from `PAL()`, so every glyph
  follows all 24 themes. No emoji in the chrome: emoji only where the thing
  *is* the thing, and all of this is furniture.
* Each nav destination has two marks — an outline at rest and a filled one
  where you are — so a stop is recognisable at a glance rather than being one
  of four grey glyphs.
* Everything tappable ripples in the accent colour and eases its state
  changes over 150 ms (`agedashii_watudo/ui.py`). A handler that has to reach
  storage cannot repaint instantly, and an unacknowledged tap is the one that
  gets tapped twice.

## Tests

```bash
/home/nvii/venvs/flet/bin/python -m unittest discover -s apps/watudo/tests -t .
```

115 tests, about four seconds, no socket and no display. `config._opener` is
replaced with one that refuses every request, so "offline" is a boolean
rather than a firewall rule, and the store is a tempfile thrown away with
each test.

| File | What it pins |
|---|---|
| `test_shell.py` | the shell builds headlessly; every surface renders cold and offline; exactly one control offers Present \| Future and Future is not built behind Wat2Do; the bottom bar reaches the three daily surfaces and lights Settings while Help is open; every surface is reachable with the hamburger hidden; the drawer, once switched on, reaches all four and carries what the bar cannot; which tab is lit is read back out of the page's own tab index rather than kept twice; the theme reaches the palette, Material's own mode and the next repaint, and survives a relaunch; the outbox chip says what is queued and nothing when nothing is; SafeArea wraps the chrome and the CRT overlay goes over the top of it with every layer still ignoring taps; everything tappable in the chrome ripples |
| `test_settings.py` | Settings renders as a surface; every control changes the thing it claims — theme, radius, glow, CRT and its intensity, bottom bar, hamburger, friendly dates, hide-finished, pain/spoons tracking and steering, the sub-task marker, mission size, the origin, the server address; turning the bar off forces the menu back without rewriting her setting; nothing inside a `Row(wrap=True)` expands; every setting survives a restart and a corrupt value costs the setting and not the app; the help surface says what queues and what needs the server |
| `test_offline.py` | nothing local goes to the wire; a tick with no signal is queued, is visible to the very next read, and 40 ticks coalesce to 1 |
| `test_isolation.py` | no file under `apps/watudo/` imports `agedashii_tofu`, `agedashii_moto` or `agedashii_foodzen`, statically or at runtime; the build manifest declares what it needs and **no permission at all**, and the README and the help surface agree with it |

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
* Settings does not carry the dashboard's page order, units, sodium target or
  refresh interval. The first two do not apply to these surfaces; the last
  two are washi settings this app never shows, so they stay at their
  defaults here.
* The origin is typed, not sensed — see *Where distances come from* above.
  There is no "find me" button, because there is nothing behind it.
* Changing an appearance or navigation setting repaints the whole shell,
  which scrolls Settings back to the top. That is the cost of seeing the
  corner radius change everywhere at once; the settings that only affect
  Wat2Do do not repaint, because the surface they change is not the one on
  screen.
* With the bottom bar switched off the page's tab bar comes back, and that
  path still builds both tabs: `wat2do.build()` hands `_tabs` two
  already-constructed bodies rather than callables. That is the page's own
  shape, kept because the dashboard renders it the same way; the shell
  catches a failure in either, since `_tabs` cannot.
