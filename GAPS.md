# Agedashii — what is done, what is not

Updated 2026-09-23. **1,114 tests green** — 767 Tofu, 120 Moto,
115 Watudo, 112 Foodzen — plus 111 on the server side.

Where a claim has a number behind it, the number was measured, not
estimated. See `BOARD.md` for the split itself and who owns what.

---

## There are four apps now

| | what it is | on the phone |
|---|---|---|
| **Agedashii Tofu** | the whole dashboard, every surface | 222 MB |
| **Agedashii Moto** | Trips + Garage + XNAV | 222 MB |
| **Agedashii Watudo** | Wat2Do + Future Finds | 142 MB |
| **Agedashii Foodzen** | Grocery + EatZen + BPM | 140 MB |

Watudo and Foodzen are a third smaller because they do not carry the
120 MB offline map they would never open.

Each has its own bundle id, so all four install side by side. Each has its
own Settings, its own in-app manual, and its own icon.

**Nothing has been removed from Tofu**, and nothing will be until she says
so. What she has instead is the ability to hide surfaces herself — which
is reversible and deletes nothing.

## Running them

**On the laptop, from source** — no build step. Search the launcher for
**Agedashii**; all four are there with their own icons.

- source: `~/agedashii-src` on `192.0.2.254`, synced with rsync
- venv: `~/venvs/agedashii` (Flet 0.86.5)
- entries: `~/.local/share/applications/agedashii-*.desktop`

**On the phone** — `tofu.alola.lol/apk`, and the same for `moto`,
`watudo`, `foodzen`.

**Builds happen on Tower now, not GitHub.** Actions minutes are exhausted
(both jobs die in 3 seconds with zero steps, which is what that looks
like), so `tools/build_app.sh <app> apk linux` on Tower is the path, using
the toolchain at `/home/nvii/flet-toolchain`. That removes the CI
dependency entirely rather than waiting on a quota.

---

## Done and verified

### Named
Agedashii everywhere — app, website, window title, desktop entry, CI
artifacts. The name sits in the original orange-pink-cyan gradient pill;
NVII has left the topbar. `agedashii` was checked and is free on GitHub,
npm and PyPI.

### Web-to-app parity, every page
Four agents took a page each, against the website as source of truth.

| Page | What was missing |
|---|---|
| Trips | the trip builder, Produce entirely, ride replay, the whole xNav navigator |
| Lab | 38 containers never listed, gauges, processes, dotfiles, terminal, power |
| Extras | repo trees, commits, file view, three AI providers, model lists |
| Garage | spec, mileage, services, expenses, notes — and it could invent bikes |
| Weather | the station map, the radar, place search, tap-for-weather |
| Tools | Feeds showed 20 of 76; Vault could not list at all |
| FoodZen | Scrape, Expenses and Meals did not exist |

### Lies that were on screen, now gone
- a hardcoded "Midnight City — M83" that played forever
- four invented bikes, three invented bike listings, fake obligations
- a fabricated AI conversation on first open
- `up 0 down 0 ft` elevation presented as measured
- SP3 reporting 0 containers, 0 services, 0% GPU **while unreachable**
- "Ride weather good" as a literal string; "0% rain" from a field that
  does not exist in the payload
- Flipper's top deal was a $1 listing badged PEARL at +$3,678

### Speed
A slow render does not merely feel slow — Flet runs a sync handler on its
event loop, so the old frame stays up and **taps are dropped**. That is why
the status dots "did nothing": `render("lab")` took 9.84 s and 15 of 17
clicks vanished.

| | before | after |
|---|---|---|
| lab | 1770 controls, 9.84 s | **126, 0.08–0.26 s** |
| tools | 1915 | **239** |
| weather | 1130, 1.09 s cold | **362, 0.05 s** |
| home | 6.0 s (SSH to five hosts inline) | **0.20 s cold, 0.01 s warm** |

### Structure and settings
- `Tools > FoodZen > EatZen` — real nesting, as asked
- the drawer lists every page *and* its tabs, and jumps straight to one
- menu and bottom bar are now **independent**; a page can be on either,
  both, or neither
