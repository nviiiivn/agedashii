"""Tests for agedashii_tofu/prefs.py — the owner-editable layout and settings.

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
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu import prefs  # noqa: E402


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
        got = prefs.sanitise_order(["tools", "home"], prefs.PAGE_KEYS)
        self.assertEqual(got[:2], ["tools", "home"])
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
        prefs.set_in_bottom_bar("home", True)        # menu + bar
        prefs.set_page_in_menu("lab", False)         # bar only
        prefs.set_page_in_menu("tools", False)       # neither
        prefs.set_in_bottom_bar("tools", False)      # ...and off the bar
        self.assertEqual(
            {k: (k in prefs.visible_pages(), k in prefs.bottom_bar_pages())
             for k in ("home", "lab", "tools")},
            {"home": (True, True), "lab": (False, True),
             "tools": (False, False)})

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
        prefs.set_page_hidden("lab", True)
        prefs.set_page_hidden("lab", False)
        self.assertIn("lab", prefs.visible_pages())

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
        """Tofu has four pages and the cap is five, so the cap cannot be
        reached any more. What is still worth pinning down is that adding
        a page to the bar succeeds while there is room, and that the bar
        never grows past the cap however many are added."""
        self.assertTrue(prefs.set_in_bottom_bar("settings", True))
        for key in prefs.PAGE_KEYS:
            prefs.set_in_bottom_bar(key, True)
        self.assertLessEqual(len(prefs.bottom_bar_pages()), prefs.MAX_BOTTOM)
        self.assertEqual(len(prefs.bottom_bar_pages()),
                         min(len(prefs.PAGE_KEYS), prefs.MAX_BOTTOM))

    def test_bottom_bar_follows_the_page_order(self):
        prefs.set_page_order(["tools", "lab", "home", "settings"])
        prefs.set_in_bottom_bar("settings", True)
        self.assertEqual(prefs.bottom_bar_pages(),
                         ["tools", "lab", "home", "settings"])

    def test_moving_a_page_reorders_it(self):
        prefs.move_page("tools", -1)
        order = prefs.page_order()
        self.assertEqual(order.index("tools"), prefs.PAGE_KEYS.index("tools") - 1)


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
            "nvii.pages_hidden": ["lab"],
            "nvii.bottom_pages": ["home", "lab", "tools"],
        })
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(),
                         ["home", "tools"],
                         "the same four the coupled build was showing")
        self.assertNotIn("lab", prefs.visible_pages())

    def test_the_split_is_applied_once_and_never_again(self):
        """Otherwise every restart would re-couple the two sets.

        The second load has to leave a deliberately menu-hidden, bar-on
        page exactly as she set it - that combination is the feature.
        """
        store = FakePrefs({"nvii.pages_hidden": ["lab"],
                           "nvii.bottom_pages": ["home", "lab"]})
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(), ["home"])

        # Now she does the thing the split exists for: garage on the bar,
        # out of the menu.
        prefs.set_in_bottom_bar("lab", True)
        self.assertEqual(prefs.bottom_bar_pages(), ["home", "lab"])
        asyncio.run(prefs.save(store))

        prefs.reset_all()
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.bottom_bar_pages(), ["home", "lab"])
        self.assertNotIn("lab", prefs.visible_pages())

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
        from agedashii_tofu import api
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
        from agedashii_tofu.pages import settings
        from agedashii_tofu.ui import S
        before = S.prefs
        S.prefs = None
        try:
            control = settings.build(self._stub_page())
            self.assertTrue(control.controls)
        finally:
            S.prefs = before

    def test_builds_with_prefs(self):
        from agedashii_tofu.pages import settings
        from agedashii_tofu.ui import S
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


class DrawerSectionsCollapse(unittest.TestCase):
    """Chevrons on the drawer's Lab/Tools/... rows: collapse or expand,
    remembered, and persisted - "home, lab, tools, trips, garage,
    wat2do... it would be nice to be able to collapse or prolapse it".
    """

    def setUp(self):
        prefs._V = prefs._defaults()

    def test_untouched_defaults_to_open_only_when_current(self):
        """Sensible default: the page she is standing on shows its own
        tabs; everywhere else starts collapsed rather than the ten rows
        Tools alone used to always add."""
        self.assertTrue(prefs.drawer_section_open("garage", "garage"))
        self.assertFalse(prefs.drawer_section_open("garage", "home"))
        self.assertFalse(prefs.drawer_section_open("tools", "lab"))

    def test_an_explicit_choice_overrides_the_current_page_default(self):
        prefs.set_drawer_open("tools", True)
        # Stays open even standing somewhere else...
        self.assertTrue(prefs.drawer_section_open("tools", "home"))
        prefs.set_drawer_open("lab", False)
        # ...and stays closed even while standing on it.
        self.assertFalse(prefs.drawer_section_open("lab", "lab"))

    def test_an_unknown_page_is_refused(self):
        # A key from another app is not "unknown" - Moto opens Garage
        # through this same function, and checking against Tofu's
        # PAGE_KEYS silently cost Moto its Garage sub-rows. What is
        # still refused is a key that cannot round-trip: the store
        # holds "<key>:<flag>" strings.
        self.assertTrue(prefs.set_drawer_open("garage", True))
        self.assertFalse(prefs.set_drawer_open("has:colon", True))
        self.assertFalse(prefs.set_drawer_open("", True))
        self.assertFalse(prefs.set_drawer_open(None, True))

    def test_survives_a_round_trip_through_stringy_storage(self):
        """The same footgun `bottom_bar_enabled` guards against: storage
        can hand a value back as text, and `bool("false")` is True. This
        key never stores a raw bool at all - only the literal "1"/"0"
        suffix counts as explicit - so prove a stray "true"/"false" (what
        a *different* key would come back as) does not read as an
        explicit choice here either, and degrades to the untouched
        default instead of an arbitrary one.
        """
        # current_key="lab" throughout, so the untouched default for
        # "tools" is always False - anything that is not a real "1"/"0"
        # entry must fall through to that, not read as an explicit True.
        cases = {"tools:1": True, "tools:0": False, "tools:true": False,
                 "tools:false": False, "tools:": False, "tools:garbage": False}
        for stored, expected in cases.items():
            with self.subTest(stored=stored):
                prefs._V[prefs.KEY_DRAWER_OPEN] = [stored]
                self.assertIs(prefs.drawer_section_open("tools", "lab"), expected)

    def test_persists_through_save_and_load(self):
        """The save function once listed its keys by hand and silently
        dropped a whole feature; KEY_DRAWER_OPEN rides `_defaults()` into
        `save_payload()`'s generic loop instead, which this proves end to
        end rather than trusting the mechanism by inspection."""
        prefs.set_drawer_open("tools", True)
        prefs.set_drawer_open("lab", False)
        store = FakePrefs()
        self.assertTrue(asyncio.run(prefs.save(store)))

        prefs._V = prefs._defaults()
        self.assertFalse(prefs.drawer_section_open("tools", "home"))  # gone

        asyncio.run(prefs.load(store))
        self.assertTrue(prefs.drawer_section_open("tools", "home"))
        self.assertFalse(prefs.drawer_section_open("lab", "lab"))

    def test_a_page_with_no_subpages_has_nothing_to_toggle(self):
        """home and settings never call set_drawer_open in the real UI
        (no chevron is drawn for them at all - see main.py's page_row),
        but the store-level function itself stays honest either way: a
        page with no sub-items is never "current and expanded" in any
        way that matters, since nothing would ever be drawn under it."""
        self.assertFalse(prefs.drawer_section_open("home", "lab"))
        # Still legal to set (prefs doesn't know which pages have tabs -
        # main.SUBPAGES does), it is simply never read for a chevron that
        # is never drawn.
        self.assertTrue(prefs.set_drawer_open("home", True))


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
            # FoodZen left with Foodzen; Projects, AI and Obligations
            # arrived when Extras stopped being a page.
            "tools": ["Feeds", "Vault", "Journal", "Projects", "AI",
                      "Obligations"],
        }
        for group in ("tools", "lab"):
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
        from agedashii_tofu import panels
        self.panels = panels
        prefs._V = prefs._defaults()
        prefs.reset_panels(self.SURFACE)

    def test_defaults_are_shipped_order_all_visible_all_full(self):
        keys = self.panels.panel_keys(self.SURFACE)
        self.assertEqual(prefs.panels(self.SURFACE), keys)
        for k in keys:
            self.assertEqual(prefs.panel_width(self.SURFACE, k), 60)
            self.assertEqual(prefs.panel_height(self.SURFACE, k), 0)

    def test_width_round_trip(self):
        self.assertTrue(prefs.set_panel_width(self.SURFACE, "machines", 30))
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), 30)
        # Others are untouched.
        self.assertEqual(prefs.panel_width(self.SURFACE, "weather"), 60)

    def test_width_is_a_genuinely_custom_span_not_a_preset(self):
        """"are you just making things fit defaults?" - no: any of the 60
        columns is a real, storable, renderable width, not only the three
        that used to be the whole menu."""
        for span in (1, 7, 17, 29, 41, 53, 59, 60):
            with self.subTest(span=span):
                self.assertTrue(prefs.set_panel_width(self.SURFACE, "machines", span))
                self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), span)

    def test_a_width_back_to_full_is_not_stored(self):
        # Keeps the stored list to the panels actually customised, so a
        # renamed panel leaves no junk behind.
        prefs.set_panel_width(self.SURFACE, "machines", 30)
        prefs.set_panel_width(self.SURFACE, "machines", 60)
        self.assertEqual(prefs._V[prefs._pkey(self.SURFACE, "width")], [])

    def test_unknown_width_or_panel_is_refused(self):
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "machines", "huge"))
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "machines", 0))
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "machines", 61))
        self.assertFalse(prefs.set_panel_width(self.SURFACE, "nope", 30))
        self.assertFalse(prefs.set_panel_hidden(self.SURFACE, "nope", True))

    def test_a_corrupt_width_reads_as_full_rather_than_raising(self):
        # Losing a layout is an annoyance; a page that will not build is not.
        prefs._V[prefs._pkey(self.SURFACE, "width")] = [
            "machines:garbage", "machines:99", "not-a-pair", 42, None]
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), 60)

    def test_the_old_three_named_widths_still_read_back(self):
        """A layout saved before width became a range stored "full",
        "half" or "third" - never a number. Those must still come back at
        the span they meant, not silently reset to full."""
        prefs._V[prefs._pkey(self.SURFACE, "width")] = [
            "machines:half", "actions:third", "links:full"]
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), 30)
        self.assertEqual(prefs.panel_width(self.SURFACE, "actions"), 20)
        # "full" was never actually written by an old build (it was the
        # unstored default), but is still mapped correctly if it is found.
        self.assertEqual(prefs.panel_width(self.SURFACE, "links"), 60)

    def test_a_legacy_store_round_trips_through_load_and_save(self):
        """The actual upgrade path: an old install's SharedPreferences
        handed back "half"/"third" strings, load() must accept them, and
        the very next save() must write the numbers instead - not the
        names straight back."""
        store = FakePrefs({
            prefs._pkey(self.SURFACE, "width"): ["machines:half", "actions:third"],
            prefs.KEY_LAYOUT_SCHEME: prefs.LAYOUT_SCHEME,
        })
        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), 30)
        self.assertEqual(prefs.panel_width(self.SURFACE, "actions"), 20)
        asyncio.run(prefs.save(store))
        self.assertEqual(sorted(store.store[prefs._pkey(self.SURFACE, "width")]),
                         ["actions:20", "machines:30"])

    def test_height_round_trip(self):
        self.assertEqual(prefs.panel_height(self.SURFACE, "machines"), 0)
        self.assertTrue(prefs.set_panel_height(self.SURFACE, "machines", 4))
        self.assertEqual(prefs.panel_height(self.SURFACE, "machines"), 4)
        self.assertEqual(prefs.panel_height(self.SURFACE, "weather"), 0)

    def test_auto_height_is_not_stored(self):
        prefs.set_panel_height(self.SURFACE, "machines", 5)
        prefs.set_panel_height(self.SURFACE, "machines", 0)
        self.assertEqual(prefs._V[prefs._pkey(self.SURFACE, "height")], [])

    def test_unknown_height_or_panel_is_refused(self):
        self.assertFalse(prefs.set_panel_height(self.SURFACE, "machines", -1))
        self.assertFalse(prefs.set_panel_height(self.SURFACE, "machines", 9))
        self.assertFalse(prefs.set_panel_height(self.SURFACE, "nope", 3))

    def test_a_corrupt_height_reads_as_auto_rather_than_raising(self):
        prefs._V[prefs._pkey(self.SURFACE, "height")] = [
            "machines:garbage", "machines:99", "not-a-pair", 42, None]
        self.assertEqual(prefs.panel_height(self.SURFACE, "machines"), 0)

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
        prefs.set_panel_width(self.SURFACE, "machines", 20)
        prefs.set_panel_height(self.SURFACE, "machines", 3)
        prefs.set_panel_hidden(self.SURFACE, "weather", True)
        prefs.move_panel(self.SURFACE, "wat2do", -4)
        prefs.reset_panels(self.SURFACE)
        self.assertEqual(prefs.panels(self.SURFACE),
                         self.panels.panel_keys(self.SURFACE))
        self.assertEqual(prefs.panel_width(self.SURFACE, "machines"), 60)
        self.assertEqual(prefs.panel_height(self.SURFACE, "machines"), 0)

    def test_every_surface_declares_unique_keys(self):
        for surface, entries in self.panels.SURFACES.items():
            with self.subTest(surface=surface):
                keys = [k for k, _ in entries]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertIn(surface, self.panels.SURFACE_LABELS)

    def test_surfaces_are_independent(self):
        prefs.set_panel_width("home", "machines", 30)
        self.assertEqual(prefs.panel_width("weather", "hero"), 60)
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
        from agedashii_tofu.pages import home

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
        from agedashii_tofu import panels
        self.assertEqual(self._spans(self._home()),
                         [panels.COLUMNS] * len(panels.panel_keys("home")))

    def test_a_custom_width_shows_up_in_the_render(self):
        """Any span, not just the old three presets, reaches the tree."""
        from agedashii_tofu import panels
        keys = panels.panel_keys("home")
        prefs.set_panel_width("home", "machines", 30)
        prefs.set_panel_width("home", "actions", 17)
        prefs.set_panel_hidden("home", "nowplaying", True)
        want = [{"machines": 30, "actions": 17}.get(k, panels.COLUMNS)
                for k in keys if k != "nowplaying"]
        self.assertEqual(self._spans(self._home()), want)

    def test_hiding_everything_says_so_instead_of_rendering_a_void(self):
        from agedashii_tofu import panels
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
        from agedashii_tofu import panels

        prefs.set_panel_width("home", "machines", 30)
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
        from agedashii_tofu import panels

        store = self.FakeStore()
        asyncio.run(prefs.save(store))
        for surface in panels.SURFACES:
            for part in ("order", "hidden", "width", "height"):
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
        from agedashii_tofu import ui
        self.assertEqual(prefs.corner_radius(), ui.DEFAULT_RADIUS)
        self.assertIs(prefs.accent_glow(), False)
        self.assertIs(prefs.crt_overlay(), False)
        self.assertEqual(prefs.crt_intensity(), prefs.DEFAULT_CRT_INTENSITY)

    def test_crt_intensity_defaults_to_tasteful_not_maximum(self):
        """The slider ships partway up, not pinned to 100 - turning the
        overlay on for the first time should not be the harshest it gets."""
        self.assertGreater(prefs.crt_intensity(), 0)
        self.assertLess(prefs.crt_intensity(), 100)

    def test_the_shipped_radius_is_a_no_op(self):
        """8 has to redraw exactly what the app drew before the setting existed."""
        from agedashii_tofu import ui
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
        from agedashii_tofu import ui
        for junk in (7, 999, -4, "wide", None, [3]):
            with self.subTest(stored=junk):
                prefs._V[prefs.KEY_RADIUS] = junk
                self.assertEqual(prefs.corner_radius(), ui.DEFAULT_RADIUS)

    def test_an_intensity_stored_as_a_string_still_counts(self):
        prefs._V[prefs.KEY_CRT_INTENSITY] = "70"
        self.assertEqual(prefs.crt_intensity(), 70)

    def test_an_out_of_range_or_junk_intensity_falls_back_to_the_default(self):
        """A slider value Settings cannot show back must not draw at all."""
        for junk in (-1, 101, 5000, "loud", None, [3], "3.5.2"):
            with self.subTest(stored=junk):
                prefs._V[prefs.KEY_CRT_INTENSITY] = junk
                self.assertEqual(prefs.crt_intensity(), prefs.DEFAULT_CRT_INTENSITY)

    def test_boundary_intensities_are_valid(self):
        """0 and 100 are the ends of the slider, not out-of-range junk."""
        for edge in (0, 100):
            with self.subTest(stored=edge):
                prefs._V[prefs.KEY_CRT_INTENSITY] = edge
                self.assertEqual(prefs.crt_intensity(), edge)

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
        prefs.set_crt_intensity(80)
        self.assertTrue(asyncio.run(prefs.save(store)))

        # Restart: a fresh process starts from the shipped defaults.
        prefs._V = prefs._defaults()
        prefs.apply_runtime()
        self.assertEqual(prefs.corner_radius(), 8)
        self.assertIs(prefs.accent_glow(), False)
        self.assertEqual(prefs.crt_intensity(), prefs.DEFAULT_CRT_INTENSITY)

        asyncio.run(prefs.load(store))
        self.assertEqual(prefs.corner_radius(), 0)
        self.assertIs(prefs.accent_glow(), True)
        self.assertIs(prefs.crt_overlay(), True)
        self.assertEqual(prefs.crt_intensity(), 80)

    def test_crt_intensity_round_trips_through_save_and_load(self):
        """The save/load round trip the task called out by name."""
        store = self.FakeStore()
        for value in (0, 1, 40, 99, 100):
            with self.subTest(intensity=value):
                prefs.set_crt_intensity(value)
                asyncio.run(prefs.save(store))
                prefs._V = prefs._defaults()
                asyncio.run(prefs.load(store))
                self.assertEqual(prefs.crt_intensity(), value)

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
        from agedashii_tofu import ui
        for value in ui.RADIUS_CHOICES:
            with self.subTest(radius=value):
                prefs.set_corner_radius(value)
                self.assertEqual(ui.S.radius, value)

    def test_setting_the_intensity_reaches_the_drawing_layer(self):
        from agedashii_tofu import ui
        for value in (0, 25, 60, 100):
            with self.subTest(intensity=value):
                prefs.set_crt_intensity(value)
                self.assertEqual(ui.S.crt_intensity, value)

    def test_load_pushes_the_appearance_it_read(self):
        from agedashii_tofu import ui
        store = self.FakeStore({prefs.KEY_RADIUS: 0, prefs.KEY_GLOW: True,
                                prefs.KEY_CRT: True, prefs.KEY_CRT_INTENSITY: 90})
        asyncio.run(prefs.load(store))
        self.assertEqual(ui.S.radius, 0)
        self.assertIs(ui.S.glow, True)
        self.assertIs(ui.S.crt, True)
        self.assertEqual(ui.S.crt_intensity, 90)


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
        from agedashii_tofu import ui
        from agedashii_tofu.pages import home, lab, settings, tools
        module = {"home": home, "lab": lab, "tools": tools,
                  "settings": settings}[page_name]
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
        for name in ("home", "lab", "tools", "settings"):
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
        from agedashii_tofu import ui
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
        before = {n: len(self._walk_page(n)) for n in ("home", "lab", "tools")}
        prefs.set_corner_radius(0)
        prefs.set_accent_glow(True)
        prefs.set_crt_overlay(True)
        after = {n: len(self._walk_page(n)) for n in ("home", "lab", "tools")}
        self.assertEqual(before, after)

    def _walk_page(self, name):
        from agedashii_tofu import ui
        from agedashii_tofu.pages import home, lab, tools
        module = {"home": home, "lab": lab, "tools": tools}[name]
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
        from agedashii_tofu import ui
        prefs.set_accent_glow(False)
        self.assertIsNone(ui.GLOW())
        self.assertIsNone(ui.GLOW_STYLE())
        self.assertIsNone(ui.title("Machines").style)
        self.assertEqual(ui.FOCUS_WIDTH(), 1)

    def test_on_puts_alolas_shadow_on_a_heading(self):
        from agedashii_tofu import ui
        prefs.set_accent_glow(True)
        style = ui.title("Machines").style
        self.assertIsNotNone(style)
        self.assertIsNotNone(style.shadow)
        self.assertEqual(ui.FOCUS_WIDTH(), 2)

    def test_the_glow_is_the_current_themes_accent_not_a_fixed_colour(self):
        """24 themes; a hard-coded yellow would be wrong on 23 of them."""
        from agedashii_tofu import theme_data, ui
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
        from agedashii_tofu import ui

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
        from agedashii_tofu import ui
        overlay = ui.crt_overlay()
        self.assertIs(overlay.ignore_interactions, True)

    def test_the_overlay_carries_no_handler_of_its_own(self):
        """Belt and braces: nothing to fire even if the flag were dropped."""
        from agedashii_tofu import ui
        overlay = ui.crt_overlay()
        for attr in ("on_click", "on_tap_down", "on_long_press", "on_hover", "url"):
            with self.subTest(attr=attr):
                self.assertIsNone(getattr(overlay, attr, None))

    def test_every_control_in_the_overlay_ignores_interactions(self):
        """The overlay is layered now - vignette+glow, scanlines, halftone -
        so this is three nested containers, not the one it used to be. What
        must still hold is that the count is small and *fixed*: it may not
        grow with page content, and every layer independently carries the
        flag rather than relying on the outermost one alone.
        """
        from agedashii_tofu import ui
        controls = RadiusReachesTheControls._walk(ui.crt_overlay())
        self.assertEqual(len(controls), 3,
                         "the overlay must stay a small, fixed number of "
                         "controls - one per visual layer, not one per page")
        for control in controls:
            self.assertIs(getattr(control, "ignore_interactions", None), True)

    def test_intensity_scales_every_layer_through_one_lever(self):
        """One number, S.crt_intensity, has to move all three layers
        together - not three settings a future edit could desync."""
        from agedashii_tofu import ui
        self._saved_intensity = ui.S.crt_intensity
        try:
            # The slider spans 0..CRT_MAX_OPACITY, not 0..1. The old scale
            # spent two thirds of its travel above the point she called
            # "far too intense", so every setting over about 30 looked the
            # same kind of wrong and the control was useless. Her words:
            # "the current 30% should be the NEW 100%.. so essentially
            # intensity focuses on the area only between 0-30%".
            cap = ui.CRT_MAX_OPACITY
            self.assertEqual(cap, 0.30)
            ui.S.crt_intensity = 0
            self.assertEqual(ui.crt_overlay().opacity, 0.0)
            ui.S.crt_intensity = 100
            self.assertAlmostEqual(ui.crt_overlay().opacity, cap)
            ui.S.crt_intensity = 25
            self.assertAlmostEqual(ui.crt_overlay().opacity, 0.25 * cap)
            # Only the outermost container carries the lever; the two
            # nested layers stay at full internal opacity and are scaled
            # by inheriting the outer Flutter Opacity, not by a value of
            # their own that could drift out of sync with it.
            overlay = ui.crt_overlay()
            self.assertEqual(overlay.content.opacity, 1.0)
            self.assertEqual(overlay.content.content.opacity, 1.0)
        finally:
            ui.S.crt_intensity = self._saved_intensity

    def test_an_out_of_range_intensity_does_not_raise(self):
        from agedashii_tofu import ui
        self._saved_intensity = ui.S.crt_intensity
        try:
            for junk in (-50, 999, 100.9):
                with self.subTest(intensity=junk):
                    ui.S.crt_intensity = junk
                    overlay = ui.crt_overlay()
                    self.assertGreaterEqual(overlay.opacity, 0.0)
                    self.assertLessEqual(overlay.opacity, 1.0)
        finally:
            ui.S.crt_intensity = self._saved_intensity

    def test_a_button_under_the_overlay_still_fires(self):
        """Stack the overlay over a real handler and call it, as Flutter would.

        `ignore_interactions` is what stops the hit test ever reaching the
        overlay, so the control beneath keeps the handler it was built with
        and the tap runs it. This asserts the shape Flutter resolves a tap
        through: the overlay is last (on top), and the thing underneath is
        untouched by being covered.
        """
        import flet as ft
        from agedashii_tofu import ui

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
        from agedashii_tofu.pages import settings
        seen = []
        settings._bump(lambda: seen.append(1))
        self.assertEqual(seen, [1])

    def test_bump_with_no_callback_is_a_no_op(self):
        from agedashii_tofu.pages import settings
        settings._bump(None)          # must not raise

    def test_bump_swallows_a_failing_repaint(self):
        # A repaint that throws must not lose the edit that triggered it.
        from agedashii_tofu.pages import settings

        def boom():
            raise RuntimeError("repaint failed")

        settings._bump(boom)

    def test_no_undefined_names_in_settings(self):
        """Catch the whole bug class, not just this one instance."""
        import ast
        import builtins
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "agedashii_tofu", "pages", "settings.py")
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

    def test_no_undefined_names_in_main(self):
        """The same bug class, in the file where it would matter most.

        main.py is one giant `async def main(page):` with every handler -
        the drawer, its new chevrons, the render loop - nested inside it
        as closures, so this walks that single top-level function exactly
        the way the settings.py version walks each of its top-level ones:
        a name used but never bound anywhere in reach (not a parameter,
        not an assignment, not a nested def, not an import, not a
        builtin) is exactly the class of typo that imports clean and only
        crashes the first time the line actually runs - which for a
        chevron nobody had tapped yet could have sat unnoticed for a
        while.
        """
        import ast
        import builtins
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "main.py")
        tree = ast.parse(open(path).read())
        module_level = {n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    module_level.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.Assign):
                # Not just `if isinstance(t, ast.Name)`: main.py's own
                # `ORANGE, PINK, CYAN = "...", "...", "..."` is a tuple
                # target, whose Name nodes only show up one level down.
                for t in node.targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name):
                            module_level.add(sub.id)

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
                    # main.py nests keyword-only-argument handlers (e.g.
                    # `def drain(*, force=False):`), which `args.args`
                    # alone misses - the settings.py version of this test
                    # never needed kwonlyargs/vararg/kwarg here because
                    # settings.py happens not to nest one.
                    local.add(n.name)
                    local |= {a.arg for a in n.args.args} | {a.arg for a in n.args.kwonlyargs}
                    if n.args.vararg:
                        local.add(n.args.vararg.arg)
                    if n.args.kwarg:
                        local.add(n.args.kwarg.arg)
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
# Arrange the cards on the page itself
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


class CardEditMode(unittest.TestCase):
    """Drag and resize the cards on the page, not in a list in Settings.

    Her ask: "is there way to translate my web feature where i basically
    can take any page and resize the component boxes/frames and organize
    them and then save that view?" The website does it with GridStack on a
    12-column free x/y grid, saved to localStorage. This is the same
    gesture over the model the app already has - order plus a responsive
    width - because absolute x/y that reads well at 1400px is unusable on
    the phone this is actually used on, and because there is already one
    store for layout and a second one would be a second source of truth.
    """

    SURFACE = "home"

    def setUp(self):
        from agedashii_tofu import panels
        self.panels = panels
        self._saved = dict(prefs._V)
        prefs._V = prefs._defaults()
        prefs.reset_panels(self.SURFACE)
        panels.set_editing(False)

    def tearDown(self):
        self.panels.set_editing(False)
        prefs._V.clear()
        prefs._V.update(self._saved)

    def _home(self):
        from agedashii_tofu.pages import home
        return home.build(_StubPage())

    def _spans(self, tree):
        """The column spans of the panel row, and nothing nested in a card."""
        for c in _walk(tree):
            if type(c).__name__ == "ResponsiveRow":
                return [getattr(x, "col", {}).get("md") for x in c.controls]
        return []

    def _reorderable(self, tree):
        for c in _walk(tree):
            if type(c).__name__ == "ReorderableListView":
                return c
        return None

    def _card_labels(self):
        """The edit-mode header label of each card, in rendered order.

        Read off the header Row that holds the drag handle, so the test
        follows the tree rather than a hardcoded chain of attributes that
        breaks whenever the chrome is rearranged.
        """
        out = []
        for c in _walk(self._home()):
            if type(c).__name__ != "Row":
                continue
            kids = list(getattr(c, "controls", []) or [])
            if kids and type(kids[0]).__name__ == "ReorderableDragHandle":
                out.append(kids[1].value)
        return out

    # ---------------------------------------------------------- off
    def test_it_is_off_until_she_asks(self):
        self.assertFalse(self.panels.editing())

    def test_normal_mode_adds_no_control_at_all(self):
        """The pages were just cut from ~1900 controls to ~240.

        A wrapper per card, drawn whether or not she is arranging
        anything, would start giving that back - and a page that takes a
        second to build is a page whose buttons "do nothing", because
        Flet holds the old frame while the handler runs. Checks both the
        old hand-rolled mechanism (must never come back) and the new one
        (must never leak into the mode that never asked for it).
        """
        tree = self._home()
        for name in ("DragTarget", "Draggable", "GestureDetector",
                     "ReorderableListView", "ReorderableDragHandle"):
            self.assertEqual(_of_type(tree, name), [], name)

    def test_edit_mode_is_the_only_thing_that_costs_controls(self):
        plain = len(list(_walk(self._home())))
        self.panels.set_editing(True)
        edit = len(list(_walk(self._home())))
        cards = len(prefs.panels(self.SURFACE))
        self.assertEqual(len(_of_type(self._home(), "ReorderableDragHandle")), cards)
        self.assertEqual((edit - plain) % cards, 0)
        self.assertLess((edit - plain) / cards, 12,
                        "a card's edit chrome is a header and two pills, not a page")

    # ---------------------------------------------------------- drag
    def test_dragging_one_card_onto_another_reorders_the_page(self):
        """Simulated the way Flet's own on_reorder contract works: pop the
        moved item from old_index, insert it at new_index, no further
        adjustment - see panels._on_reorder."""
        self.panels.set_editing(True)
        before = prefs.panels(self.SURFACE)
        tree = self._home()
        rlv = self._reorderable(tree)
        self.assertEqual(len(rlv.controls), len(before))

        rlv.on_reorder(types.SimpleNamespace(old_index=len(before) - 1, new_index=0))

        want = [before[-1]] + before[:-1]
        self.assertEqual(prefs.panels(self.SURFACE), want)
        # ...and it reaches the tree, not just the store.
        self.assertEqual(self._card_labels(),
                         [self.panels.panel_labels(self.SURFACE)[k] for k in want])

    def test_a_drop_on_itself_changes_nothing(self):
        self.panels.set_editing(True)
        before = prefs.panels(self.SURFACE)
        rlv = self._reorderable(self._home())
        rlv.on_reorder(types.SimpleNamespace(old_index=2, new_index=2))
        self.assertEqual(prefs.panels(self.SURFACE), before)

    def test_a_malformed_reorder_event_changes_nothing(self):
        """A raise inside a gesture handler is invisible - the list simply
        stops responding - so an event shaped differently than Flet's own
        contract must be a no-op, not a crash."""
        self.panels.set_editing(True)
        before = prefs.panels(self.SURFACE)
        rlv = self._reorderable(self._home())
        rlv.on_reorder(types.SimpleNamespace(old_index=None, new_index=None))
        self.assertEqual(prefs.panels(self.SURFACE), before)
        rlv.on_reorder(types.SimpleNamespace())
        self.assertEqual(prefs.panels(self.SURFACE), before)

    # ---------------------------------------------------------- width
    def _grips(self, tree):
        """Every width grip - not the height pill, which is also a bare
        GestureDetector but never sets a horizontal-drag handler."""
        return [g for g in _of_type(tree, "GestureDetector")
                if getattr(g, "on_horizontal_drag_start", None) is not None]

    def _grip(self, index=0):
        tree = self._home()
        return self._grips(tree)[index], tree

    def _grip_label(self, index=0):
        grip, _ = self._grip(index)
        for c in _walk(grip):
            if type(c).__name__ == "Text":
                return c.value
        return None

    def test_tapping_the_width_pill_cycles_the_common_fractions(self):
        """Full -> 3/4 -> 2/3 -> 1/2 -> ..., wrapping - the fast path to a
        usual width, not the only reachable one (that is the drag, below).
        """
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        got = []
        for _ in range(3):
            self._grip()[0].on_tap(None)
            got.append(prefs.panel_width(self.SURFACE, first))
        self.assertEqual(got, list(self.panels.COMMON_WIDTHS[1:4]))

    def test_the_new_width_reaches_the_rendered_tree(self):
        """Edit mode stacks one card per row (see panels.layout's
        docstring for why a ReorderableListView cannot seat two side by
        side), so what "reaches the tree" here is the grip's own label,
        not a ResponsiveRow column span - that still applies the moment
        she leaves edit mode, covered by PanelLayoutReachesTheScreen.
        """
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        self._grip()[0].on_tap(None)
        self.assertEqual(self._grip_label(),
                         self.panels.width_label(prefs.panel_width(self.SURFACE, first)))
        self._grip()[0].on_tap(None)
        self.assertEqual(self._grip_label(),
                         self.panels.width_label(prefs.panel_width(self.SURFACE, first)))

    def _at(self, x):
        """A drag event at horizontal position `x`."""
        import types
        return types.SimpleNamespace(
            local_position=types.SimpleNamespace(x=float(x), y=0.0))

    def _sweep(self, grip, *xs):
        grip.on_horizontal_drag_start(self._at(xs[0]))
        for x in xs[1:]:
            grip.on_horizontal_drag_update(self._at(x))
        grip.on_horizontal_drag_end(self._at(xs[-1]))

    def test_dragging_the_grip_sideways_resizes(self):
        """`on_horizontal_drag_*`, not `on_pan_*`, on purpose.

        These cards live in a vertically scrolling ReorderableListView. A
        pan recognizer would contend with both the list's own scroll and
        its own reorder drag - both vertical - for the same gesture arena,
        so a diagonal drag would win one of those instead of resizing the
        card. A horizontal-only recognizer never contends with either.
        """
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        self.assertEqual(prefs.panel_width(self.SURFACE, first), self.panels.COLUMNS)
        self._sweep(self._grip()[0], 200, 120)           # 80px left = 10 columns
        narrower = prefs.panel_width(self.SURFACE, first)
        self.assertEqual(narrower, 50, "leftwards is narrower")
        self._sweep(self._grip()[0], 100, 170)           # 70px right
        self.assertGreater(prefs.panel_width(self.SURFACE, first), narrower,
                           "rightwards is wider")

    def test_a_long_drag_moves_further_than_a_short_one(self):
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        self._sweep(self._grip()[0], 300, 260)           # 40px left = 5 columns
        short_move = self.panels.COLUMNS - prefs.panel_width(self.SURFACE, first)
        prefs.reset_panels(self.SURFACE)
        self._sweep(self._grip()[0], 300, 140)           # 160px left = 20 columns
        long_move = self.panels.COLUMNS - prefs.panel_width(self.SURFACE, first)
        self.assertGreater(long_move, short_move)
        # 160px is exactly 20 columns with no snap tolerance to absorb -
        # 60 - 20 = 40, which also happens to be the 2/3 stop, so this is
        # a real regression check, not only a direction check.
        self.assertEqual(prefs.panel_width(self.SURFACE, first), 40)

    def test_the_drag_clamps_instead_of_wrapping(self):
        """Hard right means "as wide as it goes", not back round to 1/60."""
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        self._sweep(self._grip()[0], 600, 0)             # far left
        self.assertEqual(prefs.panel_width(self.SURFACE, first), self.panels.MIN_WIDTH)
        self._sweep(self._grip()[0], 0, 600)             # far right
        self.assertEqual(prefs.panel_width(self.SURFACE, first), self.panels.COLUMNS)

    def test_a_twitch_is_not_a_resize(self):
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        self._sweep(self._grip()[0], 200, 197, 195)      # 5px, under one column
        self.assertEqual(prefs.panel_width(self.SURFACE, first), self.panels.COLUMNS)

    def test_a_drag_lands_exactly_where_she_put_it(self):
        """No snapping. This asserted the opposite until she said "i still
        can't customize the sizing its like 'your way' or nothing" - a drag
        to 42 was being pulled to the 40 stop two columns away, and with
        stops at 60/45/40/30/20/15/12/10 that captured much of the range.
        Tapping the grip still jumps between the tidy fractions; dragging
        is literal."""
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        # 60 - 18 columns = 42, one column short of the 40 stop. It must
        # stay 42.
        self._sweep(self._grip()[0], 300, 156)           # 144px left = 18 columns
        self.assertEqual(prefs.panel_width(self.SURFACE, first), 42)

    def test_every_column_between_the_stops_is_reachable(self):
        """The point of a custom width: the values a snap would have eaten."""
        for span in (42, 41, 39, 31, 29, 22, 19, 13, 11, 7, 3):
            with self.subTest(span=span):
                self.assertTrue(prefs.set_panel_width(self.SURFACE, "weather", span))
                self.assertEqual(prefs.panel_width(self.SURFACE, "weather"), span)

    def test_an_event_shaped_differently_costs_the_drag_not_the_app(self):
        """A gesture handler that raises is invisible; the card just dies."""
        self.panels.set_editing(True)
        first = prefs.panels(self.SURFACE)[0]
        grip = self._grip()[0]
        grip.on_horizontal_drag_start(types.SimpleNamespace())
        grip.on_horizontal_drag_update(types.SimpleNamespace())
        grip.on_horizontal_drag_end(types.SimpleNamespace())
        self.assertEqual(prefs.panel_width(self.SURFACE, first), self.panels.COLUMNS)
        self._grip()[0].on_tap(None)          # ...and the tap still works
        self.assertEqual(prefs.panel_width(self.SURFACE, first),
                         self.panels.COMMON_WIDTHS[1])

    # ---------------------------------------------------------- persistence
    def test_an_arrangement_made_on_the_page_survives_a_restart(self):
        """Through prefs.save, the store Settings already writes.

        The website keeps its GridStack layout in
        localStorage['nvii-ov-layout-v1'], which is per browser and
        invisible to everything else. Inventing a second store here would
        have made the same mistake inside one app.
        """
        self.panels.set_editing(True)
        before = prefs.panels(self.SURFACE)
        rlv = self._reorderable(self._home())
        rlv.on_reorder(types.SimpleNamespace(old_index=len(before) - 1, new_index=0))
        self._grip()[0].on_tap(None)
        want = (prefs.panels(self.SURFACE),
                [prefs.panel_width(self.SURFACE, k)
                 for k in prefs.panels(self.SURFACE)])

        store = FakePrefs()
        self.assertTrue(asyncio.run(prefs.save(store)))
        prefs._V = prefs._defaults()
        self.panels.set_editing(False)
        self.assertNotEqual(prefs.panels(self.SURFACE), want[0])

        asyncio.run(prefs.load(store))
        self.assertEqual((prefs.panels(self.SURFACE),
                          [prefs.panel_width(self.SURFACE, k)
                           for k in prefs.panels(self.SURFACE)]), want)

    def test_only_the_handle_is_draggable_not_the_whole_card(self):
        """Otherwise the surface cannot be scrolled while arranging it.

        `ReorderableDragHandle` wraps only the grip icon, not the card, for
        the same reason the old Draggable did: it competes with the
        enclosing scrollable (now also the list's own reorder drag) for
        the same vertical gesture and wins it, so a card draggable edge to
        edge would pin the page in place in edit mode - on a phone, with
        eight cards, most of them out of reach.
        """
        self.panels.set_editing(True)
        cards = len(prefs.panels(self.SURFACE))
        handles = _of_type(self._home(), "ReorderableDragHandle")
        self.assertEqual(len(handles), cards)
        for h in handles:
            with self.subTest():
                self.assertEqual(type(h.content).__name__, "Icon")

    def test_leaving_edit_mode_gives_the_plain_page_back(self):
        self.panels.set_editing(True)
        self.assertTrue(_of_type(self._home(), "ReorderableListView"))
        self.panels.set_editing(False)
        self.assertEqual(_of_type(self._home(), "ReorderableListView"), [])
        self.assertEqual(_of_type(self._home(), "ReorderableDragHandle"), [])

    # ---------------------------------------------------------- affordance
    def test_a_page_with_a_card_stack_says_so(self):
        self.panels.begin_render()
        self.assertFalse(self.panels.has_surface())
        self._home()
        self.assertTrue(self.panels.has_surface())

    def test_a_page_without_one_does_not(self):
        """So the topbar leaves Customize out rather than drawing a dead
        button - which is exactly what the search icon here used to be.

        Settings has cards but not a *rearrangeable* stack: it never
        calls `panels.layout()`, so there is nothing for edit mode to do.
        """
        from agedashii_tofu.pages import settings
        self.panels.begin_render()
        settings.build(_StubPage())
        self.assertFalse(self.panels.has_surface())


class SettingsDrivesTheTwoSurfacesSeparately(PrefsTestCase):
    """The real Settings row, tapped the way she taps it.

    Driven through the built control rather than through prefs, because
    the bug she hit was in the page, not the store, and the last one of
    those - a handler naming a parameter that did not exist - imported
    fine and passed every test that never pressed the button.
    """

    def _pills(self):
        from agedashii_tofu.pages import settings
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
        self.assertIn("lab", prefs.bottom_bar_pages())
        asyncio.run(self._pills()["lab"]["Bar"].on_click(None))
        self.assertNotIn("lab", prefs.bottom_bar_pages())
        self.assertIn("lab", prefs.visible_pages(),
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


class PanelReorderByDrop(PrefsTestCase):
    """`move_panel_onto` - what a drag means, next to the arrows' one step."""

    def test_dropping_downwards_lands_after_the_target(self):
        self.assertTrue(prefs.move_panel_onto("home", "greeting", "weather"))
        order = prefs.panel_order("home")
        self.assertEqual(order.index("greeting"), order.index("weather") + 1)

    def test_dropping_upwards_lands_before_the_target(self):
        self.assertTrue(prefs.move_panel_onto("home", "wat2do", "stats"))
        order = prefs.panel_order("home")
        self.assertEqual(order.index("wat2do"), order.index("stats") - 1)

    def test_a_hidden_neighbour_keeps_its_place(self):
        """Order is kept for every panel, hidden included, so un-hiding
        one brings it back where she left it and not at the end."""
        prefs.set_panel_hidden("home", "nowplaying", True)
        prefs.move_panel_onto("home", "wat2do", "greeting")
        self.assertIn("nowplaying", prefs.panel_order("home"))

    def test_unknown_or_self_is_refused(self):
        self.assertFalse(prefs.move_panel_onto("home", "greeting", "greeting"))
        self.assertFalse(prefs.move_panel_onto("home", "nope", "greeting"))
        self.assertFalse(prefs.move_panel_onto("home", "greeting", "nope"))

    def test_cycling_a_width_wraps(self):
        """One column (of 60) at a time, wrapping at the ends - the
        resize grip's drag, which calls this once per column crossed."""
        from agedashii_tofu import panels
        self.assertEqual(prefs.cycle_panel_width("home", "weather", -1), 59)
        prefs.reset_panels("home")
        # Already at full (the default); one more step wraps past the top
        # of the range down to the bottom, not to 61.
        self.assertEqual(prefs.cycle_panel_width("home", "weather", 1),
                         panels.MIN_WIDTH, "wrapping past full")

    def test_cycling_a_width_can_clamp_instead(self):
        """`wrap=False`, for the resize grip's drag: overshooting the top
        of the range stops at full rather than coming out the other side
        near 1/60. Already-at-the-limit correctly reports "nothing
        changed" (None) rather than re-announcing the same span."""
        from agedashii_tofu import panels
        prefs.set_panel_width("home", "weather", panels.MAX_WIDTH - 2)
        self.assertEqual(
            prefs.cycle_panel_width("home", "weather", 5, wrap=False),
            panels.MAX_WIDTH)
        self.assertIsNone(
            prefs.cycle_panel_width("home", "weather", 5, wrap=False),
            "already at the limit - nothing changed")

    def test_cycling_an_unknown_panel_is_refused(self):
        self.assertIsNone(prefs.cycle_panel_width("home", "nope"))
        self.assertIsNone(prefs.cycle_panel_width_preset("home", "nope"))
        self.assertIsNone(prefs.cycle_panel_height("home", "nope"))

    def test_cycling_the_width_presets_wraps_through_the_common_fractions(self):
        """The tap shortcut: full -> 3/4 -> 2/3 -> ... -> 1/6 -> full."""
        from agedashii_tofu import panels
        got = [prefs.cycle_panel_width_preset("home", "weather")
               for _ in range(len(panels.COMMON_WIDTHS))]
        self.assertEqual(got, list(panels.COMMON_WIDTHS[1:]) + [panels.COMMON_WIDTHS[0]])

    def test_a_custom_width_snaps_to_the_nearest_preset_on_tap(self):
        """A tap from a width that is not one of the stops at all - reached
        by a drag or the settings slider - moves to the nearest stop, not
        to whichever one shares its position in the list."""
        prefs.set_panel_width("home", "weather", 37)  # between 40 and 30
        self.assertEqual(prefs.cycle_panel_width_preset("home", "weather"), 40)

    def test_cycling_a_height_wraps(self):
        """One cell at a time, 0 (auto) through 8, wrapping."""
        from agedashii_tofu import panels
        self.assertEqual(prefs.cycle_panel_height("home", "weather", -1),
                         panels.MAX_HEIGHT, "wrapping down from auto")
        prefs.reset_panels("home")
        self.assertEqual(prefs.cycle_panel_height("home", "weather", 3), 3)
