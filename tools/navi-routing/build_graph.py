#!/usr/bin/env python3
"""Build a compact binary road graph from an osmium OPL stream.

THIS IS THE TOOL THAT BUILT assets/maps/bayarea.rg, and until 2026-09-28 it
existed nowhere but a subagent's /tmp scratchpad, since deleted. It was
recovered from that subagent's transcript (session e3664789, subagent
a9a8089930ced6ca4: one Write at 2026-09-21T00:00:18Z plus eight Edits, every
one applied cleanly) and PROVED by re-running the recipe below: the output is
byte-identical to the shipped graph.

REBUILD RECIPE. Input for both graphs below: Geofabrik california-latest.osm.pbf,
replication timestamp 2026-08-30T20:21:06Z (sequence 4896), 1,326,191,959 bytes.

    osmium extract -b BBOX -o region.osm.pbf california-latest.osm.pbf
    osmium tags-filter -o drive.osm.pbf region.osm.pbf \
        'w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,living_street,service,road' \
        'r/type=restriction'
    osmium cat -o drive.osm.opl drive.osm.pbf
    python3 build_graph.py drive.osm.opl bayarea.rg [--format 2]

  THE CURRENT GRAPH (RGRAPH03, the default): BBOX -122.65,37.20,-121.75,38.10,
  the map tiles' own box - her choice, 2026-09-28: routing stops where the map
  does, and a destination outside it is refused in words (routing.DEST_OUTSIDE).
    -> 41,433,230 bytes  md5 be63ddc26942702bf4d2b8001db5929e
       358,142 nodes, 801,552 edges, 14,152 turn restrictions. ~1 min, 0.9 GB.

  THE GRAPH SHIPPED UNTIL THEN (RGRAPH02): BBOX -123.63,36.85,-121.21,38.86 -
  the routing spike's, never chosen, six times the tiles' area - with
  --format 2. Reproduces it byte for byte:
    -> 86,090,847 bytes  md5 983501d7ea133796f4932fe299040ba5
       725,626 nodes, 1,661,666 edges, 21,806 turn restrictions. ~2 min, 1.8 GB.

A GRAPH "CLASS" IS A SPEED BUCKET, NOT A HIGHWAY TYPE. `adj_cls` is the index
of whichever SPEED_KMH entry below is nearest the way's maxspeed (or its
highway default when it has none) - see cls_for_speed in main(). So a 55 mph
freeway lands in "trunk" (90), a 35 mph street in "trunk_link" (55), an
unposted ramp in "motorway_link" (60), and slots 6, 8, 10 and 14 are never
used because they repeat 55, 45, 40 and 30 and min() takes the first index.
The CLASSES names are only the labels of those buckets. routing.SPEED_KMH is
fitted per bucket (tools/navi-routing/fit_speed_kmh.py), which is correct
for exactly this reason.

OFFLINE step. Runs on a build machine, never on the phone.
Input : .osm.opl (produced by `osmium cat -o x.osm.opl filtered.osm.pbf`)
Output: one packed .rg file -> loaded by routing.py in pure Python on device.

The input must carry `type=restriction` relations as well as the drivable
ways, or the turn restrictions are silently absent.  The filter is:

    osmium tags-filter -o drive.osm.pbf region.osm.pbf \
        w/highway=motorway,motorway_link,...,road  r/type=restriction
    osmium cat -o drive.osm.opl drive.osm.pbf

Layout of the .rg file (all little-endian, arrays are naturally aligned):

    magic  b"RGRAPH03"      (b"RGRAPH02" = no seg_hw/seg_flags;
                             b"RGRAPH01" = no segment grid, no restrictions)
    u32    n_nodes, n_edges, n_segs, n_pts, n_strbytes
    u32    n_str, gw, gh
    u32    n_trie, n_trans, n_sgrid            <- v2 only
    f64    origin_lat, origin_lon, mx (m per deg lon), my (m per deg lat),
           minx, miny, cell
    then, back to back:
      nodes_xy   f32 * 2*n_nodes   projected metres from origin
      adj_off    u32 * (n_nodes+1) CSR row pointers
      adj_to     u32 * n_edges     target node index
      adj_len    u32 * n_edges     length in decimetres
      adj_cls    u8  * n_edges     road class 0..14
      adj_seg    u32 * n_edges     segment id | 0x80000000 if traversed reversed
      seg_off    u32 * (n_segs+1)  row pointers into geom
      seg_name   u32 * n_segs      index into string table
      geom       i32 * 2*n_pts     lat*1e7, lon*1e7 shape points
      str_off    u32 * (n_str+1)
      grid_off   u32 * (gw*gh+1)   CSR row pointers for the node snap grid
      grid_nod   u32 * n_nodes     node ids bucketed by grid cell
      -- v2 only, in this order --
      sgrid_off  u32 * (gw*gh+1)   CSR row pointers for the SEGMENT snap grid
      sgrid_seg  u32 * n_sgrid     segment ids, bucketed by every cell their
                                   geometry passes through
      tr_off     u32 * (n_trie+1)  CSR over turn-restriction trie states
      tr_edge    u32 * n_trans     directed edge id of the transition
      tr_next    u32 * n_trans     child trie state, or 0xFFFFFFFF = BANNED
      tr_head    u32 * n_trie      head node of each trie state (state 0 = root,
                                   unused)
      tr_gate    u8  * n_nodes     1 if any out-edge of this node opens a
                                   restriction - the search's fast reject
      -- v3 only --
      seg_hw     u8  * n_segs      the way's REAL highway class (CLASSES
                                   index) - adj_cls is only a speed bucket
      seg_flags  u8  * n_segs      bit 0: junction=roundabout or circular
      str_blob   bytes

WHY A TRIE
    A turn restriction bans a *sequence* of directed edges: (from, to) for a
    via-node relation, (from, via.., to) for a via-way one.  A trie over
    those sequences turns "is this move banned?" into one array lookup, and
    - because every trie state ends at exactly one graph node - the search
    state stays a single integer: node id below n_nodes, trie state above.
"""
import sys, os, re, math, struct, time
from array import array

