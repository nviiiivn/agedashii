import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, S, apply_theme
from ..theme_data import THEMES, THEME_NAMES

def build(page, on_theme=None):
    p = PAL()

    def swatch(key):
        t = THEMES[key]
        active = S.theme == key
        def pick(e):
            apply_theme(page, key)
            if on_theme:
                on_theme()
        return ft.Container(
            width=72, border_radius=10, padding=PAD(6, 4),
            bgcolor=t["surface"], border=BRD(t["accent"], 2 if active else 1),
            on_click=pick,
            content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Row(spacing=3, alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["accent"]),
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["green"]),
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["pink"]),
                ]),
                ft.Text(THEME_NAMES[key], size=9, color=t["text"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER),
            ]),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=1,
                                color=ft.Colors.with_opacity(0.5, t["accent"])) if active else None,
        )

    def group(label, keys):
        return ft.Column(spacing=8, controls=[
            sub(label.upper(), 10),
            ft.Row(spacing=8, wrap=True, run_spacing=8, controls=[swatch(k) for k in keys]),
        ])

    keys = list(THEME_NAMES)
    half = (len(keys) + 1) // 2
    return ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=4, controls=[
            title("Appearance", 15),
            sub(f"Theme: {THEME_NAMES[S.theme]}", 11),
        ])),
        group("Themes", keys),
        card(ft.Column(spacing=6, controls=[
            title("System", 14),
            sub("Layout editing, menu builder, machine editor and the rest of Settings land with API wiring.", 11),
        ])),
    ])
