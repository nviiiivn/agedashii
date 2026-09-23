"""Data and pure logic behind the Trips page, split out so it can be tested.

`pages/trips.py` and `pages/xnav.py` are Flet control trees; nothing in them
can be asserted on without a running app. Everything here is either a plain
function over plain data or one clearly-marked network call, which is what
lets the arithmetic that decides what a card says be checked in `tests/`.

Two rules run through the whole file, and both come from the same place -
the website and the app write to the same JSON files on the same server:

**The shapes are the website's, key for key.** `report` is
``{"route": [...], "waypoints": {"0": [...]}}``; a waypoint is
``{name, lat, lon, note}``; a saved trip carries `distance_mi` and
`duration_min`, never `miles` or `date`. A key spelled differently here is
a field that reads as blank in the browser, which is how you lose an
afternoon's tagging without being told.

**A live third party is never queued.** Valhalla and Overpass answer or they
do not; there is nothing to replay later. Every function that needs one
returns a reason when the server is unreachable instead of parking a write
that can never be delivered. Only `/api/trips/save` and `/api/rides/upload`
- our own server, our own data - go through the outbox.
"""
import json
import math
import time
import urllib.parse
import urllib.request

import flet as ft

from agedashii_washi import api as M
from agedashii_washi import config

from .. import units as _units

#: Vulcan Lofts. Byte-for-byte the website's `TRIPS_HOME`, because it is
#: written into every saved trip and a second spelling would fork the data.
HOME = {"name": "Home — Vulcan Lofts (4401 San Leandro)",
        "lat": 37.769972, "lon": -122.216445}
HOME_LATLON = (HOME["lat"], HOME["lon"])

OFFLINE_ROUTE = ("no connection - routing needs Valhalla on the server. "
                 "The two-point planner in Selekt still works offline.")
OFFLINE_OSM = ("no connection - this reads OpenStreetMap live and there is "
               "nothing to queue.")


# --------------------------------------------------------------- transport

def _post(path, payload, timeout=30, raw=None, headers=None):
    """POST to the dashbored server. Returns (data, error_text).

    Not `api._post`, which answers None for every kind of failure: on this
    page the difference between "the phone has no signal" and "Valhalla is
    down" is the difference between two completely different things to tell
    the rider.
    """
    body = raw.encode() if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(
        config.SERVER_URL + path, data=body, method="POST",
        headers=headers or {"Content-Type": "application/json"})
    try:
        with config._opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
    except Exception as exc:                            # noqa: BLE001
        return None, M._describe_failure(exc)
    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    return data, None


def _get(path, timeout=30):
    """GET straight off the server, bypassing the read cache.

    The cache is right for `/api/trips`, which is ours and changes rarely.
    It is wrong for an Overpass bbox, which is a different question every
    time and would otherwise fill the store with single-use answers.
    """
    req = urllib.request.Request(config.SERVER_URL + path, method="GET")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
    except Exception as exc:                            # noqa: BLE001
        return None, M._describe_failure(exc)
    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    return data, None


# -------------------------------------------------------------- trips data

def trips_all():
    """Every saved trip, whole, or None. Cached and offline-readable."""
    d = M._get("/api/trips", timeout=8)
    return d if isinstance(d, list) else None


def rides_all():
    """The recorded GPX list, or None."""
    d = M._get("/api/rides", timeout=8)
    if not isinstance(d, dict):
        return None
    out = d.get("rides")
    return out if isinstance(out, list) else []


def ride_view(ride_id):
    """One ride's points and stats, or None. The server parses the GPX."""
    d = M._get("/api/rides/view?id=" + urllib.parse.quote(ride_id), timeout=20)
    if not isinstance(d, dict) or d.get("error"):
        return None
    return d


def gpx_url(trip_id):
    """Where the server will render this trip as GPX."""
    return (config.SERVER_URL + "/api/trips/gpx?id="
            + urllib.parse.quote(trip_id))


# ------------------------------------------------------------ family groups
# The website hard-codes these two families by trip id and files everything
# else under "standalone". Copied rather than inferred: inferring from
# `fork_origin` would regroup her trips the first time she edits one, and
# the grouping is hers, not a derived fact.

BERKELEY_FAMILY = ["berkeley-hills-ridge", "berkeley-hills-north",
                   "berkeley-hills-south", "san-pablo-briones", "chabot-loop",
                   "upper-san-leandro-reservoir"]
ALAMEDA_FAMILY = ["alameda-loop", "lake-merritt-addon"]

BERKELEY_LABEL = "Berkeley Hills family — fork-spine + sub-loops + reservoir"
ALAMEDA_LABEL = "Alameda family — island loop + Lake Merritt add-on"
OTHER_LABEL = "Waypoints & standalone legs"


def group_by_family(all_trips):
    """[(label, [trip, ...]), ...] in the website's order. Empty groups drop."""
    trips = [t for t in (all_trips or []) if isinstance(t, dict)]

    def name_of(t):
        return str(t.get("name") or t.get("id") or "")

    berkeley = sorted([t for t in trips if t.get("id") in BERKELEY_FAMILY],
                      key=lambda t: (t.get("id") != "berkeley-hills-ridge",
                                     name_of(t)))
    alameda = sorted([t for t in trips if t.get("id") in ALAMEDA_FAMILY],
                     key=lambda t: (t.get("id") != "alameda-loop", name_of(t)))
    used = {t.get("id") for t in berkeley} | {t.get("id") for t in alameda}
    others = sorted([t for t in trips if t.get("id") not in used], key=name_of)

    out = []
    if berkeley:
        out.append((BERKELEY_LABEL, berkeley))
    if alameda:
        out.append((ALAMEDA_LABEL, alameda))
    if others:
        out.append((OTHER_LABEL, others))
    return out


