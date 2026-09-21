"""Trips: pick a ride, edit a route, replay a recorded one, navigate it.

Four tabs, the website's four, in the order a ride actually happens:

  Selekt   the trip library, the two-point offline planner, and the builder
           that stacks legs into one ride and saves it
  Produce  the route editor - waypoints, forks, and the trip report
  Rides    recorded GPX: list, replay with a scrubber, speed-limit overlay,
           plus live turn-by-turn against whatever Selekt last planned
  xNav     the live map: GPS, track recording, and route guidance with
           speed limits, curves, elevation and POIs

What crosses the wire has to match the browser exactly, because both write
the same files on the same server: see `_trips_parts` for the shapes. What
does *not* have to match is which of the two can work with the radio off,
and that is the app's whole reason to exist - the two-point planner and the
track recorder below need no network at all, and say which parts do.

Nothing here invents a number. A distance that has not been routed shows as
"—", not as 0 mi; a ride with no timestamps says its replay is paced, not
measured; and anything that needs Valhalla or Overpass refuses with the
reason when there is no connection rather than queueing a request that
could never be replayed.
"""
import asyncio

import flet as ft

from .. import api as M
from .. import panels
from .. import prefs
from .. import routing as R
from .. import tileserver
from ..ui import PAL, PAD, BRD, card, chip, title, sub
from ._forms import rebuilder
from . import _trips_parts as T
from . import xnav
from .eatzen import _dismiss, _show
from .lab import _TAB_INDEX, _tabs

try:                      # optional: the app still runs without the extensions
    import flet_map as fmap
    import flet_geolocator as fgeo
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = fgeo = None
    MAPS_AVAILABLE = False

#: Vulcan Lofts. Same start point the website's trip builder uses.
HOME = T.HOME_LATLON

ONLINE_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# The planned route outlives a tab switch and a page rebuild on purpose: you
# plan in Selekt, then ride with Rides open, and losing the route in between
# would make the feature useless.
_PLAN = {"route": None, "from": None, "to": None,
         "from_text": "", "to_text": ""}

# One geolocator for guidance, created only if guidance is ever switched on,
# and reused across rebuilds. `render` is re-pointed at the live controls
# every build so an old closure never keeps updating a discarded screen.
_FOLLOW = {"geo": None, "on": False, "render": None, "position": None}

# Everything the page is holding between rebuilds. A tab switch repaints the
# whole page, so anything that lives in a local would be thrown away: the
# stacked legs, the routes already computed for them, which cards are open,
# and whatever the Produce editor has unsaved.
_S = {
    "builder": [],          # legs stacked into the trip being built
    "leg_routes": {},       # trip id -> {"status": pending|ok|error, ...}
    "combined": None,       # the one chained route through every stacked leg
    "detail": set(),        # trip ids whose detail panel is open
    "editor": None,         # the Produce editor's working copy
    "ride": None,           # {"id", "points", "stats", "timeline", ...}
    "note": "",             # last thing worth telling her, per page
}

# Per-build callbacks so a background route can repaint one card's numbers
# instead of rebuilding the page under her fingers.
_REFRESH = {"cards": {}, "builder": None}


def build(page):
    return _tabs(page, [("Selekt", _selekt(page)), ("Produce", _produce(page)),
                        ("Rides", _rides(page)), ("xNav", xnav.build(page))],
                 key="trips")


def _goto_tab(page, index):
    """Switch Trips sub-tab and repaint. The index is _tabs' own memory."""
    _TAB_INDEX["trips"] = index
    _rebuild(page)


def _rebuild(page):
    rebuilder(page, build)()


def _run(page, coro_fn, *args):
    """page.run_task when there is a loop; quietly skip when there is not.

    The import check and the tests build pages against a stub with no event
    loop. A page that paints its placeholders and stops is the honest
    outcome there - better than a build that raises.
    """
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return None
    try:
        return runner(coro_fn, *args)
    except Exception:                                  # noqa: BLE001
        return None


def _update(page):
    try:
        page.update()
    except Exception:                                  # noqa: BLE001
        pass


# --------------------------------------------------------------- utilities

def _fmt_latlon(pt):
    return f"{pt[0]:.5f}, {pt[1]:.5f}" if pt else ""


def _zoom_for(points):
    """A zoom level that shows the whole route without maths theatre."""
    lats = [q[0] for q in points]
    lons = [q[1] for q in points]
    # Longitude degrees are ~0.8 of a latitude degree at this latitude.
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.8, 1e-4)
    for level, limit in ((15, 0.004), (14, 0.008), (13, 0.016), (12, 0.033),
                         (11, 0.066), (10, 0.13), (9, 0.26), (8, 0.52),
                         (7, 1.05)):
        if span <= limit:
            z = level
            break
    else:
        z = 6
    return z


def _centre(points):
    lats = [q[0] for q in points]
    lons = [q[1] for q in points]
    return ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)


# Substring -> icon, most specific first. Matches both the offline router's
# phrasing ("bear left onto X") and Valhalla's ("Turn left onto X.").
_TURN_ICONS = [
    ("u-turn", ft.Icons.U_TURN_LEFT), ("uturn", ft.Icons.U_TURN_LEFT),
    ("arrive", ft.Icons.FLAG), ("destination", ft.Icons.FLAG),
    ("roundabout", ft.Icons.ROUNDABOUT_LEFT),
    ("sharp left", ft.Icons.TURN_SHARP_LEFT),
    ("sharp right", ft.Icons.TURN_SHARP_RIGHT),
    ("bear left", ft.Icons.TURN_SLIGHT_LEFT),
    ("slight left", ft.Icons.TURN_SLIGHT_LEFT),
    ("keep left", ft.Icons.TURN_SLIGHT_LEFT),
    ("bear right", ft.Icons.TURN_SLIGHT_RIGHT),
    ("slight right", ft.Icons.TURN_SLIGHT_RIGHT),
    ("keep right", ft.Icons.TURN_SLIGHT_RIGHT),
    ("exit", ft.Icons.FORK_RIGHT), ("ramp", ft.Icons.FORK_RIGHT),
    ("merge", ft.Icons.MERGE),
    ("turn left", ft.Icons.TURN_LEFT), ("turn right", ft.Icons.TURN_RIGHT),
    ("left", ft.Icons.TURN_LEFT), ("right", ft.Icons.TURN_RIGHT),
    ("head", ft.Icons.NAVIGATION), ("drive", ft.Icons.NAVIGATION),
]


def _turn_icon(instruction):
    low = (instruction or "").lower()
    for needle, icon in _TURN_ICONS:
        if needle in low:
            return icon
    return ft.Icons.STRAIGHT


def _router_chip(p):
    """One chip that says, plainly, what routing is available right now."""
    st = R.status()
    phase = st["phase"]
    if phase == "ready":
        return chip(f"offline router · {st['nodes']:,} nodes", p["green"],
                    ft.Icons.ALT_ROUTE, small=True)
    if phase == "loading":
        return chip("loading offline router…", p["yellow"],
                    ft.Icons.HOURGLASS_TOP, small=True)
    if phase == "failed":
        return chip("offline graph unreadable", p["pink"],
                    ft.Icons.ERROR_OUTLINE, small=True)
    return chip("no offline graph · server routing", p["yellow"],
                ft.Icons.CLOUD, small=True)


def _tile_url():
    """The on-device archive if there is one, otherwise online tiles."""
    local = tileserver.start(M.mbtiles_path()) if MAPS_AVAILABLE else None
    return local or ONLINE_TILES, local is not None


def _tiles_chip(p, offline):
    return chip("offline maps" if offline else "online maps",
                p["green"] if offline else p["yellow"], ft.Icons.MAP, small=True)


def _pin(colour, icon, at=None, size=22):
    return fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(*(at or HOME)),
        width=size + 4, height=size + 4, visible=at is not None,
        content=ft.Container(
            width=size, height=size, border_radius=size / 2, bgcolor=colour,
            border=ft.Border.all(2, ft.Colors.WHITE),
            content=ft.Icon(icon, size=size - 9, color=ft.Colors.WHITE)))


def _numbered(colour, label, at):
    return fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(*at), width=22, height=22,
        content=ft.Container(
            width=20, height=20, border_radius=10, bgcolor=colour,
            border=ft.Border.all(1, ft.Colors.WHITE),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(str(label), size=10, color=ft.Colors.BLACK,
                            weight=ft.FontWeight.W_700)))


def _empty(p, text, icon=ft.Icons.INFO_OUTLINE):
    """One honest empty state. Never a placeholder row that looks like data."""
    return ft.Row(spacing=8, controls=[
        ft.Icon(icon, size=16, color=p["text2"]),
        ft.Text(text, size=11, color=p["text2"], expand=True)])


def _stat(p, label, value, unit=""):
    return ft.Container(
        padding=PAD(6, 10), border_radius=8, bgcolor=p["bg2"],
        border=BRD(p["border"], 1),
        content=ft.Column(spacing=1, tight=True, controls=[
            ft.Text(label, size=9, color=p["text2"]),
            ft.Row(spacing=2, tight=True,
                   vertical_alignment=ft.CrossAxisAlignment.END, controls=[
                       ft.Text(str(value), size=15, weight=ft.FontWeight.W_700,
                               color=p["text"]),
                       ft.Text(unit, size=9, color=p["text2"]) if unit
                       else ft.Container()])]))


