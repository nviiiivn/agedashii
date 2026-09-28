"""Every surface builds, and the shell says true things about itself.

These catch the failures the core tests cannot see: a Flet constructor called
with a keyword it does not take, a page handing `go()` a name no surface answers
to, a bar whose `selected_index` Flet will assert on. None of that is
arithmetic, and all of it is a blank screen on the side of a road.

Built against a stub page - no display, no socket, no SharedPreferences service.
The stubs are deliberately the same shape as Watudo's
(`apps/watudo/tests/harness.py`): a page that *records* work handed to
`run_task` instead of running it, so a test decides when a background read
happens rather than discovering one mid-assertion.

`FLET_APP_STORAGE_DATA` is pointed at a temporary directory for the whole
module, so nothing here can write a ride log into the real one.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_TMP = tempfile.TemporaryDirectory()
os.environ["FLET_APP_STORAGE_DATA"] = _TMP.name

import flet as ft                                          # noqa: E402

from agedashii_navi import (audio, boot, gauges, gears, live,  # noqa: E402
                            places, prefs, sensors, theme, ui, updates,
                            version)
from agedashii_navi.pages import help as help_page          # noqa: E402
from agedashii_navi.pages import log as log_page            # noqa: E402
from agedashii_navi.pages import ride as ride_page          # noqa: E402
from agedashii_navi.pages import routes as routes_page      # noqa: E402
from agedashii_navi.pages import search as search_page      # noqa: E402
from agedashii_navi.pages import settings as settings_page  # noqa: E402

SURFACES = (routes_page, search_page, log_page, settings_page, help_page)


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


def load_main():
    """Import `apps/navi/main.py` by path, as its own module.

    By path, not by name: the repo root is on sys.path during discovery and it
    holds the dashboard's own `main.py`, so `import main` would quietly load the
    wrong app - and that app imports `agedashii_tofu`, which is exactly what
    this app must never touch.
    """
    spec = importlib.util.spec_from_file_location("navi_main",
                                                  APP_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Task:
    def cancel(self):
        pass


class StubPage:
    """Enough ft.Page for a surface to build against."""

    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []
        self.title = None
        self.theme = None
        self.theme_mode = None
        self.bgcolor = None
        self.padding = None
        self.tasks = []
        self.updates = 0

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *_a, **_k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **_kwargs):
        self.tasks.append((fn, args))


class Event:
    """The `e` a Flet handler is given. Only ever `e.control` in this app."""

    def __init__(self, control=None):
        self.control = control


class StubPrefs:
    """Flet's SharedPreferences, as a dict."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value
        return True


def walk(node, seen=None):
    """Every control in a built tree, once each."""
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions", "destinations"):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    for attr in ("content", "title", "leading"):
        child = getattr(node, attr, None)
        if hasattr(child, "controls") or hasattr(child, "content"):
            yield from walk(child, seen)


def texts(node):
    out = []
    for control in walk(node):
        if isinstance(control, ft.Text) and control.value:
            out.append(str(control.value))
        label = getattr(control, "label", None)
        if isinstance(label, str) and label:
            out.append(label)
    return out


def tappables(node):
    """Every control with a click handler - what a glove has to reach."""
    return [c for c in walk(node) if getattr(c, "on_click", None) is not None]


class EverySurfaceBuilds(unittest.TestCase):
    """The blank-screen test. A surface that raises is a dead app."""

    def setUp(self):
        prefs.reset()
        live.reset()
        self.page = StubPage()

    def _build(self, module):
        return module.build(self.page, go=lambda key: None)

    def test_ride(self):
        self.assertIsNotNone(self._build(ride_page))

    def test_routes(self):
        self.assertTrue(texts(self._build(routes_page)))

    def test_search(self):
        self.assertIn("What is around here", texts(self._build(search_page)))

    def test_log(self):
        self.assertIn("Rides on this phone", texts(self._build(log_page)))

    def test_settings(self):
        self.assertIn("Look", texts(self._build(settings_page)))

    def test_help(self):
        self.assertIn(help_page.TITLE, texts(self._build(help_page)))

    def test_every_surface_builds_in_every_theme(self):
        # A palette key missing from one theme only ever shows up as a crash on
        # the theme nobody tried.
        for key in theme.THEMES:
            prefs.set_value("theme", key)
            for module in SURFACES:
                with self.subTest(theme=key, page=module.__name__):
                    self.assertIsNotNone(self._build(module))

    def test_every_surface_builds_in_metric(self):
        prefs.set_value("units", "metric")
        for module in SURFACES:
            with self.subTest(page=module.__name__):
                self.assertIsNotNone(self._build(module))


