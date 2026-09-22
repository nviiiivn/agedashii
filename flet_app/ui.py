import flet as ft
from .theme_data import THEMES, THEME_NAMES, DEFAULT, LIGHT_THEMES

class State:
    theme = DEFAULT
    page = None
    # Flet SharedPreferences instance, set once in main(). Held here because a
    # service is unregistered as soon as it stops being strongly referenced.
    prefs = None
    # The three appearance knobs, mirrored here from prefs by
    # prefs.apply_runtime(). They live on S rather than being read out of
    # prefs on demand for the same reason `theme` does: this module is what
    # every page imports, and a `ui` that imported `prefs` back would tie the
    # drawing layer to the storage layer for the sake of three values.
    # RAD()/GLOW() are called once per control, so the read has to be an
    # attribute lookup and nothing more.
    radius = 8
    glow = False
    crt = False

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
def crt_overlay():
    """alola's `.crt-overlay` vignette, as a control to stack over the app.

    CSS gets this for free - `position: fixed; pointer-events: none` - and
    Flet has no CSS, so it is a Stack child instead: one full-bleed container
    painted with a radial gradient that runs clear to 60% and then to black
    at 40% opacity by the edge, which is exactly alola's stops.

    `ignore_interactions=True` is the entire safety story. It compiles to
    Flutter's IgnorePointer, which takes the widget out of hit testing
    altogether rather than merely leaving it without a handler. Without it a
    full-screen container sits on top of every button in the app and the
    whole thing looks dead while working perfectly - a bug that has shipped
    here once already, which is why the tests assert this flag, assert the
    overlay carries no handler of its own, and assert it is one control
    rather than a wrapper per page.
    """
    return ft.Container(
        expand=True, ignore_interactions=True,
        gradient=ft.RadialGradient(
            center=ft.Alignment(0, 0), radius=0.9,
            colors=[ft.Colors.TRANSPARENT, ft.Colors.TRANSPARENT,
                    ft.Colors.with_opacity(0.4, ft.Colors.BLACK)],
            stops=[0.0, 0.6, 1.0]))


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
