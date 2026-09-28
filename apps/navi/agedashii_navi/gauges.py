"""The dash: an artificial horizon, a loudness meter, climb, and a g dot.

Her ask, in her words:

    "a sort of 'volume' meter slash pseudo rpm"
    "a horizon ball thing to show side axis (left/right lean) and 'z' axis ish
     where the height from sea level is put up as well to show ascension
     descension info (wherein the xy axis is represented on the map)"
    "basically widgets for sensory logging over the map itself or on the bottom
     like some kinda dash display?"

So: the map carries x and y, and this strip carries everything else. Five
gauges, each answering a question the map cannot.

DRAWN WITH CONTAINERS, NOT A CANVAS

Every gauge here is nested `Container`s with `rotate` and `offset` transforms. No
`flet.canvas`, no custom painter, no images. Two reasons: a transformed Container
is a Flutter widget the compositor already knows how to move on the GPU, and it
survives a theme change by being rebuilt from the palette like everything else. A
canvas would need its own repaint path and its own colour bookkeeping.

THE HORIZON IS HONEST ABOUT WHICH ANGLE IT SHOWS

It takes TWO angles and treats them differently, because they are different
things:

* `lean_deg` - from speed and yaw rate, the real lean of the bike - rotates the
  horizon. This is the one that matters in a corner.
* `roll_deg` - from the accelerometer, the roll of the PHONE - draws a thin
  outline needle, and only when it disagrees with lean by enough to be worth
  seeing. A phone in a tank bag reads near zero roll in a steady corner, because
  it measures the resultant of gravity and cornering force, so showing it as the
  lean would be a lie at exactly the moment lean is interesting.

Pitch tilts the horizon band up and down, the aviation convention, which reads
correctly with no label.
"""
import math

import flet as ft

from . import bikes, guidance, ui
from .theme import BRD, PAD, PAL, RAD

#: Lean angle that puts the horizon at the edge of the ball.
LEAN_FULL_DEG = 45.0
#: Pitch that moves the horizon band a full half-ball.
PITCH_FULL_DEG = 30.0
#: Cornering/braking load that reaches the rim of the g dot.
G_FULL = 1.0
#: Degrees of disagreement between lean and phone roll before the phone's own
#: needle is drawn at all.
ROLL_HINT_DEG = 12.0

#: How far in from the left a gauge has to start to clear Ride's floating
#: chrome: the menu button at left=12, top=12 (main.py) and the record/options
#: column directly under it at top=74, both 52 wide (pages/ride.py).
#:
#: A gauge that lands in that strip gets drawn over by the buttons, which is the
#: defect she reported once already - the menu button "seems adamently placed
#: above and over the turn by turn area". ride.py's BAND_INSET fixed it for the
#: two text bands and nothing fixed it for the gauges, so picking "Sideways:
#: left" in Settings put the numbers behind the buttons.
#:
#: Same arithmetic as BAND_INSET: 12 + 52 + a 12 gap.
CHROME_CLEAR = 76


def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def _pad_clear(v, h, clear):
    """`PAD`, except a block on the left edge starts past Ride's chrome.

    `clear` is the caller's own answer to "does this layout put something in
    that strip". Bottom-left never does: the chrome column ends well above the
    bottom edge even on a short landscape screen.
    """
    return ft.Padding(left=CHROME_CLEAR if clear else h, top=v,
                      right=h, bottom=v)


