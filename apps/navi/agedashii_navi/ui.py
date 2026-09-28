"""The widgets, sized for a glove and a glance.

Every choice in this file comes from the same two constraints, which are not
the constraints the dashboard's widgets were built under:

**A glance is about 300 milliseconds.** So one number per readout, the largest
type that fits, no decimal point on anything that changes while moving, and
the label smaller than the value rather than beside it at the same weight. The
speed readout is `HUGE` because it is read most often; the turn instruction is
`BIG` because it is read second most often and has words in it.

**A gloved fingertip is about 15 mm.** `TOUCH` is 56 px rather than Material's
48, and every tappable thing in this app is at least that in both directions.
A control that needs a fingernail does not exist here - that is why there are
no sliders and no small chevrons, and why the theme picker is four fixed cards
rather than a scrolling list.

There is no card shadow, no glow and no gradient behind text; see theme.py for
why. `card()` is a flat surface with a hairline, and the hairline is the only
thing separating it from the background.
"""
import math

import flet as ft

from .theme import BIKE_COLOURS, BIKE_OUTLINE, BRD, CYAN, ORANGE, PAL, PAD, PINK, RAD

#: Minimum tappable edge, in logical pixels. Bigger than Material's 48.
TOUCH = 56
#: The four type sizes. Nothing in this app is between them.
HUGE, BIG, MID, SMALL = 64, 28, 16, 12

#: `guidance.turn_icon` names mapped to Material glyphs. Kept here rather than
#: in guidance.py so that module stays importable with no Flet at all, which is
#: what lets its tests run headless.
TURN_GLYPHS = {
    "left": ft.Icons.TURN_LEFT,
    "right": ft.Icons.TURN_RIGHT,
    "slight_left": ft.Icons.TURN_SLIGHT_LEFT,
    "slight_right": ft.Icons.TURN_SLIGHT_RIGHT,
    "sharp_left": ft.Icons.TURN_SHARP_LEFT,
    "sharp_right": ft.Icons.TURN_SHARP_RIGHT,
    "u_turn": ft.Icons.U_TURN_LEFT,
    "straight": ft.Icons.STRAIGHT,
    "flag": ft.Icons.FLAG,
}


def glyph(name):
    return TURN_GLYPHS.get(name, ft.Icons.NAVIGATION)


def card(content, pad=12, colour=None, radius=12, border=None):
    """A flat surface with a hairline. No shadow, no gradient."""
    p = PAL()
    return ft.Container(
        content=content, padding=pad, border_radius=RAD(radius),
        bgcolor=colour or p["surface"], border=border or BRD(p["border"], 1))


def label(text, size=SMALL, colour=None):
    p = PAL()
    return ft.Text(text, size=size, color=colour or p["text2"],
                   weight=ft.FontWeight.W_600)


def value(text, size=BIG, colour=None):
    p = PAL()
    return ft.Text(text, size=size, color=colour or p["text"],
                   weight=ft.FontWeight.W_800)


def readout(text, unit="", size=HUGE, colour=None):
    """A number and its unit, the unit small and sitting on the baseline.

    The unit is deliberately not the same size as the number: at 64 px a
    three-character unit beside a two-digit speed reads as a five-character
    number for the first tenth of a second.
    """
    p = PAL()
    row = [ft.Text(text, size=size, color=colour or p["text"],
                   weight=ft.FontWeight.W_900)]
    if unit:
        row.append(ft.Container(
            padding=ft.Padding(left=4, top=0, right=0, bottom=size * 0.16),
            content=ft.Text(unit, size=max(SMALL, size * 0.24),
                            color=p["text2"], weight=ft.FontWeight.W_700)))
    return ft.Row(spacing=0, tight=True,
                  vertical_alignment=ft.CrossAxisAlignment.END, controls=row)


def stat(name, text, colour=None):
    """A labelled number for the strip under the map."""
    return ft.Column(spacing=0, tight=True, controls=[
        value(text, MID, colour), label(name, SMALL)])


def button(text, on_click, icon=None, primary=False, expand=None):
    """A glove-sized button. Text, not an icon alone, wherever there is room.

    `expand` defaults to None, Flet's own default, never False: a control with
    `expand` set at all - False included - fails inside a `Row(wrap=True)`
    and a release build draws it as a grey box (the Around screen, v1.11.2).

    Material's FilledButton and OutlinedButton both come in under `TOUCH` at
    their default density, so the height is set explicitly rather than left to
    the theme - a 40 px button is a button that gets missed at a stop light.
    """
    p = PAL()
    body = [ft.Icon(icon, size=20,
                    color=p["bg"] if primary else p["text"])] if icon else []
    body.append(ft.Text(text, size=MID, weight=ft.FontWeight.W_700,
                        color=p["bg"] if primary else p["text"]))
    return ft.Container(
        height=TOUCH, expand=expand, on_click=on_click, ink=True,
        padding=PAD(0, 16), border_radius=RAD(12),
        bgcolor=p["accent"] if primary else p["surface"],
        border=None if primary else BRD(p["border"], 1),
        alignment=ft.Alignment.CENTER,
        content=ft.Row(spacing=8, tight=True,
                       alignment=ft.MainAxisAlignment.CENTER, controls=body))


