"""The shell: does it come up, does it draw, and does it get you anywhere.

The surfaces themselves are tested where they live - this file is about the
frame around them. It runs `main()` for real against a stub page, with the
network unplugged and a throwaway store, because that is the state the app
is designed for and the one a rider is most likely to be in.

Every test here is one that can only be answered by running it: a topbar
that keeps the old palette after a theme change, a drawer row whose tap
goes nowhere, a bottom bar that cannot be switched off, a CRT overlay
stacked under the SafeArea instead of over it.
"""
import asyncio
import ast
from pathlib import Path

import flet as ft

from agedashii_qi import (LIGHT_THEMES, PAL, THEME_NAMES, THEMES, appearance,
                          apply_theme)
from agedashii_washi import panels, prefs
from agedashii_washi.surface import _TAB_INDEX

from .support import ShellCase, StubPage, find, load_main, texts


class ItComesUp(ShellCase):
    """Headless, offline, first launch: the app still paints a full screen."""

    def test_it_builds_with_no_server_and_no_stored_preferences(self):
        page = self.build_shell()
        self.assertEqual(len(page.controls), 1,
                         "the shell should add exactly one root control")
        self.assertIsInstance(page.controls[0], ft.Control)

    def test_the_window_says_which_of_the_three_apps_this_is(self):
        # Three Agedashii apps can be installed side by side. "Agedashii"
        # alone would be three identical entries in a task switcher.
        page = self.build_shell()
        self.assertEqual(page.title, "Agedashii Moto")

    def test_startup_only_talks_to_the_configured_server(self):
        self.build_shell()
        for url in self.opener.attempts:
            self.assertTrue(url.startswith("http://127.0.0.1:1"), url)

    def test_the_brand_pill_reads_moto_and_is_a_gradient(self):
        page = self.build_shell()
        pill = _pill(page)
        self.assertIsNotNone(pill, "no gradient pill in the topbar")
        self.assertEqual(pill.content.value, "Moto")

    def test_the_chrome_is_inside_a_safearea_and_the_crt_is_over_the_top(self):
        """A screen effect that stops at the notch draws a bright band there.

        So the SafeArea wraps the chrome and the CRT stacks over the lot -
        the opposite nesting looks fine on a laptop and wrong on every phone
        with a cutout.
        """
        prefs._V[prefs.KEY_CRT] = True
        page = self.build_shell()
        root = page.controls[0]
        self.assertIsInstance(root, ft.Stack)
        self.assertEqual(len(root.controls), 2)
        self.assertIsInstance(root.controls[0], ft.SafeArea)
        # The whole safety story of the overlay: it must never take a tap.
        self.assertTrue(getattr(root.controls[1], "ignore_interactions", False))

    def test_with_the_crt_off_there_is_no_overlay_at_all(self):
        prefs._V[prefs.KEY_CRT] = False
        page = self.build_shell()
        self.assertIsInstance(page.controls[0], ft.SafeArea)

    def test_the_outbox_chip_stays_hidden_while_nothing_is_waiting(self):
        """An always-visible "all synced" badge is a claim about the server
        that the queue is not entitled to make."""
        page = self.build_shell()
        chip = _sync_chip(page)
        self.assertIsNotNone(chip, "the sync chip should exist")
        self.assertFalse(chip.visible)

    def test_startup_tries_to_drain_whatever_the_last_session_queued(self):
        # Launch is a resume: a write made in a canyon last week is still in
        # the outbox and this is the first chance it has had.
        page = self.build_shell()
        self.assertTrue(any(getattr(fn, "__name__", "") == "drain_and_paint"
                            for fn, _a, _k in page.threads))

    def test_the_outbox_watcher_is_started_and_kept_referenced(self):
        """A Flet task nothing holds a reference to can be collected
        mid-sleep, which is a watcher that silently stops watching."""
        module = load_main()
        self.build_shell()
        self.assertIsNotNone(appearance.S.sync_watcher)
        self.assertEqual(module.SYNC_POLL_SECS, 20)

    def test_resuming_from_the_background_drains_the_outbox_too(self):
        """The moment a phone is most likely to have found wifi since the
        last look at it."""
        page = self.build_shell()
        self.assertTrue(callable(page.on_app_lifecycle_state_change))
        page.threads.clear()
        page.on_app_lifecycle_state_change(
            type("E", (), {"state": ft.AppLifecycleState.RESUME})())
        self.assertTrue(page.threads)