- the bottom bar can be switched off entirely
- 24 themes: alola.lol's 15 at the top, in alola's order and alola's
  colours, then Agedashii's 9. Default `white`
- corner radius 0/3/8, accent glow, CRT overlay — all in Settings
- card layout: reorder, hide and resize per surface, plus a Customize mode
  that does it on the page itself

### On the web — five hosts now
`tofu.alola.lol`, `moto.alola.lol`, `watudo.alola.lol`,
`foodzen.alola.lol`, each with `/apk`, `/linux-x64`,
`/linux-arm64` and `/downloads`. `agedashii.alola.lol` is the Qi + Washi
framework page, generated from the packages themselves so it cannot drift
from what ships. Every old path 302s to its new home.

**A PWA added to the home screen from `agedashii.alola.lol` is now
broken** — that origin serves the framework page. Re-add it from
`tofu.alola.lol`.

### Elsewhere
- offline maps and routing: edge snapping (median error 72 m to 6.1 m),
  21,806 turn restrictions honoured (9 banned turns over 120 routes to 0)
- EatZen food matching: 19/19 phrases correct
- FoodZen data is backed up before every overwrite, 20 versions per
  collection
- SSH passwords are no longer served unauthenticated to the LAN
- the shipped AppImage no longer contains the git history

---

### Photo to EatZen
Point the camera at a plate; Tower's vision model names the foods, and every
number comes from the local USDA table — the model is told in its system
prompt that it is not a nutrition database.

- `Red_Apple.jpg` → "apple" → `Apples, raw` 161 g, Na 0.0 mg, in **9.6 s**
- `Banana-Single.jpg` → "banana" → `Bananas, raw` 118 g, Na 1.2 mg, in 19.4 s
- a counted item ("1 banana") is a fact; a guessed weight ("170 g chicken")
  is flagged `*` and is editable, as asked

It was 75–87 s a photo until today: the ranking was "largest vision model
under 10 GB", and Ollama reports the vision capability for most of Tower's
100-model library, so a general chat model won every time. Dedicated vision
families now rank first. Tower also cannot load `llama3.2-vision:11b` at all
(`unknown model architecture: 'mllama'`) — that is now remembered per machine
so it costs a request, not one of the three attempts.

