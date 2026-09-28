"""Where to. Four ways of saying it, in the order they work without signal.

The search box is one box, not four, and it works out what she meant: a
coordinate pair or a shared pin routes immediately, and anything else is looked
up as a street name in the graph on the device. Named places from the online
geocoder are a second button rather than an automatic fallback, because an
automatic one would make the box silently stop working in a canyon and look
like a bug rather than a dead radio.

Underneath: her saved trips and recorded rides from the server, which Washi
serves from this device's cached copy when there is nothing to ask - so a list
she has seen once is a list she still has. Then GPX import.

The destination is set here and the route is computed here, but neither is drawn
here. That is Ride's job, and this page sends her there the moment a route
exists, because standing on a list of candidate destinations after choosing one
is a page that has finished its purpose.
"""
import flet as ft

from .. import guidance, live, places, prefs, ui
from ..theme import PAL

#: What the box is showing, kept across a repaint so a theme change or a
#: returned route does not empty a list she is reading.
_S = {"text": "", "rows": [], "note": "", "gpx": ""}

#: Services that must outlive a repaint, same reason as in ride.py.
_M = {}

_KIND_ICONS = {"street": ft.Icons.SIGNPOST, "place": ft.Icons.PUBLIC,
               "poi": ft.Icons.PLACE, "trip": ft.Icons.MAP,
               "ride": ft.Icons.TWO_WHEELER, "gpx": ft.Icons.ROUTE,
               "point": ft.Icons.MY_LOCATION}


#: How many offline places to show above the matching streets.
PLACE_ROWS = 8


def nothing_found(text, places_total):
    """What the box says when this device has no answer, and why.

    An empty list reads as a broken app; this says whether to try again with
    signal or give up. `places_total` is how many places the offline list
    holds - 0 when none is installed - and is shown exactly, not rounded.
    """
    if not any(len(w) >= places.MIN_WORD for w in places.norm_tokens(text)):
        return "type at least two letters"
    if not places_total:
        return ("no street by that name on this device, and the offline places "
                "list is not installed - try Places if you have signal")
    return (f"not a street, and not one of the {places_total:,} places on this "
            "device - try Places if you have signal")


def _update(page):
    # A failed repaint must leave a trace; `except: pass` here left none.
    try:
        page.update()
    except Exception:                                  # noqa: BLE001
        import traceback
        traceback.print_exc()


async def _to_thread(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)


def _rerender(page):
    rerender = getattr(page, "_navi_rerender", None)
    if callable(rerender):
        rerender()


