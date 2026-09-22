"""The small set of things every surface is built out of.

A card, a chip, a heading, a status dot, and the two padding/border
shorthands that go with them. Deliberately tiny: this is the vocabulary a
page is written in, not a widget library. Anything that needed a theme
value asks ``agedashii_qi`` for it, so a page never reaches past this
module to a palette.
"""
import flet as ft

from agedashii_qi.appearance import PAL, RAD, GLOW_STYLE

def PAD(v=0, h=0):
    return ft.Padding(left=h, top=v, right=h, bottom=v)

def BRD(c, w=1):
    s = ft.BorderSide(w, c)
    return ft.Border(s, s, s, s)

def card(content, pad=14, color=None, radius=12, border=None):
    p = PAL()
    return ft.Container(
        content=content, padding=pad, border_radius=RAD(radius),
        bgcolor=color or p["surface"], border=border or BRD(p["border"], 1),
    )

def chip(text, color=None, icon=None, small=False):
    p = PAL()
    c = color or p["accent"]
    return ft.Container(
        padding=PAD(4, 10) if not small else PAD(2, 8),
        border_radius=RAD(20),
        bgcolor=ft.Colors.with_opacity(0.14, c),
        border=BRD(ft.Colors.with_opacity(0.4, c), 1),
        content=ft.Row(spacing=5, tight=True, controls=[
            ft.Icon(icon, size=13 if small else 15, color=c) if icon else ft.Container(),
            ft.Text(text, size=11 if small else 13, color=c, weight=ft.FontWeight.W_500),
        ]),
    )

def title(text, size=16):
    # Every card heading in the app comes through here, which is what makes
    # one switch in Settings enough to light all of them.
    return ft.Text(text, size=size, weight=ft.FontWeight.W_600, color=PAL()["text"],
                   style=GLOW_STYLE())

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

