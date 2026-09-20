import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, S, apply_theme
from ..theme_data import THEMES, THEME_NAMES
from .. import api, config


def server_card(page):
    """Where the app looks for the dashbored server, and whether it is there.

    Deliberately shows the real reason a connection failed rather than a
    generic "offline": "blocked by auth" and "unreachable" need completely
    different fixes, and the app silently falling back to sample data is the
    failure mode this card exists to make visible.
    """
    p = PAL()
    field = ft.TextField(
        value=config.SERVER_URL, dense=True, filled=True, text_size=13,
        label="Server address", hint_text="192.0.2.21:6900",
        border_color=p["border"], focused_border_color=p["accent"],
        color=p["text"], label_style=ft.TextStyle(color=p["text2"], size=11),
    )
    light = ft.Container(width=9, height=9, border_radius=5)
    status = ft.Text(size=11, color=p["text2"], selectable=True)

    def paint():
        st = config.STATUS
        light.bgcolor = p["green"] if st["ok"] else p["pink"]
        status.value = ("live data - " if st["ok"] else "sample data - ") + st["detail"]

    paint()

    async def do_test(e):
        status.value = "checking..."
        light.bgcolor = p["text2"]
        page.update()
        config.set_url(field.value)
        api.refresh()          # drop cached responses from the old address
        paint()
        page.update()

    async def do_save(e):
        config.set_url(field.value)
        api.refresh()
        saved = await config.save(S.prefs) if S.prefs else False
        paint()
        if not saved:
            status.value += "  (could not persist - will reset on restart)"
        page.update()

    return card(ft.Column(spacing=10, controls=[
        title("Server", 15),
        field,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[light, ft.Container(expand=True, content=status)]),
        ft.Row(spacing=8, controls=[
            ft.FilledButton("Save", on_click=do_save, height=34),
            ft.OutlinedButton("Test", on_click=do_test, height=34),
        ]),
        sub("Off the LAN, connect the VPN first - the public hostname is behind "
            "a login and will not answer the app.", 10),
    ]))


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
        server_card(page),
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
