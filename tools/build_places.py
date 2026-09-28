#!/usr/bin/env python3
"""Build Navi's offline places list: every named place worth riding to.

The road graph (.rg) holds streets and nothing else, so "Chabot Space and
Science Center" could never be found without signal - and the online
geocoder is a gazetteer of towns that does not know it either. This makes the
list the app searches offline: named museums, parks, cafes, fuel, peaks,
stations and the rest, from the SAME OSM extract as the graph.

    python3 tools/build_places.py --pbf bayarea.osm.pbf --out assets/maps/places.sqlite

Needs osmium-tool on PATH. Output is plain SQLite (no FTS5 - whether the
phone's Python has it is unverified): a `place` table, a `tok` table of
(word, place id) with an index so a word PREFIX is a range scan, and `meta`.
Words come from agedashii_navi.places.norm_tokens, the same function the app
searches with, so the two can never split a name differently.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "apps" / "navi"), str(REPO / "packages" / "agedashii-washi")]
from agedashii_navi.places import norm_tokens  # noqa: E402

#: The tag that says what a place IS, checked in this order. A key maps to
#: None (every value) or to the values kept. Left out on purpose: highway
#: (streets are already in the graph), bus stops and stop positions (tens of
#: thousands of "Main St & 3rd"), waterways and landuse.
KINDS = [
    ("tourism", None), ("amenity", None), ("leisure", None), ("shop", None),
    ("historic", None), ("natural", None), ("boundary", {"protected_area", "national_park"}),
    ("aeroway", {"aerodrome", "terminal"}), ("railway", {"station"}),
    ("public_transport", {"station"}), ("office", None), ("craft", None),
    ("healthcare", None), ("place", None), ("building", None),
]
#: Named, but never a destination.
SKIP_VALUES = {"bench", "waste_basket", "bicycle_parking", "parking_space", "vending_machine",
               "post_box", "telephone", "recycling", "parking_entrance", "grit_bin"}
#: Other names a place is known by; searched, never shown.
ALT_KEYS = ("name:en", "alt_name", "official_name", "short_name", "brand")
#: Two features with the same name and kind this close are one place (a
#: museum drawn as a point AND as a building outline). ~300 m.
DEDUP_DEG = 0.003


def kind_of(props):
    for key, keep in KINDS:
        value = props.get(key)
        if value is None:
            continue
        if value in SKIP_VALUES or (keep is not None and value not in keep):
            return None
        if key == "building":
            return "building"
        if key == "boundary":
            return "protected area"
        return value.replace("_", " ") if value != "yes" else key
    return None


def centre(geometry):
    """A point for a geometry: the feature itself, or its vertices' mean."""
    t, c = geometry["type"], geometry["coordinates"]
    if t == "Point":
        return c[1], c[0]
    rings = {"LineString": [c], "Polygon": c[:1], "MultiPolygon": [p[0] for p in c],
             "MultiLineString": c}.get(t)
    if not rings:
        return None
    pts = [p for ring in rings for p in ring]
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def features(pbf, workdir):
    """(name, kind, lat, lon, [alt names]) for every named place, points first."""
    named = os.path.join(workdir, "named.osm.pbf")
    out = os.path.join(workdir, "named.geojsonseq")
    subprocess.run(["osmium", "tags-filter", "-O", "-o", named, pbf, "nwr/name"], check=True)
    subprocess.run(["osmium", "export", "-O", "-f", "geojsonseq", "-o", out, named], check=True)
    with open(out, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line.strip("\x1e"))
            props = feat["properties"]
            kind = kind_of(props)
            name = (props.get("name") or "").strip()
            if kind is None or not name:
                continue
            point = centre(feat["geometry"])
            if point is None:
                continue
            alts = [props[k] for k in ALT_KEYS if props.get(k) and props[k] != name]
            yield name, kind, point[0], point[1], alts


def write_db(rows, out, meta=None):
    """Write the list. `rows` is (name, kind, lat, lon, alt names); returns the count kept."""
    tmp = str(out) + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                            lat REAL NOT NULL, lon REAL NOT NULL);
        CREATE TABLE tok (t TEXT NOT NULL, id INTEGER NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    seen = set()
    n = 0
    for name, kind, lat, lon, alts in rows:
        key = (" ".join(norm_tokens(name)), kind, round(lat / DEDUP_DEG), round(lon / DEDUP_DEG))
        if key in seen:
            continue
        seen.add(key)
        n += 1
        con.execute("INSERT INTO place VALUES (?, ?, ?, ?, ?)", (n, name, kind, round(lat, 6), round(lon, 6)))
        words = set(norm_tokens(name))
        for alt in alts:
            words.update(norm_tokens(alt))
        con.executemany("INSERT INTO tok VALUES (?, ?)", [(w, n) for w in words])
    con.execute("CREATE INDEX tok_t ON tok (t)")
    info = dict(meta or {}, count=n, built_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in info.items()])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, out)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", required=True, help="the SAME OSM extract the road graph was built from")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    stamp = subprocess.run(["osmium", "fileinfo", "-e", "-g", "data.timestamp.last", a.pbf],
                           capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as work:
        n = write_db(features(a.pbf, work), a.out,
                     meta={"source": os.path.basename(a.pbf), "osm_timestamp": stamp})
    print(f"{a.out}: {n} places, {os.path.getsize(a.out):,} bytes, OSM data to {stamp}")


if __name__ == "__main__":
    main()
