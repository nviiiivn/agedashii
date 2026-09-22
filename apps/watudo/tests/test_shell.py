"""The shell Watudo is an app because of: it builds, navigates and themes.

Everything here runs with the radio off - `config._opener` refuses every
request - because that is the condition the app is designed for, not an
edge case to check last. A shell that only assembles when a server answers
would be a shell that blanks on the train.

What each class pins down:

``TheShellBuilds``        one root control, the chrome in the right order,
                          the pill saying Watudo and not Agedashii.
``EverySurfaceRenders``   both destinations build, offline, from cold.
``TheNavReachesEachOne``  drawer and bottom bar both move between them, and
                          both read which one is lit out of the same place
                          the page's own tab bar writes.
``TheThemeApplies``       a picked theme reaches the palette, the page's
                          Material theme and the next repaint - and is still
                          there on the next launch.
``TheChromeIsHonest``     the outbox chip says what is queued and nothing
                          when nothing is, and Customize only appears on a
                          surface with cards to arrange.
``TheScreenTreatment``    SafeArea wraps the chrome, the CRT overlay goes
                          over the top of it, and every layer of the overlay
                          still ignores taps.
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

    def test_the_bottom_bar_can_be_turned_off_and_the_drawer_still_reaches_both(self):
        page = self.build()
        prefs.set_bottom_bar_enabled(False)
        self.rerender()
        root = self.shell(page)
        self.assertIsNone(first(root, ft.NavigationBar))
        self.assertEqual(len(page.controls), 1)
        drawer_labels = texts(page.drawer)
        self.assertIn("Wat2Do", drawer_labels)
        self.assertIn("Future Finds", drawer_labels)


class EverySurfaceRenders(WatudoCase):
    """Both destinations, built cold with nothing to build them from."""

    def surface_tree(self, page):
        return self.shell(page).content.controls[1]

    def test_wat2do_is_what_the_app_opens_on(self):
        page = self.build()
        self.assertEqual(_TAB_INDEX["wat2do"], 0)
        tabs = first(self.surface_tree(page), ft.Tabs)
        self.assertIsNotNone(tabs, "Wat2Do is a tabbed page")
        self.assertEqual(tabs.selected_index, 0)

    def test_future_finds_renders_offline_with_no_someday_list(self):
        page = self.build()
        self.main.surface_of()          # no side effects; it only reads
        bar = first(self.shell(page), ft.NavigationBar)
        bar.selected_index = 1
        bar.on_change(Event(bar))
        tabs = first(self.surface_tree(page), ft.Tabs)
        self.assertEqual(tabs.selected_index, 1)
        said = " ".join(texts(self.surface_tree(page)))
        self.assertIn("Not connected", said,
                      "offline, Future Finds must say so rather than show a "
                      "plausible empty list as though it were the answer")

    def test_neither_surface_invents_a_number_when_it_has_no_data(self):
        """Offline and cold there is nothing to show, so nothing is shown."""
        page = self.build()
        said = " ".join(texts(self.surface_tree(page)))
        self.assertNotIn("sample data", said)

    def test_a_surface_that_raises_says_so_instead_of_blanking(self):
        """`wat2do.build` hands `_tabs` two already-built bodies, so a crash
        inside one lands in the shell rather than in the tab guard."""
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
    def bar(self, page):
        return first(self.shell(page), ft.NavigationBar)

    def test_the_bar_carries_both_surfaces_and_nothing_else(self):
        bar = self.bar(self.build())
        self.assertEqual([d.label for d in bar.destinations],
                         ["Wat2Do", "Future Finds"])

    def test_the_bar_moves_between_them(self):
        page = self.build()
        for index, expected in ((1, "future"), (0, "wat2do")):
            bar = self.bar(page)
            bar.selected_index = index
            bar.on_change(Event(bar))
            self.assertEqual(self.main.surface_of(), expected)
            self.assertEqual(self.bar(page).selected_index, index)

    def test_the_drawer_moves_between_them(self):
        page = self.build()
        rows = [c for c in walk(page.drawer)
                if isinstance(c, ft.Container) and c.on_click is not None]
        self.assertGreaterEqual(len(rows), 2)
        future_row = next(r for r in rows if "Future Finds" in texts(r))
        future_row.on_click(Event(future_row))
        # The drawer defers its navigation so the drawer can close first.
        page.take_task("_drawer_go_async")
        self.assertEqual(self.main.surface_of(), "future")
        self.assertFalse(page.drawer_open)

    def test_the_drawer_also_offers_the_two_things_the_bar_cannot(self):
        """Otherwise the drawer is a second copy of the bottom bar."""
        said = texts(self.build().drawer)
        self.assertIn("Settings", said)
        self.assertTrue(any(s.startswith("Theme - ") for s in said), said)

    def test_which_one_is_lit_is_read_back_not_remembered(self):
        """The page's own tab bar writes _TAB_INDEX when she swipes. A copy
        of "where am I" kept in the shell would be wrong from that moment
        until the next repaint, so the shell keeps none."""
        page = self.build()
        _TAB_INDEX["wat2do"] = 1          # as a swipe on the tab bar would
        self.assertEqual(self.main.surface_of(), "future")
        self.rerender()
        self.assertEqual(self.bar(page).selected_index, 1)
        highlighted = [c for c in walk(page.drawer)
                       if isinstance(c, ft.Container) and c.bgcolor
                       and "Future Finds" in texts(c)]
        self.assertTrue(highlighted, "the drawer must light the tab she is on")

    def test_navigation_does_no_work_of_its_own(self):
        """A slow synchronous handler holds the old frame and drops the next
        taps. Moving between surfaces must be a tab index and a repaint."""
        page = self.build()
        before = len(self.opener.calls)
        bar = self.bar(page)
        bar.selected_index = 1
        bar.on_change(Event(bar))
        # The page's own reads are already deferred behind page.run_task;
        # the shell must not add a blocking one on the way in.
        self.assertLessEqual(len(self.opener.calls) - before, 2,
                             "navigation should not go to the wire")


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


class SettingsIsSmallButReal(WatudoCase):
    """Watudo has its own preferences store, so it cannot inherit an address
    set in the dashboard. Without this dialog it could only ever talk to the
    two addresses compiled into `config.DEFAULT_CANDIDATES`."""

    def open(self, page):
        buttons = [c for c in walk(self.shell(page))
                   if isinstance(c, ft.IconButton) and c.icon == ft.Icons.SETTINGS]
        self.assertEqual(len(buttons), 1)
        buttons[0].on_click(Event(buttons[0]))
        return page.dialogs[-1]

    def test_it_shows_the_address_and_why_it_is_not_answering(self):
        dlg = self.open(self.build())
        said = " ".join(texts(dlg))
        self.assertIn(self.config.SERVER_URL, said)
        self.assertIn("unreachable", said,
                      "a failure must say which failure it was")

    def test_a_new_address_is_probed_and_written_down(self):
        import asyncio
        page = self.build()
        dlg = self.open(page)
        field = first(dlg.content, ft.TextField)
        field.value = "10.0.0.9:6900"
        asyncio.run(field.on_submit(Event(field)))
        self.assertEqual(self.config.SERVER_URL, "http://10.0.0.9:6900")
        self.assertEqual(self.stub_prefs.data[self.config.KEY_URL],
                         "http://10.0.0.9:6900")
        self.assertIn("http://10.0.0.9:6900/api/config", self.opener.calls)

    def test_the_knobs_take_effect_on_the_page_behind_the_dialog(self):
        page = self.build()
        dlg = self.open(page)
        crt = [s for s in walk(dlg) if isinstance(s, ft.Switch)][-1]
        crt.value = True
        crt.on_change(Event(crt))
        self.assertTrue(prefs.crt_overlay())
        self.assertIsInstance(self.shell(page), ft.Stack)

    def test_the_bottom_bar_is_optional_from_inside_the_app(self):
        """The page carries its own Present | Future tab bar, so on a phone
        the bottom bar is a second row of the same two choices. Having the
        switch here is what makes "optional" true rather than a word."""
        page = self.build()
        dlg = self.open(page)
        bar_switch = [s for s in walk(dlg) if isinstance(s, ft.Switch)][0]
        self.assertTrue(bar_switch.value)
        bar_switch.value = False
        bar_switch.on_change(Event(bar_switch))
        self.assertFalse(prefs.bottom_bar_enabled())
        self.assertIsNone(first(self.shell(page), ft.NavigationBar))


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
