"""Turn-by-turn routing on the phone, with the radio off.

The same rule as the map tiles next door in `tileserver.py`: the thing you
need most on a motorcycle in a dead zone must not depend on the dead zone
having gone away. So the router is a pure-CPython A* over a packed road
graph that ships with the app - no C extension, no Android wheel, nothing
but `math`, `struct`, `heapq` and `array`.

WHERE THE GRAPH COMES FROM
    A `.rg` file is built off-device (`build_graph.py`, on the Pi) out of an
    OSM extract: nodes, a CSR adjacency, per-class lengths, segment
    geometry, street names and a uniform snapping grid, all in one blob that
    loads by `array.fromfile` rather than being parsed. The Bay Area is
    78 MB / 725k nodes and loads in ~55 ms; a 40 km route takes ~150 ms.

    Nothing here builds a graph. If there is no `.rg` installed that is a
    normal state, not an error - `route()` falls back to the server's
    Valhalla and *says* that is what it did.

THE TWO HONESTY RULES
    1. `Route.source` is "offline" or "server" and is set by the code path
       that actually produced the route. It is never guessed and never
       defaulted to the flattering answer.
    2. A failure carries the reason, in words, all the way to the screen.
       "no graph installed" and "that point is outside the mapped area" and
       "the server said no route" are different problems with different
       fixes, and collapsing them into "routing failed" wastes the rider's
       time at exactly the wrong moment.

THREADING
    The graph loads once, lazily, on a background thread: opening the Trips
    page must not block the UI for 50 ms, and app startup must not pay for a
    78 MB read that most launches never use. `route()` blocks while it
    searches, so callers run it off the UI thread - `route_async()` is the
    awaitable wrapper that does that for you.

    `Graph.astar_fast` reuses per-graph scratch buffers (that is what makes
    it fast), so two searches on one Graph would corrupt each other. Every
    search therefore takes `_SEARCH_LOCK`. Routes are ~150 ms and the user
    asks for one at a time; serialising them is free and the alternative is
    a wrong route.

Ported from the measured routing spike. The search, the snap, the polyline
assembly and the maneuver rules are that code, unchanged in behaviour; what
is added here is the file discovery, the background load, the server
fallback, and the per-point distance table that live guidance needs.
"""
import heapq
import json
import math
import os
import struct
import threading
import time
from array import array
from collections import namedtuple
from pathlib import Path

# ---------------------------------------------------------------- constants

#: Env var that overrides where the graph is looked for. Mirrors NVII_MBTILES.
ENV_OVERRIDE = "NVII_ROUTE_GRAPH"
#: The shipped region. Same naming as bayarea.mbtiles, deliberately.
GRAPH_FILENAME = "bayarea.rg"
#: Server endpoint that proxies to self-hosted Valhalla (dashbored-v3.py).
SERVER_ROUTE_PATH = "/api/valhalla/route"
#: Beyond this, a snap means "you are not in the mapped area", not "close
#: enough". 2 km is further than any legitimate gap between a GPS fix and a
#: drivable road, and well inside the distance to the edge of an extract.
MAX_SNAP_M = 2000.0

CLASSES = ["motorway", "motorway_link", "trunk", "trunk_link", "primary",
           "primary_link", "secondary", "secondary_link", "tertiary",
           "tertiary_link", "unclassified", "residential", "living_street",
           "service", "road"]
# Effective door-to-door speeds, least-squares fitted to Valhalla's motorcycle
# costing over 45 Bay Area routes. Lower than the signposted speeds because
# they absorb signals, stop signs and turn delay.
SPEED_KMH = [79.0, 38.7, 79.0, 38.7, 36.1, 38.7, 36.1, 38.7,
             28.0, 38.7, 24.0, 12.1, 12.1, 6.3, 6.3]
# The A* heuristic must be admissible for the cost function actually used,
# i.e. the fastest *calibrated* class, not the fastest signposted one.
MAXSPEED_MS = max(SPEED_KMH) / 3.6
# seconds per decimetre, per class -- cost = len_dm * SEC_PER_DM[cls]
SEC_PER_DM = [0.1 / (k / 3.6) for k in SPEED_KMH]
# flat penalty added when leaving a node, stands in for intersection delay
JUNCTION_PENALTY_S = 1.5

EARTH_R = 6371008.8


# ------------------------------------------------------------ graph loading

APP_ROOT = Path(__file__).resolve().parent.parent


