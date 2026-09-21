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

The Bay tab draws the fourteen stations the server marks ``on_map`` as
temperature-coloured pins on a real map, with a full-screen version behind
the Full map button. The other seven are reference points - Mt Diablo,
Boccardo Loop, Cupertino - and they are listed under their own heading
rather than pinned, because fitting the map to them zooms the Bay stations
into one blob. The colour scale is the website's, stop for stop, so a pin
is the same colour in the browser and on the phone.

**Rain** overlays RainViewer's radar on every map on the page, with frame
stepping, playback and a timestamp. RainViewer is free, keyless and from
here intermittently very slow - the first fetch took over 25 s and the next
0.64 s - so the button says it is loading, refuses a second tap, and either
lands or names the reason it did not. On the website that button used to go
active instantly and then do nothing for twenty seconds.

**Explore** draws a real map and does three things on it. It pins real OSM
data from the server's Overpass proxy; the proxy takes a raw Overpass QL
string as the POST body - not JSON - and the bbox has to be attached to
each statement (``node[amenity=fuel](s,w,n,e);``). Wrapping a union and
bbox-ing the group (``(node[amenity=fuel];)(s,w,n,e)``) is what the website
does and Overpass answers it with HTTP 400, which the proxy reports as
"overpass failed". That is the whole reason POIs looked non-existent. It
searches places through Open-Meteo's keyless geocoder. And it reads the
weather at any point you tap, from Open-Meteo directly, in the same shape
and the same units the dashbored server asks for - so a tapped point and a
named station are directly comparable, and get the same current-plus-three-
days and no more.

Rainviewer and Open-Meteo are not the dashbored server. A failure from
either is reported as itself and never marks the app offline; greying out
every other page over a dead radar tile would be worse than the radar being
dead.

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
from ..ui import BRD, PAD, PAL, S, card, chip, sub, title
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


# ------------------------------------------------------- temperature colour
# The website's scale, stop for stop: blue at 45F through green at 60-65,
# amber at 70-75, red at 85. Kept identical so a pin on the phone and a pin
# in the browser are the same colour for the same reading.
TEMP_STOPS = ((45, "#2196f3"), (55, "#03a9f4"), (60, "#4caf50"),
              (65, "#8bc34a"), (70, "#ffc107"), (75, "#ff9800"),
              (80, "#ff5722"), (85, "#f44336"))


def _lerp_hex(a, b, t):
    ai, bi = int(a[1:], 16), int(b[1:], 16)
    out = []
    for shift in (16, 8, 0):
        ac, bc = (ai >> shift) & 255, (bi >> shift) & 255
        out.append(round(ac + (bc - ac) * t))
    return "#" + "".join(f"{c:02x}" for c in out)


def temp_color(f):
    """The pin colour for a temperature in F, or grey when there isn't one.

    Grey rather than the coldest blue: a station whose current temperature
    the server omitted must not read as 45 degrees.
    """
    if f is None:
        return "#888888"
    try:
        f = float(f)
    except (TypeError, ValueError):
        return "#888888"
    if f <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    if f >= TEMP_STOPS[-1][0]:
        return TEMP_STOPS[-1][1]
    for (t0, c0), (t1, c1) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if t0 <= f <= t1:
            return _lerp_hex(c0, c1, (f - t0) / (t1 - t0))
    return TEMP_STOPS[-1][1]


def wetness(spot):
    """"Dry" / "Maybe" / "Damp" / "Wet" for one spot, the website's buckets.

    Both inputs are real: `current.precipitation` in mm and today's
    `precipitation_probability_max`. Returns None when the server sent
    neither, because "Dry" would then be a claim nobody made.
    """
    cur = spot.get("current") or {}
    daily = spot.get("daily") or {}
    precip = cur.get("precipitation")
    pop = _at(daily.get("precipitation_probability_max") or [], 0)
    if precip is None and pop is None:
        return None
    rain = float(precip or 0)
    if rain > 0.1:
        return "Wet"
    if rain > 0:
        return "Damp"
    if pop is not None and pop >= 30:
        return "Maybe"
    return "Dry"


# ------------------------------------------------------------------ weather

WX_PATH = "/api/weather/multi"

# What the last background read of WX_PATH did. `error` is only ever set by a
# read that actually ran, so a page painted before the first fetch can say it
# is reading rather than claiming the server is down.
_WX = {"busy": False, "error": ""}


def _spots():
    """The server's 21 Bay Area forecasts, or (None, why-not).

    Deliberately not ``M.WEATHER``: that accessor substitutes ``mock.WEATHER``
    whenever the server is unreachable, so the page would draw last winter's
    invented Oakland forecast and look perfectly healthy while offline.

    Deliberately not ``M._get`` either, which is what this used to be. With
    nothing cached ``_get`` fetches inline, and ``/api/weather/multi`` takes
    about a second against the live server: measured against a cold store
    that one call was 1.09 s of this page's 1.09 s build. Flet runs the
    handler that builds a page synchronously on its event loop, so for that
    whole second the old frame stays up and every tap is dropped - which is
    what "the app is super slow" actually is. The store answers in under a
    millisecond and ``_catch_up`` does the fetch behind the page.

    Returns (spots, error). Both None is the third state - nothing stored yet
    and no read has failed - and is not an error; see ``_bay``.
    """
    d = M._cached(WX_PATH)
    if d is None:
        return None, (_WX["error"] or None)
    if isinstance(d, dict):
        if d.get("error"):
            return None, str(d["error"])
        d = [d]
    if not isinstance(d, list) or not d:
        return None, "the server returned no forecasts"
    return d, None