def horizon(lean_deg=None, pitch_deg=None, roll_deg=None, size=92):
    """An artificial horizon. Lean rotates it, pitch raises it.

    All three angles may be None - the state before the first fix, and on a
    device with no accelerometer. The ball then sits level and the caption says
    nothing, rather than reading a confident zero.
    """
    p = PAL()
    lean = 0.0 if lean_deg is None else _clamp(lean_deg, -89.0, 89.0)
    pitch = 0.0 if pitch_deg is None else _clamp(pitch_deg, -89.0, 89.0)

    # Sky above, ground below, each a full ball tall, so rotating the pair never
    # brings an edge into view.
    band = ft.Column(spacing=0, controls=[
        ft.Container(height=size, bgcolor=p["accent"], opacity=0.22),
        ft.Container(height=2, bgcolor=p["text"]),
        ft.Container(height=size, bgcolor=p["warn"], opacity=0.28)])

    # Pitch shifts the band. `offset` is in fractions of the control's own size,
    # so a half-ball of travel is 0.25 of the double-height band.
    shift = _clamp(pitch / PITCH_FULL_DEG, -1.0, 1.0) * 0.25
    moved = ft.Container(width=size * 2, height=size * 2, content=band,
                         offset=ft.Offset(0, shift))
    # Rotated NEGATIVELY: leaning right drops the right side of the horizon,
    # which is what a rider sees through the visor.
    rotated = ft.Container(width=size * 2, height=size * 2, content=moved,
                           rotate=ft.Rotate(angle=-math.radians(lean),
                                            alignment=ft.Alignment.CENTER))
    ball = ft.Container(
        width=size, height=size, border_radius=RAD(size / 2),
        bgcolor=p["surface"], border=BRD(p["border"], 1),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        alignment=ft.Alignment.CENTER, content=rotated)

    # The fixed mark: two stubs and a dot that do not move, so the ball's motion
    # is read against something.
    mark = ft.Row(spacing=4, tight=True,
                  alignment=ft.MainAxisAlignment.CENTER,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[
                      ft.Container(width=size * 0.20, height=2,
                                   bgcolor=p["text"]),
                      ft.Container(width=5, height=5, border_radius=RAD(3),
                                   bgcolor=p["text"]),
                      ft.Container(width=size * 0.20, height=2,
                                   bgcolor=p["text"])])

    layers = [ball, ft.Container(width=size, height=size,
                                 alignment=ft.Alignment.CENTER, content=mark)]

    # The phone's own roll, only when it disagrees enough to be informative.
    if (roll_deg is not None and lean_deg is not None
            and abs(roll_deg - lean_deg) >= ROLL_HINT_DEG):
        layers.append(ft.Container(
            width=size, height=size, alignment=ft.Alignment.TOP_CENTER,
            content=ft.Container(width=2, height=size * 0.42,
                                 bgcolor=p["text3"], border_radius=RAD(1)),
            rotate=ft.Rotate(angle=-math.radians(_clamp(roll_deg, -89.0, 89.0)),
                             alignment=ft.Alignment.CENTER)))

    caption = "—" if lean_deg is None else f"{abs(lean_deg):.0f}°"
    return ft.Column(spacing=4, tight=True,
                     horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[ft.Stack(width=size, height=size,
                                        controls=layers),
                               ui.label(f"lean {caption}")])


def meter(fraction, peak=None, label="", value="", height=92, width=26):
    """A vertical bar with a peak tick. The loudness and RPM gauge.

    `fraction` and `peak` are 0..1. None draws an EMPTY bar rather than a full
    one - "no signal" must not look like "flat out".
    """
    p = PAL()
    filled = 0.0 if fraction is None else _clamp(fraction, 0.0, 1.0)
    # Warn in the top fifth: that is where a pipe is already loud and where the
    # RPM estimate stops being reliable.
    colour = p["warn"] if filled > 0.8 else p["accent"]
    layers = [ft.Column(spacing=0, controls=[
        ft.Container(expand=max(1, int((1.0 - filled) * 1000))),
        ft.Container(expand=max(1, int(filled * 1000)), bgcolor=colour,
                     border_radius=RAD(4))])]
    if peak is not None:
        # The tick sits at its own height, so a spike that has already passed is
        # still visible.
        tick = _clamp(peak, 0.0, 1.0)
        layers.append(ft.Column(spacing=0, controls=[
            ft.Container(expand=max(1, int((1.0 - tick) * 1000))),
            ft.Container(height=2, bgcolor=p["text"]),
            ft.Container(expand=max(1, int(tick * 1000)))]))
    return ft.Column(spacing=4, tight=True,
                     horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[
                         ft.Container(width=width, height=height,
                                      bgcolor=p["surface"],
                                      border=BRD(p["border"], 1),
                                      border_radius=RAD(6),
                                      clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                      content=ft.Stack(controls=layers)),
                         ui.value(value or "—", ui.SMALL),
                         ui.label(label)])


