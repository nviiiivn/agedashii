"""Trips: plan a route, keep the trip list, and follow a route while riding.

The three tabs are the ride pipeline in the order it is actually used:

  Selekt   pick two points, get a real turn-by-turn route, see it on the map
  Produce  grocery runs (still a placeholder)
  Rides    recorded rides, and live guidance along the route Selekt produced
  XNAV     the live map and track recorder

Routing comes from `flet_app/routing.py`, which prefers the offline graph on
the device and falls back to the server's Valhalla. Which one answered is
shown on screen every single time - see the chip beside the summary. A rider
who thinks the phone is self-sufficient when it is quietly leaning on wifi
is a rider who finds out in the wrong place.

There is no geocoder here, offline or otherwise, so points are entered as
`lat, lon`, taken from the GPS, taken from Home, or tapped on the map. That
is honest about what works with the radio off; a search box that only works
on wifi would not be.
"""
import flet as ft

from .. import api as M
from .. import prefs
from .. import routing as R
from .. import tileserver
from ..ui import PAL, PAD, card, chip, title, sub
from ._forms import Writer, offline_banner, rebuilder
from .lab import _tabs
from . import xnav

try:                      # optional: the app still runs without the extensions
    import flet_map as fmap
    import flet_geolocator as fgeo
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = fgeo = None
    MAPS_AVAILABLE = False

#: Vulcan Lofts. Same start point the website's trip builder uses.
HOME = (37.769972, -122.216445)

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


def build(page):
    return _tabs(page, [("Selekt", _trips(page)), ("Produce", _produce()),
                        ("Rides", _rides(page)), ("XNAV", xnav.build(page))],
                 key="trips")


# --------------------------------------------------------------- utilities

def _fmt_latlon(pt):
    return f"{pt[0]:.5f}, {pt[1]:.5f}" if pt else ""


def _zoom_for(points):
    """A zoom level that shows the whole route without maths theatre."""
    lats = [q[0] for q in points]
    lons = [q[1] for q in points]
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.8, 1e-4)
    z = 9
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


# ------------------------------------------------------------ Selekt tab

def _trips(page):
    """Trip list and creation, plus the route planner.

    Creating a trip by name still hands the detailed stop editing to the
    website's big map - a stripped-down stop editor on a phone would produce
    trips the website renders as empty. What the phone *can* do that the
    website cannot is route with no internet, so that is what was added.
    """
    p = PAL()
    w = Writer(page, rebuilder(page, build))
    live = M.STATUS_LIVE()

    def row(t):
        return card(ft.Column(spacing=5, controls=[
            ft.Row([title(t["name"], 14), ft.Container(expand=True),
                    chip(t.get("date", ""), p["cyan"], small=True)]),
            sub(f"{t.get('mi', 0)} mi", 11),
        ]), pad=10)

    def make(name):
        import re as _re
        import time as _time
        slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            return False, "name needs at least one letter or number"
        return M.trip_save({"id": f"{slug}-{int(_time.time())}", "name": name,
                            "date": _time.strftime("%Y-%m-%d"), "stops": []})

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        *([] if live else [offline_banner(p, "trips")]),
        *_planner(page, p),
        *([card(ft.Column(spacing=8, controls=[
            title("New trip", 14),
            w.entry("trip name...", make),
            sub("Creates the trip; add stops and route on the website's map.", 10),
        ]))] if live else []),
        *[row(t) for t in (M.TRIPS or [])],
        w.control,
    ])