def _repaint(page):
    """Repaint from what is already held. Deliberately not ``rebuilder``.

    Opening a spot, or a forecast landing behind the page, is not a reason to
    invalidate every cached answer in the app and re-read the lot - which is
    what ``rebuilder`` does, because it is meant for writes.
    """
    fn = getattr(S, "rerender", None)
    if callable(fn):
        fn()
        return
    try:
        page.update()
    except Exception:            # noqa: BLE001 - not mounted, or torn down
        pass


def _catch_up(page):
    """Read WX_PATH off the wire behind the page. True if that was scheduled.

    Repaints only when the answer or the error actually changed, so the
    repaint it triggers cannot schedule a repaint of its own for ever.
    """
    async def run():
        if _WX["busy"]:
            return
        _WX["busy"] = True
        had, had_error = M._cached(WX_PATH), _WX["error"]
        try:
            fresh = await asyncio.to_thread(M._get, WX_PATH, 8)
        finally:
            _WX["busy"] = False
        _WX["error"] = ("" if fresh is not None else
                        (config.STATUS.get("detail") or "server unreachable"))
        if fresh != had or _WX["error"] != had_error:
            _repaint(page)

    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return False
    try:
        runner(run)
    except Exception:            # noqa: BLE001 - no loop; we keep what we have
        return False
    return True


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


# The spot the Bay tab is currently focused on. Module level so a refresh or a
# tab switch does not throw the choice away.
_focus = {"name": HOME_NAME}


# ------------------------------------------------------------- rain radar
# RainViewer's index is free and keyless, and from here intermittently very
# slow: the first fetch took over 25 s and the next 0.64 s. The website's
# Rain button used to go active instantly and then do nothing for twenty
# seconds, which is the whole reason this is a state machine rather than a
# boolean. The button says it is loading, refuses a second tap, and either
# lands or says why it did not.
#
# `frames` is [{"time": epoch, "url": tile template}, ...], oldest first.
_rain = {"on": False, "frames": None, "index": 0, "error": "",
         "playing": False, "fetched_at": 0.0}

# How long a fetched frame list is reused. RainViewer publishes about one
# frame every ten minutes.
RAIN_TTL = 300.0
# Radar tiles only exist natively to zoom 7; past that they are scaled up.
RAIN_MAX_NATIVE_ZOOM = 7
RAIN_OPACITY = 0.55
RAIN_LEGEND = (("light", "#00ffff"), ("moderate", "#00aaff"),
               ("heavy", "#ff9500"), ("intense", "#ff00d4"))


def rain_stamp(frames, index, now=None):
    """"3 / 13 · 14:20 (10m ago)" for the frame on screen.

    Pure, so the arithmetic is testable without a radar. Returns "" when
    there is nothing to stamp, rather than a plausible-looking placeholder.
    """
    if not frames:
        return ""
    total = len(frames)
    i = index % total
    when = frames[i].get("time")
    if when is None:
        return f"{i + 1} / {total}"
    now = time.time() if now is None else now
    clock = time.strftime("%H:%M", time.localtime(when))
    ago = int(round((now - when) / 60.0))
    return f"{i + 1} / {total} · {clock} ({'now' if ago <= 0 else f'{ago}m ago'})"


def _rain_layer():
    """The current radar frame as a TileLayer, or None when rain is off."""
    if not (_rain["on"] and _rain["frames"] and MAPS_AVAILABLE):
        return None
    frame = _rain["frames"][_rain["index"] % len(_rain["frames"])]
    return fmap.TileLayer(
        url_template=frame["url"], opacity=RAIN_OPACITY, tile_size=256,
        max_native_zoom=RAIN_MAX_NATIVE_ZOOM, max_zoom=18,
        user_agent_package_name="online.iamprettyfamous.agedashii")


def _stack(base, overlays):
    """Base tiles, then the radar frame if there is one, then the pins."""
    layer = _rain_layer()
    return [base] + ([layer] if layer else []) + list(overlays)