def climb(alt_m=None, climb_mps=None, imperial=True, height=92):
    """Height, and whether it is going up, with the rate.

    Her "'z' axis ish where the height from sea level is put up as well to show
    ascension descension info". The arrow is the answer at a glance; the rate is
    the detail underneath it.
    """
    p = PAL()
    rate = climb_mps or 0.0
    # A dead band, because a barometer wanders by a few centimetres a second
    # standing still and a flickering arrow is worse than no arrow.
    if rate > 0.3:
        glyph, colour, word = ft.Icons.NORTH_EAST, p["good"], "climbing"
    elif rate < -0.3:
        glyph, colour, word = ft.Icons.SOUTH_EAST, p["warn"], "descending"
    else:
        glyph, colour, word = ft.Icons.EAST, p["text2"], "level"

    if alt_m is None:
        height_text = "—"
    elif imperial:
        height_text = f"{alt_m * 3.280839895:.0f} ft"
    else:
        height_text = f"{alt_m:.0f} m"

    if climb_mps is None:
        rate_text = ""
    elif imperial:
        rate_text = f"{climb_mps * 196.850394:.0f} ft/min"
    else:
        rate_text = f"{climb_mps:.1f} m/s"

    return ft.Container(
        height=height, padding=ft.Padding(left=10, top=8, right=10, bottom=8),
        bgcolor=p["surface"], border=BRD(p["border"], 1), border_radius=RAD(8),
        content=ft.Column(
            spacing=2, tight=True, alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Row(spacing=6, tight=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[ft.Icon(glyph, size=20, color=colour),
                                 ui.value(height_text, ui.MID)]),
                ui.label(rate_text or word),
                ui.label("altitude")]))


def g_ball(g_lat=None, g_long=None, size=92):
    """A dot in a circle: cornering load across, braking and drive up and down.

    The classic g-g diagram, and the one gauge that shows two channels at once
    without numbers - the dot's distance from the middle is how hard the bike is
    working, in whatever direction.
    """
    p = PAL()
    x = _clamp((g_lat or 0.0) / G_FULL, -1.0, 1.0)
    # Braking is negative g_long and belongs at the BOTTOM of the diagram, where
    # a rider expects to feel it, so the sign is flipped for the plot.
    y = _clamp(-(g_long or 0.0) / G_FULL, -1.0, 1.0)
    total = math.sqrt((g_lat or 0.0) ** 2 + (g_long or 0.0) ** 2)
    rings = [ft.Container(width=size * frac, height=size * frac,
                          border_radius=RAD(size / 2),
                          border=BRD(p["border"], 1))
             for frac in (1.0, 0.5)]
    return ft.Column(
        spacing=4, tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Stack(width=size, height=size, controls=[
                *[ft.Container(width=size, height=size,
                               alignment=ft.Alignment.CENTER, content=ring)
                  for ring in rings],
                ft.Container(width=size, height=size,
                             alignment=ft.Alignment(x, y),
                             content=ft.Container(
                                 width=12, height=12, border_radius=RAD(6),
                                 bgcolor=p["bad"] if total > 0.8
                                 else p["accent"]))]),
            ui.label(f"{total:.2f} g" if (g_lat is not None
                                          or g_long is not None) else "—")])


def loudness_norm(dbfs):
    """dBFS to 0..1 on the same scale audio.py uses, or None."""
    if dbfs is None:
        return None
    return _clamp((dbfs + 60.0) / 54.0, 0.0, 1.0)


def rpm_fraction(rpm, machine=None):
    """RPM to 0..1 for the bar, or None. Scaled to this engine's own redline.

    Not a fixed 9000: an EX250 turns 14,000 and a bar scaled to a four would sit
    in its bottom two thirds for the whole ride, while a four on a scale built for
    the twin would never leave the lower half.
    """
    if rpm is None:
        return None
    return _clamp(rpm / float(bikes.redline_rpm(machine)), 0.0, 1.0)


