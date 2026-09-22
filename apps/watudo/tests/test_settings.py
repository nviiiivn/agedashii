"""Settings, control by control: every one changes what it says it changes.

A settings screen is the one place in an app where a control that does
nothing is invisible. A switch flips, the page looks right, and the thing it
claims to govern carries on exactly as before - and nobody finds out until
the setting is the thing they needed. So each test here flips one control and
then asserts against the *consequence*, never against the control: the
palette after a theme, `RAD()` after a radius, the built Wat2Do surface after
a day setting, `W.origin()` after an origin.

Everything runs with the radio off, because Settings is entirely local and
must build and work with nothing to talk to.
"""
import asyncio

import flet as ft

from agedashii_qi import PAL, RAD, S, THEMES
from agedashii_washi import prefs

from .harness import Event, WatudoCase, every, first, texts, walk


class SettingsCase(WatudoCase):
    """A shell parked on Settings, with the handles to poke at it."""

    def setUp(self):
        super().setUp()
        self.page = self.build()
        self.nav_to(self.page, "settings")

    # --- finding things --------------------------------------------------
    def tree(self):
        return self.body(self.page)

    def switch(self, label):
        """The Switch on the row whose label is exactly `label`."""
        for c in walk(self.tree()):
            if isinstance(c, ft.Row) and label in texts(c):
                found = first(c, ft.Switch)
                if found is not None:
                    return found
        self.fail(f"no switch labelled {label!r} on the Settings surface")

    def flip(self, label, value):
        sw = self.switch(label)
        sw.value = value
        asyncio.run(sw.on_change(Event(sw)))

    def tap(self, text):
        """The tappable container carrying exactly this text - a chip, a row."""
        for c in walk(self.tree()):
            if (isinstance(c, ft.Container) and c.on_click is not None
                    and text in texts(c)):
                return c
        self.fail(f"nothing tappable says {text!r} on the Settings surface")

    def choose(self, label, value):
        """Pick `value` from the dropdown labelled `label`. 0.86: on_select."""
        for c in walk(self.tree()):
            if isinstance(c, ft.Dropdown) and c.label == label:
                self.assertIsNone(getattr(c, "on_change", None),
                                  "Dropdown has no on_change in Flet 0.86 - "
                                  "setting one ships a control that silently "
                                  "does nothing")
                c.value = value
                asyncio.run(c.on_select(Event(c)))
                return
        self.fail(f"no dropdown labelled {label!r}")

    def field(self, hint):
        for c in walk(self.tree()):
            if isinstance(c, ft.TextField) and c.hint_text == hint:
                return c
        self.fail(f"no field hinted {hint!r}")

    def wat2do_tree(self):
        """Go back to Wat2Do and hand back what it built."""
        self.nav_to(self.page, "wat2do")
        return self.body(self.page)


class TheSurfaceItselfIsReal(SettingsCase):
    def test_it_builds_offline_from_cold(self):
        said = " ".join(texts(self.tree()))
        self.assertIn("Server", said)
        self.assertIn("Measure distances from", said)

    def test_every_card_says_what_its_controls_do(self):
        """A row of unexplained toggles is a settings screen you are afraid
        to touch. Every switch here carries its own sentence."""
        rows = [c for c in walk(self.tree())
                if isinstance(c, ft.Row) and first(c, ft.Switch) is not None]
        self.assertGreaterEqual(len(rows), 6)
        for row in rows:
            said = [t for t in texts(row) if t]
            self.assertGreaterEqual(len(said), 2, said)

    def test_nothing_inside_a_wrap_is_allowed_to_expand(self):
        """A Row(wrap=True) hands its children unbounded constraints, so a
        control that expands into one paints over the whole screen. That
        exact bug made another app's page unusable twice."""
        wraps = [c for c in walk(self.tree())
                 if isinstance(c, ft.Row) and getattr(c, "wrap", False)]
        self.assertTrue(wraps, "the themes and radii are laid out in wraps")
        for wrap in wraps:
            for child in walk(wrap):
                if child is wrap:
                    continue
                self.assertFalse(getattr(child, "expand", None),
                                 f"{type(child).__name__} expands inside a wrap")
                self.assertNotIsInstance(child, ft.TextField,
                                         "a filled TextField in a wrap paints "
                                         "grey over the whole screen")

    def test_the_saved_message_survives_the_repaint_it_caused(self):
        """Appearance settings rebuild the whole shell, which builds a new
        Settings page with a new status line - so a message written to the
        old one would be thrown away in the same breath it was written."""
        self.flip("Accent glow", True)
        self.assertIn("accent glow on", " ".join(texts(self.tree())))

    def test_a_setting_that_only_changes_wat2do_does_not_repaint_the_shell(self):
        """A repaint would scroll Settings back to the top to update a page
        nobody is looking at."""
        before = self.tree()
        self.flip("Hide finished tasks", True)
        self.assertIs(self.tree(), before, "the surface was rebuilt for nothing")

    def test_it_never_goes_to_the_wire_to_draw_itself(self):
        before = len(self.opener.calls)
        self.rerender()
        self.assertEqual(len(self.opener.calls), before)