def graph_path():
    """Where the offline road graph lives, or None if none is installed.

    Same search order, and the same three locations, as
    `api.mbtiles_path()`: an explicit setting wins, then a file shipped
    inside the app bundle, then one downloaded beside the app. The two
    belong together - a map you can see but cannot route on, or the
    reverse, is half an app - so they are found the same way on purpose.

    Returning None is a normal outcome, not a failure.
    """
    candidates = []
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        candidates.append(Path(override))
    # APP_ROOT is a module global so a test can point the bundled-asset
    # search somewhere harmless. Once the real 78 MB graph shipped in
    # assets/, every "nothing is installed" test started finding it.
    candidates += [APP_ROOT / "assets" / "maps" / GRAPH_FILENAME,
                   APP_ROOT / "maps" / GRAPH_FILENAME,
                   Path.home() / ".nvii-dash" / GRAPH_FILENAME]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


_LOCK = threading.RLock()
_SEARCH_LOCK = threading.Lock()
_READY = threading.Event()
_LOAD = {"phase": "idle", "graph": None, "path": None,
         "detail": "not started", "load_ms": 0.0, "nodes": 0, "edges": 0}

NO_GRAPH_DETAIL = (
    "no offline road graph installed - put bayarea.rg beside the map "
    "archive, or set NVII_ROUTE_GRAPH")


def start_loading(path=None):
    """Begin loading the graph on a background thread. Returns the phase.

    Idempotent and cheap: call it from `build()` on every page open. The
    load happens once per process; later calls return the phase of the load
    already in flight or already finished.

    Phases: "absent" (nothing installed), "loading", "ready", "failed".

    A failure is sticky. Re-reading a 78 MB file that has already failed to
    parse, every time a page opens, turns a broken install into a slow one
    as well; replacing the file needs an app restart on a phone anyway.
    `reset()` is the escape hatch for anything that changes the file in
    place.
    """
    with _LOCK:
        if _LOAD["phase"] in ("loading", "ready", "failed"):
            return _LOAD["phase"]
        chosen = str(path) if path else graph_path()
        if not chosen:
            _LOAD.update(phase="absent", graph=None, path=None,
                         detail=NO_GRAPH_DETAIL, load_ms=0.0,
                         nodes=0, edges=0)
            _READY.set()
            return "absent"
        _LOAD.update(phase="loading", graph=None, path=chosen,
                     detail=f"loading {Path(chosen).name}...", load_ms=0.0,
                     nodes=0, edges=0)
        _READY.clear()
        worker = threading.Thread(target=_load_worker, args=(chosen,),
                                  name="nvii-route-graph", daemon=True)
    worker.start()
    return "loading"


def _load_worker(chosen):
    t0 = time.perf_counter()
    try:
        g = Graph(chosen)
    except Exception as exc:                          # noqa: BLE001
        # A truncated download or a graph from an incompatible builder is a
        # real possibility on a phone; it must degrade to the server, not to
        # a traceback on a blank page.
        with _LOCK:
            _LOAD.update(phase="failed", graph=None,
                         detail=f"{Path(chosen).name} could not be read: {exc}")
    else:
        ms = (time.perf_counter() - t0) * 1000
        with _LOCK:
            _LOAD.update(phase="ready", graph=g, load_ms=ms,
                         nodes=g.n, edges=g.e,
                         detail=(f"{g.n:,} nodes, {g.e:,} edges from "
                                 f"{Path(chosen).name} in {ms:.0f} ms"))
    finally:
        _READY.set()


def status():
    """A snapshot of the loader, safe to render straight onto the screen."""
    with _LOCK:
        return {"phase": _LOAD["phase"], "path": _LOAD["path"],
                "detail": _LOAD["detail"], "load_ms": _LOAD["load_ms"],
                "nodes": _LOAD["nodes"], "edges": _LOAD["edges"]}


def ready():
    """True when an offline route can be computed right now."""
    with _LOCK:
        return _LOAD["phase"] == "ready" and _LOAD["graph"] is not None


def graph(timeout=None):
    """The loaded Graph, or None. Starts the load if it has not started.

    `timeout=None` waits as long as the load takes (~55 ms), which is the
    right call from a worker thread. Pass 0 from the UI thread to ask
    "is it ready yet?" without ever blocking.
    """
    start_loading()
    if timeout is None:
        _READY.wait()
    elif timeout > 0:
        _READY.wait(timeout)
    with _LOCK:
        return _LOAD["graph"]