def band_meter(bands, height=92, width=14, label="bands"):
    """Her multi-peak meter: one thin bar per frequency band, side by side.

    `bands` is [(label, 0.0-1.0)] from `audio.analyse`. Three bars rather than one
    because the shape tells you something a single number cannot: energy piled in
    the low band is a big engine loafing, energy in the high band is either revs
    or wind, and the pattern shifting while the total stays put is the throttle
    doing something the loudness meter cannot see.
    """
    p = PAL()
    if not bands:
        bands = [("low", 0.0), ("mid", 0.0), ("high", 0.0)]
    bars = []
    for index, (name, value) in enumerate(bands):
        filled = _clamp(value or 0.0, 0.0, 1.0)
        # Low band coolest, high band warmest, so the shape reads at a glance
        # without reading the labels underneath it.
        colour = (p["accent"], p["good"], p["warn"])[min(2, index)]
        bars.append(ft.Column(spacing=3, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                                  ft.Container(
                                      width=width, height=height - 16,
                                      bgcolor=p["surface"],
                                      border=BRD(p["border"], 1),
                                      border_radius=RAD(4),
                                      clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                      content=ft.Column(spacing=0, controls=[
                                          ft.Container(expand=max(
                                              1, int((1.0 - filled) * 1000))),
                                          ft.Container(
                                              expand=max(1,
                                                         int(filled * 1000)),
                                              bgcolor=colour)])),
                                  ui.label(name[:3])]))
    return ft.Column(spacing=4, tight=True,
                     horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[ft.Row(spacing=4, tight=True, controls=bars),
                               ui.label(label)])


def dash(*, lean_deg=None, roll_deg=None, pitch_deg=None, g_lat=None,
         g_long=None, alt_m=None, climb_mps=None, loudness=None,
         loudness_peak=None, rpm=None, bands=None, machine=None,
         imperial=True, size=92):
    """The whole strip, in one row, scrollable when it does not fit.

    Horizontal scroll rather than wrapping: this sits over a map and has to keep a
    fixed height, so on a narrow phone the far gauge is a swipe away instead of
    stealing another row from the road.

    `loudness` is the ENGINE band's level, not the microphone's total - see
    dsp.band_energy. The distinction is the difference between a meter that
    follows the throttle and one that follows the wind.
    """
    return ft.Row(
        spacing=10, scroll=ft.ScrollMode.AUTO,
        vertical_alignment=ft.CrossAxisAlignment.START,
        controls=[
            horizon(lean_deg=lean_deg, pitch_deg=pitch_deg, roll_deg=roll_deg,
                    size=size),
            g_ball(g_lat=g_lat, g_long=g_long, size=size),
            climb(alt_m=alt_m, climb_mps=climb_mps, imperial=imperial,
                  height=size),
            meter(rpm_fraction(rpm, machine), None, "rpm est",
                  "—" if rpm is None else f"{rpm:.0f}", height=size),
            band_meter(bands, height=size, label="engine"),
            meter(loudness_norm(loudness), loudness_peak, "level",
                  "—" if loudness is None else f"{loudness:.0f} dB",
                  height=size),
        ])


#: Lean angles at which the readout changes colour, warmest last. Taken from the
#: OsmAnd motorcycle fork, which colours lean "white -> yellow -> orange -> red"
#: by intensity: it costs no space at all and says more than the number does,
#: because a rider reads a colour faster than two digits.
LEAN_BANDS = ((0.0, "text"), (18.0, "warn"), (32.0, "bad"))


def lean_colour(lean_deg):
    p = PAL()
    if lean_deg is None:
        return p["text2"]
    name = "text"
    for floor, key in LEAN_BANDS:
        if abs(lean_deg) >= floor:
            name = key
    return p[name]