class TheAppearanceControls(SettingsCase):
    def test_a_theme_reaches_the_palette_the_shell_and_storage(self):
        swatch = self.tap("Matrix")
        asyncio.run(swatch.on_click(Event(swatch)))
        self.assertEqual(S.theme, "matrix")
        self.assertEqual(PAL()["bg"], THEMES["matrix"]["bg"])
        self.assertEqual(self.topbar(self.page).bgcolor, PAL()["bg"])
        self.assertEqual(self.stub_prefs.data[self.main.KEY_THEME], "matrix")

    def test_the_corner_radius_reaches_every_rounded_corner(self):
        chip = self.tap("Square")
        asyncio.run(chip.on_click(Event(chip)))
        self.assertEqual(prefs.corner_radius(), 0)
        self.assertEqual(RAD(10), 0)
        pill = next(c for c in walk(self.topbar(self.page))
                    if isinstance(c, ft.Container) and c.gradient is not None)
        self.assertEqual(pill.border_radius, 0)

    def test_the_glow_switch_reaches_the_drawing_layer(self):
        self.flip("Accent glow", True)
        self.assertTrue(prefs.accent_glow())
        self.assertTrue(S.glow)

    def test_the_crt_switch_puts_the_overlay_over_the_whole_app(self):
        self.flip("CRT overlay", True)
        self.assertTrue(prefs.crt_overlay())
        root = self.shell(self.page)
        self.assertIsInstance(root, ft.Stack)
        self.assertEqual(len(root.controls), 2)

    def test_the_intensity_slider_is_dead_until_the_overlay_is_on(self):
        """It was stored and honoured with no control in this app at all -
        on or off at whatever intensity the dashboard last set."""
        slider = first(self.tree(), ft.Slider)
        self.assertTrue(slider.disabled)
        self.flip("CRT overlay", True)
        self.assertFalse(first(self.tree(), ft.Slider).disabled)

    def test_the_intensity_is_only_written_when_the_thumb_is_let_go(self):
        self.flip("CRT overlay", True)
        slider = first(self.tree(), ft.Slider)
        before = prefs.crt_intensity()
        slider.value = 90
        slider.on_change(Event(slider))
        self.assertEqual(prefs.crt_intensity(), before,
                         "dragging must not write to storage on every tick")
        asyncio.run(slider.on_change_end(Event(slider)))
        self.assertEqual(prefs.crt_intensity(), 90)


class TheNavigationControls(SettingsCase):
    def test_the_menu_button_switch_puts_the_hamburger_back(self):
        self.assertEqual([c for c in walk(self.topbar(self.page))
                          if isinstance(c, ft.IconButton)
                          and c.icon == ft.Icons.MENU], [])
        self.flip("Menu button", True)
        self.assertTrue(self.wprefs.menu_button())
        menu = [c for c in walk(self.topbar(self.page))
                if isinstance(c, ft.IconButton) and c.icon == ft.Icons.MENU]
        self.assertEqual(len(menu), 1)
        self.assertIsNotNone(self.page.drawer)

    def test_hiding_it_again_takes_the_slot_away_rather_than_blanking_it(self):
        self.flip("Menu button", True)
        self.flip("Menu button", False)
        self.assertFalse(self.wprefs.menu_button())
        row = self.topbar(self.page).content
        self.assertNotIsInstance(row.controls[0], ft.IconButton)
        self.assertIsNone(self.page.drawer)

    def test_the_bottom_bar_switch_takes_the_bar_away(self):
        self.flip("Bottom bar", False)
        self.assertFalse(prefs.bottom_bar_enabled())
        self.assertIsNone(first(self.shell(self.page), ft.NavigationBar))

    def test_turning_the_bar_off_forces_the_menu_back_on(self):
        """The one rail: something has to reach every surface. The switch
        says so instead of the app quietly disagreeing with it."""
        self.flip("Bottom bar", False)
        self.assertTrue(self.main.menu_visible())
        self.assertFalse(self.wprefs.menu_button(),
                         "her own setting is untouched - it is overridden, "
                         "not rewritten")
        menu_switch = self.switch("Menu button")
        self.assertTrue(menu_switch.value)
        self.assertTrue(menu_switch.disabled)
        self.assertIsNotNone(self.page.drawer)

    def test_settings_describes_the_navigation_the_shell_actually_builds(self):
        said = " ".join(texts(self.tree()))
        self.assertIn("Wat2Do, Future Finds, Settings", said)
        self.flip("Bottom bar", False)
        self.assertIn("Bottom bar: off", " ".join(texts(self.tree())))