class EverySurfaceRenders(ShellCase):
    """Three surfaces. Each builds, and each says what it cannot know."""

    def test_build_surface_returns_a_control_for_every_page_key(self):
        module = load_main()
        for key in module.PAGES:
            with self.subTest(surface=key):
                self.assertIsInstance(module.build_surface(StubPage(), key),
                                      ft.Control)

    def test_an_unknown_key_falls_back_rather_than_raising(self):
        module = load_main()
        self.assertIsInstance(module.build_surface(StubPage(), "weather"),
                              ft.Control)

    def test_trips_says_it_has_no_copy_instead_of_inventing_one(self):
        module = load_main()
        said = "\n".join(texts(module.build_surface(StubPage(), "trips")))
        self.assertIn("Not connected", said)
        # api.TRIPS falls back to mock.TRIPS on attribute access; this page
        # must never show those.
        self.assertNotIn("Grizzly Peak full", said)

    def test_garage_says_it_has_no_bikes_instead_of_inventing_one(self):
        module = load_main()
        said = "\n".join(texts(module.build_surface(StubPage(), "garage")))
        self.assertIn("No garage", said)
        # The two bikes in mock.BIKES do not exist. They used to show here.
        self.assertNotIn("Rebel 250", said)
        self.assertNotIn("CB250", said)

    def test_xnav_shows_dashes_for_a_route_it_has_not_got(self):
        """Distance, duration, ascent and descent are all "—" until there is
        a route to measure - not 0, which would read as a measurement."""
        module = load_main()
        said = " ".join(texts(module.build_surface(StubPage(), "xnav")))
        self.assertIn("No route yet", said)
        self.assertIn("— mi", said)

    def test_every_card_surface_registers_a_card_layout_worth_arranging(self):
        """`panels.has_surface()` is what puts the Customize button in the
        topbar. A surface that forgot `panels.layout` would get a button
        that does nothing."""
        module = load_main()
        for key in module.CARD_PAGES:
            with self.subTest(surface=key):
                panels.begin_render()
                module.build_surface(StubPage(), key)
                self.assertTrue(panels.has_surface(),
                                f"{key} registered no panel surface")

    def test_settings_and_help_register_none_so_they_get_no_arrange_button(self):
        """Both are ordinary scrolling columns with nothing to rearrange,
        and a button that does nothing is worse than no button."""
        module = load_main()
        for key in ("settings", "help"):
            with self.subTest(surface=key):
                panels.begin_render()
                module.build_surface(StubPage(), key)
                self.assertFalse(panels.has_surface(),
                                 f"{key} offered an Arrange button")

    def test_each_surface_is_reachable_through_the_running_shell(self):
        """Not `build_surface` in isolation - the real path, through the
        drawer, with the chrome rebuilt around it each time."""
        page = self.build_shell()
        module = load_main()
        for key in module.PAGES:
            with self.subTest(surface=key):
                appearance.S.goto(key)
                self.assertEqual(_current_label(page), module.LABELS[key])
                self.assertEqual(len(page.controls), 1)


