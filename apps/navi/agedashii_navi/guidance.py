"""Following a route, out loud, with no signal.

Washi already has the hard half. `agedashii_washi.routing` is a pure-CPython
A* over a packed road graph that ships on the device: it produces a route, a
polyline, a list of maneuvers, and the three live helpers - `progress`,
`next_maneuver`, `remaining`. None of that is re-implemented here.

What this module adds is everything between "there is a route" and "the rider
knows what to do":

**Off-route, and when to admit it.** `routing.progress` reports how far the
fix is from the line and refuses to hide it. Turning that number into a
decision needs hysteresis, because a bare threshold at a fixed distance
flickers: one noisy fix 50 m out flips to OFF, the next flips back, and the
rider gets two announcements and a re-route for a corner they took
correctly. So going off needs `CONFIRM_FIXES` consecutive bad fixes, and
coming back needs the distance to fall to `RETURN_FRACTION` of the
threshold - the same reason a thermostat has a deadband.

**Saying a turn once, at the right distance.** A prompt at a fixed distance
is far too early in town and far too late on a highway, so the distances
scale with speed: `prompt_points` is time-based, which is what a rider
actually needs - "in 200 feet" at 25 mph and "in a quarter mile" at 70 mph
are the same instruction. Each prompt for each maneuver fires once, tracked
by `(index, band)`, because an instruction repeated every second is worse
than no instruction.

**A speed limit that is honest about where it came from.** The graph carries
a road class per edge, not a signposted limit - Washi's `SPEED_KMH` table is
effective door-to-door speed fitted to routing times, which is a different
quantity and lower than any sign. So `limit_mph` returns a *typical* posted
limit for the class, in California terms, and `LIMIT_SOURCE` says exactly
that. Showing a fitted travel speed as a speed limit would be a number that
looks authoritative and is wrong by 15 mph on a residential street.

Nothing here reaches the network, and the caller passes
`allow_server=False` to `routing.route` when she has asked for offline-only,
so a dead zone produces a reason rather than a hang.
"""
import math
import time

from agedashii_washi import routing

#: Consecutive fixes beyond the threshold before guidance says "off route".
#: Three at 1 Hz is three seconds - slower than a bad fix, faster than a
#: rider can get meaningfully lost.
CONFIRM_FIXES = 3
#: Coming back on needs the distance to fall to this fraction of the
#: threshold, so the state cannot chatter at the boundary.
RETURN_FRACTION = 0.6

#: Seconds-to-turn at which each prompt fires, furthest first, and the name of
#: the band. Time rather than distance: see the module docstring.
PROMPT_BANDS = ((60.0, "far"), (20.0, "near"), (6.0, "now"))
#: Below this speed the time-based bands collapse, so fixed distances are used
#: instead - stopped at a light, every prompt would otherwise be "now".
CRAWL_MS = 3.0
#: The fixed distances used below `CRAWL_MS`, in metres, matched to the bands.
CRAWL_BANDS_M = {"far": 250.0, "near": 90.0, "now": 25.0}

#: Typical posted limits in California by OSM road class, in mph. NOT from the
#: graph - the graph has no maxspeed - and labelled as typical everywhere it
#: is shown. See the module docstring.
TYPICAL_MPH = {
    "motorway": 65, "motorway_link": 45, "trunk": 55, "trunk_link": 40,
    "primary": 45, "primary_link": 35, "secondary": 40, "secondary_link": 30,
    "tertiary": 35, "tertiary_link": 25, "unclassified": 30,
    "residential": 25, "living_street": 15, "service": 15, "road": 25,
}
LIMIT_SOURCE = "typical for this road class, not a reading of the sign"

#: How far over the typical limit before the readout warns. Wide enough not to
#: nag, since the limit itself is an estimate.
OVER_LIMIT_MPH = 7.0

MPS_TO_MPH = 2.2369362920544
MPS_TO_KMH = 3.6


def mph(speed_ms):
    return None if speed_ms is None else speed_ms * MPS_TO_MPH


def speed_text(speed_ms, imperial=True):
    """A speed for the big readout. Whole numbers - no decimal at a glance."""
    if speed_ms is None:
        return "—"
    value = speed_ms * (MPS_TO_MPH if imperial else MPS_TO_KMH)
    return f"{value:.0f}"


def speed_unit(imperial=True):
    return "mph" if imperial else "km/h"


def distance_text(metres, imperial=True):
    """Distance the way a rider hears it, not the way a float prints.

    Feet under a thousand, then tenths of a mile. Metric gets metres then
    kilometres. Rounded to something sayable: "in 400 ft", never "in 412 ft".
    """
    if metres is None:
        return "—"
    if imperial:
        feet = metres * 3.280839895
        if feet < 1000:
            rounded = int(round(feet / 50.0) * 50)
            # "0 ft" is not a distance anyone can act on, and it reads as a
            # measurement; at the turn itself the distance is simply absent.
            return f"{rounded} ft" if rounded else "—"
        miles = metres / 1609.344
        return f"{miles:.1f} mi" if miles < 10 else f"{miles:.0f} mi"
    if metres < 950:
        rounded = int(round(metres / 10.0) * 10)
        return f"{rounded} m" if rounded else "—"
    return f"{metres / 1000.0:.1f} km"


