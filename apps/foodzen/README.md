# Agedashii Foodzen

Groceries, what you ate, and what your blood pressure did. One of three apps
split out of the Agedashii dashboard; it shares the look (`agedashii-qi`) and
the system (`agedashii-washi`) with the other two and shares no code with
them.

Three surfaces:

| Surface | What is on it |
|---|---|
| **FoodZen** | Grocery lists, Meals (pantry, recipe vault, meal plan), Scrape (a recipe URL in, ingredients out), Expenses (receipt import, spending, frequency radar) — plus EatZen and BPM as its last two tabs, matching the website page it was ported from |
| **EatZen** | Food logging against the nutrition table. Sodium is the headline, added sugar comes along |
| **BPM** | Blood pressure readings: systolic, diastolic, heart rate, note, and the category the pair falls into |

FoodZen is the website's page unchanged, so its tab strip still carries six
tabs. EatZen and BPM are also top-level pages here, because in this app they
are the point rather than something buried two levels down in Tools. The
drawer lists each surface once: FoodZen's sub-rows are Grocery, Meals, Scrape
and Expenses only.

---

## What works offline and what does not

Nothing about the *app* needs a network. Themes, corner radius, the accent
glow, the CRT overlay, the layout of every card stack and every preference are
read from the device's own storage and drawn with the radio off.

Server data goes through `agedashii_washi.store`, a SQLite cache and outbox on
the device. A read answers from the cache and refreshes behind the screen; a
write is applied to the cache and queued, and the chip in the topbar says how
many are still waiting. Nothing in the queue is ever dropped — a write the
server refuses is *parked*, not discarded, and tapping the chip un-parks it.
The queue drains on three events: a write that succeeds, coming back to the
foreground, and a 20-second watcher noticing signal returned.

| | Offline |
|---|---|
| Themes, layout, radius, glow, CRT, units, sodium target | Yes — local storage, never a request |
| Grocery list: add, tick, remove, templates, Need list | Yes — cached list, writes queue |
| Pantry, recipes, meal plan, purchases | Yes — same |
| Scrape a recipe URL, import a receipt | **No.** Both are the server fetching and parsing something; there is nothing to queue |
| EatZen | **No, today.** See below |
| BPM | **No, today.** See below |
| Photo → EatZen rows | **No, by design.** See below |

### EatZen and BPM are online-only, and that is a gap, not a decision

Both pages open with `if not M.STATUS_LIVE(): return offline_banner(...)`, so
with no server they show a strip saying so and nothing else.

For EatZen that gate is nearly honest: **the nutrition table lives on the
server, not on the phone.** It is `dashbored-nutrition/nutrition.json` in the
dashbored web tree — 7,709 foods, each with sodium, calories, sugar, potassium
and its portion sizes — and every figure EatZen shows comes back from
`/api/eatzen/parse`, which does the matching and the arithmetic server-side.
That is deliberate: a gram of sodium must not be able to read differently on
the phone and in the browser, and one table with one parser is the only way to
guarantee that. The cost is that EatZen cannot price a meal with the radio
off.

For BPM the gate is stricter than the data needs: readings are cached like any
other FoodZen collection and `bpm_add` queues like any other write, so the
page *could* show history and take a reading offline. It does not, and
changing that means changing the page rather than the shell.

### The photo feature is live-only on purpose

`/api/eatzen/photo` is not queued and must not be. Once the call returns one
way or the other there is no photo left to retry — the bytes were the request.
So an unreachable Tower is a refusal with the reason printed on the status
line, never a silent queue that looks like it worked.

---

## The photo → nutrition chain, and where the numbers come from

1. **Pick.** The Photo button opens a `FilePicker` with
   `FilePickerFileType.IMAGE`, whose source list includes the camera. This is
   why the Android manifest asks for `CAMERA` and `READ_MEDIA_IMAGES`; without
   them the picker fails at the moment it is tapped.
2. **Shrink, off the event loop.** Pillow converts to RGB and thumbnails the
   longest side to 1280px, then base64. A phone photo off the sensor is 3–8 MB
   and neither the model nor the upload needs that. It runs in a thread
   because a synchronous handler on Flet's event loop freezes the frame and
   drops every tap that lands while it runs. If Pillow is not in the build the
   original bytes are sent and the server downsizes; nothing is lost but time.
3. **Look.** `POST /api/eatzen/photo` hands the image to a vision model on
   Tower. The model's whole job is to **name the foods and say how many** —
   "two eggs", "a chicken breast", "a slice of sourdough". It is forbidden from
   stating a nutrient, and nothing it says about one would be used.
4. **Price.** Every number then comes from the same local table and the same
   server-side parser that typing the meal in by hand uses. A figure cannot
   differ depending on how the food got onto the screen.
5. **Check.** The rows come back editable. A *counted* item ("1 banana") is a
   fact. A *guessed weight* ("170 g chicken") is an estimate, is marked
   `estimated` by the server, and draws with a `*` and a one-line note under
   the rows: `* estimated from the photo — tap to correct`. Correct it and the
   row is re-priced from the table.