def _elev_profile(p, profile, width_hint=44):
    """The elevation profile, as bars. No chart library ships with Flet 0.86.

    Bars rather than a line because a Container per sample is the only thing
    that draws without one, and at this size a 44-bar profile reads the same
    as a sparkline would.
    """
    values = [q["ele_ft"] for q in profile if q.get("ele_ft") is not None]
    if len(values) < 2:
        return _empty(p, "no elevation data for this route")
    lo, hi = min(values), max(values)
    span = max(1.0, hi - lo)
    step = max(1, len(values) // width_hint)
    bars = []
    for v in values[::step]:
        frac = (v - lo) / span
        bars.append(ft.Container(
            width=4, height=6 + frac * 46, border_radius=2,
            bgcolor=ft.Colors.with_opacity(0.35 + 0.6 * frac, p["accent"])))
    up, down = T.profile_climb(profile)
    return ft.Column(spacing=4, controls=[
        ft.Row(spacing=8, controls=[
            sub("Elevation profile", 10), ft.Container(expand=True),
            ft.Text(f"↑{up} ↓{down} ft", size=10, color=p["text2"])]),
        ft.Container(
            padding=PAD(4, 0),
            content=ft.Row(spacing=1, controls=bars, wrap=False,
                           vertical_alignment=ft.CrossAxisAlignment.END,
                           scroll=ft.ScrollMode.AUTO)),
        ft.Row([ft.Text(f"{int(lo)} ft", size=9, color=p["text2"]),
                ft.Container(expand=True),
                ft.Text(f"{int(hi)} ft", size=9, color=p["text2"])]),
    ])


# ============================================================== Selekt tab

def _selekt(page):
    """The trip library, the offline planner, and the trip builder."""
    p = PAL()
    status = ft.Text(_S["note"], size=11, color=p["text2"], selectable=True)

    def say(msg, bad=False):
        _S["note"] = msg
        status.value = msg
        status.color = p["pink"] if bad else p["text2"]
        _update(page)

    plan = _planner(page, p)
    trips = T.trips_all()
    _REFRESH["cards"] = {}

    # Fire the per-leg routes once, in the background, for legs never routed.
    # The website does this on every load; here a route already computed
    # survives the rebuild, so a tab switch does not re-ask Valhalla eleven
    # times.
    if trips:
        pending = [t for t in trips
                   if t.get("id") and t["id"] not in _S["leg_routes"]]
        if pending:
            _run(page, _route_legs, page, pending)

    body = panels.layout("trips.selekt", {
        "planner": plan["card"],
        "plannermap": plan["map"],
        "turns": plan["turns"],
        "builder": lambda: _builder_card(page, p, say),
        "cards": lambda: _trip_cards(page, p, trips, say),
    })
    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=body),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


async def _route_legs(page, legs):
    """Route each leg on its own, off the UI thread, repainting as they land."""
    for leg in legs:
        _S["leg_routes"][leg["id"]] = {"status": "pending"}
    for leg in legs:
        wps = T.home_wrap(leg)
        if len(wps) < 2:
            _S["leg_routes"][leg["id"]] = {"status": "error",
                                           "error": "no waypoints to route"}
        else:
            res = await asyncio.to_thread(T.valhalla_route, wps)
            _S["leg_routes"][leg["id"]] = (
                dict(res, status="ok", waypoints=wps) if res.get("ok")
                else {"status": "error", "error": res.get("error", "no route")})
        refresh = _REFRESH["cards"].get(leg["id"])
        if callable(refresh):
            refresh()
    _update(page)


async def _route_combined(page):
    """One chained route through every stacked leg. Home at both ends."""
    legs = _S["builder"]
    if not legs:
        _S["combined"] = None
    else:
        _S["combined"] = {"status": "pending"}
        refresh = _REFRESH["builder"]
        if callable(refresh):
            refresh()
        _update(page)
        chain = T.builder_chain(legs)
        res = await asyncio.to_thread(T.valhalla_route, chain)
        _S["combined"] = (dict(res, status="ok", waypoints=chain)
                          if res.get("ok")
                          else {"status": "error",
                                "error": res.get("error", "no route")})
    refresh = _REFRESH["builder"]
    if callable(refresh):
        refresh()
    _update(page)


# ------------------------------------------------------------- the planner

def _planner(page, p):
    """The two-point planner: the one thing here that works with no signal.

    Returns three zero-argument builders so the panel system can reorder,
    resize or hide the card, the map and the turn list independently.
    """
    # Start the graph read now, on a background thread. Opening this page
    # must not block the UI for the ~50 ms it takes, and app startup must not
    # pay for it at all on the launches that never come here.
    R.start_loading()

    units = prefs.unit_system()

    frm_field = ft.TextField(
        hint_text="start — lat, lon", dense=True, filled=True, text_size=12,
        expand=True, value=_PLAN["from_text"], border_color=p["border"],
        focused_border_color=p["accent"], color=p["text"])
    to_field = ft.TextField(
        hint_text="destination — lat, lon (or tap the map)", dense=True,
        filled=True, text_size=12, expand=True, value=_PLAN["to_text"],
        border_color=p["border"], focused_border_color=p["accent"],
        color=p["text"])

    status_text = ft.Text("", size=11, color=p["text2"], selectable=True)
    summary_text = ft.Text("", size=13, weight=ft.FontWeight.W_600,
                           color=p["text"])
    source_row = ft.Row(spacing=6, wrap=True, controls=[_router_chip(p)])
    turns_col = ft.Column(spacing=0, controls=[])
    turns_card = ft.Container(visible=False)

    # ---- the map, exactly the way XNAV sets one up -----------------------
    if MAPS_AVAILABLE:
        url_template, tiles_offline = _tile_url()
        source_row.controls.append(_tiles_chip(p, tiles_offline))

        route_line = fmap.PolylineMarker(
            coordinates=[], color=p["accent"], stroke_width=5,
            border_color=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
            border_stroke_width=1)
        line_layer = fmap.PolylineLayer(polylines=[route_line])

        start_pin = _pin(p["green"], ft.Icons.PLAY_ARROW)
        end_pin = _pin(p["pink"], ft.Icons.FLAG)
        pin_layer = fmap.MarkerLayer(markers=[start_pin, end_pin])

        the_map = fmap.Map(
            expand=True,
            initial_center=fmap.MapLatitudeLongitude(*HOME),
            initial_zoom=11, min_zoom=3, max_zoom=18, bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=url_template,
                user_agent_package_name="online.iamprettyfamous.nviidash"),
                line_layer, pin_layer])

        def on_map_tap(e):
            coords = getattr(e, "coordinates", None)
            if coords is None:
                return
            to_field.value = _fmt_latlon((coords.latitude, coords.longitude))
            _PLAN["to_text"] = to_field.value
            end_pin.coordinates = coords
            end_pin.visible = True
            _update(page)

        the_map.on_tap = on_map_tap
        map_box = ft.Container(height=300, border_radius=12,
                               clip_behavior=ft.ClipBehavior.HARD_EDGE,
                               content=the_map)
    else:                                  # pragma: no cover - build-dependent
        the_map = route_line = start_pin = end_pin = None
        map_box = card(sub("Map unavailable — this build has no flet-map. "
                           "Routing still works; the turn list is below.", 11),
                       pad=10)

    # ---- drawing a result -------------------------------------------------
    def clear_map():
        if route_line is not None:
            route_line.coordinates = []
            start_pin.visible = end_pin.visible = False

    def draw(res):
        summary_text.value = res.summary(units)
        source_row.controls = [_router_chip(p)]
        if MAPS_AVAILABLE:
            source_row.controls.append(
                _tiles_chip(p, tileserver.status()["running"]))
        if res.ok:
            # The one thing that must never be wrong on this screen.
            source_row.controls.append(
                chip("route computed offline" if res.offline
                     else "route from the server",
                     p["green"] if res.offline else p["cyan"],
                     ft.Icons.ALT_ROUTE if res.offline else ft.Icons.CLOUD_DONE,
                     small=True))
        status_text.value = res.reason
        status_text.color = p["text2"] if res.ok else p["pink"]

        turns_col.controls = []
        if not res.ok:
            clear_map()
            turns_card.visible = False
            summary_text.value = ""
            return

        for i, m in enumerate(res.maneuvers):
            turns_col.controls.append(ft.Container(
                padding=PAD(6, 0),
                border=ft.Border(bottom=ft.BorderSide(
                    1, p["border"] if i < len(res.maneuvers) - 1
                    else ft.Colors.TRANSPARENT)),
                content=ft.Row(spacing=10, controls=[
                    ft.Icon(_turn_icon(m.instruction), size=17,
                            color=p["accent"]),
                    ft.Text(m.instruction, size=12, color=p["text"], expand=True),
                    ft.Text(R.fmt_distance(m.distance_m, units), size=11,
                            color=p["text2"]),
                ])))
        turns_card.visible = True

        if route_line is not None and res.points:
            route_line.coordinates = [fmap.MapLatitudeLongitude(a, b)
                                      for a, b in res.points]
            start_pin.coordinates = fmap.MapLatitudeLongitude(*res.points[0])
            end_pin.coordinates = fmap.MapLatitudeLongitude(*res.points[-1])
            start_pin.visible = end_pin.visible = True

    async def fit(res):
        """Frame the whole route. Separate from draw() because move_to is a
        coroutine, and draw() also runs at build time where there is no
        loop to await it on."""
        if the_map is None or not res.ok or not res.points:
            return
        try:
            await the_map.move_to(
                destination=fmap.MapLatitudeLongitude(*_centre(res.points)),
                zoom=_zoom_for(res.points))
        except Exception:                  # noqa: BLE001 - map not mounted yet
            pass

    # ---- handlers ---------------------------------------------------------
    route_button = ft.FilledButton("Route", height=38, icon=ft.Icons.ALT_ROUTE)

    async def do_route(e=None):
        a = R.parse_latlon(frm_field.value or "")
        b = R.parse_latlon(to_field.value or "")
        if a is None or b is None:
            which = "start" if a is None else "destination"
            status_text.value = (f"the {which} is not a coordinate — type "
                                 f"'lat, lon', press the GPS button, or tap "
                                 f"the map")
            status_text.color = p["pink"]
            _update(page)
            return
        _PLAN.update({"from": a, "to": b, "from_text": frm_field.value,
                      "to_text": to_field.value})
        route_button.disabled = True
        summary_text.value = ""
        status_text.value = "routing…"
        status_text.color = p["text2"]
        _update(page)
        # Off the UI thread: a 40 km offline search is ~150 ms and the
        # server fallback is a network round trip. Either would freeze Flet.
        res = await R.route_async(a, b)
        route_button.disabled = False
        _PLAN["route"] = res if res.ok else None
        draw(res)
        _update(page)
        await fit(res)

    route_button.on_click = do_route
    frm_field.on_submit = do_route
    to_field.on_submit = do_route

    async def use_gps(e):
        pos = await _one_fix(page)
        if pos is None:
            status_text.value = "no GPS fix yet"
            status_text.color = p["pink"]
        else:
            frm_field.value = _fmt_latlon(pos)
            _PLAN["from_text"] = frm_field.value
            status_text.value = ""
            if start_pin is not None:
                start_pin.coordinates = fmap.MapLatitudeLongitude(*pos)
                start_pin.visible = True
        _update(page)

    def use_home(e):
        frm_field.value = _fmt_latlon(HOME)
        _PLAN["from_text"] = frm_field.value
        _update(page)

    def swap(e):
        frm_field.value, to_field.value = to_field.value, frm_field.value
        _PLAN.update({"from_text": frm_field.value, "to_text": to_field.value})
        _update(page)

    def clear(e):
        _PLAN.update({"route": None, "from": None, "to": None})
        summary_text.value = ""
        status_text.value = ""
        turns_col.controls = []
        turns_card.visible = False
        clear_map()
        _update(page)

    turns_card.content = card(ft.Column(spacing=6, controls=[
        ft.Row([title("Turn by turn", 13), ft.Container(expand=True),
                sub("follow it in Rides", 10)]),
        turns_col,
    ]), pad=10)

    # Redraw a route planned before the last rebuild, so switching tabs or
    # saving a trip does not silently throw the route away.
    if _PLAN["route"] is not None:
        draw(_PLAN["route"])

    planner = card(ft.Column(spacing=8, controls=[
        ft.Row([title("Plan a route", 14), ft.Container(expand=True)]),
        source_row,
        ft.Row(spacing=6, controls=[
            frm_field,
            ft.IconButton(ft.Icons.MY_LOCATION, icon_color=p["accent"],
                          tooltip="start here", on_click=use_gps),
            ft.IconButton(ft.Icons.HOME, icon_color=p["accent"],
                          tooltip="start at home", on_click=use_home),
        ]),
        ft.Row(spacing=6, controls=[
            to_field,
            ft.IconButton(ft.Icons.SWAP_VERT, icon_color=p["accent"],
                          tooltip="swap", on_click=swap),
        ]),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   route_button,
                   ft.OutlinedButton("Clear", height=38, on_click=clear),
                   ft.Container(expand=True), summary_text,
               ]),
        status_text,
    ]), pad=12)

    return {"card": lambda: planner, "map": lambda: map_box,
            "turns": lambda: turns_card}


