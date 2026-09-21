"""xNav - the live map: GPS, track recording, and route guidance.

Two halves that share one map, because on a bike there is only ever one map.

**The recorder** works with the radio off, and that is the point:

* Tiles come from an MBTiles archive on the device, handed to the map by a
  loopback HTTP server (see flet_app/tileserver.py). If no archive is
  installed the map falls back to online tiles and says so plainly, rather
  than showing an empty grey grid and letting you think it is broken.
* GPS is the phone's own receiver talking to satellites. It never needed a
  network, and neither does recording a track.
* Recorded points are held in memory and written to the device, then synced
  to the server whenever wifi comes back.

Speed and heading come from the GPS fix itself rather than being derived
from successive points: the receiver computes them from Doppler shift, which
is both more accurate and available while barely moving, where position
differencing produces mostly noise.

**The navigator** is the website's xNav, and it does not: Valhalla routes it,
Overpass colours it by legal speed limit, and Valhalla's height service
draws its profile. All three are live third parties, so every one of them
refuses with a reason when there is no connection instead of queueing a
request that could never be replayed. Two of the three public Overpass
mirrors are usually dead and the third is intermittent, so the POI overlay
asks in batches of three categories and names the ones that did not answer
rather than losing the whole overlay to one bad batch.
"""
import asyncio
import time

import flet as ft

from .. import api as M
from .. import panels
from ..ui import PAD, PAL, BRD, card, chip, sub, title
from . import _trips_parts as T

try:                      # optional: the app still runs without the extensions
    import flet_map as fmap
    import flet_geolocator as fgeo
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = fgeo = None
    MAPS_AVAILABLE = False

from .. import tileserver

# Oakland. Only used until the first fix arrives.
HOME = (37.8044, -122.2712)

# Recording state lives at module level so switching tabs does not silently
# stop a ride you are in the middle of recording.
_track = {"points": [], "recording": False, "started": 0.0,
          "last": None, "distance_m": 0.0, "last_speed": None}

# The navigation half's state, for the same reason: a route computed here
# must survive a repaint caused by anything else on the page.
_NAV = {"a": None, "b": None, "route": None, "limits": None,
        "speeds": [], "corners": [], "profile": None, "profile_error": "",
        "pois": {}, "poi_failed": [], "poi_open": False, "active": set(),
        "note": "", "trip_id": ""}


def _haversine(a, b):
    """Metres between two (lat, lon) pairs."""
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def _mph(metres_per_second):
    return (metres_per_second or 0) * 2.2369362920544


def _fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _update(page):
    try:
        page.update()
    except Exception:                                  # noqa: BLE001
        pass


def _run(page, coro_fn, *args):
    """page.run_task when there is a loop; quietly skip when there is not."""
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return None
    try:
        return runner(coro_fn, *args)
    except Exception:                                  # noqa: BLE001
        return None


