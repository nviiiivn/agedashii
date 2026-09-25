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

### A. Four Home dashboards — the largest piece

Every app gains a Home, and none of them can share Tofu's. Tofu's
`home.py` is 581 lines of machine stats, containers, services and power
controls — it is a *homelab* dashboard, and it means nothing in Foodzen.

So Home is one shared **shape** with four different contents:

| App | What its Home would show |
|---|---|
| Tofu | what it shows now — machines, services, containers, weather, quick actions |
| Moto | next ride, last ride, odometer, service due, weather for riding |
| Watudo | today's checklist, pain and spoons, what is next |
| Foodzen | today's sodium and sugar against target, what is in the list, last BPM |

**This is the part I would push back on scheduling.** Three new dashboards
is not a refactor, it is three new features, and each needs deciding what
belongs on it. Everything else in this proposal is moving existing code.

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
8. **Four Homes.** Last, because it is the only part that is new work.

Rebuild and redeploy at the end, on your word — not per step.

---

## 4. Decisions I need from you

1. **"Help" or "How this works"?** Moto says Help, Watudo says How this
   works. It should be one word in all four. I would use **Help**, because
   it is what the drawer entry has room for.
2. **Four Homes now, or later?** Step 8 is roughly as much work as steps
   1–7 together. Everything else can ship without it.
3. **Does Tofu keep a Home at all**, given its Home *is* the homelab
   dashboard and nothing moved out of it? I have assumed yes, unchanged.

---

## 5. What I am not doing

- Building any navigation. `XNAV.md` is the brief; this pass creates the
  page and nothing behind it.
- Migrating the 14 journal entries. They are test rows.
- Touching the sensor work. It belongs with xNav, per `XNAV.md`.
- Rebuilding or deploying anything until you say so.
