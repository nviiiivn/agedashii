import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs

def build(page):
    return _tabs(page, [("Bay", _bay()), ("Explore", _explore())])

def _bay():
    p = PAL()
    w = M.WEATHER["now"]
    days = M.WEATHER["days"]
    def day(d):
        return card(ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text(d["d"], size=12, color=p["text2"]),
            ft.Icon(ft.Icons.CLOUD, size=22, color=p["yellow"]),
            ft.Text(f"{d['t']}°", size=13, weight=ft.FontWeight.W_600, color=p["text"]),
        ]), pad=8)
    return ft.Column(spacing=10, controls=[
        card(ft.Column(spacing=6, controls=[
            ft.Row([ft.Text(f"{w['t']}°", size=40, weight=ft.FontWeight.W_700, color=p["text"]),
                    ft.Icon(ft.Icons.CLOUD, size=34, color=p["yellow"])]),
            sub(f"{w['desc']} · Oakland · wind {w['wind']} mph · {w['rain']}% rain"),
            ft.Row(spacing=6, controls=[chip("ride: good", p["green"], ft.Icons.MOTORCYCLE, small=True),
                                        chip("air quality 31", p["cyan"], ft.Icons.AIR, small=True)]),
        ])),
        ft.Row(spacing=8, expand=True, controls=[day(d) for d in days]),
    ])

def _explore():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("Explore map", 14), ft.Container(expand=True),
                    chip("Overpass POIs", p["orange"], small=True)]),
            ft.Container(height=160, border_radius=10, bgcolor=p["bg2"], border=BRD(p["border"], 1),
                         alignment=ft.Alignment(0, 0),
                         content=ft.Icon(ft.Icons.MAP_OUTLINED, size=44, color=p["text2"])),
            sub("Live map + POI picker lands with API wiring", 11),
        ])),
    ])