def build(page):
    p = PAL()

    if not MAPS_AVAILABLE:
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Map unavailable", 14),
            sub("This build was made without flet-map / flet-geolocator. "
                "Rebuild with them in requirements.txt.", 11)]), pad=14)])

    # ---- tiles: prefer the on-device archive, fall back to online ----------
    local_url = tileserver.start(M.mbtiles_path())
    offline = local_url is not None
    url_template = local_url or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    status_chip = chip("offline maps" if offline else "online maps",
                       p["green"] if offline else p["yellow"],
                       ft.Icons.MAP, small=True)

    here = ft.Text("waiting for GPS…", size=11, color=p["text2"])
    speed_text = ft.Text("—", size=30, weight=ft.FontWeight.W_700, color=p["text"])
    stat_dist = ft.Text("0.0 mi", size=15, weight=ft.FontWeight.W_600, color=p["text"])
    stat_time = ft.Text("0:00", size=15, weight=ft.FontWeight.W_600, color=p["text"])
    stat_pts = ft.Text("0", size=15, weight=ft.FontWeight.W_600, color=p["text"])
    note = ft.Text("", size=11, color=p["text2"])

    position_marker = fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(*HOME),
        width=22, height=22,
        content=ft.Container(width=18, height=18, border_radius=9,
                             bgcolor=p["accent"],
                             border=ft.Border.all(3, ft.Colors.WHITE)))

    track_line = fmap.PolylineMarker(
        coordinates=[], color=p["accent"], stroke_width=5,
        border_color=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
        border_stroke_width=1)
    line_layer = fmap.PolylineLayer(polylines=[track_line])

    # The navigation overlays. Separate layers so a POI refresh never
    # redraws the route and a reset never clears the recorded track.
    route_layer = fmap.PolylineLayer(polylines=[])
    nav_marker_layer = fmap.MarkerLayer(markers=[])
    poi_layer = fmap.MarkerLayer(markers=[])
    marker_layer = fmap.MarkerLayer(markers=[position_marker])

    the_map = fmap.Map(
        expand=True,
        initial_center=fmap.MapLatitudeLongitude(*HOME),
        initial_zoom=14, min_zoom=3, max_zoom=18,
        bgcolor=p["bg2"],
        layers=[fmap.TileLayer(url_template=url_template,
                               user_agent_package_name="online.iamprettyfamous.agedashii"),
                route_layer, line_layer, nav_marker_layer, poi_layer,
                marker_layer])

    def redraw_stats():
        speed_text.value = f"{_mph(_track.get('last_speed')):.0f}"
        stat_dist.value = f"{_track['distance_m'] / 1609.344:.1f} mi"
        stat_time.value = (_fmt_duration(time.time() - _track["started"])
                           if _track["recording"] else "0:00")
        stat_pts.value = str(len(_track["points"]))

    def _append(lat, lon, pos):
        _track["points"].append({
            "t": round(time.time(), 1), "lat": round(lat, 6), "lon": round(lon, 6),
            "speed": getattr(pos, "speed", None),
            "heading": getattr(pos, "heading", None),
            "alt": getattr(pos, "altitude", None),
            "acc": getattr(pos, "accuracy", None)})
        track_line.coordinates = [fmap.MapLatitudeLongitude(q["lat"], q["lon"])
                                  for q in _track["points"]]

    def on_position(e):
        """A new GPS fix. Move the marker; extend the track if recording."""
        pos = getattr(e, "position", None) or e
        lat, lon = getattr(pos, "latitude", None), getattr(pos, "longitude", None)
        if lat is None or lon is None:
            return
        accuracy = getattr(pos, "accuracy", None)
        _track["last_speed"] = getattr(pos, "speed", None)

        position_marker.coordinates = fmap.MapLatitudeLongitude(lat, lon)
        heading = getattr(pos, "heading", None)
        here.value = (f"{lat:.5f}, {lon:.5f}"
                      + (f"  ±{accuracy:.0f} m" if accuracy else "")
                      + (f"  hdg {heading:.0f}°" if heading else ""))

        if _track["recording"]:
            # Drop fixes worse than 50 m: one bad fix under a bridge
            # otherwise adds a kilometre of phantom distance to the ride.
            if accuracy is None or accuracy <= 50:
                prev = _track["last"]
                if prev is None:
                    _track["last"] = (lat, lon)
                    _append(lat, lon, pos)
                else:
                    step = _haversine(prev, (lat, lon))
                    # Under ~2 m is GPS jitter while stopped, not movement.
                    if step >= 2:
                        _track["distance_m"] += step
                        _track["last"] = (lat, lon)
                        _append(lat, lon, pos)
        redraw_stats()
        _update(page)

    def on_geo_error(e):
        here.value = f"GPS error: {getattr(e, 'data', e)}"
        _update(page)

    geo = fgeo.Geolocator(
        configuration=fgeo.GeolocatorConfiguration(
            accuracy=fgeo.GeolocatorPositionAccuracy.BEST),
        on_position_change=on_position,
        on_error=on_geo_error)
    if hasattr(page, "services") and isinstance(getattr(page, "services", None), list):
        page.services.append(geo)

    async def locate(e=None):
        try:
            await geo.request_permission()
            pos = await geo.get_current_position()
            on_position(pos)
        except Exception as exc:                       # noqa: BLE001
            note.value = f"could not get a fix: {exc}"
        _update(page)

    async def toggle_record(e):
        if _track["recording"]:
            _track["recording"] = False
            note.value = (f"stopped - {len(_track['points'])} points, "
                          f"{_track['distance_m'] / 1609.344:.1f} mi")
        else:
            _track.update({"points": [], "recording": True, "started": time.time(),
                           "last": None, "distance_m": 0.0})
            track_line.coordinates = []
            note.value = "recording"
        # FilledButton has no .text in Flet 0.86 - the label lives in
        # .content. Assigning .text is accepted silently and the button never
        # relabels, so you could not tell recording from stopped.
        rec_button.content = "Stop" if _track["recording"] else "Record"
        redraw_stats()
        _update(page)

    async def save_ride(e):
        if not _track["points"]:
            note.value = "nothing recorded yet"
            _update(page)
            return
        name = f"ride-{time.strftime('%Y-%m-%d-%H%M')}"
        ok, msg = M.ride_save(name, _track["points"], _track["distance_m"])
        note.value = (f"saved {name} ({len(_track['points'])} points)"
                      if ok else f"not saved - {msg}")
        _update(page)

    rec_button = ft.FilledButton("Record", height=38, on_click=toggle_record)

    def stat(label, control):
        return ft.Column(spacing=1, controls=[sub(label, 10), control])

    redraw_stats()

    # ------------------------------------------------------ navigation half
    nav = _navigator(page, p, the_map, route_layer, nav_marker_layer, poi_layer)

    def live_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       status_chip, ft.Container(expand=True),
                       ft.IconButton(ft.Icons.MY_LOCATION, icon_color=p["accent"],
                                     tooltip="find me", on_click=locate)]),
            ft.Row(spacing=16, vertical_alignment=ft.CrossAxisAlignment.END, controls=[
                ft.Row(spacing=4, vertical_alignment=ft.CrossAxisAlignment.END,
                       controls=[speed_text, sub("mph", 11)]),
                ft.Container(expand=True),
                stat("distance", stat_dist), stat("moving", stat_time),
                stat("points", stat_pts),
            ]),
            here,
            ft.Row(spacing=8, controls=[
                rec_button,
                ft.OutlinedButton("Save ride", height=38, on_click=save_ride),
                ft.Container(expand=True), note,
            ]),
        ]))

    def map_card():
        return ft.Container(height=380, border_radius=12,
                            clip_behavior=ft.ClipBehavior.HARD_EDGE,
                            content=the_map)

    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("trips.xnav", {
            "live": live_card,
            "route": nav["route"],
            "next": nav["next"],
            "pois": nav["pois"],
            "map": map_card,
            "elevation": nav["elevation"],
            "stats": nav["stats"],
            "maneuvers": nav["maneuvers"],
        })),
    ])


