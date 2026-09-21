"""Weather and Explore.

Two things live here because they answer the same question before a ride:
what is it like out there, and what is out there.

**Weather** is read from ``/api/weather/multi`` rather than ``/api/weather``.
Both proxy Open-Meteo, but the multi route asks for strictly more: apparent
temperature, humidity, current precipitation, wind gusts, daily rain
probability and daily max wind, for 21 named Bay Area spots instead of one
hardcoded Oakland point. The single-point route returns none of the rain or
gust fields, which is why the old page showed "0% rain" forever - it read a
``hourly.precipitation_probability`` array that this server has never sent.

Neither route returns an hourly series, so there is no hourly strip here. An
empty hourly row that quietly shows today's single reading twelve times is
worse than not having one; the page says outright that the server does not
supply it.

**Explore** draws a real map and pins real OSM data from the server's
Overpass proxy. The proxy takes a raw Overpass QL string as the POST body -
not JSON - and the bbox has to be attached to each statement
(``node[amenity=fuel](s,w,n,e);``). Wrapping a union and bbox-ing the group
(``(node[amenity=fuel];)(s,w,n,e)``) is what the website does and Overpass
answers it with HTTP 400, which the proxy reports as "overpass failed". That
is the whole reason POIs looked non-existent.

Tiles follow xnav: the on-device MBTiles archive through the loopback tile
server if one is installed, online tiles if not, and the map says which.
"""
import asyncio
import json
import math
import time
import urllib.error
import urllib.request

import flet as ft

from .. import api as M
from .. import panels
from .. import config, tileserver
from ..ui import BRD, PAD, PAL, card, chip, sub, title
from ._forms import rebuilder
from .lab import _tabs

try:                      # optional: the app still runs without the extension
    import flet_map as fmap
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = None
    MAPS_AVAILABLE = False

# The spot the hero card is about. The server publishes 21 of these; this one
# is matched by name so reordering the server's list cannot silently point the
# card at Cupertino.
HOME_NAME = "Oakland"
HOME = (37.8044, -122.2712)


# --------------------------------------------------------------- WMO codes
# The published WMO 4677 present-weather codes Open-Meteo returns, with the
# icon and palette colour each should read as. Anything unlisted renders as
# the raw code rather than being guessed at.
_WMO = {
    0:  ("clear",             ft.Icons.SUNNY,         "yellow"),
    1:  ("mainly clear",      ft.Icons.SUNNY,         "yellow"),
    2:  ("partly cloudy",     ft.Icons.FILTER_DRAMA,  "yellow"),
    3:  ("overcast",          ft.Icons.CLOUD,         "text2"),
    45: ("fog",               ft.Icons.FOGGY,         "text2"),
    48: ("freezing fog",      ft.Icons.FOGGY,         "text2"),
    51: ("light drizzle",     ft.Icons.GRAIN,         "cyan"),
    53: ("drizzle",           ft.Icons.GRAIN,         "cyan"),
    55: ("heavy drizzle",     ft.Icons.GRAIN,         "cyan"),
    56: ("freezing drizzle",  ft.Icons.SEVERE_COLD,   "cyan"),
    57: ("freezing drizzle",  ft.Icons.SEVERE_COLD,   "cyan"),
    61: ("light rain",        ft.Icons.UMBRELLA,      "cyan"),
    63: ("rain",              ft.Icons.UMBRELLA,      "cyan"),
    65: ("heavy rain",        ft.Icons.UMBRELLA,      "cyan"),
    66: ("freezing rain",     ft.Icons.SEVERE_COLD,   "cyan"),
    67: ("freezing rain",     ft.Icons.SEVERE_COLD,   "cyan"),
    71: ("light snow",        ft.Icons.AC_UNIT,       "cyan"),
    73: ("snow",              ft.Icons.AC_UNIT,       "cyan"),
    75: ("heavy snow",        ft.Icons.AC_UNIT,       "cyan"),
    77: ("snow grains",       ft.Icons.AC_UNIT,       "cyan"),
    80: ("light showers",     ft.Icons.GRAIN,         "cyan"),
    81: ("showers",           ft.Icons.GRAIN,         "cyan"),
    82: ("heavy showers",     ft.Icons.GRAIN,         "cyan"),
    85: ("snow showers",      ft.Icons.AC_UNIT,       "cyan"),
    86: ("snow showers",      ft.Icons.AC_UNIT,       "cyan"),
    95: ("thunderstorm",      ft.Icons.THUNDERSTORM,  "purple"),
    96: ("thunderstorm",      ft.Icons.THUNDERSTORM,  "purple"),
    99: ("thunderstorm",      ft.Icons.THUNDERSTORM,  "purple"),
}