# ------------------------------------------------------------- waypoint work

def waypoints_of(trip):
    """The drawable waypoints of a trip: dicts with real lat and lon."""
    out = []
    for w in (trip.get("waypoints") or []):
        if not isinstance(w, dict):
            continue
        lat, lon = w.get("lat"), w.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            out.append(w)
    return out


def _is_home(w):
    return "vulcan" in str(w.get("name") or "").lower()


def home_wrap(leg):
    """The website's `homeWrapWaypoints`: a leg routed as a usable ride.

    A fork child already starts somewhere deliberate, so only the way home
    is added. Everything else gets Home at both ends, because a leg on its
    own is a fragment and routing a fragment gives a distance that means
    nothing next to the others.
    """
    wps = [dict(w) for w in waypoints_of(leg)]
    if not wps:
        return wps
    if leg.get("fork_origin"):
        if not _is_home(wps[-1]):
            wps.append(dict(HOME, note="Return home"))
        return wps
    if not _is_home(wps[0]):
        wps.insert(0, dict(HOME, note="Start"))
    if not _is_home(wps[-1]):
        wps.append(dict(HOME, note="Return home"))
    return wps


def builder_chain(legs):
    """Home → every leg's waypoints → Home, with duplicate stops collapsed.

    One chained route, not the sum of per-leg round trips: stacking three
    legs that each start and end at home would otherwise report a ride three
    times longer than the one she would actually take.
    """
    chain = [dict(HOME, note="Start")]
    for leg in (legs or []):
        for w in waypoints_of(leg):
            chain.append(dict(w, leg=leg.get("id"), leg_name=leg.get("name")))
    if not _is_home(chain[-1]):
        chain.append(dict(HOME, note="Return home"))
    dedup = []
    for w in chain:
        prev = dedup[-1] if dedup else None
        if (prev is None or abs(prev["lat"] - w["lat"]) > 1e-6
                or abs(prev["lon"] - w["lon"]) > 1e-6):
            dedup.append(w)
    return dedup


def close_loop_leg(legs, now=None):
    """A synthetic leg returning to the first waypoint, or (None, reason)."""
    if not legs:
        return None, "nothing stacked yet"
    first = waypoints_of(legs[0])
    if not first:
        return None, "the first leg has no waypoints"
    last = legs[-1]
    if last.get("_close_loop"):
        return None, "loop already closed"
    stamp = int(now if now is not None else time.time())
    return {"id": f"_closeloop_{stamp}", "name": "⟲ Return to start",
            "type": "leg", "_close_loop": True,
            "waypoints": [dict(first[0], note="Close loop — back to start")]}, ""


def slugify(text):
    """The website's id rule: lowercase, non-alphanumerics to single dashes."""
    out = []
    for ch in str(text or "").lower():
        out.append(ch if ch.isalnum() else "-")
    return "-".join(part for part in "".join(out).split("-") if part)


# ------------------------------------------------------------- trip reports
# A report entry is {"label": str, "note": str}. They hang off either the
# whole route or a waypoint index, which is why the container is a dict with
# a list and a map in it rather than the flat list some older trips still
# have on disk.

def normalise_report(report):
    """Any shape a trip on disk might carry -> {"route": [], "waypoints": {}}."""
    if isinstance(report, list):
        return {"route": [dict(r) for r in report if isinstance(r, dict)],
                "waypoints": {}}
    if not isinstance(report, dict):
        return {"route": [], "waypoints": {}}
    route = report.get("route")
    wps = report.get("waypoints")
    out_wps = {}
    if isinstance(wps, dict):
        for k, v in wps.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, list):
                out_wps[idx] = [dict(r) for r in v if isinstance(r, dict)]
    return {"route": [dict(r) for r in (route or []) if isinstance(r, dict)],
            "waypoints": out_wps}


def report_for_save(report):
    """Back to the on-disk shape: waypoint keys are strings in JSON."""
    norm = normalise_report(report)
    return {"route": [dict(r) for r in norm["route"]],
            "waypoints": {str(k): [dict(r) for r in v]
                          for k, v in sorted(norm["waypoints"].items()) if v}}


def flatten_report(trip):
    """Every distinct label on a trip, with where it came from.

    Deduplicated by label the way the card does, so a road tagged bumpy at
    four waypoints shows one badge rather than four.
    """
    norm = normalise_report(trip.get("report"))
    rows = []
    for r in norm["route"]:
        rows.append({"label": r.get("label", ""), "note": r.get("note", ""),
                     "scope": "route"})
    for idx in sorted(norm["waypoints"]):
        for r in norm["waypoints"][idx]:
            rows.append({"label": r.get("label", ""), "note": r.get("note", ""),
                         "scope": f"wp {idx + 1}"})
    seen, out = set(), []
    for row in rows:
        if row["label"] and row["label"] not in seen:
            seen.add(row["label"])
            out.append(row)
    return out


def delete_report_index(report, idx):
    """Drop waypoint `idx`'s tags and shift every later index down one."""
    norm = normalise_report(report)
    rebuilt = {}
    for k in sorted(norm["waypoints"]):
        if k == idx:
            continue
        rebuilt[k - 1 if k > idx else k] = norm["waypoints"][k]
    norm["waypoints"] = rebuilt
    return norm


def move_report_index(report, old, new):
    """Follow a waypoint that moved from position `old` to position `new`."""
    norm = normalise_report(report)
    rebuilt = {}
    for k in sorted(norm["waypoints"]):
        nk = k
        if k == old:
            nk = new
        elif old < new and old < k <= new:
            nk = k - 1
        elif old > new and new <= k < old:
            nk = k + 1
        rebuilt[nk] = norm["waypoints"][k]
    norm["waypoints"] = rebuilt
    return norm