async def _one_fix(page):
    """A single GPS position, or None. Never raises at the call site."""
    if not MAPS_AVAILABLE:
        return None
    try:
        geo = _geolocator(page)
        if geo is None:
            return None
        await geo.request_permission()
        pos = await geo.get_current_position()
        lat = getattr(pos, "latitude", None)
        lon = getattr(pos, "longitude", None)
        return (lat, lon) if lat is not None and lon is not None else None
    except Exception:                                  # noqa: BLE001
        return None


def _geolocator(page):
    """The one guidance geolocator, created on first use and then reused.

    Not created at build time: a page that merely got opened should not turn
    the GPS on, and every rebuild would otherwise register another service.
    """
    if not MAPS_AVAILABLE:
        return None
    if _FOLLOW["geo"] is not None:
        return _FOLLOW["geo"]

    def on_position(e):
        pos = getattr(e, "position", None) or e
        lat, lon = getattr(pos, "latitude", None), getattr(pos, "longitude", None)
        if lat is None or lon is None:
            return
        _FOLLOW["position"] = (lat, lon)
        render = _FOLLOW["render"]
        if callable(render):
            render()

    def on_error(e):                                   # pragma: no cover
        _FOLLOW["position"] = None

    try:
        geo = fgeo.Geolocator(
            configuration=fgeo.GeolocatorConfiguration(
                accuracy=fgeo.GeolocatorPositionAccuracy.BEST),
            on_position_change=on_position, on_error=on_error)
    except Exception:                                  # noqa: BLE001
        return None
    services = getattr(page, "services", None)
    if isinstance(services, list):
        # A Flet service is unregistered the moment nothing holds a strong
        # reference to it, hence both the module dict and page.services.
        services.append(geo)
    _FOLLOW["geo"] = geo
    return geo


# ------------------------------------------------------------ trip builder

_LEG_COLOURS = ["#ffd700", "#ffae00", "#ff7c00", "#ff5500",
                "#ff2d00", "#e00050", "#a000ff", "#5500ff"]


def _builder_card(page, p, say):
    """Stack legs into one ride: reorder, close the loop, save it."""
    legs = _S["builder"]
    if not legs:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.LAYERS, size=16, color=p["text2"]),
                    title("Trip builder", 14)]),
            _empty(p, "Nothing stacked yet. Add a leg from a trip card below "
                      "and its route joins the chain."),
        ]), pad=12)

    stats_row = ft.Row(spacing=10, wrap=True, controls=[])
    legs_col = ft.Column(spacing=4, controls=[])

    if MAPS_AVAILABLE:
        url_template, tiles_offline = _tile_url()
        chain_line = fmap.PolylineMarker(
            coordinates=[], color=p["accent"], stroke_width=5,
            border_color=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
            border_stroke_width=1)
        line_layer = fmap.PolylineLayer(polylines=[chain_line])
        marker_layer = fmap.MarkerLayer(markers=[])
        the_map = fmap.Map(
            expand=True, initial_center=fmap.MapLatitudeLongitude(*HOME),
            initial_zoom=10, min_zoom=3, max_zoom=18, bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=url_template,
                user_agent_package_name="online.iamprettyfamous.nviidash"),
                line_layer, marker_layer])
        map_box = ft.Container(height=260, border_radius=10,
                               clip_behavior=ft.ClipBehavior.HARD_EDGE,
                               content=the_map)
    else:                                  # pragma: no cover - build-dependent
        chain_line = marker_layer = None
        map_box = _empty(p, "no map in this build — the numbers below are real")

    def move(idx, delta):
        j = idx + delta
        if 0 <= j < len(legs):
            legs[idx], legs[j] = legs[j], legs[idx]
            _run(page, _route_combined, page)
            _rebuild(page)

    def drop(idx):
        legs.pop(idx)
        if legs:
            _run(page, _route_combined, page)
        else:
            _S["combined"] = None
        _rebuild(page)

    def paint():
        cr = _S["combined"] or {}
        state = cr.get("status")
        ok = state == "ok"
        stats_row.controls = [
            _stat(p, "distance", f"{cr['distance_mi']:.1f}" if ok else "—", "mi"),
            _stat(p, "moving", T.fmt_minutes(cr["duration_min"]) if ok else "—"),
            _stat(p, "legs", len(legs)),
        ]
        if ok and (cr.get("ascent_ft") is not None or cr.get("descent_ft") is not None):
            stats_row.controls.append(
                _stat(p, "climb",
                      f"↑{cr.get('ascent_ft') or 0} ↓{cr.get('descent_ft') or 0}", "ft"))
        if state == "pending":
            stats_row.controls.append(
                chip("routing…", p["yellow"], ft.Icons.HOURGLASS_TOP, small=True))
        elif state == "error":
            stats_row.controls.append(
                chip(cr.get("error") or "no route", p["pink"],
                     ft.Icons.ERROR_OUTLINE, small=True))

        legs_col.controls = []
        for i, leg in enumerate(legs):
            colour = _LEG_COLOURS[i % len(_LEG_COLOURS)]
            legs_col.controls.append(ft.Row(
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=10, height=10, border_radius=5,
                                 bgcolor=colour),
                    ft.Text(f"{i + 1}.", size=11, color=p["text2"]),
                    ft.Text(leg.get("name", ""), size=12, color=p["text"],
                            expand=True, max_lines=2),
                    ft.IconButton(ft.Icons.ARROW_UPWARD, icon_size=15,
                                  icon_color=p["text2"], tooltip="move up",
                                  disabled=i == 0,
                                  on_click=lambda e, k=i: move(k, -1)),
                    ft.IconButton(ft.Icons.ARROW_DOWNWARD, icon_size=15,
                                  icon_color=p["text2"], tooltip="move down",
                                  disabled=i == len(legs) - 1,
                                  on_click=lambda e, k=i: move(k, +1)),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=15,
                                  icon_color=p["pink"], tooltip="remove",
                                  on_click=lambda e, k=i: drop(k)),
                ]))

        if chain_line is not None:
            markers, pts = [], []
            n = 0
            for i, leg in enumerate(legs):
                colour = _LEG_COLOURS[i % len(_LEG_COLOURS)]
                for w in T.waypoints_of(leg):
                    n += 1
                    markers.append(_numbered(colour, n, (w["lat"], w["lon"])))
                    pts.append((w["lat"], w["lon"]))
            marker_layer.markers = markers
            chain_line.coordinates = (
                [fmap.MapLatitudeLongitude(a, b) for a, b in cr["points"]]
                if ok and cr.get("points") else
                [fmap.MapLatitudeLongitude(a, b) for a, b in pts])

    _REFRESH["builder"] = paint
    paint()
    if _S["combined"] is None:
        _run(page, _route_combined, page)

    def clear(e):
        _S["builder"] = []
        _S["combined"] = None
        _rebuild(page)

    def close_loop(e):
        leg, why = T.close_loop_leg(legs)
        if leg is None:
            say(why, bad=True)
            return
        legs.append(leg)
        _run(page, _route_combined, page)
        _rebuild(page)

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   ft.Icon(ft.Icons.ROUTE, size=16, color=p["accent"]),
                   title("Trip builder", 14), ft.Container(expand=True)]),
        stats_row,
        legs_col,
        map_box,
        ft.Row(spacing=6, wrap=True, controls=[
            ft.OutlinedButton("Clear", height=34, icon=ft.Icons.ROTATE_LEFT,
                              on_click=clear),
            ft.OutlinedButton("Close loop", height=34, icon=ft.Icons.CIRCLE,
                              on_click=close_loop),
            ft.FilledButton("Save trip", height=34, icon=ft.Icons.SAVE,
                            on_click=lambda e: _save_trip_dialog(page, p, say)),
        ]),
    ]), pad=12)