def _rain_controls(page, restack):
    """The Rain button, and when it is on the legend, frame steppers and stamp.

    `restack` re-applies the layer stack to whichever maps are on screen and
    updates the page. One control drives every map, the way the website's
    single Rain button drives its mini, full and explore maps.
    """
    p = PAL()
    # Everything is built whether or not rain is on and toggled with
    # `.visible`, not built conditionally. Building the steppers only when
    # the state says "on" means they do not exist when the state changes,
    # and turning Rain on would light the button and reveal no controls
    # until something else happened to repaint the page.
    stamp = ft.Text(rain_stamp(_rain["frames"], _rain["index"]) if _rain["on"] else "",
                    size=9.5, color=p["text2"], selectable=True)
    note = ft.Text(_rain["error"], size=9.5, color=p["pink"], selectable=True,
                   visible=bool(_rain["error"]))

    label = ft.Text("Rain", size=11, weight=ft.FontWeight.W_600,
                    color=p["text"] if _rain["on"] else p["text2"])
    button = ft.Container(
        padding=PAD(4, 10), border_radius=18, ink=True,
        bgcolor=ft.Colors.with_opacity(0.20 if _rain["on"] else 0.06, p["cyan"]),
        border=BRD(p["cyan"] if _rain["on"] else p["border"], 1),
        tooltip="live rain radar from RainViewer",
        content=ft.Row(spacing=5, tight=True, controls=[
            ft.Icon(ft.Icons.RADAR, size=13, color=p["cyan"]), label]))

    def paint():
        on = _rain["on"]
        label.color = p["text"] if on else p["text2"]
        button.bgcolor = ft.Colors.with_opacity(0.20 if on else 0.06, p["cyan"])
        button.border = BRD(p["cyan"] if on else p["border"], 1)
        stamp.value = rain_stamp(_rain["frames"], _rain["index"]) if on else ""
        note.value = _rain["error"]
        note.color = p["pink"]
        note.visible = bool(_rain["error"])
        steppers.visible = on
        legend.visible = on

    async def toggle(e):
        if _rain["on"]:
            _rain.update(on=False, playing=False, error="")
            paint()
            restack()
            return
        fresh = (_rain["frames"] is not None
                 and time.time() - _rain["fetched_at"] < RAIN_TTL)
        if not fresh:
            # Say it is loading and refuse a second tap before anything is
            # awaited: the first fetch really can take half a minute.
            note.value = "loading rain radar…"
            note.color = p["text2"]
            note.visible = True
            button.disabled = True
            page.update()
            frames, err = await asyncio.to_thread(M.rainviewer_frames)
            button.disabled = False
            if err:
                _rain.update(on=False, error=f"no rain radar - {err}")
            elif not frames:
                _rain.update(on=False, frames=[], fetched_at=time.time(),
                             error="RainViewer answered, and is publishing no "
                                   "radar frames right now")
            else:
                _rain.update(frames=frames, fetched_at=time.time(),
                             index=len(frames) - 1)
            if _rain["error"]:
                paint()
                page.update()
                return
        _rain.update(on=True, error="")
        paint()
        restack()

    button.on_click = toggle

    def step(delta):
        def handler(e):
            if not (_rain["on"] and _rain["frames"]):
                return
            _rain["index"] = (_rain["index"] + delta) % len(_rain["frames"])
            paint()
            restack()
        return handler

    async def play(e):
        if _rain["playing"]:
            _rain["playing"] = False
            play_button.icon = ft.Icons.PLAY_ARROW
            page.update()
            return
        if not (_rain["on"] and _rain["frames"]):
            return
        _rain["playing"] = True
        play_button.icon = ft.Icons.PAUSE
        page.update()
        while _rain["playing"] and _rain["on"] and _rain["frames"]:
            await asyncio.sleep(0.7)
            if not (_rain["playing"] and _rain["on"]):
                break
            _rain["index"] = (_rain["index"] + 1) % len(_rain["frames"])
            paint()
            restack()
        _rain["playing"] = False
        play_button.icon = ft.Icons.PLAY_ARROW
        page.update()

    play_button = ft.IconButton(
        ft.Icons.PAUSE if _rain["playing"] else ft.Icons.PLAY_ARROW,
        icon_size=16, icon_color=p["cyan"], tooltip="play / pause",
        on_click=play)

    steppers = ft.Row(
        spacing=4, visible=_rain["on"], wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.IconButton(ft.Icons.CHEVRON_LEFT, icon_size=16, icon_color=p["cyan"],
                          tooltip="previous frame", on_click=step(-1)),
            play_button,
            ft.IconButton(ft.Icons.CHEVRON_RIGHT, icon_size=16, icon_color=p["cyan"],
                          tooltip="next frame", on_click=step(1)),
            stamp,
        ])
    legend = ft.Row(spacing=8, wrap=True, visible=_rain["on"], controls=[
        ft.Row(spacing=4, tight=True, controls=[
            ft.Container(width=10, height=10, border_radius=3, bgcolor=colour),
            ft.Text(name, size=9, color=p["text3"])])
        for name, colour in RAIN_LEGEND])

    return ft.Column(spacing=5, controls=[
        ft.Row(spacing=6, wrap=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[button, steppers]),
        legend, note,
    ])


# ------------------------------------------------------------- the Bay map

# The mini map's height. Fixed rather than expanding because these panels
# live in a scrollable stack, and a control that expands inside one has no
# height to expand into.
MINI_MAP_HEIGHT = 260
FULL_MAP_HEIGHT = 520


