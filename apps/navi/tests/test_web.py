"""The app in a browser: it must get past the splash.

v1.14.0's web build never did. Six Flet services - the five motion sensors and
ScreenBrightness - raise FletUnsupportedPlatformException from before_update()
on the web or on a desktop, which is at the NEXT page update after they are
registered, not when they are built. sensors.attach() only guarded the
constructors, so the exception surfaced at the next service anyone built - the
Ride page's Geolocator - and the Ride page never finished.
"""
import sys
import unittest
from unittest import mock
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import flet as ft  # noqa: E402

from agedashii_navi import live, prefs, sensors  # noqa: E402
from tests.test_road_test import Page  # noqa: E402

#: Flet 0.86.5's own list: every service whose before_update() refuses a page
#: that is web or not mobile (flet/controls/services/*.py).
REFUSE_WEB = (ft.Accelerometer, ft.UserAccelerometer, ft.Gyroscope,
              ft.Barometer, ft.Magnetometer, ft.ScreenBrightness)


def page_on(platform, web=False):
    page = Page()
    page.platform, page.web = platform, web
    return page


class _Clean(unittest.TestCase):
    def setUp(self):
        sensors.detach()
        sensors.reset()
        prefs.reset()
        live.reset()

    def tearDown(self):
        sensors.detach()
        sensors.reset()


class TheSensorsKnowWhereTheyAre(_Clean):
    def test_a_browser_gets_none_of_the_services_that_refuse_it(self):
        page = page_on(ft.PagePlatform.LINUX, web=True)
        sensors.attach(page)
        refused = [s for s in page.services if isinstance(s, REFUSE_WEB)]
        self.assertEqual(refused, [])
        # And says so, sensor by sensor, for Settings to show.
        for name in ("accelerometer", "user_accelerometer", "gyroscope",
                     "barometer", "magnetometer", "brightness"):
            self.assertIn("not on this platform", sensors.STATE["errors"].get(name, ""),
                          name)
        # Battery works in a browser and is kept.
        self.assertTrue(any(isinstance(s, ft.Battery) for s in page.services))

    def test_a_desktop_window_is_the_same(self):
        page = page_on(ft.PagePlatform.MACOS)
        sensors.attach(page)
        self.assertEqual([s for s in page.services if isinstance(s, REFUSE_WEB)], [])

    def test_the_phone_still_gets_every_sensor(self):
        page = page_on(ft.PagePlatform.ANDROID)
        sensors.attach(page)
        made = {type(s) for s in page.services}
        for cls in REFUSE_WEB:
            self.assertIn(cls, made, cls.__name__)
        self.assertEqual(sensors.STATE["errors"], {})


class TheRidePageBuildsInABrowser(_Clean):
    def test_ride_builds_with_the_gauges_on(self):
        from agedashii_navi.pages import ride
        ride.forget_map()
        prefs.set_value("dash", True)             # the path that attaches sensors
        page = page_on(ft.PagePlatform.WINDOWS, web=True)
        built = ride.build(page)
        self.assertIsNotNone(built)
        self.assertEqual([s for s in page.services if isinstance(s, REFUSE_WEB)], [])


