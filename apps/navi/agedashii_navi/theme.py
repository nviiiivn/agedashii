"""Navi's own look, which is not the dashboard's look.

Her words: "if you use moto you're going to just continue using styling and
theming that i don't want". So this app does not import `agedashii_qi` and
does not get Qi's twenty-four themes. It has eight, and every one of them is
designed around a single question that none of Qi's themes were asked:

    can you read this, at a glance, in a helmet, in the sun, at 60 mph?

That constraint is what makes these different rather than merely recoloured.
It buys:

* **Eight themes, not twenty-four.** Night, Day, Sunlight, Red night,
  Cyberpunk, Matrix, Pink and Cluster. Started as four - Night, Day,
  Sunlight, Cluster - and the rest were asked for by name. A theme picker
  you scroll is a theme picker you use while parked; these fit
  in one row of four taps, and Sunlight exists because a dark theme in direct
  sun is unreadable no matter how nice it looks indoors.
* **Two greys, not five.** Depth cues cost contrast, and contrast is the
  whole budget. There is a background, a raised surface, a hairline, and
  three weights of text - and nothing in between for decoration to hide in.
* **No glow, no CRT, no gradient behind text.** They are lovely on a desk
  and they are noise on a windscreen. The brand gradient survives in exactly
  one place, the identity pill, where it is not sitting behind a number
  anyone has to read.
* **A warning colour that is not the accent.** Off-route and over-the-limit
  have to be findable by colour alone, peripherally, so the accent never
  doubles as an alarm.

HOW A THEME IS SHAPED

Every theme is the same eleven keys, so any widget can be written against
`PAL()` without asking which theme is on. `bg`/`surface`/`border` are the
chrome; `text`/`text2`/`text3` are three weights of foreground, faintest
last; `accent` is the one interactive colour; `warn`, `bad` and `good` are
states, never decoration; `road` is what a route is drawn in on the map,
which is a separate decision from `accent` because a route has to stand off
tiles that are themselves already coloured.

The bike marker's two colours are hers and are not part of a theme: "A
PICTURE OF MY MOTORCYCLE OR A MOTOCRCYLE EMOJI INSTEAD OF A DOT OR CAR TO
REPRESENT MY LOCATION/PERSON. TWO CHOICES ONE YELLOW ONE BLACK." A marker
that changed colour with the theme would be a marker she cannot find by
colour, which defeats the point of choosing one.
"""
import flet as ft

# The Agedashii pill's three colours. Written here rather than imported,
# because this app must not depend on the dashboard; they are brand
# constants, not code. The pill is the only place a gradient is allowed.
ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

#: Her two marker colours, by name. Not themed - see the module docstring.
BIKE_COLOURS = {"yellow": "#ffd93b", "black": "#101418"}
#: Drawn around the marker body so a black bike is visible on dark tiles and
#: a yellow one on light tiles.
BIKE_OUTLINE = {"yellow": "#1a1200", "black": "#f2f4f8"}

