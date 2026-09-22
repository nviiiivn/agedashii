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
    """The menu and the bottom bar are two sets, not one.

    Her words: "i disabled the bottom bar and 'show' everything. seemingly
    theres no way to customize the bottom bar without the menu becoming
    affected.. it's not different / optional like i asked for user
    preferences." She was right: `bottom_bar_pages()` intersected with
    `visible_pages()`, so the single eye per row in Settings drove both
    surfaces and there was no way to have a page in one but not the other.
    """

    def test_taking_a_page_out_of_the_menu_leaves_the_bar_alone(self):
        prefs.set_page_hidden("lab", True)
        self.assertNotIn("lab", prefs.visible_pages())
        self.assertIn("lab", prefs.bottom_bar_pages(),
                      "the bar is its own set; the menu does not filter it")
        self.assertIn("lab", prefs.page_order(), "order still lists it, so it can come back")

    def test_taking_a_page_off_the_bar_leaves_the_menu_alone(self):
        prefs.set_in_bottom_bar("lab", False)
        self.assertNotIn("lab", prefs.bottom_bar_pages())
        self.assertIn("lab", prefs.visible_pages())

    def test_all_four_combinations_are_reachable(self):
        prefs.set_in_bottom_bar("home", False)       # make a slot
        prefs.set_in_bottom_bar("weather", True)     # menu + bar
        prefs.set_page_in_menu("lab", False)         # bar only
        prefs.set_page_in_menu("extras", False)      # neither
        self.assertEqual(
            {k: (k in prefs.visible_pages(), k in prefs.bottom_bar_pages())
             for k in ("weather", "lab", "trips", "extras")},
            {"weather": (True, True), "lab": (False, True),
             "trips": (True, False), "extras": (False, False)})

    def test_a_bar_only_page_is_still_reachable(self):
        prefs.set_page_in_menu("lab", False)
        self.assertNotIn("lab", prefs.visible_pages())
        self.assertIn("lab", prefs.reachable_pages(),
                      "the shell must not bounce her off a page the bar links to")

    def test_settings_cannot_leave_both_surfaces(self):
        # Not on the bar by default, so the menu is the only way back.
        self.assertFalse(prefs.set_page_hidden("settings", True))
        self.assertIn("settings", prefs.visible_pages())
        self.assertEqual(prefs.hidden_pages(), [])

    def test_settings_may_leave_the_menu_once_the_bar_carries_it(self):
        prefs.set_in_bottom_bar("home", False)       # make room
        self.assertTrue(prefs.set_in_bottom_bar("settings", True))
        self.assertTrue(prefs.set_page_in_menu("settings", False))
        self.assertNotIn("settings", prefs.visible_pages())
        self.assertIn("settings", prefs.bottom_bar_pages())
        # ...and now the bar is the last hold, so that is what is refused.
        self.assertFalse(prefs.set_in_bottom_bar("settings", False))
        self.assertIn("settings", prefs.bottom_bar_pages())

    def test_a_stranded_settings_from_storage_is_put_back_in_the_menu(self):
        """No setter wrote this - a hand-edited or older store did."""
        prefs._V[prefs.KEY_PAGES_HIDDEN] = ["settings"]
        prefs._V[prefs.KEY_BOTTOM_PAGES] = ["home"]
        self.assertIn("settings", prefs.visible_pages())

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

    def test_the_bar_stands_in_even_with_the_menu_emptied(self):
        for key in prefs.PAGE_KEYS:
            prefs.set_page_in_menu(key, False)
        for key in list(prefs.DEFAULT_BOTTOM):
            prefs.set_in_bottom_bar(key, False)
        self.assertEqual(len(prefs.bottom_bar_pages()), 1)
        self.assertTrue(prefs.reachable_pages())

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

    def test_preferences_written_before_the_rename_are_still_read(self):
        """The nvii.* -> agedashii.* rename must not read as a factory reset.

        Her laptop has been running since the app was called NVII-DASH, so
        everything it has stored is under the old namespace. If load() only
        looked for the new names it would find nothing, fall back to the
        shipped defaults, and throw away the layout, units and sodium target
        she set - which is indistinguishable from the app losing her
        settings, because it is the app losing her settings.
        """
        store = FakePrefs({
            "nvii.units": "metric",
            "nvii.sodium_target": 1500,
            "nvii.refresh_secs": 300,
            "nvii.pages_hidden": ["extras"],
            "nvii.panels.home.hidden": ["greeting"],
        })
        self.assertTrue(asyncio.run(prefs.load(store)))
        self.assertEqual(prefs.unit_system(), "metric")
        self.assertEqual(prefs.sodium_target(), 1500)
        self.assertEqual(prefs.refresh_seconds(), 300)
        self.assertNotIn("extras", prefs.visible_pages())
        self.assertNotIn("greeting", prefs.panels("home"))

    def test_the_layout_she_already_had_survives_the_menu_bar_split(self):
        """The upgrade must not hand her a bar she never asked for.

        Under the old scheme `bottom_pages` only took effect for a page
        that was also unhidden, so her store says "garage is on the bar"
        and "garage is hidden" at the same time and the bar she actually
        sees has no garage on it. Read literally under the split, garage
        would reappear on the bar on first launch. So the pair is
        collapsed once, on the first load that finds no scheme number, to
        exactly what the old build was drawing.
        """
        store = FakePrefs({
            "nvii.pages_hidden": ["garage", "extras"],
            "nvii.bottom_pages": ["home", "lab", "tools", "garage", "wat2do"],
        })
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(),
                         ["home", "lab", "tools", "wat2do"],
                         "the same four the coupled build was showing")
        self.assertNotIn("garage", prefs.visible_pages())
        self.assertNotIn("extras", prefs.visible_pages())

    def test_the_split_is_applied_once_and_never_again(self):
        """Otherwise every restart would re-couple the two sets.

        The second load has to leave a deliberately menu-hidden, bar-on
        page exactly as she set it - that combination is the feature.
        """
        store = FakePrefs({"nvii.pages_hidden": ["garage"],
                           "nvii.bottom_pages": ["home", "garage"]})
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(), ["home"])

        # Now she does the thing the split exists for: garage on the bar,
        # out of the menu.
        prefs.set_in_bottom_bar("garage", True)
        self.assertEqual(prefs.bottom_bar_pages(), ["home", "garage"])
        asyncio.run(prefs.save(store))

        prefs.reset_all()
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(), ["home", "garage"])
        self.assertNotIn("garage", prefs.visible_pages())

    def test_a_fresh_install_is_not_migrated(self):
        """Nothing stored means nothing to convert - and no phantom edits."""
        store = FakePrefs()
        self.assertFalse(asyncio.run(prefs.load(store)))
        self.assertEqual(prefs.bottom_bar_pages(), list(prefs.DEFAULT_BOTTOM))
        self.assertEqual(prefs.visible_pages(), list(prefs.PAGE_KEYS))

    def test_the_scheme_number_is_written_by_save(self):
        store = FakePrefs()
        asyncio.run(prefs.save(store))
        self.assertEqual(store.store[prefs.KEY_LAYOUT_SCHEME], prefs.LAYOUT_SCHEME)

    def test_the_current_name_wins_when_both_are_present(self):
        """After the first save both exist; the new one is the live value."""
        store = FakePrefs({"nvii.units": "metric", "agedashii.units": "imperial"})
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.unit_system(), "imperial")

    def test_saving_writes_only_the_current_names(self):
        store = FakePrefs()
        self.assertTrue(asyncio.run(prefs.save(store)))
        self.assertTrue(store.store)
        for key in store.store:
            with self.subTest(key=key):
                self.assertTrue(key.startswith(prefs.NS), key)

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
            "lab": ["Machines", "Network", "Services", "Docker", "Qbit"],
            "tools": ["Feeds", "Vault", "Journal", "FoodZen"],
            "foodzen": ["Grocery", "Meals", "Scrape", "Expenses", "EatZen", "BPM"],
            # Future lives on Wat2Do, matching the website; it was briefly
            # mounted here too.
            "extras": ["Projects", "AI", "Obligations"],
        }
        for group in ("tools", "lab", "extras"):
            for label, path, _depth in subpages[group]:
                key, index = path[-1]
                self.assertLess(index, len(expected[key]),
                                f"{label}: index {index} past the end of {key}")
                self.assertEqual(expected[key][index], label,
                                 f"{label} points at {key}[{index}], which is "
                                 f"{expected[key][index]}")