# ---------------------------------------------------------------- navigator

def _navigator(page, p, the_map, route_layer, nav_markers, poi_layer):
    """The website's xNav, on the recorder's map.

    Returns a builder per panel so the cards can be reordered, resized or
    hidden like everything else. All of them close over the same `_NAV`, so
    a route loaded from the picker repaints the badges, the stats, the
    profile and the turn list together.
    """
    trips = T.trips_all() or []

    note = ft.Text(_NAV["note"], size=11, color=p["text2"], selectable=True)
    next_icon = ft.Icon(ft.Icons.CIRCLE, size=26, color=p["accent"])
    next_dist = ft.Text("—", size=22, weight=ft.FontWeight.W_700, color=p["text"])
    next_instr = ft.Text("Pick a saved route, or tap the map to set a start",
                         size=12, color=p["text2"])
    limit_badge = ft.Container(
        width=44, height=44, border_radius=22, alignment=ft.Alignment.CENTER,
        bgcolor=p["bg2"], border=BRD(p["border"], 2),
        content=ft.Text("—", size=15, weight=ft.FontWeight.W_700,
                        color=p["text"]))
    elev_badge = ft.Text("—", size=12, color=p["text2"])
    stats_row = ft.Row(spacing=8, wrap=True, controls=[])
    maneuvers_col = ft.Column(spacing=0, controls=[])
    elev_box = ft.Container()

    def say(msg, bad=False):
        _NAV["note"] = msg
        note.value = msg
        note.color = p["pink"] if bad else p["text2"]
        _update(page)

    # ---- painting --------------------------------------------------------

    def paint_route():
        r = _NAV["route"]
        lines, markers = [], []
        if r and r.get("ok"):
            pts = r["points"]
            speeds = _NAV["speeds"] or [None] * len(pts)
            # One polyline per run of equal colour: a 2,000-point route
            # drawn segment by segment is 2,000 controls and a dead UI.
            run, run_colour = [], None
            for i, q in enumerate(pts):
                colour = T.maxspeed_colour(speeds[i] if i < len(speeds) else None)
                if run and colour != run_colour:
                    lines.append(fmap.PolylineMarker(
                        coordinates=run, color=run_colour, stroke_width=6))
                    run = [run[-1]]
                run_colour = colour
                run.append(fmap.MapLatitudeLongitude(q[0], q[1]))
            if len(run) > 1:
                lines.append(fmap.PolylineMarker(
                    coordinates=run, color=run_colour, stroke_width=6))
            for idx in _NAV["corners"]:
                if idx < len(pts):
                    markers.append(fmap.Marker(
                        coordinates=fmap.MapLatitudeLongitude(*pts[idx]),
                        width=12, height=12,
                        content=ft.Container(width=10, height=10, border_radius=5,
                                             bgcolor="#ff8c00")))
            for i, m in enumerate(r.get("maneuvers") or []):
                if i == 0 or m["index"] >= len(pts):
                    continue
                markers.append(fmap.Marker(
                    coordinates=fmap.MapLatitudeLongitude(*pts[m["index"]]),
                    width=16, height=16,
                    content=ft.Icon(T.maneuver_icon(m.get("type")), size=14,
                                    color=p["text"])))
        if _NAV["a"]:
            markers.append(_dot(p["green"], _NAV["a"]))
        if _NAV["b"]:
            markers.append(_dot(p["pink"], _NAV["b"]))
        route_layer.polylines = lines
        nav_markers.markers = markers

    def paint_badges():
        r = _NAV["route"]
        speeds = _NAV["speeds"]
        if speeds:
            mph = next((v for v in speeds if v), None)
            limit_badge.content = ft.Text(
                str(int(mph)) if mph else "—", size=15,
                weight=ft.FontWeight.W_700, color=ft.Colors.BLACK)
            limit_badge.bgcolor = T.maxspeed_colour(mph)
        else:
            limit_badge.content = ft.Text("—", size=15, color=p["text2"])
            limit_badge.bgcolor = p["bg2"]
        delta = (T.elevation_delta_ft(r["points"], _NAV["profile"])
                 if r and r.get("ok") and _NAV["profile"] else None)
        if delta is None:
            elev_badge.value = "—"
            elev_badge.color = p["text2"]
        else:
            elev_badge.value = f"{'+' if delta > 0 else ''}{delta} ft"
            elev_badge.color = (p["pink"] if delta > 30 else
                                p["green"] if delta < -30 else p["text2"])

    def paint_stats():
        r = _NAV["route"]
        if not (r and r.get("ok")):
            stats_row.controls = [
                _tile(p, "Distance", "—", "mi"), _tile(p, "Duration", "—"),
                _tile(p, "Ascent", "—", "ft"), _tile(p, "Descent", "—", "ft"),
                _tile(p, "Curves", "—")]
            return
        asc, desc = (T.profile_climb(_NAV["profile"]) if _NAV["profile"]
                     else (r.get("ascent_ft"), r.get("descent_ft")))
        stats_row.controls = [
            _tile(p, "Distance", f"{r['distance_mi']:.1f}", "mi"),
            _tile(p, "Duration", T.fmt_minutes(r["duration_min"])),
            _tile(p, "Ascent", asc if asc is not None else "—", "ft"),
            _tile(p, "Descent", desc if desc is not None else "—", "ft"),
            _tile(p, "Curves", len(_NAV["corners"])),
        ]

    def paint_maneuvers():
        r = _NAV["route"]
        rows = []
        for i, m in enumerate((r or {}).get("maneuvers") or []):
            rows.append(ft.Container(
                padding=PAD(6, 0),
                border=ft.Border(bottom=ft.BorderSide(1, p["border"])),
                content=ft.Row(spacing=10, controls=[
                    ft.Icon(T.maneuver_icon(m.get("type")), size=16,
                            color=p["accent"] if i else p["green"]),
                    ft.Text(m["instruction"], size=12, color=p["text"],
                            expand=True),
                    ft.Text(T.fmt_miles(m["length_mi"]), size=11,
                            color=p["text2"]),
                ])))
        maneuvers_col.controls = rows or [
            _hint(p, "No route yet — pick one above or tap the map twice.")]

    def paint_next():
        r = _NAV["route"]
        if not (r and r.get("ok")):
            next_icon.icon = ft.Icons.CIRCLE
            next_dist.value = "—"
            next_instr.value = ("Pick a saved route, or tap the map to set "
                                "a start")
            return
        mans = r.get("maneuvers") or []
        nxt = mans[1] if len(mans) > 1 else None
        next_icon.icon = (T.maneuver_icon(nxt.get("type")) if nxt
                          else ft.Icons.TURN_SLIGHT_RIGHT)
        next_dist.value = T.fmt_miles(nxt["length_mi"]) if nxt else "—"
        next_instr.value = (nxt["instruction"] if nxt else "No turns on this route")

    def paint_elevation():
        if _NAV["profile"]:
            elev_box.content = _profile_bars(p, _NAV["profile"])
        elif _NAV["profile_error"]:
            elev_box.content = _hint(p, _NAV["profile_error"])
        elif _NAV["route"] and _NAV["route"].get("ok"):
            elev_box.content = _hint(p, "fetching elevation…")
        else:
            elev_box.content = _hint(p, "No route yet — nothing to profile.")

    def paint_pois():
        markers = []
        for cat_id in _NAV["active"]:
            cat = T.POI_BY_ID.get(cat_id)
            for poi in _NAV["pois"].get(cat_id, []):
                markers.append(fmap.Marker(
                    coordinates=fmap.MapLatitudeLongitude(poi["lat"], poi["lon"]),
                    width=22, height=22,
                    content=ft.Container(
                        width=20, height=20, border_radius=10, bgcolor=p["bg2"],
                        border=ft.Border.all(1, p["accent"]),
                        tooltip=poi["name"],
                        content=ft.Icon(cat["icon"] if cat else ft.Icons.PLACE,
                                        size=12, color=p["accent"]))))
        poi_layer.markers = markers

    def paint_all():
        paint_route()
        paint_badges()
        paint_stats()
        paint_maneuvers()
        paint_next()
        paint_elevation()
        paint_pois()

    # ---- actions ---------------------------------------------------------

    async def route_between(waypoints):
        say("routing…")
        res = await asyncio.to_thread(T.valhalla_route, waypoints)
        if not res.get("ok"):
            _NAV["route"] = None
            paint_all()
            say(res.get("error", "no route"), bad=True)
            return
        _NAV["route"] = res
        _NAV["corners"] = T.corners(res["points"])
        _NAV["speeds"] = []
        _NAV["profile"] = None
        _NAV["profile_error"] = ""
        paint_all()
        say(f"{res['distance_mi']:.1f} mi · {T.fmt_minutes(res['duration_min'])}")
        await _fit(the_map, res["points"])

        # Speed limits and elevation are separate live services; each one
        # failing must leave the route drawn and say what is missing.
        bbox = T.bbox_of(res["points"])
        ways, err = await asyncio.to_thread(T.maxspeed_ways, bbox)
        if ways:
            _NAV["limits"] = ways
            _NAV["speeds"] = await asyncio.to_thread(
                T.speed_lookup, res["points"], ways, res["segments"])
        elif err:
            say(f"route drawn; speed limits unavailable — {err}")
        paint_route()
        paint_badges()
        _update(page)

        profile, perr = await asyncio.to_thread(T.elevations, res["points"])
        _NAV["profile"] = profile
        _NAV["profile_error"] = perr if not profile else ""
        paint_elevation()
        paint_badges()
        paint_stats()
        _update(page)

    def on_tap(e):
        coords = getattr(e, "coordinates", None)
        if coords is None:
            return
        here = (coords.latitude, coords.longitude)
        if _NAV["a"] is None:
            _NAV["a"] = here
            paint_route()
            say("start set — tap again for the destination")
            return
        if _NAV["b"] is None:
            _NAV["b"] = here
            paint_route()
            _update(page)
            _run(page, route_between,
                 [{"lat": _NAV["a"][0], "lon": _NAV["a"][1]},
                  {"lat": here[0], "lon": here[1]}])
            return
        reset(None)
        _NAV["a"] = here
        paint_route()
        say("start set — tap again for the destination")

    the_map.on_tap = on_tap

    def reset(e):
        _NAV.update({"a": None, "b": None, "route": None, "limits": None,
                     "speeds": [], "corners": [], "profile": None,
                     "profile_error": "", "trip_id": ""})
        paint_all()
        say("")

    def pick_trip(e):
        tid = getattr(e.control, "value", "") or ""
        trip = next((t for t in trips if t.get("id") == tid), None)
        if trip is None:
            return
        wps = T.waypoints_of(trip)
        if len(wps) < 2:
            say(f"{trip.get('name', tid)} has fewer than two waypoints", bad=True)
            return
        reset(None)
        _NAV["trip_id"] = tid
        _NAV["a"] = (wps[0]["lat"], wps[0]["lon"])
        _NAV["b"] = (wps[-1]["lat"], wps[-1]["lon"])
        paint_route()
        _update(page)
        _run(page, route_between, [{"lat": w["lat"], "lon": w["lon"]} for w in wps])

    async def toggle_poi(cat_id):
        if cat_id in _NAV["active"]:
            _NAV["active"].discard(cat_id)
            paint_pois()
            _update(page)
            return
        _NAV["active"].add(cat_id)
        paint_pois()
        say(f"asking OpenStreetMap for {T.POI_BY_ID[cat_id]['label']}…")
        bounds = _bounds_for_pois()
        found, failed, err = await asyncio.to_thread(
            T.fetch_pois, [cat_id], bounds)
        if err:
            _NAV["active"].discard(cat_id)
            say(err, bad=True)
            return
        _NAV["pois"][cat_id] = found.get(cat_id, [])
        _NAV["poi_failed"] = failed
        paint_pois()
        count = len(_NAV["pois"][cat_id])
        say(f"{count} {T.POI_BY_ID[cat_id]['label'].lower()} nearby"
            if count else
            f"no {T.POI_BY_ID[cat_id]['label'].lower()} in this view")

    async def load_all_pois():
        wanted = sorted(_NAV["active"])
        if not wanted:
            say("tick some categories first", bad=True)
            return
        say(f"asking OpenStreetMap for {len(wanted)} categories, three at a time…")
        found, failed, err = await asyncio.to_thread(
            T.fetch_pois, wanted, _bounds_for_pois())
        if err:
            say(err, bad=True)
            return
        _NAV["pois"].update(found)
        _NAV["poi_failed"] = failed
        paint_pois()
        total = sum(len(v) for v in found.values())
        say(f"{total} places" + (f" · {len(failed)} categories did not answer: "
                                 f"{', '.join(failed)}" if failed else ""))

    def _bounds_for_pois():
        r = _NAV["route"]
        if r and r.get("ok"):
            return T.bbox_of(r["points"], pad=0.05)
        if _track["points"]:
            return T.bbox_of([(q["lat"], q["lon"]) for q in _track["points"]],
                             pad=0.2)
        lat, lon = HOME
        return (lat - 0.03, lon - 0.03, lat + 0.03, lon + 0.03)

    paint_all()

    # ---- the cards -------------------------------------------------------

    def route_card():
        options = [ft.DropdownOption(key="", text="— pick a saved route —")]
        options += [ft.DropdownOption(key=t["id"], text=t.get("name", t["id"]))
                    for t in trips if t.get("id")]
        # on_select, not on_change: Flet 0.86's Dropdown never fires
        # on_change, and assigning it is accepted in silence.
        dd = ft.Dropdown(value=_NAV["trip_id"], options=options,
                         on_select=pick_trip, dense=True, filled=True,
                         text_size=12, width=250, menu_height=340,
                         border_color=p["border"], color=p["text"])
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(ft.Icons.NAVIGATION, size=16, color=p["accent"]),
                       title("xNav", 14),
                       sub("route · speed limits · curves · elevation", 10)]),
            ft.Row(spacing=6, wrap=True, controls=[
                dd,
                ft.OutlinedButton("Reset", height=34, icon=ft.Icons.ROTATE_LEFT,
                                  on_click=reset),
                ft.OutlinedButton("POIs", height=34, icon=ft.Icons.PLACE,
                                  on_click=lambda e: _toggle_poi_panel(page)),
            ]),
            *([] if trips else
              [_hint(p, "No saved routes on the server — tap the map twice to "
                        "route between two points instead.")]),
            sub("Tap the map for a start, tap again for the destination. "
                "Speed limits come from OpenStreetMap and the route from "
                "Valhalla, so both need a connection.", 10),
            note,
        ]), pad=12)

    def next_card():
        return card(ft.Row(spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               next_icon,
                               ft.Column(spacing=0, expand=True, controls=[
                                   next_dist, next_instr]),
                               ft.Column(spacing=2, horizontal_alignment=
                                         ft.CrossAxisAlignment.CENTER, controls=[
                                             limit_badge,
                                             ft.Text("limit", size=9,
                                                     color=p["text2"])]),
                               ft.Column(spacing=2, horizontal_alignment=
                                         ft.CrossAxisAlignment.CENTER, controls=[
                                             ft.Icon(ft.Icons.TRENDING_UP, size=16,
                                                     color=p["text2"]),
                                             elev_badge,
                                             ft.Text("next 100 ft", size=9,
                                                     color=p["text2"])]),
                           ]), pad=12)

    def poi_card():
        # Closed is an empty container, not a hidden column inside a card:
        # the card's own padding and border would otherwise leave a blank
        # box sitting in the stack with nothing in it.
        if not _NAV["poi_open"]:
            return ft.Container()
        chips = []
        for cat in T.POI_CATS:
            on = cat["id"] in _NAV["active"]
            colour = p["accent"] if on else p["text2"]
            chips.append(ft.Container(
                padding=PAD(4, 9), border_radius=16,
                bgcolor=ft.Colors.with_opacity(0.16 if on else 0.05, colour),
                border=BRD(ft.Colors.with_opacity(0.45 if on else 0.22, colour), 1),
                on_click=lambda e, c=cat["id"]: _run(page, toggle_poi, c),
                content=ft.Row(spacing=4, tight=True, controls=[
                    ft.Icon(cat["icon"], size=13, color=colour),
                    ft.Text(cat["label"], size=10, color=colour)])))
        failed = _NAV["poi_failed"]
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(ft.Icons.PLACE, size=15, color=p["accent"]),
                       title("POIs along the route", 13),
                       ft.Container(expand=True),
                       ft.OutlinedButton("Load ticked", height=30,
                                         icon=ft.Icons.DOWNLOAD,
                                         on_click=lambda e: _run(page, load_all_pois)),
                   ]),
            ft.Row(spacing=5, wrap=True, controls=chips),
            *([_hint(p, f"{len(failed)} categories went unanswered: "
                        f"{', '.join(failed)}. Overpass is intermittent; "
                        f"tap Load ticked again to retry just those.")]
              if failed else []),
        ]), pad=12)

    def stats_card():
        return card(ft.Column(spacing=8, controls=[
            title("Route stats", 13), stats_row]), pad=12)

    def maneuvers_card():
        return card(ft.Column(spacing=4, controls=[
            ft.Row(spacing=8, controls=[
                ft.Icon(ft.Icons.LIST, size=15, color=p["accent"]),
                title("Turn-by-turn", 13)]),
            maneuvers_col]), pad=10)

    def elevation_card():
        return card(ft.Column(spacing=8, controls=[
            title("Elevation", 13), elev_box]), pad=12)

    return {"route": route_card, "next": next_card, "pois": poi_card,
            "stats": stats_card, "maneuvers": maneuvers_card,
            "elevation": elevation_card}