def _station_pin(p, spot, on_pick, *, big=False):
    cur = spot.get("current") or {}
    temp = cur.get("temperature_2m")
    label, _icon, _ck = _wmo(cur.get("weather_code"))
    gust = cur.get("wind_gusts_10m")
    tip = (f"{spot.get('name', '?')} · {_n(temp)}°F · {label} · wind "
           f"{_n(cur.get('wind_speed_10m'))} mph"
           + (f" (g {_n(gust)})" if gust is not None else ""))
    box = ft.Container(
        padding=PAD(1, 5), border_radius=8, bgcolor=temp_color(temp),
        border=ft.Border.all(1, ft.Colors.WHITE), tooltip=tip,
        alignment=ft.Alignment(0, 0),
        on_click=(lambda e, n=spot.get("name"): on_pick(n)) if on_pick else None,
        content=ft.Text(f"{_n(temp)}°", size=10 if not big else 12,
                        weight=ft.FontWeight.W_700, color=ft.Colors.BLACK))
    return fmap.Marker(coordinates=fmap.MapLatitudeLongitude(spot["lat"], spot["lon"]),
                       width=44 if big else 38, height=24 if big else 20,
                       content=box)


def _temp_legend(p):
    return ft.Row(spacing=8, wrap=True, controls=[
        ft.Row(spacing=3, tight=True, controls=[
            ft.Container(width=10, height=10, border_radius=3, bgcolor=colour),
            ft.Text(("≤" if t == TEMP_STOPS[0][0] else
                     "≥" if t == TEMP_STOPS[-1][0] else "") + f"{t}°",
                    size=9, color=p["text3"])])
        for t, colour in TEMP_STOPS])


def _mapped(spots):
    """Only the stations the server marks as being on the map.

    Seven of the twenty-one are reference points well outside the frame -
    Mt Diablo, Boccardo Loop, Cupertino - and fitting the map to them
    zooms it out until the Bay stations overlap into one blob.
    """
    return [s for s in spots
            if s.get("on_map") is not False
            and s.get("lat") is not None and s.get("lon") is not None]


def _bounds(stations):
    lats = [s["lat"] for s in stations]
    lons = [s["lon"] for s in stations]
    return (sum(lats) / len(lats), sum(lons) / len(lons),
            max(lats) - min(lats), max(lons) - min(lons))


def _zoom_for(span_lat, span_lon):
    """A zoom that fits a span. Derived, so adding a station re-fits."""
    span = max(span_lat, span_lon, 0.01)
    zoom = 9.0
    while span < 0.35 and zoom < 13:
        span *= 2
        zoom += 1
    while span > 0.7 and zoom > 6:
        span /= 2
        zoom -= 1
    return zoom


def _bay_map(page, spots, on_pick, restack_holder):
    """The station map: fourteen pins, a Full Map button and the rain toggle."""
    p = PAL()
    stations = _mapped(spots)
    if not MAPS_AVAILABLE:
        return card(ft.Column(spacing=6, controls=[
            title("Map unavailable", 14),
            sub("This build was made without flet-map, so the station map "
                "cannot be drawn. The readings below are unaffected.", 11)]),
            pad=14)
    if not stations:
        return card(sub("The server marked none of its spots as mapped, so "
                        "there is nothing to pin.", 11), pad=12)

    lat, lon, dlat, dlon = _bounds(stations)
    local_url = tileserver.start(M.mbtiles_path())
    offline = local_url is not None
    base = fmap.TileLayer(
        url_template=local_url or "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        user_agent_package_name="online.iamprettyfamous.agedashii")
    pins = fmap.MarkerLayer(markers=[_station_pin(p, s, on_pick) for s in stations])

    the_map = fmap.Map(
        expand=True, initial_center=fmap.MapLatitudeLongitude(lat, lon),
        initial_zoom=_zoom_for(dlat, dlon), min_zoom=5, max_zoom=18,
        bgcolor=p["bg2"], layers=_stack(base, [pins]))

    def restack():
        the_map.layers = _stack(base, [pins])
        page.update()

    restack_holder.append(restack)

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            title("Bay Area map", 13),
            chip("offline tiles" if offline else "online tiles",
                 p["green"] if offline else p["yellow"], ft.Icons.MAP, small=True),
            ft.Container(expand=True),
            ft.TextButton(content=ft.Text("Full map", size=11),
                          icon=ft.Icons.FULLSCREEN,
                          on_click=lambda e: _open_full_map(page, spots)),
        ]),
        ft.Container(height=MINI_MAP_HEIGHT, border_radius=10,
                     clip_behavior=ft.ClipBehavior.HARD_EDGE, content=the_map),
        _temp_legend(p),
        sub(f"{len(stations)} mapped stations · tap a pin to read it below", 10),
    ]))


