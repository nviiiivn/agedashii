#!/usr/bin/env python3
"""Check the built tiles do what the Navi app needs, measured on this machine.

Run it with the build's own venv, so it is the same Valhalla (3.6.3):
    WORK/venv/bin/python verify_tiles.py --work WORK [--rg .../bayarea.rg]

1. every tile header says 3.6.3, and the router reads the TAR (tile_dir is
   pointed at nothing, so a stray directory cannot make it pass)
2. route times, cold and warm, beside her Python A* on the same machine
3. posted limits: every edge on the test routes, Valhalla's `speed_limit` and
   `speed_type` against the raw OSM `maxspeed` tag on the same way
4. the 1 Hz "what is the limit here" poll: noisy fixes map-matched with
   trace_attributes, timed, and checked against the true way
Writes out/<tar name>.verify.json beside the tar.
"""
import argparse
import collections
import json
import math
import pathlib
import random
import statistics
import subprocess
import sys
import tarfile
import time

import valhalla

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
OAKLAND = (37.8044, -122.2712)          # tests/test_routing.py:1000
ROUTES = {
    "oakland-chabot": (OAKLAND, (37.8187, -122.1805)),
    "oakland-fremont": (OAKLAND, (37.5485, -121.9886)),   # phase 5's "done" pair
}
# Exactly what routing.py:1608 asks her server for. use_hills and use_primary
# do not exist in 3.6.3's motorcycle costing and are ignored.
SERVER_OPTS = {"use_hills": 1.0, "use_primary": 0.5, "use_trails": 0.5}
WARM_RUNS = 15
KPH_TO_MPH = 0.621371


def decode6(s):
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += d
            else:
                lon += d
        pts.append((lat / 1e6, lon / 1e6))
    return pts


