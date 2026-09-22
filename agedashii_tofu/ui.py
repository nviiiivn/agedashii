"""Compatibility shim: ``ui`` is now two packages.

The look moved to ``agedashii_qi`` and the widget vocabulary to
``agedashii_washi.widgets``. This module re-exports both under their old
names so the seventeen pages that say ``from ..ui import PAL, card`` keep
working, unchanged, while the split is proven.

It is scaffolding, not architecture. When Moto, Watudo and Foodzen have
shipped and Home has been trimmed, the pages import from the packages
directly and this file goes.
"""
from agedashii_qi import *            # noqa: F401,F403
from agedashii_qi import S, State, PAL, RAD, GLOW, GLOW_STYLE, FOCUS_WIDTH  # noqa: F401
from agedashii_qi import apply_theme, crt_overlay  # noqa: F401
from agedashii_qi import (RADIUS_CHOICES, DEFAULT_RADIUS, RADIUS_LABELS,  # noqa: F401
                          CRT_INTENSITY_MIN, CRT_INTENSITY_MAX, CRT_MAX_OPACITY,
                          DEFAULT_CRT_INTENSITY)
from agedashii_washi.widgets import BRD, PAD, card, chip, dot, sub, title  # noqa: F401