def _save_trip_dialog(page, p, say):
    """Name + id, with the website's duplicate-id refusal."""
    legs = list(_S["builder"])
    if not legs:
        say("nothing stacked to save", bad=True)
        return
    existing = {t.get("id") for t in (T.trips_all() or [])}
    suggested = " → ".join(
        (str(t.get("name", "")).replace("→", "-").split("-")[0].strip().split(" ") or [""])[-1]
        for t in legs[:3])

    name_field = ft.TextField(label="Trip name", value=suggested, dense=True,
                              filled=True, text_size=12, color=p["text"],
                              border_color=p["border"],
                              focused_border_color=p["accent"])
    id_field = ft.TextField(label="URL id (lowercase, hyphens)", dense=True,
                            filled=True, text_size=12, color=p["text"],
                            hint_text=T.slugify(suggested) or "auto-from-name",
                            border_color=p["border"],
                            focused_border_color=p["accent"])
    problem = ft.Text("", size=11, color=p["pink"])

    def on_name(e):
        id_field.hint_text = T.slugify(name_field.value) or "auto-from-name"
        _update(page)

    name_field.on_change = on_name

    dlg = ft.AlertDialog(title=ft.Text("Save built trip", size=15))

    def confirm(e):
        name = (name_field.value or "").strip()
        tid = (id_field.value or "").strip() or T.slugify(name)
        if not name or not tid:
            problem.value = "a name and an id are both required"
            _update(page)
            return
        if tid in existing:
            problem.value = f"'{tid}' already exists — pick another id"
            id_field.value = ""
            _update(page)
            return
        payload = T.builder_payload(legs, name, tid, _S["leg_routes"],
                                    combined=_S["combined"])
        ok, msg = M.trip_save(payload)
        _dismiss(page, dlg)
        if ok:
            _S["builder"] = []
            _S["combined"] = None
            say(f"saved {name} — {msg}")
            _rebuild(page)
        else:
            say(f"not saved - {msg}", bad=True)

    dlg.content = ft.Column(tight=True, spacing=8, width=340, controls=[
        name_field, id_field,
        sub("Waypoints are flattened with Home stamped at both ends, the "
            "same way the website saves it.", 10),
        problem,
    ])
    dlg.actions = [
        ft.TextButton("Cancel", on_click=lambda e: _dismiss(page, dlg)),
        ft.FilledButton("Save", on_click=confirm),
    ]
    _show(page, dlg)


# -------------------------------------------------------------- trip cards

def _trip_cards(page, p, trips, say):
    """The library, grouped the way the website groups it."""
    if trips is None:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.MAP, size=16, color=p["text2"]),
                    title("Trips", 14)]),
            _empty(p, "Not connected, and no copy of the trip list on this "
                      "device yet. Nothing is being shown from memory.",
                   ft.Icons.CLOUD_OFF),
        ]), pad=12)
    if not trips:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.MAP, size=16, color=p["text2"]),
                    title("Trips", 14)]),
            _empty(p, "No trips saved yet. Stack legs above and Save trip, or "
                      "start one from scratch in Produce."),
        ]), pad=12)

    open_count = len(_S["detail"] & {t.get("id") for t in trips})
    all_open = open_count >= len(trips)

    def toggle_all(e):
        if all_open:
            _S["detail"].clear()
        else:
            _S["detail"].update(t["id"] for t in trips if t.get("id"))
        _rebuild(page)

    sections = []
    for label, items in T.group_by_family(trips):
        sections.append(ft.Container(
            padding=PAD(6, 0),
            content=ft.Text(f"{label} · {len(items)} "
                            f"leg{'s' if len(items) != 1 else ''}",
                            size=10, color=p["text2"],
                            weight=ft.FontWeight.W_600)))
        sections.extend(_trip_card(page, p, t, say) for t in items)

    # These trips are real - they were fetched and kept - but saying so
    # matters when the distances beside them cannot be, because measuring
    # one needs Valhalla. "Sample data" would be a lie here; silence would
    # leave a row of dashes with no explanation.
    stale = ([_empty(p, "Not connected. These are the trips as of the last "
                        "sync, which is why the distances are dashes — "
                        "measuring one needs the routing server.",
                     ft.Icons.CLOUD_OFF)]
             if not M.STATUS_LIVE() else [])

    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=6, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(ft.Icons.MAP, size=16, color=p["accent"]),
                       title("Trips", 14),
                       sub("stack legs into a ride", 10),
                       ft.Container(expand=True),
                       ft.TextButton(
                           "Hide all" if all_open else "Show all",
                           icon=ft.Icons.EXPAND_LESS if all_open
                           else ft.Icons.EXPAND_MORE,
                           on_click=toggle_all),
                   ]),
            *stale,
        ]), pad=10),
        *sections,
    ])


def _trip_card(page, p, t, say):
    """One trip: what it is, what it measures, and what can be done with it."""
    tid = t.get("id", "")
    added = any(x.get("id") == tid for x in _S["builder"])
    detail_open = tid in _S["detail"]
    wps = T.waypoints_of(t)

    stats_row = ft.Row(spacing=12, wrap=True, controls=[])

    def paint_stats():
        route = _S["leg_routes"].get(tid) or {}
        state = route.get("status")
        mi = route.get("distance_mi") if state == "ok" else t.get("distance_mi")
        mins = route.get("duration_min") if state == "ok" else t.get("duration_min")
        bits = [
            _mini(p, ft.Icons.ROUTE,
                  f"{mi} mi" if isinstance(mi, (int, float)) else "—"),
            _mini(p, ft.Icons.SCHEDULE,
                  T.fmt_minutes(mins) if isinstance(mins, (int, float))
                  else ("routing…" if state == "pending" else "—")),
        ]
        if state == "ok" and (route.get("ascent_ft") is not None
                              or route.get("descent_ft") is not None):
            bits.append(_mini(p, ft.Icons.TRENDING_UP,
                              f"↑{route.get('ascent_ft') or 0} "
                              f"↓{route.get('descent_ft') or 0} ft"))
        if t.get("difficulty"):
            bits.append(_mini(p, ft.Icons.SPEED, str(t["difficulty"])))
        if wps:
            bits.append(_mini(p, ft.Icons.PIN_DROP, f"{len(wps)} waypoints"))
        if state == "error":
            bits.append(ft.Text(route.get("error", "no route"), size=10,
                                color=p["pink"]))
        stats_row.controls = bits

    paint_stats()
    _REFRESH["cards"][tid] = lambda: (paint_stats(), _update(page))

    tags = [chip(str(tg), p["cyan"], small=True) for tg in (t.get("tags") or [])[:5]]
    if t.get("forks_at"):
        tags.append(chip(f"FORK · {t['forks_at']}", p["orange"],
                         ft.Icons.CALL_SPLIT, small=True))
    if t.get("fork_origin"):
        tags.append(chip(f"after {t['fork_origin']}", p["purple"],
                         ft.Icons.MERGE, small=True))

    badges = []
    for row in T.flatten_report(t):
        tag = T.find_tag(row["label"])
        text = row["label"] + (f" ({row['scope']})" if row["scope"] != "route" else "")
        badges.append(chip(text, T.severity_colour(tag["severity"], p),
                           T.tag_icon(tag["icon"]), small=True))

    def add(e):
        if added:
            return
        _S["builder"].append(t)
        _run(page, _route_combined, page)
        if tid not in _S["leg_routes"]:
            _run(page, _route_legs, page, [t])
        _rebuild(page)

    def toggle(e):
        _S["detail"].discard(tid) if detail_open else _S["detail"].add(tid)
        _rebuild(page)

    def edit(e):
        _S["editor"] = _editor_state(t, is_new=False)
        _goto_tab(page, 1)

    def gpx(e):
        opener = getattr(page, "launch_url", None)
        if callable(opener):
            opener(T.gpx_url(tid))
            say(f"opening the GPX for {t.get('name', tid)}")
        else:                                          # pragma: no cover
            say(T.gpx_url(tid))

    body = [
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
               controls=[
                   title(t.get("name", tid), 14),
                   ft.Container(expand=True),
                   chip(str(t.get("type", "leg")), p["accent"], small=True)]),
        *([ft.Row(spacing=5, wrap=True, controls=tags)] if tags else []),
        stats_row,
    ]
    if t.get("biome"):
        body.append(sub(str(t["biome"]), 11))
    if t.get("vibe"):
        body.append(sub(str(t["vibe"]), 11))
    if badges:
        body.append(ft.Row(spacing=5, wrap=True, controls=badges))
    body.append(ft.Row(spacing=6, wrap=True, controls=[
        ft.OutlinedButton("Edit", height=32, icon=ft.Icons.EDIT, on_click=edit),
        ft.OutlinedButton("GPX", height=32, icon=ft.Icons.DOWNLOAD, on_click=gpx),
        ft.OutlinedButton("Hide" if detail_open else "Detail", height=32,
                          icon=ft.Icons.EXPAND_LESS if detail_open
                          else ft.Icons.EXPAND_MORE, on_click=toggle),
        ft.FilledButton("In trip" if added else "Add to trip", height=32,
                        icon=ft.Icons.CHECK if added else ft.Icons.ADD,
                        disabled=added, on_click=add),
    ]))
    if detail_open:
        body.append(_trip_detail(page, p, t))
    return card(ft.Column(spacing=6, controls=body), pad=10)


def _mini(p, icon, text):
    return ft.Row(spacing=4, tight=True, controls=[
        ft.Icon(icon, size=13, color=p["text2"]),
        ft.Text(text, size=11, color=p["text"])])


def _trip_detail(page, p, t):
    """Waypoints, notes, the leg's map and - if it has been fetched - its
    elevation profile."""
    tid = t.get("id", "")
    route = _S["leg_routes"].get(tid) or {}
    shown = route.get("waypoints") or T.home_wrap(t)
    rows = []
    for i, w in enumerate(shown):
        first_last = i == 0 or i == len(shown) - 1
        rows.append(ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                           controls=[
                               ft.Container(width=8, height=8, border_radius=4,
                                            margin=ft.Margin(0, 5, 0, 0),
                                            bgcolor=p["green"] if first_last
                                            else p["accent"]),
                               ft.Column(spacing=0, expand=True, controls=[
                                   ft.Text(str(w.get("name", "")), size=11,
                                           color=p["text"]),
                                   *([sub(str(w["note"]), 10)] if w.get("note") else []),
                               ])]))

    parts = []
    if MAPS_AVAILABLE and shown:
        url_template, _ = _tile_url()
        pts = [(w["lat"], w["lon"]) for w in shown]
        line = fmap.PolylineMarker(
            coordinates=[fmap.MapLatitudeLongitude(a, b)
                         for a, b in (route.get("points") or pts)],
            color=p["accent"], stroke_width=4)
        markers = [_numbered(p["accent"], i + 1, q) for i, q in enumerate(pts)]
        the_map = fmap.Map(
            expand=True,
            initial_center=fmap.MapLatitudeLongitude(*_centre(pts)),
            initial_zoom=_zoom_for(pts), min_zoom=3, max_zoom=18,
            bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=url_template,
                user_agent_package_name="online.iamprettyfamous.nviidash"),
                fmap.PolylineLayer(polylines=[line]),
                fmap.MarkerLayer(markers=markers)])
        parts.append(ft.Container(height=200, border_radius=10,
                                  clip_behavior=ft.ClipBehavior.HARD_EDGE,
                                  content=the_map))

    elev_box = ft.Container()
    if route.get("status") == "ok":
        cached = route.get("profile")
        if cached:
            elev_box.content = _elev_profile(p, cached)
        elif route.get("profile_error"):
            elev_box.content = _empty(p, route["profile_error"])
        else:
            elev_box.content = _empty(p, "fetching elevation…",
                                      ft.Icons.HOURGLASS_TOP)
            _run(page, _fetch_profile, page, tid, elev_box, p)
    parts.append(elev_box)

    return ft.Container(
        padding=PAD(8, 0),
        border=ft.Border(top=ft.BorderSide(1, p["border"])),
        content=ft.Column(spacing=8, controls=[
            *parts,
            ft.Column(spacing=4, controls=rows),
            *([sub(str(t["notes"]), 10)] if t.get("notes") else []),
        ]))


