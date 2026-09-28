# Navi — asked for, not built yet

Her words, kept verbatim, because the paraphrase is where the intent goes.

## 1. Moving the elements around — the more important half

> "the ability to 'edit' the screen and physically move/place certain ui elements
> based on preference? whether its pure custom(XYZ) or assigning things to a
> 'grid' cssflex type space orientation. --- the latter being more important now
> than the 'customization' of the widgets."

Her own ordering: **layout before looks.** So this is first.

The grid is the right model, not free XY:

- Free placement means a position per gauge, per screen size, per orientation —
  and a phone has at least four of those. It would need re-doing on every device.
- A coarse grid — three columns by three rows over the map, plus the four edges —
  survives rotation by construction, because a cell is a proportion and not a
  pixel.
- Long-press to enter edit mode, drag a gauge to a cell, tap to remove.

Read `agedashii_washi.panels` before writing any of it: the dashboard already has
this exact shape for cards, including drag, resize, hide, saving to preferences
and a Done that writes through. Building a second one would be the mistake.

Storage: one preference per orientation, holding `{cell: gauge}`. Per orientation
because that is the whole point of the grid, and it is already true of
`prefs.hud_side` and `prefs.hud_edge`.

## 2. A widget framework for the telemetry, loadable like themes

> "a SLEW of widget styled UI elements for each telemetric value --- e.g., almost
> a css / yaml / rainmeter / other random type widget frameworks that can be
> loaded in like themes."

Today `gauges.py` has seven fixed layouts and every gauge is a Python function.
This is one level up: the *look* of a gauge as data rather than code, so a new
dial is a file rather than a release.

The shape that fits what exists:

- A gauge spec in YAML or JSON — kind (`bar`, `arc`, `needle`, `number`,
  `horizon`), which channel it reads, its range, its colour stops, its size.
- `gauges.py` becomes an interpreter for that spec. The functions already there
  are the reference implementation of each kind.
- A pack is a directory of specs plus a palette, installed the way an MBTiles
  archive is: dropped on the device, found by name.

The hard part is already done: `telemetry.CHANNELS` is a stable public
vocabulary, so a spec naming `g_lat` keeps working.

## 3. Smaller, from the same conversation

- **Insta360 X3 over Bluetooth.** Hers: "it has this like built in thing to
  overlay mph and other telemetric shit when synced with the phone ... its a big
  time extra to link those things and not on my big time perogative list."
  Start/stop recording with the ride is the obvious first thing; it has a BLE
  control API.
- **Cheapest fuel nearby.** Needs signal. Her links are in `apps/moto/XNAV.md`.
- **Measured gear ratios.** `gears.RATIOS` holds published figures, not numbers
  measured off her bikes. `gears.learn()` takes observed (speed, rpm) pairs and
  returns the ratios that actually fit — run it over a real ride log and write
  the answer back.
- **Silent in-app update.** Deliberately not built: it needs a FileProvider and a
  Dart shim, and tapping `/apk` already downloads and installs as an update
  because the signing key matches. Revisit only if the two extra taps annoy.
