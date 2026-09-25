# vNav and xNav

**Status: design note. Nothing here is built.** This is the brief and the
research, written down so it survives the session it came from.

## The split

What exists today is one module, `agedashii_moto/pages/xnav.py`, mounted
twice — as a tab inside Trips and as its own bottom-bar surface. It does
GPS, track recording, and route guidance through Valhalla, Overpass and
Valhalla's height service.

That becomes two separate things:

| | what it is | where it lives |
|---|---|---|
| **vNav** — virtual nav | exactly what exists now, renamed. Planning, replay, looking at a route on a screen | a tab inside Trips, off the bottom bar |
| **xNav** — on-bike nav | new. Turn-by-turn while riding, the way any maps app does it | its own surface, on the bottom bar |

Her words: *"it's great when you're on the desktop or sitting on the bike
or whatever, but when you're on the road you want a normal overlay like any
other fucking maps navigation app."*

So vNav is not being replaced or deprecated. It is good at what it is for.
xNav is a different job that it was never doing.

## The two hard requirements

**1. It works with no signal. No exception.**

This is where today's guidance fails and why the new one is not a reskin.
`xnav.py` routes through Valhalla and colours by speed limit through
Overpass — both live third parties, both of which correctly refuse rather
than queue when there is nothing to reach. On the road that is the moment
you needed them.

The pieces to build on are already on the device: `agedashii_washi.routing`
searches an 86 MB offline graph in ~150 ms and honours 21,806 turn
restrictions, and `tileserver.py` serves an MBTiles archive over loopback.
What is missing is the guidance layer on top — the instruction text, the
re-route when you miss a turn, and the overlay.

**2. Navigation and telemetry are paired.**

Not two features that happen to run at once: the ride log should say what
the route was and what you actually did, together. That also covers the
sensor work (accelerometer, gyro, lean, g-force) that has been deferred —
it belongs here rather than as its own thing.

Her words: *"the NEW xnav records telemetry and what you actually did
alongside displaying what the 'current' destination or route is."*

This may end up as a separate app with the two backends linked, rather than
a page inside Moto. That is an open question, not a decision.

## Prior art to read before writing any of it

Her list, kept whole. Nothing here has been evaluated yet — grouping is by
what a thing *is*, not by whether it is any good or whether its licence
permits lifting from it. **Check the licence before taking code.**

### Valhalla — the router already in use

- https://github.com/valhalla/demos
- https://github.com/valhalla/web-app
- https://deepwiki.com/valhalla/valhalla/7-turn-by-turn-directions
- https://mapcolonies.github.io/developer-portal/docs/MapColonies/openstreetmap/valhalla/turn-by-turn/osm-valhalla-overview/
- https://www.mapzen.com/products/mobility/turn-by-turn/

Most relevant, because Valhalla is what vNav already talks to, and its
turn-by-turn instruction generation is the part not currently used.

### Open-source navigators

- https://organicmaps.app/
- https://osmand.net/
- https://github.com/navit-gps/navit
- https://maps.me/
- https://github.com/junjunguo/PocketMaps
- https://github.com/aaswat0104/offline-osm-path-navigation/
- https://github.com/topics/offline-maps

Organic Maps and OsmAnd are the two that actually do offline turn-by-turn
on a phone today and are worth reading first.

### Commercial, for comparison only

- https://www.sygic.com/
- https://transportation.trimble.com/en/solutions/mapping-and-routing/copilot
- https://wego.here.com/
- https://www.navmii.com/mobile-navigation
- https://www.mapfactor.com/mapfactor-navigator-offline-gps-navigation-app/

Useful for what the overlay should look and behave like on a bike. Not for
code.

### Telemetry and sensor logging

- https://github.com/tszheichoi/awesome-sensor-logger/
- https://www.tszheichoi.com/sensorlogger
- https://github.com/sensorlogger
- https://github.com/Steppschuh/Sensor-Data-Logger
- https://m-philipp.github.io/AndroidSensorLogger/ — and its
  [API](https://m-philipp.github.io/AndroidSensorLogger/api/)
- https://github.com/jeanfbrito/Biker-Log
- https://github.com/Calbummy/MotoLogger

Biker-Log and MotoLogger are the two aimed at motorcycles specifically.

## To do

- [ ] Rename `xnav.py` → vNav everywhere, and take it off the bottom bar
- [ ] Read Organic Maps and OsmAnd for how the on-bike overlay behaves
- [ ] Read Valhalla's turn-by-turn instruction generation
- [ ] Decide: page inside Moto, or its own app with a linked backend
- [ ] Build guidance on top of `agedashii_washi.routing` — instructions,
      off-route detection, re-route, all with no network
- [ ] Pair the ride recorder with sensor logging, and settle what is stored
- [ ] Check the licence of anything before code is taken from it