class PanelLayout(unittest.TestCase):
    """Reorder, hide and resize the cards on any surface.

    Before this, Home's six panels were the only thing that could be
    rearranged and nothing anywhere had a width.
    """

    SURFACE = "home"

    def setUp(self):
        from flet_app import panels
        self.panels = panels
        prefs._V = prefs._defaults()
        prefs.reset_panels(self.SURFACE)

    def test_defaults_are_shipped_order_all_visible_all_full(self):
        keys = self.panels.panel_keys(self.SURFACE)
        self.assertEqual(prefs.panels(self.SURFACE), keys)
        for k in keys:
            self.assertEqual(prefs.panel_width(self.SURFACE, k), "full")

    def test_width_round_trip(self):
        self.assertTrue(prefs.set_panel_width(self.SURFACE, "machines", "half"))
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), "half")
        # Others are untouched.
        self.assertEqual(prefs.panel_width(self.SURFACE, "weather"), "full")

    def test_a_width_back_to_full_is_not_stored(self):
        # Keeps the stored list to the panels actually customised, so a
        # renamed panel leaves no junk behind.
        prefs.set_panel_width(self.SURFACE, "machines", "half")
        prefs.set_panel_width(self.SURFACE, "machines", "full")
        self.assertEqual(prefs._V[prefs._pkey(self.SURFACE, "width")], [])

    def test_unknown_width_or_panel_is_refused(self):
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "machines", "huge"))
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "nope", "half"))
        self.assertFalse(prefs.set_panel_hidden(self.SURFACE, "nope", True))

    def test_a_corrupt_width_reads_as_full_rather_than_raising(self):
        # Losing a layout is an annoyance; a page that will not build is not.
        prefs._V[prefs._pkey(self.SURFACE, "width")] = [
            "machines:garbage", "not-a-pair", 42, None]
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), "full")

    def test_hide_and_reorder(self):
        prefs.set_panel_hidden(self.SURFACE, "nowplaying", True)
        self.assertNotIn("nowplaying", prefs.panels(self.SURFACE))
        # Hiding must not drop it from the editor's list.
        self.assertIn("nowplaying", prefs.panel_order(self.SURFACE))
        # Counted off the shipped order rather than hardcoded: Home has
        # gained and lost cards twice now, and each time this test failed
        # for a reason that had nothing to do with reordering.
        was = self.panels.panel_keys(self.SURFACE).index("wat2do")
        prefs.move_panel(self.SURFACE, "wat2do", -3)
        self.assertEqual(prefs.panel_order(self.SURFACE)[was - 3], "wat2do")

    def test_reset_puts_everything_back(self):
        prefs.set_panel_width(self.SURFACE, "machines", "third")
        prefs.set_panel_hidden(self.SURFACE, "weather", True)
        prefs.move_panel(self.SURFACE, "wat2do", -4)
        prefs.reset_panels(self.SURFACE)
        self.assertEqual(prefs.panels(self.SURFACE),
                         self.panels.panel_keys(self.SURFACE))
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), "full")

    def test_every_surface_declares_unique_keys(self):
        for surface, entries in self.panels.SURFACES.items():
            with self.subTest(surface=surface):
                keys = [k for k, _ in entries]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertIn(surface, self.panels.SURFACE_LABELS)

    def test_surfaces_are_independent(self):
        prefs.set_panel_width("home", "machines", "half")
        self.assertEqual(prefs.panel_width("weather", "hero"), "full")
        prefs.set_panel_hidden("weather", "hero", True)
        self.assertIn("machines", prefs.panels("home"))