class TheGpsKeepsComingWithTheScreenOff(unittest.TestCase):
    """flet_geolocator 0.86.5 nests the foreground-service settings; its Dart
    side reads FLAT keys (geolocator.dart:64-82), so the service never started
    and the app went silent the moment her screen locked. Proved on
    emulator-5556 asleep: plain config 1 of 60 fixes, flat keys 58-59 of 60
    with isForeground=true."""

    def encoded(self, config):
        from flet.controls.base_control import BaseControl
        from flet.messaging.protocol import configure_encode_object_for_msgpack
        return configure_encode_object_for_msgpack(BaseControl)(config)

    def test_the_keys_the_dart_side_reads_are_sent(self):
        from agedashii_navi import location
        sent = self.encoded(location.android_config(hz=1))
        self.assertEqual(sent["foreground_notification_enable_wake_lock"], True)
        self.assertTrue(sent["foreground_notification_title"])
        self.assertTrue(sent["foreground_notification_text"])
        self.assertNotIn("foreground_notification_config", sent)   # the dropped form
        self.assertEqual(sent["distance_filter"], 3)

    def test_the_ride_page_streams_with_the_service_on(self):
        import flet_geolocator as fgeo
        from agedashii_navi.pages import ride
        prefs.reset()
        live.reset()
        ride.forget_map()
        self.addCleanup(ride.forget_map)
        # The stream is built once per process and kept; start from none.
        ride._M.pop("geo", None)                                 # noqa: SLF001
        self.addCleanup(ride._M.pop, "geo", None)                # noqa: SLF001
        live.set_destination(37.81, -122.25, "Lake Merritt")    # a route is up
        self.addCleanup(live.reset)
        page = page_on(ft.PagePlatform.ANDROID)
        ride.build(page)
        geo = next(s for s in page.services if isinstance(s, fgeo.Geolocator))
        sent = self.encoded(geo.configuration)
        self.assertEqual(sent.get("foreground_notification_enable_wake_lock"), True)

    def test_off_means_no_service_keys_at_all(self):
        from agedashii_navi import location
        sent = self.encoded(location.android_config(hz=1, foreground=False))
        self.assertFalse([k for k in sent if k.startswith("foreground_notification")])

    def test_the_service_is_wanted_while_recording_or_routed_only(self):
        from agedashii_navi import location
        live.reset()
        self.addCleanup(live.reset)
        self.assertFalse(location.wants_service())      # a map at a stop
        live.set_destination(37.81, -122.25, "Lake Merritt")
        self.assertTrue(location.wants_service())
        live.clear_destination()
        self.assertFalse(location.wants_service())
        with mock.patch.object(live, "recording", return_value=True):
            self.assertTrue(location.wants_service())

    def test_the_ride_page_turns_it_on_when_a_route_comes_up(self):
        import flet_geolocator as fgeo
        from agedashii_navi.pages import ride
        prefs.reset()
        live.reset()
        ride.forget_map()
        self.addCleanup(ride.forget_map)
        self.addCleanup(live.reset)
        ride._M.pop("geo", None)                                 # noqa: SLF001
        self.addCleanup(ride._M.pop, "geo", None)                # noqa: SLF001
        page = page_on(ft.PagePlatform.ANDROID)
        ride.build(page)
        geo = next(s for s in page.services if isinstance(s, fgeo.Geolocator))
        self.assertNotIn("foreground_notification_title", self.encoded(geo.configuration))
        live.set_destination(37.81, -122.25, "Lake Merritt")
        ride.build(page)                                         # back to Ride
        self.assertIs(ride._M["geo"], geo)                       # noqa: SLF001 - same stream
        self.assertEqual(self.encoded(geo.configuration)
                         .get("foreground_notification_enable_wake_lock"), True)

    def test_the_rate_follows_her_setting(self):
        from agedashii_navi import location
        sent = self.encoded(location.android_config(hz=5))
        self.assertEqual(sent["interval_duration"].milliseconds, 200)


class SheCanSeeTheGpsIsOn(unittest.TestCase):
    """Android 13+ hides a foreground service's notification until the app is
    granted POST_NOTIFICATIONS at runtime: GPS on, nothing on screen saying so.
    Asked when she starts a route or a ride - a request with a reason - once."""

    def setUp(self):
        import asyncio
        import flet_permission_handler as fph
        from agedashii_navi import location
        self.asyncio, self.fph, self.location = asyncio, fph, location
        location._ASKED.clear()                                   # noqa: SLF001
        self.addCleanup(location._ASKED.clear)                    # noqa: SLF001
        self.asked = []
        test = self

        class FakeHandler:
            def __init__(self):
                test.handler = self

            async def get_status(self, permission):
                return fph.PermissionStatus.DENIED

            async def request(self, permission):
                test.asked.append(permission)
                return fph.PermissionStatus.GRANTED

        patcher = mock.patch.object(fph, "PermissionHandler", FakeHandler)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_asked_once_on_android(self):
        page = page_on(ft.PagePlatform.ANDROID)
        self.asyncio.run(self.location.ask_notifications(page))
        self.asyncio.run(self.location.ask_notifications(page))
        self.assertEqual(self.asked, [self.fph.Permission.NOTIFICATION])

    def test_never_in_a_browser(self):
        self.asyncio.run(self.location.ask_notifications(
            page_on(ft.PagePlatform.LINUX, web=True)))
        self.assertEqual(self.asked, [])

    def test_the_ride_page_asks_when_a_route_is_up(self):
        from agedashii_navi.pages import ride
        prefs.reset()
        live.reset()
        ride.forget_map()
        self.addCleanup(ride.forget_map)
        self.addCleanup(live.reset)
        live.set_destination(37.81, -122.25, "Lake Merritt")
        page = page_on(ft.PagePlatform.ANDROID)
        ride.build(page)
        self.assertIn(self.location.ask_notifications, [fn for fn, _a in page.tasks])


if __name__ == "__main__":
    unittest.main()
