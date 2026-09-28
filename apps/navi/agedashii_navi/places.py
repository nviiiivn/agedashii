"""Where to ride to, and the four ways of saying it.

Her list wanted all of these, and they are deliberately in this order,
because the order is the priority:

1. **A street name, off the device.** `search_streets` reads the road graph
   already installed for routing - the same `.rg` file - so typing "grand" in
   a canyon with no bars finds Grand Avenue and its coordinates. This is the
   primary search, not a fallback, because "i need everything to work with no
   signal. no exception".
2. **Coordinates, typed or pasted.** `parse_point` takes every shape a phone
   hands you: "37.8044, -122.2712", a maps URL with an `@lat,lon` or a `?q=`,
   a `geo:` URI from a share sheet, and degrees-minutes-seconds. Offline by
   definition.
3. **A route from the server.** `db_routes` is her saved trips and recorded
   rides - "ABILITY TO PULL 'RIDES/LEGS/ROUTES/SECTIONS' FROM THE DB LIST OF
   ROUTES". Served through Washi's store, which answers from this device's
   cached copy when the server is unreachable, so a list she has seen once is
   a list she still has.
4. **A named place or a category of POI, online.** `search_named` and
   `search_pois` need signal and say so. They are last because they are the
   only two that can fail for a reason the rider cannot fix from the saddle.

GPX IMPORT

`parse_gpx` reads a track or a route out of a GPX file - "ABILITY TO IMPORT
SPECIFIC 'RIDES'/'ROUTES'" - with a deliberately forgiving parser: it takes
`trkpt` and `rtept` and `wpt`, in that order of preference, because exports
from different tools disagree about which one a planned route lives in, and
refusing a file over that distinction would be useless pedantry.
"""
import bisect
import math
import re
import sqlite3
import traceback
import unicodedata
import urllib.parse
from pathlib import Path

from agedashii_washi import api as M
from agedashii_washi import routing

#: Overpass categories offered for the POI search, as (label, OSM filter).
#: Short list on purpose: this is done at a stop, gloved, on a phone.
POI_CATEGORIES = [
    ("Fuel", 'node["amenity"="fuel"]'),
    ("Coffee", 'node["amenity"="cafe"]'),
    ("Food", 'node["amenity"="restaurant"]'),
    ("Water", 'node["amenity"="drinking_water"]'),
    ("Toilets", 'node["amenity"="toilets"]'),
    ("Viewpoint", 'node["tourism"="viewpoint"]'),
    ("Camp", 'node["tourism"="camp_site"]'),
    ("Motorcycle", 'node["shop"="motorcycle"]'),
]

#: Public Overpass mirrors, tried in order. Two of the three are usually busy,
#: which is why there is more than one and why a failure names the reason.
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter")

#: A decimal degree pair, with an optional sign and either separator.
_PAIR = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)")
#: Degrees/minutes/seconds with a hemisphere letter.
_DMS = re.compile(
    r"(\d{1,3})[^\d]+(\d{1,2})[^\d]+([\d.]+)[^\dNSEW]*([NSEW])", re.I)