def _planner(page, p):
    """The route planner: two points in, a drawn route and a turn list out.

    Returns a list of controls so the caller keeps control of page layout.
    """
    # Start the 78 MB graph read now, on a background thread. Opening this
    # page must not block the UI for the ~50 ms it takes, and app startup
    # must not pay for it at all on the launches that never come here.
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
        local_url = tileserver.start(M.mbtiles_path())
        tiles_offline = local_url is not None
        source_row.controls.append(
            chip("offline maps" if tiles_offline else "online maps",
                 p["green"] if tiles_offline else p["yellow"],
                 ft.Icons.MAP, small=True))

        route_line = fmap.PolylineMarker(
            coordinates=[], color=p["accent"], stroke_width=5,
            border_color=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
            border_stroke_width=1)
        line_layer = fmap.PolylineLayer(polylines=[route_line])

        def pin(colour, icon):
            return fmap.Marker(
                coordinates=fmap.MapLatitudeLongitude(*HOME),
                width=26, height=26, visible=False,
                content=ft.Container(
                    width=22, height=22, border_radius=11, bgcolor=colour,
                    border=ft.Border.all(2, ft.Colors.WHITE),
                    content=ft.Icon(icon, size=13, color=ft.Colors.WHITE)))

        start_pin = pin(p["green"], ft.Icons.PLAY_ARROW)
        end_pin = pin(p["pink"], ft.Icons.FLAG)
        pin_layer = fmap.MarkerLayer(markers=[start_pin, end_pin])

        the_map = fmap.Map(
            expand=True,
            initial_center=fmap.MapLatitudeLongitude(*HOME),
            initial_zoom=11, min_zoom=3, max_zoom=18, bgcolor=p["bg2"],
            layers=[fmap.TileLayer(
                url_template=local_url or ONLINE_TILES,
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
            page.update()

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
                chip("offline maps" if tileserver.status()["running"]
                     else "online maps",
                     p["green"] if tileserver.status()["running"] else p["yellow"],
                     ft.Icons.MAP, small=True))
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
            page.update()
            return
        _PLAN.update({"from": a, "to": b, "from_text": frm_field.value,
                      "to_text": to_field.value})
        route_button.disabled = True
        summary_text.value = ""
        status_text.value = "routing…"
        status_text.color = p["text2"]
        page.update()
        # Off the UI thread: a 40 km offline search is ~150 ms and the
        # server fallback is a network round trip. Either would freeze Flet.
        res = await R.route_async(a, b)
        route_button.disabled = False
        _PLAN["route"] = res if res.ok else None
        draw(res)
        page.update()
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
        page.update()

    def use_home(e):
        frm_field.value = _fmt_latlon(HOME)
        _PLAN["from_text"] = frm_field.value
        page.update()

    def swap(e):
        frm_field.value, to_field.value = to_field.value, frm_field.value
        _PLAN.update({"from_text": frm_field.value, "to_text": to_field.value})
        page.update()

    def clear(e):
        _PLAN.update({"route": None, "from": None, "to": None})
        summary_text.value = ""
        status_text.value = ""
        turns_col.controls = []
        turns_card.visible = False
        clear_map()
        page.update()

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

    return [planner, map_box, turns_card]


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


# ------------------------------------------------------------- Produce tab

def _produce():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=5, controls=[
            ft.Row([title("Produce runs", 14), ft.Container(expand=True),
                    chip("Lucky · foodmaxx", p["green"], small=True)]),
            sub("Grocery run history + FoodZen handoff — coming in the next pass", 11),
        ]), pad=12),
    ])


# --------------------------------------------------------------- Rides tab

def _rides(page):
    p = PAL()
    units = prefs.unit_system()

    def row(r):
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.MAP, size=16, color=p["accent"]),
                    title(r["name"], 14), ft.Container(expand=True)]),
            ft.Row(spacing=14, controls=[
                ft.Column(spacing=1, controls=[sub("points", 10), ft.Text(str(r["pts"]), size=13, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("miles", 10), ft.Text(str(r["mi"]), size=13, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("top mph", 10), ft.Text(str(r["top"]), size=13, color=p["text"])]),
            ]),
            ft.Row([chip("replay", p["purple"], ft.Icons.PLAY_ARROW, small=True),
                    chip("elevation", p["green"], ft.Icons.TERRAIN, small=True)]),
        ]), pad=10)

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        _guidance(page, p, units),
        *[row(r) for r in M.RIDES],
    ])


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
        try:
            page.update()
        except Exception:                              # noqa: BLE001
            pass

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
            page.update()
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
