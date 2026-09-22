"""Data tables, server calls and pure logic behind the Wat2Do page.

Everything here is a direct port of the website's Wat2Do (dashbored-web/app.js,
the `w2d*` functions) so the phone and the browser agree about the same day.
Two details are worth knowing before changing anything:

* **Check keys are the website's, not the raw template lines.** A sub-item is
  stored under ``"<root> / <sub>"`` and a root under its own display text. The
  app's older ``api.wat2do_state`` keyed them by the raw line (``"> vacuum"``),
  which meant a box ticked on the phone stayed unticked in the browser and vice
  versa. The website's scheme wins because the server's JSON files were written
  by it.
* **Nothing here is invented.** ``save_state`` returns the record the device
  now holds, read back out of ``flet_app.store`` after the write: the
  server's own answer once the write has landed, and this device's copy with
  the change already applied while it is still queued. Either way the page
  repaints from something real, never from what it hoped happened. Whether
  the server has seen it yet is the sync chip's job to say, not this one's.

Network helpers return ``None`` on failure rather than raising, matching
``flet_app.api``; they also go through ``api._get``/``config._opener`` so the
connected/offline status the rest of the app reads stays accurate.
"""
import json
import math
import random
import re
import time
import urllib.parse
import urllib.request

import flet as ft

from agedashii_washi import api as M
from agedashii_washi import config

# Vulcan Lofts, Oakland - the same origin the website measures everything from.
HOME = (37.769972, -122.216445)