def _toggle_poi_panel(page):
    _NAV["poi_open"] = not _NAV["poi_open"]
    from ._forms import rebuilder
    from . import trips as _trips
    rebuilder(page, _trips.build)()


def _dot(colour, at):
    return fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(*at), width=18, height=18,
        content=ft.Container(width=14, height=14, border_radius=7, bgcolor=colour,
                             border=ft.Border.all(2, ft.Colors.WHITE)))


async def _fit(the_map, points):
    """Frame a route. `Map.move_to` is a coroutine in 0.86, not a method
    call that returns - awaiting it is the only thing that moves the map."""
    if the_map is None or not points:
        return
    lats = [q[0] for q in points]
    lons = [q[1] for q in points]
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.8, 1e-4)
    zoom = 6
    for level, limit in ((15, 0.004), (14, 0.008), (13, 0.016), (12, 0.033),
                         (11, 0.066), (10, 0.13), (9, 0.26), (8, 0.52), (7, 1.05)):
        if span <= limit:
            zoom = level
            break
    try:
        await the_map.move_to(
            destination=fmap.MapLatitudeLongitude(
                (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2),
            zoom=zoom)
    except Exception:                                  # noqa: BLE001
        pass


def _tile(p, label, value, unit=""):
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


def _hint(p, text):
    return ft.Row(spacing=8, controls=[
        ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=p["text2"]),
        ft.Text(text, size=11, color=p["text2"], expand=True)])