THEMES = {
    # The default, and the one most rides happen in. Near-black rather than
    # black: an OLED true black makes the map's own dark tiles look like a
    # hole punched in the chrome.
    "night": {
        "bg": "#06080b", "surface": "#0e1319", "border": "#1e2733",
        "text": "#f2f6fb", "text2": "#9fb0c3", "text3": "#5f7183",
        "accent": "#39d0ff", "warn": "#ffb020", "bad": "#ff4d6d",
        "good": "#3ddc97", "road": "#39d0ff",
    },
    # Overcast daylight. Light, but not white - a white field behind white
    # tiles loses the edge of the map.
    "day": {
        "bg": "#eef1f5", "surface": "#ffffff", "border": "#c8d2de",
        "text": "#0b1016", "text2": "#41505f", "text3": "#6d7d8c",
        "accent": "#0068a8", "warn": "#8a5200", "bad": "#b3001f",
        "good": "#00684a", "road": "#0068a8",
    },
    # Direct sun, visor down. Maximum contrast, no mid-tones, and the
    # heaviest text weight the widgets offer. Ugly indoors on purpose.
    "sunlight": {
        "bg": "#ffffff", "surface": "#ffffff", "border": "#000000",
        "text": "#000000", "text2": "#1a1a1a", "text3": "#333333",
        "accent": "#0040a0", "warn": "#7a3d00", "bad": "#a00000",
        "good": "#00502f", "road": "#0040a0",
    },
    # Night vision. Deep red on black and nothing else - her "at night it's even
    # preferable to be all red / positive red only filters for night time".
    #
    # This is not a style. Rod cells carry night vision and are nearly blind to
    # deep red, so a red-on-black screen can be read without spending the dark
    # adaptation that took twenty minutes to build. Every colour here is a red,
    # including the ones that are green and blue in every other theme: a single
    # green "good" chip would undo the whole point, so `good` is a dim red and is
    # told apart by position and shape rather than by hue.
    "redshift": {
        "bg": "#000000", "surface": "#0a0000", "border": "#3d0d0d",
        "text": "#ff3b30", "text2": "#b32a22", "text3": "#7a1d17",
        "accent": "#ff5b4a", "warn": "#ff8f6a", "bad": "#ffd2c2",
        "good": "#c2402f", "road": "#ff5b4a",
    },
    # Magenta and cyan on near-black violet: the alola gradient pushed hard.
    # The two accents sit at opposite ends of the spectrum, which is what makes
    # a route and a warning tellable apart at a glance in the dark.
    "cyberpunk": {
        "bg": "#0a0612", "surface": "#140b22", "border": "#2e1b4a",
        "text": "#f0e6ff", "text2": "#b9a6d6", "text3": "#7d6b99",
        "accent": "#00f0ff", "warn": "#ff2bd6", "bad": "#ff4d6d",
        "good": "#5affc8", "road": "#00f0ff",
    },
    # One hue, green, and black behind it. The least distracting of the set and
    # the easiest to read at night after Red night - a single-hue screen gives
    # the eye nothing to re-focus on.
    "matrix": {
        "bg": "#000000", "surface": "#041008", "border": "#0e3d1f",
        "text": "#9dffbe", "text2": "#4fbf7a", "text3": "#2c7a4c",
        "accent": "#00ff66", "warn": "#d7ff4a", "bad": "#ff6b4a",
        "good": "#00ff66", "road": "#00ff66",
    },
    # The warm half of the brand gradient. Pink leads, orange warns.
    "pink": {
        "bg": "#120810", "surface": "#1e0f1a", "border": "#40202f",
        "text": "#ffe8f0", "text2": "#d69fb6", "text3": "#96687e",
        "accent": "#ff5e8a", "warn": "#ffb020", "bad": "#ff3b5c",
        "good": "#5affc8", "road": "#ff5e8a",
    },
    # An instrument cluster at night: amber on near-black, one colour, no
    # blue at all. Easiest of the set on dark-adapted eyes after Red night.
    "cluster": {
        "bg": "#080604", "surface": "#141009", "border": "#2e2413",
        "text": "#ffc857", "text2": "#c9973b", "text3": "#8a6a2c",
        "accent": "#ffb020", "warn": "#ffe08a", "bad": "#ff6b3d",
        "good": "#b8d84a", "road": "#ffb020",
    },
}

#: The dashboard's own look - alola.lol and Agedashii Qi - for the surfaces that
#: are NOT the map.
#:
#: Her question: "wanted to ask if we followed the agedashi/alola.css theming and
#: whatnot for the colors and menus and other display pages that AREN'T the
#: map/navigation view." The answer was no, so this is that palette, and
#: `PAL()` hands it to every surface except Ride.
#:
#: The split is the point. Sunlight legibility is a constraint on the map and
#: only on the map: Where to, Around, Rides, Settings and Help are read at a
#: stop, with the phone in a hand, where matching the rest of the family is
#: worth more than maximum contrast. The map keeps whichever of the four
#: high-contrast themes she picked.
ALOLA = {
    "bg": "#0d1117", "surface": "#161b22", "border": "#30363d",
    "text": "#e6edf3", "text2": "#9198a1", "text3": "#6e7681",
    "accent": CYAN, "warn": ORANGE, "bad": PINK,
    "good": "#3fb950", "road": CYAN,
}

