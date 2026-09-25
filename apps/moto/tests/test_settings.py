"""Settings, help, units and the navigation she asked to be able to hide.

Everything here is a question that can only be answered by running it. A
switch that flips a control and stores nothing, a theme that applies and
resets at the next launch, a units dropdown with nothing downstream reading
it, a hamburger hidden along with the last way to reach any other surface -
all four of those look completely correct in the source, and three of them
have shipped.

So: build the real shell, tap the real control, then either read the store
the app wrote or build a *second* shell against that same store and see
what it opens on. Nothing here asserts that a setter was called.
"""
import asyncio

import flet as ft

from agedashii_qi import PAL, THEMES, THEME_NAMES, appearance
from agedashii_washi import config, prefs

from agedashii_moto import motoprefs
from agedashii_moto import units as U

from .support import ShellCase, StubPage, StubPrefs, find, load_main, texts, walk


# ----------------------------------------------------------------- helpers
def _settings(page):
    """The Settings surface, through the running shell rather than alone."""
    appearance.S.goto("settings")
    return page.controls[0]


def _switch(tree, label):
    """The Switch whose row says `label`."""
    row = find(tree, lambda c: isinstance(c, ft.Row)
               and any(isinstance(x, ft.Switch) for x in (c.controls or []))
               and label in texts(c))
    assert row is not None, f"no switch row labelled {label!r}"
    return next(x for x in row.controls if isinstance(x, ft.Switch))


def _button(tree, label):
    return find(tree, lambda c: getattr(c, "content", None) == label
                or getattr(c, "content", None) == label)


def _dropdown(tree, value_in):
    """The Dropdown whose current value is one of `value_in`."""
    return find(tree, lambda c: isinstance(c, ft.Dropdown) and c.value in value_in)


def _fire(control, value=None):
    """Set a control's value and fire its handler the way Flet would."""
    if value is not None:
        control.value = value
    handler = getattr(control, "on_change", None) or getattr(control, "on_select", None)
    result = handler(type("E", (), {"control": control, "data": value})())
    if asyncio.iscoroutine(result):
        asyncio.run(result)


class SettingsCase(ShellCase):
    """Every test here starts from a real shell sitting on Settings."""

    def setUp(self):
        super().setUp()
        self.page = self.build_shell()
        self.tree = _settings(self.page)


# ------------------------------------------------------------ it is there
class ItIsReachableAtAll(ShellCase):
    """"where is the settings?" - on the bar, beside the other three."""

    def test_settings_is_a_surface_not_a_dialog(self):
        module = load_main()
        self.assertIn("settings", module.PAGES)
        self.assertIsInstance(module.build_surface(StubPage(), "settings"),
                              ft.Control)

    def test_the_bottom_bar_carries_it(self):
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        self.assertIn("Settings", [d.label for d in bar.destinations])

    def test_the_bottom_bar_reaches_every_surface_it_lists(self):
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        module = load_main()
        for i, key in enumerate(module.PAGES):
            with self.subTest(surface=key):
                bar = find(page.controls[0],
                           lambda c: isinstance(c, ft.NavigationBar))
                asyncio.run(bar.on_change(_pick(i)))
                self.assertEqual(_label(page), module.LABELS[key])

    def test_it_says_what_it_is_the_moment_it_opens(self):
        page = self.build_shell()
        said = " ".join(texts(_settings(page)))
        for word in ("Server", "Theme", "Appearance", "Navigation", "Units",
                     "Diagnostics", "How this works"):
            self.assertIn(word, said)