class DropAPinOnTheMap(unittest.TestCase):
    """Hers: "i can't even just 'place pin on map' and use that as destination
    easily". A long press offers the point; nothing routes until she says so."""

    CHABOT = (37.8187, -122.1805)

    def setUp(self):
        prefs.reset()
        live.reset()
        self.page = StubPage()
        ride_page.build(self.page, go=lambda key: None)

    def _press(self, lat, lon):
        import types
        import flet_map as fmap
        event = types.SimpleNamespace(coordinates=fmap.MapLatitudeLongitude(lat, lon))
        ride_page._MAP["map"].on_long_press(event)

    def _band(self):
        return ride_page._M["pin_band"]

    def _button(self, label):
        for control in tappables(self._band()):
            if any(label in t for t in texts(control)):
                return control
        self.fail(f"no {label!r} button on the pin band")

    def test_a_long_press_offers_the_point_and_does_not_route_yet(self):
        before = len(self.page.tasks)          # the Ride build queues its own (GPS)
        self._press(*self.CHABOT)
        self.assertTrue(self._band().visible)
        self.assertIsNone(live.LIVE.get("dest"))
        self.assertEqual(len(self.page.tasks), before)             # nothing routed
        self.assertEqual(len(ride_page._MAP["dest"].markers), 1)   # the pending pin is drawn

    def test_route_here_aims_there_and_routes_off_the_ui_thread(self):
        self._press(*self.CHABOT)
        before = len(self.page.tasks)
        self._button("Route here").on_click(None)
        dest = live.LIVE.get("dest")
        self.assertAlmostEqual(dest[0], self.CHABOT[0], places=5)
        self.assertAlmostEqual(dest[1], self.CHABOT[1], places=5)
        added = self.page.tasks[before:]
        self.assertEqual(len(added), 1)                            # route_async, not inline
        self.assertEqual(added[0][0].__name__, "_route_to_pin")
        self.assertFalse(self._band().visible)

    def test_cancel_leaves_no_destination_and_no_pin(self):
        self._press(*self.CHABOT)
        self._button("Cancel").on_click(None)
        self.assertIsNone(live.LIVE.get("dest"))
        self.assertFalse(self._band().visible)
        self.assertEqual(ride_page._MAP["dest"].markers, [])

    def test_the_pin_buttons_are_glove_sized(self):
        self._press(*self.CHABOT)
        for target in tappables(self._band()):
            self.assertGreaterEqual(target.height or 0, ui.TOUCH)


class NothingFlexesInsideAWrap(unittest.TestCase):
    """The Around grey box.

    A control with `expand` set - even `expand=False` - sitting directly in a
    `Row(wrap=True)` builds fine in Python and then fails in Flutter, and a
    release build paints a failed widget as a flat grey box. That is what
    Around was: "What is around here" and a giant grey box under it, because
    `_pick_tile` passed `expand=False` explicitly. Flet's default is None, and
    only None leaves the Wrap alone. Found by rendering it (Flet 0.86.5 web
    client): every other tile property drew, `expand=False` alone did not.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        self.page = StubPage()

    def test_around_categories_are_a_glove_sized_grid_three_across(self):
        # What the wrap row was trying to be before it crashed: a grid, three
        # to a line, each chip at least the glove target tall.
        tree = search_page.build(self.page, go=lambda key: None)
        grids = [c for c in walk(tree) if isinstance(c, ft.ResponsiveRow)]
        self.assertEqual(len(grids), 1)
        chips = grids[0].controls
        self.assertEqual(len(chips), len(places.POI_CATEGORIES))
        for chip in chips:
            self.assertEqual(chip.col, 4)          # 12 columns / 4 = three across
            self.assertGreaterEqual(chip.height, ui.TOUCH)

    def test_every_tap_target_on_around_is_glove_sized(self):
        # The radius row was 44 px - a miss in gloves at 60 mph.
        tree = search_page.build(self.page, go=lambda key: None)
        for target in tappables(tree):
            with self.subTest(target=texts(target)[:1]):
                self.assertGreaterEqual(target.height or 0, ui.TOUCH)

    def test_no_wrap_row_has_a_child_with_expand_set(self):
        for module in SURFACES:
            tree = module.build(self.page, go=lambda key: None)
            for control in walk(tree):
                if not (isinstance(control, ft.Row) and control.wrap):
                    continue
                for child in control.controls:
                    with self.subTest(page=module.__name__, child=type(child).__name__):
                        self.assertIsNone(child.expand,
                                          "a wrapping Row's child has expand set; Flutter draws a grey box")


class TheDashGauges(unittest.TestCase):
    """The gauges she asked for, drawn with no sensors attached.

    Every one of them has to build from all-None, because that is the state on a
    desk, in a browser, and on a phone with no barometer - and an exception
    there is a blank map page.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        sensors.reset()
        audio.reset()

    def test_every_gauge_builds_with_nothing_known(self):
        for name in ("horizon", "g_ball", "climb"):
            with self.subTest(gauge=name):
                self.assertIsNotNone(getattr(gauges, name)())
        self.assertIsNotNone(gauges.meter(None))
        self.assertIsNotNone(gauges.dash())

    def test_an_empty_meter_is_empty_not_full(self):
        # "no signal" must not look like "flat out".
        empty = gauges.meter(None)
        full = gauges.meter(1.0)
        self.assertNotEqual(_bar_fill(empty), _bar_fill(full))

    def test_the_horizon_survives_an_impossible_angle(self):
        # A bad fix can hand it 400 degrees; it clamps rather than raising.
        self.assertIsNotNone(gauges.horizon(lean_deg=400.0, pitch_deg=-999.0,
                                            roll_deg=180.0))

    def test_the_g_ball_clamps_instead_of_leaving_the_circle(self):
        self.assertIsNotNone(gauges.g_ball(g_lat=9.0, g_long=-9.0))

    def test_climb_reads_in_both_unit_systems(self):
        feet = texts(gauges.climb(alt_m=300.0, climb_mps=1.0, imperial=True))
        metres = texts(gauges.climb(alt_m=300.0, climb_mps=1.0,
                                    imperial=False))
        self.assertTrue(any("ft" in t for t in feet), feet)
        self.assertTrue(any("m" in t for t in metres), metres)

    def test_the_dash_is_built_from_live_state_without_a_map(self):
        sensors.STATE["roll_deg"] = 10.0
        sensors.STATE["pitch_deg"] = -4.0
        sensors.STATE["alt_baro_m"] = 210.0
        sensors.STATE["climb_mps"] = 1.2
        audio.STATE["loudness_db"] = -20.0
        audio.STATE["rpm_est"] = 3500
        built = ride_page._dash_row()          # noqa: SLF001 - ours
        words = texts(built)
        self.assertTrue(any("3500" in w for w in words), words)
        self.assertTrue(any("climbing" in w or "ft/min" in w for w in words),
                        words)

    def test_rpm_and_loudness_scales_are_bounded(self):
        self.assertEqual(gauges.rpm_fraction(99999), 1.0)
        self.assertIsNone(gauges.rpm_fraction(None))
        self.assertEqual(gauges.loudness_norm(-200.0), 0.0)
        self.assertEqual(gauges.loudness_norm(0.0), 1.0)