def _open_full_map(page, spots):
    """A full-screen map with the temperature legend.

    Checks for data before opening. The website's overlay used to add its
    `open` class first, so with the forecasts still loading you got a
    full-screen panel containing a title bar, a close button and nothing.
    """
    p = PAL()
    stations = _mapped(spots or [])
    if not MAPS_AVAILABLE or not stations:
        page.show_dialog(ft.AlertDialog(
            bgcolor=p["surface"],
            title=ft.Text("No map to show", size=14, color=p["text"]),
            content=ft.Text(
                "This build has no map extension."
                if not MAPS_AVAILABLE else
                "The forecasts have not loaded, so there are no stations to "
                "pin. Try again in a moment.", size=11, color=p["text2"])))
        return

    lat, lon, dlat, dlon = _bounds(stations)
    base = fmap.TileLayer(
        url_template=(tileserver.start(M.mbtiles_path())
                      or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        user_agent_package_name="online.iamprettyfamous.agedashii")
    pins = fmap.MarkerLayer(
        markers=[_station_pin(p, s, None, big=True) for s in stations])
    the_map = fmap.Map(
        expand=True, initial_center=fmap.MapLatitudeLongitude(lat, lon),
        initial_zoom=_zoom_for(dlat, dlon), min_zoom=5, max_zoom=18,
        bgcolor=p["bg2"], layers=_stack(base, [pins]))

    dlg = ft.AlertDialog(
        modal=True, bgcolor=p["surface"],
        title=ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.MAP, size=16, color=p["accent"]),
            ft.Text("Bay Area weather map", size=14, color=p["text"], expand=True)]),
        content=ft.Container(
            width=760, height=FULL_MAP_HEIGHT,
            content=ft.Column(spacing=8, expand=True, controls=[
                ft.Container(expand=True, border_radius=10,
                             clip_behavior=ft.ClipBehavior.HARD_EDGE,
                             content=the_map),
                _temp_legend(p),
            ])))
    dlg.actions = [ft.TextButton("Close",
                                 on_click=lambda e: page.pop_dialog())]
    page.show_dialog(dlg)


# ------------------------------------------------------------- spot tiles

def _mini_forecast(p, spot, days=3):
    """The compact per-spot 3-day row the website puts on every tile."""
    daily = spot.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return None
    codes = daily.get("weather_code") or []
    mx = daily.get("temperature_2m_max") or []
    mn = daily.get("temperature_2m_min") or []
    pop = daily.get("precipitation_probability_max") or []

    def day(i):
        _label, icon, ckey = _wmo(_at(codes, i))
        return ft.Column(spacing=1, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                         controls=[
            ft.Text(_weekday(dates[i], i), size=8.5, color=p["text3"]),
            ft.Icon(icon, size=13, color=p[ckey]),
            ft.Text(f"{_n(_at(mx, i))}°/{_n(_at(mn, i))}°", size=9, color=p["text2"]),
            ft.Text(f"{_n(_at(pop, i))}%", size=8.5, color=p["cyan"]),
        ])

    return ft.Row(spacing=10, controls=[day(i) for i in range(min(days, len(dates)))])


def _spot_tile(p, spot, selected, on_pick, on_toggle=None):
    """One station, with everything the website's tile shows.

    Built only for a spot whose chevron is open - see ``_spot_row``.
    """
    cur = spot.get("current") or {}
    label, icon, ckey = _wmo(cur.get("weather_code"))
    gust = cur.get("wind_gusts_10m")
    wind = _n(cur.get("wind_speed_10m")) + " mph"
    if gust is not None:
        wind += f" g{_n(gust)}"
    wet = wetness(spot)
    on = spot.get("name") == selected

    def stat(name, value, colour=None):
        return ft.Row(spacing=4, tight=True, controls=[
            ft.Text(name, size=9, color=p["text3"]),
            ft.Text(value, size=10, weight=ft.FontWeight.W_600,
                    color=colour or p["text"])])

    rows = [
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(icon, size=18, color=p[ckey]),
            ft.Text(f"{_n(cur.get('temperature_2m'))}°", size=20,
                    weight=ft.FontWeight.W_700, color=p["text"]),
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(spot.get("name", "?"), size=11.5,
                        weight=ft.FontWeight.W_600, color=p["text"],
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(f"{spot.get('region', '')} · {label}".strip(" ·"), size=9,
                        color=p["text3"], no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]),
        ] + ([ft.IconButton(ft.Icons.EXPAND_LESS, icon_size=15,
                            icon_color=p["text2"], tooltip="collapse this spot",
                            on_click=lambda e, n=spot.get("name"): on_toggle(n))]
             if on_toggle else [])),
        ft.Row(spacing=10, wrap=True, controls=[
            stat("feels", f"{_n(cur.get('apparent_temperature'))}°"),
            stat("wind", wind),
            stat("hum", f"{_n(cur.get('relative_humidity_2m'))}%"),
        ] + ([stat("sky", wet, p["cyan"] if wet != "Dry" else p["green"])]
             if wet else [])),
    ]
    forecast = _mini_forecast(p, spot)
    if forecast is not None:
        rows.append(forecast)

    return ft.Container(
        col={"xs": 12, "md": 6}, padding=PAD(8, 10), border_radius=10, ink=True,
        on_click=lambda e, n=spot.get("name"): on_pick(n),
        bgcolor=ft.Colors.with_opacity(0.10, p["accent"]) if on else p["bg2"],
        border=BRD(p["accent"] if on else
                   (p["cyan"] if wet in ("Wet", "Damp") else p["border"]), 1),
        content=ft.Column(spacing=6, controls=rows))


# Which spots in the All spots list are opened out to their full tile.
# Module level so picking a spot, stepping a radar frame or a fresh forecast
# landing behind the page does not fold them all back up again.
_OPEN_SPOTS = set()