def _wmo(code):
    """(label, icon, palette key) for a WMO code, honest about unknown ones."""
    try:
        return _WMO[int(code)]
    except (TypeError, ValueError, KeyError):
        return (f"code {code}" if code is not None else "—",
                ft.Icons.HELP_OUTLINE, "text2")


def _n(value, digits=0):
    """Render a number the server may have omitted, without inventing a zero."""
    if value is None:
        return "—"
    try:
        return f"{round(float(value)):.0f}" if digits == 0 else f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _at(seq, i):
    """seq[i], or None - daily arrays are parallel but not guaranteed equal."""
    try:
        return seq[i]
    except (IndexError, TypeError, KeyError):
        return None


def _weekday(iso_date, index):
    if index == 0:
        return "Today"
    try:
        return time.strftime("%a", time.strptime(iso_date, "%Y-%m-%d"))
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------ weather

def _spots():
    """The server's 21 Bay Area forecasts, or (None, why-not).

    Deliberately not ``M.WEATHER``: that accessor substitutes ``mock.WEATHER``
    whenever the server is unreachable, so the page would draw last winter's
    invented Oakland forecast and look perfectly healthy while offline.
    """
    d = M._get("/api/weather/multi", timeout=8)
    if d is None:
        return None, (config.STATUS.get("detail") or "server unreachable")
    if isinstance(d, dict):
        if d.get("error"):
            return None, str(d["error"])
        d = [d]
    if not isinstance(d, list) or not d:
        return None, "the server returned no forecasts"
    return d, None


def _pick(spots, name):
    for s in spots:
        if s.get("name") == name:
            return s
    return spots[0]


def _hero(spot):
    """Current conditions for one place, from whatever fields arrived."""
    p = PAL()
    cur = spot.get("current") or {}
    daily = spot.get("daily") or {}
    label, icon, ckey = _wmo(cur.get("weather_code"))

    def metric(name, value, unit, micon):
        return ft.Column(spacing=1, controls=[
            ft.Row(spacing=4, tight=True, controls=[
                ft.Icon(micon, size=12, color=p["text3"]), sub(name, 10)]),
            ft.Row(spacing=2, tight=True, vertical_alignment=ft.CrossAxisAlignment.END,
                   controls=[
                       ft.Text(value, size=14, weight=ft.FontWeight.W_600, color=p["text"]),
                       ft.Text(unit, size=9, color=p["text3"])]),
        ])

    hi, lo = _at(daily.get("temperature_2m_max") or [], 0), _at(daily.get("temperature_2m_min") or [], 0)
    rain = _at(daily.get("precipitation_probability_max") or [], 0)
    gust = cur.get("wind_gusts_10m")

    return card(ft.Column(spacing=10, controls=[
        ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text(f"{_n(cur.get('temperature_2m'))}°", size=44,
                    weight=ft.FontWeight.W_700, color=p["text"]),
            ft.Container(width=10),
            ft.Column(spacing=0, controls=[
                ft.Row(spacing=6, tight=True, controls=[
                    ft.Icon(icon, size=22, color=p[ckey]),
                    ft.Text(label, size=14, weight=ft.FontWeight.W_500, color=p["text"])]),
                sub(f"feels like {_n(cur.get('apparent_temperature'))}°", 11),
            ]),
            ft.Container(expand=True),
            ft.Column(spacing=1, horizontal_alignment=ft.CrossAxisAlignment.END, controls=[
                ft.Text(f"H {_n(hi)}°", size=13, color=p["text"]),
                ft.Text(f"L {_n(lo)}°", size=13, color=p["text2"]),
            ]),
        ]),
        ft.Row(spacing=6, controls=[
            chip(f"{spot.get('name', '?')} · {spot.get('region', '')}".strip(" ·"),
                 p["accent"], ft.Icons.PLACE, small=True),
            chip(f"{_n(rain)}% rain today" if rain is not None else "rain chance n/a",
                 p["cyan"], ft.Icons.WATER_DROP, small=True),
        ]),
        ft.Divider(height=1, color=p["border"]),
        ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[
            metric("wind", _n(cur.get("wind_speed_10m")), " mph", ft.Icons.AIR),
            metric("gusts", _n(gust), " mph", ft.Icons.SPEED),
            metric("humidity", _n(cur.get("relative_humidity_2m")), "%", ft.Icons.WATER),
            metric("precip now", _n(cur.get("precipitation"), 1), " mm", ft.Icons.UMBRELLA),
        ]),
        sub(f"observed {cur.get('time', '—')} local", 10),
    ]))