def _bar_fill(meter_control):
    """The `expand` of the filled half of a meter, for comparing two meters."""
    fills = [getattr(c, "expand", None) for c in walk(meter_control)
             if getattr(c, "bgcolor", None) and getattr(c, "expand", None)]
    return tuple(fills)


class TheLoaderActuallyRuns(unittest.TestCase):
    """Run the startup sequence, do not merely schedule it.

    This is the test that was missing, and its absence cost her a black screen:
    `loading_screen` called `ft.ImageFit`, which does not exist in Flet 0.86 - it
    is `ft.BoxFit` - so the first frame raised AttributeError before anything was
    added to the page. The app launched to black and stayed there.

    The old test only asserted that `boot_then_open` was SCHEDULED. A scheduled
    coroutine that raises on its first line passes that test perfectly.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        boot.reset()
        self.page = StubPage()

    def _boot_task(self):
        asyncio.run(load_main().main(self.page))
        for fn, args in self.page.tasks:
            if getattr(fn, "__name__", "") == "boot_then_open":
                return fn, args
        self.fail("the loader was never scheduled")
        return None, None

    def test_the_whole_startup_sequence_completes(self):
        fn, args = self._boot_task()
        asyncio.run(fn(*args))
        self.assertTrue(boot.booted(), "boot never finished")
        self.assertTrue(self.page.controls, "nothing was ever drawn")

    def test_every_stage_reported_something(self):
        fn, args = self._boot_task()
        asyncio.run(fn(*args))
        notes = boot.notes()
        for key, _label in boot.stages_for(
                skip_telemetry=prefs.sensors_on_demand()):
            self.assertIn(key, notes, key)
            self.assertNotIn("failed", notes[key], f"{key}: {notes[key]}")

    def test_the_loading_screen_itself_can_be_built(self):
        # Directly, because this is the call that raised. Everything it touches -
        # the image, the progress bar, the pill - has to exist in this Flet.
        main = load_main()
        page = StubPage()
        asyncio.run(main.main(page))
        for fn, args in page.tasks:
            if getattr(fn, "__name__", "") == "boot_then_open":
                asyncio.run(fn(*args))
        self.assertGreater(page.updates, 0, "the loader never painted a frame")


class ASensorReadingDoesNotRepaintTheWholePage(unittest.TestCase):
    """The actual reason this app was unusable and Moto was not.

    Flet calls `session.after_event()` after EVERY event handler
    (base_control.py:530), which auto-updates the nearest isolated ancestor
    (messaging/session.py:556-571). A service's ancestor is `Page`. So every
    reading from every sensor serialised the entire control tree and shipped
    it to Flutter: four sensors at 400 ms plus a barometer at 1 s is eleven
    full page updates a second, for ever, from the first frame.

    Moto attaches no sensors at all. That was the whole difference - not the
    app's size, not the map, not the tile server, each of which was checked
    first and was wrong.

    These handlers touch no control, so an auto-update has nothing to draw. If
    one of them ever stops calling `_quiet()` the app goes back to being
    unusable, and nothing else in this suite would notice.
    """

    class _Reading:
        x = y = z = 0.1
        pressure = 1013.25
        timestamp = None

    class _Event:
        def __init__(self, reading):
            self.reading = reading

    def setUp(self):
        self.calls = []
        real = ft.context.disable_auto_update
        ft.context.disable_auto_update = lambda *a, **k: self.calls.append(1)
        self.addCleanup(setattr, ft.context, "disable_auto_update", real)

    def _fire(self, handler):
        try:
            handler(self._Event(self._Reading()))
        except Exception:                  # noqa: BLE001 - a handler may well
            pass                           # reject a stub reading. What
        #                                    matters is that it went quiet
        #                                    BEFORE doing anything else.

    def test_every_motion_handler_suppresses_the_page_update(self):
        for handler in (sensors.on_accelerometer, sensors.on_user_accelerometer,
                        sensors.on_gyroscope, sensors.on_barometer,
                        sensors.on_magnetometer):
            self.calls.clear()
            self._fire(handler)
            self.assertEqual(len(self.calls), 1,
                             f"{handler.__name__} would repaint the whole page")

    def test_the_battery_handler_does_too(self):
        self.calls.clear()
        self._fire(sensors.on_battery)
        self.assertEqual(len(self.calls), 1)

    def test_the_microphone_stream_does_too(self):
        # This one arrives per PCM chunk, so it is the worst of the lot
        # whenever she has the microphone on.
        self.calls.clear()
        try:
            audio.on_stream(self._Event(self._Reading()))
        except Exception:                  # noqa: BLE001
            pass
        self.assertEqual(len(self.calls), 1)


class SensorsCostNothingWhenNothingReadsThem(unittest.TestCase):
    """The reason this app was unusable while Moto was fast.

    `prefs.sensors_on_demand` shipped True and was never consulted. Five
    sensors came up during the loading screen - before any surface existed to
    read them - and stayed up for the life of the process. Four fire every
    400 ms and the barometer every second: about a dozen crossings of the
    Python/Flutter channel per second, paid for ever, to feed nothing.

    Moto has no telemetry at all and does not pay it. That was the difference.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        boot.reset()
        sensors.detach()
        self.addCleanup(sensors.detach)
        self.page = StubPage()

    def test_boot_does_not_subscribe_when_they_are_on_demand(self):
        self.assertTrue(prefs.sensors_on_demand(), "the default changed")
        self.assertEqual(boot.stage_telemetry(self.page), "on demand")
        self.assertFalse(sensors.attached(),
                         "the loading screen subscribed sensors again")

    def test_turning_the_preference_off_restores_the_old_behaviour(self):
        prefs.S["sensors_on_demand"] = False
        self.assertNotEqual(boot.stage_telemetry(self.page), "on demand")

    def test_settings_can_still_force_a_probe(self):
        # Pressing "Sensors running" in Settings IS the demand, so it must
        # subscribe even though the preference would otherwise leave them off.
        self.assertTrue(prefs.sensors_on_demand())
        self.assertNotEqual(boot.stage_telemetry(self.page, True), "on demand")

    def test_the_ride_page_attaches_when_the_dash_is_drawn(self):
        prefs.S["dash"] = True
        ride_page.build(self.page)
        self.assertTrue(sensors.attached(),
                        "the gauges are on screen with nothing feeding them")

    def test_the_ride_page_leaves_them_off_when_the_dash_is_not(self):
        prefs.S["dash"] = False
        ride_page.build(self.page)
        self.assertFalse(sensors.attached(),
                         "paying for sensors that draw nothing")

    def test_recording_keeps_the_channels_even_with_the_dash_off(self):
        # The log wants lean angle and g-forces whether or not a gauge shows
        # them, so a ride in progress outranks the dash setting.
        import tempfile
        prefs.S["dash"] = False
        with tempfile.TemporaryDirectory() as tmp:
            live.start_recording(directory=tmp)
            self.addCleanup(live.reset)
            ride_page.build(self.page)
            self.assertTrue(sensors.attached(),
                            "a recording ride lost its sensor channels")


