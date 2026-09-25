# Reshape — proposal

**Status: awaiting approval. Nothing in here is built.**

Written 2026-09-25 from the four app specs she gave, one at a time. This is
the target shape, what it costs, and the order it would have to happen in.

---

## 1. The target shape

### Agedashii Tofu

| | |
|---|---|
| **Pages** | Home, Lab, Tools, Settings, Help |
| **Tools** | Feeds, Vault, Journal, Projects, AI, Obligations |
| **Bottom bar** | Home, Lab, Tools, + More |

Weather leaves for Moto and Watudo. Extras stops being a page — its three
tabs move into Tools. Journal keeps Mental-Health, New-Projects, Dr-Visits
and Medications; Trip-Reports and Mechanicry go to Moto.

### Agedashii Moto

| | |
|---|---|
| **Pages** | Home, Trips, Garage, Weather, xNav, Settings, Help |
| **Trips** | Selekt, Produce, Rides, **vNav** |
| **Garage** | Home, Mechanic, Flipper |
| **Weather** | Bay Area, Explore |
| **Bottom bar** | Trips, Garage, xNav, Settings |

Today's `xnav.py` becomes **vNav** and stays a Trips tab. **xNav** becomes a
new, empty surface for on-bike turn-by-turn — see `apps/moto/XNAV.md`, which
is the brief for it. Mechanic receives the Mechanicry journal.

### Agedashii Watudo

| | |
|---|---|
| **Pages** | Home, Wat2Do, Future Finds, Weather, Settings, Help |
| **Weather** | Bay Area, Explore |
| **Bottom bar** | Home, Wat2Do, Future Finds, Weather, Settings |

With the bottom bar off, Wat2Do and Future Finds still collapse into a
Present / Future tab pair, as they do now.

### Agedashii Foodzen

| | |
|---|---|
| **Pages** | Home, Grocery, Meals, Scrape, Expenses, EatZen, BPM, Settings, Help |
| **Bottom bar** | Home, Grocery, Meals, EatZen, BPM, Settings |
| **Drawer only** | Scrape, Expenses, Help |

Flat. The FoodZen page disappears as a container — Foodzen is the app, so
its six tabs become six peers. BPM sits at the same level as Grocery.

---

## 2. What has to be built to get there

### A. Four Home dashboards — smaller than it first looked

I costed this as three new features. It is not, and the reason is worth
knowing: `home.py:520` already registers Home as eight named, separately
buildable cards through `panels.layout("home", {...})` —

`greeting` · `stats` · `weather` · `nowplaying` · `machines` · `links` ·
`actions` · `wat2do`

Her brief for the new Homes was *"the quick links and quick actions and
'welcome'/'good morning' shit, a weather shortcut, and maybe a music
component"*. Every one of those is already a card in that list. So the work
is **moving five of those cards into Washi and letting each app pick which
it shows** — not writing three dashboards.

| App | Cards |
|---|---|
| Tofu | unchanged: all eight |
| Moto | greeting, links, actions, weather, nowplaying |
| Watudo | greeting, links, actions, weather, nowplaying |
| Foodzen | greeting, links, actions, weather, nowplaying |

Two things fall out of this for free, because `panels` already does them:
each app's Home is reorderable and each card is hideable, per app, without
any new code.

What each app *should eventually* show — next ride and service due for
Moto, today's sodium against target for Foodzen — is a later pass. Basic
first, as she asked.

`QUICK_LINKS` is a module constant of eight homelab URLs. It stays Tofu's
by default; whether each app gets its own list is question 4 below.

### B. Weather moves to Washi

`weather.py` is 1,623 lines and two apps now need it. Copying it into both
means two files that drift. It moves to `agedashii_washi/pages/weather.py`
and both import it.

This makes Washi the first shared package to own a *page* rather than
plumbing. That is a real change to what Washi is, and worth saying out loud
rather than doing quietly.

### C. Tools absorbs Extras

`extras.py` is 577 lines with three tabs. They become Tools tabs 4, 5 and 6,
and `extras.py` stops being a page. Mechanically straightforward; the risk
is Tools becoming a junk drawer — six tabs is the most any page here has.

### D. Foodzen flattens

Six tabs become six pages. The page bodies do not change; what changes is
the shell, the bar, the drawer and `SUBPAGES`. Foodzen's own `foodzen.py`
(574 lines) stops being a container.

### E. Journal categories move to Moto

The actual data is 14 entries, all in Mechanicry, all test content dated
2026-09-21 with an empty `Bike/vehicle:` field. Trip-Reports is empty.

Garage already has per-bike `Notes` / `Mods` folders, all empty, on a
different backend (`/api/garage/tree`, not `/api/journal/*`).

**Proposal:** Mechanic becomes a journal view reading the Mechanicry folder,
plus an **Unsorted** bucket, since the template already has a `Bike/vehicle:`
field to sort by later. Nothing is migrated on disk — there is nothing worth
migrating, and sorting the 14 test entries into six bikes is your call, not
a script's.

### F. vNav rename, xNav stub

Rename in one module and its two mount points. The new xNav is a page that
says what it will be, with `XNAV.md` behind it. **No navigation is built in
this pass.**

---

## 3. Order

Each step leaves every suite green.

1. **Weather → Washi.** Nothing depends on the others; unblocks Moto and Watudo.
2. **Tools absorbs Extras; Tofu drops Weather and Extras.** Tofu reaches its target shape.
3. **Weather into Moto and Watudo.** Now that it is shared.
4. **Foodzen flattens.** Self-contained.
5. **vNav rename + xNav stub.** Self-contained.
6. **Mechanic reads the Mechanicry journal.**
7. **Help everywhere**, under one name.
8. **Home cards into Washi, and a Home in each app.** Last because it
   depends on Weather already being shared (step 1), not because it is big.

Rebuild and redeploy at the end, on your word — not per step.

---

## 4. Decisions I need from you

1. ~~"Help" or "How this works"?~~ **Settled: both.** They are different
   jobs — **Help** is the nav label, where a drawer row and a bar slot have
   no room for four words, and **How this works** is the page's own title
   once you are on it. Same page. Moto and Watudo each had one half of
   this already; now all four have both.
2. ~~Four Homes now, or later?~~ **Settled: now, basic.** Greeting, quick
   links, quick actions, weather, music. See section A — these already
   exist as cards, so this is no longer the expensive step.
3. **Does Tofu keep a Home at all**, given its Home *is* the homelab
   dashboard and nothing moved out of it? I have assumed yes, unchanged.
4. **Does each app get its own quick links?** `QUICK_LINKS` is eight
   homelab URLs — AI chat, ComfyUI, VS Code, Navidrome, Jellyfin, docs,
   site builder, gallery. Right for Tofu. For Moto and Foodzen they are
   probably the wrong eight, but I do not know what the right ones are, so
   the default is: every app shows the same list until you say otherwise.

---

## 5. What I am not doing

- Building any navigation. `XNAV.md` is the brief; this pass creates the
  page and nothing behind it.
- Migrating the 14 journal entries. They are test rows.
- Touching the sensor work. It belongs with xNav, per `XNAV.md`.
- Rebuilding or deploying anything until you say so.
