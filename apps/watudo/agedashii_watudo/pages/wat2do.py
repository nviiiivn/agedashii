"""Wat2Do - the day's checklist, how you feel, and what to do about it.

This is the phone's half of the website's Wat2Do (dashbored-web, the `w2d*`
functions in app.js). It is built against the same four endpoints the browser
uses - `/api/wat2do/state`, `/save`, `/template`, `/missions` - plus the same
`/api/weather`, `/api/overpass`, `/api/wat2do/notes`, `/api/valhalla/route`
and `/api/future/*` reads, so the two clients show the same day rather than
two different opinions about it.

Three rules shape the code:

* **Nothing is ticked until the server says so.** `/api/wat2do/save` answers
  with the record it now holds, so every write repaints from the response.
  A box that ticks locally and never reached the server is the exact lie this
  page used to tell - it saved fire-and-forget and swallowed the error.
* **Check keys match the website's.** Sub-items are stored as
  `"<root> / <sub>"`. See `_wat2do_parts` - the old code keyed them by the raw
  template line, so the phone and the browser disagreed about every sub-task.
* **Slow reads never block the first paint.** Overpass and the Future lists
  take seconds, so they load in the background and say "loading" until they
  land; they never show a placeholder that looks like real data.

Session state lives in `_S` at module level so leaving the page and coming
back does not lose the day you were looking at or the spots you scanned.
"""
import asyncio
import math
import random
import time as _time

import flet as ft

from agedashii_washi import api as M
from agedashii_washi import panels
from agedashii_washi.ui_compat import PAL, PAD, BRD, RAD, card, chip, sub, title
from agedashii_washi.forms import offline_banner
from agedashii_washi.surface import _TAB_INDEX, _tabs
from . import _wat2do_parts as W
from . import future
from .. import prefs as wprefs

try:                      # optional: the app still runs without the extension
    import flet_map as fmap
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = None
    MAPS_AVAILABLE = False

MAP_HEIGHT = 260
LIST_HEIGHT = 190

# The key this page's tab index is stored under, in `surface._TAB_INDEX`.
# The app's shell writes it to navigate and reads it back to know where it
# is, so it is named once here rather than spelled out at each use.
TAB_KEY = "wat2do"

_S = {
    "date": None,       # which day is on screen
    "state": None,      # the server's record for that day
    "wx": None,         # raw /api/weather payload
    "basemap": "light",
    "mode": None,       # 'walkable' | 'indoor' | 'food' | 'all'
    "sel_cats": set(),  # category ids picked by hand, when mode == 'all'
    "cats_open": False,
    "spots": [],
    "notes": [],
    "notes_on": False,
    "deck": [],         # mission subject deck
    "venues": None,     # cached overpass elements for the mission roll
    "mission": None,
    "route": [],        # decoded route geometry, [(lat, lon), ...]
    "route_color": "#3fb950",
    "today": None,      # {'events', 'thrift', 'movies', 'fresh'}
    "poi_note": "",     # what the last scan said, so a repaint keeps saying it
    "mission_note": "",
}


def _num(v):
    """A slider-safe number, or None when the server has never been told."""
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def build(page, tabs=True):
    """Present | Future, the two sub-views the website's Wat2Do page has.

    Future Finds is `future.py`, imported rather than reimplemented - it was
    built against /api/future/{scan,gems,photos,sv} yesterday and is already
    at parity. It is also still reachable from Extras, which is where it was
    first mounted; the website keeps it here, next to the day it is planning
    for, so here is where it belongs. Nothing is duplicated: both tabs call
    the same module, and only one page renders at a time.

    `tabs=False` draws only the destination `_TAB_INDEX` names, with no tab
    bar over it. That is for the Watudo app, whose bottom bar already
    carries Present and Future: with both, a phone showed the same two
    choices twice, one row above the other, which is what the bottom bar
    switch existed to escape. Exactly one control offers them at a time now
    - the bar when it is on, this tab bar when it is not.

    It is also half the work. With a tab bar, `_tabs` is handed two
    already-built bodies, so every repaint built Future as well as Present
    (and left Customize showing on Future, which has no cards to arrange).
    The dashboard still calls this with no argument and gets the tabs it
    has always had.
    """
    if not tabs:
        return (future.build(page) if _TAB_INDEX.get(TAB_KEY, 0) == 1
                else _present(page))
    return _tabs(page, [("Present", _present(page)),
                        ("Future", future.build(page))], key=TAB_KEY)