async def _fetch_profile(page, tid, box, p):
    route = _S["leg_routes"].get(tid) or {}
    if route.get("status") != "ok":
        return
    profile, err = await asyncio.to_thread(T.elevations, route["points"])
    if profile:
        route["profile"] = profile
        box.content = _elev_profile(p, profile)
    else:
        route["profile_error"] = err
        box.content = _empty(p, err)
    _update(page)


# ============================================================= Produce tab

def _editor_state(trip, is_new=False):
    """A working copy. Never the trip itself - an abandoned edit must not
    quietly rewrite what is on screen behind it."""
    return {
        "orig": dict(trip),
        "is_new": bool(is_new),
        "name": trip.get("name", ""),
        "waypoints": [dict(w) for w in T.waypoints_of(trip)],
        "report": T.normalise_report(trip.get("report")),
        "forks_at": trip.get("forks_at", "") or "",
        "fork_origin": trip.get("fork_origin", "") or "",
        "scope": ["route"],
        "checked": set(),
        "preview": None,
        "note": "",
    }


def _blank_route():
    return {"id": "", "name": "New route", "type": "loop",
            "waypoints": [dict(T.HOME, note="Start")],
            "report": {"route": [], "waypoints": {}},
            "tags": [], "biome": "", "vibe": "", "surface": ""}


def _produce(page):
    """The route editor. Same fields, same save shape, as the website's."""
    p = PAL()
    trips = T.trips_all() or []
    st = _S["editor"]

    status = ft.Text((st or {}).get("note", ""), size=11, color=p["text2"],
                     selectable=True)

    def say(msg, bad=False):
        if st is not None:
            st["note"] = msg
        status.value = msg
        status.color = p["pink"] if bad else p["text2"]
        _update(page)

    def picker():
        options = [ft.DropdownOption(key="", text="— pick a route to edit —")]
        options += [ft.DropdownOption(key=t["id"], text=t.get("name", t["id"]))
                    for t in trips if t.get("id")]

        def pick(e):
            value = getattr(e.control, "value", "") or ""
            chosen = next((t for t in trips if t.get("id") == value), None)
            if chosen is not None:
                _S["editor"] = _editor_state(chosen, is_new=False)
                _rebuild(page)

        def new_route(e):
            _S["editor"] = _editor_state(_blank_route(), is_new=True)
            _rebuild(page)

        def close(e):
            _S["editor"] = None
            _rebuild(page)

        # Flet 0.86's Dropdown fires on_select, never on_change; assigning
        # on_change is accepted silently and the picker does nothing.
        dd = ft.Dropdown(
            value=(st or {}).get("orig", {}).get("id", "") if st else "",
            options=options, on_select=pick, dense=True, filled=True,
            text_size=12, width=250, menu_height=340,
            border_color=p["border"], color=p["text"])
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(ft.Icons.EDIT_ROAD, size=16, color=p["accent"]),
                       title("Produce", 14),
                       sub("edit routes · waypoints · section tags", 10),
                   ]),
            ft.Row(spacing=6, wrap=True, controls=[
                dd,
                ft.OutlinedButton("New", height=34, icon=ft.Icons.ADD,
                                  on_click=new_route),
                *([ft.OutlinedButton("Close", height=34, icon=ft.Icons.CLOSE,
                                     on_click=close)] if st else []),
            ]),
            *([] if trips else
              [_empty(p, "No saved routes to pick from — New starts one from "
                         "scratch, at Home.")]),
        ]), pad=12)

    if st is None:
        return ft.Column(spacing=10, expand=True, controls=[
            picker(),
            card(_empty(p, "Pick a route above to edit, or press New."), pad=12),
            ft.Container(padding=PAD(0, 4), content=status),
        ])

    name_field = ft.TextField(
        value=st["name"], hint_text="Trip name", dense=True, filled=True,
        text_size=13, expand=True, color=p["text"], border_color=p["border"],
        focused_border_color=p["accent"],
        on_change=lambda e: st.update(name=e.control.value))

    map_holder = ft.Container()
    wp_col = ft.Column(spacing=6, controls=[])
    preview_row = ft.Row(spacing=10, wrap=True, controls=[])

    def repaint_all():
        paint_map()
        paint_waypoints()
        paint_preview()
        _update(page)

    # ---- map ------------------------------------------------------------
    if MAPS_AVAILABLE:
        url_template, tiles_offline = _tile_url()
        edit_line = fmap.PolylineMarker(coordinates=[], color=p["accent"],
                                        stroke_width=4)
        edit_layer = fmap.PolylineLayer(polylines=[edit_line])
        edit_markers = fmap.MarkerLayer(markers=[])
        centre = (_centre([(w["lat"], w["lon"]) for w in st["waypoints"]])
                  if st["waypoints"] else HOME)
        edit_map = fmap.Map(
            expand=True, initial_center=fmap.MapLatitudeLongitude(*centre),
            initial_zoom=12, min_zoom=3, max_zoom=18, bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=url_template,
                user_agent_package_name="online.iamprettyfamous.nviidash"),
                edit_layer, edit_markers])

        def on_tap(e):
            coords = getattr(e, "coordinates", None)
            if coords is None:
                return
            st["waypoints"].append({
                "name": f"Waypoint {len(st['waypoints']) + 1}",
                "lat": round(coords.latitude, 6),
                "lon": round(coords.longitude, 6), "note": ""})
            repaint_all()
            _run(page, _preview_route, page, st, preview_row, p, edit_line)

        edit_map.on_tap = on_tap
        map_holder.content = ft.Column(spacing=6, controls=[
            ft.Container(height=300, border_radius=10,
                         clip_behavior=ft.ClipBehavior.HARD_EDGE,
                         content=edit_map),
            ft.Row(spacing=6, controls=[
                _tiles_chip(p, tiles_offline),
                sub("tap the map to drop a waypoint", 10)]),
        ])
    else:                                  # pragma: no cover - build-dependent
        edit_line = edit_markers = None
        map_holder.content = _empty(
            p, "no map in this build — waypoints can still be typed below")

    def paint_map():
        if edit_line is None:
            return
        pts = [(w["lat"], w["lon"]) for w in st["waypoints"]]
        preview = (st["preview"] or {}).get("points")
        edit_line.coordinates = [fmap.MapLatitudeLongitude(a, b)
                                 for a, b in (preview or pts)]
        edit_markers.markers = [_numbered(p["accent"], i + 1, q)
                                for i, q in enumerate(pts)]

    # ---- waypoint list ---------------------------------------------------
    def set_field(i, field, value):
        if 0 <= i < len(st["waypoints"]):
            st["waypoints"][i][field] = value

    def set_coord(i, field, raw):
        try:
            set_field(i, field, round(float(raw), 6))
        except (TypeError, ValueError):
            say(f"'{raw}' is not a number", bad=True)
            return
        repaint_all()
        _run(page, _preview_route, page, st, preview_row, p, edit_line)

    def move_wp(i, delta):
        j = i + delta
        if not (0 <= j < len(st["waypoints"])):
            return
        st["waypoints"][i], st["waypoints"][j] = st["waypoints"][j], st["waypoints"][i]
        st["report"] = T.move_report_index(st["report"], i, j)
        repaint_all()
        _run(page, _preview_route, page, st, preview_row, p, edit_line)

    def del_wp(i):
        st["waypoints"].pop(i)
        st["report"] = T.delete_report_index(st["report"], i)
        st["scope"] = _shift_scope(st["scope"], i)
        repaint_all()
        _run(page, _preview_route, page, st, preview_row, p, edit_line)

    def add_home(e):
        st["waypoints"].append(dict(T.HOME, note="Return home"))
        repaint_all()
        _run(page, _preview_route, page, st, preview_row, p, edit_line)

    def paint_waypoints():
        rows = []
        for i, w in enumerate(st["waypoints"]):
            rows.append(ft.Container(
                padding=PAD(6, 0),
                border=ft.Border(bottom=ft.BorderSide(1, p["border"])),
                content=ft.Column(spacing=4, controls=[
                    ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Text(f"{i + 1}", size=11, color=p["text2"],
                                       width=16),
                               ft.TextField(
                                   value=w.get("name", ""), hint_text="Waypoint name",
                                   dense=True, filled=True, text_size=12, expand=True,
                                   color=p["text"], border_color=p["border"],
                                   focused_border_color=p["accent"],
                                   on_change=lambda e, k=i: set_field(k, "name",
                                                                      e.control.value)),
                               ft.IconButton(ft.Icons.ARROW_UPWARD, icon_size=15,
                                             icon_color=p["text2"], disabled=i == 0,
                                             tooltip="move up",
                                             on_click=lambda e, k=i: move_wp(k, -1)),
                               ft.IconButton(ft.Icons.ARROW_DOWNWARD, icon_size=15,
                                             icon_color=p["text2"],
                                             disabled=i == len(st["waypoints"]) - 1,
                                             tooltip="move down",
                                             on_click=lambda e, k=i: move_wp(k, +1)),
                               ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=15,
                                             icon_color=p["pink"], tooltip="remove",
                                             on_click=lambda e, k=i: del_wp(k)),
                           ]),
                    ft.Row(spacing=6, controls=[
                        ft.TextField(value=str(w.get("lat", "")), label="lat",
                                     dense=True, filled=True, text_size=11, expand=True,
                                     color=p["text"], border_color=p["border"],
                                     focused_border_color=p["accent"],
                                     on_submit=lambda e, k=i: set_coord(k, "lat",
                                                                        e.control.value),
                                     on_blur=lambda e, k=i: set_coord(k, "lat",
                                                                      e.control.value)),
                        ft.TextField(value=str(w.get("lon", "")), label="lon",
                                     dense=True, filled=True, text_size=11, expand=True,
                                     color=p["text"], border_color=p["border"],
                                     focused_border_color=p["accent"],
                                     on_submit=lambda e, k=i: set_coord(k, "lon",
                                                                        e.control.value),
                                     on_blur=lambda e, k=i: set_coord(k, "lon",
                                                                      e.control.value)),
                    ]),
                    ft.TextField(value=w.get("note", "") or "",
                                 hint_text="Note (optional)", dense=True, filled=True,
                                 text_size=11, color=p["text"],
                                 border_color=p["border"],
                                 focused_border_color=p["accent"],
                                 on_change=lambda e, k=i: set_field(k, "note",
                                                                    e.control.value)),
                ])))
        wp_col.controls = rows or [
            _empty(p, "No waypoints yet. Tap the map, or press Home to start "
                      "from Vulcan Lofts.")]

    def paint_preview():
        pv = st["preview"] or {}
        if pv.get("pending"):
            preview_row.controls = [
                chip("routing the preview…", p["yellow"],
                     ft.Icons.HOURGLASS_TOP, small=True)]
        elif pv.get("ok"):
            preview_row.controls = [
                _stat(p, "distance", f"{pv['distance_mi']:.1f}", "mi"),
                _stat(p, "moving", T.fmt_minutes(pv["duration_min"])),
            ]
        elif pv.get("error"):
            preview_row.controls = [
                chip(pv["error"], p["pink"], ft.Icons.ERROR_OUTLINE, small=True)]
        else:
            preview_row.controls = [
                sub("two waypoints or more get a routed preview", 10)]

    paint_map()
    paint_waypoints()
    paint_preview()
    if st["preview"] is None and len(st["waypoints"]) >= 2:
        _run(page, _preview_route, page, st, preview_row, p, edit_line)

    def save(e):
        name = (st["name"] or "").strip()
        if not name:
            say("a route needs a name", bad=True)
            return
        if not st["waypoints"]:
            say("add at least one waypoint", bad=True)
            return
        payload = T.editor_payload(
            st["orig"], name, st["waypoints"], st["report"],
            st["forks_at"], st["fork_origin"],
            stats=st["preview"] if (st["preview"] or {}).get("ok") else None,
            is_new=st["is_new"])
        ok, msg = M.trip_save(payload)
        if ok:
            _S["editor"] = None
            _S["leg_routes"].pop(payload["id"], None)
            _S["note"] = f"saved {name} — {msg}"
            _rebuild(page)
        else:
            say(f"not saved - {msg}", bad=True)

    def meta_card():
        return card(ft.Column(spacing=8, controls=[
            title("Forks & stacking", 13),
            ft.TextField(value=st["forks_at"], label="Forks at",
                         hint_text="(location name, or blank)", dense=True,
                         filled=True, text_size=12, color=p["text"],
                         border_color=p["border"], focused_border_color=p["accent"],
                         on_change=lambda e: st.update(forks_at=e.control.value)),
            ft.TextField(value=st["fork_origin"], label="Stacks after",
                         hint_text="(the leg id this follows)", dense=True,
                         filled=True, text_size=12, color=p["text"],
                         border_color=p["border"], focused_border_color=p["accent"],
                         on_change=lambda e: st.update(fork_origin=e.control.value)),
            ft.Row(spacing=6, controls=[
                ft.OutlinedButton("Cancel", height=34,
                                  on_click=lambda e: (_S.update(editor=None),
                                                      _rebuild(page))),
                ft.FilledButton("Save", height=34, icon=ft.Icons.SAVE,
                                on_click=save),
            ]),
        ]), pad=12)

    def waypoints_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       title(f"Waypoints · {len(st['waypoints'])}", 13),
                       ft.Container(expand=True),
                       ft.OutlinedButton("Home", height=30, icon=ft.Icons.HOME,
                                         on_click=add_home)]),
            preview_row,
            wp_col,
        ]), pad=12)

    def header():
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(ft.Icons.EDIT_ROAD, size=16, color=p["accent"]),
                       name_field,
                       *([chip("new", p["green"], small=True)]
                         if st["is_new"] else []),
                       ft.IconButton(ft.Icons.CLOSE, icon_color=p["text2"],
                                     tooltip="close the editor",
                                     on_click=lambda e: (_S.update(editor=None),
                                                         _rebuild(page))),
                   ]),
        ]), pad=12)

    body = panels.layout("trips.produce", {
        "picker": header,
        "map": lambda: map_holder,
        "waypoints": waypoints_card,
        "meta": meta_card,
        "report": lambda: _report_card(page, p, st, say),
    })
    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=body),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