class PanelLayoutReachesTheScreen(unittest.TestCase):
    """The preference has to change what is drawn, not just what is stored."""

    def setUp(self):
        prefs._V = prefs._defaults()
        for s in ("home",):
            prefs.reset_panels(s)

    def _spans(self, control):
        """The column spans of the *panel* row, and nothing nested inside it.

        `layout()` returns one outer ResponsiveRow whose children are the
        panels. A card is free to use a ResponsiveRow of its own inside
        itself - Home's stat strip and quick links both do - and those are
        not panels, so the walk stops at the first one it finds rather than
        mixing their spans in with the layout's.
        """
        out = []

        def walk(c):
            if type(c).__name__ == "ResponsiveRow":
                out.extend(getattr(x, "col", {}).get("md") for x in c.controls)
                return
            for k in ("controls", "content"):
                v = getattr(c, k, None)
                if isinstance(v, list):
                    for y in v:
                        walk(y)
                elif v is not None and hasattr(v, "__dict__"):
                    walk(v)

        walk(control)
        return out

    def _home(self):
        from flet_app.pages import home

        class Stub:
            controls = []
            services = []
            drawer = None
            theme = None
            theme_mode = None

            def update(self):
                pass

            def add(self, *a):
                pass

        return home.build(Stub())

    def test_default_is_every_home_card_at_full_width(self):
        from flet_app import panels
        self.assertEqual(self._spans(self._home()),
                         [12] * len(panels.panel_keys("home")))

    def test_half_width_and_hidden_show_up_in_the_render(self):
        from flet_app import panels
        keys = panels.panel_keys("home")
        prefs.set_panel_width("home", "machines", "half")
        prefs.set_panel_width("home", "actions", "half")
        prefs.set_panel_hidden("home", "nowplaying", True)
        want = [6 if k in ("machines", "actions") else 12
                for k in keys if k != "nowplaying"]
        self.assertEqual(self._spans(self._home()), want)

    def test_hiding_everything_says_so_instead_of_rendering_a_void(self):
        from flet_app import panels
        for k in panels.panel_keys("home"):
            prefs.set_panel_hidden("home", k, True)
        self.assertEqual(self._spans(self._home()), [])


