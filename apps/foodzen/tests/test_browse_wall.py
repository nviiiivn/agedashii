"""The catalogue wall behind the Grocery list.

Moved out of Tofu's tests/test_parity_tools_home.py when FoodZen left
Tofu. Unchanged except for the import.
"""
import unittest


def _walk_texts(control, out=None, depth=0):
    out = [] if out is None else out
    if depth > 60:
        return out
    v = getattr(control, "value", None)
    if isinstance(v, str) and v.strip():
        out.append(v.strip())
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for y in child:
                if hasattr(y, "__dict__"):
                    _walk_texts(y, out, depth + 1)
        elif child is not None and hasattr(child, "__dict__"):
            _walk_texts(child, out, depth + 1)
    return out


class StubPage:
    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []

    def update(self, *a, **k):
        pass

    def add(self, *c):
        self.controls.extend(c)


class TheBrowseWallIsFoldedAway(unittest.TestCase):
    """231 catalogue buttons was 269 controls - most of the Grocery tab -
    rebuilt every time a checkbox was ticked, for a wall that lives behind
    a 260px scroll window below the list you came to read."""

    def setUp(self):
        from agedashii_foodzen.pages import foodzen
        self.fz = foodzen
        foodzen._OPEN["browse"] = False

    def tearDown(self):
        self.fz._OPEN["browse"] = False

    def _buttons(self, control, out=None, depth=0):
        out = [] if out is None else out
        if depth > 60:
            return out
        if type(control).__name__ == "TextButton":
            out.append(control)
        for attr in ("controls", "content"):
            child = getattr(control, attr, None)
            if isinstance(child, list):
                for y in child:
                    if hasattr(y, "__dict__"):
                        self._buttons(y, out, depth + 1)
            elif child is not None and hasattr(child, "__dict__"):
                self._buttons(child, out, depth + 1)
        return out

    def test_closed_it_builds_no_catalogue_buttons(self):
        self.assertEqual(self._buttons(self.fz._browse(StubPage(),
                                                       lambda *a: None)), [])

    def test_open_it_builds_every_one_of_them(self):
        from agedashii_washi.foodzen_catalog import CATALOG
        self.fz._OPEN["browse"] = True
        built = self._buttons(self.fz._browse(StubPage(), lambda *a: None))
        self.assertEqual(len(built), len(CATALOG))

    def test_the_count_is_on_the_header_either_way(self):
        from agedashii_washi.foodzen_catalog import CATALOG
        for state in (False, True):
            with self.subTest(open=state):
                self.fz._OPEN["browse"] = state
                shown = _walk_texts(self.fz._browse(StubPage(),
                                                    lambda *a: None))
                self.assertIn(f"{len(CATALOG)} items", shown)