# ---------------------------------------------------------------- road classes
CLASSES = ["motorway", "motorway_link", "trunk", "trunk_link", "primary",
           "primary_link", "secondary", "secondary_link", "tertiary",
           "tertiary_link", "unclassified", "residential", "living_street",
           "service", "road"]
CLS = {name: i for i, name in enumerate(CLASSES)}
# motorcycle free-flow speeds, km/h -- used when the way has no maxspeed
SPEED_KMH = [105, 60, 90, 55, 65, 45, 55, 40, 45, 35, 40, 30, 12, 20, 30]

BANNED = 0xFFFFFFFF          # tr_next sentinel: this move is illegal

#: seg_flags bits (RGRAPH03)
SEG_ROUNDABOUT = 1           # junction=roundabout or circular

UNESCAPE = re.compile(r"%([0-9a-fA-F]+)%")


def unesc(s):
    if "%" not in s:
        return s
    return UNESCAPE.sub(lambda m: chr(int(m.group(1), 16)), s)


def parse_tags(blob):
    """OPL tag field: k=v,k=v ... with %hex% escapes (commas/= are escaped)."""
    t = {}
    if not blob:
        return t
    for kv in blob.split(","):
        i = kv.find("=")
        if i > 0:
            t[unesc(kv[:i])] = unesc(kv[i + 1:])
    return t


MAXSPEED = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|knots)?\s*$", re.I)


def parse_maxspeed(v):
    m = MAXSPEED.match(v or "")
    if not m:
        return None
    n = float(m.group(1))
    u = (m.group(2) or "").lower()
    if u == "mph":
        n *= 1.609344
    if n <= 0 or n > 200:
        return None
    return n


BAD_ACCESS = {"no", "private", "customers", "delivery", "agricultural",
              "forestry", "military", "emergency"}


def way_usable(t):
    """True if a motorcycle may legally use this way."""
    hw = t.get("highway")
    if hw not in CLS:
        return False
    if t.get("area") == "yes":
        return False
    # explicit motor-vehicle permission wins over a generic access restriction
    for k in ("motorcycle", "motorcar", "motor_vehicle"):
        v = t.get(k)
        if v in BAD_ACCESS:
            return False
        if v in ("yes", "designated", "permissive", "destination"):
            return True
    if t.get("access") in BAD_ACCESS:
        return False
    if t.get("service") in ("parking_aisle", "driveway", "drive-through"):
        return False
    return True


def way_oneway(t):
    """-> (forward_ok, backward_ok)"""
    ow = t.get("oneway")
    if ow in ("yes", "1", "true"):
        return True, False
    if ow in ("-1", "reverse"):
        return False, True
    if ow in ("no", "0", "false"):
        return True, True
    if t.get("junction") in ("roundabout", "circular"):
        return True, False
    if t.get("highway") in ("motorway", "motorway_link") and ow is None:
        return True, False
    return True, True


# --------------------------------------------------------- turn restrictions
#
# WHAT IS HONOURED, and what is not, is a routing-correctness statement, so it
# lives next to the code that decides it rather than only in a report.
#
#   honoured   no_left_turn  no_right_turn  no_straight_on  no_u_turn
#              no_entry  no_exit
#              only_left_turn  only_right_turn  only_straight_on
#              only_u_turn
#              ... with a via *node*, or a single via *way*.
#
#   ignored    no_*_turn_on_red      - not an absolute ban; the turn is legal
#                                      on green, and treating it as a ban
#                                      would invent detours that do not exist
#              restriction:conditional, day_on/hour_on   - no clock here
#              restriction:hgv / :psv / :bicycle / :motorcar
#                                    - other vehicle classes; a motorcycle is
#                                      not a motorcar
#              relations with `except` covering motorcycles
#              two or more via ways   - the chain is ambiguous to order from
#                                      members alone; counted and reported
#
RESTRICT_KEYS = ("restriction", "restriction:motor_vehicle",
                 "restriction:motorcycle")
# target turn angle in degrees, used to pick between candidate edges when a
# way touches the via node twice (positive = right)
TURN_ANGLE = {"left_turn": -90.0, "right_turn": 90.0,
              "straight_on": 0.0, "u_turn": 180.0}
#: how far the geometry may be off the direction the relation claims before
#: the resolution is treated as wrong rather than merely approximate
TURN_TOL = 75.0
NO_VALUES = {"no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn",
             "no_entry", "no_exit"}
ONLY_VALUES = {"only_left_turn", "only_right_turn", "only_straight_on",
               "only_u_turn"}
EXCEPT_US = {"motorcycle", "motor_vehicle", "vehicle"}


