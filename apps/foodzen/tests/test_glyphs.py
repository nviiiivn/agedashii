"""The glyphs: the right picture, or an honest blank - never a near-miss.

Two rules are being defended here, and both of them are about trust rather
than decoration.

**A wrong emoji is worse than no emoji.** On a grocery list or a sodium row a
picture reads as a claim about what the row *is*. An orange next to
"grapefruit", or a potato next to "sweet potato", is the same class of quiet
lie as a nutrition figure nobody looked up. So the table is allowed to be
incomplete, matching is by whole word, and the longest phrase wins.

**Emoji are content and icons are chrome.** An emoji cannot be themed - it
carries its own colours - so it may never stand in for a UI mark, and a
Material icon may never stand in for a food. `test_the_two_kinds_never_mix`
is the whole of that rule as a check.

Run them::

    /home/nvii/venvs/flet/bin/python -m unittest discover -s apps/foodzen/tests -t .
"""
import pathlib
import sys
import unittest

APP = pathlib.Path(__file__).resolve().parent.parent      # apps/foodzen
REPO = APP.parent.parent                                  # the checkout root
for path in (str(REPO), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import flet as ft                                          # noqa: E402
from agedashii_qi import PAL, S, apply_theme               # noqa: E402

from agedashii_foodzen import glyphs                       # noqa: E402


class _StubPage:
    def update(self):
        pass


class TheFoodTable(unittest.TestCase):
    def test_the_common_ones_are_the_obvious_ones(self):
        """The list she actually writes, with the glyph anyone would expect."""
        for name, want in (("banana", "🍌"), ("egg", "🥚"), ("chicken", "🍗"),
                           ("salad", "🥗"), ("bread", "🍞"), ("milk", "🥛"),
                           ("coffee", "☕"), ("apple", "🍎"), ("rice", "🍚"),
                           ("cheese", "🧀"), ("pizza", "🍕"), ("water", "💧")):
            with self.subTest(food=name):
                self.assertEqual(glyphs.food_emoji(name), want)

    def test_case_and_plurals_and_quantities_do_not_matter(self):
        """"2 Eggs" and "egg" are the same food to anyone but a parser."""
        for name in ("Eggs", "EGG", "2 eggs", "eggs ", "3 large eggs"):
            with self.subTest(food=name):
                self.assertEqual(glyphs.food_emoji(name), "🥚")
        self.assertEqual(glyphs.food_emoji("cherries"), "🍒")
        self.assertEqual(glyphs.food_emoji("tomatoes"), "🍅")

    def test_a_real_eatzen_row_still_matches(self):
        """The food names this app actually receives are not single words.

        "chicken breast, meat and skin" is a row straight out of the food
        table; a lookup that only handled bare nouns would show a plate for
        most of a logged meal.
        """
        self.assertEqual(glyphs.food_emoji("chicken breast, meat and skin"), "🍗")
        self.assertEqual(glyphs.food_emoji("1 tbsp butter"), "🧈")
        self.assertEqual(glyphs.food_emoji("4oz portobello"), "🍄")

    def test_the_longest_phrase_wins(self):
        """The rule that keeps a near-miss from beating the right answer."""
        self.assertEqual(glyphs.food_emoji("sweet potato"), "🍠")
        self.assertNotEqual(glyphs.food_emoji("sweet potato"),
                            glyphs.food_emoji("potato"))
        self.assertEqual(glyphs.food_emoji("peanut butter"), "🥜")
        self.assertNotEqual(glyphs.food_emoji("peanut butter"),
                            glyphs.food_emoji("butter"))
        self.assertEqual(glyphs.food_emoji("milk chocolate"), "🍫")
        self.assertEqual(glyphs.food_emoji("chocolate milk"), "🥛")

    def test_a_word_is_never_matched_inside_another_word(self):
        """"grapefruit" is not grapes, and nothing in the table says it is.

        Substring matching is the fastest way to produce exactly the wrong
        answers this module exists to avoid, so the lookup compares whole
        words only. These three are the ones that would bite first.
        """
        self.assertIsNone(glyphs.food_emoji_or_none("grapefruit"))
        self.assertEqual(glyphs.food_emoji("eggplant"), "🍆")   # not the egg
        self.assertEqual(glyphs.food_emoji("pineapple"), "🍍")  # not the apple

    def test_an_unknown_food_gets_the_neutral_plate_and_says_nothing_more(self):
        for name in ("quinoa", "kombucha", "zzzz", "a thing in a jar"):
            with self.subTest(food=name):
                self.assertEqual(glyphs.food_emoji(name), glyphs.FALLBACK)
                self.assertIsNone(glyphs.food_emoji_or_none(name))

    def test_an_empty_name_is_not_a_food(self):
        for name in ("", "   ", None, 0):
            with self.subTest(food=name):
                self.assertIsNone(glyphs.food_emoji_or_none(name))
                self.assertEqual(glyphs.food_emoji(name), glyphs.FALLBACK)

    def test_the_fallback_can_be_asked_for_by_name(self):
        """A caller that would rather draw nothing must be able to say so."""
        self.assertEqual(glyphs.food_emoji("quinoa", default=""), "")

    def test_the_label_helper_leaves_an_unknown_name_alone(self):
        """231 catalogue buttons must not all become the same plate."""
        self.assertEqual(glyphs.food_label("banana"), "🍌 banana")
        self.assertEqual(glyphs.food_label("quinoa"), "quinoa")

    def test_every_entry_really_is_an_emoji(self):
        """A stray letter in the table would print as a letter on the row."""
        for name, glyph in glyphs.FOODS.items():
            with self.subTest(food=name):
                self.assertTrue(glyph, f"{name} has no glyph")
                self.assertTrue(all(ord(ch) > 0x2000 for ch in glyph),
                                f"{name} maps to {glyph!r}, which is text")

    def test_the_table_is_keyed_in_lower_case(self):
        for name in glyphs.FOODS:
            with self.subTest(food=name):
                self.assertEqual(name, name.lower())


class TheChromeIcons(unittest.TestCase):
    ICON_NAMES = [n for n in dir(glyphs) if n.startswith("ICON_")]

    def test_there_are_marks_for_sodium_and_for_blood_pressure(self):
        """She tracks two numbers; each gets its own recognisable mark."""
        self.assertTrue(self.ICON_NAMES)
        self.assertIsNotNone(glyphs.ICON_SODIUM)
        self.assertIsNotNone(glyphs.ICON_BP)
        self.assertNotEqual(glyphs.ICON_SODIUM, glyphs.ICON_BP)

    def test_the_two_kinds_never_mix(self):
        """Emoji are content, icons are chrome, and neither does the other's job.

        An emoji carries its own colours and cannot follow a theme; a
        Material icon is a code point in a font and means nothing on a
        shopping list.
        """
        # `ft.Icons` is a proxy object, not a class, so the members are
        # checked against the enum one of them belongs to rather than
        # against `ft.Icons` itself - isinstance() refuses the proxy.
        icon_enum = type(ft.Icons.MENU)
        foods = set(glyphs.FOODS.values())
        for name in self.ICON_NAMES:
            value = getattr(glyphs, name)
            with self.subTest(icon=name):
                self.assertIsInstance(value, icon_enum)
                self.assertNotIn(value, foods)

    def test_a_mark_is_drawn_in_the_current_theme(self):
        """Tinted from the palette, so all 24 themes reach it."""
        page = _StubPage()
        saved = S.theme
        self.addCleanup(lambda: apply_theme(page, saved))
        seen = []
        for theme in ("matrix", "white"):
            apply_theme(page, theme)
            icon = glyphs.mark(glyphs.ICON_SODIUM)
            self.assertEqual(icon.color, PAL()["accent"])
            seen.append(icon.color)
        self.assertNotEqual(seen[0], seen[1],
                            "the mark did not change colour with the theme")

    def test_a_mark_can_take_any_palette_tone(self):
        icon = glyphs.mark(glyphs.ICON_PULSE, tone="pink")
        self.assertEqual(icon.color, PAL()["pink"])

    def test_an_unknown_tone_falls_back_rather_than_raising(self):
        """A heading is never worth a KeyError."""
        self.assertEqual(glyphs.mark(glyphs.ICON_BP, tone="nope").color,
                         PAL()["accent"])

    def test_a_mark_sets_the_property_ft_icon_actually_has(self):
        """ft.Icon exposes `.icon`, not `.name`, and is not slotted - writing
        `.name` binds a stray attribute and silently changes nothing."""
        icon = glyphs.mark(glyphs.ICON_SODIUM)
        self.assertEqual(icon.icon, glyphs.ICON_SODIUM)

    def test_a_heading_carries_its_icon_its_title_and_its_trailing_control(self):
        row = glyphs.head(glyphs.ICON_GROCERY, "Grocery list", 14,
                          trailing=ft.TextButton(content="x"))
        self.assertIsInstance(row, ft.Row)
        self.assertIsInstance(row.controls[0], ft.Icon)
        self.assertEqual(row.controls[1].value, "Grocery list")
        self.assertIsInstance(row.controls[-1], ft.TextButton)

    def test_a_heading_without_a_trailing_control_still_has_its_spacer(self):
        """So headings line up whether or not they carry a chip."""
        row = glyphs.head(glyphs.ICON_GROCERY, "Grocery list")
        self.assertIsInstance(row.controls[-1], ft.Container)
        self.assertTrue(row.controls[-1].expand)


if __name__ == "__main__":
    unittest.main()
