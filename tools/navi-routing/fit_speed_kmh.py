#!/usr/bin/env python3
"""Measure washi routing.SPEED_KMH from Valhalla, per GRAPH class.

WHY: SPEED_KMH was "least-squares fitted to Valhalla's motorcycle costing over
45 Bay Area routes" against her SERVER, whose admin database is empty, so every
road got WORLD-AVERAGE speeds (urban primary 27 km/h). nvii chose California
speeds ("31 realisticc i guess" for Oakland-Fremont), so this measures each
class from the California-table tiles, and ships with its numbers because the
original fitter was lost.

THE FIRST VERSION OF THIS SCRIPT WAS WRONG, AND WHY IT MATTERED. It filed each
Valhalla edge under the routing.CLASSES slot NAMED like the edge's OSM highway
tag, on the assumption that the .rg graph numbers its classes in that order.
It does not. Checked against OSM tags, graph class 2 ("trunk") is mostly
motorway, class 3 ("trunk_link", 108,790 edges) is mostly secondary road,
class 5 ("primary_link") mostly tertiary, and four slots hold no edges at all.
So every speed landed on the wrong kind of road - freeway-grade class 2 was
priced at 58 km/h. Total ETAs still validated (+2.4% median), because errors
on different classes cancel in a sum. The harm was ROUTE CHOICE: a router that
thinks freeways are slow sends her down surface streets, and nothing on the
screen says why.

METHOD
1. O/D pairs are random points ON her .rg graph's segments (fixed seed), 2-40 km
   apart, so they are real on-road trips in the graph's own area.
2. Each pair is routed by Valhalla 3.6.3 (motorcycle, use_trails 0.0) on the
   California-table tar. trace_attributes gives each edge its length and the
   elapsed time at its end node; an edge's time is the difference from the
   previous edge, so it INCLUDES the intersection/turn delay entering it.
3. CLASS = THE GRAPH'S OWN. Each Valhalla edge's midpoint is snapped onto the
   .rg graph, heading-aware (Graph.snap_edge), within SNAP_M; the edge counts
   under the class of the graph segment it lands on. Nothing trusts a name.
   An edge that lands on no graph road, or on one with another name, takes
   the class of the nearest matched edge of the same OSM way in the same trip;
   only what is still unmatched after that is excluded (about 7% of the km,
   nearly all on a name the graph spells differently).
   The OSM highway tag of each edge (edge.way_id, looked up with osmium in the
   extract the tiles were built from) is recorded only to REPORT what each
   graph class contains.
4. speed[class] = sum(length) / sum(time) over the MEASURE half of the trips.
   JUNCTION_PENALTY_S and U_TURN_PENALTY_S are NOT in this: the Python router's
   reported ETA is sum(len * SEC_PER_DM[cls]) - the penalties only steer its
   search - so each class speed must absorb Valhalla's intersection delays
   itself, and does, by step 2. Under MIN_KM of evidence keeps the old value,
   and says so.
5. CALIBRATE, on the measure half only: the Python router's ETA with the
   measured speeds against Valhalla's, same trips; every measured class is
   divided by the median ratio. Class averages make a router's own ETA read
   short (it picks the edges the average flatters); one common factor fixes
   that without changing the ratios between classes that route choice uses.
6. VALIDATE on the other half with her actual Python router (routing.route,
   offline, bayarea.rg): current, measured and calibrated tables, on the two
   things that matter - ETA against Valhalla's California ETA, and ROUTE
   CHOICE, the share of the offline route's length within OVERLAP_M of
   Valhalla's.

    VENV/bin/python fit_speed_kmh.py --tar TAR --pbf PBF --config CFG [--pairs 400] [--out fit.json]
Needs pyvalhalla 3.6.3 (the tile build venv) and osmium on PATH.
"""
import argparse
import json
import pathlib
import random
import statistics
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(REPO / "packages" / "agedashii-washi"), str(REPO / "apps" / "navi"),
                str(pathlib.Path(__file__).resolve().parent)]
from agedashii_washi import routing  # noqa: E402
from measure_maneuvers import Near  # noqa: E402

import valhalla  # noqa: E402

SNAP_M = 15.0
OVERLAP_M = 20.0
MIN_KM = 1.0


