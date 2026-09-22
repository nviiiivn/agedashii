"""Appearance: the palette in use, and every knob that shapes it.

Split out of the dashboard's ``ui`` module, which had grown into two
unrelated halves - what the app looks like, and what it is built out of.
This is the first half. The widgets went to ``agedashii_washi.widgets``,
which imports this rather than the other way round: a theme has no opinion
about cards, and a card cannot be drawn without a theme.

Everything here reads its current value off the module-level ``S``. That
indirection is deliberate and is explained at ``State`` - RAD() and GLOW()
are called once per control, so the read has to be an attribute lookup and
nothing more.
"""
import flet as ft

from .themes import THEMES, THEME_NAMES, DEFAULT, LIGHT_THEMES

class State:
    theme = DEFAULT
    page = None
    # Flet SharedPreferences instance, set once in main(). Held here because a
    # service is unregistered as soon as it stops being strongly referenced.
    prefs = None
    # The four appearance knobs, mirrored here from prefs by
    # prefs.apply_runtime(). They live on S rather than being read out of
    # prefs on demand for the same reason `theme` does: this module is what
    # every page imports, and a `ui` that imported `prefs` back would tie the
    # drawing layer to the storage layer for the sake of four values.
    # RAD()/GLOW() are called once per control, so the read has to be an
    # attribute lookup and nothing more.
    radius = 8
    glow = False
    crt = False
    crt_intensity = 40  # matches DEFAULT_CRT_INTENSITY below
    # Off means "animate". Kept here beside the other appearance knobs
    # rather than in motion.py, because prefs.apply_runtime() mirrors this
    # whole block onto S in one place and a fifth knob living somewhere
    # else is how the other four would drift out of sync with it.
    reduce_motion = False

S = State()

def PAL():
    return THEMES[S.theme]


# ---------------------------------------------------------------- radius
# Every rounded corner in the app is written as its *shipped* radius and put
# through RAD(), so there is exactly one number - the owner's - behind all of
# them. Before this they were 87 independent literals and "make the corners
# square" was not something anyone could do.
#
# The setting scales rather than replaces. Replacing would flatten a
# hierarchy the design already has on purpose - a 20px chip and a 6px inner
# button are not the same shape - and 8, the value that scales by 1,
# reproduces the shipped look exactly, so the feature changes nothing at all
# until she moves it.
RADIUS_CHOICES = (0, 3, 8, 12, 16)
DEFAULT_RADIUS = 8
RADIUS_LABELS = {
    0: "Square - no rounding at all",
    3: "Slight - 3px",
    8: "Rounded - 8px (default)",
    12: "Soft - 12px",
    16: "Very soft - 16px",
}


def RAD(nominal=DEFAULT_RADIUS):
    """The corner radius to draw, for a surface shipped at `nominal` px.

    Zero is genuinely zero. The `max(1, ...)` floor below keeps a 2px
    progress bar from vanishing at small scales, and is deliberately not
    reached at zero - otherwise "square" would come out as "1px everywhere"
    and she would have asked for square for nothing.

    Circles are not routed through here. A status dot written as
    `border_radius=size / 2` is a circle by geometry, not a rounded corner;
    squaring it would turn the machine dots into a row of pixels rather than
    making anything less rounded.
    """
    base = S.radius
    if base <= 0:
        return 0
    if nominal == DEFAULT_RADIUS:
        return base
    # Half-up rather than round(), which is banker's: round(7.5) is 8 but
    # round(6.5) is 6, and a radius table that alternates is not worth the
    # surprise when someone reads the numbers back.
    return max(1, int(nominal * base / DEFAULT_RADIUS + 0.5))


# ---------------------------------------------------------------- glow
# alola.css hangs an accent-coloured shadow off its headings, its hovered
# logo, its hero title and its focused controls - `text-shadow: 0 0 10px
# var(--accent)` and its relatives. This is that, for Flet, off by default.
#
# The colour is always PAL()["accent"], never a constant, so it follows
# whichever theme is on instead of glowing yellow over a blue one.
def GLOW(blur=10, opacity=0.45, color=None):
    """alola's accent shadow as a BoxShadow, or None when it is switched off.

    None rather than a transparent shadow on purpose: `shadow=None` is what
    Flet already expects, so a caller can pass this straight into a
    Container it builds either way, with no branch of its own.
    """
    if not S.glow:
        return None
    return ft.BoxShadow(blur_radius=blur, spread_radius=0,
                        color=ft.Colors.with_opacity(opacity,
                                                     color or PAL()["accent"]))


