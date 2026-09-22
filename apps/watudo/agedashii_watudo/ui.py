"""Watudo's own chrome vocabulary: themed icons, taps that feel like taps.

`agedashii_washi.widgets` is the shared vocabulary - a card, a chip, a
heading - and nothing here replaces it. This is the layer above: the handful
of shapes the *shell* of this app is built from, which the dashboard has no
use for because its chrome is a nine-page drawer rather than a three-stop
bottom bar.

Two things it exists to make true everywhere at once.

**Glyphs follow the theme.** Every icon in the chrome is a Material icon
tinted from `PAL()`, never a fixed colour and never an emoji. Her rule:
emoji only where the thing *is* the thing - a plate of food, a bike - and
themed Material icons for everything that is furniture. All of Watudo's
chrome is furniture, so all of it is Material, and switching to any of the
24 themes moves every glyph with it.

**A tap looks like it landed.** Flet's own showcase feels smooth because
every tappable surface ripples and every state change eases rather than
snapping; this app was doing neither. `tap()` puts a themed ink ripple under
anything tappable and eases its colour changes, so a control that got
nothing before now answers in the ~150 ms before the work behind it
finishes. That gap is the whole point: a handler that has to read storage or
the network cannot repaint instantly, and an unacknowledged tap is the
thing that gets tapped again.
"""
import flet as ft

from agedashii_qi import GLOW, PAL, RAD, S
from agedashii_washi.widgets import BRD, PAD

# How long a state change takes to ease. Short enough to feel like a
# response rather than an animation - Material's own "short4" is 200 ms and
# a nav highlight wants to be quicker than that.
MOTION_MS = 150


def motion(ms=MOTION_MS):
    return ft.Animation(ms, ft.AnimationCurve.EASE_OUT)


def tap(container, *, tint=None):
    """Give a Container the press feedback it would otherwise have none of.

    `ink=True` alone draws Material's ripple in the default splash colour,
    which on a dark theme is a grey smear; the tint makes it the accent, so
    the feedback is themed like everything else. `animate` covers the other
    half - bgcolor, border and shadow changes ease instead of jumping, which
    is what the difference between this and Flet's showcase actually was.

    Returns the same container, so it can wrap a constructor call inline.
    """
    p = PAL()
    container.ink = True
    container.ink_color = ft.Colors.with_opacity(0.16, tint or p["accent"])
    container.animate = motion()
    return container


def glyph(icon, *, size=19, color=None, lit=False):
    """A themed Material icon. `lit` is the one you are standing on.

    The accent glow reaches an icon through `shadows`, which is the only
    shadow an ft.Icon takes - the same alola accent halo the headings and
    the brand pill carry, and off entirely when the glow setting is off,
    because `GLOW()` answers None there.
    """
    p = PAL()
    shadow = GLOW(blur=12, opacity=0.5) if lit else None
    return ft.Icon(icon, size=size, color=color or (p["accent"] if lit else p["text2"]),
                   shadows=[shadow] if shadow else None)


def nav_row(key, label, icon, *, lit, on_click, detail=None):
    """One destination in the drawer, lit if it is where you are.

    The lit row carries the accent wash and alola's glow; the others carry
    nothing, so the drawer does not become a wall of light. Both states ease
    into each other rather than swapping, which is what makes navigating
    with the drawer open look deliberate.
    """
    p = PAL()
    text = [ft.Text(label, size=14,
                    weight=ft.FontWeight.W_600 if lit else ft.FontWeight.W_400,
                    color=p["text"] if lit else p["text2"])]
    if detail:
        text.append(ft.Text(detail, size=10, color=p["text3"]))
    return tap(ft.Container(
        padding=PAD(14, 9), border_radius=RAD(8),
        on_click=lambda e, k=key: on_click(k),
        shadow=GLOW(blur=14, opacity=0.35) if lit else None,
        bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if lit else None,
        content=ft.Row(spacing=12, controls=[
            glyph(icon, lit=lit),
            ft.Column(spacing=0, expand=True, controls=text),
        ])))


def action_row(label, icon, on_click, detail=None):
    """A drawer row that does something instead of going somewhere."""
    p = PAL()
    text = [ft.Text(label, size=14, color=p["text2"])]
    if detail:
        text.append(ft.Text(detail, size=10, color=p["text3"]))
    return tap(ft.Container(
        padding=PAD(14, 9), border_radius=RAD(8), on_click=lambda e: on_click(),
        content=ft.Row(spacing=12, controls=[
            glyph(icon),
            ft.Column(spacing=0, expand=True, controls=text),
        ])))


