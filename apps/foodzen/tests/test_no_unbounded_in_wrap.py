"""The grey box, checked in the built control tree rather than in the source.

This bug has now been "fixed" twice and shipped broken twice, so it gets a
test that looks at what is actually constructed.

What it was: on EatZen, "What you ate" rendered as a flat grey slab that
ran off the bottom of the screen and could not be scrolled past, putting
the totals, the sodium meter and the "Log it" button out of reach. The
page was unusable. Reported by the owner twice before it was believed.

Why it survived two fixes:

1. The first fix added ``wrap=True`` to stop the row standing four lines
   tall. Correct, but it left ``expand=True`` on the food field - and a
   wrapping Row is a Flutter ``Wrap``, which hands its children UNBOUNDED
   main-axis constraints. An Expanded child of an unbounded box has no
   width to take a share of, so the ``filled=True`` TextField painted its
   grey over an effectively infinite rectangle.

2. The second fix wrapped the field in ``ft.Container(width=190, ...)``.
   That looked right and did nothing: a Container is not a Flex, so it
   does not consume ``expand``, and ``field()`` sets
   ``expand = width is None`` - so calling it without a width left the
   TextField Expanded regardless of what was wrapped around it.

A source-level check does not catch case 2, because no ``expand=True``
appears at the call site at all; it arrives through a default argument.
Only the constructed tree shows it. Hence this test.
"""
import unittest
from unittest.mock import patch

import flet as ft

from agedashii_foodzen.pages import eatzen
from agedashii_washi import api as M


class _StubPage:
    def __init__(self):
        self.controls = []
        self.width = 411          # a Pixel 8 Pro in portrait, logical px
        self.height = 900

    def update(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _expanded_inside_a_wrap(root):
    """Every control carrying `expand` that sits under a wrapping Row."""
    found = []
    seen = set()

    def walk(control, in_wrap):
        if id(control) in seen:
            return
        seen.add(id(control))
        if in_wrap and getattr(control, "expand", None):
            found.append(f"{type(control).__name__}"
                         f"(expand={control.expand})")
        wraps = in_wrap or (isinstance(control, ft.Row)
                            and getattr(control, "wrap", False))
        children = getattr(control, "controls", None)
        if isinstance(children, list):
            for child in children:
                walk(child, wraps)
        content = getattr(control, "content", None)
        if content is not None and "flet" in type(content).__module__:
            walk(content, wraps)

    walk(root, False)
    return found


class TheEatZenRowIsBounded(unittest.TestCase):

    def _build(self):
        with patch.object(M, "STATUS_LIVE", return_value=True), \
             patch.object(M, "eatzen_parse", return_value={"items": []}):
            return eatzen.build_eatzen(_StubPage())

    def test_nothing_expands_inside_a_wrapping_row(self):
        """The grey slab, stated as the rule that prevents it."""
        offenders = _expanded_inside_a_wrap(self._build())
        self.assertEqual(
            offenders, [],
            "an Expanded child of a Wrap has unbounded width; a filled "
            "TextField there paints grey over the whole screen: "
            + ", ".join(offenders))

    def test_the_food_field_has_a_real_width(self):
        """The specific control that caused it, checked specifically.

        The rule above and the thing it protects can drift apart, so this
        pins the field itself: it must carry a width, which is also what
        makes field() leave expand off.
        """
        col = self._build()
        fields = []

        def walk(c):
            if isinstance(c, ft.TextField):
                fields.append(c)
            for child in (getattr(c, "controls", None) or []):
                walk(child)
            content = getattr(c, "content", None)
            if content is not None and "flet" in type(content).__module__:
                walk(content)

        walk(col)
        food = [f for f in fields if f.hint_text == "food"]
        self.assertTrue(food, "no food field was built at all")
        for f in food:
            self.assertEqual(f.width, eatzen.FOOD_FIELD_W)
            self.assertFalse(f.expand,
                             "the food field must not be Expanded - it sits "
                             "in a wrapping Row")


if __name__ == "__main__":
    unittest.main()
