"""The shell Watudo is an app because of: it builds, navigates and themes.

Everything here runs with the radio off - `config._opener` refuses every
request - because that is the condition the app is designed for, not an
edge case to check last. A shell that only assembles when a server answers
would be a shell that blanks on the train.

What each class pins down:

``TheShellBuilds``        one root control, the chrome in the right order,
                          the pill saying Watudo and not Agedashii.
``EverySurfaceRenders``   every destination builds, offline, from cold, and
                          exactly one control offers Present | Future.
``TheNavReachesEachOne``  the bottom bar reaches everything it carries, the
                          fourth surface is reachable with the hamburger
                          hidden, and the drawer - once switched on - moves
                          between all four. Which tab is lit is read back
                          out of the same place the page's tab bar writes.
``TheThemeApplies``       a picked theme reaches the palette, the page's
                          Material theme and the next repaint - and is still
                          there on the next launch.
``TheChromeIsHonest``     the outbox chip says what is queued and nothing
                          when nothing is, and Customize only appears on a
                          surface with cards to arrange.
``TheScreenTreatment``    SafeArea wraps the chrome, the CRT overlay goes
                          over the top of it, and every layer of the overlay
                          still ignores taps.

What each control on the Settings surface actually does is `test_settings.py`.
"""
import flet as ft

from agedashii_qi import PAL, S, THEMES
from agedashii_washi import panels, prefs
from agedashii_washi.surface import _TAB_INDEX

from .harness import Event, WatudoCase, every, first, texts, walk


class TheShellBuilds(WatudoCase):
    def test_it_builds_with_no_server_and_nothing_cached(self):
        page = self.build()
        self.assertEqual(len(page.controls), 1)
        self.assertTrue(self.opener.calls,
                        "the shell should have tried the server and been refused")

    def test_the_window_is_named_after_the_app_not_the_framework(self):
        self.assertEqual(self.build().title, "Agedashii Watudo")

    def test_the_root_is_a_safearea_while_the_crt_is_off(self):
        """Default: no overlay, so no Stack - two controls fewer for the
        whole app, and nothing over the top to hit-test through."""
        root = self.shell(self.build())
        self.assertIsInstance(root, ft.SafeArea)

    def test_the_chrome_is_topbar_then_body_then_bar(self):
        root = self.shell(self.build())
        column = root.content
        self.assertIsInstance(column, ft.Column)
        self.assertEqual(len(column.controls), 3)
        self.assertIsInstance(column.controls[-1], ft.NavigationBar)

    def test_the_pill_says_watudo(self):
        """The pill is the identity. An app whose identity bar named a
        different app is the mistake the NVII badge was."""
        page = self.build()
        said = texts(self.shell(page))
        self.assertIn("Watudo", said)
        self.assertNotIn("Agedashii", said)

    def test_the_bottom_bar_can_be_turned_off_and_the_drawer_takes_over(self):
        """Nothing may leave her with no way off the page she is on. With
        the bar off, the hamburger comes back whether or not it is switched
        on, and the drawer lists every surface."""
        page = self.build()
        prefs.set_bottom_bar_enabled(False)
        self.rerender()
        root = self.shell(page)
        self.assertIsNone(first(root, ft.NavigationBar))
        self.assertEqual(len(page.controls), 1)
        self.assertTrue(self.main.menu_visible(),
                        "with no bar, the menu trigger is not optional")
        drawer_labels = texts(page.drawer)
        for key in self.main.PAGES:
            self.assertIn(self.main.LABELS[key], drawer_labels)


