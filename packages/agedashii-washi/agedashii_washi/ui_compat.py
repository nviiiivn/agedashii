"""The one import a page used to need, kept as one import.

Every surface in the dashboard was written against a single ``ui`` module
that carried both the palette and the widget vocabulary. Splitting those
into Qi and Washi was right - a theme has no opinion about cards - but it
would have meant editing the import line at the top of seventeen files to
say two things instead of one, for no benefit to anyone reading them.

So this is the join: Qi's appearance plus Washi's widgets, under the names
they already had. A page says ``from agedashii_washi.ui_compat import PAL,
card`` and carries on. It is a convenience, not a layer - nothing is
defined here, everything is re-exported - and a page is free to import
from Qi and Washi directly instead.
"""
from agedashii_qi import (  # noqa: F401
    CRT_INTENSITY_MAX, CRT_INTENSITY_MIN, DEFAULT_CRT_INTENSITY,
    DEFAULT_RADIUS, FOCUS_WIDTH, GLOW, GLOW_STYLE, PAL, RAD, RADIUS_CHOICES,
    RADIUS_LABELS, S, State, apply_theme, crt_overlay,
)
from agedashii_qi.themes import (  # noqa: F401
    DEFAULT, LIGHT_THEMES, THEME_NAMES, THEMES,
)
from .widgets import BRD, PAD, card, chip, dot, sub, title  # noqa: F401