def _spot_row(p, spot, selected, on_pick, on_toggle):
    """One station in a line: temperature, name, sky, and a chevron.

    The website's tile - feels-like, wind, humidity, wetness and a 3-day
    strip - is still here, in ``_spot_tile``, one tap away on the chevron.
    It stopped being the default because twenty-one of them were 790 of this
    page's 1130 controls, and everything on it for the *selected* spot is
    already on screen above in the hero card and the forecast strip.
    """
    cur = spot.get("current") or {}
    temp = cur.get("temperature_2m")
    label, _icon, ckey = _wmo(cur.get("weather_code"))
    wet = wetness(spot)
    name = spot.get("name", "?")
    on = name == selected
    is_open = name in _OPEN_SPOTS
    return ft.Container(
        col={"xs": 12, "md": 6}, padding=PAD(5, 9), border_radius=9, ink=True,
        on_click=lambda e, n=name: on_pick(n),
        tooltip=f"read {name} in the card above",
        bgcolor=ft.Colors.with_opacity(0.10, p["accent"]) if on else p["bg2"],
        border=BRD(p["accent"] if on else
                   (p["cyan"] if wet in ("Wet", "Damp") else p["border"]), 1),
        content=ft.Row(spacing=7, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
            ft.Text(f"{_n(temp)}°", size=14, weight=ft.FontWeight.W_700,
                    color=temp_color(temp)),
            ft.Text(name, size=11.5, color=p["text"], expand=True, no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(f"{label} · {wet}" if wet else label, size=9,
                    color=p[ckey] if wet is None else p["text3"], no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS),
            ft.IconButton(ft.Icons.EXPAND_LESS if is_open else ft.Icons.EXPAND_MORE,
                          icon_size=15, icon_color=p["text2"],
                          tooltip=("hide the detail" if is_open else
                                   f"wind, humidity and 3 days for {name}"),
                          on_click=lambda e, n=name: on_toggle(n)),
        ]))


def _all_spots(p, spots, selected, on_pick, on_toggle):
    """Every spot, mapped first and then the reference points, as the website
    splits them. Collapsed to a line each; the chevron opens the full tile."""
    on_map = _mapped(spots)
    off_map = [s for s in spots if s not in on_map]

    def cells(group):
        return ft.ResponsiveRow(
            columns=12, spacing=8, run_spacing=8,
            controls=[(_spot_tile(p, s, selected, on_pick, on_toggle)
                       if s.get("name") in _OPEN_SPOTS
                       else _spot_row(p, s, selected, on_pick, on_toggle))
                      for s in group])

    blocks = [
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            title("All spots", 13), ft.Container(expand=True),
            sub("tap one to read it above · chevron for its detail", 10)]),
        cells(on_map),
    ]
    if off_map:
        blocks += [
            ft.Divider(height=1, color=p["border"]),
            ft.Row(spacing=6, controls=[
                ft.Icon(ft.Icons.WRONG_LOCATION, size=13, color=p["text3"]),
                ft.Text("Reference spots — not mapped", size=10.5,
                        color=p["text3"])]),
            cells(off_map),
        ]
    blocks.append(sub(
        f"{len(spots)} spots · {len(on_map)} mapped + {len(off_map)} reference "
        f"· temps °F · winds mph · 3-day forecast · precip % = chance", 10))
    blocks.append(sub(
        "Current conditions and a 3-day daily forecast are everything "
        "/api/weather/multi returns. It sends no hourly series, so there is "
        "no hour-by-hour strip to show.", 10))
    return ft.Column(spacing=8, controls=blocks)


def _bay(page):
    p = PAL()
    spots, err = _spots()
    reload_page = rebuilder(page, build)
    # Always, not only when there is nothing yet: `_get` off the UI thread is
    # how this page stays current without ever making the first paint wait
    # for Open-Meteo. It repaints only if the answer changed.
    fetching = _catch_up(page)
    # Filled in by whichever map panels get built, so the rain toggle can
    # restack every one of them without knowing which are visible.
    restackers = []

    def restack_all():
        for fn in restackers:
            fn()
        page.update()

    header = ft.Column(spacing=6, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            title("Bay Area", 15), ft.Container(expand=True),
            ft.IconButton(ft.Icons.REFRESH, icon_color=p["accent"], icon_size=20,
                          tooltip="re-read from the server",
                          on_click=lambda e: reload_page()),
        ]),
        _rain_controls(page, restack_all),
    ])

    if spots is None:
        return ft.Column(spacing=10, controls=[header, _nothing_yet(p, err, fetching)])

    if not any(s.get("name") == _focus["name"] for s in spots):
        _focus["name"] = spots[0].get("name")

    def repaint():
        """Which spot is in the hero card, and which are open, are layout.

        Deliberately not `reload_page`: that is `rebuilder`, which marks
        every cached answer in the app stale and re-reads. Putting a network
        round trip behind a disclosure triangle is the mistake this whole
        change is undoing.
        """
        _repaint(page)

    def pick(name):
        _focus["name"] = name
        repaint()

    def toggle(name):
        _OPEN_SPOTS.symmetric_difference_update({name})
        repaint()

    spot = _pick(spots, _focus["name"])

    return panels.layout("weather", {
        "header": lambda: header,
        "map": lambda: _bay_map(page, spots, pick, restackers),
        "hero": lambda: _hero(spot),
        "forecast": lambda: _forecast(spot),
        "regional": lambda: card(_all_spots(p, spots, _focus["name"], pick,
                                            toggle)),
    })