class PanelLayoutSurvivesARestart(unittest.TestCase):
    """The layout editor wrote to memory only.

    prefs.save() listed nine fixed keys by hand and none of the per-surface
    panel keys, so reordering a card, resizing it or hiding it was lost the
    moment the app restarted - which is every time you open it.
    """

    class FakeStore:
        def __init__(self):
            self.data = {}

        async def set(self, k, v):
            self.data[k] = v

        async def get(self, k):
            return self.data.get(k)

    def setUp(self):
        prefs._V = prefs._defaults()

    def test_a_resized_hidden_reordered_surface_comes_back(self):
        import asyncio
        from flet_app import panels

        prefs.set_panel_width("home", "machines", "half")
        prefs.set_panel_hidden("home", "nowplaying", True)
        prefs.move_panel("home", "wat2do", -3)
        before = (prefs.panels("home"), prefs.panel_width("home", "machines"))

        store = self.FakeStore()
        self.assertTrue(asyncio.run(prefs.save(store)))

        # Restart: a fresh process starts from the shipped defaults.
        prefs._V = prefs._defaults()
        self.assertEqual(prefs.panels("home"), panels.panel_keys("home"))

        asyncio.run(prefs.load(store))
        self.assertEqual((prefs.panels("home"), prefs.panel_width("home", "machines")),
                         before)

    def test_every_surface_is_persisted_not_just_home(self):
        import asyncio
        from flet_app import panels

        store = self.FakeStore()
        asyncio.run(prefs.save(store))
        for surface in panels.SURFACES:
            for part in ("order", "hidden", "width"):
                with self.subTest(surface=surface, part=part):
                    self.assertIn(prefs._pkey(surface, part), store.data)