# --------------------------------------------------------------- the theme
class TheThemeSurvivesARestart(ShellCase):
    """"the dashboard's picker famously resets every launch" - this one does
    not, and the proof is a second launch against the first one's store."""

    def test_picking_a_theme_writes_it_to_storage(self):
        store = StubPrefs()
        page = self.build_shell(store=store)
        wanted = _another_theme()
        self.tap(page, _swatch(_settings(page), wanted))
        self.assertEqual(store.written.get(motoprefs.KEY_THEME), wanted)

    def test_a_second_launch_opens_on_the_theme_the_first_one_picked(self):
        store = StubPrefs()
        page = self.build_shell(store=store)
        wanted = _another_theme()
        self.tap(page, _swatch(_settings(page), wanted))

        # A restart, as literally as a test can stage one: everything the
        # first shell put on S is thrown away and a new shell is built
        # against the same storage.
        appearance.S.theme = "dark"
        motoprefs.reset()
        self.build_shell(store=store)
        self.assertEqual(appearance.S.theme, wanted)

    def test_a_theme_picked_from_the_topbar_persists_too(self):
        """Two pickers, one key. The quick one in the topbar was the only
        one there was, and it was the one that forgot."""
        store = StubPrefs()
        page = self.build_shell(store=store)
        palette = find(page.controls[0],
                       lambda c: isinstance(c, ft.IconButton)
                       and str(getattr(c, "tooltip", "")).startswith("Theme:"))
        self.tap(page, palette)
        wanted = _another_theme()
        self.tap(page, _swatch(page.dialogs[0], wanted))
        for fn, args in page.tasks:
            if getattr(fn, "__name__", "") == "save":
                asyncio.run(fn(*args))
        self.assertEqual(store.written.get(motoprefs.KEY_THEME), wanted)

    def test_a_stored_theme_that_no_longer_exists_is_ignored_not_crashed_on(self):
        store = StubPrefs()
        store.written[motoprefs.KEY_THEME] = "a-theme-that-was-deleted"
        self.build_shell(store=store)
        self.assertIn(appearance.S.theme, THEMES)

    def test_every_theme_is_offered_by_the_name_she_would_call_it(self):
        page = self.build_shell()
        said = texts(_settings(page))
        for name in THEME_NAMES.values():
            self.assertIn(name, said)

    def test_no_swatch_is_unbounded_inside_the_wrap(self):
        """A Wrap hands its children unbounded constraints, so a child with
        expand=True paints grey over the whole screen. That exact bug has
        made a page unusable twice in this codebase."""
        page = self.build_shell()
        _no_expand_inside_a_wrap(self, _settings(page), "settings")


# ---------------------------------------------------------- the appearance
class TheAppearanceControlsDoWhatTheySay(SettingsCase):

    def test_the_corner_radius_dropdown_changes_the_radius(self):
        drop = _dropdown(self.tree, {str(n) for n in (0, 3, 8, 12, 16)})
        self.assertIsNotNone(drop, "no corner-rounding dropdown")
        _fire(drop, "16")
        self.assertEqual(prefs.corner_radius(), 16)
        self.assertEqual(appearance.S.radius, 16)

    def test_the_glow_switch_changes_the_glow(self):
        before = prefs.accent_glow()
        _fire(_switch(self.tree, "Accent glow"), not before)
        self.assertEqual(prefs.accent_glow(), not before)
        self.assertEqual(appearance.S.glow, not before)

    def test_the_crt_switch_changes_the_overlay_and_frees_its_slider(self):
        _fire(_switch(self.tree, "CRT overlay"), True)
        self.assertTrue(prefs.crt_overlay())
        self.assertTrue(appearance.S.crt)
        slider = find(self.tree, lambda c: isinstance(c, ft.Slider)
                      and c.max == 100)
        self.assertFalse(slider.disabled, "the intensity slider stayed dead")

    def test_the_intensity_slider_saves_on_release_not_on_every_tick(self):
        _fire(_switch(self.tree, "CRT overlay"), True)
        tree = _settings(self.page)
        slider = find(tree, lambda c: isinstance(c, ft.Slider) and c.max == 100)
        slider.value = 80
        slider.on_change(type("E", (), {"control": slider})())
        self.assertEqual(prefs.crt_intensity(), 40, "a drag tick wrote to storage")
        asyncio.run(slider.on_change_end(type("E", (), {"control": slider})()))
        self.assertEqual(prefs.crt_intensity(), 80)
        self.assertEqual(appearance.S.crt_intensity, 80)

    def test_turning_the_crt_on_puts_the_overlay_over_the_safearea(self):
        _fire(_switch(self.tree, "CRT overlay"), True)
        root = self.page.controls[0]
        self.assertIsInstance(root, ft.Stack)
        self.assertIsInstance(root.controls[0], ft.SafeArea)
        self.assertTrue(getattr(root.controls[1], "ignore_interactions", False))


