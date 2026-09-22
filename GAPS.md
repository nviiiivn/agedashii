# Agedashii — what is done, what is not

Updated 2026-09-21 late. Branch `app-config`, 758 tests green (app),
110 green (website).

This replaces the version written before the parity work. Where a claim has
a number behind it, the number was measured, not estimated.

---

## The app runs from source on the Void laptop

No build step, no CI. Type **`agedashii`** in a terminal, or find
**Agedashii** in the desktop search.

- source: `~/agedashii-src` on `192.0.2.254`, synced with rsync
- venv: `~/venvs/agedashii` (Flet 0.86.5 on Python 3.14)
- iterate: edit here, rsync, relaunch — about 40 seconds

**GitHub Actions is out of minutes.** The repo is private so builds are
metered, and the macOS job billed at a 10x multiplier: one macOS run cost
more than everything else combined. Both jobs now start and die instantly
with zero steps, which is what exhausting the allowance looks like. The
macos-ios job is commented out with instructions to restore it. Until the
allowance resets there is no APK and no IPA.

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

### On the web, at agedashii.alola.lol
The PWA is live and answering — "Add to Home Screen" is the no-cost route
onto the iPhone, since a real iOS install needs a paid Apple Developer
account. `/apk`, `/ipa` and `/downloads` all answer too.

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

## In flight

Nothing. Everything below is either done or listed as not done.

## Not done

| Work | Note |
|---|---|
| APK / IPA | blocked on Actions minutes; the IPA also needs a Mac |
| `1 salmon fillet` matches smoked salmon | that entry is the only one that says "filets". Its alternatives list the rest |
| `1 cup oatmeal` matches QUAKER | the table has no plain cooked oatmeal to answer with |
| Forks: FoodZen, Trips+Garage, Wat2Do as separate apps | agreed, not started |
| Sensors: accelerometer, gyro, lean, g-force | deferred by her |
| Routing takes no heading | on a divided highway a fix nearer the opposite carriageway starts the route the wrong way |
| Website layouts live in `localStorage` | per browser; the app cannot see them. Moving them server-side is a decision, not yet taken |

## Known limits

- This Pi cannot run the Flet app in a browser — it is headless and the
  venv has no `flet_web/web` assets. Gesture behaviour is therefore
  verified at the handler level here, and on screen only via the laptop.
- Two of three Overpass mirrors are dead and the third is intermittent;
  `overpass.osm.ch` was added and the proxy now remembers whichever
  answered last.
- Lab > Things and Garage > Mechanic are placeholders on the website with
  no data source behind them. The app says so rather than inventing one.
