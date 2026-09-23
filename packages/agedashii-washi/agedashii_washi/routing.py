"""Turn-by-turn routing on the phone, with the radio off.

The same rule as the map tiles next door in `tileserver.py`: the thing you
need most on a motorcycle in a dead zone must not depend on the dead zone
having gone away. So the router is a pure-CPython A* over a packed road
graph that ships with the app - no C extension, no Android wheel, nothing
but `math`, `struct`, `heapq` and `array`.

WHERE THE GRAPH COMES FROM
    A `.rg` file is built off-device (`build_graph.py`, on the Pi) out of an
    OSM extract: nodes, a CSR adjacency, per-class lengths, segment
    geometry, street names, two uniform snapping grids and a turn-restriction
    trie, all in one blob that loads by `array.fromfile` rather than being
    parsed. The Bay Area is 86 MB / 725,626 nodes and loads in ~72 ms; a
    40 km route takes ~180 ms on the Pi this was measured on.

    Nothing here builds a graph. If there is no `.rg` installed that is a
    normal state, not an error - `route()` falls back to the server's
    Valhalla and *says* that is what it did.

    Two format versions exist and both load. RGRAPH01 has only the node
    grid and no turn restrictions; RGRAPH02 adds the segment grid that makes
    `snap_edge` possible and the trie that makes banned turns unroutable. On
    an RGRAPH01 file the router still works - it just snaps to junctions and
    does not know about turn restrictions, which is what it did before.

WHERE A ROUTE STARTS
    Not at the nearest junction. `snap_edge` projects the fix onto the
    nearest *edge* and the route begins at that point on that road, with a
    part-edge leg stitched on either end. That is not a cosmetic difference:
    re-routing after a missed turn happens mid-block by definition, and a
    junction snap can put the first "head <compass>" instruction 180 degrees
    out and the first turn on the wrong side of the rider.

TURN RESTRICTIONS
    `type=restriction` relations become sequences of directed edges that are
    illegal to traverse in order, packed as a trie. The search state is
    therefore a node id *or* a trie state - one integer either way - so
    honouring restrictions costs one array lookup per expansion at nodes
    that have none, which is nearly all of them.

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
    86 MB read that most launches never use. `route()` blocks while it
    searches, so callers run it off the UI thread - `route_async()` is the
    awaitable wrapper that does that for you.

    `Graph.search` reuses per-graph scratch buffers (that is what makes
    it fast), so two searches on one Graph would corrupt each other. Every
    search therefore takes `_SEARCH_LOCK`. Routes are ~180 ms and the user
    asks for one at a time; serialising them is free and the alternative is
    a wrong route.

WHAT IS STILL WRONG, AND KNOWN TO BE
    Snapping has no heading to go on, so on a divided highway a fix that
    lands nearer the other carriageway starts the route going the wrong
    way until the next exit. At realistic GPS error (5-8 m) this is rarer
    than the junction snap it replaces; at 25 m it is about as common.
    Turn restrictions with two or more via ways are dropped by the builder
    rather than guessed at, and so are conditional and other-vehicle ones -
    see the list in build_graph.py.

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

#: Env var that overrides where the graph is looked for. Mirrors
#: AGEDASHII_MBTILES.
ENV_OVERRIDE = "AGEDASHII_ROUTE_GRAPH"
#: What it was called before the product was renamed, still honoured.
LEGACY_ENV_OVERRIDE = "NVII_ROUTE_GRAPH"
#: The shipped region. Same naming as bayarea.mbtiles, deliberately.
GRAPH_FILENAME = "bayarea.rg"
#: Server endpoint that proxies to self-hosted Valhalla (dashbored-v3.py).
SERVER_ROUTE_PATH = "/api/valhalla/route"
#: Beyond this, a snap means "you are not in the mapped area", not "close
#: enough". 2 km is further than any legitimate gap between a GPS fix and a
#: drivable road, and well inside the distance to the edge of an extract.
MAX_SNAP_M = 2000.0
#: How far past the nearest road a heading-aware snap keeps looking, and how
#: far a candidate may be behind the leader and still be considered. Wide
#: enough for a divided highway (18-30 m between carriageways on I-880 and
#: I-80 in the shipped graph), narrow enough that a road on the next block
#: is never in the running.
HEADING_BAND_M = 45.0
#: What being completely reversed costs, in metres of equivalent distance.
#: Larger than any carriageway gap, smaller than the distance to a different
#: road - so a heading re-orders a tie and never overrides a clear winner.
HEADING_PENALTY_M = 60.0

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
# Turning straight back the way you came is legal on most roads and costs
# real time: slow, wait for a gap, turn. It is priced rather than banned
# because at a dead end it is the only move there is. Pricing it is also
# what makes a banned turn mean anything - with a free U-turn the router
# will happily drive past the junction, turn round and come back, which is
# a legal route through an illegal turn and not what the sign meant.
# 40 s is about 330 m of city driving: enough that a U-turn has to earn it.
U_TURN_PENALTY_S = 40.0

EARTH_R = 6371008.8

_EMPTY = {}

Snap = namedtuple("Snap", "lat lon dist_m seg i t frac len_m u v fwd bwd")
Snap.__doc__ = """Where a lat/lon actually lands on the road network.

