"""Ride options: what she changes on the bike, where she is when she wants it.

The sheet behind the round options button on the map. It took the bike, the
map, the gauges switch, the mic and the two map-follow rows; it now also takes
the theme and Follow the sun, the gauge layout and which side they sit, the
turn voice and the character lines - the things changed on the way rather than
set once. Sideways it is a grid three across, so it fits at a light without
scrolling; every target, Close and Back included, is glove-sized.

Own stub page and storage directory, for the reason test_diagnostics gives.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("FLET_APP_STORAGE_DATA", _TMP.name)

import flet as ft                                          # noqa: E402

from agedashii_navi import gauges, live, prefs, theme, ui  # noqa: E402
from agedashii_navi.pages import ride as ride_page          # noqa: E402


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


class _Task:
    def cancel(self):
        pass


class Page:
    def __init__(self, width, height):
        self.controls, self.services, self.overlay = [], [], []
        self.tasks, self.dialogs, self.popped = [], [], 0
        self.width, self.height = width, height
        self.updates = 0

    def update(self, *_a, **_k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **_k):
        self.tasks.append((fn, args))

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.popped += 1


def walk(node, seen=None):
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions"):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    for attr in ("content", "title"):
        child = getattr(node, attr, None)
        if isinstance(child, ft.BaseControl):
            yield from walk(child, seen)


def texts(node):
    return [str(c.value) for c in walk(node)
            if isinstance(c, ft.Text) and c.value]


def tap(node, words):
    """Tap the innermost clickable whose words include `words`."""
    target = None
    for control in walk(node):
        if getattr(control, "on_click", None) and any(
                words in t for t in texts(control)):
            target = control
    if target is None:
        raise AssertionError(f"nothing to tap saying {words!r}: {texts(node)}")
    target.on_click(None)
    return target


def saved(page):
    return "_save_prefs" in [getattr(fn, "__name__", "") for fn, _a in page.tasks]


class _Sheet(unittest.TestCase):
    WIDTH, HEIGHT = 412, 915

    def setUp(self):
        prefs.reset()
        live.reset()
        theme.set_theme(prefs.theme_key())
        self.page = Page(self.WIDTH, self.HEIGHT)
        built = ride_page.build(self.page)
        button = next(c for c in walk(built)
                      if getattr(c, "tooltip", None) == "Ride options")
        button.on_click(None)
        self.sheet = self.page.dialogs[-1]
        self.page.tasks.clear()


class Upright(_Sheet):
    def test_it_holds_what_she_changes_on_the_way(self):
        said = " | ".join(texts(self.sheet))
        for words in ("Gauges: on", "Layout: ", "Look: ", "Turn voice: on",
                      "Character lines: on", "Engine sound", "Follow the bike",
                      "Centre on the bike", prefs.machine_name()):
            self.assertIn(words, said)

    def test_every_target_is_glove_sized_close_included(self):
        clickable = [c for c in walk(self.sheet)
                     if getattr(c, "on_click", None) is not None]
        self.assertIn("Close", [t for c in clickable for t in texts(c)])
        for control in clickable:
            self.assertGreaterEqual(control.height or 0, ui.TOUCH,
                                    texts(control))

    def test_upright_it_is_one_column(self):
        grid = next(c for c in walk(self.sheet)
                    if isinstance(c, ft.Row) and c.wrap)
        self.assertEqual(grid.width, 320)
        self.assertIsNotNone(self.sheet.title)

    def test_the_turn_voice_switches_off_and_saves(self):
        tap(self.sheet, "Turn voice: on")
        self.assertFalse(prefs.voice())
        self.assertTrue(saved(self.page))
        self.assertEqual(self.page.popped, 1, "a toggle closes the sheet")

    def test_character_lines_switch_off_and_save(self):
        tap(self.sheet, "Character lines: on")
        self.assertFalse(prefs.flourishes())
        self.assertTrue(saved(self.page))

    def test_the_look_opens_in_place_and_back_returns(self):
        tap(self.sheet, "Look: ")
        self.assertEqual(len(self.page.dialogs), 1, "a second sheet was opened")
        self.assertEqual(self.page.popped, 0)
        said = texts(self.sheet)
        for key in theme.THEMES:
            self.assertIn(theme.THEME_NAMES[key], said)
        self.assertTrue(any(t.startswith("Follow the sun") for t in said))
        tap(self.sheet, "Back")
        self.assertIn("Turn voice: on", texts(self.sheet))

    def test_a_theme_by_hand_turns_follow_the_sun_off(self):
        self.assertTrue(prefs.auto_theme())
        tap(self.sheet, "Look: ")
        tap(self.sheet, "Red night")
        self.assertEqual(prefs.theme_key(), "redshift")
        self.assertFalse(prefs.auto_theme(),
                         "the next phase change would undo her choice")
        self.assertEqual(theme.S["theme"], "redshift")
        self.assertTrue(saved(self.page))

    def test_follow_the_sun_off_puts_back_her_own_theme(self):
        prefs.set_value("theme", "day")
        prefs.set_value("theme_auto", "redshift")      # what the sun picked
        tap(self.sheet, "Look: ")
        tap(self.sheet, "Follow the sun: on")
        self.assertFalse(prefs.auto_theme())
        self.assertEqual(theme.S["theme"], "day")

    def test_upright_the_gauges_go_top_or_bottom(self):
        tap(self.sheet, "Layout: ")
        said = texts(self.sheet)
        self.assertIn("Along the top", said)
        self.assertIn("Along the bottom", said)
        self.assertNotIn("Down the left", said)
        for _key, name, _detail in gauges.LAYOUTS:
            self.assertIn(name, said)
        tap(self.sheet, "Along the top")
        self.assertEqual(prefs.hud_edge(), "top")

    def test_a_layout_picked_while_hidden_shows_them(self):
        prefs.toggle_dash()
        self.assertFalse(prefs.dash())
        tap(self.sheet, "Layout: ")
        tap(self.sheet, "Corners")
        self.assertEqual(prefs.hud(), "corners")
        self.assertTrue(prefs.dash())


class Sideways(_Sheet):
    WIDTH, HEIGHT = 915, 412

    def test_sideways_it_is_three_across_with_no_title(self):
        grid = next(c for c in walk(self.sheet)
                    if isinstance(c, ft.Row) and c.wrap)
        self.assertEqual(grid.width, 3 * 200 + 2 * 8)
        rows = list(grid.controls)
        self.assertTrue(all(c.width == 200 for c in rows))
        # Ten rows, three across: four lines of 56 dp, which fits 412 dp
        # with the Close button and no title.
        self.assertLessEqual(len(rows), 12)
        self.assertIsNone(self.sheet.title)

    def test_sideways_the_gauges_go_left_or_right(self):
        tap(self.sheet, "Layout: ")
        said = texts(self.sheet)
        self.assertIn("Down the left", said)
        self.assertIn("Down the right", said)
        self.assertNotIn("Along the top", said)
        tap(self.sheet, "Down the left")
        self.assertEqual(prefs.hud_side(), "left")

    def test_sub_views_have_a_title_and_a_glove_sized_back(self):
        tap(self.sheet, "Look: ")
        self.assertIsNotNone(self.sheet.title)
        back = [c for c in walk(self.sheet)
                if getattr(c, "on_click", None) and texts(c) == ["Back"]]
        self.assertTrue(back)
        self.assertGreaterEqual(back[0].height, ui.TOUCH)

    def test_sideways_close_is_in_the_grid_not_a_row_under_it(self):
        self.assertEqual(self.sheet.actions, [])
        grid = next(c for c in walk(self.sheet)
                    if isinstance(c, ft.Row) and c.wrap)
        self.assertIn(["Close"], [texts(c) for c in grid.controls])


if __name__ == "__main__":
    unittest.main()