def _shift_scope(scope, removed):
    """Follow the selected waypoint scopes when waypoint `removed` goes."""
    if scope == ["route"]:
        return scope
    out = []
    for s in scope:
        if s == "route":
            out.append(s)
        elif int(s) == removed:
            continue
        elif int(s) > removed:
            out.append(int(s) - 1)
        else:
            out.append(int(s))
    return out or ["route"]


async def _preview_route(page, st, row, p, line):
    """Route the editor's waypoints so the numbers shown are measured ones."""
    if len(st["waypoints"]) < 2:
        st["preview"] = None
        return
    st["preview"] = {"pending": True}
    _update(page)
    res = await asyncio.to_thread(T.valhalla_route, [dict(w) for w in st["waypoints"]])
    st["preview"] = res
    if res.get("ok") and line is not None:
        line.coordinates = [fmap.MapLatitudeLongitude(a, b) for a, b in res["points"]]
    if res.get("pending"):                             # pragma: no cover
        return
    if st["preview"].get("ok"):
        row.controls = [_stat(p, "distance", f"{res['distance_mi']:.1f}", "mi"),
                        _stat(p, "moving", T.fmt_minutes(res["duration_min"]))]
    else:
        row.controls = [chip(res.get("error", "no route"), p["pink"],
                             ft.Icons.ERROR_OUTLINE, small=True)]
    _update(page)


# ------------------------------------------------------------- trip report

def _report_card(page, p, st, say):
    """Tag the whole route, or specific waypoints, with what it is like."""
    waypoints = st["waypoints"]
    scope = st["scope"]

    def set_scope(value):
        if value == "route":
            st["scope"] = ["route"]
        else:
            current = [s for s in st["scope"] if s != "route"]
            if value in current:
                current.remove(value)
            else:
                current.append(value)
            st["scope"] = sorted(current) or ["route"]
        st["checked"] = set()
        _rebuild(page)

    scope_buttons = [
        _scope_button(p, "whole-route", scope == ["route"],
                      lambda e: set_scope("route"))]
    for i, w in enumerate(waypoints):
        scope_buttons.append(_scope_button(
            p, str(i + 1), i in scope, lambda e, k=i: set_scope(k),
            tooltip=w.get("name", f"waypoint {i + 1}")))

    note_field = ft.TextField(
        hint_text="Note (optional) — e.g. 'miles 2-4 rough pavement'",
        dense=True, filled=True, text_size=11, expand=True, color=p["text"],
        border_color=p["border"], focused_border_color=p["accent"])

    def toggle_tag(label, on):
        if on:
            st["checked"].add(label)
        else:
            st["checked"].discard(label)

    gallery = []
    tags = T.all_tags()
    for cat, cat_label in T.TAG_CATEGORIES:
        items = [t for t in tags if t["cat"] == cat]
        if not items:
            continue
        chips = []
        for tag in items:
            colour = T.severity_colour(tag["severity"], p)
            chips.append(ft.Container(
                padding=PAD(0, 4), border_radius=16,
                bgcolor=ft.Colors.with_opacity(0.12, colour),
                border=BRD(ft.Colors.with_opacity(0.4, colour), 1),
                content=ft.Row(spacing=2, tight=True, controls=[
                    ft.Checkbox(value=tag["label"] in st["checked"], scale=0.7,
                                active_color=colour,
                                on_change=lambda e, lb=tag["label"]:
                                    toggle_tag(lb, e.control.value)),
                    ft.Icon(T.tag_icon(tag["icon"]), size=12, color=colour),
                    ft.Text(tag["label"], size=10, color=colour),
                    *([ft.IconButton(
                        ft.Icons.CLOSE, icon_size=11, icon_color=colour,
                        tooltip="remove this custom tag",
                        on_click=lambda e, lb=tag["label"]: (
                            prefs.remove_trip_tag(lb), _rebuild(page)))]
                      if tag["custom"] else []),
                    ft.Container(width=4),
                ])))
        gallery.append(ft.Column(spacing=4, controls=[
            sub(cat_label, 10), ft.Row(spacing=5, wrap=True, controls=chips)]))

    def add_selected(e):
        if not st["checked"]:
            say("tick at least one tag first", bad=True)
            return
        st["report"] = T.add_report_entries(
            st["report"], st["scope"], sorted(st["checked"]),
            (note_field.value or "").strip())
        st["checked"] = set()
        say(f"tagged {T.scope_label(st['scope'])} — Save to write it")
        _rebuild(page)

    rows = []
    for row in T.report_rows(st["report"], st["scope"]):
        tag = T.find_tag(row["label"])
        colour = T.severity_colour(tag["severity"], p)
        where = " ".join("route" if s == "route" else f"wp {int(s) + 1}"
                         for s in row["scopes"])
        rows.append(ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(T.tag_icon(tag["icon"]), size=14, color=colour),
                               ft.Column(spacing=0, expand=True, controls=[
                                   ft.Row(spacing=6, controls=[
                                       ft.Text(row["label"], size=11, color=p["text"]),
                                       ft.Text(where, size=9, color=p["text2"])]),
                                   *([sub(row["note"], 10)] if row["note"] else []),
                               ]),
                               ft.IconButton(
                                   ft.Icons.CLOSE, icon_size=14, icon_color=p["pink"],
                                   tooltip="remove from every listed scope",
                                   on_click=lambda e, lb=row["label"], nt=row["note"]: (
                                       st.update(report=T.remove_report_entries(
                                           st["report"], st["scope"], lb, nt)),
                                       _rebuild(page))),
                           ]))

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   ft.Icon(ft.Icons.CHECKLIST, size=15, color=p["accent"]),
                   title("Trip report", 13)]),
        ft.Row(spacing=4, wrap=True, controls=scope_buttons),
        sub(f"Adding to: {T.scope_label(st['scope'])} · waypoint numbers are "
            f"multi-select · Add commits to every selected scope", 10),
        *gallery,
        _custom_tag_row(page, p, say),
        ft.Row(spacing=6, controls=[
            note_field,
            ft.OutlinedButton("Add selected", height=34, icon=ft.Icons.ADD,
                              on_click=add_selected)]),
        *(rows or [_empty(p, f"No tags for {T.scope_label(st['scope'])} yet. "
                             f"Tick some chips above and press Add.")]),
    ]), pad=12)


