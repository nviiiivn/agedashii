"""Tests for flet_app/prefs.py — the owner-editable layout and settings.

Two things these guard, both of which would be invisible until the app is
already broken on a phone:

1. **A read must never raise and must never need storage.** Flet builds a
   page synchronously, so every accessor here is called before - and
   sometimes without - `load()` ever running. A missing SharedPreferences
   has to yield the shipped defaults, not a traceback on a blank screen.

2. **A stored value must never be trusted.** Preferences outlive the build
   that wrote them. A saved page order naming a page that has since been
   removed, or missing one that has since been added, must degrade to the
   default order rather than to a navigation bar with a hole in it.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flet_app import prefs  # noqa: E402


class FakePrefs:
    """Stands in for flet's SharedPreferences (async get/set)."""

    def __init__(self, initial=None, broken=False):
        self.store = dict(initial or {})
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise RuntimeError("storage unavailable")
        return self.store.get(key)

    async def set(self, key, value):
        if self.broken:
            raise RuntimeError("storage unavailable")
        self.store[key] = value
        return True


class PrefsTestCase(unittest.TestCase):
    """prefs holds module-level state by design; restore it around each test."""

    def setUp(self):
        self._saved = dict(prefs._V)
        self._loaded = prefs.LOADED
        prefs.reset_all()

    def tearDown(self):
        prefs._V.clear()
        prefs._V.update(self._saved)
        prefs.LOADED = self._loaded


class TestSanitiseOrder(PrefsTestCase):
    def test_unknown_keys_are_dropped(self):
        got = prefs.sanitise_order(["home", "atlantis", "lab"], prefs.PAGE_KEYS)
        self.assertNotIn("atlantis", got)
        self.assertEqual(sorted(got), sorted(prefs.PAGE_KEYS))

    def test_missing_keys_are_appended(self):
        got = prefs.sanitise_order(["weather", "home"], prefs.PAGE_KEYS)
        self.assertEqual(got[:2], ["weather", "home"])
        self.assertEqual(sorted(got), sorted(prefs.PAGE_KEYS))

    def test_duplicates_collapse(self):
        got = prefs.sanitise_order(["home", "home", "lab"], prefs.PAGE_KEYS)
        self.assertEqual(got.count("home"), 1)
        self.assertEqual(len(got), len(prefs.PAGE_KEYS))

    def test_junk_falls_back_to_canonical(self):
        for junk in (None, "home", 7, {"home": 1}, [1, 2, 3]):
            with self.subTest(junk=junk):
                self.assertEqual(prefs.sanitise_order(junk, prefs.PAGE_KEYS),
                                 list(prefs.PAGE_KEYS))


class TestMove(PrefsTestCase):
    def test_moves_up_and_down(self):
        seq = ["a", "b", "c"]
        self.assertEqual(prefs.move(seq, "c", -1), ["a", "c", "b"])
        self.assertEqual(prefs.move(seq, "a", +1), ["b", "a", "c"])

    def test_edges_are_no_ops(self):
        seq = ["a", "b", "c"]
        self.assertEqual(prefs.move(seq, "a", -1), seq)
        self.assertEqual(prefs.move(seq, "c", +1), seq)

    def test_unknown_key_is_a_no_op(self):
        self.assertEqual(prefs.move(["a", "b"], "z", -1), ["a", "b"])

    def test_does_not_mutate_the_input(self):
        seq = ["a", "b"]
        prefs.move(seq, "b", -1)
        self.assertEqual(seq, ["a", "b"])


