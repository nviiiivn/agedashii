#!/usr/bin/env python3
"""Measure what the offline router can tell at a junction, against Valhalla.

WHY: the offline router only said turn / bear / U-turn / continue / arrive. The
server path (Valhalla) also says sharp, exit, ramp and merge. Before the offline
router says any of those, this measures - on real Bay Area junctions - what its
own geometry and road classes show where Valhalla says each one, so the angle
thresholds come from data rather than a guess.

METHOD
1. O/D pairs are random points on the .rg graph's segments (fixed seed), 2-40 km
   apart, inside the server tiles' coverage - fit_speed_kmh.py's sampler.
2. Each pair is routed twice: by the offline router (routing.route, offline,
   the shipped bayarea.rg), and by the SERVER PATH's Valhalla over HTTP with the
   app's own payload (routing._valhalla_payload), so the instruction strings
   are the ones the app actually receives.
3. Every junction on the offline route (each boundary between two legs) is
   recorded with what the offline maneuver code sees there: the heading change
   `delta` (routing's own bearing arithmetic), the junction's out-degree, the
   road class in and out, and whether the street name changes.
4. A junction COUNTS only where both routes take the same path through it: the
   Valhalla shape passes within AGREE_M of the offline route at the junction
   and AGREE_AHEAD_M before and after it. What Valhalla says there is its
   maneuver within PAIR_M of the junction, or "none" if it says nothing.

    python measure_maneuvers.py [--pairs 300] [--out maneuvers.json]
Needs the server's Valhalla on --url (default http://localhost:8002).
"""
import argparse
import bisect
import json
import math
import pathlib
import random
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(REPO / "packages" / "agedashii-washi"), str(REPO / "apps" / "navi")]
from agedashii_washi import routing  # noqa: E402

AGREE_M = 12.0
AGREE_AHEAD_M = 40.0
PAIR_M = 15.0
CELL_M = 50.0


def pairs(g, n, seed, bbox):
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