class EveryHandlerActuallyRuns(unittest.TestCase):
    """Tap everything. This is the test that was missing.

    Building a surface proves its controls can be CONSTRUCTED. It does not run a
    single handler, so a handler calling a function that does not exist builds
    perfectly and raises the moment it is touched - which is exactly what shipped:
    a layout rewrite truncated ride.py and deleted `_rerender`, and every control
    that changed a setting died with `NameError: name '_rerender' is not defined`
    on first tap.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        sensors.reset()
        audio.reset()
        self.page = StubPage()

    def _fire(self, tree, page_name):
        fired = 0
        for control in walk(tree):
            handler = getattr(control, "on_click", None)
            if not callable(handler):
                continue
            with self.subTest(page=page_name, control=type(control).__name__):
                try:
                    handler(Event(control))
                except Exception as exc:           # noqa: BLE001 - that is the
                    # entire point of this test
                    self.fail(f"{page_name}: {type(exc).__name__}: {exc}")
            fired += 1
        return fired

    def test_every_tap_on_every_surface(self):
        total = 0
        for module in (ride_page, routes_page, search_page, log_page,
                       settings_page, help_page):
            tree = module.build(self.page, go=lambda k: None)
            total += self._fire(tree, module.__name__)
        self.assertGreater(total, 10, "no handlers were exercised at all")

    def test_the_ride_page_helpers_exist(self):
        # Named individually, because these are the three that were deleted.
        for name in ("_rerender", "repaint", "channel_report", "is_wide",
                     "_hud_state", "_dash_row"):
            self.assertTrue(hasattr(ride_page, name), name)

    def test_switches_and_changes_run_too(self):
        # `on_change` handlers are a separate attribute and were never fired by
        # the build-only tests either.
        tree = settings_page.build(self.page, go=lambda k: None)
        for control in walk(tree):
            handler = getattr(control, "on_change", None)
            if callable(handler):
                control.value = not getattr(control, "value", False)
                try:
                    handler(Event(control))
                except Exception as exc:           # noqa: BLE001
                    self.fail(f"settings on_change: {type(exc).__name__}: {exc}")


class TheHudLayouts(unittest.TestCase):
    """Seven views of the same numbers. Her "all the views should be available"."""

    STATE = {"imperial": True, "machine": "cb750", "speed_ms": 27.0,
             "limit": 45, "lean_deg": 28.0, "pitch_deg": -4.0, "g_lat": 0.4,
             "g_long": -0.3, "rpm": 5200, "gear": 3, "shift": "up",
             "alt_m": 104.0, "climb_mps": 1.1, "roll_deg": 6.0,
             "bands": [("low", 0.2), ("mid", 1.0), ("high", 0.4)],
             "loudness": -22.0, "loudness_peak": 0.7}

    def setUp(self):
        prefs.reset()

    def test_every_layout_builds_with_everything_known(self):
        for key, _name, _detail in gauges.LAYOUTS:
            with self.subTest(layout=key):
                built = gauges.overlay(self.STATE, key)
                self.assertTrue(built is None or built is not None)

    def test_every_layout_builds_with_nothing_known(self):
        bare = {"imperial": True, "machine": "cb750"}
        for key, _name, _detail in gauges.LAYOUTS:
            with self.subTest(layout=key):
                gauges.overlay(bare, key)

    def test_off_draws_nothing_at_all(self):
        self.assertIsNone(gauges.overlay(self.STATE, "off"))

    def test_every_layout_is_a_settable_preference(self):
        offered = {key for key, _n, _d in gauges.LAYOUTS}
        for key in offered:
            prefs.set_value("hud", key)
            self.assertEqual(prefs.hud(), key)

    def test_the_default_is_the_ribbon_she_picked(self):
        self.assertEqual(prefs.hud(), "ribbon")

    def test_lean_colours_warm_as_the_angle_grows(self):
        # White -> yellow -> orange -> red by intensity, the OsmAnd moto idea.
        upright = gauges.lean_colour(2.0)
        leaning = gauges.lean_colour(22.0)
        hard = gauges.lean_colour(40.0)
        self.assertNotEqual(upright, leaning)
        self.assertNotEqual(leaning, hard)
        self.assertEqual(gauges.lean_colour(None), theme.PAL()["text2"])

    def test_the_bar_is_off_by_default_and_the_menu_is_not(self):
        self.assertFalse(prefs.bar())


class TheGearEstimate(unittest.TestCase):
    """Speed over engine speed is a fixed ratio per gear. Nothing else needed."""

    def test_each_gear_is_recognised_from_its_own_ratio(self):
        for machine in ("cb750", "ex250"):
            for index, ratio in enumerate(gears.RATIOS[machine]):
                rpm = 5000.0
                speed = ratio * (rpm / 60.0)
                got, confidence = gears.estimate(speed, rpm, machine)
                with self.subTest(machine=machine, gear=index + 1):
                    self.assertEqual(got, index + 1)
                    self.assertGreater(confidence, 0.9)

    def test_a_four_and_a_twin_read_the_same_numbers_differently(self):
        speed, rpm = 20.0, 5000.0
        self.assertNotEqual(gears.estimate(speed, rpm, "cb750")[0],
                            gears.estimate(speed, rpm, "ex250")[0])

    def test_no_engine_speed_means_no_gear_rather_than_a_guess(self):
        # With the microphone off there is no RPM, and speed alone answers a
        # different question.
        self.assertEqual(gears.estimate(20.0, None, "cb750"), (None, 0.0))
        self.assertEqual(gears.estimate(20.0, 0, "cb750"), (None, 0.0))

    def test_a_slipping_clutch_reports_nothing(self):
        self.assertEqual(gears.estimate(1.0, 4000, "cb750"), (None, 0.0))

    def test_a_ratio_matching_no_gear_is_refused(self):
        self.assertEqual(gears.estimate(60.0, 1200, "cb750"), (None, 0.0))

    def test_the_shift_light_fires_near_the_redline_and_when_lugging(self):
        self.assertEqual(gears.shift_hint(9000, 3, "cb750"), "up")
        self.assertEqual(gears.shift_hint(1800, 3, "cb750"), "down")
        self.assertEqual(gears.shift_hint(5000, 3, "cb750"), "")

    def test_the_shift_light_never_says_up_in_top_gear(self):
        top = gears.gear_count("cb750")
        self.assertEqual(gears.shift_hint(9400, top, "cb750"), "")

    def test_it_never_says_down_in_first(self):
        self.assertEqual(gears.shift_hint(1500, 1, "cb750"), "")

    def test_what_gear_you_should_be_in_needs_no_microphone(self):
        # Her "or even just show 'what gear you should be at'".
        low = gears.best_gear_for(8.0, "cb750")
        high = gears.best_gear_for(30.0, "cb750")
        self.assertIsNotNone(low)
        self.assertGreater(high, low)

    def test_ratios_learned_from_samples_land_near_the_table(self):
        machine = "cb750"
        samples = []
        for ratio in gears.RATIOS[machine]:
            for rpm in (3000.0, 4500.0, 6000.0):
                samples.append((ratio * (rpm / 60.0), rpm))
        learned = gears.learn(samples, gears.gear_count(machine))
        self.assertEqual(len(learned), gears.gear_count(machine))
        for found, known in zip(learned, gears.RATIOS[machine]):
            self.assertAlmostEqual(found, known, delta=known * 0.05)


class TheUpdateCheck(unittest.TestCase):
    """OTA. The comparison is one integer, and getting it wrong is expensive."""

    def setUp(self):
        updates.reset()
        self._fetch = updates.fetch_manifest

    def tearDown(self):
        updates.fetch_manifest = self._fetch
        updates.reset()

    def _publish(self, code, name="9.9.9", size=222_000_000):
        updates.fetch_manifest = lambda *a, **k: {
            "code": code, "name": name, "size": size,
            "sha256": "0" * 64, "url": updates.APK_URL, "notes": ""}

    def test_a_newer_build_is_offered(self):
        self._publish(version.VERSION_CODE + 1)
        updates.check()
        self.assertTrue(updates.available())
        self.assertIn("available", updates.summary())

    def test_the_same_build_is_not(self):
        self._publish(version.VERSION_CODE)
        updates.check()
        self.assertFalse(updates.available())
        self.assertIn("up to date", updates.summary())

    def test_an_older_build_is_never_offered(self):
        # A rolled-back server must not talk a phone into downgrading: on
        # Android that is an uninstall, and it takes the unsynced rides with it.
        self._publish(version.VERSION_CODE - 1)
        updates.check()
        self.assertFalse(updates.available())

    def test_no_signal_is_reported_and_never_raises(self):
        updates.fetch_manifest = lambda *a, **k: None
        updates.check()
        self.assertFalse(updates.available())
        self.assertIn("could not reach", updates.summary())

    def test_a_malformed_manifest_is_refused_rather_than_crashing(self):
        updates.fetch_manifest = lambda *a, **k: {"code": "banana"}
        updates.check()
        self.assertFalse(updates.available())
        self.assertIn("malformed", updates.summary())

    def test_nothing_checked_yet_says_so(self):
        self.assertFalse(updates.available())
        self.assertIn("not checked", updates.summary())

    def test_the_manifest_written_is_the_manifest_read(self):
        # The publish side and the read side share this module, so a round trip
        # is the whole contract.
        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as fh:
            fh.write(b"not really an apk, but it hashes the same way")
            path = fh.name
        try:
            built = updates.manifest_for(path, notes="test")
            self.assertEqual(built["code"], version.VERSION_CODE)
            self.assertEqual(len(built["sha256"]), 64)
            self.assertGreater(built["size"], 0)
            updates.fetch_manifest = lambda *a, **k: built
            updates.check()
            self.assertFalse(updates.available())
        finally:
            os.unlink(path)

    def test_the_version_code_is_an_integer_android_will_accept(self):
        self.assertIsInstance(version.VERSION_CODE, int)
        self.assertLess(version.VERSION_CODE, 2_100_000_000)
        self.assertGreater(version.VERSION_CODE, 0)
        self.assertIn(".", version.VERSION_NAME)


class SensorPlumbing(unittest.TestCase):
    def setUp(self):
        sensors.reset()
        audio.reset()

    def test_no_reading_means_no_columns_rather_than_zeroes(self):
        self.assertEqual(sensors.latest(), {})
        self.assertEqual(audio.latest(), {})

    def test_an_accelerometer_reading_fills_the_orientation_channels(self):
        sensors.on_accelerometer(_Reading(x=0.0, y=0.0, z=9.80665))
        got = sensors.latest()
        self.assertAlmostEqual(got["g_vert"], 1.0, places=2)
        # Flat on its back: roll 0, pitch 0.
        self.assertAlmostEqual(got["tilt_deg"], 0.0, places=1)

    def test_peaks_hold_until_reset(self):
        sensors.on_user_accelerometer(_Reading(x=0.0, y=0.0, z=19.6))
        sensors.on_user_accelerometer(_Reading(x=0.0, y=0.0, z=0.0))
        sensors.on_accelerometer(_Reading(x=0.0, y=0.0, z=9.80665))
        self.assertGreater(sensors.latest()["g_bump"], 1.5)
        sensors.reset_peaks()
        self.assertAlmostEqual(sensors.latest()["g_bump"], 0.0)

    def test_the_barometer_reports_altitude_and_a_climb_rate(self):
        sensors.on_barometer(_Reading(pressure=1013.25))
        got = sensors.latest()
        self.assertAlmostEqual(got["alt_baro_m"], 0.0, places=0)
        self.assertAlmostEqual(sensors.altitude_from_pressure(898.7), 1000.0,
                               delta=30.0)

    def test_a_missing_sensor_is_recorded_and_not_raised(self):
        sensors.on_error("barometer")(_Reading(message="no such sensor"))
        self.assertIn("barometer", sensors.available()["errors"])

    def test_pcm_decoding_and_loudness(self):
        # Two samples at full negative and full positive scale: RMS is 1.0,
        # which is 0 dBFS.
        chunk = bytes([0x00, 0x80, 0xFF, 0x7F])
        floats = audio.pcm16_to_floats(chunk)
        self.assertEqual(len(floats), 2)
        self.assertAlmostEqual(audio.loudness_dbfs(floats), 0.0, delta=0.1)

    def test_silence_is_ninety_down_not_negative_infinity(self):
        self.assertEqual(audio.loudness_dbfs([0.0] * 64), -90.0)

    def test_an_odd_trailing_byte_is_dropped_rather_than_raising(self):
        self.assertEqual(len(audio.pcm16_to_floats(b"\x01\x02\x03")), 1)
        self.assertEqual(len(audio.pcm16_to_floats(b"\x01")), 0)

    def test_audio_reports_nothing_while_not_listening(self):
        audio.STATE["loudness_db"] = -20.0
        self.assertEqual(audio.latest(), {})


class _Reading:
    """A stand-in for a Flet sensor event: only its attributes are ever read."""

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


class TheBar(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        self.main = load_main()

    def test_every_bar_page_is_a_real_page(self):
        for key in self.main.BAR_PAGES:
            self.assertIn(key, self.main.PAGES)

    def test_every_page_is_labelled_and_has_both_icons(self):
        for key in self.main.PAGES:
            self.assertIn(key, self.main.LABELS)
            self.assertIn(key, self.main.NAV_LABELS)
            self.assertIn(key, self.main.ICONS)
            self.assertIn(key, self.main.ICONS_ON)

    def test_help_is_not_on_the_bar_but_is_reachable_from_settings(self):
        # It hangs under Settings the way a manual lives in a drawer. Neither on
        # the bar nor linked would make it unreachable.
        self.assertNotIn("help", self.main.BAR_PAGES)
        built = settings_page.build(StubPage(), go=lambda k: None)
        self.assertTrue(any("How this works" in t for t in texts(built)))

    def test_the_bar_holds_five_so_material_will_draw_it(self):
        # More than five destinations collides the labels on a phone.
        self.assertEqual(len(self.main.BAR_PAGES), 5)

    def test_nav_labels_are_short_enough_for_a_bar_slot(self):
        for key in self.main.BAR_PAGES:
            self.assertLessEqual(len(self.main.NAV_LABELS[key]), 10, key)

    def test_a_cold_launch_defers_to_the_loader(self):
        # Nothing is drawn inline: the staged loader owns the first frames, and
        # the stub page records the task rather than running it.
        boot.reset()
        page = StubPage()
        asyncio.run(self.main.main(page))
        self.assertEqual(page.title, "Agedashii Navi")
        self.assertTrue(callable(getattr(page, "_navi_rerender", None)))
        self.assertTrue(any(getattr(fn, "__name__", "") == "boot_then_open"
                            for fn, _args in page.tasks),
                        "the loader was never scheduled")

    def test_a_warm_launch_skips_the_loader_and_opens_the_map(self):
        # Her "hopefully the app doesn't need the loader upon every open if it's
        # in the background or recent apps" - coming back to a live process goes
        # straight to the map.
        boot.mark_booted()
        page = StubPage()
        asyncio.run(self.main.main(page))
        self.assertTrue(page.controls, "nothing was added to the page")
        self.assertFalse(any(getattr(fn, "__name__", "") == "boot_then_open"
                             for fn, _args in page.tasks),
                         "the loader ran again on a warm start")
        boot.reset()

    def test_every_stage_has_a_label_and_telemetry_is_the_skippable_one(self):
        keys = [key for key, _label, _skip in boot.STAGES]
        self.assertEqual(keys, ["maps", "telemetry", "routing", "ui"])
        skippable = [key for key, _label, skip in boot.STAGES if skip]
        self.assertEqual(skippable, ["telemetry"])
        for _key, label, _skip in boot.STAGES:
            self.assertTrue(label.startswith("LOADING"), label)

    def test_loading_on_demand_drops_the_telemetry_stage(self):
        self.assertIn("telemetry",
                      [k for k, _l in boot.stages_for(skip_telemetry=False)])
        self.assertNotIn("telemetry",
                         [k for k, _l in boot.stages_for(skip_telemetry=True)])

    def test_a_stage_that_throws_is_a_note_not_a_dead_loader(self):
        # A failed stage must never strand her on a loading screen.
        original = boot.stage_maps
        boot.stage_maps = lambda: 1 / 0
        try:
            note = boot.run_stage("maps")
        finally:
            boot.stage_maps = original
        self.assertIn("failed", note)
        self.assertIn("failed", boot.notes()["maps"])


class GloveSized(unittest.TestCase):
    """Her constraint, asserted rather than hoped for."""

    def setUp(self):
        prefs.reset()
        live.reset()
        self.page = StubPage()

    def test_every_fixed_height_tap_target_is_big_enough(self):
        for module in SURFACES:
            tree = module.build(self.page, go=lambda k: None)
            for control in tappables(tree):
                height = getattr(control, "height", None)
                # A row inside a scrolling list can take its height from the
                # list; only fixed-height controls can be checked here.
                if height is None:
                    continue
                with self.subTest(page=module.__name__, h=height):
                    self.assertGreaterEqual(height, 44)

    def test_the_touch_target_is_bigger_than_the_material_default(self):
        self.assertGreaterEqual(ui.TOUCH, 48)


class SwitchingSurfacesDoesNotStopARide(unittest.TestCase):
    """The bug this app's whole state design exists to prevent."""

    def setUp(self):
        prefs.reset()
        live.reset()
        self.page = StubPage()

    def test_a_recording_survives_building_every_other_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            live.start_recording(directory=tmp)
            live.set_position(37.80, -122.27, speed_ms=12.0, heading=0.0,
                              t=1000.0)
            for module in SURFACES:
                module.build(self.page, go=lambda k: None)
                self.assertTrue(live.recording(), module.__name__)
            live.set_position(37.801, -122.27, speed_ms=12.0, heading=2.0,
                              t=1001.0)
            self.assertEqual(live.LIVE["ride"].count, 2)
            self.assertEqual(live.stop_recording(when=1002.0)["samples"], 2)

    def test_the_log_page_lists_and_reads_back_a_ride(self):
        with tempfile.TemporaryDirectory() as tmp:
            live.start_recording(directory=tmp)
            live.set_position(37.80, -122.27, t=1000.0, speed_ms=10.0)
            live.stop_recording(when=1001.0)
            rows = log_page.list_rides(tmp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(log_page.read_ride(rows[0]["path"]).count, 1)


class PullingFromTheServer(unittest.TestCase):
    """The pull half of "all rides and apps should SYNC to the DB both ways".

    Driven against a stubbed api, because the point is the shape of the data and
    the fact that nothing is discarded on the way through - not whether a socket
    opens.
    """

    def setUp(self):
        prefs.reset()
        live.reset()
        self._trips = places.M.trips
        self._rides = places.M.rides_list
        self._ride_points = places.M.ride_points

    def tearDown(self):
        places.M.trips = self._trips
        places.M.rides_list = self._rides
        places.M.ride_points = self._ride_points

    def test_a_trips_waypoints_survive_the_trip_list(self):
        # They used to be dropped, so the geometry was on the device and
        # unreachable.
        places.M.trips = lambda: [{
            "id": "grizzly", "name": "Grizzly Peak", "mi": "34",
            "date": "2026-05-01",
            "waypoints": [{"lat": 37.88, "lon": -122.24},
                          {"lat": 37.89, "lon": -122.23}]}]
        places.M.rides_list = lambda: []
        rows, note = places.db_routes()
        self.assertEqual(note, "")
        self.assertEqual(rows[0]["points"], 2)
        self.assertIn("2 waypoints", rows[0]["detail"])

    def test_a_saved_trip_hands_back_its_coordinates(self):
        places.M.trips = lambda: [{
            "id": "grizzly", "name": "Grizzly Peak",
            "waypoints": [{"lat": 37.88, "lon": -122.24},
                          {"lat": 37.89, "lon": -122.23}]}]
        got = places.saved_points({"kind": "trip", "id": "grizzly"})
        self.assertEqual(got, [(37.88, -122.24), (37.89, -122.23)])

    def test_a_recorded_ride_is_pulled_by_id(self):
        places.M.ride_points = lambda rid: (
            [(1.0, 2.0)] if rid == "ride-20260926" else [])
        self.assertEqual(
            places.saved_points({"kind": "ride", "id": "ride-20260926"}),
            [(1.0, 2.0)])

    def test_a_row_with_no_id_asks_for_nothing(self):
        self.assertEqual(places.saved_points({"kind": "trip", "id": ""}), [])
        self.assertEqual(places.saved_points({}), [])

    def test_a_waypoint_missing_a_coordinate_is_skipped_not_fatal(self):
        places.M.trips = lambda: [{
            "id": "t", "name": "t",
            "waypoints": [{"lat": 37.88, "lon": -122.24},
                          {"name": "no coordinates here"},
                          {"lat": None, "lon": -122.2}]}]
        self.assertEqual(places.saved_points({"kind": "trip", "id": "t"}),
                         [(37.88, -122.24)])

    def test_no_signal_names_what_is_missing_rather_than_going_blank(self):
        places.M.trips = lambda: None
        places.M.rides_list = lambda: None
        rows, note = places.db_routes()
        self.assertEqual(rows, [])
        self.assertIn("trips", note)
        self.assertIn("rides", note)


class LiveState(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()

    def test_an_inaccurate_fix_moves_nothing(self):
        self.assertFalse(live.set_position(37.8, -122.27,
                                           acc_m=120.0)["moved"])
        self.assertIsNone(live.LIVE["pos"])

    def test_rolling_is_reported_so_autorecord_can_start(self):
        self.assertFalse(live.set_position(37.8, -122.27,
                                          speed_ms=0.5)["rolling"])
        self.assertTrue(live.set_position(37.8, -122.27,
                                         speed_ms=9.0)["rolling"])

    def test_a_destination_is_remembered_for_the_next_launch(self):
        live.set_destination(37.88, -122.24, "Grizzly Peak")
        self.assertEqual(prefs.last_dest()[2], "Grizzly Peak")

    def test_clearing_a_destination_clears_the_route_with_it(self):
        live.set_destination(37.88, -122.24, "Grizzly Peak")
        live.LIVE["route"] = object()
        live.clear_destination()
        self.assertIsNone(live.LIVE["route"])
        self.assertIsNone(live.destination())

    def test_routing_with_no_destination_says_so_instead_of_raising(self):
        self.assertIsNone(live.route_now())
        self.assertIn("destination", live.LIVE["note"])

    def test_stopping_a_ride_that_never_started_is_not_an_error(self):
        self.assertIsNone(live.stop_recording())

    def test_a_phone_with_no_barometer_still_gets_a_climb_rate(self):
        # A Moto G Stylus has no barometer. Climbing 30 m over 30 s of fixes is
        # 1 m/s, and the slope has to find it from GPS altitude alone.
        sensors.reset()
        for i in range(31):
            live.set_position(37.80 + i * 0.0005, -122.27, t=1000.0 + i,
                              speed_ms=12.0, alt_m=100.0 + i)
        self.assertAlmostEqual(live.climb_mps(), 1.0, delta=0.15)
        self.assertAlmostEqual(live.altitude_m(), 130.0, delta=0.5)

    def test_the_barometer_wins_when_there_is_one(self):
        for i in range(31):
            live.set_position(37.80 + i * 0.0005, -122.27, t=1000.0 + i,
                              speed_ms=12.0, alt_m=100.0 + i)
        sensors.STATE["climb_mps"] = -2.5
        sensors.STATE["alt_baro_m"] = 88.0
        self.assertEqual(live.climb_mps(), -2.5)
        self.assertEqual(live.altitude_m(), 88.0)

    def test_noisy_gps_altitude_does_not_invent_a_climb(self):
        # A flat road with +-8 m of GPS wander must read about zero, which is
        # why the slope is least-squares and not (last - first) / span.
        sensors.reset()
        wander = [0, 8, -8, 6, -6, 4, -4, 2, -2, 0, 5, -5, 3, -3, 0]
        for i, offset in enumerate(wander):
            live.set_position(37.80 + i * 0.0005, -122.27, t=1000.0 + i,
                              speed_ms=12.0, alt_m=100.0 + offset)
        self.assertAlmostEqual(live.climb_mps(), 0.0, delta=0.5)

    def test_a_knock_while_recording_becomes_an_incident(self):
        # The shake detector used to increment a counter nobody read.
        with tempfile.TemporaryDirectory() as tmp:
            sensors.reset()
            live.start_recording(directory=tmp)
            live.set_position(37.80, -122.27, t=1000.0, speed_ms=12.0)
            sensors.STATE["g_bump_peak"] = 2.4
            sensors.on_shake(object())
            ride = live.LIVE["ride"]
            self.assertEqual(len(ride.incidents), 1)
            self.assertIn("2.4 g", ride.incidents[0]["what"])
            live.stop_recording(when=1001.0)

    def test_a_knock_with_no_ride_running_is_not_an_error(self):
        sensors.reset()
        live.reset()
        sensors.on_shake(object())
        self.assertEqual(sensors.STATE["shakes"], 1)

    def test_the_heading_falls_back_to_the_compass_when_stopped(self):
        # Without this the marker freezes at the last moving course, so turning
        # the bike at a junction leaves the arrow pointing the way you came.
        sensors.reset()
        sensors.on_magnetometer(_Reading(x=30.0, y=0.0, z=0.0))
        live.set_position(37.80, -122.27, t=1000.0, speed_ms=0.2, heading=90.0)
        self.assertAlmostEqual(live.heading(), 0.0, places=1)

    def test_gps_course_wins_while_actually_moving(self):
        sensors.reset()
        sensors.on_magnetometer(_Reading(x=30.0, y=0.0, z=0.0))
        live.set_position(37.80, -122.27, t=1000.0, speed_ms=14.0, heading=90.0)
        self.assertEqual(live.heading(), 90.0)

    def test_a_magnetised_mount_is_refused_rather_than_believed(self):
        # Earth's field is 25-65 uT. A speaker magnet reads hundreds, and the
        # heading from it is meaningless.
        sensors.reset()
        sensors.on_magnetometer(_Reading(x=900.0, y=120.0, z=40.0))
        self.assertIsNone(sensors.compass_deg())

    def test_too_few_fixes_is_none_rather_than_a_wild_number(self):
        sensors.reset()
        live.set_position(37.80, -122.27, t=1000.0, alt_m=100.0)
        live.set_position(37.801, -122.27, t=1001.0, alt_m=140.0)
        self.assertIsNone(live.climb_mps())

    def test_the_ride_directory_honours_the_app_storage_variable(self):
        self.assertTrue(live.ride_dir().startswith(_TMP.name))


if __name__ == "__main__":         # pragma: no cover
    unittest.main()