class TheNavReachesEachOne(ShellCase):
    """Every surface has a way in, from both the drawer and the bar."""

    def test_every_surface_has_a_label_and_a_pair_of_icons(self):
        """Four on the bar, Help off it. Every one of the five needs a
        name and a mark, selected and unselected - a destination missing
        its filled twin falls back to the outline and stops reading as
        selected at all."""
        module = load_main()
        self.assertEqual(module.PAGES, ["trips", "garage", "xnav", "settings"])
        self.assertEqual(module.EXTRA_PAGES, ["help"])
        for key in module.PAGES + module.EXTRA_PAGES:
            with self.subTest(surface=key):
                self.assertIn(key, module.LABELS)
                self.assertIn(key, module.ICONS)
                self.assertIn(key, module.ICONS_SELECTED)

    def test_no_two_surfaces_share_a_mark(self):
        """MAP for Trips beside NAVIGATION for XNAV was two arrows on two
        maps at 24px. Distinct marks are the point of the set."""
        module = load_main()
        marks = [module.ICONS[k] for k in module.PAGES + module.EXTRA_PAGES]
        self.assertEqual(len(marks), len(set(marks)))

    def test_the_drawer_lists_every_surface(self):
        page = self.build_shell()
        module = load_main()
        said = texts(page.drawer)
        for key in module.PAGES:
            self.assertIn(module.LABELS[key], said)

    def test_tapping_a_drawer_row_changes_the_surface(self):
        page = self.build_shell()
        module = load_main()
        for key in ("garage", "xnav", "trips"):
            with self.subTest(surface=key):
                self.tap(page, self._drawer_row(page, module.LABELS[key]))
                self.assertEqual(_current_label(page), module.LABELS[key])

    def test_the_bottom_bar_offers_every_surface_and_switching_works(self):
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        module = load_main()
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        self.assertIsNotNone(bar, "the bottom bar is on but was not drawn")
        self.assertEqual([d.label for d in bar.destinations],
                         [module.LABELS[k] for k in module.PAGES])
        self.assertEqual(bar.selected_index,
                         module.PAGES.index(module.FIRST_PAGE))

        asyncio.run(bar.on_change(_pick(1)))
        self.assertEqual(_current_label(page), module.LABELS[module.PAGES[1]])
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        self.assertEqual(bar.selected_index, 1)

    def test_a_bottom_bar_tap_out_of_range_is_ignored_not_crashed_on(self):
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = True
        page = self.build_shell()
        bar = find(page.controls[0], lambda c: isinstance(c, ft.NavigationBar))
        asyncio.run(bar.on_change(_pick(9)))
        self.assertEqual(_current_label(page), load_main().LABELS["trips"])

    def test_the_bottom_bar_can_be_switched_off_and_the_drawer_still_reaches(self):
        """XNAV is a map; every row of pixels the bar gives back is map."""
        prefs._V[prefs.KEY_BOTTOM_ENABLED] = False
        page = self.build_shell()
        self.assertIsNone(find(page.controls[0],
                               lambda c: isinstance(c, ft.NavigationBar)))
        self.assertIsNotNone(page.drawer)

    def test_the_drawer_sub_rows_land_on_the_right_tab(self):
        page = self.build_shell()
        # Garage's section is collapsed by default - it is not the surface
        # she is standing on - so open it the way the chevron would.
        prefs.set_drawer_open("garage", True)
        appearance.S.rerender()
        self.tap(page, self._drawer_row(page, "Flipper"))
        self.assertEqual(_TAB_INDEX.get("garage"), 2)
        self.assertEqual(_current_label(page), "Garage")

    def test_the_chevron_only_repaints_the_drawer(self):
        """It is the drawer's own affordance, not a navigation. Going
        through render() would rebuild Trips' body - which reads the trip
        list - and a slow synchronous handler on Flet's loop drops taps."""
        page = self.build_shell()
        chevron = find(page.drawer,
                       lambda c: isinstance(c, ft.Container)
                       and getattr(c, "tooltip", None) in ("Collapse", "Expand")
                       and callable(getattr(c, "on_click", None)))
        self.assertIsNotNone(chevron)
        before = list(page.controls)
        chevron.on_click(None)
        self.assertEqual(page.controls, before,
                         "the chevron rebuilt the whole page")

    def test_the_declared_sub_rows_match_the_tabs_the_pages_actually_build(self):
        """The drawer's (key, index) pairs are the page's own tab order.

        Nothing links the two at runtime, so a tab inserted into Trips would
        silently point every drawer shortcut below it one tab short.
        """
        module = load_main()
        pages_dir = Path(module.trips.__file__).parent
        for key in ("trips", "garage"):
            with self.subTest(surface=key):
                declared = [(label, path[-1][1])
                            for label, path, _d in module.SUBPAGES[key]]
                actual = list(enumerate(_tab_labels(pages_dir / f"{key}.py", key)))
                self.assertEqual(declared, [(lab, i) for i, lab in actual])

    def test_xnav_has_no_sub_rows_because_it_has_no_tabs(self):
        module = load_main()
        self.assertNotIn("xnav", module.SUBPAGES)

    def test_the_shell_parks_a_rerender_so_a_write_keeps_its_chrome(self):
        """`forms.rebuilder` and Garage's `_repaint` both look for this.
        Absent, they replace page.controls wholesale, which drops the topbar
        and the bottom bar and strands you on a page with no way off."""
        page = self.build_shell()
        self.assertTrue(callable(appearance.S.rerender))
        page.controls.clear()
        appearance.S.rerender()
        self.assertEqual(len(page.controls), 1)
        self.assertIsNotNone(_pill(page), "the repaint lost the topbar")

    def test_goto_sets_the_tab_on_the_way_down(self):
        page = self.build_shell()
        appearance.S.goto("trips", [("trips", 2)])
        self.assertEqual(_TAB_INDEX.get("trips"), 2)
        self.assertEqual(_current_label(page), "Trips")

    def _drawer_row(self, page, label):
        row = find(page.drawer,
                   lambda c: isinstance(c, ft.Container)
                   and callable(getattr(c, "on_click", None))
                   and label in texts(c))
        self.assertIsNotNone(row, f"no drawer row for {label}")
        return row