def build(page, go=None):
    p = PAL()

    box = ft.TextField(
        value=_S["text"], hint_text="street, place, or 37.8044, -122.2712",
        text_size=ui.MID, height=ui.TOUCH, border_radius=12,
        border_color=p["border"], focused_border_color=p["accent"],
        color=p["text"], bgcolor=p["surface"], expand=True,
        on_submit=lambda e: _search(e))
    note = ft.Text(_S["note"], size=ui.SMALL, color=p["text2"])
    results = ft.Column(spacing=8, tight=True)
    saved = ft.Column(spacing=8, tight=True)

    def _choose(row):
        """Aim at a row and route to it, off the UI thread."""
        def handler(_e=None):
            live.set_destination(row["lat"], row["lon"], row.get("name", ""))
            note.value = _S["note"] = (
                f"routing to {row.get('name') or 'there'}…")
            _update(page)
            page.run_task(_route)
        return handler

    async def _route():
        route = await live.route_async(allow_server=False)
        if route is not None and route.ok:
            note.value = _S["note"] = route.summary(
                "imperial" if prefs.imperial() else "metric")
            _update(page)
            if callable(go):
                go("ride")
            return
        # A refusal names its reason: "no graph installed" and "that point is
        # outside the mapped area" are different problems with different fixes.
        note.value = _S["note"] = live.LIVE.get("note") or "no route"
        _update(page)

    def _row(row, on_click=None):
        pal = PAL()
        bits = [row.get("detail") or ""]
        if row.get("dist_m") is not None:
            bits.insert(0, guidance.distance_text(row["dist_m"],
                                                  prefs.imperial()))
        if row.get("outside"):
            # Said first and in the warning colour: tapping it will be refused.
            bits.insert(0, places.OUTSIDE_MAP)
        return ui.row_tap(ft.Row(spacing=10, controls=[
            ft.Icon(_KIND_ICONS.get(row.get("kind"), ft.Icons.PLACE),
                    size=20, color=pal["accent"]),
            ft.Column(spacing=0, expand=True, tight=True, controls=[
                ft.Text(row.get("name") or "—", size=ui.MID,
                        weight=ft.FontWeight.W_700, color=pal["text"],
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(" · ".join(x for x in bits if x), size=ui.SMALL,
                        color=pal["warn"] if row.get("outside") else pal["text2"])]),
            ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20, color=pal["text3"]),
        ]), on_click or _choose(row), height=ui.TOUCH)

    def _paint_results():
        results.controls = ([_row(r) for r in _S["rows"]] if _S["rows"]
                            else [ui.empty("nothing yet", ft.Icons.SEARCH)])

    async def _search_async():
        """A coordinate routes at once; anything else searches the graph.

        The street search runs on a WORKER THREAD. It reads the road graph, and
        `routing.graph()` starts an 86 MB load on its way past - doing that in a
        tap handler freezes the whole app until the read finishes, which on a
        phone is long enough to look like a crash.
        """
        _S["text"] = box.value or ""
        point = places.parse_point(_S["text"])
        if point is not None:
            _choose({"name": _S["text"].strip(), "lat": point[0],
                     "lon": point[1], "kind": "point", "detail": point[2]})()
            return
        note.value = "searching this device…"
        _update(page)
        here = live.LIVE.get("pos")
        # Named places first (a museum, a park, a fuel station - the offline
        # list beside the graph), then streets. The graph alone holds only
        # streets, which is why "Chabot Space Science Center" found nothing.
        found, total = await _to_thread(places.search_places, _S["text"], here,
                                        PLACE_ROWS)
        found = await _to_thread(places.mark_outside, found)
        streets = await _to_thread(places.search_streets, _S["text"], None,
                                   12 - len(found), here)
        _S["rows"] = found + streets
        note.value = _S["note"] = (
            "" if _S["rows"] else nothing_found(_S["text"], total))
        _paint_results()
        _update(page)

    def _search(_e=None):
        page.run_task(_search_async)

    async def _search_online():
        note.value = "asking the geocoder…"
        _update(page)
        rows, err = await _to_thread(places.search_named, box.value or "",
                                    live.LIVE.get("pos"))
        _S["rows"] = rows
        note.value = _S["note"] = err or (
            "" if rows else "the geocoder knows nowhere by that name")
        _paint_results()
        _update(page)

    def _open_saved(row):
        """Load a saved trip or ride and ride it.

        This used to answer that the points were not on the device - which was
        true and was the wrong answer. A trip's waypoints now ride along with the
        cached trip list, and a ride's points come through the store like every
        other read, so both work offline once they have been seen.

        The END of the route becomes the destination and the whole shape is drawn
        as the track, so she can see the route she asked for beside the line the
        router is giving her from wherever she actually is now.
        """
        async def run():
            note.value = f"loading {row['name']}…"
            _update(page)
            points = await _to_thread(places.saved_points, row)
            if not points:
                note.value = _S["note"] = (
                    f"{row['name']}: no points for that on this device yet - "
                    "open it once with signal and it is kept from then on")
                _update(page)
                return
            live.LIVE["track"] = list(points)
            last = points[-1]
            _choose({"name": row["name"], "lat": last[0], "lon": last[1],
                     "kind": row.get("kind", "trip"),
                     "detail": f"{len(points)} points"})()
        return lambda e=None: page.run_task(run)

    async def _load_saved():
        rows, why = await _to_thread(places.db_routes)
        if not rows:
            saved.controls = [ui.empty(why or "no saved routes", ft.Icons.ROUTE)]
        else:
            saved.controls = [_row(row, _open_saved(row)) for row in rows]
            if why:
                saved.controls.insert(
                    0, ui.chip(why, PAL()["warn"], ft.Icons.CLOUD_OFF))
        _update(page)

    # ----------------------------------------------------------- GPX import
    gpx_box = ft.TextField(
        value=_S["gpx"], hint_text="paste GPX here", multiline=True,
        min_lines=3, max_lines=6, text_size=ui.SMALL, border_radius=12,
        border_color=p["border"], focused_border_color=p["accent"],
        color=p["text"], bgcolor=p["surface"])
    gpx_note = ft.Text("", size=ui.SMALL, color=p["text2"])

    picker = _M.get("picker")
    if picker is None:
        picker = ft.FilePicker()
        _M["picker"] = picker
        if isinstance(getattr(page, "services", None), list):
            page.services.append(picker)

    def _use_points(name, points):
        if not points:
            gpx_note.value = "no points in that - is it GPX?"
            gpx_note.color = PAL()["bad"]
            _update(page)
            return
        # The END of an imported track is the destination: importing a ride
        # means "take me along this", and the router's own line from here to
        # there is what can actually be followed with live guidance. The
        # imported points are drawn as the track so she can see the shape she
        # asked for beside the line she is being given.
        last = points[-1]
        gpx_note.value = f"{name or 'imported'} - {len(points)} points"
        gpx_note.color = PAL()["good"]
        live.LIVE["track"] = list(points)
        _choose({"name": name or "imported route", "lat": last[0],
                 "lon": last[1], "kind": "gpx"})()

    def _import_text(_e=None):
        _S["gpx"] = gpx_box.value or ""
        _use_points(*places.parse_gpx(_S["gpx"]))

    async def _pick_file(_e=None):
        try:
            files = await picker.pick_files(
                dialog_title="Choose a GPX file",
                allowed_extensions=["gpx", "xml"], allow_multiple=False)
        except Exception as exc:                        # noqa: BLE001
            gpx_note.value = f"could not open the picker: {exc}"
            _update(page)
            return
        chosen = (files or [None])[0]
        path = getattr(chosen, "path", None) if chosen else None
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            gpx_note.value = f"could not read it: {exc}"
            _update(page)
            return
        _use_points(*places.parse_gpx(text))

    _paint_results()
    page.run_task(_load_saved)

    def _clear(_e=None):
        live.clear_destination()
        _rerender(page)

    header = []
    dest = live.destination()
    if dest is not None:
        header.append(ui.card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.FLAG, size=20, color=p["warn"]),
            ft.Column(spacing=0, expand=True, tight=True, controls=[
                ft.Text(dest[2] or f"{dest[0]:.4f}, {dest[1]:.4f}",
                        size=ui.MID, weight=ft.FontWeight.W_700,
                        color=p["text"], max_lines=1),
                ft.Text("current destination", size=ui.SMALL,
                        color=p["text2"])]),
            ui.icon_button(ft.Icons.CLOSE, _clear, "Clear the destination"),
        ])))

    # Whether the offline places list is on this phone, and how many places it
    # really holds - stated BEFORE any search, so a missing list can never pass
    # for "nothing by that name". Counted, not claimed; see places_status.
    places_count, places_words = places.places_status()
    places_line = ft.Text(places_words, size=ui.SMALL,
                          color=p["text2"] if places_count else p["warn"])

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        *header,
        ft.Row(spacing=8, controls=[
            box,
            ui.icon_button(ft.Icons.SEARCH, _search, "Search this device")]),
        places_line,
        ui.button("Places (needs signal)",
                  lambda e: page.run_task(_search_online),
                  icon=ft.Icons.PUBLIC, expand=True),
        note,
        results,
        ft.Container(height=4),
        ft.Text("Saved trips and rides", size=ui.MID,
                weight=ft.FontWeight.W_800, color=p["text"]),
        saved,
        ft.Container(height=4),
        ft.Text("Import a GPX", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        gpx_box,
        ft.Row(spacing=8, controls=[
            ui.button("Open a file", lambda e: page.run_task(_pick_file),
                      icon=ft.Icons.FOLDER_OPEN, expand=True),
            ui.button("Use pasted", _import_text, icon=ft.Icons.CONTENT_PASTE,
                      expand=True)]),
        gpx_note,
        ft.Container(height=12),
    ])