def pass0(path):
    """Relations: the turn restrictions, and which ways/nodes they need.

    Returns (restrictions, way_ids, node_ids, skipped) where each restriction
    is (value, from_ways, via_nodes, via_ways, to_ways).
    """
    out = []
    ways = set()
    nodes = set()
    skipped = {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line[0] != "r":
                continue
            ti = line.find(" T")
            mi = line.find(" M", ti if ti > 0 else 0)
            if mi < 0:
                continue
            t = parse_tags(line[ti + 2:mi]) if ti > 0 else {}
            if t.get("type") != "restriction":
                continue
            value = ""
            for k in RESTRICT_KEYS:
                if t.get(k):
                    value = t[k]
                    break
            if not value:
                skip("no plain restriction= tag (conditional or other vehicle)")
                continue
            if value not in NO_VALUES and value not in ONLY_VALUES:
                skip(f"unsupported restriction value {value}")
                continue
            if any(e.strip() in EXCEPT_US
                   for e in (t.get("except") or "").split(";")):
                skip("except= exempts motorcycles")
                continue
            frm, vian, viaw, to = [], [], [], []
            for m in line[mi + 2:].strip().split(","):
                if not m:
                    continue
                kind = m[0]
                ref, _, role = m[1:].partition("@")
                try:
                    ref = int(ref)
                except ValueError:
                    continue
                if role == "from" and kind == "w":
                    frm.append(ref)
                elif role == "to" and kind == "w":
                    to.append(ref)
                elif role == "via" and kind == "n":
                    vian.append(ref)
                elif role == "via" and kind == "w":
                    viaw.append(ref)
            if not frm or not to:
                skip("relation is missing a from or to way")
                continue
            if viaw and vian:
                skip("relation mixes via node and via way")
                continue
            if len(viaw) > 1:
                skip("two or more via ways")
                continue
            if not viaw and len(vian) != 1:
                skip("relation has no single via node")
                continue
            out.append((value, frm, vian, viaw, to))
            ways.update(frm)
            ways.update(to)
            ways.update(viaw)
            nodes.update(vian)
    return out, ways, nodes, skipped


# ---------------------------------------------------------------------- pass 1
def pass1(path, restr_ways):
    """Collect kept way node-id lists + tags, and count node references."""
    ways = []           # (refs, cls, name, fwd, bwd, speed_kmh, way_id_or_0)
    refcount = {}
    need = set()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line[0] != "w":
                continue
            ti = line.find(" T")
            ni = line.find(" N", ti if ti > 0 else 0)
            if ni < 0:
                continue
            tagblob = line[ti + 2:ni] if ti > 0 else ""
            t = parse_tags(tagblob)
            if not way_usable(t):
                continue
            refs = [int(x[1:]) for x in line[ni + 2:].rstrip().split(",") if len(x) > 1]
            if len(refs) < 2:
                continue
            wid = int(line[1:line.index(" ", 1)])
            c = CLS[t["highway"]]
            sp = parse_maxspeed(t.get("maxspeed")) or SPEED_KMH[c]
            fwd, bwd = way_oneway(t)
            flags = SEG_ROUNDABOUT if t.get("junction") in ("roundabout", "circular") else 0
            ways.append((refs, c, t.get("name") or t.get("ref") or "",
                         fwd, bwd, sp, wid if wid in restr_ways else 0, flags))
            for r in refs:
                refcount[r] = refcount.get(r, 0) + 1
            need.add(refs[0]); need.add(refs[-1])
    return ways, refcount, need


def pass2(path, wanted):
    """Read coordinates for the node ids we actually need.

    OPL node lines end ` x<lon> y<lat>`, so we slice from the right and only
    pay for the int()/float() on nodes the graph actually uses.
    """
    coord = {}
    get = coord.__setitem__
    seen = wanted.__contains__
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line[0] != "n":
                continue
            nid = int(line[1:line.index(" ", 1)])
            if not seen(nid):
                continue
            yi = line.rindex(" y")
            xi = line.rindex(" x", 0, yi)
            get(nid, (float(line[yi + 2:].rstrip()), float(line[xi + 2:yi])))
    return coord


def _bfs(n, off, to, start):
    """Reachability bitmap from `start` over a CSR graph."""
    seen = bytearray(n)
    seen[start] = 1
    stack = [start]
    pop = stack.pop
    while stack:
        u = pop()
        for k in range(off[u], off[u + 1]):
            v = to[k]
            if not seen[v]:
                seen[v] = 1
                stack.append(v)
    return seen


R = 6371008.8


def haversine(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180.0
    dlat = (b_lat - a_lat) * p
    dlon = (b_lon - a_lon) * p
    x = (math.sin(dlat / 2) ** 2 +
         math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


def _bearing(alat, alon, blat, blon):
    p = math.pi / 180.0
    y = math.sin((blon - alon) * p) * math.cos(blat * p)
    x = (math.cos(alat * p) * math.sin(blat * p) -
         math.sin(alat * p) * math.cos(blat * p) * math.cos((blon - alon) * p))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def main(opl, out, fmt=3):
    """Build `out` from `opl`. fmt=3 (default) adds each segment's highway
    class and flags; fmt=2 writes exactly the old RGRAPH02 layout, which is
    what reproduces the shipped bayarea.rg byte for byte."""
    if fmt not in (2, 3):
        raise ValueError(f"format must be 2 or 3, not {fmt}")
    t0 = time.time()
    restrictions, restr_ways, restr_nodes, restr_skipped = pass0(opl)
    t0b = time.time()
    print(f"  pass0 (rels)   {t0b-t0:6.2f}s  {len(restrictions)} usable turn "
          f"restrictions over {len(restr_ways)} ways")
    for why, cnt in sorted(restr_skipped.items(), key=lambda kv: -kv[1]):
        print(f"                   skipped {cnt:6d}  {why}")

    ways, refcount, endpoints = pass1(opl, restr_ways)
    t1 = time.time()
    print(f"  pass1 (ways)   {t1-t0b:6.2f}s  kept {len(ways)} ways, "
          f"{len(refcount)} distinct node refs")

    # every node the kept ways touch needs a coordinate (shape points included)
    wanted = set(refcount)
    coord = pass2(opl, wanted)
    t2 = time.time()
    print(f"  pass2 (nodes)  {t2-t1:6.2f}s  resolved {len(coord)} coords")

    # ---- junction detection: a node is a junction if >1 way uses it, or it is
    #      the first/last node of a way, or a way revisits it (self-loop).
    #      A via node of a turn restriction is a junction too, even when the
    #      count says otherwise - otherwise the restriction has nowhere to sit.
    junction = set(endpoints)
    for nid, c in refcount.items():
        if c > 1:
            junction.add(nid)
    junction |= (restr_nodes & coord.keys())
    junction &= coord.keys()

    nid2idx = {}
    lats = []; lons = []
    for nid in junction:
        nid2idx[nid] = len(lats)
        la, lo = coord[nid]
        lats.append(la); lons.append(lo)
    n_nodes = len(lats)
    t3 = time.time()
    print(f"  junctions      {t3-t2:6.2f}s  {n_nodes} junction nodes")

    # ---- split ways at junctions into segments, build directed edge list
    strings = {"": 0}
    str_list = [""]
    seg_pts = array("i")      # lat,lon *1e7 interleaved
    seg_off = array("I", [0])
    seg_name = array("I")
    seg_hw = array("B")       # RGRAPH03: the way's real highway class
    seg_flags = array("B")    # RGRAPH03: SEG_* bits
    out_edges = [[] for _ in range(n_nodes)]   # per node: (to, len_dm, cls, segref)
    # for the ways a restriction names: every piece of them, so the relation
    # can be resolved to directed edge ids once the CSR exists.
    #   way_slots[wid] = [(u_osm, v_osm, fwd_slot, bwd_slot), ...]
    # where a slot is (node_index, position within that node's out-edge list).
    way_slots = {}

    for refs, c, name, fwd, bwd, sp, wid, flags in ways:
        # cost uses the per-way speed, folded into the class-independent length;
        # we keep class for display + keep a per-segment speed by quantising it
        # back onto the class table (close enough; exact speed is stored via
        # the class whose speed is nearest).
        cls_for_speed = min(range(len(SPEED_KMH)),
                            key=lambda i: abs(SPEED_KMH[i] - sp))
        ni = strings.get(name)
        if ni is None:
            ni = len(str_list); strings[name] = ni; str_list.append(name)
        cur = []
        prev_junc = None
        for r in refs:
            if r not in coord:
                # broken geometry -> flush what we have and restart
                cur = []; prev_junc = None
                continue
            cur.append(r)
            if r in junction and len(cur) > 1 and prev_junc is not None:
                # emit segment prev_junc..r
                L = 0.0
                for i in range(len(cur) - 1):
                    a = coord[cur[i]]; b = coord[cur[i + 1]]
                    L += haversine(a[0], a[1], b[0], b[1])
                if L < 0.05:
                    L = 0.05
                sid = len(seg_name)
                for q in cur:
                    la, lo = coord[q]
                    seg_pts.append(int(round(la * 1e7)))
                    seg_pts.append(int(round(lo * 1e7)))
                seg_off.append(len(seg_pts) // 2)
                seg_name.append(ni)
                seg_hw.append(c)
                seg_flags.append(flags)
                u = nid2idx[cur[0]]; v = nid2idx[r]
                ldm = int(L * 10) or 1
                fslot = bslot = None
                if fwd:
                    fslot = (u, len(out_edges[u]))
                    out_edges[u].append((v, ldm, cls_for_speed, sid))
                if bwd:
                    bslot = (v, len(out_edges[v]))
                    out_edges[v].append((u, ldm, cls_for_speed, sid | 0x80000000))
                if wid:
                    way_slots.setdefault(wid, []).append(
                        (cur[0], r, fslot, bslot))
                cur = [r]
            if r in junction:
                prev_junc = r

    n_segs = len(seg_name)
    n_edges = sum(len(e) for e in out_edges)
    t4 = time.time()
    print(f"  segments       {t4-t3:6.2f}s  {n_segs} segments, "
          f"{n_edges} directed edges, {len(seg_pts)//2} shape points")

    # ---- CSR
    adj_off = array("I", bytes(4 * (n_nodes + 1)))
    adj_to = array("I", bytes(4 * n_edges))
    adj_len = array("I", bytes(4 * n_edges))
    adj_cls = array("B", bytes(n_edges))
    adj_seg = array("I", bytes(4 * n_edges))
    k = 0
    for i in range(n_nodes):
        adj_off[i] = k
        for (v, ldm, c, sid) in out_edges[i]:
            adj_to[k] = v; adj_len[k] = ldm; adj_cls[k] = c; adj_seg[k] = sid
            k += 1
    adj_off[n_nodes] = k
    del out_edges

    # slots -> global directed edge ids, while adj_off still means what it did
    for wid, pieces in way_slots.items():
        way_slots[wid] = [
            (a, b,
             None if fs is None else adj_off[fs[0]] + fs[1],
             None if bs is None else adj_off[bs[0]] + bs[1])
            for (a, b, fs, bs) in pieces]

    # ---- keep only the strongly-connected core -----------------------------
    # Pick the highest-degree node as a hub; the SCC containing it is the
    # routable core. Everything else is an extract-boundary stub or a
    # mapping error and would show up at runtime as "NO ROUTE".
    hub = max(range(n_nodes), key=lambda i: adj_off[i + 1] - adj_off[i])
    fwd_ok = _bfs(n_nodes, adj_off, adj_to, hub)
    # reverse adjacency, just for the backward reachability test
    rc = array("I", bytes(4 * (n_nodes + 1)))
    for kk in range(n_edges):
        rc[adj_to[kk] + 1] += 1
    for i in range(n_nodes):
        rc[i + 1] += rc[i]
    rto = array("I", bytes(4 * n_edges))
    cur = array("I", rc[:n_nodes])
    for u in range(n_nodes):
        for kk in range(adj_off[u], adj_off[u + 1]):
            v = adj_to[kk]
            rto[cur[v]] = u
            cur[v] += 1
    bwd_ok = _bfs(n_nodes, rc, rto, hub)
    del rc, rto, cur
    keep = bytearray(n_nodes)
    nkeep = 0
    for i in range(n_nodes):
        if fwd_ok[i] and bwd_ok[i]:
            keep[i] = 1
            nkeep += 1
    dropped = n_nodes - nkeep
    kmap = None
    if dropped:
        remap = array("i", bytes(4 * n_nodes))
        j = 0
        for i in range(n_nodes):
            if keep[i]:
                remap[i] = j
                j += 1
            else:
                remap[i] = -1
        kmap = array("i", [-1]) * n_edges
        new_off = array("I", bytes(4 * (nkeep + 1)))
        new_to = array("I"); new_len = array("I")
        new_cls = array("B"); new_seg = array("I")
        nlat = []; nlon = []
        j = 0
        for i in range(n_nodes):
            if not keep[i]:
                continue
            new_off[j] = len(new_to)
            nlat.append(lats[i]); nlon.append(lons[i])
            for kk in range(adj_off[i], adj_off[i + 1]):
                v = adj_to[kk]
                if keep[v]:
                    kmap[kk] = len(new_to)
                    new_to.append(remap[v]); new_len.append(adj_len[kk])
                    new_cls.append(adj_cls[kk]); new_seg.append(adj_seg[kk])
            j += 1
        new_off[nkeep] = len(new_to)
        adj_off, adj_to, adj_len, adj_cls, adj_seg = (
            new_off, new_to, new_len, new_cls, new_seg)
        lats, lons = nlat, nlon
        n_nodes, n_edges = nkeep, len(new_to)
        for wid, pieces in way_slots.items():
            way_slots[wid] = [
                (a, b,
                 None if fk is None or kmap[fk] < 0 else kmap[fk],
                 None if bk is None or kmap[bk] < 0 else kmap[bk])
                for (a, b, fk, bk) in pieces]
        del kmap, remap
    t4b = time.time()
    print(f"  SCC prune      {t4b-t4:6.2f}s  dropped {dropped} nodes "
          f"({dropped*100.0/(nkeep+dropped):.2f}%), core = {n_nodes} nodes, "
          f"{n_edges} edges")
    t4 = t4b

    # ---- resolve the restriction relations onto directed edge sequences ----
    tr_off, tr_edge, tr_next, tr_head, tr_gate, rstats = _build_restrictions(
        restrictions, way_slots, adj_off, adj_to, adj_seg, seg_off, seg_pts,
        n_nodes)
    t4c = time.time()
    print(f"  restrictions   {t4c-t4:6.2f}s  {rstats['applied']} applied "
          f"({rstats['vianode']} via node, {rstats['viaway']} via way), "
          f"{len(tr_off)-1} trie states, {len(tr_edge)} transitions, "
          f"{rstats['banned']} banned moves")
    for why, cnt in sorted(rstats["skipped"].items(), key=lambda kv: -kv[1]):
        print(f"                   unresolved {cnt:6d}  {why}")
    t4 = t4c

    # ---- projection: equirectangular metres about the extract centroid
    olat = sum(lats) / n_nodes
    olon = sum(lons) / n_nodes
    my = 111132.92 - 559.82 * math.cos(2 * olat * math.pi / 180)
    mx = 111412.84 * math.cos(olat * math.pi / 180)
    xy = array("f", bytes(8 * n_nodes))
    for i in range(n_nodes):
        xy[2 * i] = (lons[i] - olon) * mx
        xy[2 * i + 1] = (lats[i] - olat) * my

    # ---- string table
    blob = bytearray()
    str_off = array("I", [0])
    for s in str_list:
        blob += s.encode("utf-8")
        str_off.append(len(blob))

    # ---- snap grid (uniform buckets in projected metres, CSR) --------------
    CELL = 400.0
    minx = min(xy[0::2]); maxx = max(xy[0::2])
    miny = min(xy[1::2]); maxy = max(xy[1::2])
    gw = int((maxx - minx) / CELL) + 1
    gh = int((maxy - miny) / CELL) + 1
    ncell = gw * gh
    counts = array("I", bytes(4 * (ncell + 1)))
    cellof = array("I", bytes(4 * n_nodes))
    for i in range(n_nodes):
        cx = int((xy[2 * i] - minx) / CELL)
        cy = int((xy[2 * i + 1] - miny) / CELL)
        c = cy * gw + cx
        cellof[i] = c
        counts[c + 1] += 1
    for c in range(ncell):
        counts[c + 1] += counts[c]
    grid_off = counts
    grid_nod = array("I", bytes(4 * n_nodes))
    cursor = array("I", grid_off[:ncell])
    for i in range(n_nodes):
        c = cellof[i]
        grid_nod[cursor[c]] = i
        cursor[c] += 1
    print(f"  snap grid           {gw}x{gh} = {ncell} cells @ {CELL:.0f} m")
    del cellof, cursor

    # ---- SEGMENT snap grid -------------------------------------------------
    # The node grid answers "nearest junction", which is the wrong question
    # when the rider is halfway down a block.  This one buckets every segment
    # into every cell its geometry passes through, so "nearest point on a
    # road" is exact and still local.
    t4d = time.time()
    sgrid_off, sgrid_seg = _segment_grid(
        adj_seg, seg_off, seg_pts, n_segs, olat, olon, mx, my,
        minx, miny, CELL, gw, gh)
    print(f"  segment grid   {time.time()-t4d:6.2f}s  {len(sgrid_seg)} "
          f"segment-cell entries ({len(sgrid_seg)*4/1e6:.2f} MB)")

    # ---- write
    with open(out, "wb") as f:
        f.write(b"RGRAPH03" if fmt == 3 else b"RGRAPH02")
        f.write(struct.pack("<5I", n_nodes, n_edges, n_segs,
                            len(seg_pts) // 2, len(blob)))
        f.write(struct.pack("<3I", len(str_list), gw, gh))
        f.write(struct.pack("<3I", len(tr_off) - 1, len(tr_edge),
                            len(sgrid_seg)))
        f.write(struct.pack("<7d", olat, olon, mx, my, minx, miny, CELL))
        for a in (xy, adj_off, adj_to, adj_len, adj_cls, adj_seg,
                  seg_off, seg_name, seg_pts, str_off, grid_off, grid_nod,
                  sgrid_off, sgrid_seg, tr_off, tr_edge, tr_next, tr_head):
            a.tofile(f)
        f.write(bytes(tr_gate))
        if fmt == 3:
            f.write(bytes(seg_hw))
            f.write(bytes(seg_flags))
        f.write(bytes(blob))
    t5 = time.time()
    sz = os.path.getsize(out)
    print(f"  write          {t5-t4:6.2f}s")
    import resource
    print(f"  peak build RSS "
          f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f} MB")
    print(f"  TOTAL BUILD    {t5-t0:6.2f}s   ->  {out}  {sz/1e6:.2f} MB "
          f"({sz/n_nodes:.1f} B/node, {sz/n_edges:.1f} B/edge)")
    # section sizes, for the size table in the report
    print("  sections: xy %.2f MB | csr %.2f MB | geom %.2f MB | names %.2f MB"
          " | seggrid %.2f MB | turns %.2f MB"
          % (8 * n_nodes / 1e6,
             (4 * (n_nodes + 1) + 9 * n_edges + 4 * n_edges) / 1e6,
             (len(seg_pts) * 4 + 4 * (n_segs + 1)) / 1e6,
             (len(blob) + 4 * len(str_off)) / 1e6,
             (len(sgrid_off) + len(sgrid_seg)) * 4 / 1e6,
             (len(tr_off) + 2 * len(tr_edge) + len(tr_head)) * 4 / 1e6
             + len(tr_gate) / 1e6))


# ------------------------------------------------------------ segment grid
def _segment_grid(adj_seg, seg_off, seg_pts, n_segs, olat, olon, mx, my,
                  minx, miny, cell, gw, gh):
    """Bucket each live segment into every grid cell its geometry crosses.

    Only segments that still carry a directed edge after the SCC prune are
    indexed: a candidate the router could never use is a candidate it should
    never have to project onto and reject.
    """
    live = bytearray(n_segs)
    for k in range(len(adj_seg)):
        live[adj_seg[k] & 0x7FFFFFFF] = 1

    ncell = gw * gh
    counts = array("I", bytes(4 * (ncell + 1)))
    per_seg = []                       # parallel to the sids we keep
    sids = array("I")
    cells_seen = set()
    for sid in range(n_segs):
        if not live[sid]:
            continue
        a, b = seg_off[sid], seg_off[sid + 1]
        if b <= a:
            continue
        cells_seen.clear()
        pcx = pcy = None
        for i in range(a, b):
            x = (seg_pts[2 * i + 1] / 1e7 - olon) * mx
            y = (seg_pts[2 * i] / 1e7 - olat) * my
            cx = int((x - minx) / cell)
            cy = int((y - miny) / cell)
            if cx < 0: cx = 0
            elif cx >= gw: cx = gw - 1
            if cy < 0: cy = 0
            elif cy >= gh: cy = gh - 1
            if pcx is not None and (cx != pcx or cy != pcy):
                # walk the cells between the two shape points, so a long
                # straight run does not skip the cells it passes through
                steps = max(abs(cx - pcx), abs(cy - pcy))
                if steps > 1:
                    for s in range(1, steps):
                        cells_seen.add((pcy + (cy - pcy) * s // steps) * gw
                                       + (pcx + (cx - pcx) * s // steps))
            cells_seen.add(cy * gw + cx)
            pcx, pcy = cx, cy
        per_seg.append((sid, tuple(cells_seen)))
        for c in cells_seen:
            counts[c + 1] += 1
    for c in range(ncell):
        counts[c + 1] += counts[c]
    total = counts[ncell]
    sids = array("I", bytes(4 * total))
    cursor = array("I", counts[:ncell])
    for sid, cells in per_seg:
        for c in cells:
            sids[cursor[c]] = sid
            cursor[c] += 1
    return counts, sids


# --------------------------------------------------------- restriction trie
def _edge_tail(adj_off, n_nodes, k):
    lo, hi = 0, n_nodes
    while lo < hi:
        mid = (lo + hi) // 2
        if adj_off[mid] <= k:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _edge_bearings(k, adj_seg, seg_off, seg_pts):
    """(bearing_in, bearing_out) of directed edge k, degrees."""
    sid = adj_seg[k]
    rev = sid & 0x80000000
    sid &= 0x7FFFFFFF
    a, b = seg_off[sid], seg_off[sid + 1]
    if b - a < 2:
        return 0.0, 0.0
    pts = [(seg_pts[2 * i] / 1e7, seg_pts[2 * i + 1] / 1e7) for i in (a, a + 1,
                                                                      b - 2,
                                                                      b - 1)]
    if rev:
        first = _bearing(pts[3][0], pts[3][1], pts[2][0], pts[2][1])
        last = _bearing(pts[1][0], pts[1][1], pts[0][0], pts[0][1])
    else:
        first = _bearing(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
        last = _bearing(pts[2][0], pts[2][1], pts[3][0], pts[3][1])
    return first, last


def _build_restrictions(restrictions, way_slots, adj_off, adj_to, adj_seg,
                        seg_off, seg_pts, n_nodes):
    """Turn the relations into a trie of banned directed-edge sequences."""
    stats = {"applied": 0, "vianode": 0, "viaway": 0, "banned": 0,
             "skipped": {}}

    def skip(why):
        stats["skipped"][why] = stats["skipped"].get(why, 0) + 1

    # osm node id -> graph node index, for the ways a restriction names only
    osm2idx = {}
    for pieces in way_slots.values():
        for (a, b, fk, bk) in pieces:
            if fk is not None:
                osm2idx[b] = adj_to[fk]
                osm2idx[a] = _edge_tail(adj_off, n_nodes, fk)
            elif bk is not None:
                osm2idx[a] = adj_to[bk]
                osm2idx[b] = _edge_tail(adj_off, n_nodes, bk)

    def edges_into(wid, osm_node):
        """Directed edges of way `wid` whose head is `osm_node`."""
        out = []
        for (a, b, fk, bk) in way_slots.get(wid, ()):
            if fk is not None and b == osm_node:
                out.append(fk)
            if bk is not None and a == osm_node:
                out.append(bk)
        return out

    def edges_out_of(wid, osm_node):
        """Directed edges of way `wid` whose tail is `osm_node`."""
        out = []
        for (a, b, fk, bk) in way_slots.get(wid, ()):
            if fk is not None and a == osm_node:
                out.append(fk)
            if bk is not None and b == osm_node:
                out.append(bk)
        return out

    def via_chain(wid, from_edge, to_way):
        """(chain, exit_osm_node) along via way `wid`, or (None, None).

        The via way is usually one segment, but it may have been split at an
        internal junction, so walk it.
        """
        pieces = way_slots.get(wid, ())
        # entry node = head of from_edge, as an osm id
        head_idx = adj_to[from_edge]
        cands = [n for n in (p[0] for p in pieces) if osm2idx.get(n) == head_idx]
        cands += [n for n in (p[1] for p in pieces) if osm2idx.get(n) == head_idx]
        if not cands:
            return None, None
        entry = cands[0]
        # breadth-first over the via way's own edges
        seen = {entry: []}
        queue = [entry]
        while queue:
            node = queue.pop(0)
            chain = seen[node]
            if len(chain) > 6:
                continue
            if chain and edges_out_of(to_way, node):
                return chain, node
            for e in edges_out_of(wid, node):
                nxt_idx = adj_to[e]
                nxt = None
                for (a, b, fk, bk) in pieces:
                    if fk == e:
                        nxt = b
                    elif bk == e:
                        nxt = a
                if nxt is None or nxt in seen:
                    continue
                seen[nxt] = chain + [e]
                queue.append(nxt)
        return None, None

    def turn_delta(k_in, k_out):
        _, b0 = _edge_bearings(k_in, adj_seg, seg_off, seg_pts)
        b1, _ = _edge_bearings(k_out, adj_seg, seg_off, seg_pts)
        return (b1 - b0 + 540.0) % 360.0 - 180.0

    seqs = []          # tuples of directed edge ids, all illegal
    for value, frm_ways, via_nodes, via_ways, to_ways in restrictions:
        only = value in ONLY_VALUES
        want = TURN_ANGLE.get(value.split("_", 1)[1] if only
                              else value[3:] if value.startswith("no_") else "")
        pairs = []                       # (from_edge, via_chain, to_edge)
        if via_nodes:
            vn = via_nodes[0]
            if vn not in osm2idx:
                skip("via node is not a junction in the graph")
                continue
            for fw in frm_ways:
                for tw in to_ways:
                    for fe in edges_into(fw, vn):
                        for te in edges_out_of(tw, vn):
                            if te == fe:
                                continue
                            pairs.append((fe, [], te, vn))
            kind = "vianode"
        else:
            vw = via_ways[0]
            for fw in frm_ways:
                for tw in to_ways:
                    # every from-way edge that ends somewhere on the via way,
                    # then chained along the via way to the to way
                    for fe in _edges_into_way_end(way_slots, fw, vw):
                        chain, exitn = via_chain(vw, fe, tw)
                        if chain is None:
                            continue
                        for te in edges_out_of(tw, exitn):
                            if te in chain or te == fe:
                                continue
                            pairs.append((fe, chain, te, exitn))
            kind = "viaway"
        if not pairs:
            skip("could not resolve from/via/to onto graph edges")
            continue
        if want is not None:
            # The relation names a direction; the geometry has to agree. A
            # `no_u_turn` whose members resolve to a straight-through pair is
            # a mis-resolution, and banning it strands whatever is past the
            # junction - so drop it rather than guess.
            scored = []
            for p in pairs:
                err = abs(((turn_delta(p[0], p[2]) - want + 540.0) % 360.0)
                          - 180.0)
                if err <= TURN_TOL:
                    scored.append((err, p))
            if not scored:
                skip("no candidate turn points the way the relation says")
                continue
            scored.sort(key=lambda x: x[0])
            pairs = [scored[0][1]] if only else [p for _, p in scored]
        elif only:
            pairs = pairs[:1]
        applied = False
        for (fe, chain, te, vnode) in pairs:
            prefix = tuple([fe] + list(chain))
            if only:
                vidx = adj_to[prefix[-1]]
                for k in range(adj_off[vidx], adj_off[vidx + 1]):
                    if k == te:
                        continue
                    seqs.append(prefix + (k,))
                    applied = True
            else:
                seqs.append(prefix + (te,))
                applied = True
        if applied:
            stats["applied"] += 1
            stats[kind] += 1

    return _pack_trie(seqs, adj_to, adj_off, n_nodes, stats)


def _edges_into_way_end(way_slots, from_way, via_way):
    """From-way edges whose head is an endpoint the via way also touches."""
    via_nodes = set()
    for (a, b, fk, bk) in way_slots.get(via_way, ()):
        via_nodes.add(a); via_nodes.add(b)
    out = []
    for (a, b, fk, bk) in way_slots.get(from_way, ()):
        if fk is not None and b in via_nodes:
            out.append(fk)
        if bk is not None and a in via_nodes:
            out.append(bk)
    return out


def _pack_trie(seqs, adj_to, adj_off, n_nodes, stats):
    """Flatten banned edge sequences into the packed trie the router reads."""
    children = [{}]          # trie state -> {edge: child state}
    banned = [set()]         # trie state -> {edge that ends a restriction}
    head = [0]               # trie state -> graph node it sits on
    depth = [0]
    lastedge = [-1]
    for seq in seqs:
        t = 0
        for e in seq[:-1]:
            nt = children[t].get(e)
            if nt is None:
                nt = len(children)
                children.append({}); banned.append(set())
                head.append(adj_to[e]); depth.append(depth[t] + 1)
                lastedge.append(e)
                children[t][e] = nt
            t = nt
        banned[t].add(seq[-1])

    # A prefix that ends on an edge which itself opens another restriction has
    # to inherit that restriction's moves, or the second one is lost whenever
    # the first is in play. Patterns are two or three edges long, so walking
    # states in depth order settles it in one pass.
    root = children[0]
    for t in sorted(range(1, len(children)), key=lambda i: depth[i]):
        t0 = root.get(lastedge[t])
        if t0 is None or t0 == t:
            continue
        for e, c in children[t0].items():
            children[t].setdefault(e, c)
        banned[t] |= banned[t0]
    for t in range(len(children)):
        for e in banned[t]:
            children[t].pop(e, None)
    stats["banned"] = sum(len(b) for b in banned)

    ntr = len(children)
    tr_off = array("I", bytes(4 * (ntr + 1)))
    tr_edge = array("I")
    tr_next = array("I")
    for t in range(ntr):
        tr_off[t] = len(tr_edge)
        for e, c in children[t].items():
            tr_edge.append(e); tr_next.append(c)
        for e in banned[t]:
            tr_edge.append(e); tr_next.append(BANNED)
    tr_off[ntr] = len(tr_edge)
    tr_head = array("I", head)
    tr_gate = bytearray(n_nodes)
    for e in root:
        tr_gate[_edge_tail(adj_off, n_nodes, e)] = 1
    return tr_off, tr_edge, tr_next, tr_head, tr_gate, stats


if __name__ == "__main__":
    args = sys.argv[1:]
    fmt = 3
    if "--format" in args:
        i = args.index("--format")
        fmt = int(args[i + 1])
        del args[i:i + 2]
    main(args[0], args[1], fmt=fmt)