# ---------------------------------------------------------- the navigation
class TheHamburgerCanBeHidden(SettingsCase):
    """"i want the ability to show / hide the hamburger menu trigger as well"."""

    def test_it_is_shown_by_default(self):
        self.assertIsNotNone(_menu_button(self.page))

    def test_hiding_it_removes_the_slot_rather_than_greying_it_out(self):
        _fire(_switch(self.tree, "Menu button"), False)
        self.assertIsNone(_menu_button(self.page),
                          "the menu button is still in the topbar")
        # Gone, not disabled and not an empty box of the same width: the
        # brand pill is now the first thing in the row.
        row = _topbar_row(self.page)
        self.assertIsInstance(row.controls[0], ft.Container)
        self.assertIsNotNone(getattr(row.controls[0], "gradient", None),
                             "something is still holding the menu's place")

    def test_hiding_it_persists(self):
        store = StubPrefs()
        page = self.build_shell(store=store)
        _fire(_switch(_settings(page), "Menu button"), False)
        self.assertEqual(store.written.get(motoprefs.KEY_HAMBURGER), "false")

        motoprefs.reset()
        second = self.build_shell(store=store)
        self.assertFalse(motoprefs.hamburger_shown())
        self.assertIsNone(_menu_button(second))

    def test_the_drawer_is_not_even_built_when_nothing_can_open_it(self):
        _fire(_switch(self.tree, "Menu button"), False)
        self.assertIsNone(self.page.drawer,
                          "a drawer nothing can open was still built")

    def test_a_stored_false_really_reads_as_false(self):
        """SharedPreferences hands a bool back as the string "false", and
        `bool("false")` is True - which is how every switch in this app's
        ancestor came back on after a restart."""
        store = StubPrefs()
        store.written[motoprefs.KEY_HAMBURGER] = "false"
        page = self.build_shell(store=store)
        self.assertFalse(motoprefs.hamburger_visible())
        self.assertIsNone(_menu_button(page))

    def test_every_surface_is_still_reachable_with_it_hidden(self):
        _fire(_switch(self.tree, "Menu button"), False)
        module = load_main()
        bar = find(self.page.controls[0],
                   lambda c: isinstance(c, ft.NavigationBar))
        self.assertIsNotNone(bar, "no bar and no menu is no navigation")
        for i, key in enumerate(module.PAGES):
            with self.subTest(surface=key):
                bar = find(self.page.controls[0],
                           lambda c: isinstance(c, ft.NavigationBar))
                asyncio.run(bar.on_change(_pick(i)))
                self.assertEqual(_label(self.page), module.LABELS[key])

    def test_every_sub_tab_is_still_reachable_with_it_hidden(self):
        """The drawer offers Trips > Rides and Garage > Flipper directly.
        With no drawer they have to be on the surfaces themselves - and
        they are, in each surface's own tab bar."""
        _fire(_switch(self.tree, "Menu button"), False)
        module = load_main()
        for key, labels in (("trips", ["Selekt", "Produce", "Rides", "vNav"]),
                            ("garage", ["Home", "Mechanic", "Flipper"])):
            with self.subTest(surface=key):
                appearance.S.goto(key)
                bar = find(self.page.controls[0],
                           lambda c: isinstance(c, ft.TabBar))
                self.assertIsNotNone(bar, f"{key} drew no tab bar")
                self.assertEqual([t.label for t in bar.tabs], labels)
                # And the drawer's declared shortcuts name the same tabs,
                # so nothing is reachable one way and not the other.
                self.assertEqual([lab for lab, _p, _d in module.SUBPAGES[key]],
                                 labels)

    def test_help_is_still_reachable_with_it_hidden(self):
        _fire(_switch(self.tree, "Menu button"), False)
        row = find(_settings(self.page),
                   lambda c: isinstance(c, ft.Container)
                   and callable(getattr(c, "on_click", None))
                   and "How this works" in texts(c))
        self.assertIsNotNone(row, "no way to reach the guide")
        self.tap(self.page, row)
        self.assertEqual(_label(self.page), "Help")

    def test_the_last_way_around_cannot_be_switched_off(self):
        """An app with neither a bar nor a menu has no navigation and no
        way back to the page that did it."""
        _fire(_switch(self.tree, "Menu button"), False)
        # The tree the refusal is written into is the one that is on screen
        # when the switch is flipped, not a freshly built one - a refusal
        # deliberately does not repaint, so that the reason stays up.
        tree = _settings(self.page)
        _fire(_switch(tree, "Bottom bar"), False)
        self.assertTrue(prefs.bottom_bar_enabled(), "the bar went off anyway")
        self.assertIn("show the hamburger first", " ".join(texts(tree)))

    def test_a_confirmation_survives_the_repaint_the_edit_causes(self):
        """Every edit here repaints the whole shell, which rebuilds this
        page. A status line built fresh each time would be handed the
        message *after* the one on screen had already been replaced, so
        nothing would ever appear in it."""
        _fire(_switch(self.tree, "Accent glow"), True)
        self.assertIn("accent glow on", " ".join(texts(self.page.controls[0])))

    def test_and_neither_can_the_other_one(self):
        _fire(_switch(self.tree, "Bottom bar"), False)
        tree = _settings(self.page)
        _fire(_switch(tree, "Menu button"), False)
        self.assertTrue(motoprefs.hamburger_shown(),
                        "the menu went away with the bar already off")

    def test_the_bottom_bar_switch_actually_removes_the_bar(self):
        _fire(_switch(self.tree, "Bottom bar"), False)
        self.assertIsNone(find(self.page.controls[0],
                               lambda c: isinstance(c, ft.NavigationBar)))
        self.assertIsNotNone(_menu_button(self.page),
                             "the bar went and took the only other way with it")