class TheDayControls(SettingsCase):
    def test_friendly_day_names_reach_the_date_row(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.assertEqual(W.date_label(W.today_str()), "Today")
        self.flip("Friendly day names", False)
        self.assertNotEqual(W.date_label(W.today_str()), "Today")
        self.assertIn(" ", W.date_label(W.today_str()))

    def test_hiding_finished_tasks_takes_them_out_of_the_list(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        from agedashii_watudo.pages import wat2do
        day = W.today_str()
        W.save_state(day, {"Meds": True, "Walk": False}, 2, 3)
        state = dict(W.state(day) or {})
        state["items"] = ["Meds", "Walk"]
        wat2do._S.update({"date": day, "state": state})

        said = " ".join(texts(self.wat2do_tree()))
        self.assertIn("Meds", said)
        self.nav_to(self.page, "settings")
        self.flip("Hide finished tasks", True)
        wat2do._S["state"] = state
        said = " ".join(texts(self.wat2do_tree()))
        self.assertNotIn("Meds", said)
        self.assertIn("Walk", said, "unfinished tasks stay")
        self.assertIn("1/2", said, "the count still counts what is done")


class ThePainAndSpoonsControls(SettingsCase):
    def test_turning_tracking_off_stops_the_card_being_drawn(self):
        self.assertIn("vitals", prefs.panels("wat2do"))
        said = " ".join(texts(self.wat2do_tree()))
        self.assertIn("VITALS", said)
        self.nav_to(self.page, "settings")
        self.flip("Track them", False)
        said = " ".join(texts(self.wat2do_tree()))
        self.assertNotIn("VITALS", said)
        self.assertIn("vitals", prefs.panels("wat2do"),
                      "the card keeps its place in her saved order")

    def test_the_steering_switch_goes_dead_with_nothing_to_steer_with(self):
        self.flip("Track them", False)
        steer = self.switch("Let them steer the day")
        self.assertTrue(steer.disabled)
        self.assertFalse(self.wprefs.vitals_steer())

    def test_steering_off_leaves_the_suggestion_to_the_weather(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        # Two spoons is "low spoons - keep it close" while steering is on.
        self.assertEqual(W.suggest(0, 2, None)[0], "walkable")
        self.flip("Let them steer the day", False)
        self.assertFalse(self.wprefs.vitals_steer())
        # The page asks with (None, None) once steering is off, and that is
        # the weather's answer alone.
        self.assertEqual(W.suggest(None, None, None)[0], "food")

    def test_the_numbers_are_still_recorded_while_steering_is_off(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.flip("Let them steer the day", False)
        ok, _msg, record = W.save_state(W.today_str(), {}, 7, 1)
        self.assertTrue(ok)
        self.assertEqual(record["pain"], 7)


class TheTemplateAndMissionControls(SettingsCase):
    def test_the_sub_task_marker_is_what_the_editor_writes(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.assertEqual(W.parse_template_text("Wash\n> socks"), ["Wash", "> socks"])
        self.choose("Sub-task lines are written as", "indent")
        self.assertEqual(self.wprefs.sub_marker(), "indent")
        self.assertEqual(W.parse_template_text("Wash\n> socks"), ["Wash", "  socks"])

    def test_both_markers_are_read_back_as_the_same_sub_task(self):
        """The choice may not change what the browser sees."""
        from agedashii_watudo.pages import _wat2do_parts as W
        for lines in (["Wash", "> socks"], ["Wash", "  socks"]):
            parsed = W.parse_items(lines)
            self.assertEqual([p["depth"] for p in parsed], [0, 1], lines)

    def test_a_pinned_mission_size_is_the_number_it_deals(self):
        import random
        from agedashii_watudo.pages import _wat2do_parts as W
        deck = [f"S{i}" for i in range(12)]
        venue = {"name": "x", "cat": "park", "lat": 0, "lon": 0, "dist_m": 10}
        ctx = {"indoor": False, "radius": 8000, "why": ""}
        rolled = W.roll_mission(venue, deck, ctx, random.Random(1))
        self.assertIn(len(rolled["subjects"]), range(3, 7))
        self.choose("Subjects per mission", "8")
        self.assertEqual(self.wprefs.mission_subjects(), 8)
        rolled = W.roll_mission(venue, deck, ctx, random.Random(1))
        self.assertEqual(len(rolled["subjects"]), 8)

    def test_a_pinned_size_larger_than_the_deck_deals_the_deck(self):
        import random
        from agedashii_watudo.pages import _wat2do_parts as W
        self.choose("Subjects per mission", "8")
        rolled = W.roll_mission({"name": "x", "cat": "park", "lat": 0, "lon": 0,
                                 "dist_m": 10}, ["A", "B"],
                                {"indoor": False, "radius": 0, "why": ""},
                                random.Random(2))
        self.assertEqual(sorted(rolled["subjects"]), ["A", "B"])


class TheOriginControl(SettingsCase):
    """The honest half of the location story: the app asks the device
    nothing, so the origin is something she types - and it is the caller
    `future.scan()`'s `centre` never had."""

    def set_origin(self, text):
        entry = self.field("37.769972, -122.216445")
        entry.value = text
        asyncio.run(entry.on_submit(Event(entry)))

    def test_it_starts_at_home(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.assertEqual(W.origin(), W.HOME)
        self.assertEqual(W.origin_label(), "home")

    def test_a_typed_point_moves_every_distance_in_the_app(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.set_origin("40.0,-120.0")
        self.assertEqual(W.origin(), (40.0, -120.0))
        self.assertIn("40.0000", W.origin_label())
        # The Overpass query is built around the origin, so the whole POI
        # scan moves with it rather than only the numbers in a list.
        ql = W.build_overpass_ql([{"q": "node[amenity=cafe];"}], 1000)
        self.assertIn("40.0,-120.0", ql)

    def test_the_future_scan_asks_the_server_about_the_new_point(self):
        """`centre` had no caller at all: every scan was measured from the
        shipped HOME whatever the rest of the app thought."""
        from unittest import mock
        from agedashii_watudo.pages import future
        self.set_origin("40.0,-120.0")
        asked = []
        with mock.patch.object(future.M, "_get",
                               side_effect=lambda path, **kw: asked.append(path)):
            future.scan(["pretty"], 80467)
        self.assertTrue(any("lat=40.0&lon=-120.0" in a for a in asked), asked)

    def test_something_that_is_not_a_coordinate_is_refused_and_says_so(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.set_origin("somewhere nice")
        self.assertEqual(W.origin(), W.HOME)
        self.assertIn("not a latitude", " ".join(texts(self.tree())))

    def test_a_pair_outside_the_world_is_refused(self):
        self.assertIsNone(self.wprefs.parse_origin("120.0,-9999"))
        self.assertIsNone(self.wprefs.parse_origin(""))
        self.assertIsNone(self.wprefs.parse_origin("40.0"))

    def test_home_is_one_tap_back(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.set_origin("40.0,-120.0")
        home = [b for b in every(self.tree(), ft.IconButton)
                if b.icon == ft.Icons.HOME]
        self.assertEqual(len(home), 1)
        asyncio.run(home[0].on_click(Event(home[0])))
        self.assertEqual(W.origin(), W.HOME)


class TheServerControl(SettingsCase):
    def test_a_new_address_is_probed_off_the_event_loop_and_written_down(self):
        """`config.set_url` opens a socket with a 3 s timeout, and three
        seconds inside a synchronous handler is three seconds of dropped
        taps."""
        entry = self.field("http://host:6900")
        entry.value = "10.0.0.9:6900"
        asyncio.run(entry.on_submit(Event(entry)))
        self.assertEqual(self.config.SERVER_URL, "http://10.0.0.9:6900")
        self.assertEqual(self.stub_prefs.data[self.config.KEY_URL],
                         "http://10.0.0.9:6900")
        self.assertIn("http://10.0.0.9:6900/api/config", self.opener.calls)


class EverySettingSurvivesARestart(WatudoCase):
    """Written to SharedPreferences on this device. No server is involved in
    any of it, and none is reachable in this test."""

    def test_the_payload_covers_every_key_the_module_ships(self):
        """A hand-written save list is how washi's bottom-bar switch came to
        be loaded but never saved: it worked all session and was back on at
        every restart, and nothing failed loudly enough to notice."""
        self.assertEqual(sorted(self.wprefs.save_payload()),
                         sorted(self.wprefs._defaults()))

    def test_they_all_come_back(self):
        self.build()
        self.wprefs.set_menu_button(True)
        self.wprefs.set_friendly_dates(False)
        self.wprefs.set_hide_done(True)
        self.wprefs.set_track_vitals(False)
        self.wprefs.set_sub_marker("indent")
        self.wprefs.set_mission_subjects(5)
        self.wprefs.set_origin("40.0,-120.0")
        asyncio.run(self.wprefs.save(self.stub_prefs))
        stored = dict(self.stub_prefs.data)
        self.wprefs.reset_all()

        self.build(stored)
        self.assertTrue(self.wprefs.menu_button())
        self.assertFalse(self.wprefs.friendly_dates())
        self.assertTrue(self.wprefs.hide_done())
        self.assertFalse(self.wprefs.track_vitals())
        self.assertEqual(self.wprefs.sub_marker(), "indent")
        self.assertEqual(self.wprefs.mission_subjects(), 5)
        self.assertEqual(self.wprefs.origin(), (40.0, -120.0))

    def test_a_flag_stored_as_the_string_false_is_still_false(self):
        """SharedPreferences round-trips False as "false", and bool("false")
        is True - which is how every switch in the dashboard once came back
        on after a restart. That shipped."""
        self.build({self.wprefs.KEY_FRIENDLY_DATES: "false",
                    self.wprefs.KEY_MENU_BUTTON: "false"})
        self.assertFalse(self.wprefs.friendly_dates())
        self.assertFalse(self.wprefs.menu_button())

    def test_steering_is_not_switched_off_for_good_by_a_day_without_tracking(self):
        """`vitals_steer()` reports False while nothing is tracked, so saving
        the derived answer would make it permanent."""
        self.build()
        self.wprefs.set_track_vitals(False)
        self.assertFalse(self.wprefs.vitals_steer())
        self.assertTrue(self.wprefs.save_payload()[self.wprefs.KEY_VITALS_STEER])
        self.wprefs.set_track_vitals(True)
        self.assertTrue(self.wprefs.vitals_steer())

    def test_a_corrupt_value_costs_the_setting_and_not_the_app(self):
        self.build({self.wprefs.KEY_SUB_MARKER: "hieroglyphs",
                    self.wprefs.KEY_MISSION_SUBJECTS: 99,
                    self.wprefs.KEY_ORIGIN: "the moon"})
        self.assertEqual(self.wprefs.sub_marker(), "caret")
        self.assertEqual(self.wprefs.mission_subjects(), 0)
        self.assertIsNone(self.wprefs.origin())


class TheHelpSurfaceIsHonest(WatudoCase):
    def open(self):
        page = self.build()
        self.nav_to(page, "settings")
        rows = [c for c in walk(self.body(page))
                if isinstance(c, ft.Container) and c.on_click is not None
                and any("Read the short version" in t for t in texts(c))]
        rows[0].on_click(Event(rows[0]))
        return page

    def test_it_explains_both_surfaces_and_the_things_they_do(self):
        said = " ".join(texts(self.body(self.open())))
        for topic in ("Wat2Do", "Future Finds", "pain", "spoons", "template",
                      "mission"):
            self.assertIn(topic, said)

    def test_it_says_what_queues_and_what_needs_the_server(self):
        said = " ".join(texts(self.body(self.open())))
        self.assertIn("40 ticks, 1 queued item", said)
        self.assertIn("needs the server", said.lower())

    def test_it_says_the_app_does_not_ask_where_you_are(self):
        said = " ".join(texts(self.body(self.open())))
        self.assertIn("does not ask the device where it is", said)
        self.assertIn("no location permission", said)

    def test_reading_it_costs_nothing_on_the_wire(self):
        self.open()
        before = len(self.opener.calls)
        self.rerender()
        self.assertEqual(len(self.opener.calls), before,
                         "the help page is static text and themed icons")

    def test_it_leads_back_to_settings(self):
        page = self.open()
        row = next(c for c in walk(self.body(page))
                   if isinstance(c, ft.Container) and c.on_click is not None
                   and any("Back to Settings" in t for t in texts(c)))
        row.on_click(Event(row))
        self.assertEqual(self.main.surface_of(), "settings")
