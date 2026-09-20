import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs
from ._forms import Writer, offline_banner, rebuilder

def build(page):
    return _tabs(page, [("Selekt", _trips(page)), ("Produce", _produce()), ("Rides", _rides())])

def _trips(page):
    """Trip list, plus creating one.

    Only creation is offered here. A trip's real content is its stops and
    route, which are built on the big map on the website; inventing a
    stripped-down stop editor on a phone would produce trips the website
    renders as empty. Creating the trip and filling it in there is honest
    about which tool does what.
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
        *([card(ft.Column(spacing=8, controls=[
            title("New trip", 14),
            w.entry("trip name...", make),
            sub("Creates the trip; add stops and route on the website's map.", 10),
        ]))] if live else []),
        *[row(t) for t in (M.TRIPS or [])],
        w.control,
    ])

def _produce():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=5, controls=[
            ft.Row([title("Produce runs", 14), ft.Container(expand=True),
                    chip("Lucky · foodmaxx", p["green"], small=True)]),
            sub("Grocery run history + FoodZen handoff — coming in the next pass", 11),
        ]), pad=12),
    ])

def _rides():
    p = PAL()
    def row(r):
        return card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Icon(ft.Icons.MAP, size=16, color=p["accent"]), title(r["name"], 14),
                    ft.Container(expand=True)]),
            ft.Row(spacing=14, controls=[
                ft.Column(spacing=1, controls=[sub("points", 10), ft.Text(str(r["pts"]), size=13, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("miles", 10), ft.Text(str(r["mi"]), size=13, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("top mph", 10), ft.Text(str(r["top"]), size=13, color=p["text"])]),
            ]),
            ft.Row([chip("replay", p["purple"], ft.Icons.PLAY_ARROW, small=True),
                    chip("elevation", p["green"], ft.Icons.TERRAIN, small=True)]),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(r) for r in M.RIDES])