def haversine_m(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def timed(fn, *a):
    t0 = time.perf_counter()
    out = fn(*a)
    return out, (time.perf_counter() - t0) * 1000


def opl_unescape(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "%":
            j = s.index("%", i + 1)
            out.append(chr(int(s[i + 1:j], 16)))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def osm_tags(pbf, way_ids):
    """{way_id: {tag: value}} straight from the extract the tiles came from."""
    ids = "".join(f"w{w}\n" for w in sorted(way_ids))
    p = subprocess.run(["osmium", "getid", "-f", "opl", str(pbf), "-i", "-"],
                       input=ids, capture_output=True, text=True, check=True)
    tags = {}
    for line in p.stdout.splitlines():
        fields = line.split(" ")
        t = next((f[1:] for f in fields if f.startswith("T")), "")
        kv = {}
        for pair in filter(None, t.split(",")):
            k, _, v = pair.partition("=")
            kv[opl_unescape(k)] = opl_unescape(v)
        tags[int(fields[0][1:])] = kv
    return tags


def posted_mph(value):
    """The number on the sign, in mph, the way a rider reads it; None if not a number."""
    v = value.strip().lower()
    if v == "none":
        return "unlimited"
    num = v.split()[0].replace("mph", "")
    try:
        n = float(num)
    except ValueError:
        return None
    return round(n) if "mph" in v else round(n * KPH_TO_MPH)


def check_versions(tar_path):
    want = valhalla.__version__
    seen = collections.Counter()
    with tarfile.open(tar_path) as tar:
        for m in tar:
            if m.name.endswith(".gph"):
                seen[tar.extractfile(m).read(32)[16:32].split(b"\0")[0].decode()] += 1
    ok = bool(seen) and all(v.startswith(want) for v in seen)
    return {"tile_versions": dict(seen), "reader": want, "ok": ok}


def run_route(actor, frm, to, opts):
    req = {"locations": [{"lat": frm[0], "lon": frm[1]}, {"lat": to[0], "lon": to[1]}],
           "costing": "motorcycle", "costing_options": {"motorcycle": opts},
           "directions_options": {"units": "kilometers"}}
    return actor.route(req)


def bench_routes(actor, opts):
    out = {}
    for name, (frm, to) in ROUTES.items():
        first, cold = timed(run_route, actor, frm, to, opts)
        warm = sorted(timed(run_route, actor, frm, to, opts)[1] for _ in range(WARM_RUNS))
        s = first["trip"]["summary"]
        out[name] = {"km": round(s["length"], 2), "min": round(s["time"] / 60, 1),
                     "cold_ms": round(cold, 1), "warm_ms_median": round(statistics.median(warm), 1),
                     "warm_ms_min": round(warm[0], 1), "warm_ms_max": round(warm[-1], 1),
                     "maneuvers": len(first["trip"]["legs"][0]["maneuvers"]),
                     "shape": first["trip"]["legs"][0]["shape"]}
    return out


def bench_python(rg):
    sys.path.insert(0, str(REPO / "packages" / "agedashii-washi"))
    from agedashii_washi import routing as R
    g, load_ms = timed(R.Graph, str(rg))
    out = {"load_ms": round(load_ms, 1)}
    for name, (frm, to) in ROUTES.items():
        r, first = timed(R._route_offline, g, frm, to)
        warm = sorted(timed(R._route_offline, g, frm, to)[1] for _ in range(5))
        out[name] = {"ok": r.ok, "km": round(r.distance_m / 1000, 2),
                     "min": round(r.duration_s / 60, 1), "first_ms": round(first, 1),
                     "warm_ms_median": round(statistics.median(warm), 1)}
    return out


def random_pairs(n, seed=11, inset=0.02):
    """n seeded origin/destination pairs spread over the whole bbox."""
    w, s, e, nn = BBOX
    rnd = random.Random(seed)
    pt = lambda: (round(rnd.uniform(s + inset, nn - inset), 5),
                  round(rnd.uniform(w + inset, e - inset), 5))
    return [(pt(), pt()) for _ in range(n)]


def coverage(actor, pairs, rg):
    """Route every pair with Valhalla (and her A*, if the graph is here)."""
    shapes, v_fail, v_ms, p_fail = {}, [], [], []
    g = R = None
    if pathlib.Path(rg).exists():
        sys.path.insert(0, str(REPO / "packages" / "agedashii-washi"))
        from agedashii_washi import routing as R
        g = R.Graph(str(rg))
    for i, (frm, to) in enumerate(pairs):
        try:
            res, ms = timed(run_route, actor, frm, to, {})
            shapes[f"random-{i}"] = res["trip"]["legs"][0]["shape"]
            v_ms.append((res["trip"]["summary"]["length"], ms))
        except Exception as exc:                      # a failed route is a finding
            v_fail.append({"pair": (frm, to), "error": f"{type(exc).__name__}: {exc}"[:160]})
        if g is not None and not R._route_offline(g, frm, to).ok:
            p_fail.append((frm, to))
    per_km = sorted(ms / km for km, ms in v_ms if km > 5)
    return shapes, {"pairs": len(pairs), "valhalla_failed": v_fail,
                    "python_failed": p_fail if g is not None else "no graph",
                    "valhalla_km_median": round(statistics.median(k for k, _ in v_ms), 1),
                    "valhalla_ms_per_km_median": round(statistics.median(per_km), 2),
                    "valhalla_ms_max": round(max(ms for _, ms in v_ms), 1)}


BBOX = (-122.65, 37.20, -121.75, 38.10)   # build_valhalla_tiles.BBOX

ATTRS = ["edge.way_id", "edge.speed_limit", "edge.speed_type", "edge.road_class",
         "edge.names", "edge.length"]


def check_posted(actor, pbf, shapes):
    rows, snapped = [], []
    for name, shape in shapes.items():
        req = {"encoded_polyline": shape, "shape_match": "edge_walk", "costing": "motorcycle",
               "units": "miles", "filters": {"attributes": ATTRS, "action": "include"}}
        try:
            ta = actor.trace_attributes(req)
        except RuntimeError:                          # counted, not hidden
            snapped.append(name)
            ta = actor.trace_attributes({**req, "shape_match": "walk_or_snap"})
        for e in ta["edges"]:
            rows.append({"route": name, **{k: e.get(k) for k in
                         ("way_id", "speed_limit", "speed_type", "road_class", "length")},
                         "name": (e.get("names") or [""])[0]})
    tags = osm_tags(pbf, {r["way_id"] for r in rows})
    tally = collections.Counter({"agree": 0, "missing": 0, "invented": 0, "mismatch": 0})
    by_type = collections.Counter()
    miles = collections.Counter()
    examples = []
    for r in rows:
        raw = tags.get(r["way_id"], {})
        tag = raw.get("maxspeed") or raw.get("maxspeed:forward") or raw.get("maxspeed:backward")
        want = posted_mph(tag) if tag else None
        got = r["speed_limit"]
        by_type[f"{r['speed_type']}/{'limit' if got is not None else 'no-limit'}"] += 1
        miles["posted" if got is not None else "none"] += r["length"] or 0
        if tag and want is None:
            verdict = "unparsed_tag"
        elif want is None:
            verdict = "agree" if got is None else "invented"
        elif got is None:
            verdict = "missing"
        else:
            verdict = "agree" if got == want else "mismatch"
        tally[verdict] += 1
        if verdict != "agree" or len(examples) < 6:
            examples.append({**r, "osm_maxspeed": tag, "verdict": verdict,
                             "highway": raw.get("highway")})
    total = sum(miles.values())
    return {"edges": len(rows), "distinct_ways": len(tags), "verdicts": dict(tally),
            "routes": len(shapes), "matched_by_snap_not_walk": snapped,
            "speed_type_x_limit": dict(by_type),
            "posted_share_of_miles": round(miles["posted"] / total, 3) if total else None,
            "examples": examples[:40]}


def check_matching(actor, shape, fixes_per_call=8, spacing_m=25.0, noise_m=5.0, seed=7):
    """Walk the route at one fix per `spacing_m` (~56 mph at 1 Hz), add GPS
    scatter, and ask trace_attributes for the way under the newest fix."""
    rnd = random.Random(seed)
    line = decode6(shape)
    walk, carry = [line[0]], 0.0
    for a, b in zip(line, line[1:]):
        seg = haversine_m(a, b)
        d = spacing_m - carry
        while d <= seg:
            f = d / seg
            walk.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            d += spacing_m
        carry = seg - (d - spacing_m)
    truth = actor.trace_attributes({
        "shape": [{"lat": p[0], "lon": p[1]} for p in walk], "shape_match": "map_snap",
        "costing": "motorcycle", "filters": {"attributes": [
            "edge.way_id", "matched.edge_index", "matched.type"], "action": "include"}})
    true_way = [truth["edges"][m["edge_index"]]["way_id"]
                if m.get("type") == "matched" and m.get("edge_index") is not None else None
                for m in truth["matched_points"]]
    times, right, asked = [], 0, 0
    for end in range(fixes_per_call, len(walk), 10):
        pts = []
        for lat, lon in walk[end - fixes_per_call:end]:
            dy, dx = rnd.gauss(0, noise_m), rnd.gauss(0, noise_m)
            pts.append({"lat": lat + dy / 111320,
                        "lon": lon + dx / (111320 * math.cos(math.radians(lat)))})
        res, ms = timed(actor.trace_attributes, {
            "shape": pts, "shape_match": "map_snap", "costing": "motorcycle",
            "units": "miles", "filters": {"attributes": [
                "edge.way_id", "edge.speed_limit", "edge.speed_type",
                "matched.edge_index"], "action": "include"}})
        times.append(ms)
        last = res["matched_points"][-1].get("edge_index")
        if last is not None and true_way[end - 1] is not None:
            asked += 1
            right += res["edges"][last]["way_id"] == true_way[end - 1]
    gaps = [haversine_m(a, b) for a, b in zip(line, line[1:])]
    times.sort()
    return {"calls": len(times), "fixes_per_call": fixes_per_call, "noise_m": noise_m,
            "ms_median": round(statistics.median(times), 1),
            "ms_p95": round(times[int(0.95 * (len(times) - 1))], 1),
            "ms_max": round(times[-1], 1),
            "newest_fix_on_true_way": f"{right}/{asked}",
            "route_shape_points": len(line), "route_vertex_gap_m_max": round(max(gaps)),
            "route_vertex_gaps_over_100m": sum(g > 100 for g in gaps)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--rg", default=REPO / "assets" / "maps" / "bayarea.rg")
    ap.add_argument("--random", type=int, default=40, help="random routes over the bbox")
    args = ap.parse_args()
    work = pathlib.Path(args.work).resolve()
    cfg = json.loads((work / "valhalla.json").read_text())
    tar = pathlib.Path(cfg["mjolnir"]["tile_extract"])
    cfg["mjolnir"]["tile_dir"] = str(work / "no-such-dir")   # the tar, or nothing
    report = {"tar": tar.name, "tar_bytes": tar.stat().st_size,
              "host": subprocess.run(["uname", "-snm"], capture_output=True,
                                     text=True).stdout.strip()}
    report["versions"] = check_versions(tar)
    print(json.dumps(report["versions"]), flush=True)
    actor, ms = timed(valhalla.Actor, cfg)
    report["actor_init_ms"] = round(ms, 1)
    report["routes_default_costing"] = bench_routes(actor, {})
    report["routes_server_costing"] = bench_routes(actor, SERVER_OPTS)
    if pathlib.Path(args.rg).exists():
        report["python_astar"] = bench_python(args.rg)
    shapes = {k: v.pop("shape") for k, v in report["routes_default_costing"].items()}
    for v in report["routes_server_costing"].values():
        v.pop("shape")
    extra, report["coverage"] = coverage(actor, random_pairs(args.random), args.rg)
    report["posted"] = check_posted(actor, work / "bayarea.osm.pbf", shapes)
    report["posted_random_routes"] = check_posted(actor, work / "bayarea.osm.pbf", extra)
    report["matching"] = check_matching(actor, shapes["oakland-fremont"])
    path = tar.with_suffix(".verify.json")
    path.write_text(json.dumps(report, indent=2) + "\n")
    brief = dict(report)
    for k in ("posted", "posted_random_routes"):
        brief[k] = {kk: v for kk, v in report[k].items() if kk != "examples"}
    print(json.dumps(brief, indent=1))
    print(f"full report: {path}")


if __name__ == "__main__":
    main()