def _glass(content, pad_v=6, pad_h=10, radius=12):
    """A translucent slab to float over the map.

    The map is behind it, so it needs its own ground to stay legible - but an
    opaque one would be a band, which is the thing being avoided. 0.72 is the
    point where tiles read as texture behind the numbers rather than as detail
    competing with them.
    """
    p = PAL()
    return ft.Container(
        padding=ft.Padding(left=pad_h, top=pad_v, right=pad_h, bottom=pad_v),
        border_radius=RAD(radius),
        bgcolor=ft.Colors.with_opacity(0.72, p["bg"]),
        border=BRD(ft.Colors.with_opacity(0.5, p["border"]), 1),
        content=content)


def _num(value, unit="", size=ui.BIG, colour=None):
    p = PAL()
    row = [ft.Text(value, size=size, weight=ft.FontWeight.W_900,
                   color=colour or p["text"])]
    if unit:
        row.append(ft.Text(unit, size=ui.SMALL, weight=ft.FontWeight.W_700,
                           color=p["text2"]))
    return ft.Row(spacing=3, tight=True,
                  vertical_alignment=ft.CrossAxisAlignment.END, controls=row)


def gear_badge(gear, hint="", size=ui.BIG):
    """The gear, and the shift light. Her "change gear UP/DOWN light".

    The arrow is coloured rather than added: a light that appears and disappears
    moves the layout under her eye at exactly the moment she is reading it.
    """
    p = PAL()
    colour = {"up": p["bad"], "down": p["warn"]}.get(hint, p["text3"])
    glyph = {"up": ft.Icons.ARROW_UPWARD,
             "down": ft.Icons.ARROW_DOWNWARD}.get(hint, ft.Icons.REMOVE)
    return ft.Row(spacing=4, tight=True,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                      ft.Text("—" if gear is None else str(gear), size=size,
                              weight=ft.FontWeight.W_900,
                              color=p["text"] if gear else p["text3"]),
                      ft.Icon(glyph, size=size * 0.55, color=colour)])


def gforce_bars(g_lat=None, g_long=None, width=96, height=10):
    """Two bars from a centre line: braking/drive and left/right cornering.

    Centre-out rather than left-filled, because the sign is the information -
    where the bar grows FROM is fixed and where it grows TO is the reading. The
    longitudinal one is labelled ACC and BRK, which is what a rider calls it.
    """
    p = PAL()

    def bar(value, positive_colour, negative_colour):
        amount = _clamp((value or 0.0) / G_FULL, -1.0, 1.0)
        left = max(1, int((1.0 - max(0.0, -amount)) * 500))
        right = max(1, int((1.0 - max(0.0, amount)) * 500))
        fill = max(1, int(abs(amount) * 500))
        colour = positive_colour if amount >= 0 else negative_colour
        return ft.Container(
            width=width, height=height, border_radius=RAD(height / 2),
            bgcolor=ft.Colors.with_opacity(0.45, p["border"]),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(spacing=0, controls=[
                ft.Container(expand=left),
                ft.Container(expand=fill, bgcolor=colour),
                ft.Container(expand=right)]))

    return ft.Column(spacing=3, tight=True, controls=[
        bar(g_long, p["good"], p["bad"]),
        bar(g_lat, p["accent"], p["accent"]),
        ft.Row(spacing=0, controls=[
            ui.label("BRK"), ft.Container(expand=True), ui.label("ACC")],
            width=width)])


def rpm_line(rpm=None, machine=None, width=120, height=8, hint=""):
    """A single gradient bar for engine speed, reddening towards the redline.

    A bar rather than a dial: at a glance the eye reads LENGTH far faster than
    it reads the angle of a needle, and a bar costs a tenth of the height.
    """
    p = PAL()
    fraction = rpm_fraction(rpm, machine) or 0.0
    over = hint == "up"
    return ft.Column(spacing=3, tight=True, controls=[
        ft.Container(
            width=width, height=height, border_radius=RAD(height / 2),
            bgcolor=ft.Colors.with_opacity(0.45, p["border"]),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(spacing=0, controls=[
                ft.Container(
                    expand=max(1, int(fraction * 1000)),
                    gradient=ft.LinearGradient(
                        colors=[p["good"], p["warn"], p["bad"]],
                        begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0))),
                ft.Container(expand=max(1, int((1.0 - fraction) * 1000)))])),
        ft.Row(spacing=6, tight=True, controls=[
            ui.label("—" if rpm is None else f"{rpm:.0f} rpm",
                     colour=p["bad"] if over else None)])])