# -------------------------------------------------------------- the units
class TheUnitsReachTheNumbers(ShellCase):
    """The setting existed the whole time and not one distance read it."""

    def test_the_dropdown_changes_the_stored_system(self):
        page = self.build_shell()
        drop = _dropdown(_settings(page), {"imperial", "metric"})
        self.assertIsNotNone(drop, "no units dropdown")
        _fire(drop, "metric")
        self.assertEqual(prefs.unit_system(), "metric")
        self.assertEqual(U.distance_unit(), "km")

    def test_metric_changes_the_distances_vnav_shows(self):
        module = load_main()
        prefs.set_unit_system("imperial")
        imperial = " ".join(texts(module.build_surface(StubPage(), "trips")))
        prefs.set_unit_system("metric")
        metric = " ".join(texts(module.build_surface(StubPage(), "trips")))
        self.assertIn("mi", imperial)
        self.assertIn("km", metric)
        self.assertNotIn("— mi", metric)

    def test_metric_changes_the_distances_trips_shows(self):
        module = load_main()
        prefs.set_unit_system("metric")
        said = " ".join(texts(module.build_surface(StubPage(), "trips")))
        self.assertNotIn(" mi", said)

    def test_metric_changes_the_distances_garage_shows(self):
        module = load_main()
        prefs.set_unit_system("metric")
        said = " ".join(texts(module.build_surface(StubPage(), "garage")))
        self.assertNotIn(" mi ", said)
        self.assertNotIn(" mi\n", said)

    def test_the_conversion_is_the_real_one_and_goes_the_right_way(self):
        prefs.set_unit_system("imperial")
        self.assertEqual(U.distance_text(10), "10.0 mi")
        self.assertEqual(U.odometer_text(8240), "8,240 mi")
        prefs.set_unit_system("metric")
        self.assertEqual(U.distance_text(10), "16.1 km")
        self.assertEqual(U.odometer_text(8240), "13,261 km")
        self.assertEqual(U.speed_text(60), "97 km/h")
        self.assertEqual(U.elevation_text(1000), "305 m")
        # Per-distance runs the other way: a cost spread over more
        # kilometres than miles is a smaller number per kilometre.
        self.assertAlmostEqual(U.per_distance(1.0), 0.6213711, places=5)

    def test_a_missing_measurement_stays_missing_through_the_conversion(self):
        """This app once showed four invented motorcycles. A None must not
        come out of a unit conversion as a confident 0.0 km."""
        for system in ("imperial", "metric"):
            prefs.set_unit_system(system)
            with self.subTest(units=system):
                self.assertEqual(U.distance_text(None), "—")
                self.assertEqual(U.distance_value(""), "—")
                self.assertEqual(U.odometer_text(None), "—")
                self.assertEqual(U.speed_text("—"), "—")
                self.assertEqual(U.elevation_text(None), "—")
                self.assertEqual(U.distance_text("not a number"), "—")

    def test_the_turn_countdown_switches_to_the_small_unit_near_the_turn(self):
        from agedashii_moto.pages import _trips_parts as T

        prefs.set_unit_system("imperial")
        self.assertEqual(T.fmt_miles(0.005), "now")
        self.assertEqual(T.fmt_miles(0.05), "264 ft")
        self.assertEqual(T.fmt_miles(1.4), "1.4 mi")
        prefs.set_unit_system("metric")
        self.assertEqual(T.fmt_miles(0.005), "now")
        self.assertEqual(T.fmt_miles(0.05), "80 m")
        self.assertEqual(T.fmt_miles(1.4), "2.3 km")