lat, lon   the projected point - on the road, not the junction
dist_m     how far the asked-for point was from it. The honest number: it
           is what says "you are not on a mapped road", and MAX_SNAP_M
           judges it.
seg        segment id, or -1 for a fallback snap straight to a junction
i, t       the projection sits t of the way along the segment's i'th
           shape-point pair - enough to cut the drawn line at the point
frac       and this far along the whole segment, by length, 0..1
len_m      the segment's length, the graph's own figure
u, v       the junctions the segment runs from and to
fwd, bwd   the directed edge u->v and the directed edge v->u, or -1 where
           the road is one-way and that direction does not exist
"""


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
    override = os.environ.get(ENV_OVERRIDE) or os.environ.get(LEGACY_ENV_OVERRIDE)
    if override:
        candidates.append(Path(override))
    # APP_ROOT is a module global so a test can point the bundled-asset
    # search somewhere harmless. Once the real 86 MB graph shipped in
    # assets/, every "nothing is installed" test started finding it.
    candidates += [APP_ROOT / "assets" / "maps" / GRAPH_FILENAME,
                   APP_ROOT / "maps" / GRAPH_FILENAME,
                   Path.home() / ".agedashii" / GRAPH_FILENAME,
                   # The dotdir's pre-rename name, where an older install
                   # will have put a hand-downloaded graph.
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
    "archive, or set AGEDASHII_ROUTE_GRAPH")


def start_loading(path=None):
    """Begin loading the graph on a background thread. Returns the phase.

    Idempotent and cheap: call it from `build()` on every page open. The
    load happens once per process; later calls return the phase of the load
    already in flight or already finished.

    Phases: "absent" (nothing installed), "loading", "ready", "failed".

    A failure is sticky. Re-reading an 86 MB file that has already failed to
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
                                  name="agedashii-route-graph", daemon=True)
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
    than parsing - which is the entire reason 86 MB opens in 72 ms.
    """

    __slots__ = ("n", "e", "s", "p", "xy", "off", "to", "len", "cls", "seg",
                 "seg_off", "seg_name", "geom", "str_off", "blob",
                 "grid_off", "grid_nod", "gw", "gh", "minx", "miny", "cell",
                 "olat", "olon", "mx", "my", "path", "version",
                 "sgrid_off", "sgrid_seg", "tr_off", "tr_edge", "tr_next",
                 "tr_head", "tr_gate", "ntrie", "ns", "_root", "_nogate",
                 "_dist", "_par", "_prev", "_stamp", "_tgt", "_gen")

    MAGIC = b"RGRAPH01"
    MAGIC2 = b"RGRAPH02"
    #: tr_next sentinel: taking this edge from this trie state is illegal.
    BANNED = 0xFFFFFFFF

    def __init__(self, path):
        self.path = str(path)
        with open(path, "rb") as f:
            magic = f.read(8)
            if magic == self.MAGIC2:
                self.version = 2
            elif magic == self.MAGIC:
                self.version = 1
            else:
                raise ValueError("not an RGRAPH01 or RGRAPH02 graph file")
            n, e, s, p, nblob = struct.unpack("<5I", f.read(20))
            nstr, gw, gh = struct.unpack("<3I", f.read(12))
            if self.version >= 2:
                ntrie, ntrans, nsgrid = struct.unpack("<3I", f.read(12))
            else:
                ntrie = ntrans = nsgrid = 0
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
            if self.version >= 2:
                self.sgrid_off = rd("I", gw * gh + 1)
                self.sgrid_seg = rd("I", nsgrid)
                self.tr_off = rd("I", ntrie + 1)
                self.tr_edge = rd("I", ntrans)
                self.tr_next = rd("I", ntrans)
                self.tr_head = rd("I", ntrie)
                self.tr_gate = f.read(n)
                if len(self.tr_gate) != n:
                    raise ValueError("graph file is truncated")
            else:
                self.sgrid_off = self.sgrid_seg = None
                self.tr_off = self.tr_edge = self.tr_next = None
                self.tr_head = None
                self.tr_gate = bytes(n)
            self.blob = f.read(nblob)
            if len(self.blob) != nblob:
                raise ValueError("graph file is truncated")
        self.ntrie = ntrie
        # Search states: [0, n) is "at this node, no restriction in play";
        # [n, n+ntrie-1) is trie state t >= 1, which pins down both the node
        # and the part-matched restriction. One integer, either way.
        self.ns = n + (ntrie - 1 if ntrie else 0)
        self._root = None
        self._nogate = None

    # ---------------------------------------------------------- helpers
    def name(self, sid):
        i = self.seg_name[sid]
        return self.blob[self.str_off[i]:self.str_off[i + 1]].decode(
            "utf-8", "replace")

    def proj(self, lat, lon):
        return (lon - self.olon) * self.mx, (lat - self.olat) * self.my

    def root_map(self):
        """{directed edge -> trie state} for the edges that open a restriction.

        Built once, on the first search rather than at load, because most
        app launches open the map and never ask for a route, and a dict of
        twenty thousand entries is real milliseconds.
        """
        r = self._root
        if r is None:
            r = {}
            if self.ntrie:
                tr_edge, tr_next = self.tr_edge, self.tr_next
                for c in range(self.tr_off[0], self.tr_off[1]):
                    r[tr_edge[c]] = tr_next[c]
            self._root = r
        return r

    def entry_state(self, k):
        """The search state you are in having just traversed directed edge k."""
        nxt = self.root_map().get(k)
        return self.to[k] if nxt is None else self.n + nxt - 1

    def state_node(self, state):
        """The graph node a search state sits on."""
        return state if state < self.n else self.tr_head[state - self.n + 1]

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

    def node_at(self, lat, lon):
        """The node index sitting exactly on this coordinate, or -1.

        Used to tie a segment's first and last shape point back to the two
        junctions they are, which is what saves the file a 7.6 MB
        segment-to-edge table: the endpoints *are* the index.
        """
        x, y = self.proj(lat, lon)
        gw, gh, cell = self.gw, self.gh, self.cell
        cx = int((x - self.minx) / cell)
        cy = int((y - self.miny) / cell)
        xy, goff, gnod = self.xy, self.grid_off, self.grid_nod
        best, bestd = -1, 1.0                      # 1 m; f32 error is ~2 cm
        for j in range(max(0, cy - 1), min(gh, cy + 2)):
            for i in range(max(0, cx - 1), min(gw, cx + 2)):
                c = j * gw + i
                for k in range(goff[c], goff[c + 1]):
                    nd = gnod[k]
                    d = abs(xy[2 * nd] - x) + abs(xy[2 * nd + 1] - y)
                    if d < bestd:
                        bestd, best = d, nd
        return best

    def snap_edge(self, lat, lon, max_m=MAX_SNAP_M, heading=None):
        """Nearest point on a *road*, not nearest junction. A Snap, or None.

        The segment grid buckets every segment into every cell its geometry
        crosses, so expanding rings until the ring distance exceeds the best
        hit so far is exact - unlike growing a junction search, which can
        never prove it has not missed a long edge running past the door.

        `heading`, when given, is the direction of travel in compass degrees
        and breaks the one tie distance cannot. On a divided highway the two
        carriageways are separate one-way segments 18-30 m apart (measured
        on I-880 and I-80 in the shipped graph), so a fix landing between
        them picks whichever is nearer by centimetres - and half the time
        that is the one going the other way, which starts the route in the
        wrong direction until the next exit.

        Costed in metres so it stays comparable with distance: being fully
        reversed is worth HEADING_PENALTY_M, which clears a carriageway gap
        and does not clear a genuinely nearer road. A two-way segment can be
        driven either way, so it takes the smaller of the two errors and is
        effectively never penalised - ordinary streets behave as before.

        With `heading=None` this is the original function, unchanged, so
        every existing caller keeps its behaviour.

        Returns None on an RGRAPH01 graph (no segment grid), on an empty
        graph, or when nothing lies within `max_m`. Callers fall back to
        `snap()` and say which one they used.
        """
        if self.sgrid_off is None or not self.s:
            return None
        x, y = self.proj(lat, lon)
        cell, gw, gh = self.cell, self.gw, self.gh
        cx = int((x - self.minx) / cell)
        cy = int((y - self.miny) / cell)
        goff, gseg, geom, so = (self.sgrid_off, self.sgrid_seg,
                                self.geom, self.seg_off)
        mx, my, olat, olon = self.mx, self.my, self.olat, self.olon
        seen = set()
        add = seen.add
        best = None
        bestd = 1e30
        # Only collected when a heading was given: on the hot path this dict
        # would be one more allocation and one more write per segment tested,
        # for nothing.
        cands = {} if heading is not None else None
        r = 0
        rmax = int(max_m / cell) + 2
        while r <= rmax:
            for j in range(max(0, cy - r), min(gh, cy + r + 1)):
                if r and cy - r < j < cy + r:
                    cols = (cx - r, cx + r)
                else:
                    cols = range(max(0, cx - r), min(gw, cx + r + 1))
                for i in cols:
                    if i < 0 or i >= gw:
                        continue
                    c = j * gw + i
                    for q in range(goff[c], goff[c + 1]):
                        sid = gseg[q]
                        if sid in seen:
                            continue
                        add(sid)
                        a, b = so[sid], so[sid + 1]
                        px = (geom[2 * a + 1] / 1e7 - olon) * mx
                        py = (geom[2 * a] / 1e7 - olat) * my
                        for i2 in range(a, b - 1):
                            qx = (geom[2 * i2 + 3] / 1e7 - olon) * mx
                            qy = (geom[2 * i2 + 2] / 1e7 - olat) * my
                            ex, ey = qx - px, qy - py
                            l2 = ex * ex + ey * ey
                            if l2 <= 0.0:
                                t = 0.0
                            else:
                                t = ((x - px) * ex + (y - py) * ey) / l2
                                if t < 0.0:
                                    t = 0.0
                                elif t > 1.0:
                                    t = 1.0
                            dx = px + t * ex - x
                            dy = py + t * ey - y
                            d = dx * dx + dy * dy
                            if d < bestd:
                                bestd = d
                                best = (sid, i2 - a, t)
                            if cands is not None:
                                prev = cands.get(sid)
                                if prev is None or d < prev[0]:
                                    cands[sid] = (d, i2 - a, t)
                            px, py = qx, qy
            # With a heading, keep going a little past the nearest road: the
            # other carriageway is the whole point and it is 18-30 m further
            # out, so stopping the moment the first one is proven closest
            # would never see it.
            reach = math.sqrt(bestd) + (HEADING_BAND_M if cands is not None else 0.0)
            if best is not None and r * cell > reach:
                break
            r += 1
        if best is None:
            return None
        if cands:
            best = self._pick_by_heading(cands, bestd, heading) or best
            bestd = cands[best[0]][0]
        return self._snap_detail(best, math.sqrt(bestd))

    def _pick_by_heading(self, cands, bestd, heading):
        """Choose among near-tie candidates using the direction of travel.

        Returns a (sid, i, t) triple, or None to leave the choice alone.
        Only candidates within HEADING_BAND_M of the nearest are considered,
        so this re-orders a tie and never reaches past a road that is simply
        closer by a clear margin.
        """
        limit = math.sqrt(bestd) + HEADING_BAND_M
        best_key = None
        best_cost = None
        for sid, (d2, i, t) in cands.items():
            dist = math.sqrt(d2)
            if dist > limit:
                continue
            snap = self._snap_detail((sid, i, t), dist)
            if snap is None:
                continue
            a = self.seg_off[sid]
            run = bearing(
                (self.geom[2 * (a + i)] / 1e7, self.geom[2 * (a + i) + 1] / 1e7),
                (self.geom[2 * (a + i + 1)] / 1e7,
                 self.geom[2 * (a + i + 1) + 1] / 1e7))
            # Which ways this segment may actually be driven. fwd/bwd are
            # -1 when that direction does not exist, which is exactly what
            # makes a one-way carriageway distinguishable from a street.
            ways = []
            if snap.fwd >= 0:
                ways.append(run)
            if snap.bwd >= 0:
                ways.append((run + 180.0) % 360.0)
            if not ways:
                continue
            err = min(abs((heading - w + 180) % 360 - 180) for w in ways)
            cost = dist + (err / 180.0) * HEADING_PENALTY_M
            if best_cost is None or cost < best_cost:
                best_cost, best_key = cost, (sid, i, t)
        return best_key

    def _snap_detail(self, best, dist_m):
        """Turn (segment, point index, parameter) into a full Snap."""
        sid, i, t = best
        geom, so = self.geom, self.seg_off
        a, b = so[sid], so[sid + 1]
        lat0 = geom[2 * (a + i)] / 1e7
        lon0 = geom[2 * (a + i) + 1] / 1e7
        lat1 = geom[2 * (a + i + 1)] / 1e7
        lon1 = geom[2 * (a + i + 1) + 1] / 1e7
        plat = lat0 + (lat1 - lat0) * t
        plon = lon0 + (lon1 - lon0) * t
        # how far along the whole segment, by length, the projection sits
        run = 0.0
        upto = 0.0
        mx, my, olat, olon = self.mx, self.my, self.olat, self.olon
        px = (geom[2 * a + 1] / 1e7 - olon) * mx
        py = (geom[2 * a] / 1e7 - olat) * my
        for j in range(a, b - 1):
            qx = (geom[2 * j + 3] / 1e7 - olon) * mx
            qy = (geom[2 * j + 2] / 1e7 - olat) * my
            step = math.hypot(qx - px, qy - py)
            if j - a == i:
                upto = run + step * t
            run += step
            px, py = qx, qy
        frac = (upto / run) if run > 0 else 0.0
        # the two junctions the segment runs between, and the directed edges
        u = self.node_at(geom[2 * a] / 1e7, geom[2 * a + 1] / 1e7)
        v = self.node_at(geom[2 * (b - 1)] / 1e7, geom[2 * (b - 1) + 1] / 1e7)
        fwd = bwd = -1
        off, seg = self.off, self.seg
        if u >= 0:
            for k in range(off[u], off[u + 1]):
                if seg[k] == sid:
                    fwd = k
                    break
        if v >= 0:
            for k in range(off[v], off[v + 1]):
                if seg[k] == (sid | 0x80000000):
                    bwd = k
                    break
        if fwd < 0 and bwd < 0:
            return None
        length = self.len[fwd if fwd >= 0 else bwd] / 10.0
        return Snap(plat, plon, dist_m, sid, i, t, frac, length, u, v,
                    fwd, bwd)

    # ---------------------------------------- A* with reusable scratch
    def search(self, sources, targets, goal, metric="time", weight=1.0,
               restrictions=True):
        """A* from a set of starts to a set of finishes, honouring turns.

        `sources` is [(state, cost already spent getting there)], `targets`
        is {node: (cost still to spend after arriving, final edge or -1)},
        and `goal` is the (x, y) in projected metres that the heuristic aims
        at. Those three are what let a route begin and end in the middle of
        a block: the part-edge at each end is priced as a source cost and a
        finish cost rather than pretended away.

        Returns (total_cost, edges, source_state, settled). `edges` is the
        list of directed edge ids between the winning source and finish, and
        is [] for a finish on the source's own edge. `edges` is None when
        there is no route.

        Admissibility, and therefore the early exit: every edge costs at
        least its straight-line length over the fastest calibrated speed,
        and so does every finish cost, so once the cheapest thing left in
        the heap is no better than the best finish already found, nothing
        left can beat it.

        A* with the O(n) per-call scratch allocation hoisted: a fresh
        [inf]*n / [-1]*n per route costs ~3n writes, the one thing that
        scales with graph size rather than route length. The buffers are
        allocated once per Graph and invalidated with a generation counter,
        so the per-route reset is O(settled) instead of O(n).

        NOT reentrant: the scratch is shared. Callers hold _SEARCH_LOCK.
        """
        n, ns = self.n, self.ns
        try:
            dist = self._dist
        except AttributeError:
            self._dist = dist = [0.0] * ns
            self._par = array("i", bytes(4 * ns))
            self._prev = array("i", bytes(4 * ns))
            self._stamp = array("I", bytes(4 * ns))
            self._tgt = bytearray(n)
            self._gen = 0
        par, prev, stamp = self._par, self._prev, self._stamp
        tgt = self._tgt
        self._gen += 1
        if self._gen >= 0x7FFFFFFF:          # generation wrap, ~2e9 routes
            self._stamp = stamp = array("I", bytes(4 * ns))
            self._gen = 1
        gen = self._gen
        CLOSED = 0x80000000
        closed = gen | CLOSED
        BANNED = self.BANNED

        off, to, elen, ecls = self.off, self.to, self.len, self.cls
        xy = self.xy
        tr_off, tr_edge, tr_next, tr_head = (self.tr_off, self.tr_edge,
                                             self.tr_next, self.tr_head)
        if restrictions:
            gate = self.tr_gate
            root_get = self.root_map().get
        else:
            # Used only to answer "is this unroutable because of a banned
            # turn, or at all?" - see _route_offline. Allocated the first
            # time that question is asked, which on most graphs is never.
            if self._nogate is None:
                self._nogate = bytes(self.n)
            gate = self._nogate
            root_get = _EMPTY.get
        gx, gy = goal
        sqrt = math.sqrt
        push = heapq.heappush
        pop = heapq.heappop
        if metric == "time":
            spd = SEC_PER_DM
            inv_h = weight / MAXSPEED_MS
            pen = JUNCTION_PENALTY_S
            uturn = U_TURN_PENALTY_S
        else:
            spd = [0.1] * len(SEC_PER_DM)
            inv_h = weight
            pen = 0.0
            uturn = 0.0

        heap = []
        for state, cost0 in sources:
            if stamp[state] == gen and dist[state] <= cost0:
                continue
            dist[state] = cost0
            par[state] = -1
            prev[state] = -1
            stamp[state] = gen
            sn = state if state < n else tr_head[state - n + 1]
            dx = xy[2 * sn] - gx
            dy = xy[2 * sn + 1] - gy
            push(heap, (cost0 + sqrt(dx * dx + dy * dy) * inv_h, state))
        for node in targets:
            tgt[node] = 1

        best_cost = float("inf")
        best_state = -1
        settled = 0
        try:
            while heap:
                f, s = pop(heap)
                if f >= best_cost:
                    break
                if stamp[s] & CLOSED:
                    continue
                stamp[s] = closed
                settled += 1
                if s < n:
                    u = s
                    ca = cb = 0
                else:
                    t = s - n + 1
                    u = tr_head[t]
                    ca, cb = tr_off[t], tr_off[t + 1]
                du = dist[s]
                ps = prev[s]
                if ps < 0:
                    pnode = -1
                elif ps < n:
                    pnode = ps
                else:
                    pnode = tr_head[ps - n + 1]
                if tgt[u]:
                    finish, fedge = targets[u]
                    allowed = True
                    if fedge >= 0 and cb > ca:
                        for c in range(ca, cb):
                            if tr_edge[c] == fedge:
                                allowed = tr_next[c] != BANNED
                                break
                    if allowed and du + finish < best_cost:
                        best_cost = du + finish
                        best_state = s
                if cb == ca and not gate[u]:
                    # the ordinary case: nothing here is restricted
                    for k in range(off[u], off[u + 1]):
                        v = to[k]
                        sv = stamp[v]
                        if sv == closed:
                            continue
                        nd = du + elen[k] * spd[ecls[k]] + pen
                        if v == pnode:
                            nd += uturn
                        if sv != gen or nd < dist[v]:
                            dist[v] = nd
                            par[v] = k
                            prev[v] = s
                            stamp[v] = gen
                            dx = xy[2 * v] - gx
                            dy = xy[2 * v + 1] - gy
                            push(heap, (nd + sqrt(dx * dx + dy * dy) * inv_h, v))
                else:
                    for k in range(off[u], off[u + 1]):
                        vn = to[k]
                        nxt = -1
                        for c in range(ca, cb):
                            if tr_edge[c] == k:
                                nxt = tr_next[c]
                                break
                        if nxt < 0 and gate[u]:
                            nxt = root_get(k, -1)
                        if nxt == BANNED:
                            continue
                        v = vn if nxt < 0 else n + nxt - 1
                        sv = stamp[v]
                        if sv == closed:
                            continue
                        nd = du + elen[k] * spd[ecls[k]] + pen
                        if vn == pnode:
                            nd += uturn
                        if sv != gen or nd < dist[v]:
                            dist[v] = nd
                            par[v] = k
                            prev[v] = s
                            stamp[v] = gen
                            dx = xy[2 * vn] - gx
                            dy = xy[2 * vn + 1] - gy
                            push(heap, (nd + sqrt(dx * dx + dy * dy) * inv_h, v))
        finally:
            for node in targets:
                tgt[node] = 0

        if best_state < 0:
            return None, None, -1, settled
        edges = []
        v = best_state
        while par[v] >= 0:
            edges.append(par[v])
            v = prev[v]
            if len(edges) > self.e:          # cycle guard; should never fire
                return None, None, -1, settled
        edges.reverse()
        return best_cost, edges, v, settled

    def astar_fast(self, src, dst, metric="time", weight=1.0):
        """Plain node-to-node A*. (cost, edges, settled); cost None = no route.

        A thin wrapper over `search` kept because "route between these two
        junctions" is still the question a test or a bench wants to ask.
        """
        cost, edges, _src, settled = self.search(
            [(src, 0.0)], {dst: (0.0, -1)},
            (self.xy[2 * dst], self.xy[2 * dst + 1]), metric, weight)
        return cost, edges, settled


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
    """(points, bounds) where bounds[i] = (first_idx, last_idx) of edge i."""
    return _chain([_edge_points(g, k) for k in edges])


def _chain(chunks):
    """(points, bounds) where bounds[i] = (first_idx, last_idx) of chunk i.

    The spike's `polyline()` threw the per-edge boundaries away. Live
    guidance needs them: they are what ties a maneuver to a point on the
    drawn line, and therefore what lets "300 m to the next turn" mean the
    distance along the road rather than the distance as the crow flies.
    """
    pts = []
    bounds = []
    for chunk in chunks:
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


def _maneuvers_from_legs(g, legs, pts, bounds, cum):
    """Turn-by-turn list. The spike's rules, plus where each turn happens.

    A junction only earns an instruction if the geometry actually turns
    *and* there was a real alternative (degree > 2), or the street name
    changes. Without the degree test every kink in a road becomes a "bear
    left" and the list is unreadable.

    A leg is (points, length_m, class, street name, node it ends at). Most
    are whole graph edges; the first and last may be part of one, which is
    the point - the "head <compass>" now describes the direction the rider
    is really facing rather than the direction out of a junction they are
    nowhere near.
    """
    if not legs:
        return []
    out = []
    cur_name = legs[0][3]
    start_b = bearing(pts[0], pts[1]) if len(pts) > 1 else 0.0
    out.append([f"head {compass_word(start_b)}"
                + (f" on {cur_name}" if cur_name else ""),
                cur_name, 0.0, pts[0], 0, 0.0])
    run = legs[0][1]
    for i in range(1, len(legs)):
        nm = legs[i][3]
        s0, e0 = bounds[i - 1]
        s1, e1 = bounds[i]
        b0 = bearing(pts[e0 - 1], pts[e0]) if e0 > s0 else 0.0
        b1 = bearing(pts[s1], pts[s1 + 1]) if e1 > s1 else 0.0
        delta = (b1 - b0 + 540) % 360 - 180
        node = legs[i - 1][4]
        degree = (g.off[node + 1] - g.off[node]) if node >= 0 else 0
        significant = (abs(delta) > 25 and degree > 2) or (nm != cur_name and nm)
        if significant:
            out[-1][2] = run
            out.append([turn_word(delta) + (f" onto {nm}" if nm else ""),
                        nm, 0.0, pts[s1], s1, cum[s1]])
            cur_name = nm
            run = 0.0
        run += legs[i][1]
    out[-1][2] = run
    out.append(["arrive at your destination", cur_name, 0.0,
                pts[-1], len(pts) - 1, cum[-1]])
    return [Maneuver(*m) for m in out]


def _edge_leg(g, k):
    """One whole directed edge, as a leg."""
    return (_edge_points(g, k), g.len[k] / 10.0, g.cls[k],
            g.name(g.seg[k] & 0x7FFFFFFF), g.to[k])


def _same_point(a, b):
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def _part_leg(g, snap, k, to_end, head):
    """The slice of a snapped segment on one side of the projection.

    `to_end` True gives the run from the projection to the segment's last
    shape point, False the run from its first shape point to the
    projection; either is then reversed by the caller if it is travelled
    the other way. Returns None when the slice is too short to draw or to
    count, which happens whenever the fix lands on a junction.
    """
    pts = _seg_points(g, snap.seg)
    p = (snap.lat, snap.lon)
    if to_end:
        chunk = list(pts[snap.i + 1:])
        if chunk and _same_point(chunk[0], p):
            chunk[0] = p
        else:
            chunk.insert(0, p)
        length = (1.0 - snap.frac) * snap.len_m
    else:
        chunk = list(pts[:snap.i + 1])
        if chunk and _same_point(chunk[-1], p):
            chunk[-1] = p
        else:
            chunk.append(p)
        length = snap.frac * snap.len_m
    if len(chunk) < 2 or length < 0.05:
        return None
    return (chunk, length, g.cls[k], g.name(snap.seg), head)


def _seg_points(g, sid):
    a, b = g.seg_off[sid], g.seg_off[sid + 1]
    geom = g.geom
    return [(geom[2 * i] / 1e7, geom[2 * i + 1] / 1e7) for i in range(a, b)]


def _mid_leg(g, snap_a, snap_b, k, forward):
    """The slice of one segment between two projections onto it.

    Start and destination on the same block is the one case the search
    cannot answer: both ends are the same edge, so there is nothing to
    search. Travelling it directly is also always the cheapest way, since
    the only alternative is the whole way round.
    """
    pts = _seg_points(g, snap_a.seg)
    lo, hi = (snap_a, snap_b) if forward else (snap_b, snap_a)
    chunk = ([(lo.lat, lo.lon)]
             + pts[lo.i + 1:hi.i + 1]
             + [(hi.lat, hi.lon)])
    out = [chunk[0]]
    for q in chunk[1:]:
        if not _same_point(out[-1], q):
            out.append(q)
    if not forward:
        out.reverse()
    length = abs(snap_b.frac - snap_a.frac) * snap_a.len_m
    if len(out) < 2 or length < 0.05:
        return None
    return (out, length, g.cls[k], g.name(snap_a.seg), -1)


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

def _anchor(g, lat, lon, heading=None):
    """Snap a point, preferring an edge and falling back to a junction.

    `heading` is only ever passed for the START: it is where the rider is
    pointing now, and the destination has no direction of travel to speak
    of. Passing it to both would pick the destination's carriageway by the
    direction you happen to be facing at the other end of the trip.
    """
    s = g.snap_edge(lat, lon, heading=heading)
    if s is not None:
        return s
    node, d = g.snap(lat, lon)
    if node < 0:
        return None
    return Snap(g.olat + g.xy[2 * node + 1] / g.my,
                g.olon + g.xy[2 * node] / g.mx,
                d, -1, 0, 0.0, 0.0, 0.0, node, node, -1, -1)


def _at_junction(s):
    """The node this snap is really standing on, or -1 for mid-block.

    A projection that lands within a metre of a segment's end is at the
    junction, and treating it as "part-way along this one segment" would
    offer the rider only that segment's two directions out of a crossroads
    that has four. One metre is well inside GPS noise and inside the
    precision the geometry is stored at.
    """
    if s.seg < 0:
        return s.u
    if s.frac * s.len_m <= 1.0:
        return s.u
    if (1.0 - s.frac) * s.len_m <= 1.0:
        return s.v
    return -1


def _sources_for(g, s):
    """[(search state, cost already spent)] for starting at snap `s`.

    Both ways out of the block, where both are legal: the rider halfway
    down a two-way street may reach either end, and which one is cheaper
    depends on where they are going. On a one-way there is only one.
    """
    node = _at_junction(s)
    if node >= 0:
        return [(node, 0.0)], {}
    out = []
    legs = {}
    if s.fwd >= 0:
        state = g.entry_state(s.fwd)
        out.append((state, (1.0 - s.frac) * s.len_m * 10.0
                    * SEC_PER_DM[g.cls[s.fwd]]))
        legs[state] = (s.fwd, True)
    if s.bwd >= 0:
        state = g.entry_state(s.bwd)
        out.append((state, s.frac * s.len_m * 10.0 * SEC_PER_DM[g.cls[s.bwd]]))
        legs[state] = (s.bwd, False)
    return out, legs


def _targets_for(g, s):
    """{node: (cost still to spend, final edge)} for finishing at snap `s`."""
    node = _at_junction(s)
    if node >= 0:
        return {node: (0.0, -1)}, {}
    out = {}
    legs = {}
    if s.fwd >= 0:
        out[s.u] = (s.frac * s.len_m * 10.0 * SEC_PER_DM[g.cls[s.fwd]], s.fwd)
        legs[s.u] = (s.fwd, False)
    if s.bwd >= 0 and s.v not in out:
        out[s.v] = ((1.0 - s.frac) * s.len_m * 10.0
                    * SEC_PER_DM[g.cls[s.bwd]], s.bwd)
        legs[s.v] = (s.bwd, True)
    return out, legs


def _route_offline(g, frm, to, heading=None):
    t0 = time.perf_counter()
    with _SEARCH_LOCK:
        a = _anchor(g, *frm, heading=heading)
        b = _anchor(g, *to)
        if a is None or b is None:
            which = "start" if a is None else "destination"
            return _failed("offline", f"the {which} is outside the area the "
                                      f"offline map covers")
        ds, dd = a.dist_m, b.dist_m
        if ds > MAX_SNAP_M or dd > MAX_SNAP_M:
            far, dist = ("start", ds) if ds >= dd else ("destination", dd)
            return _failed("offline",
                           f"the {far} is {dist / 1000:.1f} km from the nearest "
                           f"road in the offline map - it is outside the area "
                           f"this graph covers",
                           snap_from_m=ds, snap_to_m=dd)
        if haversine_m((a.lat, a.lon), (b.lat, b.lon)) < 1.0:
            return _failed("offline",
                           "start and destination are the same place on the "
                           "same road", snap_from_m=ds, snap_to_m=dd)
        note = ""
        legs = _same_block(g, a, b)
        if legs is None:
            sources, head_legs = _sources_for(g, a)
            targets, tail_legs = _targets_for(g, b)
            cost, edges, src_state, _settled = g.search(
                sources, targets, g.proj(b.lat, b.lon), "time")
            if cost is None:
                # Turn restrictions can strand a pocket of road that the
                # graph's own connectivity check said was fine - a ramp you
                # may only leave one way, tagged so you may not. Refusing to
                # route there is the wrong answer on a motorcycle; so is
                # quietly routing through a banned turn. Do it, and say so.
                cost, edges, src_state, _settled = g.search(
                    sources, targets, g.proj(b.lat, b.lon), "time",
                    restrictions=False)
                if cost is None:
                    return _failed("offline",
                                   "no road route between those two points "
                                   "in the offline map",
                                   snap_from_m=ds, snap_to_m=dd)
                note = ("the only way there in the offline map goes through a "
                        "turn the map says is banned - check the signs")
            legs = []
            if src_state in head_legs:
                k, to_end = head_legs[src_state]
                leg = _part_leg(g, a, k, to_end, g.to[k])
                if leg is not None:
                    if not to_end:
                        leg = ([q for q in reversed(leg[0])],) + leg[1:]
                    legs.append(leg)
            legs += [_edge_leg(g, k) for k in edges]
            end_node = (g.to[edges[-1]] if edges
                        else g.state_node(src_state))
            if end_node in tail_legs:
                k, to_end = tail_legs[end_node]
                leg = _part_leg(g, b, k, to_end, -1)
                if leg is not None:
                    if to_end:
                        leg = ([q for q in reversed(leg[0])],) + leg[1:]
                    legs.append(leg)
    if not legs:
        return _failed("offline", "the offline router produced an empty path",
                       snap_from_m=ds, snap_to_m=dd)
    pts, bounds = _chain([leg[0] for leg in legs])
    if len(pts) < 2:
        return _failed("offline", "the offline route has no drawable shape",
                       snap_from_m=ds, snap_to_m=dd)
    cum = cumulative_m(pts, [leg[1] for leg in legs], bounds)
    turns = _maneuvers_from_legs(g, legs, pts, bounds, cum)
    return Route(ok=True, source="offline",
                 source_detail=f"offline · {Path(g.path).name}",
                 reason=note, points=pts, maneuvers=turns,
                 distance_m=sum(leg[1] for leg in legs),
                 duration_s=sum(leg[1] * 10.0 * SEC_PER_DM[leg[2]]
                                for leg in legs),
                 cum_m=cum, snap_from_m=ds, snap_to_m=dd,
                 elapsed_ms=(time.perf_counter() - t0) * 1000)


def _same_block(g, a, b):
    """The legs for a trip that never leaves one segment, or None."""
    if a.seg < 0 or a.seg != b.seg:
        return None
    if a.frac <= b.frac and a.fwd >= 0:
        leg = _mid_leg(g, a, b, a.fwd, True)
    elif a.frac >= b.frac and a.bwd >= 0:
        leg = _mid_leg(g, a, b, a.bwd, False)
    else:
        return None                       # one-way, the wrong way: go around
    return [leg] if leg is not None else None


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


def route(frm, to, *, allow_server=True, timeout=25.0, graph_timeout=None,
          heading=None):
    """Route from one (lat, lon) to another. Always returns a Route.

    Blocking: the search is ~150 ms for 40 km and the server fallback is a
    network round trip, so call this off the UI thread (or use
    `route_async`). Never raises for a routing failure - the failure is the
    Route, with the reason in it.

    `heading` is the rider's current direction of travel in compass degrees,
    if it is known. It only affects where the START snaps: between the two
    carriageways of a divided road, distance alone picks one at random and
    half the time that is the one going the other way. Omit it and nothing
    changes - the server path ignores it either way, since Valhalla is
    asked for a point and not a bearing here.
    """
    try:
        frm = _latlon(frm)
        to = _latlon(to)
    except ValueError as exc:
        return _failed("offline", str(exc))

    g = graph(timeout=graph_timeout)
    if g is not None:
        out = _route_offline(g, frm, to, heading=heading)
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