def horizon_line(lean_deg=None, pitch_deg=None, width=260, thickness=5):
    """A thick line across the middle that tilts with lean. The gunsight view.

    An aircraft attitude indicator reduced to its one load-bearing element. No
    ball, no sky, no ground - those need a filled circle, which on a map is a
    hole. The line alone carries roll and pitch, reads at the very edge of
    vision, and leaves the map entirely visible through it.
    """
    p = PAL()
    lean = 0.0 if lean_deg is None else _clamp(lean_deg, -75.0, 75.0)
    colour = lean_colour(lean_deg)
    wing = ft.Container(width=width * 0.4, height=thickness,
                        bgcolor=colour, border_radius=RAD(thickness / 2))
    centre = ft.Container(width=thickness * 2.2, height=thickness * 2.2,
                          border_radius=RAD(thickness * 1.1),
                          bgcolor=colour)
    bar = ft.Row(spacing=10, tight=True,
                 alignment=ft.MainAxisAlignment.CENTER,
                 vertical_alignment=ft.CrossAxisAlignment.CENTER,
                 controls=[wing, centre, wing])
    # Pitch raises and lowers the whole line; lean rotates it. Both are the
    # aviation convention, and both are what a rider's inner ear already expects.
    shift = _clamp((pitch_deg or 0.0) / PITCH_FULL_DEG, -1.0, 1.0) * 0.35
    return ft.Container(
        width=width, height=width * 0.5, alignment=ft.Alignment.CENTER,
        content=ft.Container(
            content=bar, offset=ft.Offset(0, shift),
            rotate=ft.Rotate(angle=-math.radians(lean),
                             alignment=ft.Alignment.CENTER)))


#: The layouts, and one line each for the Settings picker.
#:
#: No "Off" here any more. Off is not a layout, it is the gauges switch
#: (`prefs.dash`), and offering it in both places is what made the Settings
#: switch and the Ride options button look like two different things. The
#: "off" key is still honoured by `overlay()` so an older stored value draws
#: nothing, which is what it always meant.
LAYOUTS = (
    ("ribbon", "Ribbon", "one thin row along the bottom edge"),
    ("corners", "Corners", "anchored in the corners, middle of the map clear"),
    ("gunsight", "Gunsight", "a full attitude indicator in the centre"),
    ("column", "Column", "a narrow strip down the left edge"),
    ("strip", "Strip", "the full band under the map - everything at once"),
    ("minimal", "Minimal", "speed and limit, nothing else"),
)


def _speed_block(state, size=ui.HUGE):
    p = PAL()
    over = guidance.over_limit(state.get("speed_ms"), state.get("limit"))
    return ft.Column(spacing=0, tight=True, controls=[
        _num(guidance.speed_text(state.get("speed_ms"), state["imperial"]),
             guidance.speed_unit(state["imperial"]), size,
             p["bad"] if over else p["text"]),
        ui.label("—" if not state.get("limit") else f"limit {state['limit']}")])