# ===================================================================
# Appearance: corner radius, accent glow, CRT overlay
# ===================================================================
class AppearanceStore(unittest.TestCase):
    """The three appearance knobs, from storage to the drawn control.

    Everything here is guarding one of two bugs this repo has already
    shipped: a boolean that came back from storage as the string "false"
    and read as True, and a feature whose keys `save()` never listed, so
    it worked all session and was gone at the next launch.
    """

    class FakeStore:
        def __init__(self, initial=None):
            self.data = dict(initial or {})

        async def set(self, k, v):
            self.data[k] = v

        async def get(self, k):
            return self.data.get(k)

    def setUp(self):
        self._saved = dict(prefs._V)
        prefs._V = prefs._defaults()
        prefs.apply_runtime()

    def tearDown(self):
        prefs._V.clear()
        prefs._V.update(self._saved)
        prefs.apply_runtime()

    # ---------------------------------------------------------- defaults
    def test_ships_rounded_with_no_effects(self):
        from flet_app import ui
        self.assertEqual(prefs.corner_radius(), ui.DEFAULT_RADIUS)
        self.assertIs(prefs.accent_glow(), False)
        self.assertIs(prefs.crt_overlay(), False)

    def test_the_shipped_radius_is_a_no_op(self):
        """8 has to redraw exactly what the app drew before the setting existed."""
        from flet_app import ui
        prefs.set_corner_radius(8)
        for nominal in (2, 3, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22):
            with self.subTest(nominal=nominal):
                self.assertEqual(ui.RAD(nominal), nominal)

    # ------------------------------------------------------------ coercion
    def test_a_boolean_stored_as_a_string_is_not_read_as_true(self):
        """bool("false") is True. That shipped here once; it does not again."""
        for falsey in ("false", "False", " FALSE ", "0", "no", "off", ""):
            with self.subTest(stored=falsey):
                prefs._V[prefs.KEY_GLOW] = falsey
                prefs._V[prefs.KEY_CRT] = falsey
                self.assertIs(prefs.accent_glow(), False)
                self.assertIs(prefs.crt_overlay(), False)

    def test_a_truthy_string_still_reads_as_on(self):
        for truthy in ("true", "True", "1", "yes", "on"):
            with self.subTest(stored=truthy):
                prefs._V[prefs.KEY_GLOW] = truthy
                self.assertIs(prefs.accent_glow(), True)

    def test_a_radius_stored_as_a_string_still_counts(self):
        prefs._V[prefs.KEY_RADIUS] = "0"
        self.assertEqual(prefs.corner_radius(), 0)

    def test_a_radius_that_is_not_on_the_menu_falls_back(self):
        """A value Settings cannot undo must not be drawn."""
        from flet_app import ui
        for junk in (7, 999, -4, "wide", None, [3]):
            with self.subTest(stored=junk):
                prefs._V[prefs.KEY_RADIUS] = junk
                self.assertEqual(prefs.corner_radius(), ui.DEFAULT_RADIUS)

    # --------------------------------------------------------- persistence
    def test_every_default_key_is_written_by_save(self):
        """The discipline test.

        KEY_BOTTOM_ENABLED was in `_defaults()` and therefore loaded, but
        not in save()'s hand-written payload, so the bottom-bar switch
        reset at every launch. save_payload() is derived from `_defaults()`
        now; this fails the moment someone goes back to listing keys.
        """
        payload = prefs.save_payload()
        for key in prefs._defaults():
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_appearance_survives_a_restart(self):
        store = self.FakeStore()
        prefs.set_corner_radius(0)
        prefs.set_accent_glow(True)
        prefs.set_crt_overlay(True)
        self.assertTrue(asyncio.run(prefs.save(store)))

        # Restart: a fresh process starts from the shipped defaults.
        prefs._V = prefs._defaults()
        prefs.apply_runtime()
        self.assertEqual(prefs.corner_radius(), 8)
        self.assertIs(prefs.accent_glow(), False)

        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.corner_radius(), 0)
        self.assertIs(prefs.accent_glow(), True)
        self.assertIs(prefs.crt_overlay(), True)

    def test_a_restart_through_a_string_only_store_still_works(self):
        """Some backends stringify everything on the way out. This is that."""
        class Stringly(AppearanceStore.FakeStore):
            async def get(self, k):
                v = self.data.get(k)
                return str(v).lower() if isinstance(v, bool) else v

        store = Stringly()
        prefs.set_accent_glow(True)
        prefs.set_crt_overlay(False)
        asyncio.run(prefs.save(store))
        prefs._V = prefs._defaults()
        asyncio.run(prefs.load(store))
        self.assertIs(prefs.accent_glow(), True)
        self.assertIs(prefs.crt_overlay(), False)

    def test_the_bottom_bar_switch_also_survives_now(self):
        """The bug that motivated deriving save() from the defaults."""
        store = self.FakeStore()
        prefs.set_bottom_bar_enabled(False)
        asyncio.run(prefs.save(store))
        prefs._V = prefs._defaults()
        asyncio.run(prefs.load(store))
        self.assertIs(prefs.bottom_bar_enabled(), False)

    # ------------------------------------------------------ runtime push
    def test_setting_the_radius_reaches_the_drawing_layer(self):
        from flet_app import ui
        for value in ui.RADIUS_CHOICES:
            with self.subTest(radius=value):
                prefs.set_corner_radius(value)
                self.assertEqual(ui.S.radius, value)

    def test_load_pushes_the_appearance_it_read(self):
        from flet_app import ui
        store = self.FakeStore({prefs.KEY_RADIUS: 0, prefs.KEY_GLOW: True,
                                prefs.KEY_CRT: True})
        asyncio.run(prefs.load(store))
        self.assertEqual(ui.S.radius, 0)
        self.assertIs(ui.S.glow, True)
        self.assertIs(ui.S.crt, True)