def GLOW_STYLE(blur=10, opacity=0.55, color=None):
    """The same shadow for text - alola's `text-shadow: 0 0 10px var(--accent)`."""
    shadow = GLOW(blur, opacity, color)
    return ft.TextStyle(shadow=shadow) if shadow else None


def FOCUS_WIDTH():
    """How thick a focused input draws the accent border it already has.

    Flet's TextField takes no shadow, so a focused field cannot literally
    carry alola's `box-shadow`. What it can do is weigh that border, which
    is the same signal by other means; the accent tint behind it comes from
    the theme's focus colour, set in apply_theme below.
    """
    return 2 if S.glow else 1


# ---------------------------------------------------------------- CRT
# alola's `.crt-overlay` was one line - a radial gradient running clear to
# 60% and then to 40%-opacity black at the edge. That is a vignette and
# nothing else, and it read like one: "the crt looks a bit off and too dark
# and not really crt as much as just a harsh vignette... the crt actually
# had some good ditherish halftone effect". A convincing CRT is layered -
# this stacks three things a real tube actually did, instead of the one
# thing it happened to share with every darkened-corners effect ever:
#   - scanlines, the signature element and the one most obviously missing
#   - a halftone dot suggesting a shadow mask / aperture grille
#   - a much gentler vignette than before, plus a faint centre glow so the
#     whole thing reads as glowing rather than simply darker
#
# Flet's gradients are positioned in the box's own -1..1 space, not device
# pixels - fine for the vignette, which is meant to scale with the screen,
# wrong for a scanline, which should not. The two textures are instead tiny
# repeating PNGs, built once below by hand (struct + zlib only, so this
# carries no Pillow dependency at runtime) and tiled with
# ft.ImageRepeat.REPEAT, which gives them a period in fixed logical pixels
# regardless of screen size: a 1x4 tile for the scanlines, a 4x4 tile for
# the halftone dot. Both use a 4px period rather than 1-2px on purpose - a
# tight period is exactly what moires once a compositor scales it, and 4px
# held up clean at phone and desktop width on the website, which draws the
# same period in CSS (see dashbored-web/app.css).
#
# The three layers are nested containers, each the next one's `content`,
# rather than siblings in a Stack: a parent's decoration paints before its
# child's, which paints over it, giving the same back-to-front order a
# Stack would without the Stack itself adding a fourth control to the walk
# in CrtOverlayDoesNotEatTaps.test_every_control_in_the_overlay_ignores_interactions.
# `ignore_interactions=True` is the entire safety story, and every one of
# the three carries it independently rather than only the outermost - "a
# single missed flag kills input for the whole app" is why this is a
# property on each container and not a wrapper applied once at the top. A
# full-screen container without it sits on top of every button and the
# whole app looks dead while working perfectly; that bug has shipped here
# once already.
#
# Intensity is one number, S.crt_intensity, 0-100, and it scales all three
# layers together through exactly one lever: the outermost container's
# `opacity`. Flet's opacity wraps a control's whole subtree in a single
# Flutter Opacity, so every nested decoration dims by the same fraction
# without needing its own multiplier - "must scale every layer together" is
# free this way rather than three numbers kept in sync by hand. Every
# colour below is tuned to look right at 100%; 0% is fully invisible, which
# is correct for the bottom of a slider, not a bug to work around.
import base64 as _b64
import struct as _struct
import zlib as _zlib


def _tile_png_data_uri(width, height, pixels):
    """A minimal RGBA PNG as a base64 data URI, built without Pillow.

    `pixels` is `width * height` (r, g, b, a) tuples, row-major. This only
    ever runs against the two tiny textures below, both built once at
    import time, so the unoptimised per-pixel loop costs nothing anyone
    will notice.
    """
    def chunk(tag, data):
        return (_struct.pack(">I", len(data)) + tag + data +
                _struct.pack(">I", _zlib.crc32(tag + data) & 0xffffffff))
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0: None
        for x in range(width):
            raw += bytes(pixels[y * width + x])
    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", _struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
           chunk(b"IDAT", _zlib.compress(bytes(raw), 9)) +
           chunk(b"IEND", b""))
    return "data:image/png;base64," + _b64.b64encode(png).decode("ascii")