def overlay(state, layout="ribbon", wide=False, anchor="bottom"):
    """The telemetry, drawn to float over the map. None when there is nothing.

    `state` is one flat dict so every layout reads the same values and none of
    them can quietly show a different number than its neighbour. The caller
    builds it once per repaint; see `pages/ride.py`.

    Every layout here returns something that goes INSIDE a Stack over the map,
    which is the whole point - "too much of the screen is covered by ui right
    now". Only `strip` takes height of its own, and it is kept because it is the
    only view that shows everything at once.
    """
    p = PAL()
    lean = state.get("lean_deg")
    hint = state.get("shift", "")

    if layout == "off":
        return None

    if layout == "minimal":
        align = {"top": ft.Alignment.TOP_LEFT,
                 "bottom": ft.Alignment.BOTTOM_LEFT,
                 "left": ft.Alignment.BOTTOM_LEFT,
                 "right": ft.Alignment.BOTTOM_RIGHT}[anchor]
        # anchor "top" is the one that lands under the chrome column.
        return ft.Container(alignment=align,
                            padding=_pad_clear(12, 12, anchor == "top"),
                            content=_glass(_speed_block(state)))

    if layout == "ribbon":
        # One strip of numbers, pinned to whichever edge this orientation wants.
        #
        # In landscape it becomes a COLUMN down the chosen side rather than a row
        # along the bottom: a wide screen is short, and a row across the bottom of
        # a landscape phone eats the part of the map she is riding into. Her spec:
        # "horizontal/wide makes the sensor shit mostly on the right side (or left
        # adjustable in settings) and vertically it's at the bottom (or top)".
        items = [
            _num(guidance.speed_text(state.get("speed_ms"), state["imperial"]),
                 guidance.speed_unit(state["imperial"]), ui.BIG,
                 p["bad"] if guidance.over_limit(
                     state.get("speed_ms"), state.get("limit")) else p["text"]),
            _num("—" if not state.get("limit") else str(state["limit"]), "lim",
                 ui.MID),
            _num("—" if lean is None else f"{abs(lean):.0f}°", "lean", ui.MID,
                 lean_colour(lean)),
            gforce_bars(state.get("g_lat"), state.get("g_long"), width=72),
            rpm_line(state.get("rpm"), state.get("machine"), width=90,
                     hint=hint),
            gear_badge(state.get("gear"), hint, ui.MID),
            _num("—" if state.get("alt_m") is None else
                 (f"{state['alt_m'] * 3.28084:.0f}" if state["imperial"]
                  else f"{state['alt_m']:.0f}"),
                 "ft" if state["imperial"] else "m", ui.MID),
        ]
        if wide:
            body = ft.Column(spacing=12, tight=True,
                             scroll=ft.ScrollMode.AUTO,
                             horizontal_alignment=ft.CrossAxisAlignment.END
                             if anchor == "right"
                             else ft.CrossAxisAlignment.START,
                             controls=items)
            align = (ft.Alignment.CENTER_RIGHT if anchor == "right"
                     else ft.Alignment.CENTER_LEFT)
        else:
            body = ft.Row(spacing=14, tight=True, scroll=ft.ScrollMode.AUTO,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=items)
            align = (ft.Alignment.TOP_CENTER if anchor == "top"
                     else ft.Alignment.BOTTOM_CENTER)
        # A column down the LEFT side in landscape runs the full height of the
        # screen, straight through the chrome. A row along an edge does not.
        return ft.Container(alignment=align,
                            padding=_pad_clear(10, 8, wide and anchor == "left"),
                            content=_glass(body, pad_v=8))

    if layout == "corners":
        top = ft.Row(controls=[
            _glass(ft.Row(spacing=6, tight=True, controls=[
                _num("—" if lean is None else f"{abs(lean):.0f}°", "lean",
                     ui.MID, lean_colour(lean))])),
            ft.Container(expand=True),
            _glass(ft.Row(spacing=6, tight=True, controls=[
                _num("—" if state.get("alt_m") is None else
                     (f"{state['alt_m'] * 3.28084:.0f}" if state["imperial"]
                      else f"{state['alt_m']:.0f}"),
                     "ft" if state["imperial"] else "m", ui.MID)]))])
        bottom = ft.Row(vertical_alignment=ft.CrossAxisAlignment.END, controls=[
            _glass(_speed_block(state)),
            ft.Container(expand=True),
            _glass(ft.Column(spacing=4, tight=True,
                             horizontal_alignment=ft.CrossAxisAlignment.END,
                             controls=[
                                 rpm_line(state.get("rpm"),
                                          state.get("machine"), hint=hint),
                                 gear_badge(state.get("gear"), hint, ui.MID)]))])
        # The TOP row only: its left-hand glass sits exactly where the menu
        # button floats. The outer padding already gives 10, so the row needs
        # the remainder. `bottom` is left alone - the chrome ends above it.
        return ft.Container(padding=PAD(10, 10), content=ft.Column(
            controls=[ft.Container(content=top, padding=ft.Padding(
                          left=CHROME_CLEAR - 10, top=0, right=0, bottom=0)),
                      ft.Container(expand=True), bottom]))

    if layout == "gunsight":
        # The real instrument, not a line.
        #
        # This drew `horizon_line` - a single tilting bar - on the reasoning
        # that a filled circle is a hole punched in the map. Her answer was to
        # name what she actually wanted: "attitude indicator also known as gyro
        # horizon and artificial horizon". That is the ball - sky above, ground
        # below, pitch raising the band, roll rotating it, read against a fixed
        # aircraft mark that does not move.
        #
        # It was already built, and was reachable only from the `strip` layout,
        # buried in a scrolling row of small widgets. Gunsight is the layout
        # whose entire point is one large instrument in the middle of the
        # screen, so this is where it belongs.
        return ft.Container(padding=PAD(10, 10), content=ft.Column(controls=[
            ft.Container(expand=True),
            ft.Row(alignment=ft.MainAxisAlignment.CENTER, controls=[
                horizon(lean_deg=lean, pitch_deg=state.get("pitch_deg"),
                        roll_deg=state.get("tilt_deg"), size=148)]),
            ft.Container(expand=True),
            ft.Row(vertical_alignment=ft.CrossAxisAlignment.END, controls=[
                _glass(_speed_block(state, ui.BIG)),
                ft.Container(expand=True),
                _glass(ft.Column(spacing=4, tight=True,
                                 horizontal_alignment=ft.CrossAxisAlignment.END,
                                 controls=[
                                     rpm_line(state.get("rpm"),
                                              state.get("machine"), width=90,
                                              hint=hint),
                                     gear_badge(state.get("gear"), hint,
                                                ui.MID)]))])]))

    if layout == "column":
        return ft.Container(
            alignment=(ft.Alignment.CENTER_RIGHT if anchor == "right"
                       else ft.Alignment.CENTER_LEFT),
            padding=_pad_clear(10, 8, anchor != "right"),
            content=_glass(ft.Column(
                spacing=10, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START, controls=[
                    _speed_block(state, ui.BIG),
                    _num("—" if lean is None else f"{abs(lean):.0f}°", "lean",
                         ui.MID, lean_colour(lean)),
                    gforce_bars(state.get("g_lat"), state.get("g_long"),
                                width=80),
                    rpm_line(state.get("rpm"), state.get("machine"), width=80,
                             hint=hint),
                    gear_badge(state.get("gear"), hint, ui.MID),
                ]), pad_v=10))

    # strip: the original band, everything at once, and the only layout that
    # takes height from the map rather than floating on it.
    return dash(lean_deg=lean, roll_deg=state.get("roll_deg"),
                pitch_deg=state.get("pitch_deg"), g_lat=state.get("g_lat"),
                g_long=state.get("g_long"), alt_m=state.get("alt_m"),
                climb_mps=state.get("climb_mps"),
                loudness=state.get("loudness"),
                loudness_peak=state.get("loudness_peak"),
                rpm=state.get("rpm"), bands=state.get("bands"),
                machine=state.get("machine"), imperial=state["imperial"])


def speed_and_limit(speed_ms, limit_mph, imperial=True):
    """The two numbers that belong together, boxed.

    Here rather than in ride.py so the block can be reused - Settings previews
    it, and a future Moto tab would want the same thing.
    """
    p = PAL()
    over = guidance.over_limit(speed_ms, limit_mph)
    return ft.Row(
        spacing=12, vertical_alignment=ft.CrossAxisAlignment.END, controls=[
            ui.readout(guidance.speed_text(speed_ms, imperial),
                       guidance.speed_unit(imperial),
                       colour=p["bad"] if over else p["text"]),
            ft.Container(expand=True),
            ft.Column(spacing=0, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[ui.value("—" if not limit_mph
                                         else str(limit_mph), ui.BIG),
                                ui.label("limit")])])