def _forecast(spot):
    """The daily forecast, every day the server sent - today included.

    The old strip started at index 1 and stopped at 4, so a three-day payload
    rendered as two cards and today's own high and low never appeared.
    """
    p = PAL()
    daily = spot.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return card(sub("The server sent no daily forecast.", 11), pad=12)

    codes = daily.get("weather_code") or []
    mx = daily.get("temperature_2m_max") or []
    mn = daily.get("temperature_2m_min") or []
    pop = daily.get("precipitation_probability_max") or []
    wind = daily.get("wind_speed_10m_max") or []

    def day(i):
        label, icon, ckey = _wmo(_at(codes, i))
        return ft.Container(
            expand=True, padding=8, border_radius=10,
            bgcolor=p["surface"], border=BRD(p["border"], 1),
            content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                ft.Text(_weekday(dates[i], i), size=11, weight=ft.FontWeight.W_600,
                        color=p["text2"]),
                ft.Icon(icon, size=24, color=p[ckey]),
                ft.Text(label, size=9, color=p["text3"], no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row(spacing=4, tight=True, alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Text(f"{_n(_at(mx, i))}°", size=14, weight=ft.FontWeight.W_600,
                            color=p["text"]),
                    ft.Text(f"{_n(_at(mn, i))}°", size=12, color=p["text3"]),
                ]),
                ft.Row(spacing=3, tight=True, alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Icon(ft.Icons.WATER_DROP, size=10, color=p["cyan"]),
                    ft.Text(f"{_n(_at(pop, i))}%", size=10, color=p["cyan"]),
                    ft.Icon(ft.Icons.AIR, size=10, color=p["text3"]),
                    ft.Text(f"{_n(_at(wind, i))}", size=10, color=p["text3"]),
                ]),
            ]))

    return ft.Row(spacing=8, controls=[day(i) for i in range(len(dates))])


def _regional(spots, selected, on_pick):
    """Every spot the server forecasts, so a ride can be aimed at the sun."""
    p = PAL()

    def row(s):
        cur = s.get("current") or {}
        daily = s.get("daily") or {}
        label, icon, ckey = _wmo(cur.get("weather_code"))
        pop = _at(daily.get("precipitation_probability_max") or [], 0)
        is_sel = s.get("name") == selected
        return ft.Container(
            padding=PAD(7, 10), border_radius=9,
            bgcolor=ft.Colors.with_opacity(0.10, p["accent"]) if is_sel else p["surface"],
            border=BRD(p["accent"] if is_sel else p["border"], 1),
            on_click=lambda e, n=s.get("name"): on_pick(n),
            content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Icon(icon, size=17, color=p[ckey]),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(s.get("name", "?"), size=12, weight=ft.FontWeight.W_600,
                            color=p["text"], no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{s.get('region', '')} · {label}", size=9, color=p["text3"],
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ]),
                ft.Text(f"{_n(cur.get('wind_speed_10m'))} mph", size=10, color=p["text3"]),
                ft.Text(f"{_n(pop)}%", size=10, color=p["cyan"]),
                ft.Text(f"{_n(cur.get('temperature_2m'))}°", size=16,
                        weight=ft.FontWeight.W_700, color=p["text"]),
            ]))

    return ft.Column(spacing=5, controls=[row(s) for s in spots])


# The spot the Bay tab is currently focused on. Module level so a refresh or a
# tab switch does not throw the choice away.
_focus = {"name": HOME_NAME}