def _present(page):
    p = PAL()

    if not _S["date"]:
        _S["date"] = W.today_str()
    date = _S["date"]

    fetched = W.state(date)
    if fetched is not None:
        _S["state"] = fetched
    if _S["wx"] is None:
        _S["wx"] = W.weather_now()

    st = _S["state"] or {}
    live = M.STATUS_LIVE() and fetched is not None
    wx_cur = (_S["wx"] or {}).get("current") if _S["wx"] else None

    status = ft.Text("", size=11, color=p["text2"], selectable=True)

    def say(ok, msg):
        status.value = msg if ok else f"not saved - {msg}"
        status.color = p["text2"] if ok else p["pink"]

    def repaint():
        try:
            page.update()
        except Exception:      # noqa: BLE001 - not mounted yet (first paint)
            pass

    # ------------------------------------------------------------ date nav
    date_label = ft.Text(W.date_label(date), size=16, weight=ft.FontWeight.W_600,
                         color=p["text"])
    date_sub = ft.Text(date, size=10, color=p["text2"])

    async def go_date(delta_or_today):
        _S["date"] = (W.today_str() if delta_or_today == "today"
                      else W.shift_date(_S["date"], delta_or_today))
        got = await asyncio.to_thread(W.state, _S["date"], True)
        if got is None:
            say(False, "could not read that day")
            repaint()
            return
        _S["state"] = got
        date_label.value = W.date_label(_S["date"])
        date_sub.value = _S["date"]
        say(True, "")
        paint_checklist()
        paint_vitals()
        paint_suggest()
        repaint()

    def date_nav():
        return card(ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.IconButton(ft.Icons.CHEVRON_LEFT, icon_color=p["text2"],
                              tooltip="Previous day",
                              on_click=lambda e: _run(page, go_date, -1)),
                ft.Column(spacing=0, expand=True,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[date_label, date_sub]),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, icon_color=p["text2"],
                              tooltip="Next day",
                              on_click=lambda e: _run(page, go_date, 1)),
                ft.IconButton(ft.Icons.TODAY, icon_color=p["accent"], tooltip="Back to today",
                              on_click=lambda e: _run(page, go_date, "today")),
                ft.IconButton(ft.Icons.EDIT_NOTE, icon_color=p["text2"],
                              tooltip="Edit the checklist template",
                              on_click=lambda e: _run(page, open_template)),
            ]), pad=6)

    # ------------------------------------------------------------ checklist
    items_box = ft.Column(spacing=2)
    prog = ft.ProgressBar(value=0, color=p["orange"], bgcolor=p["bg2"],
                          bar_height=5, border_radius=RAD(2))
    count = ft.Text("", size=11, color=p["text2"])

    def flat_rows():
        return W.flatten((_S["state"] or {}).get("items") or [],
                         (_S["state"] or {}).get("checks") or {})

    async def toggle(index):
        flat = flat_rows()
        if index >= len(flat):
            return
        checks = W.toggle_checks(flat, index, (_S["state"] or {}).get("checks") or {})
        status.value = "saving..."
        status.color = p["text2"]
        repaint()
        ok, msg, served = await asyncio.to_thread(
            W.save_state, _S["date"], checks,
            _num((_S["state"] or {}).get("pain")), _num((_S["state"] or {}).get("spoons")))
        if ok and served:
            # Repaint from what the server now holds, never from what we sent.
            _S["state"] = served
            say(True, "")
            paint_checklist()
        else:
            say(False, msg)
        repaint()

    def item_row(it, index):
        checked = W.row_checked(it)
        if it["has_subs"]:
            icon = (ft.Icons.DONE_ALL if checked
                    else (ft.Icons.INDETERMINATE_CHECK_BOX if it["sub_done"]
                          else ft.Icons.CHECK_BOX_OUTLINE_BLANK))
        else:
            icon = ft.Icons.CHECK_BOX if checked else ft.Icons.CHECK_BOX_OUTLINE_BLANK
        colour = p["green"] if checked else p["text2"]
        label = ft.Text(
            it["display"], size=12 if it["depth"] else 13, expand=True,
            color=(p["text2"] if checked else (p["text3"] if it["depth"] else p["text"])),
            style=(ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH)
                   if checked else None))
        row = [ft.Icon(icon, size=17, color=colour), label]
        if it["has_subs"]:
            row.append(sub(f"{it['sub_done']}/{it['sub_total']}", 10))
        return ft.Container(
            padding=ft.Padding(left=22 if it["depth"] else 0, top=3, right=2, bottom=3),
            border_radius=RAD(6), ink=live,
            on_click=(lambda e, i=index: _run(page, toggle, i)) if live else None,
            content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=row))

    def paint_checklist():
        flat = flat_rows()
        done, total = W.progress(flat)
        prog.value = (done / total) if total else 0
        count.value = f"{done}/{total}"
        # The index handed to `toggle` is the row's place in the *whole*
        # list, taken before anything is filtered out. Enumerating first and
        # filtering after is what keeps a tick landing on the task it was
        # next to once finished ones stop being drawn.
        hide = wprefs.hide_done()
        rows = [item_row(it, i) for i, it in enumerate(flat)
                if not (hide and W.row_checked(it))]
        if not rows and flat:
            # Everything is done and she has asked not to see finished
            # tasks. Saying so beats an empty card that looks like a
            # checklist that failed to load.
            rows = [sub(f"All {total} done for today.", 11)]
        items_box.controls = rows or [sub("No items - edit the template.", 11)]

    def checklist_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row([title("Checklist", 14), ft.Container(expand=True), count]),
            prog, items_box]))

    # --------------------------------------------------------------- vitals
    pain_val = _num(st.get("pain"))
    spoons_val = _num(st.get("spoons"))
    pain_text = ft.Text("", size=11, color=p["red"])
    spoons_text = ft.Text("", size=11, color=p["green"])
    pain_slider = ft.Slider(min=0, max=10, divisions=10, value=pain_val or 0,
                            active_color=p["red"], label="{value}", disabled=not live)
    spoons_slider = ft.Slider(min=1, max=5, divisions=4, value=spoons_val or 3,
                              active_color=p["green"], label="{value}", disabled=not live)

    def paint_vitals():
        s = _S["state"] or {}
        pv, sv = _num(s.get("pain")), _num(s.get("spoons"))
        pain_slider.value = pv if pv is not None else 0
        spoons_slider.value = sv if sv is not None else 3
        pain_text.value = f"pain {pv}/10" if pv is not None else "pain - not set"
        spoons_text.value = f"spoons {sv}/5" if sv is not None else "spoons - not set"

    def drag(which):
        """Dragging only moves the label, and marks it unsaved with '...'.

        The number does not become the truth until commit_vital has heard
        back from the server; until then the page is showing what your thumb
        is doing, not what is stored.
        """
        def handler(e):
            v = int(e.control.value)
            target = pain_text if which == "pain" else spoons_text
            target.value = (f"pain {v}/10 ..." if which == "pain"
                            else f"spoons {v}/5 ...")
            try:
                target.update()
            except Exception:      # noqa: BLE001 - not mounted yet
                pass
        return handler

    async def commit_vital(which, value):
        s = _S["state"] or {}
        pain = int(value) if which == "pain" else _num(s.get("pain"))
        spoons = int(value) if which == "spoons" else _num(s.get("spoons"))
        status.value = "saving..."
        status.color = p["text2"]
        repaint()
        ok, msg, served = await asyncio.to_thread(
            W.save_state, _S["date"], s.get("checks") or {}, pain, spoons)
        if ok and served:
            _S["state"] = served
            say(True, "")
        else:
            say(False, msg)      # slider snaps back to the server's value
        paint_vitals()
        paint_suggest()
        repaint()

    pain_slider.on_change = drag("pain")
    spoons_slider.on_change = drag("spoons")
    pain_slider.on_change_end = (
        lambda e: _run(page, commit_vital, "pain", e.control.value))
    spoons_slider.on_change_end = (
        lambda e: _run(page, commit_vital, "spoons", e.control.value))

    def vitals_card():
        return card(ft.Column(spacing=2, controls=[
            sub("VITALS", 10), pain_text, pain_slider, spoons_text, spoons_slider,
            sub("Saved when you let go of the slider.", 10)]))

    # -------------------------------------------------------------- weather
    def weather_card():
        if not wx_cur:
            return card(ft.Column(spacing=6, controls=[
                sub("WEATHER", 10), sub("Weather unavailable.", 11)]))
        code = wx_cur.get("weather_code")
        head = ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(W.wx_icon(code), size=30, color=p["yellow"]),
            ft.Column(spacing=0, expand=True, controls=[
                ft.Text(f"{round(wx_cur.get('temperature_2m') or 0)}°F", size=22,
                        weight=ft.FontWeight.W_700, color=p["text"]),
                sub(f"{W.wx_desc(code)} · feels "
                    f"{round(wx_cur.get('apparent_temperature') or 0)}°F · wind "
                    f"{round(wx_cur.get('wind_speed_10m') or 0)} mph", 11)]),
        ])
        daily = (_S["wx"] or {}).get("daily") or {}
        times = daily.get("time") or []
        tiles = []
        for i, t in enumerate(times[:3]):
            try:
                name = "Today" if i == 0 else _time.strftime(
                    "%a", _time.strptime(t, "%Y-%m-%d"))
            except (ValueError, TypeError):
                name = t
            hi = (daily.get("temperature_2m_max") or [None] * 3)[i]
            lo = (daily.get("temperature_2m_min") or [None] * 3)[i]
            dc = (daily.get("weather_code") or [None] * 3)[i]
            tiles.append(ft.Container(
                expand=True, padding=PAD(6, 4), border_radius=RAD(8), bgcolor=p["bg2"],
                content=ft.Column(spacing=2,
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                  controls=[
                                      sub(name, 10),
                                      ft.Icon(W.wx_icon(dc), size=16, color=p["text2"]),
                                      ft.Text(
                                          f"{round(hi)}° / {round(lo)}°"
                                          if hi is not None and lo is not None else "—",
                                          size=11, color=p["text"])])))
        text, tone = W.wx_callout(wx_cur)
        tone_colour = {"good": p["green"], "meh": p["yellow"], "bad": p["red"]}[tone]
        return card(ft.Column(spacing=8, controls=[
            sub("WEATHER", 10), head,
            ft.Row(spacing=6, controls=tiles) if tiles else ft.Container(),
            ft.Container(padding=PAD(6, 10), border_radius=RAD(8),
                         bgcolor=ft.Colors.with_opacity(0.12, tone_colour),
                         border=BRD(ft.Colors.with_opacity(0.35, tone_colour), 1),
                         content=ft.Text(text, size=12, color=tone_colour)),
        ]))

    # ------------------------------------------------------------- the map
    tile_layer = marker_layer = route_layer = the_map = None
    if MAPS_AVAILABLE:
        tile_layer = fmap.TileLayer(
            url_template=W.BASEMAPS[_S["basemap"]][1],
            user_agent_package_name="online.iamprettyfamous.agedashii")
        home_marker = fmap.Marker(
            coordinates=fmap.MapLatitudeLongitude(*W.origin()), width=16, height=16,
            content=ft.Container(width=14, height=14, border_radius=7,
                                 bgcolor="#3fb950",
                                 border=ft.Border.all(2, ft.Colors.WHITE)))
        marker_layer = fmap.MarkerLayer(markers=[home_marker])
        route_line = fmap.PolylineMarker(coordinates=[], color=_S["route_color"],
                                         stroke_width=4)
        route_layer = fmap.PolylineLayer(polylines=[route_line])
        the_map = fmap.Map(
            height=MAP_HEIGHT, bgcolor=p["bg2"], keep_alive=True,
            initial_center=fmap.MapLatitudeLongitude(*W.origin()), initial_zoom=13,
            min_zoom=3, max_zoom=18,
            layers=[tile_layer, route_layer, marker_layer])

    def _ll(lat, lon):
        return fmap.MapLatitudeLongitude(lat, lon)

    def paint_markers():
        if not MAPS_AVAILABLE:
            return
        markers = [marker_layer.markers[0]]
        for s in _S["spots"]:
            colour = W.CAT_BY_ID[s["cat"]]["color"]
            markers.append(fmap.Marker(
                coordinates=_ll(s["lat"], s["lon"]), width=14, height=14,
                content=ft.Container(width=11, height=11, border_radius=6, bgcolor=colour,
                                     tooltip=s["name"],
                                     border=ft.Border.all(2, ft.Colors.WHITE))))
        for n in (_S["notes"] if _S["notes_on"] else []):
            markers.append(fmap.Marker(
                coordinates=_ll(n["lat"], n["lon"]), width=14, height=14,
                content=ft.Container(width=11, height=11, border_radius=6, bgcolor="#f2c94c",
                                     tooltip=(n.get("text") or f"OSM note {n.get('id')}")[:120],
                                     border=ft.Border.all(2, ft.Colors.WHITE))))
        if _S["mission"] and _S["mission"].get("venue"):
            v = _S["mission"]["venue"]
            markers.append(fmap.Marker(
                coordinates=_ll(v["lat"], v["lon"]), width=20, height=20,
                content=ft.Container(width=17, height=17, border_radius=9, bgcolor="#ff2d95",
                                     tooltip=f"MISSION: {v['name']}",
                                     border=ft.Border.all(3, ft.Colors.WHITE))))
        marker_layer.markers = markers
        route_line.coordinates = [_ll(a, b) for a, b in _S["route"]]
        route_line.color = _S["route_color"]

    async def fit(points, pad=1.35, max_zoom=16):
        """Stand-in for Leaflet's fitBounds, which flet-map does not expose.

        flet-map can move and zoom but not fit a bounding box after the fact,
        so the zoom is derived from the span: at zoom z a 256 px tile spans
        360/2^z degrees of longitude, and the map here is roughly 1.6 tiles
        wide. Approximate on purpose - it beats leaving the map on the wrong
        continent.
        """
        if not MAPS_AVAILABLE or not points:
            return
        lats = [a for a, _ in points]
        lons = [b for _, b in points]
        centre = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
        span = max(max(lons) - min(lons), (max(lats) - min(lats)) / 0.7, 1e-4) * pad
        zoom = max(3, min(max_zoom, math.log2(360.0 * 1.6 / span)))
        try:
            await the_map.move_to(destination=_ll(*centre), zoom=zoom)
        except Exception:          # noqa: BLE001 - map not on screen yet
            pass

    async def draw_route(lat, lon, costing, colour):
        status.value = "routing..."
        status.color = p["text2"]
        repaint()
        ok, msg, r = await asyncio.to_thread(W.route, lat, lon, costing)
        if not ok or not r:
            say(False, msg)
            repaint()
            return
        _S["route"] = r["geometry"]
        _S["route_color"] = colour
        paint_markers()
        say(True, f"{round(r['minutes'])} min "
                  f"{'ride' if costing == 'motorcycle' else 'walk'} "
                  f"· {r['miles']:.1f} mi")
        await fit(r["geometry"] or [(lat, lon)])
        repaint()

    # ------------------------------------------------------ POIs and modes
    mode_row = ft.Row(spacing=6, wrap=True)
    cats_row = ft.Row(spacing=6, wrap=True, visible=False)
    spot_list = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=LIST_HEIGHT)
    poi_note = ft.Text("Pick a mode to find spots near home.", size=11, color=p["text2"])

    def active_cats():
        if _S["mode"] == "all":
            return [c for c in W.POI_CATS if c["id"] in _S["sel_cats"]]
        preset = W.MODES.get(_S["mode"] or "")
        return [c for c in W.POI_CATS if preset and c["id"] in preset["cats"]]

    def radius():
        if _S["mode"] == "all":
            return 8000
        return (W.MODES.get(_S["mode"] or "") or {"r": 4000})["r"]

    async def load_pois():
        cats = active_cats()
        if not cats:
            _S["spots"] = []
            poi_note.value = "Pick categories to show - tap the chips above."
            paint_markers()
            paint_spots()
            repaint()
            return
        poi_note.value = "scanning..."
        spot_list.controls = [sub("scanning...", 11)]
        repaint()
        elems, failed = await asyncio.to_thread(W.overpass, cats, radius())
        if elems is None:
            poi_note.value = "POI scan failed - the server or Overpass did not answer."
            spot_list.controls = []
            repaint()
            return
        allowed = {c["id"] for c in cats}
        _S["spots"] = W.spots_from(elems, allowed_ids=allowed)[:60]
        poi_note.value = (f"{len(_S['spots'])} spots within "
                          f"{W.miles(radius()):.1f} mi of home"
                          if _S["spots"] else W.empty_scan_note(elems, failed))
        if failed:
            # Say the list is short rather than let it look complete.
            poi_note.value += (" · Overpass dropped "
                               + ", ".join(W.CAT_BY_ID[i]["label"] for i in failed))
        _S["poi_note"] = poi_note.value
        paint_markers()
        paint_spots()
        await fit([W.origin()] + [(s["lat"], s["lon"]) for s in _S["spots"]])
        repaint()

    def paint_spots():
        rows = []
        for s in _S["spots"]:
            c = W.CAT_BY_ID[s["cat"]]
            rows.append(ft.Container(
                padding=PAD(4, 2), border_radius=RAD(6), ink=True,
                on_click=(lambda e, s=s: _run(page, draw_route, s["lat"], s["lon"],
                                              "pedestrian", "#3fb950")),
                content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                                   ft.Icon(c["icon"], size=14, color=c["color"]),
                                   ft.Text(s["name"], size=12, color=p["text"], expand=True,
                                           max_lines=1,
                                           overflow=ft.TextOverflow.ELLIPSIS),
                                   sub(f"{W.miles(s['dist_m']):.1f}mi · "
                                       f"{W.walk_minutes(s['dist_m'])}m", 10)])))
        spot_list.controls = rows or [sub("No spots yet.", 11)]

    def paint_modes():
        def mode_chip(label, icon, active, on_click, suggested=False):
            colour = p["accent"] if active else (p["yellow"] if suggested else p["text2"])
            return ft.Container(
                padding=PAD(4, 10), border_radius=RAD(20), ink=True, on_click=on_click,
                bgcolor=ft.Colors.with_opacity(0.18 if active else 0.06, colour),
                border=BRD(ft.Colors.with_opacity(0.5 if active or suggested else 0.25,
                                                  colour), 1),
                content=ft.Row(spacing=5, tight=True, controls=[
                    ft.Icon(icon, size=13, color=colour),
                    ft.Text(label, size=11, color=colour, weight=ft.FontWeight.W_500)]))

        s = _S["state"] or {}
        suggested, _why = W.suggest(_num(s.get("pain")), _num(s.get("spoons")), wx_cur)

        def pick(key):
            def handler(e):
                _S["mode"] = key
                _S["sel_cats"] = set()
                _S["cats_open"] = False
                cats_row.visible = False
                paint_modes()
                _run(page, load_pois)
            return handler

        def all_pois(e):
            _S["mode"] = "all"
            _S["cats_open"] = True
            cats_row.visible = True
            every = {c["id"] for c in W.POI_CATS}
            _S["sel_cats"] = set() if _S["sel_cats"] == every else every
            paint_modes()
            paint_cats()
            _run(page, load_pois)

        controls = [mode_chip(m["label"], m["icon"], _S["mode"] == k, pick(k),
                              suggested=(k == suggested))
                    for k, m in W.MODES.items()]
        controls.append(mode_chip(
            "All POIs", ft.Icons.GRID_VIEW,
            _S["mode"] == "all" and _S["sel_cats"] == {c["id"] for c in W.POI_CATS},
            all_pois))
        mode_row.controls = controls

    def paint_cats():
        def toggle_cat(cid):
            def handler(e):
                if _S["mode"] != "all":
                    _S["mode"] = "all"
                    _S["sel_cats"] = set()
                _S["sel_cats"] ^= {cid}
                paint_modes()
                paint_cats()
                _run(page, load_pois)
            return handler

        chips = []
        for c in W.POI_CATS:
            on = c["id"] in _S["sel_cats"]
            chips.append(ft.Container(
                padding=PAD(3, 8), border_radius=RAD(20), ink=True, on_click=toggle_cat(c["id"]),
                bgcolor=ft.Colors.with_opacity(0.22 if on else 0.05, c["color"]),
                border=BRD(ft.Colors.with_opacity(0.6 if on else 0.2, c["color"]), 1),
                content=ft.Row(spacing=4, tight=True, controls=[
                    ft.Icon(c["icon"], size=12, color=c["color"]),
                    ft.Text(c["label"], size=10, color=c["color"])])))
        cats_row.controls = chips

    base_bar = ft.Row(spacing=6, controls=[])

    def paint_base_bar():
        def pick(key):
            def handler(e):
                _S["basemap"] = key
                if MAPS_AVAILABLE:
                    tile_layer.url_template = W.BASEMAPS[key][1]
                paint_base_bar()
                repaint()
            return handler

        out = []
        for key, (label, _url) in W.BASEMAPS.items():
            on = _S["basemap"] == key
            out.append(ft.Container(
                padding=PAD(3, 9), border_radius=RAD(7), ink=True, on_click=pick(key),
                bgcolor=ft.Colors.with_opacity(0.2 if on else 0.05, p["accent"]),
                border=BRD(ft.Colors.with_opacity(0.5 if on else 0.2, p["accent"]), 1),
                content=ft.Text(label, size=10,
                                color=p["accent"] if on else p["text2"])))
        out.append(ft.Container(expand=True))
        out.append(ft.Container(
            padding=PAD(3, 9), border_radius=RAD(7), ink=True,
            on_click=lambda e: _run(page, toggle_notes),
            bgcolor=ft.Colors.with_opacity(0.2 if _S["notes_on"] else 0.05, p["yellow"]),
            border=BRD(ft.Colors.with_opacity(
                0.5 if _S["notes_on"] else 0.2, p["yellow"]), 1),
            content=ft.Row(spacing=4, tight=True, controls=[
                ft.Icon(ft.Icons.STICKY_NOTE_2, size=12, color=p["yellow"]),
                ft.Text("OSM Notes", size=10, color=p["yellow"])])))
        base_bar.controls = out

    async def toggle_notes():
        if _S["notes_on"]:
            _S["notes_on"] = False
            _S["notes"] = []
            paint_base_bar()
            paint_markers()
            say(True, "")
            repaint()
            return
        centre = W.origin()
        if MAPS_AVAILABLE:
            try:
                cam = await the_map.get_camera()
                centre = (cam.center.latitude, cam.center.longitude)
            except Exception:      # noqa: BLE001 - map not mounted yet
                pass
        # The map reports a centre and a zoom but not a viewport, so the bbox
        # is the current POI radius around the centre rather than exactly what
        # is on screen. Same notes, a slightly wider window.
        r = max(radius(), 2000) / 1000.0
        dlat = r / 111.0
        dlon = r / max(0.1, 111.0 * math.cos(math.radians(centre[0])))
        bbox = (f"{centre[0] - dlat:.5f},{centre[1] - dlon:.5f},"
                f"{centre[0] + dlat:.5f},{centre[1] + dlon:.5f}")
        status.value = "loading OSM notes..."
        repaint()
        notes = await asyncio.to_thread(W.osm_notes, bbox)
        if notes is None:
            say(False, "OSM notes failed")
            repaint()
            return
        _S["notes"] = notes
        _S["notes_on"] = True
        paint_base_bar()
        paint_markers()
        say(True, f"{len(notes)} OSM notes in this area")
        repaint()

    # ------------------------------------------------------------- mission
    mission_box = ft.Column(spacing=6, visible=False)
    mission_note = ft.Text("no idea what to do? let the dice decide", size=11,
                           color=p["text2"])
    suggest_chip = ft.Row(spacing=5, tight=True, controls=[])

    def steering():
        """The two numbers the suggestion and the mission roll are allowed.

        (None, None) when Settings > Pain & spoons has turned steering off,
        which makes both fall back to the weather alone - the numbers are
        still recorded, they just stop deciding anything. One place, so the
        mode chip and the dice can never disagree about what today is.
        """
        if not wprefs.vitals_steer():
            return None, None
        s = _S["state"] or {}
        return _num(s.get("pain")), _num(s.get("spoons"))

    def paint_suggest():
        mode, why = W.suggest(*steering(), wx_cur)
        label = W.MODES[mode]["label"] + (f" · {why}" if why else "")
        suggest_chip.controls = [ft.Icon(ft.Icons.LIGHTBULB, size=13, color=p["yellow"]),
                                 ft.Text(label, size=11, color=p["yellow"])]
        paint_modes()

    def paint_mission():
        m = _S["mission"]
        if not m:
            mission_box.visible = False
            mission_box.controls = []
            return
        v = m["venue"]
        c = W.CAT_BY_ID.get(v["cat"], {"label": v["cat"], "icon": ft.Icons.PLACE,
                                       "color": p["accent"]})
        mission_box.visible = True
        mission_box.controls = [
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Icon(c["icon"], size=18, color=c["color"]),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(v["name"], size=13, weight=ft.FontWeight.W_600, color=p["text"]),
                    sub(f"{c['label']} · {W.miles(v['dist_m']):.1f} mi · ~"
                        f"{W.walk_minutes(v['dist_m'])} min walk", 10)]),
                ft.IconButton(ft.Icons.MAP, icon_size=17, icon_color=p["accent"],
                              tooltip="Show it and walk there",
                              on_click=lambda e: _run(page, mission_go)),
                ft.IconButton(ft.Icons.CASINO, icon_size=17, icon_color=p["text2"],
                              tooltip="Different mission",
                              on_click=lambda e: _run(page, roll)),
            ]),
            ft.Text("Your mission: go there and photograph —", size=12, color=p["text"]),
            ft.Row(spacing=6, wrap=True, controls=[
                chip(s, p["purple"], small=True) for s in m["subjects"]]),
            ft.Row(spacing=6, controls=[
                ft.Icon(ft.Icons.LOCK, size=12, color=p["orange"]),
                ft.Text(m["constraint"], size=11, color=p["orange"])]),
            sub(m["why"], 10),
        ]

    async def roll():
        ctx = W.mission_context(*steering(), wx_cur)
        mission_note.value = "rolling the dice..."
        repaint()
        venues = _S["venues"]
        dropped = []
        if not venues:
            wanted = (W.VENUE_CATS["indoor"] if ctx["indoor"]
                      else W.VENUE_CATS["outdoor"] + W.VENUE_CATS["indoor"])
            venues, dropped = await asyncio.to_thread(
                W.overpass, [c for c in W.POI_CATS if c["id"] in wanted], ctx["radius"])
            if venues is None:
                mission_note.value = "mission roll failed - Overpass did not answer."
                _S["mission_note"] = mission_note.value
                repaint()
                return
            if dropped:
                mission_note.value = ("rolling from a short list - Overpass dropped "
                                      + ", ".join(W.CAT_BY_ID[i]["label"] for i in dropped))
            _S["venues"] = venues
        venue = W.pick_venue(venues, ctx)
        if not venue:
            # Same distinction the POI scan draws: an Overpass that answers
            # with no elements at all is describing its own database, not
            # the neighbourhood, and "try a wider day" sends you looking for
            # a problem that is not yours.
            mission_note.value = W.empty_scan_note(venues, dropped)
            _S["mission_note"] = mission_note.value
            _S["mission"] = None
            paint_mission()
            repaint()
            return
        if not _S["deck"]:
            deck = await asyncio.to_thread(W.missions)
            _S["deck"] = deck or []
        _S["mission"] = W.roll_mission(venue, _S["deck"], ctx, random)
        if not mission_note.value.startswith("rolling from a short list"):
            mission_note.value = ""
        _S["mission_note"] = mission_note.value
        paint_mission()
        paint_markers()
        repaint()

    async def mission_go():
        m = _S["mission"]
        if not m:
            return
        v = m["venue"]
        await draw_route(v["lat"], v["lon"], "pedestrian", "#ff2d95")

    # ------------------------------------------------------------- dialogs
    def _show(dlg):
        opener = getattr(page, "show_dialog", None) or getattr(page, "open", None)
        if callable(opener):
            opener(dlg)

    def _dismiss(dlg):
        closer = getattr(page, "pop_dialog", None)
        if callable(closer):
            closer()
            return
        closer = getattr(page, "close", None)
        if callable(closer):
            closer(dlg)

    def _editor_dialog(heading, hint, text, on_save):
        field = ft.TextField(value=text, multiline=True, min_lines=10, max_lines=18,
                             text_size=12, dense=True, filled=True,
                             border_color=p["border"], focused_border_color=p["accent"],
                             color=p["text"])
        note = ft.Text("", size=11, color=p["pink"])
        dlg = ft.AlertDialog(
            title=ft.Text(heading, size=14, weight=ft.FontWeight.W_600),
            content=ft.Column(spacing=8, width=420, tight=True,
                              controls=[sub(hint, 10), field, note]),
            actions=[])

        async def save(e):
            note.value = "saving..."
            note.color = p["text2"]
            repaint()
            ok, msg = await on_save(field.value or "")
            if ok:
                _dismiss(dlg)
                say(True, msg)
            else:
                note.value = f"not saved - {msg}"
                note.color = p["pink"]
            repaint()

        dlg.actions = [ft.TextButton("Cancel", on_click=lambda e: _dismiss(dlg)),
                       ft.FilledButton("Save", on_click=save)]
        return dlg

    async def open_template():
        items = await asyncio.to_thread(W.template, True)
        if items is None:
            say(False, "could not load the template")
            repaint()
            return

        async def do_save(text):
            lines = W.parse_template_text(text)
            ok, msg, _saved = await asyncio.to_thread(W.save_template, lines)
            if not ok:
                return False, msg
            got = await asyncio.to_thread(W.state, _S["date"], True)
            if got is not None:
                _S["state"] = got
                paint_checklist()
            return True, msg

        _show(_editor_dialog(
            "Checklist template",
            "One task per line. Start a line with '> ' to make it a sub-task "
            "of the line above.",
            W.template_lines(items), do_save))
        repaint()

    async def open_deck():
        subjects = await asyncio.to_thread(W.missions, True)
        if subjects is None:
            say(False, "could not load the deck")
            repaint()
            return

        async def do_save(text):
            lines = [s.strip() for s in (text or "").split("\n") if s.strip()]
            ok, msg, saved = await asyncio.to_thread(W.save_missions, lines)
            if ok:
                _S["deck"] = saved or lines
            return ok, msg

        _show(_editor_dialog("Mission subject deck", "One photo subject per line.",
                             "\n".join(subjects), do_save))
        repaint()

    # ------------------------------------------------------------ out card
    def out_card():
        controls = [
            ft.Row([title("Get out", 14), ft.Container(expand=True), suggest_chip]),
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.FilledButton("Roll a mission", height=34, icon=ft.Icons.CASINO,
                                on_click=lambda e: _run(page, roll)),
                ft.IconButton(ft.Icons.EDIT_NOTE, icon_color=p["text2"],
                              tooltip="Edit the subject deck",
                              on_click=lambda e: _run(page, open_deck)),
                ft.Container(expand=True),
            ]),
            mission_note, mission_box,
            ft.Divider(height=1, color=p["border"]),
            base_bar, mode_row, cats_row,
        ]
        if MAPS_AVAILABLE:
            controls.append(ft.Container(
                border_radius=RAD(10), clip_behavior=ft.ClipBehavior.HARD_EDGE,
                content=the_map))
        else:
            controls.append(card(sub("Map unavailable - this build has no flet-map.", 11),
                                 pad=10))
        controls += [poi_note, spot_list]
        return card(ft.Column(spacing=8, controls=controls))

    # ---------------------------------------------------------- today card
    today_box = ft.Column(spacing=10)
    today_fresh = ft.Text("", size=10, color=p["text2"])

    def today_rows(heading, rows, colour, empty="nothing listed"):
        out = [ft.Text(heading, size=11, weight=ft.FontWeight.W_600, color=p["text2"])]
        if rows is None:
            out.append(sub("loading...", 11))
            return out
        if not rows:
            # `empty` distinguishes "the server said there is nothing" from
            # "the server never answered". Both show no rows; only one is news.
            out.append(sub(empty, 11))
            return out
        for r in rows:
            out.append(ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                                  ft.Container(width=7, height=7, border_radius=4,
                                               bgcolor=colour),
                                  ft.Text(r["name"], size=12, color=p["text"], expand=True,
                                          max_lines=2),
                                  sub(r.get("meta", ""), 10),
                                  ft.IconButton(
                                      ft.Icons.TWO_WHEELER, icon_size=15,
                                      icon_color=p["orange"], tooltip="Route a ride there",
                                      on_click=(lambda e, r=r: _run(
                                          page, draw_route, r["lat"], r["lon"],
                                          "motorcycle", "#e3b341")))]))
        return out

    def paint_today():
        t = _S["today"] or {}
        today_fresh.value = t.get("fresh", "")
        gone = t.get("gone") or {}
        today_box.controls = [
            ft.Column(spacing=3, controls=today_rows(
                "Free today", t.get("events"), "#4dd0e1",
                gone.get("curated") or "no free events listed for today")),
            ft.Column(spacing=3, controls=today_rows(
                "Thrift near", t.get("thrift"), "#ef5350",
                gone.get("thrift") or "no thrift spots within 15 mi")),
            ft.Column(spacing=3, controls=today_rows(
                "Movie spots near", t.get("movies"), "#ff8fab",
                gone.get("curated") or "no movie spots listed")),
        ]

    async def load_today():
        out = {"events": None, "thrift": None, "movies": None, "fresh": "loading",
               "gone": {}}
        cur = await asyncio.to_thread(W.curated)
        today = W.today_str()
        if cur is None:
            out.update({"events": [], "movies": [], "fresh": "unavailable"})
            out["gone"]["curated"] = "unavailable - the server did not answer"
        else:
            stale = cur.get("events_stale_h")
            out["fresh"] = "stale list" if (stale is None or stale > 30) else "fresh"
            out["events"] = [
                {"name": e.get("name", ""), "meta": e.get("s") or "",
                 "lat": e["lat"], "lon": e["lon"]}
                for e in (cur.get("events") or [])
                if e.get("d") == today and e.get("lat") is not None][:8]
            movies = [(m, W.haversine_m(W.origin(), (m["lat"], m["lon"])))
                      for m in (cur.get("movies") or []) if m.get("lat") is not None]
            movies.sort(key=lambda x: x[1])
            out["movies"] = [{"name": m.get("name", ""),
                              "meta": f"{W.miles(d):.1f}mi",
                              "lat": m["lat"], "lon": m["lon"]}
                             for m, d in movies[:5]]
        _S["today"] = dict(out)
        paint_today()
        repaint()
        els = await asyncio.to_thread(W.thrift_near)
        if els is None:
            out["thrift"] = []
            out["gone"]["thrift"] = "unavailable - the server did not answer"
        else:
            out["thrift"] = [{"name": (e.get("tags") or {}).get("name", "—"),
                              "meta": f"{W.miles(e.get('dist') or 0):.1f}mi",
                              "lat": e["lat"], "lon": e["lon"]} for e in els]
        _S["today"] = dict(out)
        paint_today()
        repaint()

    def today_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row([title("Today", 14), ft.Container(expand=True), today_fresh]),
            today_box]))

    # ------------------------------------------------------------ assemble
    paint_checklist()
    paint_vitals()
    paint_suggest()          # also paints the mode chips
    paint_cats()
    paint_base_bar()
    paint_spots()
    paint_mission()
    paint_markers()
    paint_today()
    cats_row.visible = _S["cats_open"]
    if _S["poi_note"]:
        poi_note.value = _S["poi_note"]
    if _S["mission_note"]:
        mission_note.value = _S["mission_note"]

    if _S["today"] is None:
        _run(page, load_today)
    if not _S["deck"]:
        _run(page, _load_deck)

    builders = {
        "date": date_nav, "checklist": checklist_card, "vitals": vitals_card,
        "weather": weather_card, "out": out_card, "today": today_card,
    }
    # Not tracking pain and spoons means not drawing the card and not writing
    # either number. `panels.layout` skips a key with no builder, so the card
    # keeps its place in her saved order and comes back where she left it the
    # moment she turns tracking on again.
    if not wprefs.track_vitals():
        builders.pop("vitals")
    body = panels.layout("wat2do", builders)
    # The offline banner and the save status are chrome, not panels: they
    # must not be hideable, or a failed write would have nowhere to show.
    return ft.Column(spacing=10, expand=True, controls=[
        *([] if live else [offline_banner(p, "your day", sample=False)]),
        ft.Container(expand=True, content=body),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


async def _load_deck():
    """Pull the mission subject deck in the background; harmless if it fails."""
    deck = await asyncio.to_thread(W.missions)
    if deck:
        _S["deck"] = deck


def _run(page, coro_fn, *args):
    """page.run_task when there is an event loop, otherwise quietly skip.

    Tests and the import check build the page with a stub, where scheduling
    work would either fail or hang; a page that paints its placeholders and
    stops is the honest outcome there.
    """
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return
    try:
        runner(coro_fn, *args)
    except Exception:          # noqa: BLE001 - no running loop
        pass