class TestDefaults(PrefsTestCase):
    def test_reads_work_with_no_storage_at_all(self):
        """Every accessor is called during a synchronous build()."""
        self.assertEqual(prefs.page_order(), list(prefs.PAGE_KEYS))
        self.assertEqual(prefs.visible_pages(), list(prefs.PAGE_KEYS))
        self.assertEqual(prefs.bottom_bar_pages(), list(prefs.DEFAULT_BOTTOM))
        self.assertEqual(prefs.home_panels(), list(prefs.HOME_PANEL_KEYS))
        self.assertEqual(prefs.unit_system(), "imperial")
        self.assertEqual(prefs.sodium_target(), 2300)
        self.assertEqual(prefs.refresh_seconds(), prefs.DEFAULT_REFRESH)
        self.assertTrue(prefs.app_version())

    def test_unit_helpers_track_the_system(self):
        self.assertEqual((prefs.distance_unit(), prefs.temperature_unit()), ("mi", "F"))
        prefs.set_unit_system("metric")
        self.assertEqual((prefs.distance_unit(), prefs.temperature_unit()), ("km", "C"))

    def test_bogus_values_do_not_stick(self):
        prefs.set_unit_system("furlongs")
        prefs.set_sodium_target(9999)
        prefs.set_refresh_seconds("banana")
        self.assertEqual(prefs.unit_system(), "imperial")
        self.assertEqual(prefs.sodium_target(), 2300)
        self.assertEqual(prefs.refresh_seconds(), prefs.DEFAULT_REFRESH)

    def test_numbers_stored_as_strings_still_read_back(self):
        prefs._V[prefs.KEY_SODIUM_TARGET] = "1500"
        prefs._V[prefs.KEY_REFRESH_SECS] = "300"
        self.assertEqual(prefs.sodium_target(), 1500)
        self.assertEqual(prefs.refresh_seconds(), 300)


class TestVisibility(PrefsTestCase):
    def test_hiding_removes_from_visible_and_from_the_bar(self):
        prefs.set_page_hidden("lab", True)
        self.assertNotIn("lab", prefs.visible_pages())
        self.assertNotIn("lab", prefs.bottom_bar_pages())
        self.assertIn("lab", prefs.page_order(), "order still lists it, so it can come back")

    def test_settings_cannot_be_hidden(self):
        prefs.set_page_hidden("settings", True)
        self.assertIn("settings", prefs.visible_pages())
        self.assertEqual(prefs.hidden_pages(), [])

    def test_unhiding_restores_it(self):
        prefs.set_page_hidden("trips", True)
        prefs.set_page_hidden("trips", False)
        self.assertIn("trips", prefs.visible_pages())

    def test_bottom_bar_never_empties(self):
        for key in list(prefs.DEFAULT_BOTTOM):
            prefs.set_in_bottom_bar(key, False)
        bar = prefs.bottom_bar_pages()
        self.assertEqual(len(bar), 1, "a bar of nothing but More is a dead end")
        self.assertIn(bar[0], prefs.visible_pages())

    def test_bottom_bar_is_capped(self):
        added = prefs.set_in_bottom_bar("weather", True)
        self.assertFalse(added, "the sixth page must be refused, not silently dropped")
        self.assertEqual(len(prefs.bottom_bar_pages()), prefs.MAX_BOTTOM)

    def test_bottom_bar_follows_the_page_order(self):
        prefs.set_page_order(["wat2do", "garage", "tools", "lab", "home",
                              "trips", "weather", "extras", "settings"])
        self.assertEqual(prefs.bottom_bar_pages(),
                         ["wat2do", "garage", "tools", "lab", "home"])

    def test_moving_a_page_reorders_it(self):
        prefs.move_page("weather", -1)
        order = prefs.page_order()
        self.assertEqual(order.index("weather"), prefs.PAGE_KEYS.index("weather") - 1)


class TestHomePanels(PrefsTestCase):
    def test_hide_and_reorder(self):
        prefs.set_home_panel_hidden("nowplaying", True)
        prefs.move_home_panel("wat2do", -10)   # clamped: already impossible to overshoot
        panels = prefs.home_panels()
        self.assertNotIn("nowplaying", panels)
        self.assertIn("wat2do", panels)
        self.assertEqual(len(panels), len(prefs.HOME_PANEL_KEYS) - 1)

    def test_move_to_the_top(self):
        for _ in range(len(prefs.HOME_PANEL_KEYS)):
            prefs.move_home_panel("wat2do", -1)
        self.assertEqual(prefs.home_panel_order()[0], "wat2do")

    def test_every_panel_can_be_hidden(self):
        for key in prefs.HOME_PANEL_KEYS:
            prefs.set_home_panel_hidden(key, True)
        self.assertEqual(prefs.home_panels(), [])


