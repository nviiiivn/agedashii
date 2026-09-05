import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs

def build(page):
    return _tabs(page, [("Feeds", _feeds()), ("Vault", _vault()),
                        ("Journal", _journal()), ("FoodZen", _foodzen())])

def _feeds():
    p = PAL()
    def row(it):
        c = {"riders-share": p["orange"], "cyclefish": p["cyan"], "lightning": p["red"],
             "sf-mc": p["purple"]}.get(it["src"], p["accent"])
        return card(ft.Column(spacing=5, controls=[
            ft.Row([chip(it["tag"], c, small=True), ft.Container(expand=True), sub(it["meta"], 10)]),
            ft.Text(it["title"], size=13, weight=ft.FontWeight.W_500, color=p["text"]),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(i) for i in M.FEEDS])

def _vault():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.FOLDER, color=p["yellow"], size=18),
            ft.Text("notes/", size=13, color=p["text"]),
            ft.Container(expand=True), sub("12 files", 11)]), pad=10),
        card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.DESCRIPTION, color=p["text2"], size=18),
            ft.Text("spark-plugs.md", size=13, color=p["text"]),
            ft.Container(expand=True), sub("2.1 KB", 11)]), pad=10),
        card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.DESCRIPTION, color=p["text2"], size=18),
            ft.Text("roadtrip-ideas.md", size=13, color=p["text"]),
            ft.Container(expand=True), sub("4.8 KB", 11)]), pad=10),
    ])

def _journal():
    p = PAL()
    def row(j):
        return card(ft.Column(spacing=4, controls=[
            ft.Row([chip(j["cat"], p["purple"], small=True), ft.Container(expand=True), sub(j["date"], 10)]),
            ft.Text(j["title"], size=13, weight=ft.FontWeight.W_500, color=p["text"]),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(j) for j in M.JOURNAL])

def _foodzen():
    p = PAL()
    fz = M.FOODZEN
    def item(t, done=False, subi=False):
        return ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.CHECK_BOX if done else ft.Icons.CHECK_BOX_OUTLINE_BLANK, size=15,
                    color=p["green"] if done else p["text2"]),
            ft.Text(t, size=12, color=p["text2"] if done else (p["text3"] if subi else p["text"]),
                    style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH) if done else None,
                    offset=ft.Offset(0.5, 0) if subi else None)])
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("Grocery list"), ft.Container(expand=True),
                    chip(f"{len(fz['list'])} items", p["orange"], small=True)]),
            *[item(t) for t in fz["list"]],
        ])),
        card(ft.Column(spacing=4, controls=[
            title("Needs", 13),
            *[item(f"+ " + n) for n in fz["need"]],
        ])),
        card(ft.Column(spacing=4, controls=[
            ft.Row([title("Frequency radar", 13), ft.Container(expand=True),
                    chip("fixation", p["yellow"], ft.Icons.LOCAL_FIRE_DEPARTMENT, small=True)]),
            sub(f"Current fixation: {fz['fixation'] or '— none tracked'}", 11),
        ])),
    ])