# ---------------------------------------------------------------- POI catalogue
# id / label / colour / Material icon / Overpass fragment. The fragments are the
# website's verbatim; build_overpass_ql rewrites `node[`/`way[` into the
# around-home form exactly as w2dLoadPois does.
POI_CATS = [
    {"id": "cafe", "label": "Coffee", "color": "#e0a458", "icon": ft.Icons.LOCAL_CAFE,
     "q": "node[amenity=cafe];"},
    {"id": "restaurant", "label": "Food", "color": "#eb5757", "icon": ft.Icons.RESTAURANT,
     "q": "node[amenity=restaurant];"},
    {"id": "fast_food", "label": "Fast Food", "color": "#f2994a", "icon": ft.Icons.FASTFOOD,
     "q": "node[amenity=fast_food];"},
    {"id": "bar", "label": "Bar", "color": "#bb86fc", "icon": ft.Icons.LOCAL_BAR,
     "q": "node[amenity=bar];node[amenity=pub];"},
    {"id": "park", "label": "Park", "color": "#6fcf97", "icon": ft.Icons.PARK,
     "q": "node[leisure=park];way[leisure=park];"},
    {"id": "playground", "label": "Playground", "color": "#56ccf2", "icon": ft.Icons.TOYS,
     "q": "node[leisure=playground];way[leisure=playground];"},
    {"id": "viewpoint", "label": "Viewpoint", "color": "#f2c94c", "icon": ft.Icons.LANDSCAPE,
     "q": "node[tourism=viewpoint];"},
    {"id": "drinking_water", "label": "Water", "color": "#4fc3f7", "icon": ft.Icons.WATER_DROP,
     "q": "node[amenity=drinking_water];"},
    {"id": "toilets", "label": "Toilets", "color": "#90a4ae", "icon": ft.Icons.WC,
     "q": "node[amenity=toilets];"},
    {"id": "library", "label": "Library", "color": "#7986cb", "icon": ft.Icons.LOCAL_LIBRARY,
     "q": "node[amenity=library];"},
    {"id": "cinema", "label": "Cinema", "color": "#e57373", "icon": ft.Icons.MOVIE,
     "q": "node[amenity=cinema];"},
    {"id": "museum", "label": "Museum", "color": "#ba68c8", "icon": ft.Icons.MUSEUM,
     "q": "node[tourism=museum];"},
    {"id": "arcade", "label": "Arcade", "color": "#ffd54f", "icon": ft.Icons.SPORTS_ESPORTS,
     "q": "node[leisure=adult_gaming_centre];node[leisure=amusement_arcade];"},
    {"id": "bicycle_shop", "label": "Bike Shop", "color": "#81c784", "icon": ft.Icons.PEDAL_BIKE,
     "q": "node[shop=bicycle];"},
    {"id": "atm", "label": "ATM", "color": "#64b5f6", "icon": ft.Icons.ATM,
     "q": "node[amenity=atm];"},
    {"id": "pharmacy", "label": "Pharmacy", "color": "#4db6ac", "icon": ft.Icons.LOCAL_PHARMACY,
     "q": "node[amenity=pharmacy];node[healthcare=pharmacy];"},
    {"id": "artwork", "label": "Artwork", "color": "#ff8fab", "icon": ft.Icons.PALETTE,
     "q": "node[tourism=artwork];way[tourism=artwork];"},
    {"id": "clock", "label": "Clock", "color": "#a5a5a5", "icon": ft.Icons.SCHEDULE,
     "q": "node[amenity=clock];"},
    {"id": "bench", "label": "Benches", "color": "#b0bec5", "icon": ft.Icons.CHAIR,
     "q": "node[amenity=bench];"},
    {"id": "vending", "label": "Vending", "color": "#ffb74d",
     "icon": ft.Icons.LOCAL_CONVENIENCE_STORE, "q": "node[amenity=vending_machine];"},
    {"id": "penny_press", "label": "Penny Press", "color": "#ce93d8",
     "icon": ft.Icons.MONETIZATION_ON,
     "q": "node[amenity=vending_machine][vending=penny_press];"},
    {"id": "ice_cream", "label": "Ice Cream", "color": "#f48fb1", "icon": ft.Icons.ICECREAM,
     "q": "node[amenity=ice_cream];node[shop=ice_cream];"},
    {"id": "lighthouse", "label": "Lighthouse", "color": "#ffd180", "icon": ft.Icons.LIGHT,
     "q": "node[man_made=lighthouse];"},
    {"id": "nature", "label": "Nature", "color": "#81c784", "icon": ft.Icons.FOREST,
     "q": "way[leisure=nature_reserve];way[natural=wood];"},
    {"id": "obs_tower", "label": "Obs Tower", "color": "#90caf9", "icon": ft.Icons.CELL_TOWER,
     "q": "node[man_made=tower][tower:type=observation];node[man_made=observation_tower];"},
    {"id": "parking", "label": "Parking", "color": "#64b5f6", "icon": ft.Icons.LOCAL_PARKING,
     "q": "way[amenity=parking];node[amenity=parking];"},
    {"id": "dog_park", "label": "Dog Park", "color": "#a1887f", "icon": ft.Icons.PETS,
     "q": "way[leisure=dog_park];node[leisure=dog_park];"},
    {"id": "sauna", "label": "Sauna", "color": "#e57373", "icon": ft.Icons.HOT_TUB,
     "q": "node[amenity=sauna];node[leisure=sauna];"},
    {"id": "shrine", "label": "Shrine", "color": "#b39ddb", "icon": ft.Icons.CHURCH,
     "q": "node[historic=wayside_shrine];"},
    {"id": "shops", "label": "Shops", "color": "#4dd0e1", "icon": ft.Icons.STOREFRONT,
     "q": "way[shop=mall];node[shop=supermarket];way[shop=supermarket];"},
    {"id": "architecture", "label": "Architecture", "color": "#ffb300",
     "icon": ft.Icons.ACCOUNT_BALANCE, "q": "way['building:architecture'];"},
    {"id": "binocular", "label": "Binocular", "color": "#80cbc4", "icon": ft.Icons.VISIBILITY,
     "q": "node[amenity=binocular];node[tourism=binocular];"},
    {"id": "circular_economy", "label": "2nd Hand", "color": "#aed581",
     "icon": ft.Icons.RECYCLING,
     "q": "node[shop=second_hand];node[shop=charity];node[amenity=give_box];"},
]
CAT_BY_ID = {c["id"]: c for c in POI_CATS}