class TestPersistence(PrefsTestCase):
    def test_round_trip(self):
        prefs.move_page("settings", -2)
        prefs.set_page_hidden("extras", True)
        prefs.set_in_bottom_bar("home", False)
        prefs.set_home_panel_hidden("greeting", True)
        prefs.move_home_panel("machines", -1)
        prefs.set_unit_system("metric")
        prefs.set_sodium_target(1500)
        prefs.set_refresh_seconds(300)
        expected = (prefs.page_order(), prefs.visible_pages(), prefs.bottom_bar_pages(),
                    prefs.home_panels(), prefs.unit_system(),
                    prefs.sodium_target(), prefs.refresh_seconds())

        store = FakePrefs()
        self.assertTrue(asyncio.run(prefs.save(store)))

        prefs.reset_all()
        self.assertNotEqual(prefs.unit_system(), "metric")

        asyncio.run(prefs.load(store))
        self.assertEqual((prefs.page_order(), prefs.visible_pages(), prefs.bottom_bar_pages(),
                          prefs.home_panels(), prefs.unit_system(),
                          prefs.sodium_target(), prefs.refresh_seconds()), expected)

    def test_saved_values_are_types_shared_preferences_accepts(self):
        """SharedPreferences rejects anything but str/int/float/bool/list[str]."""
        store = FakePrefs()
        asyncio.run(prefs.save(store))
        for key, value in store.store.items():
            with self.subTest(key=key):
                self.assertIsInstance(value, (str, int, float, bool, list))
                if isinstance(value, list):
                    self.assertTrue(all(isinstance(x, str) for x in value), value)

    def test_load_with_no_storage_does_not_crash(self):
        self.assertFalse(asyncio.run(prefs.load(None)))
        self.assertEqual(prefs.page_order(), list(prefs.PAGE_KEYS))

    def test_save_with_no_storage_reports_failure(self):
        self.assertFalse(asyncio.run(prefs.save(None)))

    def test_broken_storage_does_not_crash(self):
        broken = FakePrefs(broken=True)
        self.assertFalse(asyncio.run(prefs.load(broken)))
        self.assertFalse(asyncio.run(prefs.save(broken)))
        self.assertEqual(prefs.visible_pages(), list(prefs.PAGE_KEYS))

    def test_stale_stored_order_from_an_older_build(self):
        """A preference written before a page existed must not lose that page."""
        store = FakePrefs({prefs.KEY_PAGE_ORDER: ["home", "gone", "lab"],
                           prefs.KEY_PAGES_HIDDEN: ["gone"],
                           prefs.KEY_BOTTOM_PAGES: ["gone", "home"]})
        asyncio.run(prefs.load(store))
        order = prefs.page_order()
        self.assertEqual(sorted(order), sorted(prefs.PAGE_KEYS))
        self.assertEqual(order[:2], ["home", "lab"])
        self.assertEqual(prefs.bottom_bar_pages(), ["home"])

    def test_load_ignores_a_key_of_the_wrong_type(self):
        store = FakePrefs({prefs.KEY_HOME_ORDER: "greeting",
                           prefs.KEY_UNITS: 42,
                           prefs.KEY_SODIUM_TARGET: "not a number"})
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.home_panel_order(), list(prefs.HOME_PANEL_KEYS))
        self.assertEqual(prefs.unit_system(), "imperial")
        self.assertEqual(prefs.sodium_target(), 2300)

    def test_one_unreadable_key_does_not_lose_the_others(self):
        class Picky(FakePrefs):
            async def get(self, key):
                if key == prefs.KEY_UNITS:
                    raise RuntimeError("this platform hates that key")
                return self.store.get(key)

        store = Picky({prefs.KEY_SODIUM_TARGET: 1500})
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.sodium_target(), 1500)
        self.assertEqual(prefs.unit_system(), "imperial")


class TestRuntimeApplication(PrefsTestCase):
    def test_refresh_interval_reaches_the_api_cache(self):
        """A setting that changes nothing is a lie; this one moves api.TTL."""
        from flet_app import api
        before = api.TTL
        try:
            prefs.set_refresh_seconds(300)
            self.assertEqual(api.TTL, 300)
        finally:
            api.TTL = before