def actor(tar, config):
    cfg = json.loads(pathlib.Path(config).read_text())
    cfg["mjolnir"]["tile_extract"] = str(tar)
    cfg["mjolnir"]["tile_dir"] = tempfile.mkdtemp()     # only the tar can answer
    cfg["mjolnir"].pop("traffic_extract", None)
    return valhalla.Actor(cfg)


def pairs(g, n, seed, bbox):
    """Random on-graph O/D pairs inside `bbox` (the tiles' coverage - the .rg
    reaches well past it, sampled 36.8-38.9 N, -123.6 to -121.1)."""
    from agedashii_navi.places import segment_point
    west, south, east, north = bbox
    rnd = random.Random(seed)
    out = []
    while len(out) < n:
        a = segment_point(g, rnd.randrange(g.s))
        b = segment_point(g, rnd.randrange(g.s))
        inside = all(p and south <= p[0] <= north and west <= p[1] <= east for p in (a, b))
        if inside and 2000 <= routing.haversine_m(a, b) <= 40000:
            out.append((a, b))
    return out


def valhalla_trip(A, a, b):
    """(total seconds, route shape, [(way_id, km, seconds, (lat, lon), heading)])."""
    req = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
           "costing": "motorcycle", "costing_options": {"motorcycle": {"use_trails": 0.0}},
           "units": "kilometers", "directions_type": "none"}
    r = json.loads(A.route(json.dumps(req)))
    encoded = r["trip"]["legs"][0]["shape"]
    shape = routing.decode_polyline6(encoded)
    t = json.loads(A.trace_attributes(json.dumps({
        "encoded_polyline": encoded, "shape_match": "edge_walk",
        "costing": "motorcycle", "costing_options": req["costing_options"],
        "filters": {"action": "include", "attributes": [
            "edge.length", "edge.way_id", "edge.names", "node.elapsed_time",
            "edge.begin_shape_index", "edge.end_shape_index"]}})))
    edges, prev = [], 0.0
    for e in t["edges"]:
        end = e["end_node"]["elapsed_time"]
        i, j = e.get("begin_shape_index", 0), e.get("end_shape_index", 0)
        i, j = max(0, min(i, len(shape) - 1)), max(0, min(j, len(shape) - 1))
        mid = _midpoint(shape[i:j + 1]) if j > i else shape[i]
        head = routing.bearing(shape[i], shape[j]) if j > i else None
        names = {n.lower() for n in (e.get("names") or [])}
        edges.append((e["way_id"], e["length"], end - prev, mid, head, names))
        prev = end
    return r["trip"]["summary"]["time"], shape, edges


