"""What is around here - the Watudo idea, narrowed to what a rider wants.

Her ask: "WATUDO VIEW OF POI AS 'SEARCH' FEATURE TO FIND NEW
DESTINATION/LOCATIONS IN AREAS."

Eight categories, chosen for a motorcycle rather than for a day out: fuel,
coffee, food, water, toilets, a viewpoint worth stopping at, somewhere to camp,
and a bike shop. Multi-select, because "fuel or coffee" is one question.

This is the one surface in the app that cannot work without signal, and it says
so on the button rather than after the tap. Overpass is a live third party with
three public mirrors, two of which are usually busy - `places.search_pois` tries
them in turn and only reports a failure once every one has refused.

The radius is a row of four fixed distances instead of a slider: a slider needs
a fingernail, and the difference between five and six miles has never mattered
to anyone looking for petrol.
"""
import traceback

import flet as ft

from .. import guidance, live, places, prefs, ui
from ..theme import BRD, PAL, RAD

#: Offered radii, as (miles shown, metres asked for).
RADII = ((2, 3200), (5, 8000), (10, 16000), (25, 40000))

#: Survives a repaint so a theme change does not lose her results.
_S = {"picked": {"Fuel"}, "radius_m": 8000, "rows": [], "note": ""}


def _update(page):
    # A repaint that fails must leave a trace in logcat. `except: pass` here
    # hid every failure on this page; the grey box left none.
    try:
        page.update()
    except Exception:                                  # noqa: BLE001
        traceback.print_exc()


def _rerender(page):
    rerender = getattr(page, "_navi_rerender", None)
    if callable(rerender):
        rerender()


def _pick_tile(text, on_click, chosen, expand=None, radius=12, height=44):
    """A filled-when-chosen tap target, the only control shape on this page.

    `expand` defaults to None - Flet's own default - never False. The category
    row is a `Row(wrap=True)`, and a Wrap child with `expand` set AT ALL, False
    included, fails in Flutter, which a release build paints as a flat grey
    box. That was the whole Around screen: "a giant grey box".
    test_shell.NothingFlexesInsideAWrap holds the line.
    """
    p = PAL()
    return ft.Container(
        on_click=on_click, ink=True, height=height, expand=expand,
        padding=ft.Padding(left=14, top=0, right=14, bottom=0),
        border_radius=RAD(radius), alignment=ft.Alignment.CENTER,
        bgcolor=p["accent"] if chosen else p["surface"],
        border=None if chosen else BRD(p["border"], 1),
        content=ft.Text(text, size=ui.SMALL, weight=ft.FontWeight.W_700,
                        color=p["bg"] if chosen else p["text"]))


def _chip(text, on_click, chosen):
    """One category in the three-across grid: a pill, glove-target tall."""
    tile = _pick_tile(text, on_click, chosen, radius=22, height=ui.TOUCH)
    tile.col = 4
    return tile


def build(page, go=None):
    p = PAL()
    note = ft.Text(_S["note"], size=ui.SMALL, color=p["text2"])
    results = ft.Column(spacing=8, tight=True)

    def _toggle(label):
        def handler(_e=None):
            if label in _S["picked"]:
                _S["picked"].discard(label)
            else:
                _S["picked"].add(label)
            _rerender(page)
        return handler

    def _set_radius(metres):
        def handler(_e=None):
            _S["radius_m"] = metres
            _rerender(page)
        return handler

    def _choose(row):
        def handler(_e=None):
            live.set_destination(row["lat"], row["lon"], row.get("name", ""))
            note.value = _S["note"] = f"routing to {row.get('name')}…"
            _update(page)
            page.run_task(_route)
        return handler

    async def _route():
        route = await live.route_async(allow_server=False)
        if route is not None and route.ok:
            if callable(go):
                go("ride")
            return
        note.value = _S["note"] = live.LIVE.get("note") or "no route"
        _update(page)

    def _paint():
        pal = PAL()
        if not _S["rows"]:
            results.controls = [ui.empty("nothing found yet",
                                         ft.Icons.TRAVEL_EXPLORE)]
            return
        results.controls = [ui.row_tap(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.PLACE, size=20, color=pal["accent"]),
            ft.Column(spacing=0, expand=True, tight=True, controls=[
                ft.Text(row["name"], size=ui.MID, weight=ft.FontWeight.W_700,
                        color=pal["text"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(" · ".join(x for x in (
                    guidance.distance_text(row["dist_m"], prefs.imperial()),
                    row.get("detail") or "") if x),
                        size=ui.SMALL, color=pal["text2"])]),
            ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20, color=pal["text3"]),
        ]), _choose(row), height=ui.TOUCH) for row in _S["rows"]]

    async def _look():
        here = live.LIVE.get("pos")
        if here is None:
            note.value = _S["note"] = "no fix yet - nothing to search around"
            _update(page)
            return
        note.value = "asking Overpass…"
        _update(page)
        import asyncio
        rows, err = await asyncio.to_thread(
            places.search_pois, sorted(_S["picked"]), here[0], here[1],
            _S["radius_m"])
        _S["rows"] = rows
        note.value = _S["note"] = err or (
            f"{len(rows)} within "
            f"{guidance.distance_text(_S['radius_m'], prefs.imperial())}"
            if rows else "nothing of that kind nearby")
        _paint()
        _update(page)

    _paint()

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Text("What is around here", size=ui.MID,
                weight=ft.FontWeight.W_800, color=p["text"]),
        # A grid, three across (col 4 of 12), each chip the glove target tall.
        # This used to be a wrapping Row; with `expand` fixed, centred chips in
        # a Wrap each take a whole line and stack eight deep.
        ft.ResponsiveRow(spacing=8, run_spacing=8, controls=[
            _chip(label, _toggle(label), label in _S["picked"])
            for label, _filter in places.POI_CATEGORIES]),
        ft.Row(spacing=8, controls=[
            _pick_tile(f"{miles} mi", _set_radius(metres),
                       _S["radius_m"] == metres, expand=True, height=ui.TOUCH)
            for miles, metres in RADII]),
        ui.button("Look (needs signal)", lambda e: page.run_task(_look),
                  icon=ft.Icons.TRAVEL_EXPLORE, primary=True, expand=True),
        note,
        results,
        ft.Container(height=12),
    ])
