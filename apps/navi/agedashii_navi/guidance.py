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

**A speed limit that is honest about where it came from.** A graph "class"
is a speed bucket: the builder's default speed nearest each road's posted
maxspeed. So `limit_mph` reads the bucket as the posted limit, rounded to
5 mph, and falls back to a *typical* California limit for the road's real
class (RGRAPH03) where the bucket is only that class's default. Washi's
`SPEED_KMH` is a different quantity - door-to-door speed fitted to routing
times - and is never shown as a limit. `LIMIT_SOURCE` says all of this.

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

#: A maneuver already announced is announced again, short of "now", only once
#: the distance has fallen below this share of where it last was. The bands
#: move with speed: picked at a light, "far" (crawl, 250 m) fired at 178 m, then
#: riding off at 15 m/s made "near" (300 m) due at 170 m - the same "In 600 ft,
#: turn right" twice, 6 s apart. And a maneuver that first comes up just
#: outside "near" got "far" then "near" 2 s apart. At one speed far to near is
#: 1/3, and crawl 90/250.
REPEAT_FRACTION = 0.6

#: A wider band is not spoken when the "now" point is less than this many
#: seconds away at her speed: "now" is about to say it. Right after a turn the
#: next one can come up just outside "now", and at 15 m/s "In 350 ft, turn
#: left" then "Turn left" came 1 s apart (minion 1's rides, #4).
NOW_SOON_S = 3.0

#: Typical posted limits in California by OSM road class, in mph. NOT from the
#: graph - the graph has no maxspeed - and labelled as typical everywhere it
#: is shown. See the module docstring.
TYPICAL_MPH = {
    "motorway": 65, "motorway_link": 45, "trunk": 55, "trunk_link": 40,
    "primary": 45, "primary_link": 35, "secondary": 40, "secondary_link": 30,
    "tertiary": 35, "tertiary_link": 25, "unclassified": 30,
    "residential": 25, "living_street": 15, "service": 15, "road": 25,
}
LIMIT_SOURCE = ("the map's posted limit, to the nearest 5 mph, where it has one; "
                "typical for the road where it has none - not a reading of the sign")

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


#: The road-graph builder's default speed per highway class, km/h, in
#: routing.CLASSES order (tools/navi-routing/build_graph.py, SPEED_KMH). A
#: graph "class" is the INDEX of whichever of these is nearest a road's posted
#: maxspeed - or its own highway default when it has none - so the class is
#: the posted limit, quantized. It was being read as a highway type instead,
#: which called a 35 mph street a "trunk link" limited to 40.
BUCKET_KMH = (105, 60, 90, 55, 65, 45, 55, 40, 45, 35, 40, 30, 12, 20, 30)


def _bucket_of(kmh):
    """The bucket the builder files `kmh` under: nearest, first index on a tie."""
    return min(range(len(BUCKET_KMH)), key=lambda i: abs(BUCKET_KMH[i] - kmh))


def _round5(mph_value):
    return int(round(mph_value / 5.0) * 5)


def limit_mph(graph, lat, lon, heading=None):
    """(mph, road class) for the road at this point, or (None, "").

    The speed bucket is the posted limit, quantized, wherever the map has one.
    Where the bucket is just the road's own class default - most residential
    streets carry no maxspeed, and the builder's residential default is 19 mph
    - the typical California limit for what the road REALLY is (RGRAPH03's
    highway class) is used instead. On an older graph there is no real class,
    so the bucket alone. A posted limit that happens to share its class
    default's bucket reads as the typical one: at most 5 mph off.

    Snapped to an edge rather than a junction so that standing at a corner
    does not report the side street. None with no graph installed - a normal
    state, and the UI says so rather than inventing a limit.
    """
    if graph is None:
        return None, ""
    try:
        snap = graph.snap_edge(lat, lon, heading=heading)
    except Exception:                    # noqa: BLE001 - snapping never fatal
        return None, ""
    if snap is None:
        return None, ""
    k = snap.fwd if getattr(snap, "fwd", -1) >= 0 else getattr(snap, "bwd", -1)
    try:
        bucket = graph.cls[k] if k >= 0 else _edge_class(graph, snap)
        posted = _round5(BUCKET_KMH[bucket] / 1.609344)
    except (IndexError, TypeError):
        return None, ""
    highway = getattr(graph, "highway", None)
    hw = highway(snap.seg) if callable(highway) and getattr(snap, "seg", -1) >= 0 else None
    if hw is None:
        return posted, "road"
    name = routing.CLASSES[hw]
    if bucket == _bucket_of(BUCKET_KMH[hw]):
        return TYPICAL_MPH.get(name), name
    return posted, name


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