class EverySurfaceRenders(WatudoCase):
    """Every destination, built cold with nothing to build it from."""

    def surface_tree(self, page):
        return self.shell(page).content.controls[1]

    def test_wat2do_is_what_the_app_opens_on(self):
        page = self.build()
        self.assertEqual(_TAB_INDEX["wat2do"], 0)
        self.assertEqual(self.main.surface_of(), "wat2do")
        self.assertIn("Checklist", " ".join(texts(self.surface_tree(page))))

    def test_only_one_control_offers_present_and_future_at_a_time(self):
        """The bottom bar carries both, so the page's own tab bar over the
        top of them was the same two choices in two rows - which is what the
        bottom bar switch existed to escape. Switch the bar off and the tab
        bar is how you get between them, so it comes back."""
        page = self.build()
        self.assertIsNotNone(first(self.shell(page), ft.NavigationBar))
        self.assertIsNone(first(self.surface_tree(page), ft.Tabs),
                          "two rows of Present | Future, stacked")
        prefs.set_bottom_bar_enabled(False)
        self.rerender()
        self.assertIsNotNone(first(self.body(page), ft.Tabs),
                             "with no bar, the tabs are the only way across")

    def test_future_finds_renders_offline_with_no_someday_list(self):
        page = self.build()
        self.nav_to(page, "future")
        self.assertEqual(_TAB_INDEX["wat2do"], 1)
        said = " ".join(texts(self.surface_tree(page)))
        self.assertIn("Not connected", said,
                      "offline, Future Finds must say so rather than show a "
                      "plausible empty list as though it were the answer")

    def test_future_finds_is_built_only_when_it_is_the_one_on_screen(self):
        """`_tabs` is handed two already-built bodies, so a tab bar means
        building both on every repaint - and Customize appeared on Future,
        which has no cards to arrange."""
        page = self.build()
        said = " ".join(texts(self.surface_tree(page)))
        self.assertIn("Checklist", said)
        self.assertNotIn("Someday list", said,
                         "Future Finds should not be built behind Wat2Do")
        icons = [c.icon for c in every(self.shell(page), ft.IconButton)]
        self.assertIn(ft.Icons.DASHBOARD_CUSTOMIZE, icons)
        self.nav_to(page, "future")
        icons = [c.icon for c in every(self.shell(page), ft.IconButton)]
        self.assertNotIn(ft.Icons.DASHBOARD_CUSTOMIZE, icons,
                         "a button that does nothing is worse than no button")

    def test_neither_surface_invents_a_number_when_it_has_no_data(self):
        """Offline and cold there is nothing to show, so nothing is shown."""
        page = self.build()
        said = " ".join(texts(self.surface_tree(page)))
        self.assertNotIn("sample data", said)

    def test_a_surface_that_raises_says_so_instead_of_blanking(self):
        """A failure inside a surface lands in the shell: with no tab bar
        there is no `_tabs` guard above it, and with one, `wat2do.build`
        hands `_tabs` two already-built bodies rather than callables."""
        from unittest import mock
        from agedashii_watudo.pages import wat2do
        page = self.build()
        with mock.patch.object(wat2do, "build",
                               side_effect=RuntimeError("kaboom")):
            self.rerender()
        self.assertEqual(len(page.controls), 1)
        said = " ".join(texts(self.shell(page)))
        self.assertIn("could not be built", said)
        self.assertIn("kaboom", said)


