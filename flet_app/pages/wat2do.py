import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot, S
from .. import api as M
from .lab import _tabs

def build(page):
    p = PAL()
    w = M.WAT2DO

    def _save():
        try:
            M.wat2do_save(w)
        except Exception:
            pass  # fire-and-forget; local state already updated

    def item_row(it, rebuild):
        def toggle(e):
            it["done"] = not it["done"]
            _save()
            rebuild()
        return ft.Row(spacing=8, controls=[
            ft.IconButton(
                icon=ft.Icons.CHECK_BOX if it.get("done") else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                icon_size=17, on_click=toggle, icon_color=p["green"] if it.get("done") else p["text2"],
                style=ft.ButtonStyle(padding=0),
            ),
            ft.Text(it["t"], size=13 if not it.get("sub") else 12,
                    color=p["text2"] if it.get("done") else (p["text3"] if it.get("sub") else p["text"]),
                    style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH) if it.get("done") else None,
                    offset=ft.Offset(0.4, 0) if it.get("sub") else None),
        ])

    def vitals_sliders():
        pain = ft.Slider(min=0, max=10, divisions=10, value=w["pain"], active_color=p["red"], label="{value}")
        spoons = ft.Slider(min=1, max=5, divisions=4, value=w["spoons"], active_color=p["green"], label="{value}")
        pl = ft.Text(f"pain {w['pain']}/10", size=11, color=p["red"])
        sl = ft.Text(f"spoons {w['spoons']}/5", size=11, color=p["green"])
        def pc(e):
            w["pain"] = int(pain.value); pl.value = f"pain {w['pain']}/10"; pl.update(); _save()
        def sc(e):
            w["spoons"] = int(spoons.value); sl.value = f"spoons {w['spoons']}/5"; sl.update(); _save()
        pain.on_change, spoons.on_change = pc, sc
        return card(ft.Column(spacing=4, controls=[
            sub("VITALS", 10), pl, pain, sl, spoons]))
    def checklist():
        box = ft.Column(spacing=6)
        prog = ft.ProgressBar(color=p["orange"], bgcolor=p["bg2"], bar_height=5, border_radius=2)
        cnt = ft.Text()
        def rebuild():
            done = sum(1 for i in w["items"] if i.get("done"))
            prog.value = (done / len(w["items"])) if w["items"] else 0
            cnt.value = f"{done}/{len(w['items'])}"
            box.controls = [item_row(i, rebuild) for i in w["items"]]
            try:
                if cnt.page:
                    cnt.update(); prog.update(); box.update()
            except RuntimeError:
                pass  # not mounted yet — first render happens via page.add
        rebuild()
        return card(ft.Column(spacing=8, controls=[
            ft.Row([title(w["date"], 15), ft.Container(expand=True), cnt]),
            prog, box]))
    def conditions():
        return card(ft.Column(spacing=6, controls=[
            sub("CONDITIONS", 10),
            ft.Row(spacing=6, wrap=True, controls=[
                chip(w["wx_call"] or "—", p["yellow"], ft.Icons.CLOUD, small=True),
                chip("suggest: indoor", p["cyan"], ft.Icons.CHAIR, small=True),
            ]),
        ]))
    return ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Row([ft.IconButton(ft.Icons.CHEVRON_LEFT, icon_color=p["text2"]),
                ft.Column(spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                    title(w["date"], 14)]),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, icon_color=p["text2"])]),
        conditions(), vitals_sliders(), checklist(),
    ])