def reset():
    """Drop the loaded graph. For tests, and for changing the graph file."""
    with _LOCK:
        _LOAD.update(phase="idle", graph=None, path=None,
                     detail="not started", load_ms=0.0, nodes=0, edges=0)
        _READY.clear()


# --------------------------------------------------------------- the graph

class Graph:
    """A packed .rg road graph, read straight into arrays.

    Ported unchanged from the spike. Everything is a flat `array`, and the
    file layout matches it byte for byte, so loading is `fromfile` rather
    than parsing - which is the entire reason 78 MB opens in 55 ms.
    """

    __slots__ = ("n", "e", "s", "p", "xy", "off", "to", "len", "cls", "seg",
                 "seg_off", "seg_name", "geom", "str_off", "blob",
                 "grid_off", "grid_nod", "gw", "gh", "minx", "miny", "cell",
                 "olat", "olon", "mx", "my", "path",
                 "_dist", "_par", "_stamp", "_gen")

    MAGIC = b"RGRAPH01"

    def __init__(self, path):
        self.path = str(path)
        with open(path, "rb") as f:
            if f.read(8) != self.MAGIC:
                raise ValueError("not an RGRAPH01 graph file")
            n, e, s, p, nblob = struct.unpack("<5I", f.read(20))
            nstr, gw, gh = struct.unpack("<3I", f.read(12))
            (self.olat, self.olon, self.mx, self.my,
             self.minx, self.miny, self.cell) = struct.unpack("<7d", f.read(56))
            self.n, self.e, self.s, self.p = n, e, s, p
            self.gw, self.gh = gw, gh

            def rd(typecode, count):
                a = array(typecode)
                a.fromfile(f, count)
                return a

            self.xy = rd("f", 2 * n)
            self.off = rd("I", n + 1)
            self.to = rd("I", e)
            self.len = rd("I", e)
            self.cls = rd("B", e)
            self.seg = rd("I", e)
            self.seg_off = rd("I", s + 1)
            self.seg_name = rd("I", s)
            self.geom = rd("i", 2 * p)
            self.str_off = rd("I", nstr + 1)
            self.grid_off = rd("I", gw * gh + 1)
            self.grid_nod = rd("I", n)
            self.blob = f.read(nblob)

    # ---------------------------------------------------------- helpers
    def name(self, sid):
        i = self.seg_name[sid]
        return self.blob[self.str_off[i]:self.str_off[i + 1]].decode(
            "utf-8", "replace")

    def proj(self, lat, lon):
        return (lon - self.olon) * self.mx, (lat - self.olat) * self.my

    def snap(self, lat, lon):
        """Nearest graph node to a lat/lon, via the stored uniform grid.

        Returns (node_index, metres). Note *node*: this lands on the nearest
        junction, not the nearest point along an edge, so a fix in the
        middle of a long block snaps to one end of it. See the module notes.
        """
        x, y = self.proj(lat, lon)
        cell, gw, gh = self.cell, self.gw, self.gh
        cx = int((x - self.minx) / cell)
        cy = int((y - self.miny) / cell)
        xy, goff, gnod = self.xy, self.grid_off, self.grid_nod
        best = -1
        bestd = 1e30
        r = 0
        while r < max(gw, gh):
            found = False
            for j in range(max(0, cy - r), min(gh, cy + r + 1)):
                # only the ring at radius r, after r == 0
                if r and cy - r < j < cy + r:
                    cols = (cx - r, cx + r)
                else:
                    cols = range(max(0, cx - r), min(gw, cx + r + 1))
                for i in cols:
                    if i < 0 or i >= gw:
                        continue
                    c = j * gw + i
                    for k in range(goff[c], goff[c + 1]):
                        nd = gnod[k]
                        dx = xy[2 * nd] - x
                        dy = xy[2 * nd + 1] - y
                        d = dx * dx + dy * dy
                        if d < bestd:
                            bestd = d
                            best = nd
                            found = True
            if best >= 0 and (found is False or r * cell > math.sqrt(bestd)):
                break
            r += 1
        return best, math.sqrt(bestd)

    # ---------------------------------------- A* with reusable scratch
    def astar_fast(self, src, dst, metric="time", weight=1.0):
        """A*, with the O(n) per-call scratch allocation hoisted.

        A fresh [inf]*n / [-1]*n / bytearray(n) per route costs ~3n writes,
        which is the one thing that scales with graph size rather than route
        length. Here the buffers are allocated once per Graph and
        invalidated with a generation counter, so the per-route reset is
        O(settled) instead of O(n).

        NOT reentrant: the scratch is shared. Callers hold _SEARCH_LOCK.
        """
        n = self.n
        try:
            dist = self._dist
        except AttributeError:
            self._dist = dist = [0.0] * n
            self._par = [-1] * n
            self._stamp = array("I", bytes(4 * n))
            self._gen = 0
        par, stamp = self._par, self._stamp
        self._gen += 1
        if self._gen >= 0x7FFFFFFF:          # generation wrap, ~2e9 routes
            self._stamp = stamp = array("I", bytes(4 * n))
            self._gen = 1
        gen = self._gen
        CLOSED = 0x80000000

        off, to, elen, ecls = self.off, self.to, self.len, self.cls
        xy = self.xy
        gx = xy[2 * dst]
        gy = xy[2 * dst + 1]
        sqrt = math.sqrt
        push = heapq.heappush
        pop = heapq.heappop
        if metric == "time":
            spd = SEC_PER_DM
            inv_h = weight / MAXSPEED_MS
            pen = JUNCTION_PENALTY_S
        else:
            spd = [0.1] * len(SEC_PER_DM)
            inv_h = weight
            pen = 0.0

        dist[src] = 0.0
        par[src] = -1
        stamp[src] = gen
        dx = xy[2 * src] - gx
        dy = xy[2 * src + 1] - gy
        heap = [(sqrt(dx * dx + dy * dy) * inv_h, src)]
        settled = 0
        while heap:
            _, u = pop(heap)
            su = stamp[u]
            if su & CLOSED:
                continue
            stamp[u] = gen | CLOSED
            settled += 1
            if u == dst:
                return dist[u], (par, stamp, gen), settled
            du = dist[u]
            for k in range(off[u], off[u + 1]):
                v = to[k]
                sv = stamp[v]
                if sv == (gen | CLOSED):
                    continue
                nd = du + elen[k] * spd[ecls[k]] + pen
                if sv != gen or nd < dist[v]:
                    dist[v] = nd
                    par[v] = k
                    stamp[v] = gen
                    dx = xy[2 * v] - gx
                    dy = xy[2 * v + 1] - gy
                    push(heap, (nd + sqrt(dx * dx + dy * dy) * inv_h, v))
        return None, (par, stamp, gen), settled