class TheNavReachesEachOne(WatudoCase):
    """Bottom-bar-first. Her words: "Each of these smaller apps (not tofu)
    work better with the bottom navigation bar and less with the hamburger
    menu". So the bar carries the app and the drawer is opt-in - and the
    thing that must stay true either way is that every surface is reachable.
    """

    def test_the_bar_carries_the_surfaces_that_are_used_daily(self):
        bar = self.bar(self.build())
        self.assertEqual([d.label for d in bar.destinations],
                         ["Wat2Do", "Future Finds", "Settings"])

    def test_each_destination_has_a_mark_of_its_own_and_a_lit_one(self):
        """Four grey glyphs that differ only in outline are not navigation."""
        bar = self.bar(self.build())
        icons = [d.icon for d in bar.destinations]
        lit = [d.selected_icon for d in bar.destinations]
        self.assertEqual(len(set(icons)), len(icons), icons)
        self.assertEqual(len(set(lit)), len(lit), lit)
        for a, b in zip(icons, lit):
            self.assertNotEqual(a, b, "the lit mark must differ from the resting one")

    def test_the_bar_moves_between_every_surface_it_carries(self):
        page = self.build()
        for key in ("future", "settings", "wat2do"):
            self.nav_to(page, key)
            self.assertEqual(self.bar(page).selected_index,
                             self.main.BAR_PAGES.index(key))

    def test_standing_on_help_lights_settings_because_that_is_where_it_lives(self):
        page = self.build()
        self.nav_to(page, "settings")
        self.go_to_help(page)
        self.assertEqual(self.main.surface_of(), "help")
        self.assertEqual(self.bar(page).selected_index,
                         self.main.BAR_PAGES.index("settings"))

    def go_to_help(self, page):
        """Tap the "How this works" row on the Settings surface."""
        rows = [c for c in walk(self.body(page))
                if isinstance(c, ft.Container) and c.on_click is not None
                and any("Read the short version" in t for t in texts(c))]
        self.assertEqual(len(rows), 1, "Settings must lead to the help surface")
        rows[0].on_click(Event(rows[0]))

    def test_every_surface_is_reachable_with_the_hamburger_hidden(self):
        """The default. Three stops on the bar and the fourth under Settings,
        so hiding the trigger costs nothing but the trigger."""
        page = self.build()
        self.assertFalse(self.wprefs.menu_button())
        self.assertIsNone(page.drawer, "no drawer is built while none can be opened")
        menu = [c for c in walk(self.topbar(page))
                if isinstance(c, ft.IconButton) and c.icon == ft.Icons.MENU]
        self.assertEqual(menu, [], "the slot goes away rather than sitting empty")
        reached = []
        for key in ("wat2do", "future", "settings"):
            self.nav_to(page, key)
            reached.append(key)
        self.go_to_help(page)
        reached.append("help")
        self.assertEqual(sorted(reached), sorted(self.main.PAGES))

    def test_the_drawer_moves_between_them_once_it_is_switched_on(self):
        page = self.build()
        self.show_menu()
        self.assertIsNotNone(first(self.topbar(page), ft.IconButton))
        rows = [c for c in walk(page.drawer)
                if isinstance(c, ft.Container) and c.on_click is not None]
        self.assertGreaterEqual(len(rows), len(self.main.PAGES))
        for key in ("future", "help", "wat2do"):
            label = self.main.LABELS[key]
            row = next(r for r in walk(page.drawer)
                       if isinstance(r, ft.Container) and r.on_click is not None
                       and label in texts(r))
            row.on_click(Event(row))
            # The drawer defers its navigation so the drawer can close first.
            page.take_task("_drawer_go_async")
            self.assertEqual(self.main.surface_of(), key)
            self.assertFalse(page.drawer_open)

    def test_the_drawer_offers_what_the_bar_cannot(self):
        """Otherwise the drawer is a second copy of the bottom bar."""
        page = self.build()
        self.show_menu()
        said = texts(page.drawer)
        self.assertIn("How this works", said)
        self.assertTrue(any(s.startswith("Theme - ") for s in said), said)

    def test_which_one_is_lit_is_read_back_not_remembered(self):
        """The page's own tab bar writes _TAB_INDEX when she swipes. A copy
        of "where am I" kept in the shell would be wrong from that moment
        until the next repaint, so the shell keeps none."""
        page = self.build()
        self.show_menu()
        _TAB_INDEX["wat2do"] = 1          # as a swipe on the tab bar would
        self.assertEqual(self.main.surface_of(), "future")
        self.rerender()
        self.assertEqual(self.bar(page).selected_index, 1)
        highlighted = [c for c in walk(page.drawer)
                       if isinstance(c, ft.Container) and c.bgcolor
                       and "Future Finds" in texts(c)]
        self.assertTrue(highlighted, "the drawer must light the tab she is on")

    def test_a_swipe_still_wins_after_a_visit_to_settings(self):
        """Settings is remembered by the shell because it has no tab bar to
        read back; the moment a tab page is shown again, the tab index is
        the truth once more."""
        page = self.build()
        self.nav_to(page, "settings")
        self.assertEqual(self.main.surface_of(), "settings")
        self.nav_to(page, "wat2do")
        _TAB_INDEX["wat2do"] = 1
        self.assertEqual(self.main.surface_of(), "future")

    def test_everything_tappable_in_the_chrome_answers_the_tap(self):
        """Flet's own showcase feels smooth because every tappable surface
        ripples; this app had controls that did nothing until their handler
        came back, and a tap with no acknowledgement is a tap made twice."""
        page = self.build()
        self.show_menu()
        for tree in (self.topbar(page), page.drawer):
            for c in walk(tree):
                if isinstance(c, ft.Container) and c.on_click is not None:
                    said = texts(c)
                    self.assertTrue(c.ink, said)
                    self.assertIsNotNone(c.ink_color, said)
                    self.assertIsNotNone(c.animate, said)

    def test_navigation_does_no_work_of_its_own(self):
        """A slow synchronous handler holds the old frame and drops the next
        taps. Moving between surfaces must be a tab index and a repaint."""
        page = self.build()
        before = len(self.opener.calls)
        self.nav_to(page, "future")
        # The page's own reads are already deferred behind page.run_task;
        # the shell must not add a blocking one on the way in.
        self.assertLessEqual(len(self.opener.calls) - before, 2,
                             "navigation should not go to the wire")

    def test_reaching_settings_costs_nothing_on_the_wire(self):
        page = self.build()
        before = len(self.opener.calls)
        self.nav_to(page, "settings")
        self.assertEqual(len(self.opener.calls), before,
                         "Settings is entirely local and must not probe")


