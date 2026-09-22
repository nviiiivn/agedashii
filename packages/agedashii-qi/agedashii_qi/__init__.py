"""Agedashii Qi - the look.

Twenty-four palettes, and the four knobs that shape how they are drawn:
corner radius, accent glow, the CRT overlay, and its intensity. Nothing in
here knows what a dashboard is. That is the point: a page asks for a
colour, not for permission.

alola.lol is the source of truth for any theme that shares a name with it.
Fifteen of the twenty-four are copied verbatim from ``~/www/shared/theme.js``
so that ``white`` here and ``white`` there are the same white; see
``themes`` for the derivation rule and for why you fix theme.js rather
than patching the table.

Typical use::

    from agedashii_qi import PAL, RAD, apply_theme

    apply_theme(page, "matrix")
    ft.Container(bgcolor=PAL()["surface"], border_radius=RAD(12))

Everything reads the current theme off the shared ``S``; set it through
``apply_theme`` rather than assigning, so the Flet page is updated with it.
"""

from .appearance import (
    CRT_INTENSITY_MAX,
    CRT_INTENSITY_MIN,
    DEFAULT_CRT_INTENSITY,
    DEFAULT_RADIUS,
    FOCUS_WIDTH,
    GLOW,
    GLOW_STYLE,
    PAL,
    RAD,
    RADIUS_CHOICES,
    RADIUS_LABELS,
    S,
    State,
    apply_theme,
    crt_overlay,
)
from .themes import DEFAULT, LIGHT_THEMES, THEME_NAMES, THEMES

__all__ = [
    "CRT_INTENSITY_MAX",
    "CRT_INTENSITY_MIN",
    "DEFAULT",
    "DEFAULT_CRT_INTENSITY",
    "DEFAULT_RADIUS",
    "FOCUS_WIDTH",
    "GLOW",
    "GLOW_STYLE",
    "LIGHT_THEMES",
    "PAL",
    "RAD",
    "RADIUS_CHOICES",
    "RADIUS_LABELS",
    "S",
    "State",
    "THEMES",
    "THEME_NAMES",
    "apply_theme",
    "crt_overlay",
]

__version__ = "1.0.0"
