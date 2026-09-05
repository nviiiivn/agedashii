import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M

def _tabs(page, tabs):
    p = PAL()
    return ft.Tabs(
        length=len(tabs), selected_index=0, animation_duration=150, expand=True,
        content=ft.Column(spacing=0, expand=True, controls=[
            ft.TabBar(
                tabs=[ft.Tab(label=t) for t, _ in tabs],
                indicator_color=p["accent"], divider_color=p["border"],
                label_color=p["accent"], unselected_label_color=p["text2"],
                overlay_color=ft.Colors.with_opacity(0.08, p["accent"]),
            ),
            ft.TabBarView(expand=True, controls=[
                ft.Container(padding=PAD(10, 0), content=c) for _, c in tabs]),
        ]),
    )

def build(page):
    return _tabs(page, [("Machines", _machines()), ("Things", _things()),
                        ("Services", _services()), ("Qbit", _qbit())])

def _machines():
    p = PAL()
    def mc(m):
        rows = [
            ("OS", m["os"]), ("Host", m["host"]),
        ] + ([("Uptime", m["uptime"]), ("CPU", f"{m['cpu']}%"),
              ("RAM", f"{m['ram']}%"), ("Temp", f"{m['temp']}°C"),
              ("Docker", str(m["docker"]))] if m["online"] else [])
        return card(ft.Column(spacing=6, controls=[
            ft.Row([
                dot(m["color"], dim=not m["online"]),
                title(m["display"]),
                ft.Container(expand=True),
                chip("online" if m["online"] else "offline", p["green"] if m["online"] else p["red"], small=True),
            ]),
            ft.Divider(height=1, color=p["border"]),
            ft.Row(spacing=14, wrap=True, controls=[
                ft.Column(spacing=2, controls=[sub(k, 10), ft.Text(v, size=12, color=p["text"])])
                for k, v in rows
            ]),
        ]))
    return ft.Column(spacing=10, controls=[mc(m) for m in M.MACHINES])

def _things():
    p = PAL()
    return ft.Column(spacing=10, alignment=ft.MainAxisAlignment.CENTER, controls=[
        card(ft.Column(spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(ft.Icons.DEVICES_OTHER, size=34, color=p["text2"]),
            sub("IoT things — coming in the next pass", 12),
        ]), pad=30),
    ])

def _services():
    p = PAL()
    svcs = [("Gitea", True), ("Valhalla", True), ("Ollama (local)", False),
            ("Ollama (Tower)", True), ("Caddy (origin)", True), ("qBit (SP3)", False)]
    def row(n, up):
        return ft.Row([
            dot(p["green"] if up else p["red"]),
            ft.Text(n, size=13, color=p["text"]),
            ft.Container(expand=True),
            chip("up" if up else "down", p["green"] if up else p["red"], small=True),
        ])
    return ft.Column(spacing=8, controls=[card(ft.Column(spacing=12, controls=[
        title("LAN services"), *[row(n, u) for n, u in svcs]]))])

def _qbit():
    p = PAL()
    return ft.Column(spacing=10, controls=[card(ft.Column(spacing=8, controls=[
        ft.Row([title("Torrents"), ft.Container(expand=True), chip("3 down · 2 seed", p["green"], small=True)]),
        ft.ProgressBar(value=0.62, color=p["accent"], bgcolor=p["bg2"], bar_height=4, border_radius=2),
        sub("Fedora 42 ISO — 62% · 3.1 MB/s", 11),
        sub("ubuntu-mate-24.04 — queued", 11),
        sub("moto-docs-pack — seeding", 11),
    ]))])