class RadiusReachesTheControls(unittest.TestCase):
    """Proof the number leaves the store and lands on real built controls.

    A setting that only changes a dict is the failure mode worth testing
    for here: every corner in the app was a literal until this, and the
    only way to know they are all routed is to build pages and look.
    """

    def setUp(self):
        self._saved = dict(prefs._V)
        prefs._V = prefs._defaults()
        prefs.apply_runtime()

    def tearDown(self):
        prefs._V.clear()
        prefs._V.update(self._saved)
        prefs.apply_runtime()

    class StubPage:
        def __init__(self):
            self.controls = []
            self.drawer = None
            self.theme = None
            self.theme_mode = None
            self.title = ""

        def update(self):
            pass

        def add(self, *controls):
            self.controls.extend(controls)

        def run_task(self, *a, **k):
            return None

        def run_thread(self, *a, **k):
            return None

        @property
        def services(self):
            return []

    @staticmethod
    def _walk(control, seen=None):
        import dataclasses
        import flet as ft
        if seen is None:
            seen = set()
        if not isinstance(control, ft.Control) or id(control) in seen:
            return []
        seen.add(id(control))
        found = [control]
        for field in dataclasses.fields(control):
            if field.name.startswith("_"):
                continue
            try:
                value = getattr(control, field.name)
            except Exception:  # noqa: BLE001 - a lazy property is not a control
                continue
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                found += RadiusReachesTheControls._walk(item, seen)
        return found

    def _radii(self, page_name):
        from flet_app import ui
        from flet_app.pages import home, lab, settings, tools, weather
        module = {"home": home, "lab": lab, "tools": tools,
                  "weather": weather, "settings": settings}[page_name]
        page = self.StubPage()
        ui.apply_theme(page)
        out = []
        for control in self._walk(module.build(page)):
            radius = getattr(control, "border_radius", None)
            if radius is None:
                continue
            try:
                out.append((control, float(radius)))
            except (TypeError, ValueError):
                pass
        return out

    @staticmethod
    def _is_circle(control, radius):
        """A dot, not a corner: square and rounded by at least half its side."""
        w, h = getattr(control, "width", None), getattr(control, "height", None)
        return (w is not None and w == h and radius >= w / 2 - 0.01)

    def test_zero_is_genuinely_square_everywhere(self):
        prefs.set_corner_radius(0)
        for name in ("home", "lab", "tools", "weather", "settings"):
            for control, radius in self._radii(name):
                if self._is_circle(control, radius):
                    continue           # a status dot is a circle, not a corner
                with self.subTest(page=name, control=type(control).__name__):
                    self.assertEqual(radius, 0)

    def test_the_setting_moves_every_corner_together(self):
        """Not one card, all of them - and each by the documented ratio.

        Which sites are circles is decided once, from the shipped
        geometry, and then held fixed. Re-deciding it per setting would
        misread a 10px swatch whose 3px corner scales past 5 at the
        softest setting as a circle, and quietly stop checking it.
        """
        from flet_app import ui
        prefs.set_corner_radius(8)
        shipped = {}
        for name in ("home", "settings"):
            shipped[name] = [(self._is_circle(c, r), r) for c, r in self._radii(name)]
            self.assertTrue(any(not circle for circle, _ in shipped[name]),
                            "no corners found on " + name)

        for value in (0, 3, 12, 16):
            prefs.set_corner_radius(value)
            for name in ("home", "settings"):
                now = [r for _, r in self._radii(name)]
                self.assertEqual(len(now), len(shipped[name]))
                for (was_circle, was), is_now in zip(shipped[name], now):
                    with self.subTest(radius=value, page=name, nominal=was):
                        self.assertEqual(is_now, was if was_circle
                                         else float(ui.RAD(int(was))))

    def test_a_round_dot_stays_round_at_zero(self):
        """Squaring the machine dots would be a bug, not the feature."""
        prefs.set_corner_radius(0)
        circles = [(c, r) for c, r in self._radii("home") if self._is_circle(c, r)]
        self.assertTrue(circles, "home draws status dots; none were found")
        for control, radius in circles:
            self.assertGreater(radius, 0)

    def test_turning_everything_on_does_not_add_controls_to_a_page(self):
        """The pages were cut from 1915 controls to a few hundred.

        A glow that wrapped each card, or an overlay per page, would undo
        that. Both are properties of controls that already exist; the only
        control the CRT adds is one, over the whole shell, in main.py.
        """
        prefs.set_corner_radius(8)
        before = {n: len(self._walk_page(n)) for n in ("home", "lab", "tools", "weather")}
        prefs.set_corner_radius(0)
        prefs.set_accent_glow(True)
        prefs.set_crt_overlay(True)
        after = {n: len(self._walk_page(n)) for n in ("home", "lab", "tools", "weather")}
        self.assertEqual(before, after)

    def _walk_page(self, name):
        from flet_app import ui
        from flet_app.pages import home, lab, tools, weather
        module = {"home": home, "lab": lab, "tools": tools, "weather": weather}[name]
        page = self.StubPage()
        ui.apply_theme(page)
        return self._walk(module.build(page))