def _bay(page):
    p = PAL()
    spots, err = _spots()
    reload_page = rebuilder(page, build)

    header = ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        title("Bay Area", 15), ft.Container(expand=True),
        ft.IconButton(ft.Icons.REFRESH, icon_color=p["accent"], icon_size=20,
                      tooltip="re-read from the server",
                      on_click=lambda e: reload_page()),
    ])

    if err:
        return ft.Column(spacing=10, controls=[
            header,
            card(ft.Column(spacing=6, controls=[
                ft.Row(spacing=8, controls=[
                    ft.Icon(ft.Icons.CLOUD_OFF, size=18, color=p["pink"]),
                    ft.Text("No weather", size=14, weight=ft.FontWeight.W_600,
                            color=p["text"])]),
                ft.Text(err, size=11, color=p["pink"], selectable=True),
                sub(f"asked {config.SERVER_URL}/api/weather/multi", 10),
            ]), pad=14),
        ])

    if not any(s.get("name") == _focus["name"] for s in spots):
        _focus["name"] = spots[0].get("name")

    def pick(name):
        _focus["name"] = name
        reload_page()

    spot = _pick(spots, _focus["name"])

    def regional_block():
        return ft.Column(spacing=8, controls=[
            ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                title("All spots", 13), ft.Container(expand=True),
                sub("wind · rain · temp", 10)]),
            _regional(spots, _focus["name"], pick),
            sub("Current conditions and a 3-day daily forecast are everything "
                "/api/weather/multi returns. It sends no hourly series, so "
                "there is no hour-by-hour strip to show.", 10),
        ])

    return panels.layout("weather", {
        "header": lambda: header,
        "hero": lambda: _hero(spot),
        "forecast": lambda: _forecast(spot),
        "regional": regional_block,
    })


# ------------------------------------------------------------------ explore

# Categories lifted from the website's GPSX and Wat2Do pickers, with the bbox
# attached per statement - see the module docstring for why the website's own
# grouped form is rejected by Overpass.
POI_CATS = [
    {"id": "fuel", "label": "Fuel", "icon": ft.Icons.LOCAL_GAS_STATION, "color": "orange",
     "q": ['node[amenity=fuel]'],
     "match": lambda t: t.get("amenity") == "fuel"},
    {"id": "food", "label": "Food", "icon": ft.Icons.RESTAURANT, "color": "red",
     "q": ['node[amenity~"^(restaurant|fast_food|pub|bar)$"]'],
     "match": lambda t: t.get("amenity") in ("restaurant", "fast_food", "pub", "bar")},
    {"id": "coffee", "label": "Coffee", "icon": ft.Icons.LOCAL_CAFE, "color": "yellow",
     "q": ['node[amenity=cafe]'],
     "match": lambda t: t.get("amenity") == "cafe"},
    {"id": "view", "label": "Viewpoint", "icon": ft.Icons.LANDSCAPE, "color": "green",
     "q": ['node[tourism=viewpoint]'],
     "match": lambda t: t.get("tourism") == "viewpoint"},
    {"id": "parking", "label": "Parking", "icon": ft.Icons.LOCAL_PARKING, "color": "cyan",
     "q": ['node[amenity=parking]', 'way[amenity=parking]'],
     "match": lambda t: t.get("amenity") == "parking"},
    {"id": "water", "label": "Water", "icon": ft.Icons.WATER_DROP, "color": "cyan",
     "q": ['node[amenity=drinking_water]'],
     "match": lambda t: t.get("amenity") == "drinking_water"},
    {"id": "wc", "label": "Restrooms", "icon": ft.Icons.WC, "color": "purple",
     "q": ['node[amenity=toilets]'],
     "match": lambda t: t.get("amenity") == "toilets"},
    {"id": "atm", "label": "ATM", "icon": ft.Icons.LOCAL_ATM, "color": "green",
     "q": ['node[amenity~"^(atm|bank)$"]'],
     "match": lambda t: t.get("amenity") in ("atm", "bank")},
]
_CAT_BY_ID = {c["id"]: c for c in POI_CATS}

# Assumed size of the map viewport in logical pixels. The widget does not
# report its own size, so "search this area" derives the bbox from the camera
# and this; it is approximate by construction, which is why the button says
# "this area" and not "exactly what you see".
_VIEW_W, _VIEW_H = 400.0, 340.0

