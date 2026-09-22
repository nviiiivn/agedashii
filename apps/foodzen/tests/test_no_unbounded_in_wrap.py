"""The grey box, checked in the built control tree rather than in the source.

This bug has now been "fixed" twice and shipped broken twice, so it gets a
test that looks at what is actually constructed.

What it was: on EatZen, "What you ate" rendered as a flat grey slab that
ran off the bottom of the screen and could not be scrolled past, putting
the totals, the sodium meter and the "Log it" button out of reach. The
page was unusable. Reported by the owner twice before it was believed.

Why it survived three fixes, and what it actually was
-----------------------------------------------------
It was never about width. A Flutter ``Wrap`` hands its children unbounded
constraints on BOTH axes - the cross axis too, because it sizes each run
to its tallest child - and a Material ``TextField`` with ``filled=True``
and no height expands to fill whatever it is given. That is the bug, in
one sentence, and all three attempts reasoned about the wrong axis:

1. Added ``wrap=True`` to stop the row standing four lines tall. Correct,
   and it left ``expand=True`` in place.
2. Removed the ``expand``. Also correct, also not the cause.
3. Wrapped the field in ``ft.Container(width=190, ...)``. Did nothing
   twice over: a Container is not a Flex so it does not consume
   ``expand``, and a width does not constrain a height.

What settled it was an isolating probe built on a real emulator: a
wrapping Row of plain Texts renders fine, the same Row with ONE filled
TextField in it produces the slab. No amount of reading the tree would
have shown that, because the tree was correct every time.

The lesson the tests encode: ``test_nothing_expands_inside_a_wrapping_row``
keeps the shape clean, but
``test_every_filled_field_in_a_wrap_has_a_height`` is the one that guards
the actual defect.
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

    def test_every_filled_field_in_a_wrap_has_a_height(self):
        """The actual cause, and the one both earlier fixes missed.

        A Wrap hands its children unbounded constraints on BOTH axes - the
        cross axis too, because it sizes each run to its tallest child. A
        Material TextField with filled=True and no height expands to fill
        that, and paints its grey fill past the bottom of the screen.

        Both previous fixes reasoned about WIDTH: one removed expand=True,
        the other wrapped the field in a fixed-width Container. Neither
        constrains a height, so the slab survived both and shipped twice.
        An isolating probe on a real emulator is what settled it - a
        wrapping Row of plain Texts renders fine; the same Row with one
        filled TextField in it produces the slab.

        A height on the TextField itself is NOT enough, which is the fourth
        thing this bug survived. An APK built with `ft.TextField(height=40)`
        was installed on the emulator and was still the full grey slab:
        Flet feeds that height into the field's own InputDecoration instead
        of wrapping the control in a box, so nothing ever tells the outer
        render box how tall it may be. A Container around the field does
        constrain it, and that is what this checks for - a height anywhere
        on the path between the wrapping Row and the field.
        """
        unbounded = []
        seen = set()

        def walk(control, in_wrap, bounded):
            if id(control) in seen:
                return
            seen.add(id(control))
            if (in_wrap and not bounded
                    and isinstance(control, ft.TextField)
                    and getattr(control, "filled", False)):
                unbounded.append(
                    f"TextField(hint={control.hint_text!r}) has no bounded "
                    f"height between it and the wrapping Row")
            wraps = in_wrap or (isinstance(control, ft.Row)
                                and getattr(control, "wrap", False))
            # A height on an ancestor bounds everything below it. Reset to
            # False when leaving the wrap is unnecessary - `wraps` only ever
            # turns on - but `bounded` must only count heights seen at or
            # below the wrap, which is what threading it through does.
            below = bounded or bool(getattr(control, "height", None))
            for child in (getattr(control, "controls", None) or []):
                walk(child, wraps, below if wraps else False)
            content = getattr(control, "content", None)
            if content is not None and "flet" in type(content).__module__:
                walk(content, wraps, below if wraps else False)

        walk(self._build(), False, False)
        self.assertEqual(unbounded, [], "\n".join(unbounded))

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