6. **Log.** Nothing is saved until "Log it". The photo id rides along with the
   entry so the logged row keeps its thumbnail.

Two rules the arithmetic keeps, matching the web client exactly:

- **A number you typed beats a looked-up one.** You are reading a label; the
  table holds a generic average.
- **Nothing is guessed.** A row marked IDK contributes nothing and says so,
  rather than counting as zero and making the day look better than it was.

A nutrition *label* photo is the one place the model may report figures
(`/api/eatzen/label`) — it is transcribing print, not recalling an average.

---

## Running it from source

```bash
cd apps/foodzen
/home/nvii/venvs/flet/bin/python main.py
```

The entry point decides how to run from the environment:

| | |
|---|---|
| a display is present | opens a real window (`ft.AppView.FLET_APP`), OS-chosen port |
| no `DISPLAY`/`WAYLAND_DISPLAY`, or `AGEDASHII_HEADLESS=1` | serves over HTTP on **8552** |
| `AGEDASHII_PORT=9001` | pins the port |

8552, not the dashboard's 8550: both are expected to be served off the same Pi
at once, and two defaults that collided would kill whichever started second
with "address already in use".

**Headless does not render on this Pi**, and it is not this app's fault:
`flet_web-0.86.5-py3-none-any.whl` from pypi.org ships **zero** files under
`flet_web/web/`, so any Flet app served with `view=None` starts, binds its
port, and then answers 500 with `RuntimeError: Web root path not found`. A
three-line Flet app fails identically. Use the CI web/PWA artifact to preview
on the Pi.

On first launch the app restores the saved server address, probes it, loads
preferences, and only then paints — so no screen flashes sample data and then
swaps it for real data.

## Tests

```bash
/home/nvii/venvs/flet/bin/python -m unittest discover -s apps/foodzen/tests -t .
```

42 tests, no network, well under a second. They test the shell, not the pages
— FoodZen, EatZen and BPM came over from the dashboard with their own history.
Covered: it builds headlessly; every surface renders both offline and with the
server answering; the drawer and the bottom bar reach each surface; a drawer
sub-row lands on the tab it names (checked against the page's real tab order,
so reordering the tabs fails the test rather than silently misrouting the
menu); themes apply to the body and the drawer; the CRT stacks over the
SafeArea and not under it; and no file in this app imports `agedashii_tofu`,
`agedashii_moto` or `agedashii_watudo`, by AST, by name at runtime, and by
string literal.

One of them exists for a specific reason: `test_rendering_never_reaches_the_
network` fails if painting a screen issues *any* HTTP request. Her FoodZen data
has already been destroyed once by something that poked a save endpoint to see
what it did.

## Building

```bash
cd apps/foodzen
flet build apk        # or: linux, macos, ipa, web
```

`[tool.flet]` in `pyproject.toml` fixes the product name
(`Agedashii Foodzen`), the org and the bundle id
(`online.iamprettyfamous.agedashii.foodzen`). Without it `flet build` invents
all three from the directory name and the app's identity becomes a side effect
of what the checkout is called.

The manifest asks for `CAMERA` and `READ_MEDIA_IMAGES`, declares the camera as
**not** required hardware (every other route into EatZen works without one),
and asks for **no location permission at all** — Foodzen has no map, no
recording and no route, and an app that asks for permissions it never uses
teaches its owner to grant them without reading.

Python 3.12+, Flet pinned to `>=0.86,<0.87`. Three things in that version fail
*silently* if written the old way, so if a control stops responding check these
first: `Dropdown.on_select` (not `on_change`), `FilledButton(content=...)` (not
`text`), `page.show_dialog()` / `page.pop_dialog()` (not `open`/`close`). A
fourth: `ft.Icon` has `.icon`, not `.name`, and the control is not slotted, so
assigning `.name` binds a stray attribute, raises nothing, and never changes
the glyph.

## Known gaps

- **No settings page.** Server address, units, sodium target, corner radius,
  glow and the CRT are read from shared storage but can only be *changed* from
  the dashboard app, which writes the same `agedashii.*` keys. The theme
  picker in the topbar is the one appearance control Foodzen has of its own.
- **The theme is not remembered across launches.** `agedashii_washi.prefs` has
  no key for it — this is true of the dashboard too, not something introduced
  here.
- **EatZen and BPM refuse to draw offline**, as above.
- **`agedashii_foodzen/pages/_compat.py` is a patch with an expiry date.**
  `agedashii_washi.forms.rebuilder` opens with `from .. import api as M`,
  which was correct at `agedashii_tofu/pages/_forms.py` and is an ImportError at
  `agedashii_washi/forms.py`. Every editable page calls it on its first line.
  `_compat` delegates to the shared helper and only substitutes its own copy
  while that import raises; fix the `..` in `packages/` and this module can be
  deleted and the import in `eatzen.py` pointed back at Washi.