def _tail(g, k):
    """Owner node of directed edge index k (binary search in CSR offsets)."""
    off = g.off
    lo, hi = 0, g.n
    while lo < hi:
        mid = (lo + hi) // 2
        if off[mid] <= k:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def edge_path(g, parent, src, dst):
    """Walk the parent array back from dst to src. List of edge ids, or None."""
    path = []
    v = dst
    while v != src:
        k = parent[v]
        if k < 0:
            return None
        path.append(k)
        v = _tail(g, k)
        if len(path) > g.e:                  # cycle guard; should never fire
            return None
    path.reverse()
    return path


# ---------------------------------------------------------------- geometry

def haversine_m(a, b):
    """Metres between two (lat, lon) pairs."""
    lat1, lon1, lat2, lon2 = (math.radians(a[0]), math.radians(a[1]),
                              math.radians(b[0]), math.radians(b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def _edge_points(g, k):
    """The lat/lon shape of one directed edge, in travel order."""
    sid = g.seg[k]
    rev = sid & 0x80000000
    sid &= 0x7FFFFFFF
    a, b = g.seg_off[sid], g.seg_off[sid + 1]
    geom = g.geom
    chunk = [(geom[2 * i] / 1e7, geom[2 * i + 1] / 1e7) for i in range(a, b)]
    if rev:
        chunk.reverse()
    return chunk


def polyline(g, edges):
    """Concatenated lat/lon shape points for a list of directed edge ids."""
    return _polyline_bounds(g, edges)[0]


def _polyline_bounds(g, edges):
    """(points, bounds) where bounds[i] = (first_idx, last_idx) of edge i.

    The spike's `polyline()` threw the per-edge boundaries away. Live
    guidance needs them: they are what ties a maneuver to a point on the
    drawn line, and therefore what lets "300 m to the next turn" mean the
    distance along the road rather than the distance as the crow flies.
    """
    pts = []
    bounds = []
    for k in edges:
        chunk = _edge_points(g, k)
        if not chunk:
            last = max(len(pts) - 1, 0)
            bounds.append((last, last))
            continue
        if pts and pts[-1] == chunk[0]:
            start = len(pts) - 1
            pts.extend(chunk[1:])
        else:
            start = len(pts)
            pts.extend(chunk)
        bounds.append((start, len(pts) - 1))
    return pts, bounds


def cumulative_m(pts, lengths=None, bounds=None):
    """Distance from the start of the route to each polyline point.

    When the graph's own edge lengths are supplied they are authoritative -
    each edge's points are scaled to span exactly its stored length - so
    the totals here and the totals in the turn list are the same number,
    not two nearly-equal ones.
    """
    cum = [0.0] * len(pts)
    if len(pts) < 2:
        return cum
    if lengths is None or bounds is None:
        for i in range(1, len(pts)):
            cum[i] = cum[i - 1] + haversine_m(pts[i - 1], pts[i])
        return cum
    for i, (s, e) in enumerate(bounds):
        if e <= s:
            continue
        raw = [haversine_m(pts[j], pts[j + 1]) for j in range(s, e)]
        total = sum(raw)
        target = lengths[i]
        scale = (target / total) if total > 0 else 0.0
        acc = cum[s]
        for j, step in enumerate(raw):
            acc += step * scale
            cum[s + 1 + j] = acc
    return cum


def total_length_m(g, edges):
    return sum(g.len[k] for k in edges) / 10.0


def total_time_s(g, edges):
    return sum(g.len[k] * SEC_PER_DM[g.cls[k]] for k in edges)


# --------------------------------------------------------------- maneuvers

Maneuver = namedtuple("Maneuver", "instruction street distance_m at index cum_m")
Maneuver.__doc__ = """One turn-by-turn instruction.

instruction  what to do, e.g. "turn left onto Harrison Street"
street       the street you are on *after* obeying it ("" if unnamed)
distance_m   how far you then drive before the next instruction
at           (lat, lon) of the point the instruction applies at
index        that point's index into Route.points
cum_m        distance from the start of the route to that point
"""


def bearing(a, b):
    """Initial compass bearing from (lat, lon) a to b, degrees."""
    p = math.pi / 180
    y = math.sin((b[1] - a[1]) * p) * math.cos(b[0] * p)
    x = (math.cos(a[0] * p) * math.sin(b[0] * p) -
         math.sin(a[0] * p) * math.cos(b[0] * p) * math.cos((b[1] - a[1]) * p))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


TURNS = [(-180, -160, "make a U-turn"), (-160, -50, "turn left"),
         (-50, -20, "bear left"), (-20, 20, "continue straight"),
         (20, 50, "bear right"), (50, 160, "turn right"),
         (160, 180, "make a U-turn")]

COMPASS = ["north", "northeast", "east", "southeast",
           "south", "southwest", "west", "northwest"]


def turn_word(delta):
    """The instruction verb for a heading change in degrees, -180..180."""
    delta = (delta + 540) % 360 - 180
    for lo, hi, word in TURNS:
        if lo <= delta < hi or (hi == 180 and delta == 180):
            return word
    return "continue straight"                        # unreachable in practice


def compass_word(deg):
    return COMPASS[int((deg + 22.5) % 360 / 45)]


def _maneuvers_from_graph(g, edges, pts, bounds, cum):
    """Turn-by-turn list. The spike's rules, plus where each turn happens.

    A junction only earns an instruction if the geometry actually turns
    *and* there was a real alternative (degree > 2), or the street name
    changes. Without the degree test every kink in a road becomes a "bear
    left" and the list is unreadable.
    """
    if not edges:
        return []
    out = []
    cur_name = g.name(g.seg[edges[0]] & 0x7FFFFFFF)
    start_b = bearing(pts[0], pts[1]) if len(pts) > 1 else 0.0
    out.append([f"head {compass_word(start_b)}"
                + (f" on {cur_name}" if cur_name else ""),
                cur_name, 0.0, pts[0], 0, 0.0])
    run = g.len[edges[0]] / 10.0
    for i in range(1, len(edges)):
        k0, k1 = edges[i - 1], edges[i]
        nm = g.name(g.seg[k1] & 0x7FFFFFFF)
        s0, e0 = bounds[i - 1]
        s1, e1 = bounds[i]
        b0 = bearing(pts[e0 - 1], pts[e0]) if e0 > s0 else 0.0
        b1 = bearing(pts[s1], pts[s1 + 1]) if e1 > s1 else 0.0
        delta = (b1 - b0 + 540) % 360 - 180
        node = g.to[k0]
        degree = g.off[node + 1] - g.off[node]
        significant = (abs(delta) > 25 and degree > 2) or (nm != cur_name and nm)
        if significant:
            out[-1][2] = run
            out.append([turn_word(delta) + (f" onto {nm}" if nm else ""),
                        nm, 0.0, pts[s1], s1, cum[s1]])
            cur_name = nm
            run = 0.0
        run += g.len[k1] / 10.0
    out[-1][2] = run
    out.append(["arrive at your destination", cur_name, 0.0,
                pts[-1], len(pts) - 1, cum[-1]])
    return [Maneuver(*m) for m in out]


# ------------------------------------------------------------------- Route

class Route:
    """The answer to one routing question - or the reason there isn't one.

    `source` is the load-bearing field: "offline" means this came out of the
    graph on the device, "server" means it came from Valhalla over the
    network. Callers show it. It is never inferred from what would be nicer.
    """

    __slots__ = ("ok", "source", "source_detail", "reason", "points",
                 "maneuvers", "distance_m", "duration_s", "cum_m",
                 "snap_from_m", "snap_to_m", "elapsed_ms")

    def __init__(self, *, ok=False, source="offline", source_detail="",
                 reason="", points=None, maneuvers=None, distance_m=0.0,
                 duration_s=0.0, cum_m=None, snap_from_m=None,
                 snap_to_m=None, elapsed_ms=0.0):
        self.ok = bool(ok)
        self.source = source
        self.source_detail = source_detail or source
        self.reason = reason
        self.points = points or []
        self.maneuvers = maneuvers or []
        self.distance_m = float(distance_m)
        self.duration_s = float(duration_s)
        self.cum_m = cum_m if cum_m is not None else []
        self.snap_from_m = snap_from_m
        self.snap_to_m = snap_to_m
        self.elapsed_ms = float(elapsed_ms)

    @property
    def offline(self):
        return self.source == "offline"

    def summary(self, units="imperial"):
        if not self.ok:
            return self.reason or "no route"
        return (f"{fmt_distance(self.distance_m, units)} · "
                f"{fmt_duration(self.duration_s)} · {self.source_detail}")

    def __repr__(self):                               # pragma: no cover
        if not self.ok:
            return f"<Route failed via {self.source}: {self.reason}>"
        return (f"<Route {self.distance_m / 1000:.1f} km "
                f"{len(self.maneuvers)} turns via {self.source}>")


def _failed(source, reason, **kw):
    return Route(ok=False, source=source, reason=reason, **kw)


# ------------------------------------------------------------- formatting

def fmt_distance(metres, units="imperial"):
    """A distance a rider can act on: short ones exact, long ones rounded."""
    try:
        metres = float(metres)
    except (TypeError, ValueError):
        return "—"
    if metres < 0:
        metres = 0.0
    if units == "metric":
        if metres < 950:
            return f"{int(round(metres / 10.0) * 10)} m"
        return f"{metres / 1000.0:.1f} km"
    feet = metres * 3.280839895
    if feet < 1000:
        return f"{int(round(feet / 10.0) * 10)} ft"
    return f"{metres / 1609.344:.1f} mi"


def fmt_duration(seconds):
    """'39 min', '1 h 12 min'. Never '0 min' for a trip that takes time."""
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "—"
    minutes = int(round(seconds / 60.0))
    if seconds > 0 and minutes == 0:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_eta(seconds, now=None):
    """Clock time of arrival, in the device's local time."""
    try:
        seconds = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "—"
    return time.strftime("%H:%M", time.localtime((now or time.time()) + seconds))


# ------------------------------------------------------------ live guidance

def progress(route, position):
    """Where along `route` a GPS fix is.

    Returns (index, along_m, off_route_m): the nearest polyline point, the
    distance from the start to it, and how far the fix is from the line.
    `off_route_m` is the honest one - it is what tells you the rider has
    missed a turn, and no amount of snapping should hide it.
    """
    pts = route.points
    if not pts:
        return None, 0.0, None
    best_i, best_d = 0, float("inf")
    for i, q in enumerate(pts):
        # Cheap equirectangular squared distance for the scan; the winner
        # gets a real haversine. A route is a few hundred points, so this
        # runs in well under a millisecond per fix.
        dlat = q[0] - position[0]
        dlon = (q[1] - position[1]) * math.cos(math.radians(position[0]))
        d = dlat * dlat + dlon * dlon
        if d < best_d:
            best_d, best_i = d, i
    cum = route.cum_m
    along = cum[best_i] if best_i < len(cum) else 0.0
    return best_i, along, haversine_m(position, pts[best_i])


def next_maneuver(route, position):
    """(maneuver, metres_to_it, off_route_m) for a live GPS fix.

    The "head ..." instruction is never the *next* one - it describes what
    you are already doing - so guidance starts at the first real turn and
    ends on "arrive at your destination".
    """
    if not route.ok or len(route.maneuvers) < 2:
        return None, None, None
    _, along, off = progress(route, position)
    for m in route.maneuvers[1:]:
        if m.cum_m >= along:
            return m, max(0.0, m.cum_m - along), off
    last = route.maneuvers[-1]
    return last, max(0.0, last.cum_m - along), off


def remaining(route, position):
    """(metres, seconds) still to go, scaled from the route's own average."""
    if not route.ok or not route.cum_m:
        return None, None
    _, along, _ = progress(route, position)
    left = max(0.0, route.distance_m - along)
    if route.distance_m <= 0:
        return left, 0.0
    return left, route.duration_s * (left / route.distance_m)


# ----------------------------------------------------------- offline route

def _route_offline(g, frm, to):
    t0 = time.perf_counter()
    with _SEARCH_LOCK:
        src, ds = g.snap(*frm)
        dst, dd = g.snap(*to)
        if src < 0 or dst < 0:
            which = "start" if src < 0 else "destination"
            return _failed("offline", f"the {which} is outside the area the "
                                      f"offline map covers")
        if ds > MAX_SNAP_M or dd > MAX_SNAP_M:
            far, dist = ("start", ds) if ds >= dd else ("destination", dd)
            return _failed("offline",
                           f"the {far} is {dist / 1000:.1f} km from the nearest "
                           f"road in the offline map - it is outside the area "
                           f"this graph covers",
                           snap_from_m=ds, snap_to_m=dd)
        if src == dst:
            return _failed("offline",
                           "start and destination snap to the same junction",
                           snap_from_m=ds, snap_to_m=dd)
        cost, state, _settled = g.astar_fast(src, dst, "time")
        if cost is None:
            return _failed("offline",
                           "no road route between those two points in the "
                           "offline map", snap_from_m=ds, snap_to_m=dd)
        edges = edge_path(g, state[0], src, dst)
    if not edges:
        return _failed("offline", "the offline router produced an empty path",
                       snap_from_m=ds, snap_to_m=dd)
    pts, bounds = _polyline_bounds(g, edges)
    if len(pts) < 2:
        return _failed("offline", "the offline route has no drawable shape",
                       snap_from_m=ds, snap_to_m=dd)
    cum = cumulative_m(pts, [g.len[k] / 10.0 for k in edges], bounds)
    turns = _maneuvers_from_graph(g, edges, pts, bounds, cum)
    return Route(ok=True, source="offline",
                 source_detail=f"offline · {Path(g.path).name}",
                 reason="", points=pts, maneuvers=turns,
                 distance_m=total_length_m(g, edges),
                 duration_s=total_time_s(g, edges), cum_m=cum,
                 snap_from_m=ds, snap_to_m=dd,
                 elapsed_ms=(time.perf_counter() - t0) * 1000)


# ------------------------------------------------------------ server route

def decode_polyline6(encoded):
    """Decode Valhalla's shape: Google polyline, precision 6."""
    if not encoded or not isinstance(encoded, str):
        return []
    coords = []
    index = 0
    lat = lon = 0
    n = len(encoded)
    while index < n:
        for target in (0, 1):
            shift = result = 0
            while True:
                if index >= n:
                    return coords
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if target == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat * 1e-6, lon * 1e-6))
    return coords