def _nothing_yet(p, err, fetching):
    """The card for a page with no forecast on it, saying which kind of none.

    Three different states, and calling them all "No weather" is how a page
    that is simply still reading looks broken.
    """
    if err:
        return card(ft.Column(spacing=6, controls=[
            ft.Row(spacing=8, controls=[
                ft.Icon(ft.Icons.CLOUD_OFF, size=18, color=p["pink"]),
                ft.Text("No weather", size=14, weight=ft.FontWeight.W_600,
                        color=p["text"])]),
            ft.Text(err, size=11, color=p["pink"], selectable=True),
            sub(f"asked {config.SERVER_URL}{WX_PATH}", 10),
        ]), pad=14)
    if fetching or _WX["busy"]:
        return card(ft.Row(spacing=10, controls=[
            ft.ProgressRing(width=15, height=15, stroke_width=2),
            ft.Column(spacing=2, expand=True, controls=[
                ft.Text("Reading the forecast", size=13,
                        weight=ft.FontWeight.W_600, color=p["text"]),
                sub(f"{config.SERVER_URL}{WX_PATH} · it lands here when it "
                    f"arrives", 10)])]), pad=14)
    return card(ft.Column(spacing=6, controls=[
        ft.Text("No forecast stored yet", size=13, weight=ft.FontWeight.W_600,
                color=p["text"]),
        sub(f"Nothing has been read from {config.SERVER_URL}{WX_PATH} on this "
            f"device, and nothing is reading it now. Hit refresh above.", 10),
    ]), pad=14)


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

# The Explore map's height. Fixed for the same reason the Bay map's is:
# these panels sit in a scrollable stack, where an expanding control has
# nothing to expand into.
EXPLORE_MAP_HEIGHT = 320

# What tapping the map, or picking a search result, last turned up:
# {"name", "lat", "lon", "current", "daily"} or an error.
_point = {"name": "", "lat": None, "lon": None, "data": None,
          "error": "", "busy": False}

# The place search box.
_search = {"text": "", "results": None, "error": "", "busy": False}


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
    point_layer = fmap.MarkerLayer(markers=[])
    base_layer = fmap.TileLayer(
        url_template=url_template,
        user_agent_package_name="online.iamprettyfamous.agedashii")

    the_map = fmap.Map(
        expand=True,
        initial_center=fmap.MapLatitudeLongitude(*_explore["center"]),
        initial_zoom=_explore["zoom"], min_zoom=3, max_zoom=18,
        bgcolor=p["bg2"],
        on_position_change=lambda e: _remember_camera(e),
        on_tap=lambda e: page.run_task(_tapped, e),
        layers=_stack(base_layer, [marker_layer, point_layer]))

    def restack():
        the_map.layers = _stack(base_layer, [marker_layer, point_layer])

    def restack_and_update():
        restack()
        page.update()

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
        point_layer.markers = ([_tapped_pin(p)] if _point["lat"] is not None else [])
        restack()
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
        detail.controls = [_point_card(p)]

    async def _load_point(lat, lon, name):
        """Fetch current + 3-day for an arbitrary point, saying so first.

        Open-Meteo is a third party, not the dashbored server, so a failure
        here is reported as itself and never marks the app offline.
        """
        _point.update(name=name, lat=lat, lon=lon, data=None, error="", busy=True)
        redraw()
        page.update()
        data, err = await asyncio.to_thread(M.point_forecast, lat, lon)
        _point["busy"] = False
        if err:
            _point.update(data=None, error=f"no forecast for this point - {err}")
        else:
            _point.update(data=data, error="")
        redraw()
        page.update()

    async def _tapped(e):
        coords = getattr(e, "coordinates", None)
        lat = getattr(coords, "latitude", None)
        lon = getattr(coords, "longitude", None)
        if lat is None or lon is None:
            return
        await _load_point(lat, lon, f"{lat:.3f}, {lon:.3f}")

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
            _explore["error"] = ("could not look up places - "
                                 + (err or "no forecast is stored on this "
                                           "device yet; open the Bay tab"))
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

    # ---------------------------------------------------------- place search
    detail = ft.Column(spacing=8)
    found = ft.Column(spacing=5)
    place_box = ft.TextField(
        hint_text="Search a city or place…", value=_search["text"], dense=True,
        filled=True, text_size=12, expand=True, border_color=p["border"],
        focused_border_color=p["accent"], color=p["text"])
    place_note = ft.Text(_search["error"] or "", size=10,
                         color=p["pink"] if _search["error"] else p["text2"],
                         selectable=True)

    def draw_found():
        found.controls = []
        for r in (_search["results"] or []):
            where = ", ".join(x for x in (r["admin1"], r["country"]) if x)
            found.controls.append(ft.Container(
                padding=PAD(5, 9), border_radius=8, ink=True, bgcolor=p["bg2"],
                border=BRD(p["border"], 1),
                on_click=lambda e, r=r: page.run_task(_goto_place, r),
                content=ft.Column(spacing=0, controls=[
                    ft.Text(r["name"], size=11.5, color=p["text"]),
                    ft.Text(f"{where} · {r['lat']:.3f}, {r['lon']:.3f}", size=9,
                            color=p["text3"]),
                ])))
        if _search["results"] == []:
            found.controls.append(sub("No matches.", 10.5))

    async def _goto_place(r):
        _search["results"] = None
        draw_found()
        _explore["center"] = (r["lat"], r["lon"])
        try:
            # move_to is a coroutine: not awaiting it leaves the map where it
            # was and warns to stderr where nobody looks.
            await the_map.move_to(
                destination=fmap.MapLatitudeLongitude(r["lat"], r["lon"]),
                zoom=max(_explore["zoom"], 10.0))
        except Exception as exc:                          # noqa: BLE001
            _explore["error"] = f"could not move the map - {exc}"
        await _load_point(r["lat"], r["lon"], r["name"])

    async def do_place_search(e=None):
        text = (place_box.value or "").strip()
        _search["text"] = text
        if len(text) < 2:
            _search.update(results=None, error="type at least two letters")
            place_note.value = _search["error"]
            place_note.color = p["pink"]
            draw_found()
            page.update()
            return
        # Open-Meteo's geocoder is a third party and can be slow; say so
        # before awaiting it and refuse a second submit meanwhile.
        _search["error"] = ""
        place_note.value = "searching…"
        place_note.color = p["text2"]
        place_button.disabled = True
        page.update()
        places, err = await asyncio.to_thread(M.geocode, text)
        place_button.disabled = False
        if err:
            _search.update(results=None, error=f"place search failed - {err}")
            place_note.value = _search["error"]
            place_note.color = p["pink"]
        else:
            _search.update(results=places, error="")
            place_note.value = (f"{len(places)} match"
                                f"{'' if len(places) == 1 else 'es'}"
                                if places else "")
            place_note.color = p["text2"]
        draw_found()
        page.update()

    place_box.on_submit = do_place_search
    place_button = ft.IconButton(ft.Icons.SEARCH, icon_color=p["accent"],
                                 tooltip="search places", on_click=do_place_search)

    rebuild_chips()
    draw_found()
    if not _explore["pois"] and not _explore["error"]:
        _explore["note"] = "pick categories, then search this area"
    redraw()

    return panels.layout("weather.explore", {
        "search": lambda: card(ft.Column(spacing=6, controls=[
            ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[place_box, place_button]),
            place_note,
            found,
            sub("Tap anywhere on the map for that point's weather.", 10),
        ])),
        "cats": lambda: card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                chip("offline maps" if offline else "online maps",
                     p["green"] if offline else p["yellow"], ft.Icons.MAP,
                     small=True),
                ft.Container(expand=True),
                *([picker] if place_names else []),
            ]),
            chip_row,
            _rain_controls(page, restack_and_update),
        ])),
        "map": lambda: ft.Container(
            height=EXPLORE_MAP_HEIGHT, border_radius=12,
            clip_behavior=ft.ClipBehavior.HARD_EDGE, content=the_map),
        "point": lambda: detail,
        "results": lambda: card(ft.Column(spacing=6, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[search_button, status]),
            results,
        ])),
    })