# --------------------------------------------------------------- the server
class TheServerCardIsHonest(SettingsCase):

    def test_there_is_an_address_field_and_a_check_button(self):
        field = find(self.tree, lambda c: isinstance(c, ft.TextField)
                     and c.label == "Server address")
        self.assertIsNotNone(field)
        self.assertIsNotNone(_button(self.tree, "Check"))

    def test_checking_probes_off_the_event_loop(self):
        """A three-second blocking probe run inline holds Flet's loop, keeps
        the old frame on screen and drops every tap that lands meanwhile -
        which is exactly what "the button is dead" looks like."""
        import inspect

        source = inspect.getsource(load_main().settings)
        self.assertIn("asyncio.to_thread(config.set_url", source)

    def test_checking_reports_what_actually_came_back(self):
        before = len(self.opener.attempts)
        self.tap(self.page, _button(self.tree, "Check"))
        self.assertGreater(len(self.opener.attempts), before,
                           "Check did not probe anything")
        said = " ".join(texts(self.tree))
        self.assertIn("not connected", said)
        self.assertIn(config.STATUS["detail"], said)

    def test_saving_the_address_stores_it(self):
        store = StubPrefs()
        page = self.build_shell(store=store)
        tree = _settings(page)
        field = find(tree, lambda c: isinstance(c, ft.TextField)
                     and c.label == "Server address")
        field.value = "192.0.2.99:6900"
        self.tap(page, _button(tree, "Save"))
        self.assertEqual(store.written.get(config.KEY_URL),
                         "http://192.0.2.99:6900")