def valhalla(url, a, b):
    payload = routing._valhalla_payload(a, b)              # noqa: SLF001
    req = urllib.request.Request(url.rstrip("/") + "/route", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


class Near:
    """Distance from a point to a polyline, through a grid of its segments."""

    def __init__(self, pts):
        self.lat0 = pts[0][0]
        self.k = math.cos(math.radians(self.lat0))
        self.xy = [self._xy(p) for p in pts]
        self.cells = {}
        for i in range(len(self.xy) - 1):
            (x0, y0), (x1, y1) = self.xy[i], self.xy[i + 1]
            for cx in range(int(min(x0, x1) // CELL_M), int(max(x0, x1) // CELL_M) + 1):
                for cy in range(int(min(y0, y1) // CELL_M), int(max(y0, y1) // CELL_M) + 1):
                    self.cells.setdefault((cx, cy), []).append(i)

    def _xy(self, p):
        return (p[1] * 111320.0 * self.k, p[0] * 110540.0)

    def dist(self, p):
        x, y = self._xy(p)
        best = math.inf
        cx, cy = int(x // CELL_M), int(y // CELL_M)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.cells.get((cx + dx, cy + dy), ()):
                    (x0, y0), (x1, y1) = self.xy[i], self.xy[i + 1]
                    vx, vy = x1 - x0, y1 - y0
                    L = vx * vx + vy * vy
                    t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / L))
                    best = min(best, math.hypot(x - x0 - t * vx, y - y0 - t * vy))
        return best


def point_at(pts, cum, c):
    """The point `c` metres along a polyline with cumulative distances `cum`."""
    c = max(0.0, min(cum[-1], c))
    i = max(1, bisect.bisect_left(cum, c))
    i = min(i, len(pts) - 1)
    span = cum[i] - cum[i - 1]
    t = 0.0 if span <= 0 else (c - cum[i - 1]) / span
    a, b = pts[i - 1], pts[i]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def junctions(g, legs, pts, bounds, cum):
    """What routing._maneuvers_from_legs sees at each leg boundary."""
    out = []
    for i in range(1, len(legs)):
        s0, e0 = bounds[i - 1]
        s1, e1 = bounds[i]
        if e0 <= s0 or e1 <= s1:
            continue
        b0 = routing.bearing(pts[e0 - 1], pts[e0])
        b1 = routing.bearing(pts[s1], pts[s1 + 1])
        delta = (b1 - b0 + 540) % 360 - 180
        node = legs[i - 1][4]
        degree = (g.off[node + 1] - g.off[node]) if node >= 0 else 0
        out.append({"s1": s1, "at": pts[s1], "cum": cum[s1], "delta": round(delta, 1), "degree": degree,
                    "cls_in": routing.CLASSES[legs[i - 1][2]], "cls_out": routing.CLASSES[legs[i][2]],
                    "name_in": legs[i - 1][3], "name_out": legs[i][3],
                    "len_in": round(legs[i - 1][1], 1), "len_out": round(legs[i][1], 1)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--bbox", default="-122.65,37.20,-121.75,38.10")
    ap.add_argument("--url", default="http://localhost:8002")
    ap.add_argument("--graph", default=str(REPO / "assets" / "maps" / "bayarea.rg"))
    ap.add_argument("--out", default="maneuvers.json")
    a = ap.parse_args()

    routing.start_loading(a.graph)
    g = routing.graph(timeout=120)
    captured = {}
    original = routing._maneuvers_from_legs                  # noqa: SLF001

    def capture(g_, legs, pts, bounds, cum):
        captured["j"] = junctions(g_, legs, pts, bounds, cum)
        made = original(g_, legs, pts, bounds, cum)
        # What the offline router itself said at each junction, if anything.
        said = {m.index: m for m in made[1:-1]}
        for j in captured["j"]:
            m = said.get(j["s1"])
            j["o_text"] = m.instruction if m else ""
            j["o_kind"] = getattr(m, "kind", None) if m else None
        return made

    routing._maneuvers_from_legs = capture                  # noqa: SLF001
    rows, skipped = [], 0
    for n, (o, d) in enumerate(pairs(g, a.pairs, a.seed, [float(x) for x in a.bbox.split(",")])):
        captured.clear()
        r = routing.route(o, d, allow_server=False)
        try:
            v = valhalla(a.url, o, d)
        except Exception as exc:                            # noqa: BLE001
            print(f"pair {n}: valhalla failed: {exc}", file=sys.stderr)
            skipped += 1
            continue
        if not r.ok or "j" not in captured or not v.get("trip"):
            skipped += 1
            continue
        vpts = routing.decode_polyline6(v["trip"]["legs"][0]["shape"])
        near = Near(vpts)
        mans = [(vpts[min(m["begin_shape_index"], len(vpts) - 1)], m)
                for m in v["trip"]["legs"][0]["maneuvers"]]
        agreed = []
        for j in captured["j"]:
            probes = [j["at"], point_at(r.points, r.cum_m, j["cum"] - AGREE_AHEAD_M),
                      point_at(r.points, r.cum_m, j["cum"] + AGREE_AHEAD_M)]
            if any(near.dist(p) > AGREE_M for p in probes):
                continue
            j.update(pair=n, v_type=0, v_text="")
            agreed.append(j)
        # One to one: each Valhalla maneuver goes to the nearest agreed
        # junction, so two leg boundaries a few metres apart do not both claim
        # the same "turn right".
        for at, m in mans:
            best, bd = None, PAIR_M
            for j in agreed:
                dd = routing.haversine_m(at, j["at"])
                if dd <= bd:
                    best, bd = j, dd
            if best is not None:
                best.update(v_type=m["type"], v_text=m["instruction"],
                            v_delta=(m.get("bearing_after", 0) - m.get("bearing_before", 0)
                                     + 540) % 360 - 180)
        rows += agreed
        if n % 25 == 0:
            print(f"{n} pairs, {len(rows)} agreed junctions", file=sys.stderr, flush=True)
    pathlib.Path(a.out).write_text(json.dumps({"pairs": a.pairs, "seed": a.seed, "skipped": skipped,
                                               "rows": rows}, indent=0))
    print(f"wrote {len(rows)} junctions to {a.out}; {skipped} pairs skipped", file=sys.stderr)


if __name__ == "__main__":
    main()