### The food she meant
Running the photo feature's own vocabulary through the matcher found nine
wrong answers — "1 orange" was answering with orange **peel**, "1 cup coffee"
with 240 g of **cream**, "2 tbsp peanut butter" with a **cereal**, and every
counted item came back at its smallest size (a banana at 81 g against a
medium's 118 g). All nine fixed; the phrase battery is now 30 cases with
controls, and it caught a regression on the way.

### Each app is its own app now
Settings is a real surface on every one of them — server, all 24 themes
(persisted, which they never were), radius, glow, CRT and its intensity,
navigation, and that app's own domain settings. Each has an in-app manual.
The bottom bar is the primary navigation and the hamburger can be hidden;
neither can be switched off in a way that strands a surface.

Watudo no longer asks for location. It never used it — every distance came
from a hardcoded HOME and `future.scan()`'s `centre` had no caller. Rather
than keep a permission for a feature that did not exist, the argument got
a real caller: "Measure distances from" in Settings.

### Glyphs
231 foods to emoji, whole-word and longest-phrase-first, so grapefruit is
not grapes, eggplant is not an egg, and "chocolate milk" is not "milk
chocolate". Unknown returns a neutral plate rather than a guess — a wrong
glyph reads as a claim about the row. Chrome uses themed Material icons
tinted from the palette, and a test asserts the two sets never mix.

### It moves
One module owns every duration and curve. Page and tab changes cross-fade
at 180 ms instead of hard-cutting; cards fade and rise, staggered;
resizing or reordering a card animates instead of teleporting; skeletons
stand in for content that has not arrived. Flet's own default for a
transition is ONE SECOND, which is how an app that uses the defaults ends
up feeling sluggish. **Reduce motion** is a real switch in Settings, and
every helper returns the still version rather than a zero-length
animation.

## In flight

Nothing. Everything below is either done or listed as not done.

## Fixed on 2026-09-23

Each of these was on the "not done" list below the day before.

**The web apps were blank in Firefox.** `flet build web` emits
`loadPyodide({stdout, stderr})` with no `indexURL`, so Pyodide has to
derive its own base path from the module it was imported from — which is
cross-origin (`cdn.jsdelivr.net`). Chromium allows that; Firefox does not,
and the worker dies with *"Could not extract indexURL path"*. Python never
starts, nothing is drawn, and the page goes blank the moment the loading
overlay clears. This was reported working repeatedly because the browser
automation used to check it is Chromium-only — the one browser that hides
the bug. Patched post-build in `agedashii-splash.py`; all four now reach
`Python worker initialized` **and paint a real surface** in Firefox, which
is checked by pixel count because a screenshot taken the instant Python
starts is still white and passed a console-only check.

**Five EatZen answers were wrong**, not the two listed here before:

| Phrase | Was | Why |
|---|---|---|
| `1 salmon fillet` | smoked sockeye | a cut is a *portion*. Every plain salmon row **offers** a fillet; only the smoked one spells "filets" in its name, so it alone covered both words |
| `1 cup oatmeal` | a QUAKER cereal, no weight | the right row never says "oatmeal" — USDA files it under oats |
| `3 strips bacon` | no weight at all | USDA calls that portion "slice cooked" |
| `1 apple` | peeled | "without skin" tied with the whole fruit and won on being the shorter name |
| `2 slices of bread` | a pita, no weight | the unit is stripped before the name reaches the ranker, so it could not know a slice was wanted |

The bread one was found by a **new** test, not by reading the code: the
battery asserted the food's *name* and never that the row weighed
anything, so bacon passed while returning `grams=None` and 0 mg sodium.

**Routing now takes a heading.** Sampling the shipped graph for opposed
one-way segments finds I-880's carriageways 18 m apart and I-80's 29 m
apart; at the midpoint the snap was choosing between them on centimetres.
`snap_edge()` costs heading disagreement in metres so it stays comparable
with distance — fully reversed is worth 60 m, which clears a carriageway
gap and not a genuinely nearer road — and only considers candidates within
45 m of the leader, so it re-orders a tie rather than going shopping.
`heading=None` is asserted byte-identical to the old function. Trips
passes the fix's own course when you route from "start here"; XNAV does
not route, it follows what Trips planned.

**The duplicate Caddy, and its actual cause.** Four cron lines
(`ss -tlnp | grep -q 8081 || nohup caddy run …`) raced the systemd units,
which already restart Caddy on their own. A port check is not a lock:
`SO_REUSEPORT` lets a second Caddy bind a port that is already listening,
so a cron tick could spawn a duplicate serving half the requests from a
stale config. That is the mechanism behind both the `:8081` orphan and a
second one found on `:8083`. Cron lines disabled, orphan killed.

## Not done

| Work | Note |
|---|---|
| Sensors: accelerometer, gyro, lean, g-force | deferred by her |
| The dashboards site answers 502 at `/` | `***REMOVED***` proxies `/` to `localhost:6902`, which has no systemd unit and is not listening. The config has not been touched since June; Caddy itself is healthy (`/dash1` returns 401). Nothing has been guessed about what belongs there |
| Website layouts live in `localStorage` | per browser; the app cannot see them. She has since said layouts SHOULD be local, so this is behaviour, not a gap |

## Known limits

- This Pi cannot run the Flet app in a browser — it is headless and the
  venv has no `flet_web/web` assets. Gesture behaviour is therefore
  verified at the handler level here, and on screen only via the laptop.
- Two of three Overpass mirrors are dead and the third is intermittent;
  `overpass.osm.ch` was added and the proxy now remembers whichever
  answered last.
- Lab > Things and Garage > Mechanic are placeholders on the website with
  no data source behind them. The app says so rather than inventing one.