# One dark row, three transparent - a 4-logical-pixel vertical period.
# VERTICAL lines: a 4x1 tile, one dark COLUMN of four. It was a 1x4 tile -
# one dark row - which draws the horizontal scanlines of a broadcast CRT.
# Her call, and it is the right one for a phone held upright: vertical
# stripes read as an aperture grille (a Trinitron) rather than as interlace
# banding, and they do not fight the horizontal rhythm of a list of cards.
_SCANLINE_TILE = _tile_png_data_uri(4, 1, [
    (0, 0, 0, 140), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])

# One dot in sixteen - the same 4px period on both axes, at low enough
# density that it reads as texture rather than a visible grid.
_HALFTONE_TILE = _tile_png_data_uri(4, 4, [
    (0, 0, 0, 100), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0),
    (0, 0, 0, 0),   (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0),
    (0, 0, 0, 0),   (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0),
    (0, 0, 0, 0),   (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])

CRT_INTENSITY_MIN = 0
CRT_INTENSITY_MAX = 100

# What 100 on the slider is actually worth. "the current 30% should be the
# NEW 100%.. so essentially intensity focuses on the area only between
# 0-30%" - so the whole slider now spans what used to be its bottom third.
# The old scale spent two thirds of its travel in a range she called far
# too intense, which made the control useless: everything above about 30
# looked the same kind of wrong.
#
# Applied as a multiplier on the one opacity lever (see crt_overlay), so
# all three layers still scale together and nothing has to be retuned.
CRT_MAX_OPACITY = 0.30

# Tasteful, not maximum. On the new scale this is 0.40 * 0.30 = 12% real
# opacity - about where the old slider's 40 used to sit before the ceiling
# came down, which is roughly what she was happy with.
DEFAULT_CRT_INTENSITY = 40


def crt_overlay():
    """The layered CRT effect as a control to stack over the app.

    Scaled by S.crt_intensity (0-100); see the module comment above for the
    layering and the intensity lever. `ignore_interactions=True` on every
    nested container is the entire safety story.
    """
    t = max(CRT_INTENSITY_MIN,
            min(CRT_INTENSITY_MAX, S.crt_intensity)) / 100 * CRT_MAX_OPACITY

    halftone = ft.Container(
        expand=True, ignore_interactions=True,
        image=ft.DecorationImage(src=_HALFTONE_TILE, repeat=ft.ImageRepeat.REPEAT))

    scanlines = ft.Container(
        expand=True, ignore_interactions=True, content=halftone,
        image=ft.DecorationImage(src=_SCANLINE_TILE, repeat=ft.ImageRepeat.REPEAT))

    return ft.Container(
        expand=True, ignore_interactions=True, content=scanlines, opacity=t,
        gradient=ft.RadialGradient(
            center=ft.Alignment(0, 0), radius=0.9,
            colors=[ft.Colors.with_opacity(0.06, ft.Colors.WHITE),
                    ft.Colors.TRANSPARENT,
                    ft.Colors.with_opacity(0.16, ft.Colors.BLACK)],
            stops=[0.0, 0.55, 1.0]))


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
            bgcolor=p["bg2"],
            # The pill behind the selected bottom-bar tab. Glow on, it burns
            # twice as bright - which is how "the active nav item glows"
            # reaches a NavigationBar, a control that takes no shadow and
            # builds its own destinations.
            indicator_color=ft.Colors.with_opacity(0.30 if S.glow else 0.15, p["accent"]),
            label_text_style=ft.TextStyle(size=11, color=p["text2"]),
        ),
        # Material reads these for the wash it paints behind a focused or
        # hovered control. Setting them here is what gets the glow onto the
        # app's 42 text fields without touching 42 call sites.
        focus_color=ft.Colors.with_opacity(0.18, p["accent"]) if S.glow else None,
        hover_color=ft.Colors.with_opacity(0.10, p["accent"]) if S.glow else None,
    )
    page.update()