MODES = {
    "walkable": {"r": 2000, "label": "Walkable", "icon": ft.Icons.DIRECTIONS_WALK,
                 "cats": ["cafe", "park", "playground", "viewpoint", "drinking_water"]},
    "indoor": {"r": 8000, "label": "Indoor", "icon": ft.Icons.CHAIR,
               "cats": ["library", "cinema", "museum", "arcade"]},
    "food": {"r": 4000, "label": "Food", "icon": ft.Icons.RESTAURANT,
             "cats": ["restaurant", "cafe", "fast_food", "bar"]},
}

VENUE_CATS = {
    "outdoor": ["park", "viewpoint", "artwork", "nature", "obs_tower", "lighthouse",
                "architecture", "shrine", "playground", "dog_park"],
    "indoor": ["museum", "library", "cinema", "arcade", "shops"],
}
MISSION_COUNTS = ["10 shots max", "exactly 25 shots", "50 shots max", "one roll of 24",
                  "as many as you can in 20 min"]
MISSION_STYLES = ["only red things", "ground level only", "hip-shoot everything",
                  "no deleting - keep every shot", "black & white mindset",
                  "reflections only", "textures only", "shoot from below",
                  "things that start with M", "one subject, ten angles"]

BASEMAPS = {
    "light": ("Light", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
    "dark": ("Dark", "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"),
    "satellite": ("Sat", "https://server.arcgisonline.com/ArcGIS/rest/services/"
                         "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
}

WX_DESC = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
           45: "Fog", 48: "Depositing rime fog",
           51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
           61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
           71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
           80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
           95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Heavy thunderstorm"}

WX_ICON = {0: ft.Icons.WB_SUNNY, 1: ft.Icons.WB_SUNNY, 2: ft.Icons.WB_CLOUDY,
           3: ft.Icons.CLOUD, 45: ft.Icons.FOGGY, 48: ft.Icons.FOGGY,
           51: ft.Icons.GRAIN, 53: ft.Icons.GRAIN, 55: ft.Icons.UMBRELLA,
           61: ft.Icons.UMBRELLA, 63: ft.Icons.UMBRELLA, 65: ft.Icons.UMBRELLA,
           71: ft.Icons.AC_UNIT, 73: ft.Icons.AC_UNIT, 75: ft.Icons.AC_UNIT,
           80: ft.Icons.GRAIN, 81: ft.Icons.UMBRELLA, 82: ft.Icons.THUNDERSTORM,
           95: ft.Icons.THUNDERSTORM, 96: ft.Icons.THUNDERSTORM,
           99: ft.Icons.THUNDERSTORM}


def wx_icon(code):
    return WX_ICON.get(_int(code), ft.Icons.THERMOSTAT)


def wx_desc(code):
    return WX_DESC.get(_int(code), "Unknown")


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ dates
def today_str():
    return time.strftime("%Y-%m-%d")


def shift_date(date, delta):
    """`date` (YYYY-MM-DD) moved by `delta` days, still YYYY-MM-DD."""
    try:
        t = time.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return today_str()
    return time.strftime("%Y-%m-%d", time.localtime(time.mktime(
        (t.tm_year, t.tm_mon, t.tm_mday + delta, 12, 0, 0, 0, 0, -1))))


def date_label(date):
    if date == today_str():
        return "Today"
    try:
        return time.strftime("%a %d %b", time.strptime(date, "%Y-%m-%d"))
    except (TypeError, ValueError):
        return date or "—"


# ----------------------------------------------------- checklist hierarchy
_SUB_MARK = re.compile(r"^\s*>")
_SUB_INDENT = re.compile(r"^\s{2,}\S")
_SUB_STRIP = re.compile(r"^\s*>\s*")


def parse_items(items):
    """Template lines into {display, depth}. '>' or 2+ leading spaces = sub-task."""
    out = []
    for raw in items or []:
        s = str(raw)
        if _SUB_MARK.match(s) or _SUB_INDENT.match(s):
            display = _SUB_STRIP.sub("", s).strip()
            if display and out:
                out.append({"display": display, "depth": 1})
        else:
            display = s.strip()
            if display:
                out.append({"display": display, "depth": 0})
    return out


def flatten(items, checks):
    """Template lines + the server's checks into the rows the list renders.

    Each row carries the key the server stores it under, so a toggle never has
    to guess. Roots that own sub-items also carry sub_done/sub_total.
    """
    checks = checks or {}
    flat = []
    last_root = None
    for p in parse_items(items):
        if p["depth"] == 0:
            last_root = p["display"]
            flat.append({"key": p["display"], "display": p["display"], "depth": 0,
                         "parent": None, "has_subs": False})
        elif last_root:
            flat.append({"key": f"{last_root} / {p['display']}", "display": p["display"],
                         "depth": 1, "parent": last_root, "has_subs": False})
            for root in flat:
                if root["depth"] == 0 and root["display"] == last_root:
                    root["has_subs"] = True
                    break
    for it in flat:
        it["done"] = bool(checks.get(it["key"]))
        subs = ([x for x in flat if x["depth"] == 1 and x["parent"] == it["display"]]
                if it["has_subs"] else [])
        it["sub_total"] = len(subs)
        it["sub_done"] = sum(1 for s in subs if checks.get(s["key"]))
    return flat


def row_checked(it):
    """What the box shows: a root with subs is done when all its subs are."""
    if it["has_subs"]:
        return it["sub_total"] > 0 and it["sub_done"] >= it["sub_total"]
    return bool(it["done"])


def toggle_checks(flat, index, checks):
    """The checks dict as it would be after tapping row `index`. Pure."""
    out = dict(checks or {})
    it = flat[index]
    if it["depth"] == 1 or not it["has_subs"]:
        out[it["key"]] = not out.get(it["key"])
        return out
    subs = [x for x in flat if x["depth"] == 1 and x["parent"] == it["display"]]
    all_done = bool(subs) and all(out.get(s["key"]) for s in subs)
    for s in subs:
        out[s["key"]] = not all_done
    out[it["key"]] = not all_done
    return out


def progress(flat):
    """(done, total) counting leaves only, so a root is not counted twice."""
    leaves = [it for it in flat if not it["has_subs"]]
    return sum(1 for it in leaves if it["done"]), len(leaves)


def template_lines(items):
    """Template list to the text the editor shows (one line each, '> ' kept)."""
    return "\n".join(str(i) for i in (items or []))


def parse_template_text(text):
    """Editor text back to template lines, normalising sub markers like the site."""
    out = []
    for raw in (text or "").split("\n"):
        t = raw.strip()
        if not t:
            continue
        if t.startswith(">"):
            out.append("> " + _SUB_STRIP.sub("", t).strip())
        elif _SUB_INDENT.match(raw):
            out.append("> " + t)
        else:
            out.append(t)
    return out


# --------------------------------------------------------------- geometry
def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def miles(metres):
    return metres / 1609.344


def walk_minutes(metres):
    """~3 mph, the same 80 m/min the website quotes."""
    return round(metres / 80)


def decode_polyline(encoded, precision=6):
    """Valhalla's encoded shape into [(lat, lon), ...]. Precision 6, as it sends."""
    factor = float(10 ** precision)
    out, index, lat, lon = [], 0, 0, 0
    n = len(encoded or "")
    while index < n:
        for is_lat in (True, False):
            shift, result = 0, 0
            while index < n:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lon += delta
        out.append((lat / factor, lon / factor))
    return out


# -------------------------------------------------------------- classify
def classify(tags):
    """OSM tags to one of our category ids, or None. Port of w2dClassify + its
    extension, extension first (the website monkey-patches it in that order)."""
    t = tags or {}
    pairs = [
        (("vending", "penny_press"), "penny_press"),
        (("tourism", "artwork"), "artwork"),
        (("amenity", "clock"), "clock"),
        (("amenity", "bench"), "bench"),
        (("amenity", "vending_machine"), "vending"),
        (("amenity", "ice_cream"), "ice_cream"),
        (("shop", "ice_cream"), "ice_cream"),
        (("man_made", "lighthouse"), "lighthouse"),
        (("leisure", "nature_reserve"), "nature"),
        (("natural", "wood"), "nature"),
        (("man_made", "observation_tower"), "obs_tower"),
        (("amenity", "parking"), "parking"),
        (("leisure", "dog_park"), "dog_park"),
        (("amenity", "sauna"), "sauna"),
        (("leisure", "sauna"), "sauna"),
        (("historic", "wayside_shrine"), "shrine"),
        (("shop", "mall"), "shops"),
        (("shop", "supermarket"), "shops"),
        (("amenity", "binocular"), "binocular"),
        (("tourism", "binocular"), "binocular"),
        (("shop", "second_hand"), "circular_economy"),
        (("shop", "charity"), "circular_economy"),
        (("amenity", "give_box"), "circular_economy"),
        # base table
        (("amenity", "cafe"), "cafe"),
        (("amenity", "restaurant"), "restaurant"),
        (("amenity", "fast_food"), "fast_food"),
        (("amenity", "bar"), "bar"),
        (("amenity", "pub"), "bar"),
        (("leisure", "park"), "park"),
        (("leisure", "playground"), "playground"),
        (("tourism", "viewpoint"), "viewpoint"),
        (("amenity", "drinking_water"), "drinking_water"),
        (("amenity", "toilets"), "toilets"),
        (("amenity", "library"), "library"),
        (("amenity", "cinema"), "cinema"),
        (("tourism", "museum"), "museum"),
        (("leisure", "adult_gaming_centre"), "arcade"),
        (("leisure", "amusement_arcade"), "arcade"),
        (("shop", "bicycle"), "bicycle_shop"),
        (("amenity", "atm"), "atm"),
        (("amenity", "pharmacy"), "pharmacy"),
        (("healthcare", "pharmacy"), "pharmacy"),
    ]
    if t.get("man_made") == "tower" and t.get("tower:type") == "observation":
        return "obs_tower"
    for (k, v), cat in pairs:
        if t.get(k) == v:
            return cat
    if t.get("building:architecture"):
        return "architecture"
    return None


def spots_from(elements, allowed_ids=None):
    """Overpass elements to named, located, classified spots sorted by distance."""
    out = []
    for el in elements or []:
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        cat = classify(tags)
        if not cat or cat not in CAT_BY_ID:
            continue
        if allowed_ids is not None and cat not in allowed_ids:
            continue
        if el.get("lat") is not None:
            lat, lon = el["lat"], el["lon"]
        elif el.get("center"):
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        elif el.get("geometry"):
            lat, lon = el["geometry"][0]["lat"], el["geometry"][0]["lon"]
        else:
            continue
        out.append({"name": name, "cat": cat, "lat": lat, "lon": lon,
                    "dist_m": haversine_m(HOME, (lat, lon))})
    out.sort(key=lambda s: s["dist_m"])
    return out


# ------------------------------------------------- suggestion / mission ctx
def _wet(wx):
    return bool(wx) and _int((wx or {}).get("weather_code")) >= 51


def _windy(wx):
    try:
        return bool(wx) and float((wx or {}).get("wind_speed_10m") or 0) > 20
    except (TypeError, ValueError):
        return False


def wx_callout(wx):
    """(text, tone) for the weather banner. tone is good / meh / bad."""
    if not wx:
        return ("Weather unavailable", "meh")
    if _wet(wx):
        return ("Wet today - indoor day.", "bad")
    if _windy(wx):
        return ("Windy today - bundled ride only.", "meh")
    return ("Clear-ish window - good day to go out.", "good")


def suggest(pain, spoons, wx):
    """(mode, why) - the website's w2dSuggest, same order of precedence."""
    if spoons is not None and spoons <= 2:
        return "walkable", "low spoons - keep it close"
    if pain is not None and pain >= 6:
        return "indoor", "pain high - easy indoor"
    if _wet(wx) or _windy(wx):
        return "indoor", ("wet" if _wet(wx) else "windy") + " out - indoor"
    if spoons is not None and spoons >= 4:
        return "food", "spoons up - go eat"
    return "food", ""


def mission_context(pain, spoons, wx):
    """{indoor, radius, why} - what kind of day it is, for the mission roll."""
    indoor = bool(_wet(wx) or _windy(wx)
                  or (spoons is not None and spoons <= 2)
                  or (pain is not None and pain >= 6))
    radius = 8000
    if spoons is not None and spoons <= 2:
        radius = 2000
    elif spoons is not None and spoons <= 3:
        radius = 4000
    if indoor:
        reason = ("wet" if _wet(wx) else "windy" if _windy(wx)
                  else "low spoons" if (spoons is not None and spoons <= 2) else "pain")
        why = f"{reason} - keeping it indoor"
    else:
        why = "good day - going far"
    return {"indoor": indoor, "radius": radius, "why": why}


def pick_venue(elements, ctx, rng=random):
    """One venue, closer ones favoured. The website's dist-weighted rejection
    sampling, kept so the dice feel the same in both places."""
    allowed = (VENUE_CATS["indoor"] if ctx["indoor"]
               else VENUE_CATS["outdoor"] + VENUE_CATS["indoor"])
    pool = spots_from(elements, allowed_ids=set(allowed))
    if not pool:
        return None
    max_d = max([p["dist_m"] for p in pool] + [1])
    for _ in range(24):
        p = pool[rng.randrange(len(pool))]
        if rng.random() < 1 - 0.6 * (p["dist_m"] / max_d):
            return p
    return pool[0]


def roll_mission(venue, deck, ctx, rng=random):
    """A whole mission: venue + 3-6 subjects + one constraint."""
    deck = list(deck or []) or ["TRIANGLES", "STAIRS", "REFLECTIONS"]
    n = 3 + rng.randrange(4)
    picked = []
    copy = list(deck)
    while copy and len(picked) < n:
        picked.append(copy.pop(rng.randrange(len(copy))))
    constraint = (MISSION_STYLES[rng.randrange(len(MISSION_STYLES))] if rng.random() < 0.55
                  else MISSION_COUNTS[rng.randrange(len(MISSION_COUNTS))])
    return {"venue": venue, "subjects": picked, "constraint": constraint, "why": ctx["why"]}


# ------------------------------------------------------------ server calls
def _fresh(path):
    """Drop a cached GET so the next read is the server's current answer."""
    try:
        M._cache.pop(path, None)
    except Exception:  # noqa: BLE001 - cache shape is api.py's business
        pass


def state(date, fresh=False):
    """A day's record, or None. /api/wat2do/state?date=YYYY-MM-DD."""
    path = f"/api/wat2do/state?date={urllib.parse.quote(date)}"
    if fresh:
        _fresh(path)
    return M._get(path, timeout=6)


def save_state(date, checks, pain, spoons):
    """Write a day. Returns (ok, message, record).

    Goes through ``api.wat2do_save``, which is to say through the outbox:
    the write is queued and applied to this device's copy in one transaction
    and only *then* attempted on the wire. That is the difference between a
    box ticked in a tunnel being kept and being lost - the old code here
    posted straight out with ``M._post`` and threw the edit away when the
    post failed, alone among every editable page in the app.

    ``api.wat2do_save`` answers (ok, message) rather than the server's record,
    so the record the caller repaints from is read back out of the store. It
    is the server's own answer once the write lands, and this device's copy
    with the tick already applied while it has not - which is exactly what
    should be on screen in each case, and never a value we merely hoped for.
    """
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""):
        return False, "bad date", None
    path = f"/api/wat2do/state?date={urllib.parse.quote(date)}"
    before = M._cached(path) or {}
    ok, msg = M.wat2do_save({
        "date": date, "pain": pain, "spoons": spoons,
        # api.wat2do_save rebuilds the checks dict from `_key`/`done`, so hand
        # it the website's keys straight through rather than re-deriving them.
        "items": [{"t": k, "_key": k, "done": bool(v)}
                  for k, v in (checks or {}).items()],
    })
    if not ok:
        return False, msg, None
    record = state(date)
    if record is None:
        # No local database at all (api fell back to sending inline). Nothing
        # to read back, so reconstruct the day from what we know rather than
        # report a save that worked as a failure.
        record = {**before, "date": date, "pain": pain, "spoons": spoons,
                  "checks": {**(before.get("checks") or {}), **(checks or {})}}
    return True, msg, record


def template(fresh=False):
    if fresh:
        _fresh("/api/wat2do/template")
    d = M._get("/api/wat2do/template", timeout=6)
    return None if d is None else (d.get("items") or [])


def save_template(items):
    r = M._post("/api/wat2do/template", {"items": items}, timeout=10)
    if r is None:
        return False, config.STATUS.get("detail") or "server unreachable", None
    if r.get("error") or not r.get("ok"):
        return False, str(r.get("error") or "server refused the write"), None
    _fresh("/api/wat2do/template")
    return True, "template saved", r.get("items") or items


def missions(fresh=False):
    if fresh:
        _fresh("/api/wat2do/missions")
    d = M._get("/api/wat2do/missions", timeout=6)
    return None if d is None else (d.get("subjects") or [])


def save_missions(subjects):
    r = M._post("/api/wat2do/missions", {"subjects": subjects}, timeout=10)
    if r is None:
        return False, config.STATUS.get("detail") or "server unreachable", None
    if r.get("error") or not r.get("ok"):
        return False, str(r.get("error") or "server refused the write"), None
    _fresh("/api/wat2do/missions")
    return True, "deck saved", r.get("subjects") or subjects


def weather_now():
    """The raw /api/weather payload, or None. The page wants `current` and
    `daily`, which api.weather() reshapes away."""
    return M._get("/api/weather", timeout=8)


def build_overpass_ql(cats, radius, ql_timeout=25):
    lat, lon = HOME
    parts = []
    for c in cats:
        q = c["q"].replace("node[", f"node(around:{radius},{lat},{lon})[")
        q = q.replace("way[", f"way(around:{radius},{lat},{lon})[")
        parts.append(q)
    return (f"[out:json][timeout:{ql_timeout}];(" + "".join(parts)
            + ");out center tags 350;")


_POI_CACHE = {}
_POI_TTL = 30 * 60
# Overpass gives up on a wide query long before the proxy does: the website's
# own 15-category, 8 km venue scan 504s every time (measured 2026-09-20).
# Asking for a few categories at a time succeeds, caches better, and lets a
# single bad category fail on its own instead of taking the whole scan down.
POI_CHUNK = 3


def _poi_key(cats, radius):
    return (",".join(sorted(c["id"] for c in cats)), radius)


def _poi_cached(cats, radius):
    hit = _POI_CACHE.get(_poi_key(cats, radius))
    if hit and time.time() - hit[0] < _POI_TTL:
        return hit[1]
    return None


def _overpass_once(cats, radius, timeout):
    """One Overpass round trip, cached 30 min. Returns elements or None."""
    key = _poi_key(cats, radius)
    cached = _poi_cached(cats, radius)
    if cached is not None:
        return cached
    ql = build_overpass_ql(cats, radius)
    req = urllib.request.Request(
        config.SERVER_URL + "/api/overpass", data=ql.encode(), method="POST",
        headers={"Content-Type": "text/plain"})
    try:
        with config._opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
        config._remember(config.SERVER_URL, True, "connected")
    except Exception as e:  # noqa: BLE001
        config._remember(config.SERVER_URL, False, config._describe(e))
        return None
    elems = data.get("elements") or []
    _POI_CACHE[key] = (time.time(), elems)
    return elems


def overpass(cats, radius, timeout=35, chunk=POI_CHUNK, budget=80):
    """POIs around home, in chunks, within a time budget.

    Returns ``(elements, failed_ids)``. ``elements`` is None only when every
    chunk failed; otherwise it is what came back and ``failed_ids`` names the
    categories that did not, so the page can say the list is short rather than
    pretend it is complete.

    The budget matters because Overpass queues requests when it is busy: a
    wide scan can sit for a minute per chunk. Better a short list now, named
    as short, than a spinner that never resolves.
    """
    if not cats:
        return [], []
    deadline = time.time() + budget
    out, failed, any_ok = [], [], False
    for i in range(0, len(cats), max(1, chunk)):
        group = cats[i:i + max(1, chunk)]
        got = _poi_cached(group, radius)        # cached chunks are free
        if got is None:
            left = deadline - time.time()
            got = (_overpass_once(group, radius, min(timeout, max(5.0, left)))
                   if left > 0 else None)
        if got is None:
            failed.extend(c["id"] for c in group)
        else:
            any_ok = True
            out.extend(got)
    if not any_ok:
        return None, failed
    return out, failed


def empty_scan_note(elements, failed):
    """What to say when a scan succeeded and still found nothing.

    "Nothing found nearby" is the website's answer to both of the cases
    below, and for one of them it is false. Measured 2026-09-21: the mirror
    the server reached answered a 4 km cafe-and-restaurant query around home
    with ``"elements": []`` and a generator timestamp of "117180" - an
    instance running on an empty extract. There are, in fact, cafes within
    4 km of Vulcan Lofts.

    So: Overpass returning literally no elements at all, having raised no
    error, is a statement about the mirror rather than about Oakland.
    Elements that did come back but carry no name, or no tag this app
    classifies, really is nothing worth showing.
    """
    if elements:
        return "Nothing nearby that Overpass has a name for."
    if failed:
        return "Overpass answered with nothing, and dropped the rest."
    return ("Overpass returned no data at all - the mirror it reached looks "
            "empty, which is not the same as the area being empty.")


def osm_notes(bbox):
    """Open OSM community notes in a bbox 'south,west,north,east', or None."""
    d = M._get(f"/api/wat2do/notes?bbox={urllib.parse.quote(bbox)}", timeout=25)
    if d is None or d.get("error"):
        return None
    return d.get("notes") or []


def curated():
    """The Future/curated lists the Today card slices: events, movies, ..."""
    return M._get("/api/future/curated", timeout=20)


def thrift_near(radius_m=24140, limit=5):
    """Thrift and flea spots near home, or None. /api/future/scan."""
    lat, lon = HOME
    d = M._get(f"/api/future/scan?lat={lat}&lon={lon}&r={int(radius_m)}&cats=thrift,flea",
               timeout=40)
    if d is None:
        return None
    return (d.get("elements") or [])[:limit]


def route(lat, lon, costing="pedestrian"):
    """Home to (lat, lon). Returns (ok, message, {geometry, minutes, miles})."""
    opts = {"pedestrian": {}, "motorcycle": {"use_hills": 1.0, "use_primary": 0.5,
                                             "use_trails": 0.5}}.get(costing, {})
    payload = {"costing": costing, "directions_options": {"units": "miles"},
               "locations": [{"lat": HOME[0], "lon": HOME[1]}, {"lat": lat, "lon": lon}]}
    if opts:
        payload["costing_options"] = {costing: opts}
    r = M._post("/api/valhalla/route", payload, timeout=40)
    if r is None:
        return False, config.STATUS.get("detail") or "server unreachable", None
    trip = r.get("trip") or {}
    legs = trip.get("legs") or []
    if not legs:
        return False, str(r.get("error") or "no route"), None
    geometry = []
    for leg in legs:
        geometry.extend(decode_polyline(leg.get("shape") or ""))
    summary = trip.get("summary") or {}
    return True, "ok", {"geometry": geometry,
                        "minutes": (summary.get("time") or 0) / 60.0,
                        "miles": summary.get("length") or 0}