class AccentGlow(unittest.TestCase):
    """The glow is off by default, themed when on, and touches the right things."""

    def setUp(self):
        self._saved = dict(prefs._V)
        prefs._V = prefs._defaults()
        prefs.apply_runtime()

    def tearDown(self):
        prefs._V.clear()
        prefs._V.update(self._saved)
        prefs.apply_runtime()

    def test_off_means_no_shadow_at_all(self):
        from flet_app import ui
        prefs.set_accent_glow(False)
        self.assertIsNone(ui.GLOW())
        self.assertIsNone(ui.GLOW_STYLE())
        self.assertIsNone(ui.title("Machines").style)
        self.assertEqual(ui.FOCUS_WIDTH(), 1)

    def test_on_puts_alolas_shadow_on_a_heading(self):
        from flet_app import ui
        prefs.set_accent_glow(True)
        style = ui.title("Machines").style
        self.assertIsNotNone(style)
        self.assertIsNotNone(style.shadow)
        self.assertEqual(ui.FOCUS_WIDTH(), 2)

    def test_the_glow_is_the_current_themes_accent_not_a_fixed_colour(self):
        """24 themes; a hard-coded yellow would be wrong on 23 of them."""
        from flet_app import theme_data, ui
        prefs.set_accent_glow(True)
        seen = {}
        for key in theme_data.THEMES:
            ui.S.theme = key
            seen[key] = ui.GLOW().color
            self.assertIn(theme_data.THEMES[key]["accent"].lstrip("#").lower(),
                          str(seen[key]).lower())
        ui.S.theme = theme_data.DEFAULT
        self.assertGreater(len(set(seen.values())), 1,
                           "every theme glowed the same colour")

    def test_the_active_nav_indicator_brightens_with_the_glow(self):
        from flet_app import ui

        class P:
            theme = None
            theme_mode = None

            def update(self):
                pass

        page = P()
        prefs.set_accent_glow(False)
        ui.apply_theme(page)
        off = page.theme.navigation_bar_theme.indicator_color
        self.assertIsNone(page.theme.focus_color)
        prefs.set_accent_glow(True)
        ui.apply_theme(page)
        self.assertNotEqual(page.theme.navigation_bar_theme.indicator_color, off)
        self.assertIsNotNone(page.theme.focus_color)


class CrtOverlayDoesNotEatTaps(unittest.TestCase):
    """The failure this guards is the one that makes the whole app look dead.

    A full-screen container over the content takes every tap unless it is
    explicitly removed from hit testing. That has shipped here before, in
    another form, and cost a day of looking at the handlers instead of at
    what was on top of them.
    """

    def setUp(self):
        self._saved = dict(prefs._V)
        prefs._V = prefs._defaults()
        prefs.apply_runtime()

    def tearDown(self):
        prefs._V.clear()
        prefs._V.update(self._saved)
        prefs.apply_runtime()

    def test_it_is_off_by_default(self):
        self.assertIs(prefs.crt_overlay(), False)

    def test_the_overlay_is_removed_from_hit_testing(self):
        from flet_app import ui
        overlay = ui.crt_overlay()
        self.assertIs(overlay.ignore_interactions, True)

    def test_the_overlay_carries_no_handler_of_its_own(self):
        """Belt and braces: nothing to fire even if the flag were dropped."""
        from flet_app import ui
        overlay = ui.crt_overlay()
        for attr in ("on_click", "on_tap_down", "on_long_press", "on_hover", "url"):
            with self.subTest(attr=attr):
                self.assertIsNone(getattr(overlay, attr, None))

    def test_every_control_in_the_overlay_ignores_interactions(self):
        from flet_app import ui
        controls = RadiusReachesTheControls._walk(ui.crt_overlay())
        self.assertEqual(len(controls), 1, "the overlay must stay one control")
        for control in controls:
            self.assertIs(getattr(control, "ignore_interactions", None), True)

    def test_a_button_under_the_overlay_still_fires(self):
        """Stack the overlay over a real handler and call it, as Flutter would.

        `ignore_interactions` is what stops the hit test ever reaching the
        overlay, so the control beneath keeps the handler it was built with
        and the tap runs it. This asserts the shape Flutter resolves a tap
        through: the overlay is last (on top), and the thing underneath is
        untouched by being covered.
        """
        import flet as ft
        from flet_app import ui

        fired = []
        button = ft.Container(on_click=lambda e: fired.append(True),
                              content=ft.Text("tap me"))
        shell = ft.Column(controls=[button])
        stack = ft.Stack(expand=True, controls=[shell, ui.crt_overlay()])

        self.assertIs(stack.controls[-1].ignore_interactions, True)
        self.assertIs(stack.controls[0], shell)
        under = stack.controls[0].controls[0]
        self.assertIsNotNone(under.on_click)
        under.on_click(None)
        self.assertEqual(fired, [True])