def parse_valhalla(data):
    """Turn a Valhalla /route response into a Route. Pure; no network.

    Kept separate from the request so the fallback path is testable without
    a server, which is the whole point of having a fallback.
    """
    if not isinstance(data, dict):
        return _failed("server", "the server sent something that is not JSON")
    if data.get("error"):
        return _failed("server", f"the server said: {data['error']}")
    trip = data.get("trip")
    if not isinstance(trip, dict) or not trip.get("legs"):
        return _failed("server", "the server found no route")
    pts = []
    turns = []
    for leg in trip["legs"]:
        if not isinstance(leg, dict):
            continue
        shape = decode_polyline6(leg.get("shape"))
        base = len(pts)
        if pts and shape and pts[-1] == shape[0]:
            shape = shape[1:]
            base -= 1
        pts.extend(shape)
        for m in (leg.get("maneuvers") or []):
            if not isinstance(m, dict):
                continue
            idx = min(base + int(m.get("begin_shape_index") or 0),
                      max(len(pts) - 1, 0))
            street = " / ".join(m.get("street_names") or [])
            turns.append([m.get("instruction") or "continue", street,
                          float(m.get("length") or 0.0) * 1000.0, idx])
    if len(pts) < 2:
        return _failed("server", "the server's route has no drawable shape")
    cum = cumulative_m(pts)
    summary = trip.get("summary") or {}
    distance = float(summary.get("length") or 0.0) * 1000.0
    duration = float(summary.get("time") or 0.0)
    if distance <= 0:
        distance = cum[-1]
    built = [Maneuver(instruction=i, street=s, distance_m=d, at=pts[k],
                      index=k, cum_m=cum[k] if k < len(cum) else cum[-1])
             for i, s, d, k in turns]
    return Route(ok=True, source="server", source_detail="server · Valhalla",
                 points=pts, maneuvers=built, distance_m=distance,
                 duration_s=duration, cum_m=cum)