# Overpass will happily start a query that covers the whole Bay and then time
# out sixty seconds later. Half a degree is about 55 km - past that, refusing
# is faster and more honest than waiting.
_MAX_SPAN = 0.5

# `out center N` caps the union as a whole, not each category, so in a dense
# city cafes can crowd fuel stations out of the answer entirely. The cap is
# high enough that that rarely bites, and when it does the page says so
# instead of letting you believe there are no gas stations in Oakland.
_POI_LIMIT = 120

_explore = {"cats": {"fuel"}, "center": HOME, "zoom": 13.0,
            "pois": [], "note": "", "error": ""}


def _bbox_for(lat, lon, zoom):
    """(south, west, north, east) roughly covering the map viewport."""
    scale = 256.0 * (2.0 ** zoom)
    dlon = 360.0 * _VIEW_W / scale
    # In Web Mercator a pixel spans fewer degrees of latitude the further
    # from the equator you are, by cos(lat).
    dlat = dlon * (_VIEW_H / _VIEW_W) * math.cos(math.radians(lat))
    dlon = min(dlon, _MAX_SPAN) / 2.0
    dlat = min(dlat, _MAX_SPAN) / 2.0
    return (round(lat - dlat, 5), round(lon - dlon, 5),
            round(lat + dlat, 5), round(lon + dlon, 5))


def _haversine_mi(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 3958.7613 * math.asin(math.sqrt(h))


def _overpass(ql, timeout=75):
    """POST raw Overpass QL to the server's proxy. Returns (data, error).

    The proxy reads the request body as the query itself, so this cannot go
    through ``api._post``, which would wrap it in JSON. A 4xx/5xx here means
    Overpass upstream said no, not that the dashbored server is down, so the
    connection status is deliberately left alone - marking it offline would
    grey out every other page over a bad POI query.
    """
    req = urllib.request.Request(
        config.SERVER_URL + "/api/overpass", data=ql.encode(),
        headers={"Content-Type": "text/plain"}, method="POST")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode() or "{}").get("error") or e.reason
        except Exception:                                  # noqa: BLE001
            detail = e.reason
        return None, f"{detail} (HTTP {e.code})"
    except TimeoutError:
        # The proxy tries three Overpass endpoints at up to 60s each, so a
        # slow public instance can outlast any sane wait on a phone.
        return None, "Overpass did not answer in time - try a smaller area"
    except Exception as e:                                 # noqa: BLE001
        return None, config._describe(e)
    try:
        data = json.loads(raw)
    except ValueError:
        # Overpass answers a malformed query with an HTML error page.
        return None, "Overpass rejected the query"
    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    return data, None


def _classify(tags):
    for c in POI_CATS:
        try:
            if c["match"](tags):
                return c
        except Exception:                                  # noqa: BLE001
            continue
    return None


def _fetch_pois(cat_ids, bbox):
    """(pois, error, truncated). Each poi is {name, cat, lat, lon, tags}."""
    cats = [_CAT_BY_ID[i] for i in cat_ids if i in _CAT_BY_ID]
    if not cats:
        return [], "pick at least one category", False
    box = "{},{},{},{}".format(*bbox)
    stmts = "".join(f"{sel}({box});" for c in cats for sel in c["q"])
    ql = f"[out:json][timeout:25];({stmts});out center {_POI_LIMIT};"

    # The public Overpass instances fail intermittently - the same query can
    # take 1s, or 70s and then 504 - so one retry is worth it. Only when the
    # first attempt failed *fast*, though: retrying after the proxy has
    # already spent a minute exhausting all three endpoints just makes the
    # user wait two minutes for the same answer.
    started = time.time()
    data, err = _overpass(ql)
    if err:
        if time.time() - started > 30:
            return None, f"{err} - tap search again", False
        time.sleep(2)
        data, err = _overpass(ql)
        if err:
            return None, err, False

    elements = data.get("elements") or []
    out = []
    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            centre = el.get("center") or {}
            lat, lon = centre.get("lat"), centre.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        cat = _classify(tags)
        if cat is None:
            continue
        out.append({"name": tags.get("name") or tags.get("brand") or cat["label"],
                    "cat": cat["id"], "lat": lat, "lon": lon, "tags": tags})
    return out, None, len(elements) >= _POI_LIMIT