def _midpoint(pts):
    """The point halfway along a short polyline, by length."""
    steps = [routing.haversine_m(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
    half, acc = sum(steps) / 2, 0.0
    for k, s in enumerate(steps):
        if acc + s >= half and s > 0:
            f = (half - acc) / s
            return (pts[k][0] + (pts[k + 1][0] - pts[k][0]) * f,
                    pts[k][1] + (pts[k + 1][1] - pts[k][1]) * f)
        acc += s
    return pts[len(pts) // 2]


def graph_class(g, mid, head, names):
    """(class of the graph road under this point, why not) - one is None.

    The snap alone can land on a parallel service road or frontage road a
    few metres from the one Valhalla drove, so the street NAME is checked too:
    the graph road must carry one of the names Valhalla gives the edge, or
    both must be unnamed. A name is independent of the class being measured.
    """
    s = g.snap_edge(mid[0], mid[1], max_m=SNAP_M, heading=head)
    if s is None or s.seg < 0 or s.dist_m > SNAP_M:
        return None, "no graph road within SNAP_M"
    k = s.fwd if s.fwd >= 0 else s.bwd
    if k < 0:
        return None, "no graph road within SNAP_M"
    name = g.name(s.seg).lower()
    if (name or names) and name not in names:
        return None, "a different road's name"
    return g.cls[k], None


def highway_tags(pbf, way_ids):
    tags = {}
    ids = sorted(way_ids)
    for i in range(0, len(ids), 2000):
        opl = subprocess.run(["osmium", "getid", "-f", "opl", pbf] + [f"w{w}" for w in ids[i:i + 2000]],
                             capture_output=True, text=True, check=True).stdout
        for line in opl.splitlines():
            wid = int(line.split(" ", 1)[0][1:])
            t = next((p[1:] for p in line.split(" ") if p.startswith("T")), "")
            kv = dict(x.split("=", 1) for x in t.split(",") if "=" in x)
            tags[wid] = kv.get("highway")
    return tags


def python_route(a, b, speed_kmh):
    saved = routing.SEC_PER_DM, routing.MAXSPEED_MS
    routing.SEC_PER_DM = [0.1 / (k / 3.6) for k in speed_kmh]
    routing.MAXSPEED_MS = max(speed_kmh) / 3.6
    try:
        r = routing.route(a, b, allow_server=False)
        return r if r.ok else None
    finally:
        routing.SEC_PER_DM, routing.MAXSPEED_MS = saved


def overlap(route, near):
    """Share of the route's length within OVERLAP_M of the other route."""
    pts, total, on = route.points, 0.0, 0.0
    for k in range(len(pts) - 1):
        step = routing.haversine_m(pts[k], pts[k + 1])
        mid = ((pts[k][0] + pts[k + 1][0]) / 2, (pts[k][1] + pts[k + 1][1]) / 2)
        total += step
        if near.dist(mid) <= OVERLAP_M:
            on += step
    return on / total if total else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tar", required=True, help="the California-table Valhalla tar")
    ap.add_argument("--pbf", required=True, help="the extract that tar was built from")
    ap.add_argument("--config", required=True, help="the valhalla.json the tar was built with")
    ap.add_argument("--graph", default=str(REPO / "assets" / "maps" / "bayarea.rg"))
    ap.add_argument("--pairs", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--bbox", default="-122.65,37.20,-121.75,38.10",
                    help="west,south,east,north - the tiles' coverage (their manifest's bbox)")
    ap.add_argument("--out", default="fit_speed_kmh.json")
    a = ap.parse_args()

    routing.start_loading(a.graph)
    g = routing.graph(timeout=120)
    A = actor(a.tar, a.config)
    trips = []
    for o, d in pairs(g, a.pairs, a.seed, [float(x) for x in a.bbox.split(",")]):
        try:
            total, shape, edges = valhalla_trip(A, o, d)
        except Exception as ex:  # noqa: BLE001 - a pair Valhalla cannot route is reported, not fatal
            print(f"skip {o}->{d}: {str(ex)[:80]}", file=sys.stderr)
            continue
        trips.append((o, d, total, shape, edges))
        if len(trips) % 50 == 0:
            print(f"{len(trips)} trips", file=sys.stderr, flush=True)
    half = len(trips) // 2
    measure, validate = trips[:half], trips[half:]
    tags = highway_tags(a.pbf, {e[0] for t in measure for e in t[4]})

    n = len(routing.CLASSES)
    in_graph = [0] * n
    for c in g.cls:
        in_graph[c] += 1
    km, sec, edges_n = [0.0] * n, [0.0] * n, [0] * n
    osm = [dict() for _ in range(n)]
    unmatched, borrowed_km = {}, 0.0
    for t in measure:
        found = [graph_class(g, mid, head, names) for _way, _l, _s, mid, head, names in t[4]]
        # A short piece of a way inside a junction can snap onto the cross
        # street and fail the name check, so a piece that did not match takes
        # the class of the nearest piece of the SAME OSM way in this trip that
        # did. Measured, this recovers little (2.4 km of 6,400) - the -4% ETA
        # bias first blamed on dropped pieces is the router's, see CALIBRATE.
        by_way = {}
        for idx, (c, _why) in enumerate(found):
            if c is not None:
                by_way.setdefault(t[4][idx][0], []).append((idx, c))
        for idx, (way, length, secs, mid, head, names) in enumerate(t[4]):
            c, why = found[idx]
            if c is None and way in by_way:
                c = min(by_way[way], key=lambda ic: abs(ic[0] - idx))[1]
                borrowed_km += length
            if c is None:
                unmatched[why] = unmatched.get(why, 0.0) + length
                continue
            km[c] += length
            sec[c] += secs
            edges_n[c] += 1
            tag = tags.get(way) or "?"
            osm[c][tag] = osm[c].get(tag, 0.0) + length
    old = list(routing.SPEED_KMH)
    new, per_class = [], {}
    for i, label in enumerate(routing.CLASSES):
        measured = km[i] / (sec[i] / 3600) if sec[i] > 0 else None
        enough = measured is not None and km[i] >= MIN_KM
        use = round(measured, 1) if enough else old[i]
        new.append(use)
        mix = sorted(osm[i].items(), key=lambda kv: -kv[1])
        per_class[i] = {"label": label, "edges_in_graph": in_graph[i], "km": round(km[i], 2),
                        "edges": edges_n[i], "measured_kmh": round(measured, 1) if measured else None,
                        "old_kmh": old[i], "proposed_kmh": use, "kept_old": not enough,
                        "osm_mix_pct": {k: round(100 * v / km[i]) for k, v in mix[:4]} if km[i] else {}}

    # CALIBRATION, on the MEASURE half only. Per-class average speeds make the
    # router's own ETA short: a search picks exactly the edges where the class
    # average flatters the road (measured: median -4.0% on held-out trips).
    # One factor for every measured class fixes the ETA and leaves the ratios
    # between classes - which is all route choice sees - untouched.
    ratios = []
    for o, d, total, _shape, _ in measure:
        r = python_route(o, d, new)
        if r and r.duration_s > 0:
            ratios.append(total / r.duration_s)
    factor = statistics.median(ratios) if ratios else 1.0
    measured_raw = list(new)
    new = [round(s / factor, 1) if not per_class[i]["kept_old"] else s
           for i, s in enumerate(measured_raw)]
    for i in per_class:
        per_class[i]["proposed_kmh"] = new[i]

    rows = []
    for o, d, total, shape, _ in validate:
        r_old = python_route(o, d, old)
        r_raw = python_route(o, d, measured_raw)
        r_new = python_route(o, d, new)
        if r_old and r_raw and r_new:
            near = Near(shape)
            rows.append({"from": o, "to": d, "valhalla_min": total / 60,
                         "old_min": r_old.duration_s / 60, "raw_min": r_raw.duration_s / 60,
                         "new_min": r_new.duration_s / 60,
                         "old_overlap": overlap(r_old, near), "raw_overlap": overlap(r_raw, near),
                         "new_overlap": overlap(r_new, near)})

    def err(key):
        pct = [100 * (r[key] - r["valhalla_min"]) / r["valhalla_min"] for r in rows]
        return {"median_pct": round(statistics.median(pct), 1),
                "median_abs_pct": round(statistics.median(abs(p) for p in pct), 1),
                "median_abs_min": round(statistics.median(abs(r[key] - r["valhalla_min"]) for r in rows), 1)}

    def same_road(key):
        xs = [r[key] for r in rows]
        return {"median_pct": round(100 * statistics.median(xs), 1),
                "mean_pct": round(100 * sum(xs) / len(xs), 1),
                "trips_under_80pct": sum(1 for x in xs if x < 0.8)}

    result = {"method": "see fit_speed_kmh.py docstring", "tar": a.tar, "pbf": a.pbf, "seed": a.seed,
              "trips_measured": len(measure), "trips_validated": len(rows),
              "unmatched_km": {why: round(v, 2) for why, v in unmatched.items()},
              "matched_km": round(sum(km), 2),
              "borrowed_from_same_way_km": round(borrowed_km, 2),
              "classes": per_class, "SPEED_KMH_old": old, "SPEED_KMH_measured": measured_raw,
              "calibration_factor": round(factor, 4), "calibration_trips": len(ratios),
              "SPEED_KMH_proposed": new,
              "validation_eta_vs_valhalla_CA": {"old_table": err("old_min"), "measured_table": err("raw_min"),
                                                "proposed_table": err("new_min")},
              "validation_route_on_valhallas_road": {"old_table": same_road("old_overlap"),
                                                     "measured_table": same_road("raw_overlap"),
                                                     "proposed_table": same_road("new_overlap")},
              "validation_rows": rows}
    pathlib.Path(a.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("trips_measured", "trips_validated", "unmatched_km", "matched_km",
                                             "borrowed_from_same_way_km",
                                             "SPEED_KMH_old", "SPEED_KMH_measured",
                                             "calibration_factor", "SPEED_KMH_proposed",
                                             "validation_eta_vs_valhalla_CA",
                                             "validation_route_on_valhallas_road")}, indent=1))
    for i, v in per_class.items():
        print(f"  {i:2d} {v['label']:15s} {v['edges_in_graph']:7d} in graph  {v['km']:8.2f} km  "
              f"measured {v['measured_kmh']}  old {v['old_kmh']} -> {v['proposed_kmh']}"
              + ("  (kept old)" if v["kept_old"] else "") + f"  {v['osm_mix_pct']}")


if __name__ == "__main__":
    main()