def parse_point(text):
    """(lat, lon, label) from whatever she pasted, or None.

    Total and offline. Tries the shapes in order of how unambiguous they are,
    so a maps URL that also happens to contain numbers elsewhere in its query
    string does not get read as a bare coordinate pair from the wrong part.
    """
    if not text:
        return None
    raw = text.strip()

    # geo:37.8044,-122.2712 - what an Android share sheet hands over.
    if raw.lower().startswith("geo:"):
        found = _PAIR.search(raw[4:])
        if found:
            return _checked(found.group(1), found.group(2), "shared pin")

    if "http" in raw.lower():
        # A maps URL: the authoritative pair is the one after the @.
        if "@" in raw:
            found = _PAIR.search(raw.split("@", 1)[1])
            if found:
                return _checked(found.group(1), found.group(2), "map link")
        # ?q=lat,lon and its several aliases, the other maps-link shape.
        try:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        except ValueError:
            query = {}
        for key in ("q", "query", "daddr", "destination", "ll"):
            for candidate in query.get(key, []):
                found = _PAIR.search(candidate)
                if found:
                    return _checked(found.group(1), found.group(2), "map link")

    # Degrees, minutes, seconds - two of them, in either order.
    dms = _DMS.findall(raw)
    if len(dms) >= 2:
        first, second = _dms_value(dms[0]), _dms_value(dms[1])
        if first is not None and second is not None:
            lat, lon = first, second
            # A leading E/W means the longitude was written first.
            if dms[0][3].upper() in ("E", "W"):
                lat, lon = second, first
            return _checked(lat, lon, "typed")

    # A bare pair, last, because it matches inside almost anything.
    found = _PAIR.fullmatch(raw) or _PAIR.search(raw)
    if found:
        return _checked(found.group(1), found.group(2), "typed")
    return None


def _dms_value(parts):
    try:
        deg, minutes, secs, hemi = parts
        value = float(deg) + float(minutes) / 60.0 + float(secs) / 3600.0
    except (TypeError, ValueError):
        return None
    return -value if hemi.upper() in ("S", "W") else value


