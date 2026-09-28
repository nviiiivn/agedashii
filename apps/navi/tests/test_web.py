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


if __name__ == "__main__":
    unittest.main()