#: How far in time an anchored prompt may move from its band's target: from
#: target / ANCHOR_WINDOW to target * ANCHOR_WINDOW. Wide enough that his four
#: distances cover 15-75 mph, narrow enough that "far" stays far and "near"
#: stays near - and never inside the 6 s "now".
ANCHOR_WINDOW = 1.6
FT_PER_M = 3.280839895


def prompt_points(speed_ms, anchors_ft=()):
    """{band: metres} at which each prompt for the next turn should fire.

    Time-based above a crawl and fixed below it. With `anchors_ft` - the
    distances her recorded voice says, in feet - the far and near prompts fire
    instead when she REACHES one of them: the one nearest the band's time,
    within ANCHOR_WINDOW, far and near never the same. His four (0.1 mi, a
    quarter, a half, a mile) are the points Waze announces at, not a scale to
    round to; fired on a clock, the near prompt on every 25-35 mph street fell
    between them and the phone said the number. "now" never moves, and a band
    with no distance in its window keeps its time.
    """
    if speed_ms is None or speed_ms < CRAWL_MS:
        return dict(CRAWL_BANDS_M)
    points = {band: speed_ms * secs for secs, band in PROMPT_BANDS}
    candidates = sorted({ft / FT_PER_M for ft in anchors_ft or () if ft and ft > 0})
    taken = None
    for secs, band in PROMPT_BANDS:                  # far first, then near
        if band == "now" or not candidates:
            continue
        best = None
        for metres in candidates:
            if taken is not None and metres >= taken:
                continue                             # near must come after far
            ratio = metres / (speed_ms * secs)
            if 1 / ANCHOR_WINDOW <= ratio <= ANCHOR_WINDOW:
                key = abs(math.log(ratio))
                if best is None or key < best[0]:
                    best = (key, metres)
        if best is not None:
            points[band] = taken = best[1]
        else:
            taken = points[band]
    return points


#: routing.KINDS -> glyph. "exit" is left out: its side is in the words.
_KIND_ICON = {"turn-left": "left", "turn-right": "right",
              "slight-left": "slight_left", "slight-right": "slight_right",
              "sharp-left": "sharp_left", "sharp-right": "sharp_right",
              "uturn": "u_turn", "continue": "straight", "start": "straight",
              "merge": "merge", "arrive": "flag", "roundabout": "roundabout"}


def turn_icon(instruction, kind=None):
    """A glyph name for a maneuver: from the router's `kind`, else its words.

    KIND BEATS WORDS. Words are for her to read; `kind` (routing.KINDS) is for
    the machine to switch on. Parsing the sentence broke the first time the
    router reworded anything: its new offline "keep left" read as a full left
    turn, wrong arrow included. The words are the fallback for a maneuver
    with no kind (a server route's roundabout exit, an older route). An exit
    still reads its side from the words, which is the only place it is said.
    Deliberately coarse: a few shapes a rider can tell apart at a glance beat
    many they cannot.
    """
    text = (instruction or "").lower()
    if kind in _KIND_ICON and text.strip():
        return _KIND_ICON[kind]
    if not text.strip():
        # No instruction is not "go straight". An arrow on a nav banner IS an
        # instruction, read at 60 mph; with nothing to say, draw nothing.
        return None
    if "roundabout" in text:
        # Before "exit": "take the 2nd exit at the roundabout" is a roundabout.
        return "roundabout"
    if "u-turn" in text or "turn around" in text:
        return "u_turn"
    if "arrive" in text or "destination" in text:
        return "flag"
    # A merge or a ramp drawn as a 90-degree turn arrow would be a wrong
    # instruction at a glance, so they get their own shapes.
    if "merge" in text:
        return "merge"
    if "exit" in text or "ramp" in text:
        return "ramp_left" if "left" in text else "ramp_right"
    if "left" in text:
        if "sharp" in text:
            return "sharp_left"
        return ("slight_left" if any(w in text for w in ("slight", "bear", "keep"))
                else "left")
    if "right" in text:
        if "sharp" in text:
            return "sharp_right"
        return ("slight_right" if any(w in text for w in ("slight", "bear", "keep"))
                else "right")
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
    # A whole sentence (Valhalla's words) takes nothing after its full stop:
    # "Continue toward CA 24/Berkeley. onto Grove Shafter Freeway" (#9).
    sentence = what.rstrip().endswith((".", "!", "?"))
    where = (f" onto {street}" if street and street not in what
             and not arriving and not sentence else "")
    if band == "now":
        return f"{what}{where}".strip()
    return f"In {distance_text(metres, imperial)}, {what}{where}".strip()


