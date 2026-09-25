"""The cards every app's Home is built from.

Widgets, not a page - the same kind of thing as ``widgets.py`` and
``surface.py``. Each function returns one card and knows nothing about
which app is drawing it or what else is on the screen. The app's own
``home.py`` decides which of these it wants and in what order, and
``panels.layout`` does the arranging.

Weather is deliberately NOT here. It is a 1,623-line page that lives in
Moto and Watudo; what is here is a *shortcut* to it - a number and a tap -
which an app without a Weather page simply leaves out.

Her brief, in full: "the quick links and quick actions and
'welcome'/'good morning' shit, a weather shortcut, and maybe a music
component".
"""
import datetime

import flet as ft

from . import api as M
from . import prefs
from .ui_compat import BRD, PAD, PAL, RAD, card, sub

#: Fallback when a quick link names an icon that does not exist. A link
#: that draws no icon reads as a broken row.
FALLBACK_ICON = ft.Icons.LINK


def _icon(name):
    return getattr(ft.Icons, str(name).upper(), FALLBACK_ICON)


def greeting(name="nvii", now=None):
    """Good morning / afternoon / evening, and today's date."""
    p = PAL()
    now = now or datetime.datetime.now()
    hour = now.hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return ft.Column(spacing=0, controls=[
        ft.Text(f"Good {part}, {name}", size=22,
                weight=ft.FontWeight.W_700, color=p["text"]),
        ft.Text(now.strftime("%a, %b ") + str(now.day), size=12,
                color=p["text3"]),
    ])


def links(on_open=None):
    """The shortcuts, read from prefs rather than hardcoded.

    They were a module constant, which made "which links should this app
    ship with" a question somebody had to answer in advance for every app.
    Settings edits them now.
    """
    p = PAL()
    rows = []
    for label, note, url, icon in prefs.quick_links():
        rows.append(ft.Container(
            padding=PAD(7, 10), border_radius=RAD(9), bgcolor=p["bg2"],
            border=BRD(p["border"], 1), ink=True,
            on_click=(lambda e, u=url: on_open(u)) if on_open else None,
            content=ft.Row(spacing=9, controls=[
                ft.Icon(_icon(icon), size=15, color=p["accent"]),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(label, size=12.5, color=p["text"]),
                    ft.Text(note, size=10.5, color=p["text3"]),
                ]),
                ft.Icon(ft.Icons.OPEN_IN_NEW, size=13, color=p["text3"]),
            ])))
    if not rows:
        rows = [sub("no quick links - add one in Settings", 10)]
    return card(ft.Column(spacing=8, controls=[sub("QUICK LINKS", 10), *rows]))


def actions(items):
    """A row of real controls. `items` is (label, icon, handler) tuples.

    A tile with no handler is not drawn. Home once showed four - Wake,
    Ride, Wat2Do, List - that did nothing when tapped, which reads as the
    app being broken rather than as a button that is not ready yet.
    """
    p = PAL()
    tiles = [
        ft.Container(
            col={"xs": 6, "md": 3}, padding=PAD(9, 0), border_radius=RAD(10),
            bgcolor=p["bg2"], border=BRD(p["border"], 1), ink=True,
            alignment=ft.Alignment(0, 0), on_click=handler,
            content=ft.Column(
                spacing=3, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[ft.Icon(icon, size=17, color=p["accent"]),
                          ft.Text(label, size=11, color=p["text2"])]))
        for label, icon, handler in items if callable(handler)]
    if not tiles:
        return None
    return card(ft.Column(spacing=8, controls=[
        sub("QUICK ACTIONS", 10), ft.ResponsiveRow(controls=tiles)]))


def nowplaying():
    """What is playing, or nothing at all.

    Only looks like a playing track when one is. This card showed the same
    hardcoded song permanently, play icon and all, for as long as it
    existed.
    """
    p = PAL()
    n = getattr(M, "NOWPLAYING", None)
    if not n or not n.get("title"):
        return None
    return card(ft.Row(spacing=10, controls=[
        ft.Icon(ft.Icons.MUSIC_NOTE, size=18, color=p["accent"]),
        ft.Column(spacing=0, expand=True, controls=[
            ft.Text(n.get("title", ""), size=13, color=p["text"], no_wrap=True),
            ft.Text(n.get("artist", ""), size=11, color=p["text3"],
                    no_wrap=True)]),
    ]))


def weather_shortcut(on_open=None, place="Oakland"):
    """A number and a tap through to the Weather page.

    Not the page - that is 1,623 lines and lives in the apps that have one.
    An app with no Weather page passes no handler, or leaves this out.
    """
    p = PAL()
    try:
        cur = (M.weather() or {}).get("current")
    except Exception:                                  # noqa: BLE001
        cur = None
    if not cur:
        return card(ft.Column(spacing=6, controls=[
            sub(f"WEATHER - {place.upper()}", 10),
            ft.Text("no reading", size=12, color=p["text3"])]))
    temp = cur.get("temperature_2m")
    return ft.Container(
        border_radius=RAD(12), bgcolor=p["surface"], padding=14,
        border=BRD(p["border"], 1), ink=bool(on_open),
        on_click=(lambda e: on_open()) if on_open else None,
        content=ft.Column(spacing=6, controls=[
            sub(f"WEATHER - {place.upper()}", 10),
            ft.Row(spacing=10, controls=[
                ft.Text(f"{round(temp)}" if temp is not None else "-",
                        size=24, weight=ft.FontWeight.W_700, color=p["text"]),
                ft.Text(f"wind {cur.get('wind_speed_10m', '-')} mph",
                        size=11.5, color=p["text3"]),
            ]),
        ]))
