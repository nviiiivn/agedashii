#!/usr/bin/env python3
"""Measure a replacement for washi routing.SPEED_KMH from Valhalla, per class.

WHY: SPEED_KMH was "least-squares fitted to Valhalla's motorcycle costing over
45 Bay Area routes" against her SERVER, whose admin database is empty, so every
road got WORLD-AVERAGE speeds (urban primary 27 km/h). nvii chose California
speeds ("31 realisticc i guess" for Oakland-Fremont). This measures each class
from the California-table tiles instead of solving for all of them at once, and
the script ships with the numbers because the original fitter was lost - which
is why nobody could tell the table was wrong.

METHOD
1. O/D pairs are random points ON her .rg graph's segments (fixed seed), 2-40 km
   apart, so they are real on-road trips in the graph's own area.
2. Each pair is routed by Valhalla 3.6.3 (motorcycle, use_trails 0.0) on the
   California-table tar. trace_attributes gives each edge its length and the
   elapsed time at its end node; an edge's time is the difference from the
   previous edge, so it INCLUDES the intersection/turn delay entering it.
3. CLASS MAPPING - an edge's slot in routing.CLASSES is the OSM `highway` tag
   of its way (edge.way_id, looked up with osmium in the same extract the
   tiles were built from). CLASSES are highway values, so the mapping is
   identity; Valhalla's own road_class is NOT used (it merges links into
   their parent class and tracks into service_other). Edges on ways whose
   highway is not a .rg class (track, path, busway...) are counted and
   excluded - the Python graph has no such class.
4. speed[class] = sum(length) / sum(time) over the MEASURE half of the trips.
   JUNCTION_PENALTY_S and U_TURN_PENALTY_S are NOT in this: the Python router's
   reported ETA is sum(len * SEC_PER_DM[cls]) (routing.py:1509-1510) - the
   penalties only steer its search (routing.py:755-760) - so each class speed
   must absorb Valhalla's intersection delays itself, and does, by step 2.
5. VALIDATE on the other half: her actual Python router (routing.route,
   offline, bayarea.rg) with the current table and with the new one, each ETA
   against Valhalla's California ETA for the same pair.

    VENV/bin/python fit_speed_kmh.py --tar TAR --pbf PBF [--pairs 120] [--out fit.json]
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
sys.path[:0] = [str(REPO / "packages" / "agedashii-washi"), str(REPO / "apps" / "navi")]
from agedashii_washi import routing  # noqa: E402

import valhalla  # noqa: E402


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
    req = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
           "costing": "motorcycle", "costing_options": {"motorcycle": {"use_trails": 0.0}},
           "units": "kilometers", "directions_type": "none"}
    r = json.loads(A.route(json.dumps(req)))
    t = json.loads(A.trace_attributes(json.dumps({
        "encoded_polyline": r["trip"]["legs"][0]["shape"], "shape_match": "edge_walk",
        "costing": "motorcycle", "costing_options": req["costing_options"],
        "filters": {"action": "include", "attributes": ["edge.length", "edge.way_id", "node.elapsed_time"]}})))
    edges, prev = [], 0.0
    for e in t["edges"]:
        end = e["end_node"]["elapsed_time"]
        edges.append((e["way_id"], e["length"], end - prev))
        prev = end
    return r["trip"]["summary"]["time"], edges


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


def python_eta(a, b, speed_kmh):
    saved = routing.SEC_PER_DM, routing.MAXSPEED_MS
    routing.SEC_PER_DM = [0.1 / (k / 3.6) for k in speed_kmh]
    routing.MAXSPEED_MS = max(speed_kmh) / 3.6
    try:
        r = routing.route(a, b, allow_server=False)
        return r.duration_s if r.ok else None
    finally:
        routing.SEC_PER_DM, routing.MAXSPEED_MS = saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tar", required=True, help="the California-table Valhalla tar")
    ap.add_argument("--pbf", required=True, help="the extract that tar was built from")
    ap.add_argument("--config", default=str(pathlib.Path(__file__).with_name("valhalla.json")))
    ap.add_argument("--pairs", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--bbox", default="-122.65,37.20,-121.75,38.10",
                    help="west,south,east,north - the tiles' coverage (their manifest's bbox)")
    ap.add_argument("--out", default="fit_speed_kmh.json")
    a = ap.parse_args()

    g = routing.graph(timeout=60)
    A = actor(a.tar, a.config)
    trips = []
    for o, d in pairs(g, a.pairs, a.seed, [float(x) for x in a.bbox.split(",")]):
        try:
            total, edges = valhalla_trip(A, o, d)
        except Exception as ex:  # noqa: BLE001 - a pair Valhalla cannot route is reported, not fatal
            print(f"skip {o}->{d}: {str(ex)[:80]}")
            continue
        trips.append((o, d, total, edges))
    tags = highway_tags(a.pbf, {w for _, _, _, es in trips for w, _, _ in es})
    half = len(trips) // 2
    measure, validate = trips[:half], trips[half:]

    km = {c: 0.0 for c in routing.CLASSES}
    sec = {c: 0.0 for c in routing.CLASSES}
    edges_n = {c: 0 for c in routing.CLASSES}
    other_km = 0.0
    for _, _, _, es in measure:
        for w, length, t in es:
            c = tags.get(w)
            if c in km:
                km[c] += length
                sec[c] += t
                edges_n[c] += 1
            else:
                other_km += length
    old = list(routing.SPEED_KMH)
    new = []
    per_class = {}
    for i, c in enumerate(routing.CLASSES):
        measured = km[c] / (sec[c] / 3600) if sec[c] > 0 else None
        # A class with under 1 km of evidence keeps its old value: a speed from
        # a handful of metres is noise, and keeping it is said, not hidden.
        use = round(measured, 1) if measured and km[c] >= 1.0 else old[i]
        new.append(use)
        per_class[c] = {"km": round(km[c], 2), "edges": edges_n[c], "measured_kmh":
                        round(measured, 1) if measured else None, "old_kmh": old[i], "proposed_kmh": use,
                        "kept_old": use == old[i] and not (measured and km[c] >= 1.0)}

    rows = []
    for o, d, total, _ in validate:
        e_old, e_new = python_eta(o, d, old), python_eta(o, d, new)
        if e_old and e_new:
            rows.append({"from": o, "to": d, "valhalla_min": total / 60, "old_min": e_old / 60, "new_min": e_new / 60})

    def err(key):
        pct = [100 * (r[key] - r["valhalla_min"]) / r["valhalla_min"] for r in rows]
        return {"median_pct": round(statistics.median(pct), 1),
                "median_abs_pct": round(statistics.median(abs(p) for p in pct), 1),
                "median_abs_min": round(statistics.median(abs(r[key] - r["valhalla_min"]) for r in rows), 1)}

    result = {"method": "see fit_speed_kmh.py docstring", "tar": a.tar, "pbf": a.pbf, "seed": a.seed,
              "trips_measured": len(measure), "trips_validated": len(rows),
              "excluded_km_not_a_rg_class": round(other_km, 2),
              "classes": per_class, "SPEED_KMH_old": old, "SPEED_KMH_proposed": new,
              "validation_vs_valhalla_CA": {"old_table": err("old_min"), "proposed_table": err("new_min")},
              "validation_rows": rows}
    pathlib.Path(a.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("trips_measured", "trips_validated", "excluded_km_not_a_rg_class",
                                             "SPEED_KMH_old", "SPEED_KMH_proposed", "validation_vs_valhalla_CA")}, indent=1))
    for c, v in per_class.items():
        print(f"  {c:16s} {v['km']:8.2f} km {v['edges']:5d} edges  measured {v['measured_kmh']}  old {v['old_kmh']}  -> {v['proposed_kmh']}"
              + ("  (kept old: <1 km evidence)" if v["kept_old"] else ""))


if __name__ == "__main__":
    main()
