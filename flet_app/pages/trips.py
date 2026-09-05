import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs

def build(page):
    return _tabs(page, [("Selekt", _trips()), ("Produce", _produce()), ("Rides", _rides())])

def _trips():
    p = PAL()
    def row(t):
        return card(ft.Column(spacing=5, controls=[
            ft.Row([title(t["name"], 14), ft.Container(expand=True),
                    chip(t["date"], p["cyan"], small=True)]),
            sub(f"{t['mi']} mi", 11),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(t) for t in M.TRIPS])

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