# The website's 27 presets, label for label - the label is what lands in the
# JSON, so a rewording here would orphan every tag already saved. Lucide
# icons become their nearest Material equivalent; the icon is decoration and
# is not written to disk.
TAG_ICONS = {
    "git-fork": ft.Icons.CALL_SPLIT, "tornado": ft.Icons.CYCLONE,
    "rabbit": ft.Icons.PETS, "wind": ft.Icons.AIR,
    "minimize-2": ft.Icons.COMPRESS, "maximize-2": ft.Icons.EXPAND,
    "sparkles": ft.Icons.AUTO_AWESOME, "droplets": ft.Icons.WATER_DROP,
    "users": ft.Icons.GROUPS, "camera": ft.Icons.PHOTO_CAMERA,
    "tree-pine": ft.Icons.PARK, "alert-octagon": ft.Icons.DANGEROUS,
    "circle-dot": ft.Icons.BLUR_ON, "trending-up": ft.Icons.TRENDING_UP,
    "thermometer": ft.Icons.THERMOSTAT, "snowflake": ft.Icons.AC_UNIT,
    "cloud-rain": ft.Icons.UMBRELLA, "car-front": ft.Icons.DIRECTIONS_CAR,
    "signal-zero": ft.Icons.SIGNAL_CELLULAR_OFF,
    "shield-alert": ft.Icons.LOCAL_POLICE, "eye-off": ft.Icons.VISIBILITY_OFF,
    "mountain": ft.Icons.TERRAIN, "smile": ft.Icons.SENTIMENT_SATISFIED,
    "tag": ft.Icons.LABEL,
}

PRESET_TAGS = [
    ("small switchbacks", "git-fork", "good", "attribute"),
    ("long switchbacks", "git-fork", "good", "attribute"),
    ("fun curves", "tornado", "good", "attribute"),
    ("higher speed curving", "rabbit", "good", "attribute"),
    ("sweepers", "wind", "good", "attribute"),
    ("tight road", "minimize-2", "info", "attribute"),
    ("wide road", "maximize-2", "info", "attribute"),
    ("super clean road", "sparkles", "good", "attribute"),
    ("slippery when wet", "droplets", "warn", "attribute"),
    ("popular moto route", "users", "good", "attribute"),
    ("scenic", "camera", "good", "attribute"),
    ("shady", "tree-pine", "info", "attribute"),
    ("bumpy", "wind", "warn", "warning"),
    ("heavy potholage", "alert-octagon", "danger", "warning"),
    ("gravel", "circle-dot", "warn", "warning"),
    ("narrow", "minimize-2", "warn", "warning"),
    ("steep", "trending-up", "warn", "warning"),
    ("hot", "thermometer", "warn", "warning"),
    ("cold", "snowflake", "warn", "warning"),
    ("wet", "cloud-rain", "warn", "warning"),
    ("traffic", "car-front", "warn", "warning"),
    ("no cell", "signal-zero", "warn", "warning"),
    ("cops", "shield-alert", "danger", "bolo"),
    ("deer", "rabbit", "danger", "bolo"),
    ("blind curves", "eye-off", "warn", "bolo"),
    ("cliffs", "mountain", "danger", "bolo"),
    ("fun", "smile", "good", "bolo"),
]

TAG_CATEGORIES = [("attribute", "Attribute — street facts"),
                  ("warning", "Warning — conditions"),
                  ("bolo", "BOLO — situational alerts")]


def default_tags():
    return [{"label": lb, "icon": ic, "severity": sv, "cat": ct, "custom": False}
            for lb, ic, sv, ct in PRESET_TAGS]


def all_tags():
    """Presets plus whatever custom tags she has added. Presets are immutable."""
    from agedashii_washi import prefs
    tags = default_tags()
    known = {t["label"] for t in tags}
    for label, severity, cat in prefs.trip_tags():
        if label not in known:
            known.add(label)
            tags.append({"label": label, "icon": "tag", "severity": severity,
                         "cat": cat, "custom": True})
    return tags


def find_tag(label):
    for t in all_tags():
        if t["label"] == label:
            return t
    return {"label": label, "icon": "tag", "severity": "info", "cat": "attribute",
            "custom": False}


def tag_icon(name):
    return TAG_ICONS.get(name, ft.Icons.LABEL)


def severity_colour(severity, palette):
    return {"good": palette["green"], "warn": palette["yellow"],
            "danger": palette["pink"]}.get(severity, palette["cyan"])


def add_report_entries(report, scopes, labels, note=""):
    """Append each label to every selected scope. Returns the new report."""
    norm = normalise_report(report)
    for scope in scopes:
        target = (norm["route"] if scope == "route"
                  else norm["waypoints"].setdefault(int(scope), []))
        for label in labels:
            target.append({"label": label, "note": note})
    return norm


def remove_report_entries(report, scopes, label, note=""):
    """Drop every (label, note) match from the selected scopes."""
    norm = normalise_report(report)
    for scope in scopes:
        if scope == "route":
            norm["route"] = [r for r in norm["route"]
                             if (r.get("label"), r.get("note", "")) != (label, note)]
        else:
            key = int(scope)
            kept = [r for r in norm["waypoints"].get(key, [])
                    if (r.get("label"), r.get("note", "")) != (label, note)]
            if kept:
                norm["waypoints"][key] = kept
            else:
                norm["waypoints"].pop(key, None)
    return norm