def _checked(lat, lon, label):
    """Reject a pair that is not on Earth rather than routing to nowhere."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    return lat, lon, label


# ---------------------------------------------------- offline street search

#: Cache of graph path -> {lowercase name: segment id}. Built once per graph,
#: because it is a full pass over the segment table.
_NAME_INDEX = {"path": None, "map": None}


def street_index(graph):
    """{lowercased street name: a segment id with that name}.

    One pass over `seg_name`, cached. It holds a segment id rather than a
    coordinate because the coordinate is derived from the segment's geometry
    on demand - keeping a couple of hundred thousand coordinate pairs alive to
    answer a search that happens twice a ride would waste the phone's memory.
    """
    if graph is None:
        return {}
    if _NAME_INDEX["path"] == getattr(graph, "path", None):
        return _NAME_INDEX["map"] or {}
    out = {}
    try:
        for sid in range(graph.s):
            name = graph.name(sid)
            if not name:
                continue
            key = name.lower()
            # First one wins: a long street is many segments and any of them
            # is a usable answer to "where is Grand Avenue".
            if key not in out:
                out[key] = sid
    except (AttributeError, IndexError, UnicodeDecodeError):
        return {}
    _NAME_INDEX["path"] = getattr(graph, "path", None)
    _NAME_INDEX["map"] = out
    return out


def segment_point(graph, sid):
    """A (lat, lon) somewhere along segment `sid`, or None.

    The middle of the stored geometry rather than an end: an end is a
    junction, and routing to the junction of two roads is ambiguous in a way
    that routing to the middle of a block is not.
    """
    try:
        lo, hi = graph.seg_off[sid], graph.seg_off[sid + 1]
        if hi <= lo:
            return None
        mid = (lo + hi) // 2
        # Segment geometry is ABSOLUTE degrees x 1e7, lat then lon - exactly
        # how routing.py reads it (routing.py:618-645, 1122). The origin
        # (olat/olon) and scale (mx/my) belong to the NODE array `xy` only
        # (routing.py:1370-1371). Using them here put every street result at
        # a place like (-10933, 4281): she chose a street and it routed to
        # nowhere.
        return (graph.geom[2 * mid] / 1e7, graph.geom[2 * mid + 1] / 1e7)
    except (AttributeError, IndexError, TypeError, ZeroDivisionError):
        return None


#: What a result row says when the offline router cannot reach it.
OUTSIDE_MAP = "outside the map on this phone"


def mark_outside(rows, graph=None, timeout=20.0):
    """Set `outside` on every row the offline router cannot reach.

    The routing graph was cut to the map tiles' box (her choice, 2026-09-28);
    the places list reaches further, so about a third of it - Santa Cruz,
    Sonoma, Napa - is outside. Routing to one is refused in words; the row
    says so first, before she taps. With no graph loaded nothing is claimed,
    either way. Call from a worker thread, like search_streets.
    """
    g = graph if graph is not None else routing.graph(timeout=timeout)
    if g is None:
        return rows
    for row in rows:
        lat, lon = row.get("lat"), row.get("lon")
        if lat is not None and lon is not None and not g.covers(lat, lon):
            row["outside"] = True
    return rows


def search_streets(text, graph=None, limit=12, near=None, timeout=20.0):
    """Street names matching `text`, from the installed graph. Offline.

    Returns [{name, lat, lon, kind: "street", dist_m}]. `near` sorts by
    distance from a point, which matters a great deal: "main street" exists in
    every town in the graph and the one she wants is the near one.
    """
    needle = (text or "").strip().lower()
    if len(needle) < 2:
        return []
    if graph is None:
        # A real timeout, not zero. This is the one place where waiting for the
        # graph is correct: she has typed a street name and pressed search, so
        # the load is the thing she asked for. It MUST be called from a worker
        # thread - `routing.graph()` starts the load on its way past, and an
        # 86 MB read on the UI thread is a frozen app.
        graph = routing.graph(timeout=timeout)
    index = street_index(graph)
    if not index:
        return []
    # Prefixes first, then anything merely containing the needle: typing "gra"
    # should offer Grand before Vallejo Grange Road.
    starts = [n for n in index if n.startswith(needle)]
    inside = [n for n in index if needle in n and not n.startswith(needle)]
    out = []
    for name in starts + inside:
        point = segment_point(graph, index[name])
        if point is None:
            continue
        row = {"name": graph.name(index[name]), "lat": point[0],
               "lon": point[1], "kind": "street", "detail": "", "dist_m": None}
        if near:
            row["dist_m"] = routing.haversine_m(near, point)
        out.append(row)
        # With no origin to sort by there is nothing to gain from scanning the
        # rest; with one, every candidate has to be measured before the
        # nearest is known.
        if near is None and len(out) >= limit:
            break
    if near:
        out.sort(key=lambda r: r["dist_m"] if r["dist_m"] is not None else 9e9)
    return out[:limit]


# ------------------------------------------------------- the server's list

def db_routes():
    """Her saved trips and recorded rides as one list. From cache when offline.

    Returns (rows, note). `note` is not an error - it is what to tell her about
    where the list came from, because a cached list is worth having and worth
    knowing about.
    """
    rows, missing = [], []
    trips = M.trips()
    if trips is None:
        missing.append("trips")
    else:
        for t in trips:
            count = len(t.get("waypoints") or [])
            rows.append({
                "name": t.get("name") or t.get("id") or "—",
                "kind": "trip", "id": t.get("id", ""),
                # The waypoints travel with the row, so choosing one needs no
                # second request and works with the radio off.
                "points": count,
                "detail": " · ".join(x for x in (
                    str(t.get("mi") or ""), str(t.get("date") or ""),
                    f"{count} waypoints" if count else "") if x)})
    rides = M.rides_list()
    if rides is None:
        missing.append("rides")
    else:
        rows += [{"name": r.get("name") or "—", "kind": "ride",
                  "id": r.get("id", ""), "points": None,
                  "detail": str(r.get("mi") or "")} for r in rides]
    note = f"no signal - {' and '.join(missing)} not loaded" if missing else ""
    return rows, note


def saved_points(row):
    """[(lat, lon)] for a saved trip or ride row, or [].

    The pull half of "all rides and apps should SYNC to the DB both pulls and
    pushes". A trip's waypoints are already in the cached list; a ride's points
    come from `/api/rides/view`, which the store caches like anything else - so
    a route opened once at home can be ridden in a dead zone.
    """
    kind, rid = row.get("kind"), row.get("id")
    if not rid:
        return []
    if kind == "trip":
        return M.trip_points(rid)
    if kind == "ride":
        return M.ride_points(rid)
    return []


# ------------------------------------------------------------- GPX import

# EITHER ORDER. XML attribute order carries no meaning, and plenty of tools
# write `<trkpt lon=".." lat="..">`. This used to demand lat first and returned
# an EMPTY LIST for such a file - not an error, not a warning, just nothing -
# so importing a route from another app looked like an import that did nothing.
_GPX_TAG = re.compile(r"<(?:trkpt|rtept|wpt)\b([^>]*?)/?>", re.I | re.S)
_GPX_LAT = re.compile(r"\blat\s*=\s*[\"'](-?[\d.]+)[\"']", re.I)
_GPX_LON = re.compile(r"\blon\s*=\s*[\"'](-?[\d.]+)[\"']", re.I)
_GPX_NAME = re.compile(r"<name>\s*(.*?)\s*</name>", re.I | re.S)


def parse_gpx(text):
    """(name, points) out of GPX text. `points` is [(lat, lon), ...].

    Forgiving by design - see the module docstring. Returns an empty list
    rather than raising on a file that is not GPX at all, so the caller can say
    "no points in that file" instead of showing a traceback.

    `trkpt` wins over `rtept` wins over `wpt` when a file has more than one
    kind: a file with a recorded track and a handful of waypoints is a recorded
    ride, and the waypoints are annotations on it.
    """
    if not text:
        return "", []
    by_kind = {"trkpt": [], "rtept": [], "wpt": []}
    for match in _GPX_TAG.finditer(text):
        kind = match.group(0)[1:].split(None, 1)[0].rstrip("/>").lower()
        if kind not in by_kind:
            continue
        attrs = match.group(1)
        lat = _GPX_LAT.search(attrs)
        lon = _GPX_LON.search(attrs)
        if not (lat and lon):
            continue
        checked = _checked(lat.group(1), lon.group(1), "gpx")
        if checked:
            by_kind[kind].append((checked[0], checked[1]))
    points = by_kind["trkpt"] or by_kind["rtept"] or by_kind["wpt"]
    found = _GPX_NAME.search(text)
    return (found.group(1) if found else "").strip(), points


# ---------------------------------------------------- offline places search
#
# The road graph holds streets and nothing else, so "Chabot Space and Science
# Center" or "Tilden Regional Park" could never be found offline - and the
# online geocoder is a gazetteer of towns that does not know them either. This
# is a separate list: every named museum, park, cafe, fuel station, peak and
# so on from the same OSM extract as the graph, built off the phone by
# tools/build_places.py and installed beside the graph.

PLACES_FILENAME = "places.sqlite"
#: Words that carry no meaning in a place search ("Chabot Space AND Science").
_STOP_WORDS = frozenset({"and", "the", "of", "at", "on", "in"})
#: A typed word also matches its long or short form: "mt diablo" has to find
#: "Mount Diablo State Park", which is how OSM spells it.
_SAME_WORD = {"mt": ("mount",), "mount": ("mt",), "ft": ("fort",), "fort": ("ft",),
              "pt": ("point",), "point": ("pt",)}
#: Words shorter than this are ignored. One letter prefixes a quarter of the
#: list and took 356-485 ms on the Pi; two letters take ~78 ms.
MIN_WORD = 2
#: SQLite's older default limit on bound parameters, so an IN (...) never trips it.
_IN_CHUNK = 900


def norm_tokens(text):
    """Lowercase words, accents stripped: "Café & Bar" -> ["cafe", "bar"].

    The ONE definition: tools/build_places.py imports this to build the
    index, so what is stored and what is searched can never be split
    differently.
    """
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return [t for t in re.split(r"[^0-9a-z]+", s) if t]


def places_path():
    """The installed places list, or None. It lives beside the road graph:
    a destination you can find but not route to, or the reverse, is half a
    search, so the two are found together."""
    graph = routing.graph_path()
    if graph is None:
        return None
    path = Path(graph).with_name(PLACES_FILENAME)
    return str(path) if path.is_file() else None


def places_status(path=None):
    """(count, one line for the screen) - whether the offline list is usable.

    Shown on Where-to before any search, so "not installed" can never pass for
    "no results". The count is COUNTED (count(*) on the rows), not taken from
    the file's own claim, and the two must agree: a line saying "found" can be
    true while a truncated list holds four rows. count is None when the list
    cannot be used, and the words say what that means for her, not which file
    is missing.
    """
    path = path or places_path()
    if path is None:
        return None, ("Places search is not on this phone - without signal only "
                      "street names can be found. A newer build of the app includes it.")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = int(con.execute("SELECT count(*) FROM place").fetchone()[0])
            claimed = int(con.execute("SELECT value FROM meta WHERE key = 'count'").fetchone()[0])
        finally:
            con.close()
    except Exception:                                  # noqa: BLE001 - said on screen AND in the log
        traceback.print_exc()
        return None, ("The places list on this phone could not be read - "
                      "reinstalling the app replaces it.")
    if rows == 0 or rows != claimed:
        print(f"[navi] places list damaged: {rows} rows, file says {claimed}", flush=True)
        return None, ("The places list on this phone is damaged - "
                      "reinstalling the app replaces it.")
    return rows, f"{rows:,} places on this phone, searchable without signal"


# RANKING. A place's cost is how well its name answers the words (below) plus
# how far away it is; lowest first. Distance counts DIST_WEIGHT per doubling of
# (1 + km), and the name score is clamped to Q_RANGE, whose width (5.5) is
# less than the distance cost between 1 km and 100 km (1.5 x 5.66 = 8.5) - so
# on a motorcycle, where near is the point, 100 km can never beat 1 km for the
# same words. Within a similar distance, the name decides: the beach before the
# Pacifica Beach HOTEL, IKEA before the IKEA parking garage 100 m nearer.
DIST_WEIGHT = 1.5
Q_RANGE = (-2.5, 3.0)
NOT_EXACT = 1.0      # the name is more than exactly the words typed
KIND_HIT = 1.5       # a typed word says what it IS: "pacifica BEACH" -> natural=beach
AREA_WORD = 1.0      # a word matched only the town/neighbourhood (weaker than the name)
KIND_WORD = 0.5      # a word matched only the kind ("chabot museum")
EXTRA_WORD = 0.1     # each name word she did not type - a tie-break, no more
ALT_WORD = 0.75      # a word found only in an ALTERNATIVE name: the Spees Building's
                     # official name mentions Chabot; the museum is what she means
SIZE = 0.25          # per tenfold of area in m2: a big park before a kiosk
NOTABLE = 0.5        # has a Wikidata/Wikipedia entry
#: A town is stored as one point but stands for the whole town, so "berkeley"
#: means Berkeley before "Berkeley Yeast". Sizes in m2, treated as its area.
TOWN_SIZE = {"city": 1e8, "town": 1e7, "village": 1e6}
#: Parts of a place rather than places: a car park, a taxi rank, a plain named
#: building. They rank below the place they serve and get no credit for size -
#: the Chabot parking garage outranked the museum on its outline alone.
PART_OF = {"parking", "parking space", "taxi", "building", "bicycle parking", "motorcycle parking"}
PART_OF_COST = 1.0


def _prefix(con, table, column, word):
    """Values of `column` whose `table.t` begins with the word or its long/short form."""
    out = set()
    for form in (word,) + _SAME_WORD.get(word, ()):
        out.update(r[0] for r in con.execute(
            f"SELECT {column} FROM {table} WHERE t >= ? AND t < ?", (form, form + "\uffff")))
    return out


def search_places(text, near=None, limit=12, path=None):
    """(rows, how many places the list holds) for a named place. Offline.

    Every word typed must begin a word of the place's name, its kind or its
    town/neighbourhood, and at least one must be in the NAME - so "berkeley"
    alone does not return every place in Berkeley, and "ikea" never returns a
    shop that is not called IKEA. "berkeley stadium" finds California Memorial
    Stadium: "stadium" is in its name, "berkeley" is its town. Ranked by
    distance and the weights above. A row's detail is its kind and town
    ("furniture · Emeryville"), so two IKEAs can be told apart. The count is 0
    when no list is installed, so the screen can say WHY nothing was found.

    Scored nearest-first, stopping once no remaining place can make the list:
    the name score is bounded by Q_RANGE, so a place whose distance alone
    already costs more than the last kept result cannot beat it. Same answer as
    scoring everything; a 2-letter search stops paying for thousands of rows.
    """
    path = path or places_path()
    if path is None:
        return [], 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        total = int(con.execute("SELECT value FROM meta WHERE key = 'count'").fetchone()[0])
        words = [w for w in norm_tokens(text) if w not in _STOP_WORDS and len(w) >= MIN_WORD]
        if not words:
            return [], total
        words = list(dict.fromkeys(words))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        v2 = {"area", "area_tok", "kind_tok"} <= tables     # schema 1: names only
        by_name = [_prefix(con, "tok", "id", w) for w in words]
        kinds_for = [_prefix(con, "kind_tok", "kind", w) if v2 else set() for w in words]
        areas_for = [_prefix(con, "area_tok", "aid", w) if v2 else set() for w in words]
        pool = sorted(set().union(*by_name))           # at least one word is in the name
        cols = "id, name, kind, lat, lon, " + ("area_m2, notable, a1, a2" if v2 else "0, 0, NULL, NULL")
        rows = []
        for i in range(0, len(pool), _IN_CHUNK):
            chunk = pool[i:i + _IN_CHUNK]
            rows += con.execute(f"SELECT {cols} FROM place WHERE id IN ({','.join('?' * len(chunk))})",
                                chunk).fetchall()
        kept = []
        for row in rows:
            pid, kind, a1, a2 = row[0], row[2], row[7], row[8]
            srcs = []
            for i in range(len(words)):
                if pid in by_name[i]:
                    srcs.append("name")
                elif kind in kinds_for[i]:
                    srcs.append("kind")
                elif (a1 is not None and a1 in areas_for[i]) or (a2 is not None and a2 in areas_for[i]):
                    srcs.append("area")
                else:
                    break
            else:
                dist = routing.haversine_m(near, (row[3], row[4])) if near else None
                dcost = DIST_WEIGHT * math.log2(1.0 + dist / 1000.0) if near else 0.0
                kept.append((dcost, dist, row, srcs))
        kept.sort(key=lambda k: k[0])

        # Compare on canonical words, so "mt diablo" scores "Mount Diablo" as exact.
        canon = {"mt": "mount", "ft": "fort", "pt": "point"}
        typed = [canon.get(w, w) for w in words]
        want = " ".join(typed)
        kind_words = {}
        best = []                                       # sorted (cost, name, dist, row)
        for dcost, dist, row, srcs in kept:
            if len(best) >= limit and Q_RANGE[0] + dcost > best[limit - 1][0]:
                break
            pid, name, kind, lat, lon, size, notable, a1, _a2 = row
            have = [canon.get(w, w) for w in norm_tokens(name) if w not in _STOP_WORDS]
            q = 0.0 if " ".join(have) == want else NOT_EXACT
            if kind not in kind_words:
                kind_words[kind] = norm_tokens(kind)
            if any(k.startswith(w) for w in typed for k in kind_words[kind]):
                q -= KIND_HIT
            for w, src in zip(typed, srcs):
                if src == "area":
                    q += AREA_WORD
                elif src == "kind":
                    q += KIND_WORD
                elif not any(h.startswith(w) for h in have):
                    q += ALT_WORD       # in an alternative name only, not the one shown
            q += EXTRA_WORD * sum(1 for h in have if not any(h.startswith(w) for w in typed))
            if kind in PART_OF:
                q += PART_OF_COST
            else:
                q -= SIZE * math.log10(1.0 + max(size or 0.0, TOWN_SIZE.get(kind, 0.0)))
            q -= NOTABLE if notable else 0.0
            q = min(max(q, Q_RANGE[0]), Q_RANGE[1])
            bisect.insort(best, (q + dcost, name, dist, row))
            del best[limit:]
        need = [r[3][7] for r in best if r[3][7] is not None]
        towns = dict(con.execute(f"SELECT aid, name FROM area WHERE aid IN ({','.join('?' * len(need))})",
                                 need).fetchall()) if v2 and need else {}
    finally:
        con.close()
    out = []
    for _cost, name, dist, (pid, _n, kind, lat, lon, *_rest) in best:
        a1 = _rest[2]
        out.append({"name": name, "lat": lat, "lon": lon, "kind": "poi",
                    "detail": " · ".join(x for x in (kind, towns.get(a1)) if x), "dist_m": dist})
    return out, total


# ------------------------------------------------------ online, and said so

def search_named(text, near=None, limit=8):
    """(rows, error) for a named place. Needs signal.

    Open-Meteo's keyless geocoder, through Washi so the timeout and the error
    text are the same as everywhere else in the family.
    """
    places, err = M.geocode(text, count=limit)
    if err:
        return [], err
    rows = []
    for place in places or []:
        row = {"name": place["name"], "lat": place["lat"], "lon": place["lon"],
               "kind": "place", "dist_m": None,
               "detail": " · ".join(x for x in (place.get("admin1"),
                                                place.get("country")) if x)}
        if near:
            row["dist_m"] = routing.haversine_m(near, (row["lat"], row["lon"]))
        rows.append(row)
    if near:
        rows.sort(key=lambda r: r["dist_m"] if r["dist_m"] is not None else 9e9)
    return rows, None


def poi_query(filters, lat, lon, radius_m):
    """The Overpass QL for a set of category filters around a point."""
    body = "".join(f"{f}(around:{int(radius_m)},{lat:.5f},{lon:.5f});"
                   for f in filters)
    return f"[out:json][timeout:25];({body});out center;"


def search_pois(categories, lat, lon, radius_m=8000, timeout=25):
    """(rows, error) for POI categories around a point. Needs signal.

    Her "WATUDO VIEW OF POI AS 'SEARCH' FEATURE TO FIND NEW
    DESTINATION/LOCATIONS IN AREAS." Asks one mirror at a time and takes the
    first answer; a busy mirror is the normal case rather than an outage, so
    the error only comes back once every mirror has refused.
    """
    wanted = set(categories or ())
    filters = [f for label, f in POI_CATEGORIES if label in wanted]
    if not filters:
        return [], "pick a category first"
    query = poi_query(filters, lat, lon, radius_m)
    last = "no mirror answered"
    for url in OVERPASS:
        data, err = M._third_party(f"{url}?data={urllib.parse.quote(query)}",
                                   timeout)
        if err:
            last = err
            continue
        rows = []
        for element in (data or {}).get("elements", []):
            point = element.get("center") or element
            plat, plon = point.get("lat"), point.get("lon")
            if plat is None or plon is None:
                continue
            tags = element.get("tags") or {}
            rows.append({
                "name": (tags.get("name") or tags.get("amenity")
                         or tags.get("shop") or "unnamed"),
                "lat": float(plat), "lon": float(plon), "kind": "poi",
                "detail": (tags.get("amenity") or tags.get("tourism")
                           or tags.get("shop") or ""),
                "dist_m": routing.haversine_m(
                    (lat, lon), (float(plat), float(plon)))})
        rows.sort(key=lambda r: r["dist_m"])
        return rows, None
    return [], last