class TheThemeApplies(WatudoCase):
    def picker(self, page):
        """Open the topbar's palette button and hand back its dialog."""
        buttons = [c for c in walk(self.shell(page))
                   if isinstance(c, ft.IconButton) and c.icon == ft.Icons.PALETTE]
        self.assertEqual(len(buttons), 1)
        buttons[0].on_click(Event(buttons[0]))
        return page.dialogs[-1]

    def test_the_picker_lists_every_theme(self):
        dlg = self.picker(self.build())
        said = texts(dlg)
        from agedashii_qi import THEME_NAMES
        for name in THEME_NAMES.values():
            self.assertIn(name, said)

    def swatch(self, dlg, key):
        from agedashii_qi import THEME_NAMES
        name = THEME_NAMES[key]
        for c in walk(dlg):
            if (isinstance(c, ft.Container) and c.on_click is not None
                    and name in texts(c)):
                return c
        self.fail(f"no row for {name!r} in the picker")

    def test_picking_one_reaches_the_palette_and_the_repaint(self):
        page = self.build()
        target = self.swatch(self.picker(page), "matrix")
        target.on_click(Event(target))
        self.assertEqual(S.theme, "matrix")
        self.assertEqual(PAL()["bg"], THEMES["matrix"]["bg"])
        # The repaint the pick triggers must use the new palette, not the
        # one the topbar was built with.
        topbar = self.shell(page).content.controls[0]
        self.assertEqual(topbar.bgcolor, PAL()["bg"])

    def test_a_light_theme_flips_materials_own_mode(self):
        page = self.build()
        from agedashii_qi import apply_theme
        apply_theme(page, "white")
        self.assertEqual(page.theme_mode, ft.ThemeMode.LIGHT)
        apply_theme(page, "matrix")
        self.assertEqual(page.theme_mode, ft.ThemeMode.DARK)

    def test_the_theme_is_remembered_for_the_next_launch(self):
        """The dashboard's picker is per-session and resets every launch.
        In an app you open to tick three boxes that is worse, so this one
        writes the choice down - locally, with no server involved."""
        page = self.build()
        target = self.swatch(self.picker(page), "nord")
        target.on_click(Event(target))
        page.take_task("_write_theme")
        self.assertEqual(self.stub_prefs.data[self.main.KEY_THEME], "nord")

    def test_a_stored_theme_is_put_back_at_launch(self):
        self.build({self.main.KEY_THEME: "nord"})
        self.assertEqual(S.theme, "nord")

    def test_a_stored_theme_that_no_longer_exists_falls_back(self):
        from agedashii_qi import DEFAULT
        self.build({self.main.KEY_THEME: "a-theme-that-was-deleted"})
        self.assertEqual(S.theme, DEFAULT)