def report_rows(report, scopes):
    """The union of entries across `scopes`, each tagged with where it lives."""
    norm = normalise_report(report)
    seen = {}
    order = []
    for scope in scopes:
        entries = (norm["route"] if scope == "route"
                   else norm["waypoints"].get(int(scope), []))
        for r in entries:
            key = (r.get("label", ""), r.get("note", ""))
            if key not in seen:
                seen[key] = {"label": key[0], "note": key[1], "scopes": []}
                order.append(key)
            if scope not in seen[key]["scopes"]:
                seen[key]["scopes"].append(scope)
    return [seen[k] for k in order]


def scope_label(scopes):
    if scopes == ["route"]:
        return "whole-route"
    if len(scopes) == 1:
        return f"wp {int(scopes[0]) + 1}"
    return f"{len(scopes)} wps ({','.join(str(int(s) + 1) for s in scopes)})"


# ------------------------------------------------------------ save payloads

def editor_payload(orig, name, waypoints, report, forks_at="", fork_origin="",
                   stats=None, is_new=False):
    """What Produce writes. Mirrors the website's `teSave`, field for field."""
    orig = orig if isinstance(orig, dict) else {}
    stats = stats or {}
    trip_id = orig.get("id") or ""
    if not trip_id:
        base = slugify(name) or "route"
        trip_id = f"{base}-{_short_stamp()}"
    out = dict({} if is_new else orig)
    out.update({
        "id": trip_id,
        "name": name,
        "type": orig.get("type") or "loop",
        "waypoints": [dict(w) for w in waypoints],
        "report": report_for_save(report),
        "tags": list(orig.get("tags") or []),
        "distance_mi": stats.get("distance_mi", orig.get("distance_mi", 0)),
        "duration_min": stats.get("duration_min", orig.get("duration_min", 0)),
    })
    forks_at = (forks_at or "").strip()
    fork_origin = (fork_origin or "").strip()
    if forks_at:
        out["forks_at"] = forks_at
    else:
        out.pop("forks_at", None)
    if fork_origin:
        out["fork_origin"] = fork_origin
    else:
        out.pop("fork_origin", None)
    return out


def _short_stamp():
    """The website's `Date.now().toString(36).slice(-4)`, same alphabet."""
    n = int(time.time() * 1000)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out[-4:]


def builder_payload(legs, name, trip_id, leg_stats=None, combined=None):
    """What Save trip writes. The website's `saveTripDialog`, shape for shape.

    One deliberate difference in the *value* of two fields, not their names.
    The website writes the sum of the per-leg routes, and each of those is
    home-wrapped into its own round trip - stack three legs and it saves a
    distance about three times the ride she would take. The chained route
    through all of them is the one that was actually measured, so that is
    what goes in when it exists. The per-leg sum is the fallback, and 0 the
    last resort: a number nobody measured is not written as though it were.
    """
    leg_stats = leg_stats or {}
    waypoints = []
    first = waypoints_of(legs[0]) if legs else []
    if not first or "Vulcan" not in str(first[0].get("name") or ""):
        waypoints.append(dict(HOME, note="Start"))
    for i, leg in enumerate(legs):
        for w in waypoints_of(leg):
            waypoints.append(dict(w, leg=i + 1, leg_name=leg.get("name")))
    if not waypoints or "Vulcan" not in str(waypoints[-1].get("name") or ""):
        waypoints.append(dict(HOME, note="Return home"))

    if combined and combined.get("ok"):
        total_mi = combined.get("distance_mi") or 0
        total_min = combined.get("duration_min") or 0
    else:
        total_mi = total_min = 0.0
        for leg in legs:
            st = leg_stats.get(leg.get("id"))
            if st and st.get("ok"):
                total_mi += st.get("distance_mi") or 0
                total_min += st.get("duration_min") or 0

    def joined(field):
        return " → ".join(str(t[field]) for t in legs if t.get(field))

    return {
        "id": trip_id, "name": name, "type": "loop",
        "distance_mi": round(total_mi * 10) / 10,
        "duration_min": int(round(total_min)),
        "difficulty": next((t["difficulty"] for t in legs if t.get("difficulty")),
                           "mixed"),
        "biome": joined("biome"),
        "vibe": " ".join(str(t["vibe"]) for t in legs if t.get("vibe")),
        "surface": " / ".join(str(t["surface"]) for t in legs if t.get("surface")),
        "curvy_vs_straight": next((t["curvy_vs_straight"] for t in legs
                                   if t.get("curvy_vs_straight")), "mixed"),
        "addons": [],
        "tags": _unique([tg for t in legs for tg in (t.get("tags") or [])]),
        "start": dict(HOME),
        "waypoints": waypoints,
        "legs": [t.get("id") for t in legs],
        "notes": "\n\n".join(f"Leg {i + 1}: {t.get('name', '')}\n{t.get('notes') or ''}"
                             for i, t in enumerate(legs)),
    }


