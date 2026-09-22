# Agedashii — the split

One line: the dashboard is being taken apart into pieces that each work on
their own, and **nothing is removed from Tofu until she has verified the
piece that replaces it, herself, on her own device.** Her sequencing, and
the safe one.

Branch `split`. 758 tests green at every commit so far.

---

## The shape

```
agedashii-qi        the look          24 themes, radius, glow, CRT
agedashii-washi     the system        store, api, panels, prefs, widgets
  |
  +-- Agedashii Tofu     everything, still
  +-- Agedashii Moto     Trips + Garage + XNAV
  +-- Agedashii Watudo   Wat2Do + Future Finds
  +-- Agedashii Foodzen  Grocery + EatZen + BPM
```

Qi does not import Washi. A theme has no opinion about cards, and a card
cannot be drawn without a theme, so the arrow points one way only.

The three apps do not import each other, and none of them imports Tofu.

## Local first, meant literally

> "ONLY THE DB stuff needs to sync everything else should be stored locally
> and not need internet access"

`agedashii_washi.store` is the single module in the system that talks to a
network, and it is built to be told no: writes queue offline, an unreachable
server burns no retry attempt, failures park rather than vanish, and the
outbox drains on resume.

Everything else is local and works with the radio off — themes, layouts,
preferences, the 86 MB routing graph, the 39 MB map tiles, the 7,709-food
nutrition table.

The one deliberate exception is the EatZen photo feature, which is live-only
by design: an unreachable Tower is a refusal with the reason, never a silent
queue, because there is no photo left to retry once the call returns.

---

## Board

| # | Work | Owner | State | Gate |
|---|---|---|---|---|
| S1 | Qi + Washi extracted, Tofu unchanged | me | **merged** `a370631` | 758 tests green |
| S2 | Nine pages moved into the three apps | me | **merged** `ac06ec1` | 758 tests green |
| S3 | Moto: shell, build config, README, tests | agent | running | builds headless, no cross-imports |
| S4 | Watudo: shell, build config, README, tests | agent | running | builds headless, no cross-imports |
| S5 | Foodzen: shell, build config, README, tests | agent | running | builds headless, no cross-imports |
| S6 | Icons for all four apps | — | ready | distinct at 48px, no text |
| S7 | APK + IPA + Linux x64 per app, built on Tower | — | ready | manifest, label and id verified per artifact |
| S8 | Hosting: a download page per app | — | ready | each URL 200s with the right size and type |
| S9 | Trim Tofu once the three are confirmed | — | **blocked** | her confirmation, on device |
| S10 | Rename `agedashii_tofu/` to `agedashii_tofu/` | me | ready | deferred: three agents are mid-flight asserting they do not import `agedashii_tofu` |

### Blocked, and why

**S9 is blocked on her, and stays blocked.** It is the only card that
deletes anything. Her instruction, verbatim: *"I'll 'disable' and NOT SHOW
the things i want for now. ONCE I SAY YOU CAN SHIP IT WITHOUT THE PARTS
THAT ARE THEIR OWN I NEED TO VERIFY SHIT WORKS BEFORE YOU FUCKING REMOVE
IT COMPLETELY."*

So nothing is removed. What she gets instead is the ability to hide
surfaces herself, which already works and is reversible — verified:
hiding trips/garage/wat2do drops them from the menu, unhiding restores
them, and the code is untouched throughout.

**S18 is blocked on one file.** Everything else about the macOS route is
proven: KVM passes into the container, it carries its own QEMU 10.1.2, and
Apple's installer catalogue downloads with no account. Only Xcode itself is
gated behind an Apple ID — verified three ways, including that
xcodereleases.com is a directory rather than a mirror, and that `xtool`
builds SwiftPM packages rather than Flutter's Xcode project.

### Known, not fixed here

- `api.py` is 2,911 lines and still carries domain logic — 17 call sites
  into the foodzen tables — so it is not purely framework yet. Splitting it
  by domain is its own card, after the three apps ship.
- CI is out of Actions minutes; builds happen on Tower now, which is why
  S7 does not depend on GitHub.

---

## Naming

The base dashboard is **Agedashii Tofu** - agedashi tofu is the dish, and
this is the whole of it; the other three are pieces taken off it. Its
bundle id stays `online.iamprettyfamous.agedashii` deliberately, so her
installed copy UPGRADES rather than landing beside itself as a second app.

She wrote both `Agedashii:Gomoto` and `Agedashi-Moto`. Settled on **Moto**,
her later form. Display names are `Agedashii Moto` / `Watudo` / `Foodzen`;
package names are lowercase and pip-safe (`agedashii-moto`); bundle ids are
`online.iamprettyfamous.agedashii.<app>`, so all four install side by side
rather than replacing each other.