class TheChromeIsHonest(WatudoCase):
    CLOUDS = (ft.Icons.CLOUD_UPLOAD, ft.Icons.CLOUD_OFF)

    def chip(self, page):
        """The outbox chip: the one tappable Container carrying a cloud."""
        topbar = self.shell(page).content.controls[0]
        for c in walk(topbar):
            icon = first(c, ft.Icon)
            if (isinstance(c, ft.Container) and c.on_click is not None
                    and icon is not None and icon.icon in self.CLOUDS):
                return c
        self.fail("no outbox chip in the topbar")

    def test_the_chips_glyph_actually_changes(self):
        """ft.Icon exposes the glyph as `.icon` in Flet 0.86 and the class is
        not slotted, so `icon.name = ...` binds a stray attribute, raises
        nothing, and leaves the cloud pointing up while the queue is stuck.
        Both states are asserted because only one of them is ever wrong."""
        from unittest import mock
        page = self.build()
        base = dict(self.api.sync_status())
        for status, expected in (
                ({**base, "pending": 1, "label": "1 change waiting"},
                 ft.Icons.CLOUD_UPLOAD),
                ({**base, "parked": 1, "label": "1 change the server refused"},
                 ft.Icons.CLOUD_OFF)):
            with self.subTest(expected=expected):
                with mock.patch.object(self.api, "sync_status",
                                       return_value=status):
                    self.rerender()
                    icon = first(self.chip(page), ft.Icon)
                self.assertEqual(icon.icon, expected)

    def test_nothing_queued_shows_no_chip(self):
        """An always-present "all synced" badge would be noise, and worse, a
        claim about the server when it is only a claim about the queue."""
        self.assertFalse(self.chip(self.build()).visible)

    def test_a_queued_write_is_announced(self):
        from unittest import mock
        page = self.build()
        status = dict(self.api.sync_status())
        status.update({"pending": 2, "label": "2 changes waiting"})
        with mock.patch.object(self.api, "sync_status", return_value=status):
            self.rerender()
            chip = self.chip(page)
            self.assertTrue(chip.visible)
            self.assertIn("2 changes waiting", texts(chip))

    def test_a_refused_write_is_announced_differently(self):
        from unittest import mock
        page = self.build()
        status = dict(self.api.sync_status())
        status.update({"parked": 1, "label": "1 change the server refused"})
        with mock.patch.object(self.api, "sync_status", return_value=status):
            self.rerender()
            chip = self.chip(page)
        self.assertTrue(chip.visible)
        self.assertEqual(first(chip, ft.Icon).icon, ft.Icons.CLOUD_OFF)

    def test_launch_tries_to_drain_whatever_the_last_session_queued(self):
        """A queue survives a process death; it must not survive unnoticed."""
        page = self.build()
        self.assertTrue(page.threads,
                        "the shell should have put a drain on a worker thread")

    def test_customize_appears_on_wat2do_which_has_cards(self):
        page = self.build()
        self.assertTrue(panels.has_surface(),
                        "Wat2Do's Present tab is a panels.layout card stack")
        icons = [c.icon for c in every(self.shell(page), ft.IconButton)]
        self.assertIn(ft.Icons.DASHBOARD_CUSTOMIZE, icons)


