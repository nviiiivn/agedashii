import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M

def build(page):
    p = PAL()
    w = M.WEATHER["now"]

    def weather_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row([
                ft.Text(f"{w['t']}°", size=34, weight=ft.FontWeight.W_700, color=p["text"]),
                ft.Icon(ft.Icons.CLOUD, size=30, color=p["yellow"]),
                ft.Container(expand=True),
                ft.Column(spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END, controls=[
                    ft.Text("Oakland", size=12, color=p["text2"]),
                    ft.Text(f"H{w['h']}° / L{w['l']}°", size=12, color=p["text2"]),
                ]),
            ]),
            ft.Row(spacing=6, controls=[
                chip("Ride weather good", p["green"], ft.Icons.MOTORCYCLE, small=True),
                chip(f"Wind {w['wind']}mph", p["cyan"], ft.Icons.AIR, small=True),
                chip(f"{w['rain']}% rain", p["orange"], ft.Icons.WATER_DROP, small=True),
            ]),
        ]))

    def nowplaying_card():
        n = M.NOWPLAYING
        return card(ft.Row(spacing=12, controls=[
            ft.Container(width=44, height=44, border_radius=8, bgcolor=p["bg2"],
                         border=BRD(p["border"], 1), alignment=ft.Alignment(0, 0),
                         content=ft.Icon(ft.Icons.MUSIC_NOTE, color=p["pink"], size=22)),
            ft.Column(spacing=2, expand=True, controls=[
                ft.Text(n["title"], size=14, weight=ft.FontWeight.W_600, color=p["text"]),
                sub(n["artist"]),
            ]),
            ft.Icon(ft.Icons.PLAY_CIRCLE, color=p["accent"], size=30),
        ]))

    def machines_card():
        return card(ft.Column(spacing=10, controls=[
            sub("MACHINES"),
            ft.Row(spacing=8, wrap=False, controls=[
                ft.Container(border_radius=10, bgcolor=p["bg2"], border=BRD(p["border"], 1),
                             padding=PAD(8, 10),
                             content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                                 dot(m["color"], dim=not m["online"]),
                                 ft.Text(m["display"], size=10,
                                         color=p["text"] if m["online"] else p["text2"]),
                             ])) for m in M.MACHINES
            ]),
        ]))

    def actions_row():
        acts = [("Wake", ft.Icons.BOLT, p["yellow"]), ("Ride", ft.Icons.MOTORCYCLE, p["green"]),
                ("Wat2Do", ft.Icons.CHECK_CIRCLE, p["accent"]), ("List", ft.Icons.LIST, p["purple"])]
        return ft.Row(spacing=8, expand=True, controls=[
            ft.Container(expand=True, border_radius=10, bgcolor=p["surface"], border=BRD(p["border"], 1),
                         padding=PAD(10, 0), alignment=ft.Alignment(0, 0),
                         content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                             ft.Icon(i, color=c, size=20), ft.Text(t, size=11, color=p["text2"])]))
            for t, i, c in acts
        ])

    done = sum(1 for it in M.WAT2DO["items"] if it.get("done"))
    total = len(M.WAT2DO["items"])

    def wat2do_mini():
        def row(it):
            return ft.Row(spacing=8, controls=[
                ft.Icon(ft.Icons.CHECK_BOX if it.get("done") else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                        size=16, color=p["green"] if it.get("done") else p["text2"]),
                ft.Text(it["t"], size=12 if not it.get("sub") else 11,
                        color=p["text2"] if it.get("done") else (p["text3"] if it.get("sub") else p["text"]),
                        style=None if not it.get("done") else ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH),
                        offset=ft.Offset(0.6, 0) if it.get("sub") else None),
            ])
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Wat2Do"), ft.Container(expand=True),
                    ft.Text(f"{done}/{total}", size=12, color=p["accent"], weight=ft.FontWeight.W_600)]),
            ft.ProgressBar(value=(done / total) if total else 0, color=p["orange"], bgcolor=p["bg2"], bar_height=4, border_radius=2),
            *[row(it) for it in M.WAT2DO["items"][:4]],
        ]))

    return ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Column(spacing=0, controls=[
            ft.Text("Good evening, nvii", size=22, weight=ft.FontWeight.W_700, color=p["text"]),
            sub("Fri, Sep 4 · Vulcan Lofts"),
        ]),
        weather_card(), nowplaying_card(), machines_card(), actions_row(), wat2do_mini(),
    ])
