"""How the app moves.

One place for every duration and curve in Agedashii, because the thing that
makes an interface feel expensive is not any single animation - it is that
all of them agree. Flet's own showcase feels smooth largely because its
timings are consistent; a screen where a tab cross-fades in 200 ms and a
card slides in 600 ms feels broken even though both are "animated".

Three things live here, matching what was actually asked for:

``switcher``   cross-fade between surfaces and tabs, instead of the hard
               cut the app does today - the whole screen being replaced in
               one frame is the single biggest reason it reads as cheap.
``skeleton``   a placeholder with the shape of the thing that is loading,
               instead of a blank area followed by a sudden pop of content.
``reveal``     a card that fades and rises into place, staggered down a
               list, so a page assembles rather than appearing.

Reduced motion
--------------
Every helper checks ``S.reduce_motion`` and returns the still version when
it is on - the content, with no animation wrapper at all, not a zero-length
animation. Motion is genuinely unpleasant for some people and this app's
owner is one of the people it can affect, so it is a real switch rather
than a token one, and it is honoured by construction: a caller cannot
forget, because the helpers are the only way these effects get built.

Durations
---------
Deliberately short. Flet's AnimatedSwitcher defaults to ONE SECOND, which
is about five times too slow for navigation and is why an app that uses
the defaults feels sluggish rather than smooth. Material's own guidance
puts a simple transition at 150-250 ms; anything a person does dozens of
times an hour belongs at the bottom of that range.
"""
import flet as ft

from agedashii_qi import PAL, RAD, S

# --------------------------------------------------------------- timings
# Named by what they are for, not by their length, so a call site reads as
# intent and every screen changing at the same speed is the default rather
# than a coincidence.
SURFACE_MS = 180      # switching page or tab - the most frequent move
CARD_MS = 220         # a card appearing, expanding or resizing
FEEDBACK_MS = 90      # press / hover, must feel instant
SHIMMER_MS = 1100     # one pass of a loading placeholder

# EASE_OUT for anything arriving: fast at first, settling at the end, which
# reads as the thing having weight. EASE_IN_OUT for something that both
# leaves and arrives, so neither end snaps.
ARRIVE = ft.AnimationCurve.EASE_OUT
DEPART = ft.AnimationCurve.EASE_IN
BOTH = ft.AnimationCurve.EASE_IN_OUT


def _still():
    """True when animation should be skipped entirely."""
    return bool(getattr(S, "reduce_motion", False))


def switcher(content, duration=SURFACE_MS, transition=None):
    """Cross-fade to `content` whenever it is replaced.

    Put this once, around the body, and assign to ``.content`` instead of
    rebuilding the parent - that is what turns a hard cut into a fade. The
    duration is explicit because Flet's default of one second is far too
    slow to navigate with.
    """
    if _still():
        return content
    return ft.AnimatedSwitcher(
        content=content,
        transition=transition or ft.AnimatedSwitcherTransition.FADE,
        duration=duration,
        # Out faster than in: the old screen should be gone before the new
        # one has finished arriving, or the two are briefly superimposed
        # and the whole thing looks like a dissolve rather than a change.
        reverse_duration=max(60, duration // 2),
        switch_in_curve=ARRIVE,
        switch_out_curve=DEPART,
    )


def reveal(control, index=0, stagger_ms=28, max_stagger=6):
    """A card that fades and rises into place.

    `index` is its position in a list, which shifts the start slightly so a
    page assembles top-down instead of flashing into existence all at once.
    The stagger is capped: past about six items the delay stops reading as
    rhythm and starts reading as the app being slow, and a forty-item list
    would take over a second to finish arriving.
    """
    if _still():
        return control
    delay = min(index, max_stagger) * stagger_ms
    return ft.Container(
        content=control,
        animate_opacity=ft.Animation(CARD_MS + delay, ARRIVE),
        animate_offset=ft.Animation(CARD_MS + delay, ARRIVE),
        opacity=1,
        offset=ft.Offset(0, 0),
    )


def resizable(control, duration=CARD_MS):
    """Wrap something whose SIZE changes, so it grows instead of jumping.

    For a card being resized, a section expanding, or a panel being
    rearranged - the "expanding / resizing / organizing components" case.
    Flet animates a Container's own width and height, so the thing that
    changes size has to be the Container, not a child of it.
    """
    if _still():
        return (control if isinstance(control, ft.Container)
                else ft.Container(content=control))
    if isinstance(control, ft.Container):
        control.animate_size = ft.Animation(duration, BOTH)
        control.animate_position = ft.Animation(duration, BOTH)
        return control
    return ft.Container(
        content=control,
        animate_size=ft.Animation(duration, BOTH),
        animate_position=ft.Animation(duration, BOTH),
    )


def skeleton(height=14, width=None, radius=6):
    """One grey bar standing in for a line of content that has not arrived.

    Deliberately NOT a spinner. A spinner says "something is happening
    somewhere"; a skeleton says "a list of cards is about to be here, this
    tall", so the layout does not jump when the real thing lands. That jump
    is most of what "feels like shit" actually is.

    The colour comes from the palette, so it belongs to whichever of the 24
    themes is on rather than being a grey that looks wrong in half of them.
    """
    p = PAL()
    bar = ft.Container(
        height=height, width=width, border_radius=RAD(radius),
        bgcolor=ft.Colors.with_opacity(0.10, p["text"]),
    )
    if _still():
        return bar
    bar.animate_opacity = ft.Animation(SHIMMER_MS, BOTH)
    bar.opacity = 0.55
    return bar


def skeleton_card(lines=3, pad=14):
    """A card-shaped placeholder: a heading bar and a few shorter ones."""
    p = PAL()
    widths = [140, None, 90, None, 110]
    rows = [skeleton(height=13 if i == 0 else 10,
                     width=widths[i % len(widths)])
            for i in range(max(1, lines))]
    side = ft.BorderSide(1, p["border"])
    return ft.Container(
        padding=pad, border_radius=RAD(12), bgcolor=p["surface"],
        border=ft.Border(side, side, side, side),
        content=ft.Column(spacing=9, controls=rows),
    )


def skeleton_list(cards=3):
    """What a loading surface shows instead of nothing."""
    return ft.Column(spacing=10, controls=[skeleton_card() for _ in range(cards)])


def pressable(control, on_click=None, tooltip=None, radius=12):
    """Give something press feedback it does not have.

    Most of this app's tappable surfaces are plain Containers with an
    on_click and no ink, so a tap produces no acknowledgement at all until
    whatever it triggered finishes - which on a slow handler is the
    difference between "loading" and "broken". Material's ink ripple is
    drawn by Flutter on the raster thread, so it stays smooth even while
    Python is busy, which is exactly when the reassurance is needed.
    """
    if isinstance(control, ft.Container):
        control.ink = True
        control.border_radius = control.border_radius or RAD(radius)
        if on_click is not None:
            control.on_click = on_click
        if tooltip is not None:
            control.tooltip = tooltip
        if not _still():
            control.animate_scale = ft.Animation(FEEDBACK_MS, ARRIVE)
        return control
    return ft.Container(
        content=control, ink=True, border_radius=RAD(radius),
        on_click=on_click, tooltip=tooltip,
        animate_scale=None if _still() else ft.Animation(FEEDBACK_MS, ARRIVE),
    )