class SettingsIsOneOfTheSurfaces(WatudoCase):
    """The shell's half of Settings: that it is a destination like any other
    rather than something the topbar opens over the page.

    What each control on it actually does is `test_settings.py`.
    """

    def test_there_is_no_gear_in_the_topbar_any_more(self):
        """A third way to the same screen is what made the old drawer a copy
        of the old bar. Settings is on the bar; that is the way there."""
        page = self.build()
        gears = [c for c in walk(self.topbar(page))
                 if isinstance(c, ft.IconButton) and c.icon == ft.Icons.SETTINGS]
        self.assertEqual(gears, [])

    def test_it_opens_as_the_page_body_not_as_a_dialog(self):
        page = self.build()
        self.nav_to(page, "settings")
        self.assertEqual(page.dialogs, [], "Settings is a surface, not a dialog")
        said = " ".join(texts(self.body(page)))
        for heading in ("Server", "Navigation", "Theme", "Appearance",
                        "Your day", "Pain & spoons", "Diagnostics"):
            self.assertIn(heading, said)

    def test_it_still_says_the_address_and_why_it_is_not_answering(self):
        page = self.build()
        self.nav_to(page, "settings")
        said = " ".join(texts(self.body(page)))
        self.assertIn(self.config.SERVER_URL, said)
        self.assertIn("unreachable", said,
                      "a failure must say which failure it was")

    def test_the_topbar_keeps_the_theme_button_because_it_is_not_navigation(self):
        """It changes the app you are looking at without leaving the page
        you are on, which no destination can do."""
        page = self.build()
        palettes = [c for c in walk(self.topbar(page))
                    if isinstance(c, ft.IconButton) and c.icon == ft.Icons.PALETTE]
        self.assertEqual(len(palettes), 1)

    def test_a_surface_that_raises_names_the_surface_that_raised(self):
        from unittest import mock
        from agedashii_watudo.pages import settings as settings_page
        page = self.build()
        with mock.patch.object(settings_page, "build",
                               side_effect=RuntimeError("kaboom")):
            self.nav_to(page, "settings")
        said = " ".join(texts(self.body(page)))
        self.assertIn("Settings could not be built", said)
        self.assertIn("kaboom", said)


class TheScreenTreatment(WatudoCase):
    def test_the_crt_goes_over_the_safearea_and_not_inside_it(self):
        """A screen effect that stopped short of the cutout would draw a
        bright band exactly where the cutout is."""
        page = self.build()
        prefs.set_crt_overlay(True)
        prefs.apply_runtime()
        self.rerender()
        root = self.shell(page)
        self.assertIsInstance(root, ft.Stack)
        self.assertEqual(len(root.controls), 2)
        self.assertIsInstance(root.controls[0], ft.SafeArea)

    def test_every_layer_of_the_overlay_still_ignores_taps(self):
        """A single missed flag kills input for the whole app: the overlay
        covers every button underneath it."""
        page = self.build()
        prefs.set_crt_overlay(True)
        prefs.apply_runtime()
        self.rerender()
        overlay = self.shell(page).controls[1]
        layers = [c for c in walk(overlay) if isinstance(c, ft.Container)]
        self.assertEqual(len(layers), 3)
        for layer in layers:
            self.assertTrue(layer.ignore_interactions)

    def test_the_corner_radius_setting_reaches_the_shell(self):
        page = self.build()
        prefs.set_corner_radius(0)
        prefs.apply_runtime()
        self.rerender()
        from agedashii_qi import RAD
        self.assertEqual(RAD(10), 0)
        pill = next(c for c in walk(self.shell(page))
                    if isinstance(c, ft.Container) and c.gradient is not None)
        self.assertEqual(pill.border_radius, 0)