def _tapped_pin(p):
    """A pin on whatever point was last tapped or picked from the search."""
    return fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(_point["lat"], _point["lon"]),
        width=26, height=26,
        content=ft.Container(
            width=22, height=22, border_radius=11,
            bgcolor=temp_color(((_point["data"] or {}).get("current") or {})
                               .get("temperature_2m")),
            border=ft.Border.all(2, ft.Colors.WHITE),
            alignment=ft.Alignment(0, 0), tooltip=_point["name"],
            content=ft.Icon(ft.Icons.PLACE, size=12, color=ft.Colors.BLACK)))


def _point_card(p):
    """Current conditions and three days for a tapped point, or why not."""
    if _point["lat"] is None:
        return card(sub("Tap the map, or search a place, for its weather.", 11),
                    pad=12)
    where = f"{_point['lat']:.3f}, {_point['lon']:.3f}"
    if _point["busy"]:
        return card(ft.Row(spacing=8, controls=[
            ft.ProgressRing(width=14, height=14, stroke_width=2),
            sub(f"reading {_point['name']} ({where})…", 11)]), pad=12)
    if _point["error"]:
        return card(ft.Column(spacing=4, controls=[
            ft.Text(_point["name"], size=12, color=p["text"]),
            ft.Text(_point["error"], size=10.5, color=p["pink"], selectable=True),
        ]), pad=12)
    # A tapped point is named by its own coordinates, so do not print them
    # twice; a searched place has a real name and the coordinates belong
    # beside it.
    spot = {"name": _point["name"],
            "region": "tapped point" if _point["name"] == where else where,
            "current": (_point["data"] or {}).get("current") or {},
            "daily": (_point["data"] or {}).get("daily") or {}}
    return ft.Column(spacing=8, controls=[_hero(spot), _forecast(spot)])


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
    """Both tabs deferred: whichever is on screen is the only one built.

    Explore draws a second map, a POI layer and a place search, and used to
    be built whether or not it was ever looked at. See ``lab._tabs`` for the
    mechanism and for what a slow build actually costs.
    """
    return _tabs(page, [("Bay", lambda: _bay(page)),
                        ("Explore", lambda: _explore_tab(page))],
                 key="weather")
