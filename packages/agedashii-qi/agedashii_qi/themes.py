"""Every colour the app paints with, and the rule that fills in the gaps.

Where the colours come from
---------------------------
Twenty-four themes, in two blocks.

The first fifteen are alola.lol's own, copied from ``~/www/shared/theme.js``
verbatim.  alola.lol is the source of truth for anything that shares a name
with it: ``white`` here and ``white`` there are the same white, so the site
and the app read as one system instead of two that happen to use the same
words.  Twelve of those names used to exist here with different, locally
chosen values (GitHub Dark for ``dark``, Catppuccin Mocha for ``catppuccin``,
and so on); those have been replaced.  If a theme's colours look wrong, fix
theme.js and re-derive, do not patch this file.

The remaining nine - nord, everforest, rosepine, monokai, ayu, kanagawa,
nightowl, one-dark, synthwave - have no alola equivalent, so they keep the
full palettes they always had and sit below the alola block.

The derivation rule
-------------------
alola stores eight values per theme::

    [bg, bg2, surface, border, text, text2, accent, green]

The app needs fifteen: the eight above plus ``text3`` and six semantic hues
(``red``, ``orange``, ``yellow``, ``cyan``, ``purple``, ``pink``) that pages
use for status - a gauge is green under 60%, yellow under 80%, red above.
``derive()`` computes the seven missing ones, the same way for every theme,
so adding a sixteenth alola theme means adding eight numbers and nothing
else:

1. ``text3`` is ``text2`` brought 30% of the way toward ``text``.  It is a
   third tier, not a dimmer one: blending toward ``bg`` instead produced
   values under 2:1 on the dark themes (the old hand-written matrix text3
   sat at 1.4:1 and was effectively invisible).

2. Each semantic hue is the theme's own ``accent`` rotated to a fixed hue,
   keeping the accent's saturation and lightness, so the set stays in the
   theme's register rather than importing six stock colours.  An accent with
   almost no saturation (alola's ``dark`` accent is plain ``#888``) would
   rotate to six identical greys, so saturation is floored at 0.60 first.

3. The rotated colour is then walked in lightness until it clears 4.5:1
   against all three grounds it can land on - ``bg``, ``bg2`` and
   ``surface``.  Hue and saturation never move.  This is what stops
   ``yellow`` from being a 2:1 smear on the white theme: it darkens to an
   olive that can actually be read, on the page and on a card.

``green`` is not derived - alola supplies it, and it is used as given, even
where alola chose a monochrome one (``white``'s green is near-black, and
``black``'s is near-white; on those themes "fine" is deliberately silent and
only trouble is coloured).

Keeping the website in step
---------------------------
dashbored-web/ carries the same palettes as CSS custom properties and as the
THEME_NAMES / THEME_COLORS / THEME_BG maps in app.js.  ``tools/gen-themes.py``
regenerates both from this module; run it rather than editing them by hand.
"""

# ── colour maths ────────────────────────────────────────────────────────────


def _hex(colour):
    """'#abc' -> '#aabbcc'; anything already six digits is returned as-is."""
    c = colour.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return "#" + c.lower()


def _rgb(colour):
    c = _hex(colour)[1:]
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _unrgb(rgb):
    return "#" + "".join("%02x" % max(0, min(255, round(v * 255))) for v in rgb)