class BottomBarToggleDoesNotCrash(unittest.TestCase):
    """Hiding a bottom-bar row took the whole app down.

    settings._bump took a parameter called `on_layout` and its body said
    `callback`. Python resolves a global only when the line runs, so the
    module imported fine and every test that did not toggle a row passed -
    and the first time she hid one, the app died with "name 'callback' is
    not defined" and had to be quit.
    """

    def test_bump_calls_what_it_was_given(self):
        from flet_app.pages import settings
        seen = []
        settings._bump(lambda: seen.append(1))
        self.assertEqual(seen, [1])

    def test_bump_with_no_callback_is_a_no_op(self):
        from flet_app.pages import settings
        settings._bump(None)          # must not raise

    def test_bump_swallows_a_failing_repaint(self):
        # A repaint that throws must not lose the edit that triggered it.
        from flet_app.pages import settings

        def boom():
            raise RuntimeError("repaint failed")

        settings._bump(boom)

    def test_no_undefined_names_in_settings(self):
        """Catch the whole bug class, not just this one instance."""
        import ast
        import builtins
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "flet_app", "pages", "settings.py")
        tree = ast.parse(open(path).read())
        module_level = {n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    module_level.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        module_level.add(t.id)

        def check(fn):
            local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
            if fn.args.vararg:
                local.add(fn.args.vararg.arg)
            if fn.args.kwarg:
                local.add(fn.args.kwarg.arg)
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    for t in ast.walk(n):
                        if isinstance(t, ast.Name):
                            local.add(t.id)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    local.add(n.name)
                    local |= {a.arg for a in n.args.args}
                elif isinstance(n, (ast.For, ast.comprehension, ast.With, ast.ExceptHandler)):
                    for t in ast.walk(n):
                        if isinstance(t, ast.Name):
                            local.add(t.id)
            known = local | module_level | set(dir(builtins))
            return [n.id for n in ast.walk(fn)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id not in known]

        top = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        unknown = {fn.name: u for fn in top if (u := check(fn))}
        self.assertEqual(unknown, {}, f"names used but never bound: {unknown}")


# ===================================================================
# Driving the real Settings controls
# ===================================================================
class _StubPage:
    """Enough Page for a build() to finish. No event loop, no storage."""

    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []
        self.drawer = None
        self.theme = None
        self.theme_mode = None

    def update(self, *a, **k):
        pass

    def add(self, *c):
        self.controls.extend(c)


def _walk(control, seen=None):
    """Every control in a built tree, once each."""
    if seen is None:
        seen = set()
    if id(control) in seen:
        return
    seen.add(id(control))
    yield control
    for attr in ("controls", "content"):
        v = getattr(control, attr, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                if hasattr(x, "__dict__"):
                    yield from _walk(x, seen)
        elif v is not None and hasattr(v, "__dict__"):
            yield from _walk(v, seen)


def _of_type(tree, name):
    return [c for c in _walk(tree) if type(c).__name__ == name]


class SettingsDrivesTheTwoSurfacesSeparately(PrefsTestCase):
    """The real Settings row, tapped the way she taps it.

    Driven through the built control rather than through prefs, because
    the bug she hit was in the page, not the store, and the last one of
    those - a handler naming a parameter that did not exist - imported
    fine and passed every test that never pressed the button.
    """

    def _pills(self):
        from flet_app.pages import settings
        tree = settings.build(_StubPage())
        out = {}
        page_keys = prefs.page_order()
        found = []
        for c in _walk(tree):
            text = getattr(getattr(c, "content", None), "value", None)
            if text in ("Menu", "Bar") and getattr(c, "on_click", None) is not None:
                found.append((text, c))
            elif text in ("Menu", "Bar"):
                found.append((text, None))     # disabled - the last hold
        for i, key in enumerate(page_keys):
            out[key] = dict(found[i * 2:i * 2 + 2])
        return out

    def test_the_bar_pill_leaves_the_menu_alone(self):
        self.assertIn("garage", prefs.bottom_bar_pages())
        asyncio.run(self._pills()["garage"]["Bar"].on_click(None))
        self.assertNotIn("garage", prefs.bottom_bar_pages())
        self.assertIn("garage", prefs.visible_pages(),
                      "taking it off the bar must not take it out of the menu")

    def test_the_menu_pill_leaves_the_bar_alone(self):
        self.assertIn("lab", prefs.visible_pages())
        asyncio.run(self._pills()["lab"]["Menu"].on_click(None))
        self.assertNotIn("lab", prefs.visible_pages())
        self.assertIn("lab", prefs.bottom_bar_pages(),
                      "taking it out of the menu must not take it off the bar")

    def test_every_page_offers_one_pill_per_surface(self):
        pills = self._pills()
        self.assertEqual(sorted(pills), sorted(prefs.PAGE_KEYS))
        for key, row in pills.items():
            with self.subTest(page=key):
                self.assertEqual(sorted(row), ["Bar", "Menu"])

    def test_the_last_surface_holding_settings_is_not_tappable(self):
        pills = self._pills()
        self.assertIsNone(pills["settings"]["Menu"],
                          "Settings is the only way back from a layout you regret")
        self.assertIsNotNone(pills["settings"]["Bar"])