def _explore_tab(page):
    p = PAL()

    if not MAPS_AVAILABLE:
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Map unavailable", 14),
            sub("This build was made without flet-map. Add it to "
                "requirements.txt and rebuild.", 11)]), pad=14)])

    local_url = tileserver.start(M.mbtiles_path())
    offline = local_url is not None
    url_template = local_url or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    status = ft.Text("", size=11, color=p["text2"], selectable=True, expand=True)
    results = ft.Column(spacing=5, scroll=ft.ScrollMode.AUTO)
    marker_layer = fmap.MarkerLayer(markers=[])

    the_map = fmap.Map(
        expand=True,
        initial_center=fmap.MapLatitudeLongitude(*_explore["center"]),
        initial_zoom=_explore["zoom"], min_zoom=3, max_zoom=18,
        bgcolor=p["bg2"],
        on_position_change=lambda e: _remember_camera(e),
        layers=[fmap.TileLayer(url_template=url_template,
                               user_agent_package_name="online.iamprettyfamous.nviidash"),
                marker_layer])

    def _remember_camera(e):
        cam = getattr(e, "camera", None)
        centre = getattr(cam, "center", None) or getattr(e, "coordinates", None)
        lat = getattr(centre, "latitude", None)
        lon = getattr(centre, "longitude", None)
        if lat is not None and lon is not None:
            _explore["center"] = (lat, lon)
        zoom = getattr(cam, "zoom", None)
        if zoom:
            _explore["zoom"] = zoom

    def pin(poi):
        cat = _CAT_BY_ID.get(poi["cat"], POI_CATS[0])
        colour = p[cat["color"]]
        return fmap.Marker(
            coordinates=fmap.MapLatitudeLongitude(poi["lat"], poi["lon"]),
            width=26, height=26,
            content=ft.Container(
                width=24, height=24, border_radius=12, bgcolor=colour,
                border=ft.Border.all(2, ft.Colors.WHITE),
                alignment=ft.Alignment(0, 0),
                tooltip=f"{poi['name']} · {cat['label']}",
                content=ft.Icon(cat["icon"], size=13, color=ft.Colors.BLACK)))

    def redraw():
        marker_layer.markers = [pin(q) for q in _explore["pois"]]
        centre = _explore["center"]
        rows = sorted(_explore["pois"],
                      key=lambda q: _haversine_mi(centre, (q["lat"], q["lon"])))
        results.controls = [_poi_row(p, q, _haversine_mi(centre, (q["lat"], q["lon"])))
                            for q in rows[:40]]
        if _explore["error"]:
            status.value = _explore["error"]
            status.color = p["pink"]
        else:
            status.value = _explore["note"]
            status.color = p["text2"]

    async def search(e=None):
        if not _explore["cats"]:
            _explore["error"] = "pick at least one category"
            _explore["pois"] = []
            redraw()
            page.update()
            return
        lat, lon = _explore["center"]
        bbox = _bbox_for(lat, lon, _explore["zoom"])
        _explore["error"] = ""
        # Public Overpass instances are routinely slow, and the proxy retries
        # across three of them, so say so rather than looking hung.
        _explore["note"] = "searching Overpass — this can take a minute…"
        search_button.disabled = True
        status.value = _explore["note"]
        status.color = p["text2"]
        page.update()

        pois, err, capped = await asyncio.to_thread(
            _fetch_pois, sorted(_explore["cats"]), bbox)
        search_button.disabled = False
        if err:
            _explore["error"] = f"no POIs - {err}"
            _explore["pois"] = []
        else:
            _explore["pois"] = pois
            _explore["error"] = ""
            span_mi = _haversine_mi((bbox[0], bbox[1]), (bbox[2], bbox[1]))
            if not pois:
                _explore["note"] = ("Overpass answered, but there is nothing "
                                    "here in these categories")
            else:
                _explore["note"] = (f"{len(pois)} found within about "
                                    f"{span_mi:.1f} mi of the map centre")
                if capped:
                    _explore["note"] += (f" · hit the {_POI_LIMIT} cap, so some "
                                         f"are missing - zoom in or drop a category")
        redraw()
        page.update()

    def toggle(cat_id):
        async def handler(e):
            if cat_id in _explore["cats"]:
                _explore["cats"].discard(cat_id)
            else:
                _explore["cats"].add(cat_id)
            rebuild_chips()
            page.update()
        return handler

    chip_row = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO, controls=[])

    def rebuild_chips():
        chip_row.controls = [
            ft.Container(
                padding=PAD(4, 9), border_radius=20,
                on_click=toggle(c["id"]),
                bgcolor=ft.Colors.with_opacity(
                    0.22 if c["id"] in _explore["cats"] else 0.06, p[c["color"]]),
                border=BRD(p[c["color"]] if c["id"] in _explore["cats"]
                           else p["border"], 1),
                content=ft.Row(spacing=5, tight=True, controls=[
                    ft.Icon(c["icon"], size=13, color=p[c["color"]]),
                    ft.Text(c["label"], size=11, weight=ft.FontWeight.W_500,
                            color=p["text"] if c["id"] in _explore["cats"] else p["text2"]),
                ]))
            for c in POI_CATS]

    async def jump(e):
        """Centre on one of the places the server already publishes."""
        name = getattr(e.control, "value", None)
        spots, err = _spots()
        if err or not spots:
            _explore["error"] = f"could not look up places - {err or 'none returned'}"
            redraw()
            page.update()
            return
        for s in spots:
            if s.get("name") == name and s.get("lat") is not None:
                _explore["center"] = (s["lat"], s["lon"])
                try:
                    # move_to is a coroutine: not awaiting it leaves the map
                    # where it was and warns to stderr where nobody looks. It
                    # also refuses to run before the map has mounted, which
                    # is a timing accident, not a reason to throw.
                    await the_map.move_to(
                        destination=fmap.MapLatitudeLongitude(s["lat"], s["lon"]),
                        zoom=_explore["zoom"])
                except Exception as exc:                   # noqa: BLE001
                    _explore["error"] = f"could not move the map - {exc}"
                    redraw()
                break
        page.update()

    spots, _ = _spots()
    place_names = [s.get("name") for s in (spots or []) if s.get("name")]
    picker = ft.Dropdown(
        options=[ft.DropdownOption(key=n, text=n) for n in place_names],
        value=_focus["name"] if _focus["name"] in place_names else None,
        hint_text="jump to…", dense=True, filled=True, text_size=12,
        width=150, menu_height=320, on_select=jump,
        border_color=p["border"], focused_border_color=p["accent"],
        color=p["text"])

    search_button = ft.FilledButton("Search this area", height=36,
                                    icon=ft.Icons.SEARCH, on_click=search)

    rebuild_chips()
    if not _explore["pois"] and not _explore["error"]:
        _explore["note"] = "pick categories, then search this area"
    redraw()

    return ft.Column(spacing=8, expand=True, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            chip("offline maps" if offline else "online maps",
                 p["green"] if offline else p["yellow"], ft.Icons.MAP, small=True),
            ft.Container(expand=True),
            *([picker] if place_names else []),
        ]),
        chip_row,
        ft.Container(expand=True, border_radius=12,
                     clip_behavior=ft.ClipBehavior.HARD_EDGE, content=the_map),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[search_button, status]),
        ft.Container(height=190, content=results),
    ])


def _poi_row(p, poi, miles):
    cat = _CAT_BY_ID.get(poi["cat"], POI_CATS[0])
    tags = poi["tags"]
    detail = tags.get("brand") or tags.get("cuisine") or tags.get("operator") or cat["label"]
    return ft.Container(
        padding=PAD(6, 9), border_radius=8, bgcolor=p["surface"],
        border=BRD(p["border"], 1),
        content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(cat["icon"], size=15, color=p[cat["color"]]),
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(poi["name"], size=12, color=p["text"], no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(detail, size=9, color=p["text3"], no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]),
            ft.Text(f"{miles:.1f} mi", size=11, color=p["text2"]),
        ]))


# --------------------------------------------------------------------- page

def build(page):
    return _tabs(page, [("Bay", _bay(page)), ("Explore", _explore_tab(page))],
                 key="weather")