def _channel_luminance(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(colour):
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    r, g, b = (_channel_luminance(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio between two colours, 1.0 to 21.0."""
    la, lb = luminance(a), luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _hsl(colour):
    r, g, b = _rgb(colour)
    hi, lo = max(r, g, b), min(r, g, b)
    l = (hi + lo) / 2
    if hi == lo:
        return 0.0, 0.0, l
    d = hi - lo
    s = d / (2 - hi - lo) if l > 0.5 else d / (hi + lo)
    if hi == r:
        h = ((g - b) / d) % 6
    elif hi == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0, s, l


def _unhsl(h, s, l):
    h = h % 360.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return _unrgb((r + m, g + m, b + m))


def _mix(a, b, t):
    """Blend `t` of `b` into `a`."""
    return _unrgb(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


# ── the rule ────────────────────────────────────────────────────────────────

#: Where each semantic colour sits on the wheel once the accent is rotated.
HUES = {"red": 352.0, "orange": 28.0, "yellow": 45.0,
        "cyan": 187.0, "purple": 268.0, "pink": 325.0}

#: Every derived colour must clear this against the theme's own background.
MIN_CONTRAST = 4.5

#: A near-grey accent rotates to six near-identical greys, so lift it first.
SAT_FLOOR = 0.60

#: How far text3 travels from text2 toward text.
TEXT3_MIX = 0.30


def legible(colour, grounds, target=MIN_CONTRAST):
    """Move `colour` in lightness until it clears `target` on every ground.

    `grounds` is every surface the colour can land on - the page, the panels
    behind it, the cards on top - because a status colour that reads on the
    page and vanishes on a card is still a bug. Hue and saturation are
    untouched, so the result still belongs to the theme; only how light it
    is changes.
    """
    h, s, l = _hsl(colour)
    darkest = min(grounds, key=luminance)
    step = 0.02 if contrast("#ffffff", darkest) >= contrast("#000000", darkest) else -0.02
    for _ in range(60):
        if min(contrast(colour, g) for g in grounds) >= target:
            break
        nl = min(0.98, max(0.02, l + step))
        if nl == l:
            break
        l = nl
        colour = _unhsl(h, s, l)
    return colour


def derive(eight):
    """Expand alola's eight values into the fifteen the app paints with."""
    bg, bg2, surface, border, text, text2, accent, green = (_hex(c) for c in eight)
    pal = {"bg": bg, "bg2": bg2, "surface": surface, "border": border,
           "text": text, "text2": text2, "accent": accent, "green": green,
           "text3": _mix(text2, text, TEXT3_MIX)}
    h, s, l = _hsl(accent)
    s = max(s, SAT_FLOOR)
    grounds = (bg, bg2, surface)
    for name, hue in HUES.items():
        pal[name] = legible(_unhsl(hue, s, l), grounds)
    return pal


# ── alola.lol's fifteen, verbatim from ~/www/shared/theme.js ────────────────
# [bg, bg2, surface, border, text, text2, accent, green]

ALOLA = {
    'white': ['#ffffff', '#f5f5f5', '#ececec', '#d4d4d4', '#0a0a0a', '#555555', '#e11d48', '#111111'],
    'red': ['#1a0004', '#2a0008', '#3a0010', '#7a2030', '#ffe0e4', '#cc9999', '#ff2a44', '#ff7a66'],
    'black': ['#000000', '#0a0a0a', '#161616', '#2e2e2e', '#f5f5f5', '#888888', '#ff2a44', '#e5e5e5'],
    'dark': ['#0a0a0a', '#111', '#1a1a1a', '#2a2a2a', '#e0e0e0', '#666', '#888', '#0f0'],
    'light': ['#fafafa', '#f0f0f4', '#e6e6ea', '#d4d4da', '#1a1a1e', '#6b7280', '#4f46e5', '#16a34a'],
    'sepia': ['#fdf6e3', '#f5e6c8', '#efe0b8', '#d8c8a0', '#433422', '#7a6a5a', '#b58900', '#589500'],
    'cyberpunk': ['#0a0020', '#120030', '#1a0045', '#6633cc', '#f0e0ff', '#8877bb', '#ff00ff', '#00ffaa'],
    'ocean': ['#0a1628', '#0f1f3a', '#152a4a', '#1e3a5f', '#e0f0ff', '#6080b0', '#4fc3ff', '#4caf88'],
    'dracula': ['#282a36', '#1e1f2b', '#21222c', '#44475a', '#f8f8f2', '#6272a4', '#bd93f9', '#50fa7b'],
    'gruvbox': ['#282828', '#1d2021', '#32302f', '#504945', '#ebdbb2', '#928374', '#fabd2f', '#b8bb26'],
    'tokyo': ['#1a1b26', '#16161e', '#1f2335', '#363b54', '#c0d0ff', '#565f89', '#7aa2f7', '#9ece6a'],
    'matrix': ['#000', '#001100', '#002200', '#003300', '#00ff00', '#006600', '#00ff00', '#00ff00'],
    'solarized': ['#002b36', '#073642', '#093b46', '#1a5a6a', '#d0d0a0', '#586e75', '#b58900', '#859900'],
    'pastel': ['#1a1a2e', '#16213e', '#1f2a47', '#3a4a6a', '#f0e0ff', '#a0a0c0', '#ff88cc', '#88dd88'],
    'catppuccin': ['#1e1e2e', '#181825', '#232340', '#45475a', '#cdd6f4', '#6c7086', '#89b4fa', '#a6e3a1'],
}

ALOLA_NAMES = {
    'white': 'White', 'red': 'Red', 'black': 'Black', 'dark': 'Dark',
    'light': 'Light', 'sepia': 'Sepia', 'cyberpunk': 'Cyberpunk',
    'ocean': 'Ocean', 'dracula': 'Dracula', 'gruvbox': 'Gruvbox',
    'tokyo': 'Tokyo Night', 'matrix': 'Matrix', 'solarized': 'Solarized',
    'pastel': 'Pastel', 'catppuccin': 'Catppuccin',
}

# ── the nine with no alola equivalent, kept as they were ────────────────────

LOCAL = {
    'nord': {
        'bg': '#2e3440',
        'bg2': '#242933',
        'surface': '#2e3440',
        'border': 'rgba(136,192,208,0.18)',
        'text': '#eceff4',
        'text2': '#7b88a1',
        'text3': '#9099aa',
        'accent': '#88c0d0',
        'green': '#a3be8c',
        'yellow': '#ebcb8b',
        'red': '#bf616a',
        'orange': '#d08770',
        'purple': '#b48ead',
        'pink': '#b48ead',
        'cyan': '#8fbcbb',
    },
    'everforest': {
        'bg': '#2d353b',
        'bg2': '#272e33',
        'surface': '#343f44',
        'border': 'rgba(167,192,128,0.18)',
        'text': '#d3c6aa',
        'text2': '#7a8478',
        'text3': '#9da9a0',
        'accent': '#a7c080',
        'green': '#a7c080',
        'yellow': '#dbbc7f',
        'red': '#e67e80',
        'orange': '#e69875',
        'purple': '#d699b6',
        'pink': '#d699b6',
        'cyan': '#7fbbb3',
    },
    'rosepine': {
        'bg': '#191724',
        'bg2': '#1f1d2e',
        'surface': '#26233a',
        'border': 'rgba(235,188,186,0.18)',
        'text': '#e0def4',
        'text2': '#908caa',
        'text3': '#6e6a86',
        'accent': '#ebbcba',
        'green': '#31748f',
        'yellow': '#f6c177',
        'red': '#eb6f92',
        'orange': '#ebbcba',
        'purple': '#c4a7e7',
        'pink': '#eb6f92',
        'cyan': '#9ccfd8',
    },
    'monokai': {
        'bg': '#272822',
        'bg2': '#1e1f1c',
        'surface': '#2e2f2a',
        'border': 'rgba(166,226,46,0.18)',
        'text': '#f8f8f2',
        'text2': '#75715e',
        'text3': '#a59f85',
        'accent': '#a6e22e',
        'green': '#a6e22e',
        'yellow': '#e6db74',
        'red': '#f92672',
        'orange': '#fd971f',
        'purple': '#ae81ff',
        'pink': '#f92672',
        'cyan': '#66d9ef',
    },
    'ayu': {
        'bg': '#1e2430',
        'bg2': '#171b24',
        'surface': '#242b38',
        'border': 'rgba(115,195,231,0.18)',
        'text': '#d5dbe0',
        'text2': '#617187',
        'text3': '#6e7e94',
        'accent': '#73c3e7',
        'green': '#a1c95c',
        'yellow': '#ffd173',
        'red': '#f07178',
        'orange': '#f8a84c',
        'purple': '#d4a0ff',
        'pink': '#f07178',
        'cyan': '#73c3e7',
    },
    'kanagawa': {
        'bg': '#1f1f2e',
        'bg2': '#181825',
        'surface': '#252540',
        'border': 'rgba(121,199,204,0.18)',
        'text': '#dcd7ba',
        'text2': '#8481a5',
        'text3': '#72709e',
        'accent': '#7fb4ca',
        'green': '#98bb6c',
        'yellow': '#c0a36e',
        'red': '#c34043',
        'orange': '#ffa066',
        'purple': '#957fb8',
        'pink': '#d27e99',
        'cyan': '#7fb4ca',
    },
    'nightowl': {
        'bg': '#011627',
        'bg2': '#0a1f35',
        'surface': '#112a45',
        'border': 'rgba(130,180,210,0.18)',
        'text': '#d6deeb',
        'text2': '#637aa6',
        'text3': '#7f9bc0',
        'accent': '#82aaff',
        'green': '#22da6e',
        'yellow': '#ffeb95',
        'red': '#ff5874',
        'orange': '#f78c6c',
        'purple': '#c792ea',
        'pink': '#ff5874',
        'cyan': '#7fdbca',
    },
    'one-dark': {
        'bg': '#282c34',
        'bg2': '#21252b',
        'surface': '#2c313c',
        'border': 'rgba(97,175,239,0.18)',
        'text': '#abb2bf',
        'text2': '#5c6370',
        'text3': '#7f848e',
        'accent': '#61afef',
        'green': '#98c379',
        'yellow': '#e5c07b',
        'red': '#e06c75',
        'orange': '#d19a66',
        'purple': '#c678dd',
        'pink': '#e06c75',
        'cyan': '#56b6c2',
    },
    'synthwave': {
        'bg': '#262335',
        'bg2': '#1e1a2b',
        'surface': '#342f4a',
        'border': 'rgba(255,121,198,0.25)',
        'text': '#e2e0f0',
        'text2': '#8b86a8',
        'text3': '#a9a2c9',
        'accent': '#ff79c6',
        'green': '#3ee849',
        'yellow': '#f3e877',
        'red': '#fe4450',
        'orange': '#ff9f43',
        'purple': '#b388ff',
        'pink': '#ff79c6',
        'cyan': '#36f9f6',
    },
}

LOCAL_NAMES = {
    'nord': 'Nord', 'everforest': 'Everforest', 'rosepine': 'Rosé Pine',
    'monokai': 'Monokai', 'ayu': 'Ayu Mirage', 'kanagawa': 'Kanagawa',
    'nightowl': 'Night Owl', 'one-dark': 'One Dark', 'synthwave': 'Synthwave',
}

# ── the list the app offers, alola's fifteen first, in alola's order ────────

KEYS = list(ALOLA) + list(LOCAL)

THEMES = {k: derive(ALOLA[k]) for k in ALOLA}
THEMES.update(LOCAL)

THEME_NAMES = dict(ALOLA_NAMES)
THEME_NAMES.update(LOCAL_NAMES)

#: alola.lol's own default, so a browser and the app open on the same thing.
DEFAULT = 'white'

#: Themes whose background is light. Material needs to be told, and asking the
#: background is more reliable than a hand-kept list that silently goes stale
#: when a theme changes side - alola's `pastel` is dark, ours used to be light.
LIGHT_THEMES = frozenset(
    k for k, p in THEMES.items()
    if contrast('#000000', p['bg']) > contrast('#ffffff', p['bg']))