def _unique(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- routing
# The route the cards, the builder and the Produce preview all draw. This is
# the website's `valhallaRoute`, using the app's own decoder and its own
# online check so it can say *why* rather than spinning.

def valhalla_route(waypoints, costing="motorcycle"):
    """{ok, distance_mi, duration_min, ascent_ft, descent_ft, points,
    segments, maneuvers, error}. Never raises."""
    from agedashii_washi import routing as R
    pts = [(w["lat"], w["lon"]) for w in waypoints
           if isinstance(w, dict) and isinstance(w.get("lat"), (int, float))
           and isinstance(w.get("lon"), (int, float))]
    if len(pts) < 2:
        return {"ok": False, "error": "need at least two waypoints"}
    if not M.STATUS_LIVE():
        return {"ok": False, "error": OFFLINE_ROUTE}
    payload = {
        "costing": costing,
        "costing_options": {costing: {"use_hills": 1.0, "use_primary": 0.5,
                                      "use_trails": 0.5}},
        "directions_options": {"units": "miles"},
        "locations": [{"lat": a, "lon": b} for a, b in pts],
    }
    data, err = _post("/api/valhalla/route", payload, timeout=90)
    if err is not None:
        return {"ok": False, "error": err}
    return parse_route(data, R)


def parse_route(data, R=None):
    """Valhalla's /route JSON -> the dict the page draws. Pure."""
    if R is None:
        from agedashii_washi import routing as R          # pragma: no cover - import shim
    trip = (data or {}).get("trip") if isinstance(data, dict) else None
    if not isinstance(trip, dict) or not trip.get("legs"):
        return {"ok": False, "error": "the router found no route"}
    summary = trip.get("summary") or {}
    points, segments, maneuvers = [], [], []
    ascent = descent = 0.0
    have_elev = False
    idx = 0
    for leg in trip["legs"]:
        if not isinstance(leg, dict):
            continue
        shape = R.decode_polyline6(leg.get("shape"))
        points.extend(shape)
        lsum = leg.get("summary") or {}
        # Valhalla's plain /route carries no elevation unless it was asked
        # for, so an absent figure stays absent rather than being reported
        # as a flat zero for a ridge ride.
        if isinstance(lsum.get("elevation"), (int, float)):
            ascent += lsum["elevation"]
            have_elev = True
        if isinstance(lsum.get("elevation_change"), (int, float)):
            have_elev = True
            if lsum["elevation_change"] < 0:
                descent += abs(lsum["elevation_change"])
        for m in (leg.get("maneuvers") or []):
            if not isinstance(m, dict):
                continue
            begin = idx + int(m.get("begin_shape_index") or 0)
            end = idx + int(m.get("end_shape_index") or 0)
            length_mi = float(m.get("length") or 0.0)
            secs = float(m.get("time") or 0.0)
            segments.append({
                "start": begin, "end": end,
                "mph": round(length_mi / secs * 3600) if secs > 0 else None,
                "street": " / ".join(m.get("street_names") or []),
                "length_mi": length_mi, "time_s": secs,
                "instruction": m.get("instruction") or "",
            })
            maneuvers.append({
                "instruction": m.get("instruction") or "",
                "type": m.get("type"), "length_mi": length_mi,
                "street": " / ".join(m.get("street_names") or []),
                "index": min(begin, max(len(points) - 1, 0)),
            })
        idx += len(shape)
    if len(points) < 2:
        return {"ok": False, "error": "the route came back with no shape"}
    return {
        "ok": True, "points": points, "segments": segments,
        "maneuvers": maneuvers,
        "distance_mi": round(float(summary.get("length") or 0.0) * 10) / 10,
        "duration_min": int(round(float(summary.get("time") or 0.0) / 60)),
        "ascent_ft": round(ascent * 3.28084) if have_elev else None,
        "descent_ft": round(descent * 3.28084) if have_elev else None,
        "error": "",
    }


# -------------------------------------------------------------- elevation

def elevations(points, samples=60):
    """(profile, error). Profile is [{lat, lon, ele_ft}, ...] along `points`.

    Valhalla's /height answers ``{"shape": [...], "height": [m, ...]}`` - a
    parallel array of metres, not an `elevation` key on each shape point.
    Reading it the other way is why the website's elevation panel says "no
    elevation data" on every route.
    """
    if len(points or []) < 2:
        return None, "route too short for a profile"
    if not M.STATUS_LIVE():
        return None, OFFLINE_ROUTE
    sampled = sample_points(points, samples)
    data, err = _post("/api/valhalla/height",
                      {"shape": [{"lat": a, "lon": b} for a, b in sampled]},
                      timeout=30)
    if err is not None:
        return None, err
    heights = data.get("height") if isinstance(data, dict) else None
    if not isinstance(heights, list) or len(heights) != len(sampled):
        return None, "the elevation service answered in a shape we do not know"
    profile = []
    for (lat, lon), h in zip(sampled, heights):
        profile.append({"lat": lat, "lon": lon,
                        "ele_ft": h * 3.28084 if isinstance(h, (int, float)) else None})
    if not any(p["ele_ft"] is not None for p in profile):
        return None, "no elevation data for this route"
    return profile, ""


def sample_points(points, samples=60):
    """At most `samples` points, evenly spaced, last one always kept."""
    n = len(points)
    if n <= samples:
        return list(points)
    step = max(1, n // samples)
    out = [points[i] for i in range(0, n, step)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def profile_climb(profile):
    """(ascent_ft, descent_ft) summed over a profile. 0, 0 for a flat one."""
    up = down = 0.0
    prev = None
    for p in profile or []:
        v = p.get("ele_ft")
        if v is None:
            continue
        if prev is not None:
            if v > prev:
                up += v - prev
            else:
                down += prev - v
        prev = v
    return round(up), round(down)


# ------------------------------------------------------------- speed limits

MAXSPEED_FALLBACK = {"motorway": 65, "trunk": 60, "primary": 45, "secondary": 40,
                     "tertiary": 30, "unclassified": 30, "residential": 25,
                     "service": 15, "living_street": 15, "road": 30}
#: Measured against this server: 0.0001 deg² answers in 270 KB, 0.0064 deg²
#: in 7.9 MB and half a minute. Past this the honest answer is "zoom in".
MAXSPEED_MAX_DEG2 = 0.0025
MAXSPEED_TIMEOUT = 40


def parse_mph(value):
    """OSM's `maxspeed` tag -> mph, or None. '35 mph', '50 km/h', '30'."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    for suffix, scale in (("mph", 1.0), ("km/h", 1 / 1.609344), ("kmh", 1 / 1.609344)):
        if text.endswith(suffix):
            head = text[: -len(suffix)].strip()
            try:
                return round(float(head) * scale) if scale != 1.0 else float(head)
            except ValueError:
                return None
    if text in ("none", "unlimited"):
        return 80.0
    try:
        return float(text)
    except ValueError:
        return None


def maxspeed_colour(mph):
    if mph is None:
        return "#888888"
    if mph <= 15:
        return "#d73027"
    if mph <= 25:
        return "#fc8d59"
    if mph <= 35:
        return "#fee08b"
    if mph <= 45:
        return "#d9ef8b"
    if mph <= 55:
        return "#91cf60"
    return "#1a9850"


def bbox_of(points, pad=0.05):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    dlat = (max(lats) - min(lats)) * pad
    dlon = (max(lons) - min(lons)) * pad
    return (min(lats) - dlat, min(lons) - dlon,
            max(lats) + dlat, max(lons) + dlon)


def bbox_area(bbox):
    s, w, n, e = bbox
    return abs((n - s) * (e - w))


def maxspeed_ways(bbox):
    """(ways, error). Each way is {points, mph, inferred, name, highway}.

    Refuses a bbox too wide for Overpass rather than waiting eighty seconds
    for nothing, which is exactly what the website's button used to do.
    """
    if not M.STATUS_LIVE():
        return None, OFFLINE_OSM
    if bbox_area(bbox) > MAXSPEED_MAX_DEG2:
        return None, ("zoom in to load speed limits — this view is too wide "
                      "for OpenStreetMap to answer")
    s, w, n, e = bbox
    query = f"bbox={s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    data, err = _get("/api/maxspeed?" + query, timeout=MAXSPEED_TIMEOUT)
    if err is not None:
        return None, err
    ways = parse_maxspeed(data)
    if not ways:
        return [], "no speed limits published for this view"
    return ways, ""


def parse_maxspeed(data):
    """Overpass elements -> drawable ways. Pure."""
    out = []
    for el in ((data or {}).get("elements") or []):
        if not isinstance(el, dict) or el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        tags = el.get("tags") or {}
        mph = parse_mph(tags.get("maxspeed"))
        inferred = mph is None
        if inferred:
            mph = MAXSPEED_FALLBACK.get(tags.get("highway"))
        if mph is None:
            continue
        out.append({
            "points": [(g["lat"], g["lon"]) for g in geom
                       if isinstance(g, dict) and "lat" in g and "lon" in g],
            "mph": mph, "inferred": inferred,
            "name": tags.get("name") or tags.get("ref") or tags.get("highway") or "road",
            "highway": tags.get("highway") or "road",
        })
    return [w for w in out if len(w["points"]) >= 2]


def speed_lookup(points, ways, segments=None, snap_deg2=0.0002):
    """A speed limit per shape point: nearest OSM way, else the leg's own pace.

    The website's fallback of a bare 30 for anything unmatched is kept, but
    only after the routed segment's own average has had a go - "30" on a
    freeway is a worse lie than a computed 62.
    """
    out = [None] * len(points)
    flat = []
    for w in ways or []:
        for q in w["points"]:
            flat.append((q[0], q[1], w["mph"]))
    if flat:
        for i, p in enumerate(points):
            best_d, best = float("inf"), None
            for lat, lon, mph in flat:
                d = (p[0] - lat) ** 2 + (p[1] - lon) ** 2
                if d < best_d:
                    best_d, best = d, mph
            if best_d < snap_deg2:
                out[i] = best
    for seg in segments or []:
        for i in range(seg["start"], min(seg["end"], len(out) - 1) + 1):
            if out[i] is None:
                out[i] = seg.get("mph") or 30
    return [30 if v is None else v for v in out]


# ---------------------------------------------------------------- geometry

def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def path_length_m(points):
    return sum(haversine_m(points[i - 1], points[i])
               for i in range(1, len(points or [])))


def corners(points, threshold_deg=30):
    """Indices where the line turns more than `threshold_deg` in one step."""
    out = []
    for i in range(1, len(points or []) - 1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        bearing_in = math.atan2(b[1] - a[1], b[0] - a[0])
        bearing_out = math.atan2(c[1] - b[1], c[0] - b[0])
        turn = abs(bearing_out - bearing_in) * 180 / math.pi
        if turn > 180:
            turn = 360 - turn
        if turn > threshold_deg:
            out.append(i)
    return out


def elevation_delta_ft(points, profile, start=0, window_ft=100.0):
    """Feet of climb in the next `window_ft` from `start`, or None.

    The badge the website shows while riding: how hard the next hundred feet
    of road goes up or down.
    """
    if not profile or len(profile) < 2 or len(points or []) < 2:
        return None
    travelled = 0.0
    first = last = None
    for i in range(start, len(points) - 1):
        pi = min(len(profile) - 1, int(i / len(points) * len(profile)))
        ele = profile[pi].get("ele_ft")
        if ele is not None:
            if first is None:
                first = ele
            last = ele
        travelled += haversine_m(points[i], points[i + 1]) * 3.28084
        if travelled >= window_ft:
            break
    if first is None or last is None:
        return None
    return round(last - first)


# ------------------------------------------------------------------- rides

def speed_colour(mph):
    """Blue → yellow → orange → red across 0..80 mph. The website's ramp."""
    if mph is None:
        return "#888888"
    v = max(0.0, min(80.0, float(mph))) / 80.0
    stops = [(0.0, (49, 54, 149)), (0.33, (255, 232, 144)),
             (0.66, (244, 109, 67)), (1.0, (165, 0, 38))]
    lo, hi = stops[0], stops[-1]
    for i in range(len(stops) - 1):
        if stops[i][0] <= v <= stops[i + 1][0]:
            lo, hi = stops[i], stops[i + 1]
            break
    span = (hi[0] - lo[0]) or 1
    t = (v - lo[0]) / span
    rgb = [int(round(a + (b - a) * t)) for a, b in zip(lo[1], hi[1])]
    return "#%02x%02x%02x" % tuple(rgb)


#: With no timestamps in the GPX there is no real clock, so the replay is
#: paced at a steady 35 mph and the UI says as much rather than presenting a
#: synthesised duration as a measured one.
SYNTHETIC_MPH = 35.0


def ride_timeline(points):
    """(offsets_ms, duration_ms, has_time) for the scrubber.

    Gaps in the timestamps are clamped forward rather than dropped: a GPX
    with one unstamped point in the middle should still scrub.
    """
    pts = points or []
    if len(pts) < 2:
        return [], 0.0, False
    has_time = any(p.get("time_ms") is not None for p in pts)
    out = []
    if has_time:
        t0 = next(p["time_ms"] for p in pts if p.get("time_ms") is not None)
        last = 0.0
        for p in pts:
            t = p.get("time_ms")
            t = last if t is None or t - t0 < 0 else t - t0
            last = t
            out.append(float(t))
    else:
        per_ms = SYNTHETIC_MPH * 0.44704 / 1000.0
        cum = 0.0
        out.append(0.0)
        for p in pts[1:]:
            cum += p.get("dist_m") or 0.0
            out.append(cum / per_ms)
    return out, out[-1], has_time


def ride_head(points, timeline, ms):
    """(lat, lon, index, mph) at `ms` into the replay, interpolated."""
    if not timeline or not points:
        return None
    ms = max(0.0, min(timeline[-1], float(ms)))
    lo, hi = 0, len(timeline) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if timeline[mid] <= ms:
            lo = mid
        else:
            hi = mid - 1
    i = lo
    t0 = timeline[i]
    t1 = timeline[i + 1] if i + 1 < len(timeline) else t0 + 1
    f = (ms - t0) / (t1 - t0) if t1 > t0 else 0.0
    a = points[i]
    b = points[min(i + 1, len(points) - 1)]
    mph = b.get("speed_mph") if b.get("speed_mph") is not None else a.get("speed_mph")
    return (a["lat"] + (b["lat"] - a["lat"]) * f,
            a["lon"] + (b["lon"] - a["lon"]) * f, i, mph)


def fmt_clock(ms):
    secs = max(0, int((ms or 0) / 1000))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_minutes(minutes):
    """'45m', '2h 10m'. The website's `formatDur`."""
    try:
        minutes = max(0, int(round(float(minutes or 0))))
    except (TypeError, ValueError):
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def fmt_ms(ms):
    if not ms:
        return "—"
    secs = int(ms / 1000)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_mtime(epoch_secs):
    """`/api/rides` sends seconds; a raw JS Date on that dates every ride 1970."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(epoch_secs)))
    except (TypeError, ValueError, OSError):
        return "—"


def ride_upload(filename, gpx_text):
    """Queue a GPX upload through the outbox. (ok, message)."""
    if not (gpx_text or "").strip():
        return False, "that file is empty"
    name = filename if filename.lower().endswith(".gpx") else filename + ".gpx"
    res = M._queue_write("/api/rides/upload",
                         json_body={"filename": name, "content": gpx_text},
                         timeout=60, invalidate=("/api/rides",))
    if not res.ok:
        return False, res.message
    return True, (f"uploaded {name}" if not res.queued
                  else f"{name} saved on this device, will sync")


# ---------------------------------------------------------------- POIs
# The website's twenty xNav categories, fragments verbatim. Queried in
# batches of three: Overpass gives up on a wide multi-category query long
# before the proxy does, and two of the three public mirrors are usually
# dead, so a batch that fails must cost one chip and not the whole overlay.

POI_CATS = [
    {"id": "parking", "label": "Parking", "icon": ft.Icons.LOCAL_PARKING,
     "q": "node[amenity=parking];way[amenity=parking];"},
    {"id": "fuel", "label": "Fuel", "icon": ft.Icons.LOCAL_GAS_STATION,
     "q": "node[amenity=fuel];"},
    {"id": "food", "label": "Food", "icon": ft.Icons.RESTAURANT,
     "q": "node[amenity~'^(restaurant|fast_food|pub|bar)$'];"},
    {"id": "coffee", "label": "Coffee", "icon": ft.Icons.LOCAL_CAFE,
     "q": "node[amenity=cafe];"},
    {"id": "icecream", "label": "Ice Cream", "icon": ft.Icons.ICECREAM,
     "q": "node[amenity=ice_cream];"},
    {"id": "toilets", "label": "Restrooms", "icon": ft.Icons.WC,
     "q": "node[amenity=toilets];"},
    {"id": "vending", "label": "Vending", "icon": ft.Icons.LOCAL_CONVENIENCE_STORE,
     "q": "node[amenity=vending_machine];"},
    {"id": "atm", "label": "ATM/Bank", "icon": ft.Icons.ATM,
     "q": "node[amenity~'^(atm|bank)$'];"},
    {"id": "museum", "label": "Museum", "icon": ft.Icons.MUSEUM,
     "q": "node[tourism=museum];"},
    {"id": "attraction", "label": "Attraction", "icon": ft.Icons.PHOTO_CAMERA,
     "q": "node[tourism=attraction];"},
    {"id": "viewpoint", "label": "Viewpoint", "icon": ft.Icons.LANDSCAPE,
     "q": "node[tourism=viewpoint];"},
    {"id": "artwork", "label": "Artwork", "icon": ft.Icons.PALETTE,
     "q": "node[tourism=artwork];"},
    {"id": "graffiti", "label": "Graffiti", "icon": ft.Icons.FORMAT_PAINT,
     "q": "node[artwork_type=graffiti];node[tag=graffiti];"},
    {"id": "gallery", "label": "Gallery", "icon": ft.Icons.IMAGE,
     "q": "node[tourism=gallery];"},
    {"id": "monument", "label": "Monument", "icon": ft.Icons.FLAG,
     "q": "node[historic=monument];node[historic=memorial];"},
    {"id": "shrine", "label": "Wayside Shrine", "icon": ft.Icons.CHURCH,
     "q": "node[historic=wayside_shrine];"},
    {"id": "obs_tower", "label": "Obs Tower", "icon": ft.Icons.CELL_TOWER,
     "q": "node[man_made=tower][\"tower:type\"=observation];"
          "node[man_made=observation_tower];"},
    {"id": "arcade", "label": "Arcade", "icon": ft.Icons.SPORTS_ESPORTS,
     "q": "node[leisure=adult_gaming_centre];"},
    {"id": "nature", "label": "Nature", "icon": ft.Icons.FOREST,
     "q": "way[leisure=nature_reserve];way[boundary=national_park];"},
    {"id": "surveillance", "label": "Surveillance", "icon": ft.Icons.VIDEOCAM,
     "q": "node[man_made=surveillance];"},
]
POI_BY_ID = {c["id"]: c for c in POI_CATS}
POI_BATCH = 3
POI_TIMEOUT = 25


def poi_query(cats, bbox, ql_timeout=15, limit=50):
    """Overpass QL for these categories inside `bbox`.

    The bbox has to scope each statement, not the group: Overpass 504s on
    `(node[amenity=fuel];)(bbox)` through the proxy and answers instantly on
    `node(bbox)[amenity=fuel];`. Getting that backwards is why the website's
    POI chips did nothing for months.
    """
    s, w, n, e = bbox
    box = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    parts = []
    for c in cats:
        q = c["q"].replace("node[", f"node({box})[").replace("way[", f"way({box})[")
        parts.append(q)
    return f"[out:json][timeout:{ql_timeout}];({''.join(parts)});out center {limit};"


def parse_pois(data, label):
    """Overpass elements -> [{lat, lon, name}]. Centres count for ways."""
    out = []
    for el in ((data or {}).get("elements") or []):
        if not isinstance(el, dict):
            continue
        centre = el.get("center") or {}
        lat = el.get("lat", centre.get("lat"))
        lon = el.get("lon", centre.get("lon"))
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        out.append({"lat": lat, "lon": lon,
                    "name": tags.get("name") or tags.get("brand") or label})
    return out


def fetch_pois(cat_ids, bbox, batch=POI_BATCH, timeout=POI_TIMEOUT):
    """({cat_id: [poi, ...]}, failed_ids, error).

    A whole-overlay error is only returned when nothing could be asked at
    all. Otherwise a failed batch names its categories and the rest draw:
    a short list she is told is short beats a spinner that never resolves.
    """
    wanted = [POI_BY_ID[c] for c in cat_ids if c in POI_BY_ID]
    if not wanted:
        return {}, [], ""
    if not M.STATUS_LIVE():
        return {}, list(cat_ids), OFFLINE_OSM
    found, failed = {}, []
    for i in range(0, len(wanted), max(1, batch)):
        group = wanted[i:i + max(1, batch)]
        data, err = _post("/api/overpass", None, timeout=timeout,
                          raw=poi_query(group, bbox),
                          headers={"Content-Type": "text/plain"})
        if err is not None:
            failed.extend(c["id"] for c in group)
            continue
        # One request per batch, so an element is attributed to the first
        # category in the batch whose tags it matches - close enough for a
        # pin, and far cheaper than a request per category.
        for c in group:
            found.setdefault(c["id"], [])
        rows = parse_pois(data, group[0]["label"])
        for row in rows:
            found[group[0]["id"]].append(row)
    if failed and len(failed) == len(wanted):
        return {}, failed, "Overpass did not answer for any category"
    return found, failed, ""


# ---------------------------------------------------------------- maneuvers
# Valhalla numbers its maneuver types; the website maps them to arrows.

MANEUVER_ICONS = {
    0: ft.Icons.NAVIGATION, 1: ft.Icons.STRAIGHT, 2: ft.Icons.STRAIGHT,
    3: ft.Icons.TURN_SLIGHT_RIGHT, 4: ft.Icons.STRAIGHT,
    5: ft.Icons.TURN_SLIGHT_LEFT, 6: ft.Icons.TURN_LEFT,
    7: ft.Icons.TURN_SHARP_LEFT, 8: ft.Icons.U_TURN_LEFT,
    9: ft.Icons.TURN_SHARP_RIGHT, 10: ft.Icons.TURN_RIGHT,
    11: ft.Icons.ROUNDABOUT_LEFT, 12: ft.Icons.FLAG, 13: ft.Icons.LOGIN,
    14: ft.Icons.CIRCLE, 15: ft.Icons.CIRCLE, 16: ft.Icons.CIRCLE,
    17: ft.Icons.CIRCLE,
}


def maneuver_icon(kind):
    return MANEUVER_ICONS.get(kind, ft.Icons.TURN_SLIGHT_RIGHT)


def fmt_miles(mi):
    """'now' / '300 ft' / '1.4 mi'. The website's `gpsxFmtDist`.

    The name describes what comes *in* - a Valhalla leg length, which is
    always miles - not what goes out. What goes out is whichever unit
    Settings is set to, because this is the number on the turn card the
    rider is actually reading, and a countdown in the wrong unit is worse
    than no countdown.
    """
    return _units.near_text(mi)