def _valhalla_payload(frm, to, costing="motorcycle"):
    return {"costing": costing,
            "costing_options": {costing: {"use_hills": 1.0,
                                          "use_primary": 0.5,
                                          "use_trails": 0.5}},
            # kilometres so lengths come back in one unit and are converted
            # exactly once, here, rather than wherever they are displayed.
            "directions_options": {"units": "kilometers"},
            "locations": [{"lat": frm[0], "lon": frm[1]},
                          {"lat": to[0], "lon": to[1]}]}


def _post_json(path, payload, timeout):
    """POST and return (data, error_text). error_text is None on success.

    Not `api._post`: that answers None for every kind of failure, and the
    difference between "no server" and "Valhalla is down" is the difference
    between turning wifi on and giving up.
    """
    import urllib.request
    from . import api as _api
    from . import config as _config
    req = urllib.request.Request(
        _config.SERVER_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _config._opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}"), None
    except Exception as exc:                          # noqa: BLE001
        return None, _api._describe_failure(exc)


def _route_server(frm, to, timeout=25.0):
    t0 = time.perf_counter()
    data, err = _post_json(SERVER_ROUTE_PATH, _valhalla_payload(frm, to), timeout)
    if err is not None:
        return _failed("server", f"could not reach the routing server: {err}")
    out = parse_valhalla(data)
    out.elapsed_ms = (time.perf_counter() - t0) * 1000
    return out