def _scope_button(p, label, active, on_click, tooltip=None):
    colour = p["accent"] if active else p["text2"]
    return ft.Container(
        padding=PAD(4, 9), border_radius=8, tooltip=tooltip,
        bgcolor=ft.Colors.with_opacity(0.18 if active else 0.05, colour),
        border=BRD(ft.Colors.with_opacity(0.5 if active else 0.25, colour), 1),
        on_click=on_click,
        content=ft.Text(label, size=10, color=colour,
                        weight=ft.FontWeight.W_600 if active else None))


def _custom_tag_row(page, p, say):
    """Her own tags, on top of the 27 presets. Persisted on this device."""
    label = ft.TextField(hint_text="new tag name", dense=True, filled=True,
                         text_size=11, expand=True, color=p["text"],
                         border_color=p["border"],
                         focused_border_color=p["accent"])
    cat = ft.Dropdown(value="attribute", dense=True, filled=True, text_size=11,
                      width=120, border_color=p["border"], color=p["text"],
                      options=[ft.DropdownOption(key=k, text=k.title())
                               for k in prefs.TAG_CATS])
    sev = ft.Dropdown(value="info", dense=True, filled=True, text_size=11,
                      width=110, border_color=p["border"], color=p["text"],
                      options=[ft.DropdownOption(key=k, text=k.title())
                               for k in prefs.TAG_SEVERITIES])

    def add(e):
        name = (label.value or "").strip().lower()
        if not name:
            return
        if not prefs.add_trip_tag(name, sev.value, cat.value):
            say(f"'{name}' is already a tag", bad=True)
            return
        say(f"added the tag '{name}'")
        _rebuild(page)

    return ft.Row(spacing=6, wrap=True, controls=[
        label, cat, sev,
        ft.OutlinedButton("New tag", height=32, icon=ft.Icons.ADD, on_click=add)])


# =============================================================== Rides tab

def _rides(page):
    """Recorded GPX: upload, list, replay. Plus live guidance from Selekt."""
    p = PAL()
    units = prefs.unit_system()
    status = ft.Text("", size=11, color=p["text2"], selectable=True)

    def say(msg, bad=False):
        status.value = msg
        status.color = p["pink"] if bad else p["text2"]
        _update(page)

    body = panels.layout("trips.rides", {
        "guidance": lambda: _guidance(page, p, units),
        "upload": lambda: _upload_card(page, p, say),
        "list": lambda: _rides_list(page, p, say),
        "viewer": lambda: _ride_viewer(page, p, say),
    })
    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=body),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


def _upload_card(page, p, say):
    picker = ft.FilePicker()
    services = getattr(page, "services", None)
    if isinstance(services, list):
        services.append(picker)

    async def choose(e):
        try:
            files = await picker.pick_files(
                dialog_title="Pick a GPX recording", allowed_extensions=["gpx"],
                with_data=True)
        except Exception as exc:                       # noqa: BLE001
            say(f"could not open the file picker: {exc}", bad=True)
            return
        if not files:
            return
        f = files[0]
        raw = getattr(f, "bytes", None)
        if raw is None and getattr(f, "path", None):
            try:
                with open(f.path, "rb") as fh:
                    raw = fh.read()
            except OSError as exc:
                say(f"could not read {f.name}: {exc}", bad=True)
                return
        if raw is None:
            say("the picker gave no file contents to upload", bad=True)
            return
        ok, msg = T.ride_upload(f.name, raw.decode("utf-8", "replace"))
        say(msg, bad=not ok)
        if ok:
            _rebuild(page)

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   ft.Icon(ft.Icons.UPLOAD_FILE, size=16, color=p["accent"]),
                   title("Upload GPX", 14), ft.Container(expand=True),
                   ft.FilledButton("Pick a file", height=32,
                                   icon=ft.Icons.FOLDER_OPEN, on_click=choose)]),
        sub("From OsmAnd, Strava or Geo Tracker. Queued if there is no signal "
            "and sent when there is.", 10),
    ]), pad=12)


def _rides_list(page, p, say):
    rides = T.rides_all()
    if rides is None:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.SPEED, size=16, color=p["text2"]),
                    title("Recorded rides", 14)]),
            _empty(p, "Not connected, and no copy of the ride list on this "
                      "device yet.", ft.Icons.CLOUD_OFF)]), pad=12)
    if not rides:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.SPEED, size=16, color=p["text2"]),
                    title("Recorded rides", 14)]),
            _empty(p, "No rides uploaded yet. Record one on the phone, or use "
                      "xNav's recorder, and it appears here.")]), pad=12)

    open_id = (_S["ride"] or {}).get("id")

    def open_ride(rid):
        async def handler(e):
            say(f"loading {rid}…")
            data = await asyncio.to_thread(T.ride_view, rid)
            if data is None:
                say(f"could not read {rid} from the server", bad=True)
                return
            points = data.get("points") or []
            timeline, duration, has_time = T.ride_timeline(points)
            _S["ride"] = {"id": rid, "points": points,
                          "stats": data.get("stats") or {},
                          "timeline": timeline, "duration": duration,
                          "has_time": has_time, "playing": False, "head": 0.0,
                          "speed_x": 60, "limits": None}
            say("")
            _rebuild(page)
        return handler

    rows = []
    for r in rides:
        rid = str(r.get("id", ""))
        size_kb = (r.get("size") or 0) / 1024.0
        rows.append(ft.Container(
            padding=PAD(7, 0),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])),
            content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(ft.Icons.ROUTE, size=15,
                                       color=p["accent"] if rid == open_id
                                       else p["text2"]),
                               ft.Column(spacing=0, expand=True, controls=[
                                   ft.Text(rid, size=12, color=p["text"]),
                                   sub(f"{T.fmt_mtime(r.get('mtime'))} · "
                                       f"{size_kb:.1f} KB", 10)]),
                               ft.OutlinedButton("Open", height=30,
                                                 icon=ft.Icons.PLAY_ARROW,
                                                 on_click=open_ride(rid)),
                           ])))
    return card(ft.Column(spacing=4, controls=[
        ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.SPEED, size=16, color=p["accent"]),
            title("Recorded rides", 14), ft.Container(expand=True),
            sub(f"{len(rides)}", 10)]),
        *rows,
    ]), pad=10)


#: How the speed-coloured track is drawn: one polyline per bucket instead of
#: one per point. A 4,000-point ride would otherwise be 4,000 controls.
_SPEED_BUCKETS = [0, 10, 20, 30, 40, 50, 60, 70, 80]


def _bucket(mph):
    if mph is None:
        return None
    for i in range(len(_SPEED_BUCKETS) - 1, -1, -1):
        if mph >= _SPEED_BUCKETS[i]:
            return i
    return 0