# ----------------------------------------------------------------- the help
class TheGuideSaysTheHonestThings(ShellCase):

    def test_it_is_reachable_from_settings(self):
        page = self.build_shell()
        row = find(_settings(page),
                   lambda c: isinstance(c, ft.Container)
                   and callable(getattr(c, "on_click", None))
                   and "How this works" in texts(c))
        self.assertIsNotNone(row)
        self.tap(page, row)
        self.assertEqual(_label(page), "Help")

    def test_it_names_all_three_surfaces_and_settings(self):
        said = " ".join(texts(load_main().build_surface(StubPage(), "help")))
        for word in ("Trips", "Garage", "XNAV", "Settings"):
            self.assertIn(word, said)

    def test_it_says_what_works_with_no_signal_and_gives_the_numbers(self):
        said = " ".join(texts(load_main().build_surface(StubPage(), "help")))
        self.assertIn("39 MB", said)          # the shipped tile archive
        self.assertIn("86 MB", said)          # the shipped routing graph
        self.assertIn("725,626", said)
        self.assertIn("11,716", said)

    def test_it_says_what_queues_and_what_refuses_and_why(self):
        said = " ".join(texts(load_main().build_surface(StubPage(), "help")))
        self.assertIn("outbox", said)
        for service in ("Valhalla", "Overpass", "elevation"):
            self.assertIn(service, said)
        self.assertIn("nothing to replay", said)

    def test_it_says_how_recording_works(self):
        said = " ".join(texts(load_main().build_surface(StubPage(), "help")))
        self.assertIn("Record", said)
        self.assertIn("screen locks", said)

    def test_it_reads_as_sentences_rather_than_marketing(self):
        said = " ".join(texts(load_main().build_surface(StubPage(), "help"))).lower()
        for word in ("seamless", "blazing", "effortless", "revolutionary",
                     "powerful", "cutting-edge"):
            self.assertNotIn(word, said)

    def test_it_can_be_left_again(self):
        page = self.build_shell()
        appearance.S.goto("help")
        back = find(page.controls[0],
                    lambda c: isinstance(c, ft.Container)
                    and callable(getattr(c, "on_click", None))
                    and "Back to Settings" in texts(c))
        self.assertIsNotNone(back, "the guide is a dead end")
        self.tap(page, back)
        self.assertEqual(_label(page), "Settings")

    def test_the_bar_lights_settings_while_the_guide_is_open(self):
        """Help is not on the bar. A bar with no selection reads as "you
        are nowhere", so it lights the surface Help hangs off."""
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        appearance.S.goto("help")
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        module = load_main()
        self.assertEqual(bar.selected_index, module.PAGES.index("settings"))


# ------------------------------------------------------------ the polish
class TheChromeIsThemedAndAnswersATap(ShellCase):

    def test_every_nav_mark_is_tinted_from_the_palette(self):
        """Themed Material icons, not emoji and not Material's own seeded
        colour - the chrome has to follow all 24 themes."""
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        for d in bar.destinations:
            with self.subTest(destination=d.label):
                self.assertIsInstance(d.icon, ft.Icon)
                self.assertEqual(d.icon.color, PAL()["text2"])
                self.assertIsInstance(d.selected_icon, ft.Icon)
                self.assertEqual(d.selected_icon.color, PAL()["accent"])

    def test_the_marks_follow_a_theme_change(self):
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        wanted = _another_theme()
        self.tap(page, _swatch(_settings(page), wanted))
        appearance.S.goto("trips")
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        self.assertEqual(bar.destinations[0].selected_icon.color, PAL()["accent"])

    def test_no_surface_uses_an_emoji_where_an_icon_belongs(self):
        """"themed Material icons for all UI chrome" - an emoji is painted
        by the font at a fixed colour and would not follow the theme."""
        module = load_main()
        for key in module.PAGES + module.EXTRA_PAGES:
            with self.subTest(surface=key):
                for said in texts(module.build_surface(StubPage(), key)):
                    for ch in said:
                        self.assertLess(ord(ch), 0x1F000,
                                        f"{key} draws an emoji: {said!r}")

    def test_every_tappable_row_on_settings_has_press_feedback(self):
        """Flet's ink ripple. Without it a tap on a card row is completely
        silent, which is the whole of "flet's showcase feels smooth and
        this does not"."""
        page = self.build_shell()
        for node in walk(_settings(page)):
            if isinstance(node, ft.Container) and callable(getattr(node, "on_click", None)):
                with self.subTest(tooltip=getattr(node, "tooltip", None)):
                    self.assertTrue(getattr(node, "ink", False),
                                    "a tappable container with no ripple")

    def test_the_drawer_rows_answer_a_tap_too(self):
        page = self.build_shell()
        for node in walk(page.drawer):
            if isinstance(node, ft.Container) and callable(getattr(node, "on_click", None)):
                self.assertTrue(getattr(node, "ink", False))

    def test_no_shipped_container_takes_a_tap_without_answering_it(self):
        """Read off the source rather than off a built tree, because most
        of these live in a tab that is only built when it is selected - a
        walk of the default surfaces would miss them and pass."""
        import ast

        from .support import APP_DIR

        offenders = []
        for path in sorted((APP_DIR / "agedashii_moto").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(node, ast.Call)
                        and getattr(getattr(node.func, "value", None), "id", None) == "ft"
                        and getattr(node.func, "attr", None) == "Container"):
                    continue
                names = {k.arg for k in node.keywords if k.arg}
                if "on_click" in names and "ink" not in names:
                    rel = path.relative_to(APP_DIR)
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "tappable containers with no ripple")