# -------------------------------------------------------------- public API

def _latlon(value):
    """Accept (lat, lon), [lat, lon] or 'lat,lon'. Raises ValueError."""
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
        if len(parts) != 2:
            raise ValueError("expected 'lat,lon'")
        value = [parts[0].strip(), parts[1].strip()]
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError("expected 'lat,lon'") from None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError("latitude must be -90..90 and longitude -180..180")
    return lat, lon


def parse_latlon(text):
    """'37.8044, -122.2712' -> (37.8044, -122.2712), or None if it isn't."""
    try:
        return _latlon(text)
    except ValueError:
        return None


def route(frm, to, *, allow_server=True, timeout=25.0, graph_timeout=None):
    """Route from one (lat, lon) to another. Always returns a Route.

    Blocking: the search is ~150 ms for 40 km and the server fallback is a
    network round trip, so call this off the UI thread (or use
    `route_async`). Never raises for a routing failure - the failure is the
    Route, with the reason in it.
    """
    try:
        frm = _latlon(frm)
        to = _latlon(to)
    except ValueError as exc:
        return _failed("offline", str(exc))

    g = graph(timeout=graph_timeout)
    if g is not None:
        out = _route_offline(g, frm, to)
        if out.ok:
            return out
        why = out.reason
    else:
        why = status()["detail"]

    if not allow_server:
        return _failed("offline", why)

    out = _route_server(frm, to, timeout=timeout)
    if out.ok:
        # Say which one, always. A route silently produced by the server
        # while the rider believes the phone is self-sufficient is the one
        # failure mode that gets someone stranded.
        out.reason = f"offline routing unavailable ({why}) - routed by the server"
        return out
    return _failed("server", f"{why}; and the server could not help: {out.reason}")


async def route_async(frm, to, **kwargs):
    """`route()` on a worker thread. Await from a Flet event handler."""
    import asyncio
    return await asyncio.to_thread(route, frm, to, **kwargs)