def _next_spoken(route, man, metres):
    """(maneuver, metres to it) for the next maneuver that is SAID.

    `man` is the next maneuver on the route; if the router marked it
    spoken=False (a name change on a straight road), look past it to the next
    one, measuring from the same place - so the turn after a name change gets
    its far warning while the name change is still ahead. (None, None) if
    nothing ahead is spoken, which the arrival maneuver makes impossible.
    """
    if metres is None or getattr(man, "spoken", True):
        return man, metres
    mans = list(getattr(route, "maneuvers", ()) or ())
    at = next((i for i, m in enumerate(mans) if m is man), None)
    if at is None:
        return man, metres
    along = man.cum_m - metres
    for m in mans[at + 1:]:
        if getattr(m, "spoken", True):
            return m, m.cum_m - along
    return None, None


class Follower:
    """Live state for following one route. Feed it fixes, ask it questions.

    Holds no controls and no page, so a test can ride a whole route through it
    with a list of coordinates. The UI reads `status()` after every fix.
    """

    def __init__(self, route=None, offroute_m=45, imperial=True, anchors_ft=()):
        #: The distances her recorded voice says, in feet (see prompt_points).
        #: Set by whoever builds the Follower; empty means clock-timed prompts.
        self.anchors_ft = tuple(anchors_ft or ())
        self.route = route
        self.offroute_m = float(offroute_m)
        self.imperial = imperial
        self.off_route = False
        self.off_since = None
        self.arrived = False
        self._bad_run = 0
        self._said = set()          # (maneuver index, band) already spoken
        self._said_at = {}          # maneuver index -> metres when last spoken
        self._last = None
        self.reroutes = 0
        self.fired = None           # the parts of the prompt just fired

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
        self._said_at.clear()
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
        was_arrived = self.arrived
        if left_m is not None and left_m < 25.0:
            self.arrived = True

        say = None
        self.fired = None
        # `was_arrived`, not `arrived`: the fix that ARRIVES may still say so.
        # Below a crawl the "now" band is 25 m - the arrival distance - and
        # checking the new state swallowed the only prompt saying she is there.
        if not self.off_route and man is not None and not was_arrived:
            # The banner shows the next maneuver; the voice warns about the
            # next one she has to DO. A name change is shown, not said.
            talk, talk_m = _next_spoken(route, man, metres)
            if talk is not None:
                say = self._maybe_say(talk, talk_m, speed_ms)

        self._last = {
            "ok": True,
            "reason": "",
            "maneuver": man,
            "instruction": getattr(man, "instruction", "") if man else "",
            "street": getattr(man, "street", "") if man else "",
            "icon": turn_icon(getattr(man, "instruction", "") if man else "",
                              getattr(man, "kind", None)),
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
            "say_parts": self.fired if say else None,
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
        points = prompt_points(speed_ms, self.anchors_ft)
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
            # Short of "now", nothing when "now" is seconds away: it is about
            # to say this, and two prompts a second apart are one too many.
            now_at = points.get("now", 0.0)
            if (band != "now" and speed_ms is not None and speed_ms > 0
                    and (metres - now_at) / speed_ms < NOW_SOON_S):
                return None
            # Short of "now", not again while it is still about as far as
            # when she last heard it: only her speed moved the bands.
            before = self._said_at.get(index)
            if band != "now" and before is not None and metres > before * REPEAT_FRACTION:
                return None
            self._said_at[index] = metres
            # A continue is said once, whichever band first comes due.
            if getattr(man, "kind", None) == "continue":
                for _s3, b in PROMPT_BANDS:
                    self._said.add((index, b))
            # The parts as well as the sentence: her recorded lines replace the
            # MANEUVER and the DISTANCE, not our wording, so the voice layer
            # needs to know which turn this is rather than re-parse the text.
            self.fired = {"instruction": getattr(man, "instruction", ""),
                          "kind": getattr(man, "kind", None),
                          # Which maneuver: its far, near and now prompts share
                          # it, so a flourish can be held for all three.
                          "index": index,
                          "street": getattr(man, "street", ""),
                          "metres": metres, "band": band}
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