def pill(label, *, on, on_click, tooltip=None, width=None):
    """A small chosen/not-chosen chip - a radius, a marker style, a count.

    Fixed or intrinsic width and never `expand`: these are laid out in a
    `Row(wrap=True)`, and a wrap hands its children unbounded constraints.
    Anything that tries to expand into that paints over the whole screen -
    which is exactly how a filled TextField in a wrap made another app's
    page unusable twice. Nothing returned from here expands.
    """
    p = PAL()
    return tap(ft.Container(
        padding=PAD(5, 12), border_radius=RAD(20), width=width, tooltip=tooltip,
        on_click=on_click,
        bgcolor=ft.Colors.with_opacity(0.16, p["accent"]) if on else None,
        border=BRD(p["accent"] if on else p["border"], 1),
        shadow=GLOW(blur=10, opacity=0.3) if on else None,
        content=ft.Text(label, size=11, text_align=ft.TextAlign.CENTER,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["accent"] if on else p["text2"])))


def switch_row(label, why, value, on_change, *, icon=None, disabled=False):
    """One labelled switch with the sentence that says what it does.

    The sentence is not optional. Every switch in this app changes something
    you cannot see from the switch itself, and a row of unexplained toggles
    is how a settings screen becomes something you are afraid to touch.
    """
    p = PAL()
    lead = [glyph(icon, size=17, color=p["text3"] if disabled else None)] if icon else []
    return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        *lead,
        ft.Column(spacing=1, expand=True, controls=[
            ft.Text(label, size=13, color=p["text3"] if disabled else p["text"]),
            ft.Text(why, size=10, color=p["text3"]),
        ]),
        ft.Switch(value=value, on_change=on_change, disabled=disabled,
                  active_color=p["accent"]),
    ])


def link_row(label, why, icon, on_click):
    """A tappable row that leads somewhere else - Help, from Settings."""
    p = PAL()
    return tap(ft.Container(
        padding=PAD(8, 10), border_radius=RAD(8), on_click=lambda e: on_click(),
        border=BRD(p["border"], 1),
        content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
            glyph(icon, size=18, color=p["accent"]),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(label, size=13, color=p["text"]),
                ft.Text(why, size=10, color=p["text3"])]),
            glyph(ft.Icons.CHEVRON_RIGHT, size=18, color=p["text3"]),
        ])))


def dropdown(value, options, on_select, *, label=None, width=260):
    """A Dropdown wired to 0.86's `on_select`.

    There is no `on_change` on a Dropdown in this Flet: setting one binds a
    stray attribute, raises nothing, and ships a control that silently does
    not work. It has happened here before, which is why every dropdown in
    this app is built by this function and not by hand.
    """
    p = PAL()
    return ft.Dropdown(
        value=value, options=[ft.dropdown.Option(k, t) for k, t in options],
        on_select=on_select, dense=True, filled=True, text_size=12, width=width,
        label=label, border_color=p["border"], color=p["text"],
        label_style=ft.TextStyle(color=p["text2"], size=11))


def picked(e, valid, fallback):
    """The option chosen, however this Flet build reports it.

    The selection arrives as a property patch on the control or as the event
    payload depending on the platform, so both are checked and anything
    unrecognised leaves the setting alone. Guessing here would silently
    reset a preference.
    """
    for candidate in (getattr(getattr(e, "control", None), "value", None),
                      getattr(e, "data", None)):
        if isinstance(candidate, str) and candidate in valid:
            return candidate
    return fallback


def field(value, hint, *, on_submit=None, width=None, expand=False):
    """A text field in the app's colours. Never `expand` inside a wrap.

    `expand` is a parameter rather than always-on precisely so a caller
    inside a `Row(wrap=True)` can say no: a filled TextField given unbounded
    constraints paints its fill over the whole screen.
    """
    p = PAL()
    return ft.TextField(
        value=value, hint_text=hint, dense=True, filled=True, text_size=12,
        width=width, expand=expand, on_submit=on_submit,
        border_color=p["border"], focused_border_color=p["accent"],
        focused_border_width=2 if S.glow else 1, color=p["text"],
        hint_style=ft.TextStyle(color=p["text3"], size=11))