def eta_text(seconds, now=None, estimate=False):
    """Clock time of arrival, which is what a rider actually wants.

    `estimate` marks it "~": the offline router prices each road by one speed
    per class (routing.SPEED_KMH), so its time is an estimate, not Valhalla's.
    Minute resolution is kept on purpose - a time rounded to 5 minutes sits
    frozen for 5 minutes at 60 mph and reads as a broken app.
    """
    if seconds is None:
        return "—"
    clock = time.strftime("%H:%M", time.localtime((now or time.time()) + seconds))
    return f"~{clock}" if estimate else clock


def limit_mph(graph, lat, lon, heading=None):
    """(mph, class name) typical for the road at this point, or (None, "").

    Snapped to an edge rather than a junction so that standing at a corner
    does not report the side street. Returns None with no graph installed -
    which is a normal state, not an error, and the UI says so rather than
    inventing a limit.
    """
    if graph is None:
        return None, ""
    try:
        snap = graph.snap_edge(lat, lon, heading=heading)
    except Exception:                    # noqa: BLE001 - snapping never fatal
        return None, ""
    if snap is None:
        return None, ""
    cls_index = _edge_class(graph, snap)
    if cls_index is None:
        return None, ""
    try:
        name = routing.CLASSES[cls_index]
    except (IndexError, TypeError):
        return None, ""
    return TYPICAL_MPH.get(name), name


def _edge_class(graph, snap):
    """The road class of the edge a Snap landed on.

    A Snap names the two nodes either side of the segment it hit; the class
    lives per directed edge, so the edge from u to v is looked up in the CSR
    adjacency, and the reverse direction is tried second for a one-way
    snapped against its flow. Guarded throughout: a graph version missing one
    of these arrays should cost the speed limit, not the ride.
    """
    try:
        u, v = snap.u, snap.v
        for k in range(graph.off[u], graph.off[u + 1]):
            if graph.to[k] == v:
                return graph.cls[k]
        for k in range(graph.off[v], graph.off[v + 1]):
            if graph.to[k] == u:
                return graph.cls[k]
    except (AttributeError, IndexError, TypeError):
        return None
    return None


def over_limit(speed_ms, limit):
    """Whether to colour the speed readout as over. False when unknown."""
    if speed_ms is None or not limit:
        return False
    return mph(speed_ms) > limit + OVER_LIMIT_MPH


def prompt_points(speed_ms):
    """{band: metres} at which each prompt for the next turn should fire.

    Time-based above a crawl and fixed below it. Returned as a dict rather
    than computed inline so a test can state the distances directly.
    """
    if speed_ms is None or speed_ms < CRAWL_MS:
        return dict(CRAWL_BANDS_M)
    return {band: speed_ms * secs for secs, band in PROMPT_BANDS}


def turn_icon(instruction):
    """A glyph name for an instruction, chosen off its words.

    Reads the instruction text because that is what `routing` produces -
    there is no maneuver type code to switch on. Deliberately coarse: four
    shapes a rider can tell apart at a glance beat twelve they cannot.
    """
    text = (instruction or "").lower()
    if not text.strip():
        # No instruction is not "go straight". An arrow on a nav banner IS an
        # instruction, read at 60 mph; with nothing to say, draw nothing.
        return None
    if "u-turn" in text or "turn around" in text:
        return "u_turn"
    if "arrive" in text or "destination" in text:
        return "flag"
    if "left" in text:
        if "sharp" in text:
            return "sharp_left"
        return "slight_left" if ("slight" in text or "bear" in text) else "left"
    if "right" in text:
        if "sharp" in text:
            return "sharp_right"
        return "slight_right" if ("slight" in text or "bear" in text) else "right"
    return "straight"


def spoken(instruction, street, metres, imperial=True, band="near"):
    """One line to say out loud.

    "now" drops the distance: at six seconds out, "turn left onto Grand" is
    the instruction and "in 80 feet" is noise in front of it.
    """
    what = instruction or "continue"
    # Arrival has no "onto". The router gives the arrive maneuver the street
    # it ends on, and this appended it: "In 400 ft, arrive at your destination
    # onto Broadway" - nonsense on screen, worse said aloud (fixed when the
    # prompts started being spoken, 2026-09-28).
    arriving = "arrive" in what.lower() or "destination" in what.lower()
    where = f" onto {street}" if street and street not in what and not arriving else ""
    if band == "now":
        return f"{what}{where}".strip()
    return f"In {distance_text(metres, imperial)}, {what}{where}".strip()