class TestSettingsPageBuilds(PrefsTestCase):
    """The page must build with and without storage - it is the only way back."""

    def _stub_page(self):
        class StubPage:
            def __init__(self):
                self.controls = []
                self.drawer = None

            def update(self):
                pass

            def add(self, *controls):
                self.controls.extend(controls)

            async def open(self, *a, **k):
                pass

            async def close(self, *a, **k):
                pass

            @property
            def services(self):
                return []
        return StubPage()

    def test_builds_with_no_prefs(self):
        from flet_app.pages import settings
        from flet_app.ui import S
        before = S.prefs
        S.prefs = None
        try:
            control = settings.build(self._stub_page())
            self.assertTrue(control.controls)
        finally:
            S.prefs = before

    def test_builds_with_prefs(self):
        from flet_app.pages import settings
        from flet_app.ui import S
        before = S.prefs
        S.prefs = FakePrefs({prefs.KEY_UNITS: "metric"})
        try:
            control = settings.build(self._stub_page(), on_theme=lambda: None,
                                     on_layout=lambda: None)
            self.assertTrue(control.controls)
        finally:
            S.prefs = before


if __name__ == "__main__":
    unittest.main()


class BottomBarEnabled(unittest.TestCase):
    """The bar can be switched off; the drawer still reaches every page."""

    def setUp(self):
        prefs._V = prefs._defaults()

    def test_on_by_default(self):
        self.assertTrue(prefs.bottom_bar_enabled())

    def test_toggles(self):
        prefs.set_bottom_bar_enabled(False)
        self.assertFalse(prefs.bottom_bar_enabled())
        prefs.set_bottom_bar_enabled(True)
        self.assertTrue(prefs.bottom_bar_enabled())

    def test_survives_a_round_trip_through_stringy_storage(self):
        # SharedPreferences can hand a bool back as a string, and
        # bool("false") is True - so without coercion, switching the bar off
        # and reopening the app would silently switch it back on.
        cases = {None: True, True: True, False: False, 1: True, 0: False,
                 "true": True, "True": True, "false": False, "False": False,
                 "0": False, "off": False, "": False, "yes": True}
        for stored, expected in cases.items():
            with self.subTest(stored=stored):
                prefs._V[prefs.KEY_BOTTOM_ENABLED] = stored
                self.assertIs(prefs.bottom_bar_enabled(), expected)

    def test_switching_it_off_hides_nothing(self):
        prefs.set_bottom_bar_enabled(False)
        # Every page must still be listed somewhere, i.e. in the drawer.
        self.assertEqual(set(prefs.drawer_pages()), set(prefs.visible_pages()))


class DrawerSubpagesMatchTheRealTabs(unittest.TestCase):
    """main.SUBPAGES hardcodes tab indices; this fails if they drift.

    The drawer jumps straight to a tab by writing its index into _TAB_INDEX,
    so a tab reordered in a page module without updating SUBPAGES would send
    you silently to the wrong place.
    """

    def test_indices_line_up(self):
        import importlib.util
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, "main.py")).read()
        # main.py calls ft.run() at import, so read SUBPAGES out of the AST
        # rather than importing it.
        import ast
        tree = ast.parse(src)
        subpages = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "SUBPAGES" for t in node.targets):
                subpages = ast.literal_eval(node.value)
        self.assertIsNotNone(subpages, "SUBPAGES not found in main.py")

        expected = {
            "lab": ["Machines", "Network", "Services", "Qbit"],
            "tools": ["Feeds", "Vault", "Journal", "FoodZen"],
            "foodzen": ["Grocery", "Meals", "Scrape", "Expenses", "EatZen", "BPM"],
        }
        for label, path, _depth in subpages["tools"]:
            group, index = path[-1]
            self.assertLess(index, len(expected[group]),
                            f"{label}: index {index} past the end of {group}")
            self.assertEqual(expected[group][index], label,
                             f"{label} points at {group}[{index}], which is "
                             f"{expected[group][index]}")
        for label, path, _depth in subpages["lab"]:
            group, index = path[-1]
            self.assertEqual(expected[group][index], label)