class TheThemeApplies(ShellCase):
    """24 themes, shared with the other Agedashii apps, applied live."""

    def test_apply_theme_reaches_the_page_before_the_first_paint(self):
        page = self.build_shell()
        self.assertIsNotNone(page.theme)
        self.assertIn(page.theme_mode, (ft.ThemeMode.LIGHT, ft.ThemeMode.DARK))

    def test_the_body_is_painted_in_the_current_palette(self):
        page = self.build_shell()
        self.assertIsNotNone(
            find(page.controls[0],
                 lambda c: isinstance(c, ft.Container)
                 and getattr(c, "expand", None) is True
                 and getattr(c, "bgcolor", None) == PAL()["bg"]),
            "nothing on screen is painted in PAL()['bg']")

    def test_changing_the_theme_repaints_rather_than_leaving_stale_colour(self):
        """The drawer and the topbar are rebuilt on every render for exactly
        this reason: one built once at startup keeps the old palette."""
        page = self.build_shell()
        before = PAL()["bg"]
        other = next(k for k in THEMES if THEMES[k]["bg"] != before)
        apply_theme(page, other)
        appearance.S.rerender()
        self.assertEqual(appearance.S.theme, other)
        self.assertIsNotNone(
            find(page.controls[0],
                 lambda c: isinstance(c, ft.Container)
                 and getattr(c, "bgcolor", None) == PAL()["bg"]))
        self.assertIsNotNone(
            find(page.drawer,
                 lambda c: isinstance(c, ft.NavigationDrawer)
                 and c.bgcolor == PAL()["bg2"]),
            "the drawer kept the old palette")

    def test_a_light_theme_puts_material_into_light_mode(self):
        """A LIGHT_THEMES check that goes stale paints light-mode widgets on
        a near-black page, which is how `pastel` broke once."""
        page = self.build_shell()
        apply_theme(page, sorted(LIGHT_THEMES)[0])
        self.assertEqual(page.theme_mode, ft.ThemeMode.LIGHT)

    def test_the_theme_picker_offers_every_theme_by_name(self):
        """21 themes is already too many to step through one tap at a time,
        and a tooltip naming the current one needs a mouse."""
        page = self.build_shell()
        palette = find(page.controls[0],
                       lambda c: isinstance(c, ft.IconButton)
                       and str(getattr(c, "tooltip", "")).startswith("Theme:"))
        self.assertIsNotNone(palette, "no way to reach the theme list")
        self.tap(page, palette)
        self.assertEqual(len(page.dialogs), 1)
        said = texts(page.dialogs[0])
        # Listed by the name she would call it, not by the storage key.
        for name in THEME_NAMES.values():
            self.assertIn(name, said)
        self.assertEqual(len(THEME_NAMES), len(THEMES))

    def test_the_corner_radius_and_glow_come_from_stored_preferences(self):
        """`prefs.apply_runtime()` is what makes the four appearance knobs
        apply at all; without the call they sit in storage doing nothing."""
        prefs._V[prefs.KEY_RADIUS] = 16
        prefs._V[prefs.KEY_GLOW] = True
        self.build_shell()
        self.assertEqual(appearance.S.radius, 16)
        self.assertTrue(appearance.S.glow)


# ----------------------------------------------------------------- helpers
def _pill(page):
    """The gradient brand pill in the topbar."""
    return find(page.controls[0],
                lambda c: isinstance(c, ft.Container)
                and getattr(c, "gradient", None) is not None
                and isinstance(getattr(c, "content", None), ft.Text))


def _sync_chip(page):
    return find(page.controls[0],
                lambda c: isinstance(c, ft.Container)
                and isinstance(getattr(c, "content", None), ft.Row)
                # `.icon`, not `.name`. This helper used to read `.name`,
                # which matched only because the shell was SETTING `.name` -
                # a stray attribute ft.Icon does not have. The test agreed
                # with the bug, so both passed. Reading the real attribute
                # is what makes the fix provable.
                and any(isinstance(x, ft.Icon)
                        and x.icon in (ft.Icons.CLOUD_UPLOAD, ft.Icons.CLOUD_OFF)
                        for x in (c.content.controls or [])))


def _current_label(page):
    """The topbar's own name for the surface on screen."""
    module = load_main()
    row = find(page.controls[0],
               lambda c: isinstance(c, ft.Row)
               and any(isinstance(x, ft.Container)
                       and getattr(x, "gradient", None) is not None
                       for x in (c.controls or [])))
    if row is None:
        return None
    wanted = set(module.LABELS.values())
    for control in (row.controls or []):
        if isinstance(control, ft.Text) and control.value in wanted:
            return control.value
    return None


def _pick(index):
    """The shape `NavigationBar.on_change` reads out of its event."""
    return type("E", (), {"control": type("C", (), {"selected_index": index})()})()


def _tab_labels(path, key):
    """The tab labels a page module passes to `_tabs(..., key=key)`."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_tabs"):
            continue
        if not any(kw.arg == "key" and getattr(kw.value, "value", None) == key
                   for kw in node.keywords):
            continue
        return [t.elts[0].value for t in node.args[1].elts]
    raise AssertionError(f"{path.name} has no _tabs(..., key={key!r}) call")