THEME_NAMES = {"night": "Night", "day": "Day", "sunlight": "Sunlight",
               "cluster": "Cluster", "redshift": "Red night",
               "cyberpunk": "Cyberpunk", "matrix": "Matrix", "pink": "Pink"}

#: Which map rendering suits each theme, for the tile picker on the map. A
#: dark theme under bright tiles is the worst of both.
THEME_TILES = {"night": "dark", "day": "streets", "sunlight": "streets",
               "cluster": "dark", "redshift": "dark", "cyberpunk": "dark",
               "matrix": "dark", "pink": "dark"}

#: Themes that are dark. Read off a set rather than computed from the background,
#: because comparing hex strings happens to work for these and would quietly stop
#: the first time a palette's background began with a letter.
DARK_THEMES = {"night", "cluster", "redshift", "cyberpunk",
               "matrix", "pink"}

DEFAULT = "night"

#: The theme in force. A module-level dict rather than a bare global so that
#: `from .theme import S` and `theme.S` are the same object - the same reason
#: Washi parks its runtime state this way.
#: `surface` is which KIND of surface is being built - "map" or "chrome" - and
#: `chrome_alola` is whether the chrome follows the dashboard's palette.
#: `main.render` sets the first before each build; `main` copies the second out
#: of prefs at launch, because prefs imports this module and the reverse would be
#: a cycle.
S = {"theme": DEFAULT, "surface": "map", "chrome_alola": True}


def PAL(key=None):
    """The palette in force, or a named one. Never raises, never returns None.

    Three rules, in order:

    1. A name passed in always wins - that is how the theme picker previews a
       theme it is not yet wearing.
    2. Otherwise, a non-map surface gets `ALOLA` when `chrome_alola` is on, so
       Settings and Help look like the rest of the family.
    3. Otherwise the chosen theme, and an unknown name falls back to the default
       rather than raising - a theme can be deleted from this file while a device
       still has its name in storage, and that must not be a launch that dies
       before the first frame.
    """
    if key:
        return THEMES.get(key, THEMES[DEFAULT])
    if S.get("surface") != "map" and S.get("chrome_alola", True):
        return ALOLA
    return THEMES.get(S.get("theme") or DEFAULT, THEMES[DEFAULT])


def set_surface(kind):
    """Say which kind of surface is being built: "map" or "chrome"."""
    S["surface"] = "map" if kind == "map" else "chrome"
    return S["surface"]


def surface_kind():
    return S.get("surface", "map")


def set_theme(key):
    """Remember a theme in memory. Storage is prefs' job, not this module's."""
    if key in THEMES:
        S["theme"] = key
        return True
    return False


def is_dark(key=None):
    """Whether the palette in force is a dark one.

    Read off a named set rather than computed from the background colour.
    Comparing hex strings happens to order these four correctly and would
    quietly stop doing so the first time a theme is added whose background
    starts with a letter - a bug that shows up as the wrong scrollbar colour
    and is never traced back to here.
    """
    if key is None and S.get("surface") != "map" and S.get("chrome_alola", True):
        return True                      # ALOLA is a dark palette
    return (key or S.get("theme") or DEFAULT) in DARK_THEMES


def apply(page, key=None):
    """Put a theme on the page. Safe to call before the first frame.

    Sets the Flutter theme as well as our own palette, because the controls
    Navi does not draw itself - scroll bars, text selection handles, the
    ripple on a bare control - take their colour from there and would
    otherwise stay Material blue on an amber cluster.
    """
    if key:
        set_theme(key)
    p = PAL()
    page.theme_mode = (ft.ThemeMode.DARK if is_dark() else ft.ThemeMode.LIGHT)
    page.bgcolor = p["bg"]
    try:
        page.theme = ft.Theme(color_scheme_seed=p["accent"])
    except Exception:                    # noqa: BLE001 - older flet, no seed
        pass
    return p


# ------------------------------------------------------------------ helpers
# Local copies rather than an import from Washi's widgets, so that changing
# the dashboard's spacing cannot change this app's. They are three lines
# each; the coupling would cost more than the duplication.

def PAD(v=0, h=0):
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def BRD(colour, width=1):
    side = ft.BorderSide(width, colour)
    return ft.Border(side, side, side, side)


def RAD(r):
    return ft.BorderRadius.all(r)