class NothingIsUnboundedInsideAWrap(ShellCase):
    """A Wrap gives its children unbounded constraints.

    A filled TextField in there paints grey over the whole screen, and a
    Container with `expand=True` takes the width of infinity. It is not an
    exception and not a warning - the page simply comes out wrong, and it
    has made a page in this codebase unusable twice.
    """

    def test_no_surface_puts_an_expanding_control_inside_a_wrapping_row(self):
        """Every tab, not only the one a surface opens on.

        `_tabs()` builds the selected tab and leaves the rest to a
        background pass a stub page never runs, so a sweep of the default
        surfaces would walk one tab in four and pass on the strength of it.
        """
        from agedashii_washi.surface import _TAB_INDEX

        module = load_main()
        for key in module.PAGES + module.EXTRA_PAGES:
            for tab in range(len(module.SUBPAGES.get(key, [])) or 1):
                with self.subTest(surface=key, tab=tab):
                    _TAB_INDEX[key] = tab
                    _no_expand_inside_a_wrap(
                        self, module.build_surface(StubPage(), key),
                        f"{key} tab {tab}")

    def test_it_would_notice_one(self):
        """The sweep above passes trivially if it never finds a wrap at
        all, so here is a tree that should fail it."""
        bad = ft.Row(wrap=True, controls=[ft.Container(expand=True)])
        with self.assertRaises(AssertionError):
            _no_expand_inside_a_wrap(self, bad, "a deliberately bad tree")


def _no_expand_inside_a_wrap(case, tree, where):
    for row in walk(tree):
        if not (isinstance(row, ft.Row) and getattr(row, "wrap", False)):
            continue
        for child in (row.controls or []):
            for node in walk(child):
                case.assertNotEqual(
                    getattr(node, "expand", None), True,
                    f"{where}: {type(node).__name__} expands inside a wrap")


# ----------------------------------------------------------------- helpers
def _another_theme():
    """Any theme that is not the one currently applied."""
    return next(k for k in THEME_NAMES if k != appearance.S.theme)


def _swatch(tree, key):
    """The tappable swatch for theme `key`, in either picker."""
    name = THEME_NAMES[key]
    swatch = find(tree, lambda c: isinstance(c, ft.Container)
                  and callable(getattr(c, "on_click", None))
                  and name in texts(c))
    assert swatch is not None, f"no swatch for {name}"
    return swatch


def _topbar_row(page):
    return find(page.controls[0],
                lambda c: isinstance(c, ft.Row)
                and any(isinstance(x, ft.Container)
                        and getattr(x, "gradient", None) is not None
                        for x in (c.controls or [])))


def _menu_button(page):
    return find(page.controls[0],
                lambda c: isinstance(c, ft.IconButton) and c.icon == ft.Icons.MENU)


def _label(page):
    """The topbar's own name for the surface on screen."""
    module = load_main()
    row = _topbar_row(page)
    if row is None:
        return None
    wanted = set(module.LABELS.values())
    for control in (row.controls or []):
        if isinstance(control, ft.Text) and control.value in wanted:
            return control.value
    return None


def _pick(index):
    return type("E", (), {"control": type("C", (), {"selected_index": index})()})()
