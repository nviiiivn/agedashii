"""XNAV - live map, GPS, and track recording. Works with the radio off.

This is the page that has to keep working on a motorcycle in a dead zone,
so nothing here requires a network:

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
"""
import time

import flet as ft

from .. import api as M
from ..ui import PAL, card, chip, sub, title

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
    marker_layer = fmap.MarkerLayer(markers=[position_marker])

    track_line = fmap.PolylineMarker(
        coordinates=[], color=p["accent"], stroke_width=5,
        border_color=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
        border_stroke_width=1)
    line_layer = fmap.PolylineLayer(polylines=[track_line])

    the_map = fmap.Map(
        expand=True,
        initial_center=fmap.MapLatitudeLongitude(*HOME),
        initial_zoom=14, min_zoom=3, max_zoom=18,
        bgcolor=p["bg2"],
        layers=[fmap.TileLayer(url_template=url_template,
                               user_agent_package_name="online.iamprettyfamous.nviidash"),
                line_layer, marker_layer])

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
        page.update()

    def on_geo_error(e):
        here.value = f"GPS error: {getattr(e, 'data', e)}"
        page.update()

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
        page.update()

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
        rec_button.text = "Stop" if _track["recording"] else "Record"
        redraw_stats()
        page.update()

    async def save_ride(e):
        if not _track["points"]:
            note.value = "nothing recorded yet"
            page.update()
            return
        name = f"ride-{time.strftime('%Y-%m-%d-%H%M')}"
        ok, msg = M.ride_save(name, _track["points"], _track["distance_m"])
        note.value = (f"saved {name} ({len(_track['points'])} points)"
                      if ok else f"not saved - {msg}")
        page.update()

    rec_button = ft.FilledButton("Record", height=38, on_click=toggle_record)

    def stat(label, control):
        return ft.Column(spacing=1, controls=[sub(label, 10), control])

    redraw_stats()
    return ft.Column(spacing=8, expand=True, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            status_chip, ft.Container(expand=True),
            ft.IconButton(ft.Icons.MY_LOCATION, icon_color=p["accent"], on_click=locate),
        ]),
        ft.Container(expand=True, border_radius=12,
                     clip_behavior=ft.ClipBehavior.HARD_EDGE, content=the_map),
        card(ft.Column(spacing=8, controls=[
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
        ])),
    ])