class Follower:
    """Live state for following one route. Feed it fixes, ask it questions.

    Holds no controls and no page, so a test can ride a whole route through it
    with a list of coordinates. The UI reads `status()` after every fix.
    """

    def __init__(self, route=None, offroute_m=45, imperial=True):
        self.route = route
        self.offroute_m = float(offroute_m)
        self.imperial = imperial
        self.off_route = False
        self.off_since = None
        self.arrived = False
        self._bad_run = 0
        self._said = set()          # (maneuver index, band) already spoken
        self._last = None
        self.reroutes = 0

    def set_route(self, route):
        """Start following a new route, forgetting everything about the old.

        Called on the first route and on every re-route. The spoken-prompt
        memory has to be cleared, or a re-routed rider is never told about the
        turn they are now approaching because a maneuver with the same index
        was already announced on the route that got replaced.
        """
        self.route = route
        self.off_route = False
        self.off_since = None
        self.arrived = False
        self._bad_run = 0
        self._said.clear()
        self._last = None
        return route

    def note_reroute(self):
        self.reroutes += 1

    def update(self, position, speed_ms=None, now=None):
        """Take a fix. Returns a status dict; also decides what to say.

        `position` is (lat, lon). The returned dict is the whole UI contract:
        everything the HUD draws comes out of here, so there is one place
        where "what is going on" gets decided.
        """
        now = now if now is not None else time.time()
        route = self.route
        if route is None or not getattr(route, "ok", False):
            self._last = {"ok": False, "reason": "no route", "say": None}
            return self._last

        man, metres, _man_off = routing.next_maneuver(route, position)
        # Off-route comes from `progress`, NOT from `next_maneuver`.
        # `next_maneuver` returns (None, None, None) for a route with fewer
        # than two maneuvers - which is a real case, a straight run with no
        # turns - and taking the off-route distance from it meant that route
        # could never report being off at all.
        _index, _along, off = routing.progress(route, position)
        left_m, left_s = routing.remaining(route, position)
        self._track_off_route(off, now)

        # Arrival is distance to the end of the line, not to the last
        # maneuver: the last maneuver IS "arrive", and its own distance counts
        # down to the same point.
        if left_m is not None and left_m < 25.0:
            self.arrived = True

        say = None
        if not self.off_route and man is not None and not self.arrived:
            say = self._maybe_say(man, metres, speed_ms)

        self._last = {
            "ok": True,
            "reason": "",
            "maneuver": man,
            "instruction": getattr(man, "instruction", "") if man else "",
            "street": getattr(man, "street", "") if man else "",
            "icon": turn_icon(getattr(man, "instruction", "") if man else ""),
            "turn_in_m": metres,
            "off_route_m": off,
            "off_route": self.off_route,
            "arrived": self.arrived,
            "left_m": left_m,
            "left_s": left_s,
            "eta": eta_text(left_s, now,
                            estimate=getattr(route, "source", "") == "offline"),
            "source": getattr(route, "source", ""),
            "say": say,
        }
        return self._last

    @property
    def last(self):
        return self._last

    def status(self):
        return self._last

    def should_reroute(self):
        """Whether to ask for a new route now.

        Only once the off-route state is confirmed, and never after arrival -
        a rider parking twenty metres from the pin does not want a re-route
        back to the pin.
        """
        return bool(self.off_route and not self.arrived)

    # -------------------------------------------------------------- private
    def _track_off_route(self, off, now):
        threshold = self.offroute_m
        if off is None:
            return
        if not self.off_route:
            if off > threshold:
                self._bad_run += 1
                if self._bad_run >= CONFIRM_FIXES:
                    self.off_route = True
                    self.off_since = now
            else:
                self._bad_run = 0
            return
        # Already off. Coming back has to clear the deadband.
        if off <= threshold * RETURN_FRACTION:
            self.off_route = False
            self.off_since = None
            self._bad_run = 0

    def _maybe_say(self, man, metres, speed_ms):
        """The one prompt due for this maneuver, or None.

        Walks the bands from NEAREST to furthest and fires the first one that
        is due and unsaid. Nearest first is the whole point: inside the "now"
        band every wider band is also technically due, so walking outwards-in
        would announce "in a quarter mile, turn left" at the corner itself.
        Firing a band also marks every wider one as said, so one that was
        crossed without being announced cannot fire later.
        """
        if metres is None:
            return None
        index = getattr(man, "index", None)
        points = prompt_points(speed_ms)
        for _secs, band in reversed(PROMPT_BANDS):
            if metres > points.get(band, 0.0):
                continue
            if (index, band) in self._said:
                # This band has been announced, and every band nearer than it
                # has already been ruled out above. Nothing is due.
                return None
            # Every wider band for this maneuver is now moot: crossing
            # straight into "now" must not leave "far" armed to fire later.
            for _s2, other in PROMPT_BANDS:
                self._said.add((index, other))
                if other == band:
                    break
            return spoken(getattr(man, "instruction", ""),
                          getattr(man, "street", ""), metres,
                          self.imperial, band)
        return None


def bearing(a, b):
    """Compass degrees from (lat, lon) `a` to `b`. For orienting the map."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
