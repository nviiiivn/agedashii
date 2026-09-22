import flet as ft
from .theme_data import THEMES, THEME_NAMES, DEFAULT, LIGHT_THEMES

class State:
    theme = DEFAULT
    page = None
    # Flet SharedPreferences instance, set once in main(). Held here because a
    # service is unregistered as soon as it stops being strongly referenced.
    prefs = None

S = State()

def PAL():
    return THEMES[S.theme]

def PAD(v=0, h=0):
    return ft.Padding(left=h, top=v, right=h, bottom=v)

def BRD(c, w=1):
    s = ft.BorderSide(w, c)
    return ft.Border(s, s, s, s)

def card(content, pad=14, color=None, radius=12, border=None):
    p = PAL()
    return ft.Container(
        content=content, padding=pad, border_radius=radius,
        bgcolor=color or p["surface"], border=border or BRD(p["border"], 1),
    )

def chip(text, color=None, icon=None, small=False):
    p = PAL()
    c = color or p["accent"]
    return ft.Container(
        padding=PAD(4, 10) if not small else PAD(2, 8),
        border_radius=20,
        bgcolor=ft.Colors.with_opacity(0.14, c),
        border=BRD(ft.Colors.with_opacity(0.4, c), 1),
        content=ft.Row(spacing=5, tight=True, controls=[
            ft.Icon(icon, size=13 if small else 15, color=c) if icon else ft.Container(),
            ft.Text(text, size=11 if small else 13, color=c, weight=ft.FontWeight.W_500),
        ]),
    )

def title(text, size=16):
    return ft.Text(text, size=size, weight=ft.FontWeight.W_600, color=PAL()["text"])

def sub(text, size=12):
    return ft.Text(text, size=size, color=PAL()["text2"])

def dot(color, dim=False, size=10):
    p = PAL()
    c = ft.Colors.with_opacity(0.35, color) if dim else color
    return ft.Container(
        width=size, height=size, border_radius=size,
        bgcolor=c,
        shadow=ft.BoxShadow(blur_radius=8, spread_radius=1, color=c) if not dim else None,
    )

def apply_theme(page, key=None):
    if key:
        S.theme = key if key in THEMES else DEFAULT
    p = PAL()
    S.page = page
    # Which themes are light is asked of the palette, not kept in a list here:
    # the list said "light, sepia, pastel" and went stale the moment pastel
    # became a dark theme, leaving Material painting light-mode widgets on a
    # near-black page.
    page.theme_mode = ft.ThemeMode.LIGHT if S.theme in LIGHT_THEMES else ft.ThemeMode.DARK
    page.theme = ft.Theme(
        color_scheme_seed=p["accent"],
        scaffold_bgcolor=p["bg"],
        canvas_color=p["bg"],
        appbar_theme=ft.AppBarTheme(color=p["text"], bgcolor=p["bg"]),
        navigation_bar_theme=ft.NavigationBarTheme(
            bgcolor=p["bg2"], indicator_color=ft.Colors.with_opacity(0.15, p["accent"]),
            label_text_style=ft.TextStyle(size=11, color=p["text2"]),
        ),
    )
    page.update()