def icon_button(icon, on_click, tooltip="", lit=False):
    """A square glove-sized tap target, for the map's own controls."""
    p = PAL()
    return ft.Container(
        width=TOUCH, height=TOUCH, on_click=on_click, ink=True,
        tooltip=tooltip or None, border_radius=RAD(12),
        bgcolor=p["surface"],
        border=BRD(p["accent"] if lit else p["border"], 1),
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(icon, size=24, color=p["accent"] if lit else p["text"]))


def pill(text="Navi"):
    """The identity, and the only gradient in the app."""
    return ft.Container(
        padding=PAD(4, 12), border_radius=RAD(10),
        gradient=ft.LinearGradient(colors=[ORANGE, PINK, CYAN],
                                   begin=ft.Alignment(-1, 0),
                                   end=ft.Alignment(1, 0)),
        content=ft.Text(text, size=13, weight=ft.FontWeight.W_800,
                        color="#0d1117"))


def chip(text, colour=None, icon=None):
    """A small state badge. Never decoration - only ever a state."""
    p = PAL()
    body = [ft.Icon(icon, size=13, color=colour or p["text2"])] if icon else []
    body.append(ft.Text(text, size=SMALL, weight=ft.FontWeight.W_700,
                        color=colour or p["text2"]))
    return ft.Container(
        padding=PAD(3, 8), border_radius=RAD(9), bgcolor=p["surface"],
        border=BRD(colour or p["border"], 1),
        content=ft.Row(spacing=5, tight=True, controls=body))


def bike_marker(colour_key="yellow", heading=None, size=34):
    """Her bike on the map, in one of her two colours.

    Her ask: "A PICTURE OF MY MOTORCYCLE OR A MOTOCRCYLE EMOJI INSTEAD OF A
    DOT OR CAR TO REPRESENT MY LOCATION/PERSON. TWO CHOICES ONE YELLOW ONE
    BLACK."

    A Material motorcycle glyph rather than the emoji: the emoji renders in
    whatever colour the platform font decides, which is neither yellow nor
    black, and it faces different ways on different Android versions. This is
    one glyph, tinted to her choice, on a disc of the opposite tone so it is
    findable on light tiles and dark ones alike.

    `heading` rotates the disc. Flet's rotation is in radians, so a compass
    bearing in degrees is converted rather than passed straight through.
    """
    body = BIKE_COLOURS.get(colour_key, BIKE_COLOURS["yellow"])
    edge = BIKE_OUTLINE.get(colour_key, BIKE_OUTLINE["yellow"])
    disc = ft.Container(
        width=size, height=size, border_radius=size / 2, bgcolor=body,
        border=BRD(edge, 2), alignment=ft.Alignment.CENTER,
        content=ft.Icon(ft.Icons.TWO_WHEELER, size=size * 0.62, color=edge))
    if heading is None:
        return disc
    return ft.Container(
        width=size, height=size, content=disc,
        rotate=ft.Rotate(angle=math.radians(heading),
                         alignment=ft.Alignment.CENTER))


def bar(fraction, colour=None, height=8, back=None):
    """A horizontal fill, for lean and for progress along a route.

    Clamped rather than asserted: a lean fraction over 1.0 means she is
    leaning further than the scale expected, which is a moment to draw a full
    bar and not one to raise an exception.
    """
    p = PAL()
    fraction = 0.0 if fraction is None else max(0.0, min(1.0, fraction))
    return ft.Container(
        height=height, border_radius=RAD(height / 2),
        bgcolor=back or p["border"],
        content=ft.Row(spacing=0, controls=[
            ft.Container(expand=max(1, int(fraction * 1000)), height=height,
                         border_radius=RAD(height / 2),
                         bgcolor=colour or p["accent"]),
            ft.Container(expand=max(1, int((1.0 - fraction) * 1000))),
        ]))


def row_tap(content, on_click, height=TOUCH):
    """A whole list row as one tap target."""
    p = PAL()
    return ft.Container(
        height=height, on_click=on_click, ink=True, padding=PAD(0, 12),
        border_radius=RAD(10), bgcolor=p["surface"],
        border=BRD(p["border"], 1), content=content)


def empty(text, icon=ft.Icons.INBOX):
    """What a list says when it has nothing, without looking broken."""
    p = PAL()
    return ft.Container(
        padding=PAD(28, 16), alignment=ft.Alignment.CENTER,
        content=ft.Column(spacing=8, tight=True,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[ft.Icon(icon, size=32, color=p["text3"]),
                                    label(text, MID, p["text2"])]))


def error_card(text, exc=None):
    """A surface that failed to build, saying so instead of going blank."""
    p = PAL()
    lines = [value(text, MID, p["bad"])]
    if exc is not None:
        lines.append(label(f"{type(exc).__name__}: {exc}", SMALL, p["text2"]))
    return card(ft.Column(spacing=6, controls=lines), border=BRD(p["bad"], 1))
