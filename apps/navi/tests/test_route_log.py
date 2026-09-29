"""Every route the app computes is in the log, whole, for a ride harness.

minion 1's ride test follows the app's OWN route; without it, every off-route
in a report might be the harness's (AGEDASHII).
"""
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from agedashii_washi import routing  # noqa: E402

from agedashii_navi import live, prefs  # noqa: E402

LINE = re.compile(r"^\[navi\] route: (\d+) pts ([\d.]+) km (\S+) polyline6 (\d+)/(\d+) (\S*)$")


class TheRouteIsLogged(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        self.addCleanup(live.reset)

    def route(self, n):
        points = [(37.8044 + i * 0.00037, -122.2712 - (i % 7) * 0.00011) for i in range(n)]
        return routing.Route(ok=True, source="offline", points=points,
                             cum_m=routing.cumulative_m(points), distance_m=4321.0)

    def logged(self, route):
        live.LIVE["dest"] = (37.81, -122.26, "Somewhere")
        out = io.StringIO()
        with mock.patch.object(routing, "route", return_value=route), \
                contextlib.redirect_stdout(out):
            live.route_now()
        return [m for m in map(LINE.match, out.getvalue().splitlines()) if m]

    def test_the_pieces_decode_to_the_route_the_app_follows(self):
        route = self.route(600)
        lines = self.logged(route)
        self.assertGreater(len(lines), 1, "a long route must span several lines")
        self.assertEqual([int(m.group(4)) for m in lines], list(range(1, len(lines) + 1)))
        self.assertTrue(all(int(m.group(5)) == len(lines) for m in lines))
        self.assertTrue(all(len(m.group(6)) <= live.ROUTE_LOG_CHUNK for m in lines))
        self.assertEqual(lines[0].group(1), "600")
        self.assertEqual(lines[0].group(2), "4.32")
        self.assertEqual(lines[0].group(3), "offline")
        back = routing.decode_polyline6("".join(m.group(6) for m in lines))
        self.assertEqual(len(back), 600)
        for (lat, lon), (blat, blon) in zip(route.points, back):
            self.assertAlmostEqual(lat, blat, places=6)
            self.assertAlmostEqual(lon, blon, places=6)

    def test_a_failed_route_logs_nothing(self):
        failed = routing.Route(ok=False, source="offline", reason="no graph")
        self.assertEqual(self.logged(failed), [])


if __name__ == "__main__":
    unittest.main()
