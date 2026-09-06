import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs

def build(page):
    return _tabs(page, [("Home", _bikes()), ("Mechanic", _mechanic()), ("Flipper", _flipper())])

def _bikes():
    p = PAL()
    def bike(b):
        return card(ft.Column(spacing=8, controls=[
            ft.Row([
                ft.Container(width=10, height=38, border_radius=4, bgcolor=b["color"]),
                ft.Column(spacing=1, controls=[
                    title(b["name"], 15),
                    sub(f"{b['year']} {b['make']} · {b['status']}", 11),
                ]),
                ft.Container(expand=True),
                chip(b["status"],
                     p["green"] if b["status"]=="running" else p["yellow"], small=True),
            ]),
            ft.Divider(height=1, color=p["border"]),
            ft.Row(spacing=16, controls=[
                ft.Column(spacing=1, controls=[sub("total mi", 10),
                    ft.Text(f"{b['total_mi']:,}", size=15, weight=ft.FontWeight.W_700, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("since svc", 10),
                    ft.Text(f"{b['mi_since_svc']}", size=15, weight=ft.FontWeight.W_700,
                            color=b["color"] if b["mi_since_svc"] < 800 else p["red"])]),
            ]),
            ft.ProgressBar(value=min(1.0, b["mi_since_svc"] / 1000), color=b["color"],
                           bgcolor=p["bg2"], bar_height=4, border_radius=2),
        ]))
    return ft.Column(spacing=10, controls=[bike(b) for b in M.BIKES])

def _mechanic():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=6, controls=[
            title("Mechanic", 14),
            sub("Service records, parts lookups, maintenance schedule — coming in the next pass", 11),
        ]), pad=20),
    ])

def _flipper():
    p = PAL()
    def deal(d):
        c = p["green"] if d["flag"] == "BUY" else (p["yellow"] if d["flag"] == "WATCH" else p["red"])
        return card(ft.Column(spacing=5, controls=[
            ft.Row([title(d["title"], 13), ft.Container(expand=True), chip(("BUY" if d["score"]>=80 else "WATCH"), c, small=True)]),
            ft.Row(spacing=14, controls=[
                ft.Column(spacing=1, controls=[sub("ask", 10), ft.Text(f"${d['price']:,}", size=13, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("net", 10), ft.Text(f"${d['net']:,}", size=13, color=c)]),
                ft.Column(spacing=1, controls=[sub("score", 10), ft.Text(str(d["score"]), size=13, color=c)]),
            ]),
            sub(d["flag"], 10),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[
        ft.Row([chip(f"{len(M.FLIPPER)} sample deals", p["orange"], ft.Icons.SEARCH, small=True)]),
        *[deal(d) for d in M.FLIPPER],
    ])