def _profile_bars(p, profile, width_hint=44):
    """The elevation profile as bars. Flet 0.86 ships no chart control."""
    values = [q["ele_ft"] for q in profile if q.get("ele_ft") is not None]
    if len(values) < 2:
        return _hint(p, "no elevation data for this route")
    lo, hi = min(values), max(values)
    span = max(1.0, hi - lo)
    step = max(1, len(values) // width_hint)
    bars = [ft.Container(width=4, height=6 + (v - lo) / span * 46, border_radius=2,
                         bgcolor=ft.Colors.with_opacity(
                             0.35 + 0.6 * (v - lo) / span, p["accent"]))
            for v in values[::step]]
    up, down = T.profile_climb(profile)
    return ft.Column(spacing=4, controls=[
        ft.Row(spacing=8, controls=[
            sub("along the route", 10), ft.Container(expand=True),
            ft.Text(f"↑{up} ↓{down} ft", size=10, color=p["text2"])]),
        ft.Container(padding=PAD(4, 0),
                     content=ft.Row(spacing=1, controls=bars, wrap=False,
                                    vertical_alignment=ft.CrossAxisAlignment.END,
                                    scroll=ft.ScrollMode.AUTO)),
        ft.Row([ft.Text(f"{int(lo)} ft", size=9, color=p["text2"]),
                ft.Container(expand=True),
                ft.Text(f"{int(hi)} ft", size=9, color=p["text2"])]),
    ])