def _ride_viewer(page, p, say):
    ride = _S["ride"]
    if ride is None:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.PLAY_CIRCLE, size=16, color=p["text2"]),
                    title("Ride replay", 14)]),
            _empty(p, "Open a ride from the list to replay it here.")]), pad=12)

    s = ride["stats"]
    points = ride["points"]
    stat_row = ft.Row(spacing=8, wrap=True, controls=[
        _stat(p, "Distance", s.get("distance_mi", "—"), "mi"),
        _stat(p, "Time", T.fmt_ms(s.get("duration_ms"))),
        _stat(p, "Max speed", s.get("max_speed_mph") if s.get("max_speed_mph")
              is not None else "—", "mph"),
        _stat(p, "Avg speed", s.get("avg_speed_mph") if s.get("avg_speed_mph")
              is not None else "—", "mph"),
        _stat(p, "Ascent", s.get("ascent_ft") if s.get("ascent_ft") is not None
              else "—", "ft"),
        _stat(p, "Descent", s.get("descent_ft") if s.get("descent_ft") is not None
              else "—", "ft"),
        _stat(p, "Points", s.get("point_count", len(points))),
    ])

    clock = ft.Text(T.fmt_clock(ride["head"]), size=12, color=p["text"],
                    width=58)
    now_mph = ft.Text("— mph", size=12, color=p["accent"], width=64)
    scrub = ft.Slider(min=0, max=max(1.0, ride["duration"]),
                      value=min(ride["head"], ride["duration"]), expand=True,
                      active_color=p["accent"])
    play_button = ft.FilledButton(
        "Pause" if ride["playing"] else "Play", height=32,
        icon=ft.Icons.PAUSE if ride["playing"] else ft.Icons.PLAY_ARROW)
    limits_note = ft.Text("", size=11, color=p["text2"])

    head_marker = None
    limit_layer = None
    the_map = None
    if MAPS_AVAILABLE and points:
        url_template, tiles_offline = _tile_url()
        lines = []
        run, run_bucket = [], None
        for i, q in enumerate(points):
            b = _bucket(q.get("speed_mph"))
            if run and b != run_bucket:
                lines.append(fmap.PolylineMarker(
                    coordinates=run, stroke_width=5,
                    color=T.speed_colour(
                        None if run_bucket is None
                        else _SPEED_BUCKETS[run_bucket] + 5)))
                run = [run[-1]]
            run_bucket = b
            run.append(fmap.MapLatitudeLongitude(q["lat"], q["lon"]))
        if len(run) > 1:
            lines.append(fmap.PolylineMarker(
                coordinates=run, stroke_width=5,
                color=T.speed_colour(None if run_bucket is None
                                     else _SPEED_BUCKETS[run_bucket] + 5)))
        limit_line_layer = fmap.PolylineLayer(polylines=[])
        head_marker = _pin(p["accent"], ft.Icons.NAVIGATION,
                           at=(points[0]["lat"], points[0]["lon"]), size=18)
        start_pin = _pin(p["green"], ft.Icons.PLAY_ARROW,
                         at=(points[0]["lat"], points[0]["lon"]), size=18)
        end_pin = _pin(p["pink"], ft.Icons.FLAG,
                       at=(points[-1]["lat"], points[-1]["lon"]), size=18)
        pts = [(q["lat"], q["lon"]) for q in points]
        the_map = fmap.Map(
            expand=True, initial_center=fmap.MapLatitudeLongitude(*_centre(pts)),
            initial_zoom=_zoom_for(pts), min_zoom=3, max_zoom=18,
            bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=url_template,
                user_agent_package_name="online.iamprettyfamous.nviidash"),
                fmap.PolylineLayer(polylines=lines), limit_line_layer,
                fmap.MarkerLayer(markers=[start_pin, end_pin, head_marker])])
        limit_layer = limit_line_layer
        map_box = ft.Container(height=300, border_radius=10,
                               clip_behavior=ft.ClipBehavior.HARD_EDGE,
                               content=the_map)
    else:                                  # pragma: no cover - build-dependent
        map_box = _empty(p, "no map in this build — the stats above are real")

    def set_head(ms):
        ride["head"] = max(0.0, min(ride["duration"], ms))
        got = T.ride_head(points, ride["timeline"], ride["head"])
        clock.value = T.fmt_clock(ride["head"])
        if got is None:
            return
        lat, lon, _, mph = got
        now_mph.value = f"{round(mph)} mph" if mph is not None else "— mph"
        if head_marker is not None:
            head_marker.coordinates = fmap.MapLatitudeLongitude(lat, lon)
        scrub.value = ride["head"]

    async def tick():
        import time as _t
        last = _t.monotonic()
        while ride["playing"] and _S["ride"] is ride:
            await asyncio.sleep(1 / 15)
            now = _t.monotonic()
            set_head(ride["head"] + (now - last) * 1000 * ride["speed_x"])
            last = now
            if ride["head"] >= ride["duration"]:
                ride["playing"] = False
                play_button.content = "Play"
                play_button.icon = ft.Icons.PLAY_ARROW
            _update(page)

    def play_pause(e):
        if not ride["duration"]:
            say("this ride has no timeline to replay", bad=True)
            return
        ride["playing"] = not ride["playing"]
        if ride["playing"] and ride["head"] >= ride["duration"] - 10:
            ride["head"] = 0.0
        play_button.content = "Pause" if ride["playing"] else "Play"
        play_button.icon = ft.Icons.PAUSE if ride["playing"] else ft.Icons.PLAY_ARROW
        _update(page)
        if ride["playing"]:
            _run(page, tick)

    play_button.on_click = play_pause
    scrub.on_change = lambda e: (set_head(float(e.control.value)), _update(page))

    speed_dd = ft.Dropdown(
        value=str(ride["speed_x"]), dense=True, filled=True, text_size=11,
        width=90, border_color=p["border"], color=p["text"],
        options=[ft.DropdownOption(key=str(v), text=f"{v}×")
                 for v in (30, 60, 120, 300)],
        # on_select, not on_change: 0.86's Dropdown never fires on_change.
        on_select=lambda e: ride.update(speed_x=int(e.control.value or 60)))

    async def toggle_limits(e):
        if ride.get("limits"):
            ride["limits"] = None
            if limit_layer is not None:
                limit_layer.polylines = []
            limits_note.value = ""
            _update(page)
            return
        if not points:
            return
        limits_note.value = "asking OpenStreetMap…"
        _update(page)
        bbox = T.bbox_of([(q["lat"], q["lon"]) for q in points], pad=0.10)
        ways, err = await asyncio.to_thread(T.maxspeed_ways, bbox)
        if not ways:
            limits_note.value = err or "no speed limits published for this view"
            limits_note.color = p["yellow"]
            _update(page)
            return
        ride["limits"] = ways
        if limit_layer is not None:
            limit_layer.polylines = [
                fmap.PolylineMarker(
                    coordinates=[fmap.MapLatitudeLongitude(a, b)
                                 for a, b in w["points"]],
                    color=T.maxspeed_colour(w["mph"]), stroke_width=4)
                for w in ways]
        limits_note.value = f"{len(ways)} roads with a published limit"
        limits_note.color = p["text2"]
        _update(page)

    def close(e):
        ride["playing"] = False
        _S["ride"] = None
        _rebuild(page)

    set_head(ride["head"])

    legend = ft.Row(spacing=8, wrap=True, controls=[
        sub("replay speed", 10),
        *[ft.Row(spacing=3, tight=True, controls=[
            ft.Container(width=18, height=7, border_radius=3,
                         bgcolor=T.speed_colour(v)),
            ft.Text(f"{v}", size=9, color=p["text2"])])
          for v in (0, 20, 40, 60, 80)],
        ft.Text("mph", size=9, color=p["text2"]),
    ])

    pacing = ([] if ride["has_time"] else
              [_empty(p, f"This GPX has no timestamps, so the replay is paced "
                         f"at a steady {int(T.SYNTHETIC_MPH)} mph. The clock "
                         f"below is synthetic, not measured.",
                      ft.Icons.SCHEDULE)])

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   ft.Icon(ft.Icons.PLAY_CIRCLE, size=16, color=p["accent"]),
                   title(ride["id"], 14), ft.Container(expand=True),
                   ft.IconButton(ft.Icons.CLOSE, icon_color=p["text2"],
                                 tooltip="back to the list", on_click=close)]),
        stat_row,
        *pacing,
        map_box,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[play_button, clock, scrub, now_mph]),
        ft.Row(spacing=8, wrap=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                   speed_dd,
                   ft.OutlinedButton("Speed limits", height=32,
                                     icon=ft.Icons.SPEED,
                                     on_click=toggle_limits),
                   limits_note]),
        legend,
    ]), pad=12)


def _guidance(page, p, units):
    """Live turn-by-turn against the route planned in Selekt.

    Everything here is driven by the GPS receiver, which never needed a
    network, and by a route already in memory. Once the route exists this
    keeps working with the phone in airplane mode.
    """
    res = _PLAN["route"]

    if res is None:
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.ALT_ROUTE, size=16, color=p["text2"]),
                    title("Follow a route", 14)]),
            sub("No route planned yet. Plan one in Selekt and it shows up "
                "here, with the next turn and the distance to it.", 11),
        ]), pad=12)

    instruction = ft.Text("—", size=17, weight=ft.FontWeight.W_700,
                          color=p["text"])
    distance = ft.Text("—", size=30, weight=ft.FontWeight.W_700,
                       color=p["accent"])
    street = ft.Text("", size=11, color=p["text2"])
    left_text = ft.Text("", size=11, color=p["text2"])
    warn = ft.Text("", size=11, color=p["yellow"])
    follow_button = ft.FilledButton(
        "Stop" if _FOLLOW["on"] else "Follow", height=38,
        icon=ft.Icons.STOP if _FOLLOW["on"] else ft.Icons.NAVIGATION)
    turn_icon = ft.Icon(ft.Icons.STRAIGHT, size=28, color=p["accent"])

    def render():
        pos = _FOLLOW["position"]
        if not _FOLLOW["on"] or pos is None:
            first = res.maneuvers[1] if len(res.maneuvers) > 1 else res.maneuvers[0]
            instruction.value = first.instruction
            distance.value = R.fmt_distance(first.cum_m, units)
            street.value = "waiting for GPS…" if _FOLLOW["on"] else "not following"
            left_text.value = (f"{R.fmt_distance(res.distance_m, units)} · "
                               f"{R.fmt_duration(res.duration_s)} total")
            warn.value = ""
        else:
            nxt, to_go, off = R.next_maneuver(res, pos)
            left_m, left_s = R.remaining(res, pos)
            if nxt is None:
                instruction.value = "route has no turns"
                distance.value = "—"
            else:
                instruction.value = nxt.instruction
                distance.value = R.fmt_distance(to_go, units)
                street.value = nxt.street or ""
            left_text.value = (f"{R.fmt_distance(left_m, units)} left · "
                               f"{R.fmt_duration(left_s)} · "
                               f"arrive {R.fmt_eta(left_s)}")
            # Off-route is reported, never hidden. A silent snap back onto
            # the line is how you keep being told to turn onto a road you
            # are no longer anywhere near.
            warn.value = (f"off route by {R.fmt_distance(off, units)}"
                          if off is not None and off > 60 else "")
        turn_icon.icon = _turn_icon(instruction.value)
        _update(page)

    _FOLLOW["render"] = render

    async def toggle(e):
        if _FOLLOW["on"]:
            _FOLLOW["on"] = False
            # .content, not .text: a Flet button's label is its content, and
            # assigning .text silently does nothing.
            follow_button.content = "Follow"
            follow_button.icon = ft.Icons.NAVIGATION
            render()
            return
        if not MAPS_AVAILABLE:
            warn.value = "this build has no flet-geolocator"
            _update(page)
            return
        _FOLLOW["on"] = True
        follow_button.content = "Stop"
        follow_button.icon = ft.Icons.STOP
        render()
        pos = await _one_fix(page)
        if pos is not None:
            _FOLLOW["position"] = pos
        render()

    follow_button.on_click = toggle
    render()

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   ft.Icon(ft.Icons.ALT_ROUTE, size=16, color=p["accent"]),
                   title("Following", 14), ft.Container(expand=True),
                   chip("computed offline" if res.offline else "from the server",
                        p["green"] if res.offline else p["cyan"],
                        ft.Icons.ALT_ROUTE if res.offline else ft.Icons.CLOUD_DONE,
                        small=True),
               ]),
        ft.Row(spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   turn_icon,
                   ft.Column(spacing=1, expand=True, controls=[
                       instruction, street]),
                   distance,
               ]),
        left_text,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[follow_button, ft.Container(expand=True), warn]),
    ]), pad=12)
