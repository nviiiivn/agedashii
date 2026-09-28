"""Tests for agedashii_tofu/routing.py — offline routing and its server fallback.

Three things these guard, none of which shows up until the phone is already
somewhere with no signal:

1. **The honesty rule.** `Route.source` says where a route came from, and it
   has to be right in both directions: a route the server computed must
   never be labelled offline, and a route the device computed must never
   quietly have gone over the network first. A rider who believes the phone
   is self-sufficient, wrongly, finds out in the wrong place.

2. **Absence is a normal state, not a crash.** No graph installed, a
   half-downloaded graph, a graph from a different builder - each has to
   produce a sentence a person can act on and a fallback, not a traceback
   on a blank screen.

3. **The arithmetic behind the guidance.** Distance along the route,
   distance to the next turn, and the formatting of both are what the rider
   actually reads at 60 mph. They are pure functions here so they can be
   tested without 86 MB of graph.

Everything runs offline. The cases that need the real graph skip themselves,
loudly, when it is not installed - a skipped test says "not checked here",
which is true, while a test that quietly passes without its data does not.
"""
import math
import os
import struct
import sys
import tempfile
import threading
import unittest
from array import array
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu import routing as R  # noqa: E402


def _encode6(points):
    """Google polyline encoder at precision 6, written from the spec.

    Independent of the decoder under test: it is the format Valhalla speaks
    and the only way to build a fixture response without a live server.
    """
    out = []
    plat = plon = 0
    for lat, lon in points:
        ilat, ilon = int(round(lat * 1e6)), int(round(lon * 1e6))
        for delta in (ilat - plat, ilon - plon):
            v = ~(delta << 1) if delta < 0 else (delta << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


SHAPE = [(37.8000, -122.2700), (37.8010, -122.2700),
         (37.8020, -122.2690), (37.8030, -122.2690)]

VALHALLA_OK = {
    "trip": {
        "summary": {"length": 0.42, "time": 63.0},
        "legs": [{
            "shape": _encode6(SHAPE),
            "maneuvers": [
                {"instruction": "Drive north on Broadway.",
                 "street_names": ["Broadway"], "length": 0.11,
                 "begin_shape_index": 0},
                {"instruction": "Turn right onto 5th Street.",
                 "street_names": ["5th Street"], "length": 0.31,
                 "begin_shape_index": 1},
                {"instruction": "You have arrived.", "street_names": [],
                 "length": 0.0, "begin_shape_index": 3},
            ],
        }],
    }
}


class RoutingTestCase(unittest.TestCase):
    """routing holds one process-wide loaded graph; restore it around each test."""

    def setUp(self):
        self._env = os.environ.get(R.ENV_OVERRIDE)
        # The real 86 MB graph now ships in assets/, so the bundled-asset
        # search must be redirected or every "nothing installed" case finds
        # it and these tests assert against the shipping file.
        self._root = R.APP_ROOT
        self._tmproot = tempfile.TemporaryDirectory()
        R.APP_ROOT = Path(self._tmproot.name) / "app"
        R.reset()

    def tearDown(self):
        R.APP_ROOT = self._root
        self._tmproot.cleanup()
        R.reset()
        if self._env is None:
            os.environ.pop(R.ENV_OVERRIDE, None)
        else:
            os.environ[R.ENV_OVERRIDE] = self._env

    def no_server(self):
        """Patch the server call so a test can never silently reach one."""
        def boom(*a, **kw):
            raise AssertionError("the server must not be contacted here")
        return mock.patch.object(R, "_post_json", boom)


# --------------------------------------------------------- graph discovery

class TestGraphPath(RoutingTestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "somewhere-else.rg"
            f.write_bytes(b"x")
            os.environ[R.ENV_OVERRIDE] = str(f)
            self.assertEqual(R.graph_path(), str(f))

    def test_env_override_pointing_at_nothing_is_skipped(self):
        """A stale setting must not shadow a graph that is actually there."""
        with tempfile.TemporaryDirectory() as d:
            os.environ[R.ENV_OVERRIDE] = str(Path(d) / "gone.rg")
            home = Path(d) / "home"
            (home / ".nvii-dash").mkdir(parents=True)
            installed = home / ".nvii-dash" / R.GRAPH_FILENAME
            installed.write_bytes(b"x")
            with mock.patch.object(R.Path, "home", staticmethod(lambda: home)):
                self.assertEqual(R.graph_path(), str(installed))

    def test_home_directory_is_searched(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ.pop(R.ENV_OVERRIDE, None)
            home = Path(d)
            (home / ".nvii-dash").mkdir()
            installed = home / ".nvii-dash" / R.GRAPH_FILENAME
            installed.write_bytes(b"x")
            with mock.patch.object(R.Path, "home", staticmethod(lambda: home)):
                self.assertEqual(R.graph_path(), str(installed))

    def test_nothing_installed_is_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ.pop(R.ENV_OVERRIDE, None)
            with mock.patch.object(R.Path, "home", staticmethod(lambda: Path(d))):
                self.assertIsNone(R.graph_path())

    def test_directory_named_like_the_graph_is_not_a_graph(self):
        with tempfile.TemporaryDirectory() as d:
            bogus = Path(d) / R.GRAPH_FILENAME
            bogus.mkdir()
            os.environ[R.ENV_OVERRIDE] = str(bogus)
            with mock.patch.object(R.Path, "home", staticmethod(lambda: Path(d))):
                self.assertIsNone(R.graph_path())


# ------------------------------------------------------------- the loader

class TestLoader(RoutingTestCase):
    def test_absent_graph_reports_absent_and_says_why(self):
        with mock.patch.object(R, "graph_path", lambda: None):
            self.assertEqual(R.start_loading(), "absent")
            self.assertFalse(R.ready())
            self.assertIsNone(R.graph(timeout=1))
            st = R.status()
        self.assertEqual(st["phase"], "absent")
        self.assertIn("no offline road graph", st["detail"])

    def test_unreadable_graph_fails_with_the_filename_in_the_reason(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "truncated.rg"
            bad.write_bytes(b"NOTAGRAPH" + b"\0" * 64)
            with mock.patch.object(R, "graph_path", lambda: str(bad)):
                R.graph(timeout=5)
                st = R.status()
        self.assertEqual(st["phase"], "failed")
        self.assertIn("truncated.rg", st["detail"])

    def test_loading_happens_once_even_when_asked_many_times(self):
        """start_loading runs on every page open; it must not re-read 86 MB."""
        calls = []
        real_thread = threading.Thread

        def counting_thread(*a, **kw):
            calls.append(1)
            return real_thread(*a, **kw)

        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "x.rg"
            bad.write_bytes(b"NOTAGRAPH")
            with mock.patch.object(R, "graph_path", lambda: str(bad)), \
                 mock.patch.object(R.threading, "Thread", counting_thread):
                R.start_loading()
                R.graph(timeout=5)            # waits for the load to finish
                self.assertEqual(R.status()["phase"], "failed")
                for _ in range(5):
                    self.assertEqual(R.start_loading(), "failed")
        self.assertEqual(sum(calls), 1)

    def test_an_absent_graph_is_looked_for_again_later(self):
        """Unlike a failure, "not installed yet" can stop being true."""
        with mock.patch.object(R, "graph_path", lambda: None):
            self.assertEqual(R.start_loading(), "absent")
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "arrived.rg"
            bad.write_bytes(b"NOTAGRAPH")
            with mock.patch.object(R, "graph_path", lambda: str(bad)):
                self.assertEqual(R.start_loading(), "loading")
                R.graph(timeout=5)
        self.assertEqual(R.status()["phase"], "failed")

    def test_graph_with_zero_timeout_never_blocks(self):
        with mock.patch.object(R, "graph_path", lambda: None):
            self.assertIsNone(R.graph(timeout=0))

    def test_bad_magic_is_rejected_by_the_reader_itself(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "x.rg"
            bad.write_bytes(b"RGRAPH99" + struct.pack("<5I", 0, 0, 0, 0, 0))
            with self.assertRaises(ValueError):
                R.Graph(bad)


# ------------------------------------------------- failure without a graph

class TestNoGraphPath(RoutingTestCase):
    def test_offline_only_says_the_graph_is_missing(self):
        with mock.patch.object(R, "graph_path", lambda: None), self.no_server():
            out = R.route((37.8, -122.27), (37.55, -121.99), allow_server=False)
        self.assertFalse(out.ok)
        self.assertEqual(out.source, "offline")
        self.assertIn("no offline road graph", out.reason)

    def test_bad_coordinates_never_reach_the_network(self):
        with self.no_server():
            out = R.route("not a place", (37.55, -121.99))
        self.assertFalse(out.ok)
        self.assertIn("lat,lon", out.reason)

    def test_out_of_range_coordinates_are_rejected(self):
        with self.no_server():
            out = R.route((91.0, 0.0), (0.0, 0.0))
        self.assertFalse(out.ok)
        self.assertIn("latitude", out.reason)

    def test_falls_back_to_the_server_and_says_so(self):
        with mock.patch.object(R, "graph_path", lambda: None), \
             mock.patch.object(R, "_post_json", lambda *a, **k: (VALHALLA_OK, None)):
            out = R.route((37.80, -122.27), (37.803, -122.269))
        self.assertTrue(out.ok)
        # The honesty rule, in the direction that matters most.
        self.assertEqual(out.source, "server")
        self.assertFalse(out.offline)
        self.assertIn("Valhalla", out.source_detail)
        self.assertIn("routed by the server", out.reason)
        self.assertIn("server", out.summary())

    def test_server_failure_reports_both_reasons(self):
        with mock.patch.object(R, "graph_path", lambda: None), \
             mock.patch.object(R, "_post_json",
                               lambda *a, **k: (None, "connection refused")):
            out = R.route((37.80, -122.27), (37.803, -122.269))
        self.assertFalse(out.ok)
        self.assertEqual(out.source, "server")
        self.assertIn("no offline road graph", out.reason)
        self.assertIn("connection refused", out.reason)

    def test_the_payload_is_the_shape_the_server_proxy_expects(self):
        seen = {}

        def capture(path, payload, timeout):
            seen["path"] = path
            seen["payload"] = payload
            return VALHALLA_OK, None

        with mock.patch.object(R, "graph_path", lambda: None), \
             mock.patch.object(R, "_post_json", capture):
            R.route((37.80, -122.27), (37.803, -122.269))
        self.assertEqual(seen["path"], "/api/valhalla/route")
        body = seen["payload"]
        self.assertEqual(body["costing"], "motorcycle")
        self.assertIn("motorcycle", body["costing_options"])
        # Kilometres, so lengths are converted in exactly one place.
        self.assertEqual(body["directions_options"]["units"], "kilometers")
        self.assertEqual([q["lat"] for q in body["locations"]], [37.80, 37.803])


# ------------------------------------------------- parsing what a server said

class TestValhallaParsing(RoutingTestCase):
    def test_a_good_trip_becomes_a_route(self):
        out = R.parse_valhalla(VALHALLA_OK)
        self.assertTrue(out.ok)
        self.assertEqual(out.source, "server")
        self.assertEqual(len(out.points), len(SHAPE))
        self.assertAlmostEqual(out.points[0][0], SHAPE[0][0], places=5)
        self.assertAlmostEqual(out.distance_m, 420.0, places=3)
        self.assertAlmostEqual(out.duration_s, 63.0, places=3)
        self.assertEqual(len(out.maneuvers), 3)
        self.assertEqual(out.maneuvers[1].street, "5th Street")
        self.assertAlmostEqual(out.maneuvers[1].distance_m, 310.0, places=3)
        self.assertEqual(len(out.cum_m), len(out.points))

    def test_an_error_body_is_repeated_not_swallowed(self):
        out = R.parse_valhalla({"error": "No path could be found"})
        self.assertFalse(out.ok)
        self.assertIn("No path could be found", out.reason)

    def test_no_legs_is_no_route(self):
        self.assertFalse(R.parse_valhalla({"trip": {"legs": []}}).ok)
        self.assertFalse(R.parse_valhalla({}).ok)
        self.assertFalse(R.parse_valhalla("<html>login</html>").ok)

    def test_a_shapeless_trip_is_not_drawable(self):
        out = R.parse_valhalla({"trip": {"legs": [{"shape": ""}],
                                         "summary": {"length": 1}}})
        self.assertFalse(out.ok)
        self.assertIn("shape", out.reason)

    def test_maneuver_indices_stay_inside_the_polyline(self):
        data = {"trip": {"summary": {"length": 0.4, "time": 60},
                         "legs": [{"shape": _encode6(SHAPE),
                                   "maneuvers": [{"instruction": "Go",
                                                  "begin_shape_index": 999}]}]}}
        out = R.parse_valhalla(data)
        self.assertTrue(out.ok)
        self.assertLess(out.maneuvers[0].index, len(out.points))


class TestPolylineDecoding(RoutingTestCase):
    def test_the_classic_test_vector_at_precision_six(self):
        # "_p~iF~ps|U" is the canonical (38.5, -120.2) at precision 5; at
        # precision 6 the same integers mean a tenth of that.
        got = R.decode_polyline6("_p~iF~ps|U")
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][0], 3.85, places=6)
        self.assertAlmostEqual(got[0][1], -12.02, places=6)

    def test_round_trip(self):
        got = R.decode_polyline6(_encode6(SHAPE))
        self.assertEqual(len(got), len(SHAPE))
        for a, b in zip(got, SHAPE):
            self.assertAlmostEqual(a[0], b[0], places=6)
            self.assertAlmostEqual(a[1], b[1], places=6)

    def test_rubbish_input_yields_nothing_rather_than_raising(self):
        self.assertEqual(R.decode_polyline6(""), [])
        self.assertEqual(R.decode_polyline6(None), [])
        self.assertIsInstance(R.decode_polyline6("~"), list)


# ------------------------------------------------------ maneuver phrasing

class TestManeuverWording(RoutingTestCase):
    def test_every_heading_change_has_a_word(self):
        for delta in range(-180, 181):
            self.assertTrue(R.turn_word(delta))

    def test_the_boundaries_are_where_they_should_be(self):
        self.assertEqual(R.turn_word(0), "continue straight")
        self.assertEqual(R.turn_word(19), "continue straight")
        self.assertEqual(R.turn_word(-19), "continue straight")
        self.assertEqual(R.turn_word(25), "bear right")
        self.assertEqual(R.turn_word(-25), "bear left")
        self.assertEqual(R.turn_word(90), "turn right")
        self.assertEqual(R.turn_word(-90), "turn left")
        self.assertEqual(R.turn_word(170), "make a U-turn")
        self.assertEqual(R.turn_word(-170), "make a U-turn")
        self.assertEqual(R.turn_word(180), "make a U-turn")

    def test_wrapping_is_handled(self):
        """A bearing change of 350 degrees is a 10 degree kink, not a U-turn."""
        self.assertEqual(R.turn_word(350), "continue straight")
        self.assertEqual(R.turn_word(-350), "continue straight")

    def test_turns_start_at_45_and_sharp_at_136(self):
        # Valhalla's own classifier (baldr/turn.cc, identical in 3.6.3 and
        # 3.8.3), which the Kotlin app uses too. Measured: at 45-50 degrees
        # Valhalla said turn 21 times and slight 0.
        self.assertEqual(R.turn_word(44.9), "bear right")
        self.assertEqual(R.turn_word(45), "turn right")
        self.assertEqual(R.turn_word(-45), "turn left")
        self.assertEqual(R.turn_word(135), "turn right")
        self.assertEqual(R.turn_word(-135), "turn left")
        self.assertEqual(R.turn_word(136), "make a sharp right")
        self.assertEqual(R.turn_word(-136), "make a sharp left")
        self.assertEqual(R.turn_word(159.9), "make a sharp right")
        self.assertEqual(R.turn_word(160), "make a U-turn")

    def test_compass_words(self):
        self.assertEqual(R.compass_word(0), "north")
        self.assertEqual(R.compass_word(45), "northeast")
        self.assertEqual(R.compass_word(135), "southeast")
        self.assertEqual(R.compass_word(359), "north")

    def test_bearing_of_a_due_north_step(self):
        self.assertAlmostEqual(R.bearing((37.0, -122.0), (37.1, -122.0)),
                               0.0, places=3)
        self.assertAlmostEqual(R.bearing((37.0, -122.0), (37.0, -121.9)),
                               90.0, places=1)


class TestManeuverKinds(RoutingTestCase):
    """The kind is the clip contract: her recordings are named these."""

    def test_the_kinds_are_the_clip_names(self):
        self.assertEqual(R.KINDS, (
            "turn-left", "turn-right", "slight-left", "slight-right",
            "sharp-left", "sharp-right", "uturn", "continue", "merge", "exit",
            "arrive", "start", "roundabout"))

    def test_every_band_has_a_kind(self):
        cases = {0: "continue", 19.9: "continue", -19.9: "continue",
                 20: "slight-right", -20: "slight-left", 44.9: "slight-right",
                 45: "turn-right", -45: "turn-left", 135: "turn-right",
                 -135: "turn-left", 136: "sharp-right", -136: "sharp-left",
                 159.9: "sharp-right", -159.9: "sharp-left", 160: "uturn",
                 -160: "uturn", 180: "uturn", 350: "continue"}
        for delta, kind in cases.items():
            self.assertEqual(R.turn_kind(delta), kind, delta)

    def test_the_words_and_the_kind_never_disagree(self):
        # The banner reads the words, her voice plays the kind. A "bear left"
        # on screen with her TURN LEFT in her ears is a wrong instruction.
        says = {"make a U-turn": "uturn", "make a sharp left": "sharp-left",
                "make a sharp right": "sharp-right", "turn left": "turn-left",
                "turn right": "turn-right", "bear left": "slight-left",
                "bear right": "slight-right", "continue straight": "continue"}
        for tenth in range(-1800, 1801):
            delta = tenth / 10
            self.assertEqual(says[R.turn_word(delta)], R.turn_kind(delta), delta)

    def test_a_maneuver_built_without_a_kind_still_builds(self):
        m = R.Maneuver("turn left", "", 0.0, (0.0, 0.0), 0, 0.0)
        self.assertIsNone(m.kind)

    def test_the_server_route_carries_kinds_from_valhallas_types(self):
        shape = [(37.80 + i * 0.001, -122.27) for i in range(12)]

        def man(t, i, before=0, after=0, text="x"):
            return {"type": t, "instruction": text, "begin_shape_index": i,
                    "length": 0.1, "bearing_before": before, "bearing_after": after}

        data = {"trip": {"summary": {"length": 1.2, "time": 90}, "legs": [{
            "shape": _encode6(shape),
            "maneuvers": [man(1, 0), man(10, 1), man(11, 2), man(16, 3),
                          man(20, 4), man(18, 5, 0, 30), man(19, 6, 0, 270),
                          man(23, 7), man(24, 8), man(25, 9), man(26, 10),
                          man(27, 10), man(28, 10), man(12, 10), man(8, 11),
                          man(4, 11)]}]}}
        out = R.parse_valhalla(data)
        self.assertEqual([m.kind for m in out.maneuvers], [
            "start", "turn-right", "sharp-right", "slight-left", "exit",
            "slight-right", "turn-left", "slight-right", "slight-left",
            "merge", "roundabout", None, None, "uturn", "continue", "arrive"])


# ----------------------------------------------------- distance and timing

class TestDistanceMaths(RoutingTestCase):
    def test_haversine_against_a_known_pair(self):
        # Oakland City Center to Fremont: ~37.8 km great-circle.
        d = R.haversine_m((37.8044, -122.2712), (37.5485, -121.9886))
        self.assertAlmostEqual(d / 1000.0, 37.8, delta=0.3)
        # One degree of latitude is ~111.2 km anywhere on earth.
        self.assertAlmostEqual(R.haversine_m((0.0, 0.0), (1.0, 0.0)) / 1000.0,
                               111.2, delta=0.2)

    def test_haversine_is_zero_for_the_same_point(self):
        self.assertAlmostEqual(R.haversine_m((37.8, -122.2), (37.8, -122.2)), 0.0)

    def test_cumulative_is_monotonic_and_ends_at_the_total(self):
        pts = [(37.80, -122.27), (37.81, -122.27), (37.82, -122.26)]
        cum = R.cumulative_m(pts)
        self.assertEqual(cum[0], 0.0)
        self.assertLess(cum[0], cum[1])
        self.assertLess(cum[1], cum[2])
        total = (R.haversine_m(pts[0], pts[1]) + R.haversine_m(pts[1], pts[2]))
        self.assertAlmostEqual(cum[-1], total, places=6)

    def test_graph_lengths_are_authoritative_when_supplied(self):
        """The turn list and the progress bar must not disagree by rounding."""
        pts = [(37.80, -122.27), (37.81, -122.27), (37.82, -122.26)]
        bounds = [(0, 1), (1, 2)]
        cum = R.cumulative_m(pts, lengths=[1000.0, 2000.0], bounds=bounds)
        self.assertAlmostEqual(cum[1], 1000.0, places=6)
        self.assertAlmostEqual(cum[2], 3000.0, places=6)

    def test_cumulative_of_a_degenerate_line(self):
        self.assertEqual(R.cumulative_m([]), [])
        self.assertEqual(R.cumulative_m([(1.0, 2.0)]), [0.0])


class TestFormatting(RoutingTestCase):
    def test_short_imperial_distances_are_feet(self):
        self.assertEqual(R.fmt_distance(0), "0 ft")
        self.assertEqual(R.fmt_distance(100), "330 ft")
        self.assertTrue(R.fmt_distance(300).endswith("ft"))

    def test_long_imperial_distances_are_miles(self):
        self.assertEqual(R.fmt_distance(1609.344), "1.0 mi")
        self.assertEqual(R.fmt_distance(42714.0), "26.5 mi")

    def test_metric(self):
        self.assertEqual(R.fmt_distance(100, "metric"), "100 m")
        self.assertEqual(R.fmt_distance(1000, "metric"), "1.0 km")
        self.assertEqual(R.fmt_distance(42714.0, "metric"), "42.7 km")

    def test_nonsense_distance_is_a_dash_not_a_crash(self):
        self.assertEqual(R.fmt_distance(None), "—")
        self.assertEqual(R.fmt_distance("soon"), "—")
        self.assertEqual(R.fmt_distance(-5), "0 ft")

    def test_durations(self):
        self.assertEqual(R.fmt_duration(0), "0 min")
        # A turn 20 seconds away is not "0 min" away.
        self.assertEqual(R.fmt_duration(20), "<1 min")
        self.assertEqual(R.fmt_duration(2340), "39 min")
        self.assertEqual(R.fmt_duration(4500), "1 h 15 min")
        self.assertEqual(R.fmt_duration(None), "—")

    def test_eta_is_a_clock_time(self):
        import time as _t
        base = _t.mktime((2026, 1, 2, 10, 0, 0, 0, 0, -1))
        self.assertEqual(R.fmt_eta(1800, now=base), "10:30")

    def test_parse_latlon_accepts_what_a_person_types(self):
        self.assertEqual(R.parse_latlon("37.8044, -122.2712"),
                         (37.8044, -122.2712))
        self.assertEqual(R.parse_latlon("37.8044,-122.2712"),
                         (37.8044, -122.2712))
        self.assertEqual(R.parse_latlon("37.8044; -122.2712"),
                         (37.8044, -122.2712))
        self.assertEqual(R.parse_latlon((37.8, -122.2)), (37.8, -122.2))

    def test_parse_latlon_rejects_what_it_should(self):
        for bad in ("", "Oakland", "37.8", "37.8,-122.2,5", "a,b",
                    (91, 0), (0, 181), None):
            self.assertIsNone(R.parse_latlon(bad), bad)


# ------------------------------------------------------------- guidance

def _fake_route(source="offline"):
    """A short straight route, built by hand so guidance is testable."""
    pts = [(37.800, -122.270), (37.802, -122.270), (37.804, -122.270),
           (37.806, -122.270), (37.808, -122.270)]
    cum = R.cumulative_m(pts)
    turns = [
        R.Maneuver("head north on A Street", "A Street",
                   cum[2], pts[0], 0, cum[0]),
        R.Maneuver("turn right onto B Street", "B Street",
                   cum[4] - cum[2], pts[2], 2, cum[2]),
        R.Maneuver("arrive at your destination", "B Street",
                   0.0, pts[4], 4, cum[4]),
    ]
    return R.Route(ok=True, source=source, source_detail=source, points=pts,
                   maneuvers=turns, distance_m=cum[-1], duration_s=120.0,
                   cum_m=cum)


class TestGuidance(RoutingTestCase):
    def test_progress_finds_the_nearest_point(self):
        r = _fake_route()
        i, along, off = R.progress(r, r.points[2])
        self.assertEqual(i, 2)
        self.assertAlmostEqual(along, r.cum_m[2], places=6)
        self.assertAlmostEqual(off, 0.0, places=6)

    def test_off_route_distance_is_reported_not_hidden(self):
        r = _fake_route()
        _, _, off = R.progress(r, (37.800, -122.300))
        self.assertGreater(off, 1000)

    def test_the_head_instruction_is_never_the_next_one(self):
        r = _fake_route()
        m, to_go, _ = R.next_maneuver(r, r.points[0])
        self.assertIn("turn right", m.instruction)
        self.assertAlmostEqual(to_go, r.cum_m[2], places=6)

    def test_next_maneuver_advances_along_the_route(self):
        r = _fake_route()
        m, to_go, _ = R.next_maneuver(r, r.points[3])
        self.assertIn("arrive", m.instruction)
        self.assertAlmostEqual(to_go, r.cum_m[4] - r.cum_m[3], places=6)

    def test_at_the_destination_the_next_turn_is_arrival_at_zero(self):
        r = _fake_route()
        m, to_go, _ = R.next_maneuver(r, r.points[4])
        self.assertIn("arrive", m.instruction)
        self.assertAlmostEqual(to_go, 0.0, places=6)

    def test_remaining_shrinks(self):
        r = _fake_route()
        start_m, start_s = R.remaining(r, r.points[0])
        mid_m, mid_s = R.remaining(r, r.points[2])
        self.assertAlmostEqual(start_m, r.distance_m, places=6)
        self.assertLess(mid_m, start_m)
        self.assertLess(mid_s, start_s)
        self.assertAlmostEqual(R.remaining(r, r.points[4])[0], 0.0, places=6)

    def test_guidance_on_a_failed_route_is_silent_not_explosive(self):
        bad = R.Route(ok=False, reason="nope")
        self.assertEqual(R.next_maneuver(bad, (37.8, -122.2)), (None, None, None))
        self.assertEqual(R.remaining(bad, (37.8, -122.2)), (None, None))

    def test_summary_always_names_the_source(self):
        self.assertIn("offline", _fake_route("offline").summary())
        self.assertIn("server", _fake_route("server").summary())

    def test_a_failed_route_summarises_as_its_reason(self):
        self.assertEqual(R.Route(ok=False, reason="no graph").summary(),
                         "no graph")


# -------------------------------------- a whole graph, small enough to read
#
# The shipped Bay Area graph is 86 MB and is not on the CI box, so the two
# things that most need testing - snapping to a point halfway down a block,
# and refusing a banned turn - would otherwise only ever be checked by hand.
# So build a nine-node lattice and write it out in the real file format.
# Writing the format here rather than importing a helper from the module is
# deliberate: if the loader and the builder drift apart, this catches it.

def _pack_trie(bans, adj_to, adj_off, n_nodes):
    """Banned directed-edge sequences -> the packed trie the router reads."""
    children = [{}]
    banned = [set()]
    head = [0]
    for seq in bans:
        t = 0
        for e in seq[:-1]:
            nt = children[t].get(e)
            if nt is None:
                nt = len(children)
                children.append({})
                banned.append(set())
                head.append(adj_to[e])
                children[t][e] = nt
            t = nt
        banned[t].add(seq[-1])
    ntr = len(children)
    tr_off = array("I", bytes(4 * (ntr + 1)))
    tr_edge = array("I")
    tr_next = array("I")
    for t in range(ntr):
        tr_off[t] = len(tr_edge)
        for e, c in children[t].items():
            tr_edge.append(e)
            tr_next.append(c)
        for e in banned[t]:
            tr_edge.append(e)
            tr_next.append(0xFFFFFFFF)
    tr_off[ntr] = len(tr_edge)
    gate = bytearray(n_nodes)
    for e in children[0]:
        lo, hi = 0, n_nodes
        while lo < hi:
            mid = (lo + hi) // 2
            if adj_off[mid] <= e:
                lo = mid + 1
            else:
                hi = mid
        gate[lo - 1] = 1
    return tr_off, tr_edge, tr_next, array("I", head), gate


def _pack_graph(path, nodes, segs, bans=(), version=2, cell=400.0):
    """Write a .rg file. Returns {(u, v): directed edge index}.

    `nodes` is [(lat, lon)], `segs` is [(u, v, street name, one-way?, road
    class)], and `bans` is [(edge, edge, ...)] - sequences that may not be
    driven in order.
    """
    n = len(nodes)
    strings = [""]
    seg_pts = array("i")
    seg_off = array("I", [0])
    seg_name = array("I")
    out_edges = [[] for _ in range(n)]
    for sid, (u, v, name, oneway, cls) in enumerate(segs):
        if name not in strings:
            strings.append(name)
        mid = ((nodes[u][0] + nodes[v][0]) / 2, (nodes[u][1] + nodes[v][1]) / 2)
        for la, lo in (nodes[u], mid, nodes[v]):
            seg_pts.append(int(round(la * 1e7)))
            seg_pts.append(int(round(lo * 1e7)))
        seg_off.append(len(seg_pts) // 2)
        seg_name.append(strings.index(name))
        ldm = max(1, int(R.haversine_m(nodes[u], nodes[v]) * 10))
        out_edges[u].append((v, ldm, cls, sid))
        if not oneway:
            out_edges[v].append((u, ldm, cls, sid | 0x80000000))
    e = sum(len(x) for x in out_edges)
    adj_off = array("I", bytes(4 * (n + 1)))
    adj_to = array("I")
    adj_len = array("I")
    adj_cls = array("B")
    adj_seg = array("I")
    index = {}
    for i in range(n):
        adj_off[i] = len(adj_to)
        for (v, ldm, c, sid) in out_edges[i]:
            index[(i, v)] = len(adj_to)
            adj_to.append(v)
            adj_len.append(ldm)
            adj_cls.append(c)
            adj_seg.append(sid)
    adj_off[n] = len(adj_to)

    olat = sum(q[0] for q in nodes) / n
    olon = sum(q[1] for q in nodes) / n
    my = 111132.92 - 559.82 * math.cos(2 * olat * math.pi / 180)
    mx = 111412.84 * math.cos(olat * math.pi / 180)
    xy = array("f", bytes(8 * n))
    for i, (la, lo) in enumerate(nodes):
        xy[2 * i] = (lo - olon) * mx
        xy[2 * i + 1] = (la - olat) * my
    minx, miny = min(xy[0::2]), min(xy[1::2])
    gw = int((max(xy[0::2]) - minx) / cell) + 1
    gh = int((max(xy[1::2]) - miny) / cell) + 1
    ncell = gw * gh

    def cell_of(x, y):
        cx = min(gw - 1, max(0, int((x - minx) / cell)))
        cy = min(gh - 1, max(0, int((y - miny) / cell)))
        return cy * gw + cx

    buckets = [[] for _ in range(ncell)]
    for i in range(n):
        buckets[cell_of(xy[2 * i], xy[2 * i + 1])].append(i)
    grid_off = array("I", [0])
    grid_nod = array("I")
    for b in buckets:
        grid_nod.extend(b)
        grid_off.append(len(grid_nod))

    sbuckets = [set() for _ in range(ncell)]
    for sid in range(len(segs)):
        for p in range(seg_off[sid], seg_off[sid + 1]):
            x = (seg_pts[2 * p + 1] / 1e7 - olon) * mx
            y = (seg_pts[2 * p] / 1e7 - olat) * my
            sbuckets[cell_of(x, y)].add(sid)
    sgrid_off = array("I", [0])
    sgrid_seg = array("I")
    for b in sbuckets:
        sgrid_seg.extend(sorted(b))
        sgrid_off.append(len(sgrid_seg))

    tr_off, tr_edge, tr_next, tr_head, tr_gate = _pack_trie(
        bans, adj_to, adj_off, n)

    blob = bytearray()
    str_off = array("I", [0])
    for s in strings:
        blob += s.encode()
        str_off.append(len(blob))

    with open(path, "wb") as f:
        f.write(b"RGRAPH02" if version == 2 else b"RGRAPH01")
        f.write(struct.pack("<5I", n, e, len(segs), len(seg_pts) // 2,
                            len(blob)))
        f.write(struct.pack("<3I", len(strings), gw, gh))
        if version == 2:
            f.write(struct.pack("<3I", len(tr_off) - 1, len(tr_edge),
                                len(sgrid_seg)))
        f.write(struct.pack("<7d", olat, olon, mx, my, minx, miny, cell))
        for a in (xy, adj_off, adj_to, adj_len, adj_cls, adj_seg, seg_off,
                  seg_name, seg_pts, str_off, grid_off, grid_nod):
            a.tofile(f)
        if version == 2:
            for a in (sgrid_off, sgrid_seg, tr_off, tr_edge, tr_next, tr_head):
                a.tofile(f)
            f.write(bytes(tr_gate))
        f.write(bytes(blob))
    return index


#   0 --- 1 --- 2      North Street
#   |     |     |
#   3 --- 4 --- 5      Middle Street
#   |     |     |
#   6 --- 7 --- 8      South Street
#
# ~220 m between neighbours, so "halfway down a block" is ~110 m from either
# junction - far enough that snapping to the wrong one is a visible error.
LAT0, LON0, STEP = 37.8000, -122.2700, 0.0020
LATTICE = [(LAT0 - STEP * (i // 3), LON0 + STEP * (i % 3)) for i in range(9)]
ROWS = ["North Street", "Middle Street", "South Street"]
COLS = ["First Avenue", "Second Avenue", "Third Avenue"]
RESIDENTIAL, SERVICE = 11, 13
# The outer two avenues are slow, so that between any two corners there is
# one clearly quickest way round rather than a tie the test cannot pin down.
LATTICE_SEGS = (
    [(r * 3 + c, r * 3 + c + 1, ROWS[r], False, RESIDENTIAL) for r in range(3)
     for c in range(2)]
    + [(r * 3 + c, (r + 1) * 3 + c, COLS[c], False,
        RESIDENTIAL if c == 1 else SERVICE) for r in range(2)
       for c in range(3)])
BLOCK_EW = R.haversine_m(LATTICE[3], LATTICE[4])      # ~176 m
BLOCK_NS = R.haversine_m(LATTICE[0], LATTICE[3])      # ~222 m
CORNER = BLOCK_EW + BLOCK_NS                          # one turn, either way


class LatticeTestCase(RoutingTestCase):
    """A whole graph in a temp file, so offline routing is always exercised."""

    def build(self, bans=(), version=2, segs=None):
        path = Path(self._tmproot.name) / "lattice.rg"
        idx = _pack_graph(path, LATTICE, segs or LATTICE_SEGS, bans, version)
        os.environ[R.ENV_OVERRIDE] = str(path)
        R.reset()
        return R.Graph(path), idx

    def edges(self):
        """{(u, v): directed edge index} for the lattice, before any ban."""
        return _pack_graph(Path(self._tmproot.name) / "probe.rg", LATTICE,
                           LATTICE_SEGS)

    def midpoint(self, a, b):
        return ((LATTICE[a][0] + LATTICE[b][0]) / 2,
                (LATTICE[a][1] + LATTICE[b][1]) / 2)


class TestEdgeSnapping(LatticeTestCase):
    def test_a_mid_block_fix_snaps_to_the_road_not_the_junction(self):
        g, _ = self.build()
        p = self.midpoint(3, 4)
        _, node_err = g.snap(*p)
        s = g.snap_edge(*p)
        self.assertIsNotNone(s)
        self.assertLess(s.dist_m, 1.0)
        # the junction snap is half a block out, which is the whole problem
        self.assertAlmostEqual(node_err, BLOCK_EW / 2, delta=1.0)
        self.assertAlmostEqual(s.frac, 0.5, places=2)
        self.assertEqual({s.u, s.v}, {3, 4})

    def test_the_snap_carries_the_edges_in_both_directions(self):
        g, idx = self.build()
        s = g.snap_edge(*self.midpoint(3, 4))
        self.assertEqual(s.fwd, idx[(3, 4)])
        self.assertEqual(s.bwd, idx[(4, 3)])

    def test_a_one_way_offers_only_the_legal_direction(self):
        segs = [(u, v, nm, (u, v) == (3, 4), c)
                for (u, v, nm, _, c) in LATTICE_SEGS]
        g, idx = self.build(segs=segs)
        s = g.snap_edge(*self.midpoint(3, 4))
        self.assertEqual(s.fwd, idx[(3, 4)])
        self.assertEqual(s.bwd, -1)

    def test_the_offset_reported_is_the_distance_to_the_road(self):
        """Not to the junction: it is what decides "you are off the map"."""
        g, _ = self.build()
        lat, lon = self.midpoint(3, 4)
        s = g.snap_edge(lat + 0.0003, lon)        # ~33 m north of the road
        self.assertAlmostEqual(s.dist_m, 33.0, delta=4.0)

    def test_nothing_within_reach_is_none_not_a_wrong_answer(self):
        g, _ = self.build()
        self.assertIsNone(g.snap_edge(48.8584, 2.2945))

    def test_a_route_begins_where_the_rider_actually_is(self):
        g, _ = self.build()
        start = self.midpoint(3, 4)
        with self.no_server():
            out = R.route(start, LATTICE[2], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertLess(R.haversine_m(out.points[0], start), 1.0)
        self.assertLess(out.snap_from_m, 1.0)

    def test_the_first_instruction_points_the_way_the_rider_is_facing(self):
        """The junction snap could only ever say "head" out of a junction."""
        g, _ = self.build()
        with self.no_server():
            east = R.route(self.midpoint(3, 4), LATTICE[5], allow_server=False)
            west = R.route(self.midpoint(4, 5), LATTICE[3], allow_server=False)
        self.assertTrue(east.ok and west.ok)
        self.assertIn("east", east.maneuvers[0].instruction)
        self.assertIn("west", west.maneuvers[0].instruction)

    def test_the_distances_still_add_up_with_part_edges_on_both_ends(self):
        g, _ = self.build()
        with self.no_server():
            out = R.route(self.midpoint(3, 4), self.midpoint(4, 5),
                          allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertAlmostEqual(sum(m.distance_m for m in out.maneuvers),
                               out.distance_m, delta=0.5)
        self.assertAlmostEqual(out.cum_m[-1], out.distance_m, delta=0.5)
        self.assertEqual(len(out.cum_m), len(out.points))
        # half of one block plus half of the next
        self.assertAlmostEqual(out.distance_m, BLOCK_EW, delta=2.0)

    def test_both_ends_on_one_block_is_the_length_between_them(self):
        g, _ = self.build()
        a = ((LATTICE[3][0] + LATTICE[4][0]) / 2,
             LATTICE[3][1] + STEP * 0.25)
        b = ((LATTICE[3][0] + LATTICE[4][0]) / 2,
             LATTICE[3][1] + STEP * 0.75)
        with self.no_server():
            out = R.route(a, b, allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertAlmostEqual(out.distance_m, BLOCK_EW / 2, delta=2.0)
        self.assertLess(R.haversine_m(out.points[0], a), 1.0)
        self.assertLess(R.haversine_m(out.points[-1], b), 1.0)

    def test_one_way_the_wrong_way_goes_round_the_block(self):
        segs = [(u, v, nm, (u, v) == (3, 4), c)
                for (u, v, nm, _, c) in LATTICE_SEGS]
        g, _ = self.build(segs=segs)
        a = (LATTICE[3][0], LATTICE[3][1] + STEP * 0.75)
        b = (LATTICE[3][0], LATTICE[3][1] + STEP * 0.25)
        with self.no_server():
            out = R.route(a, b, allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertGreater(out.distance_m, 600.0)

    def test_the_same_point_twice_is_said_plainly(self):
        g, _ = self.build()
        p = self.midpoint(3, 4)
        with self.no_server():
            out = R.route(p, p, allow_server=False)
        self.assertFalse(out.ok)
        self.assertIn("same place", out.reason)


class TestTurnRestrictions(LatticeTestCase):
    def test_without_the_ban_the_short_way_is_taken(self):
        g, idx = self.build()
        with self.no_server():
            out = R.route(LATTICE[3], LATTICE[1], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertAlmostEqual(out.distance_m, CORNER, delta=2.0)
        self.assertIn("Middle Street", [m.street for m in out.maneuvers])

    def test_a_banned_turn_is_not_taken(self):
        idx = self.edges()
        g, _ = self.build(bans=[(idx[(3, 4)], idx[(4, 1)])])
        with self.no_server():
            out = R.route(LATTICE[3], LATTICE[1], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        # 3->4->1 is banned, so it has to go 3->0->1 (or the long way round)
        self.assertNotIn("Middle Street",
                         [m.street for m in out.maneuvers])
        self.assertIn("First Avenue", [m.street for m in out.maneuvers])
        # 3->0->1 is the same length, so the ban costs nothing here; what it
        # must not do is leave the route unchanged
        self.assertAlmostEqual(out.distance_m, CORNER, delta=2.0)

    def test_the_ban_only_applies_to_the_approach_it_names(self):
        """Banning left from the west must not ban the same left from the east."""
        idx = self.edges()
        g, _ = self.build(bans=[(idx[(3, 4)], idx[(4, 1)])])
        with self.no_server():
            out = R.route(LATTICE[5], LATTICE[1], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertAlmostEqual(out.distance_m, CORNER, delta=2.0)
        self.assertIn("Middle Street", [m.street for m in out.maneuvers])

    def test_a_via_way_ban_needs_all_three_edges_in_order(self):
        idx = self.edges()
        g, _ = self.build(bans=[(idx[(3, 4)], idx[(4, 5)], idx[(5, 2)])])
        with self.no_server():
            straight = R.route(LATTICE[3], LATTICE[5], allow_server=False)
            banned = R.route(LATTICE[3], LATTICE[2], allow_server=False)
        # reaching 5 along the via way is still fine
        self.assertTrue(straight.ok)
        self.assertAlmostEqual(straight.distance_m, 2 * BLOCK_EW, delta=2.0)
        # but carrying on to 2 from there is not, so it must go round
        self.assertTrue(banned.ok, banned.reason)
        self.assertAlmostEqual(banned.distance_m, 2 * BLOCK_EW + BLOCK_NS,
                               delta=2.0)

    def test_a_ban_is_honoured_from_a_mid_block_start_too(self):
        """Re-routing mid-block is the case that matters, so test it there."""
        idx = self.edges()
        start = self.midpoint(3, 4)
        self.build()
        with self.no_server():
            free = R.route(start, LATTICE[1], allow_server=False)
        self.build(bans=[(idx[(3, 4)], idx[(4, 1)])])
        with self.no_server():
            out = R.route(start, LATTICE[1], allow_server=False)
        self.assertTrue(free.ok and out.ok, out.reason)
        # unbanned: carry on east half a block and turn left
        self.assertAlmostEqual(free.distance_m, BLOCK_EW / 2 + BLOCK_NS,
                               delta=2.0)
        # banned: turn round, back to the corner behind, and up
        self.assertAlmostEqual(out.distance_m,
                               BLOCK_EW / 2 + BLOCK_NS + BLOCK_EW, delta=2.0)

    def test_stranding_a_place_gets_a_route_and_a_warning_not_a_refusal(self):
        """A ban that cuts the only way in must not read as "no such road"."""
        segs = LATTICE_SEGS + [(8, 9, "Dead End", False, RESIDENTIAL)]
        nodes = LATTICE + [(LAT0 - STEP * 3, LON0 + STEP * 2)]
        path = Path(self._tmproot.name) / "strand.rg"
        idx = _pack_graph(path, nodes, segs)
        idx = _pack_graph(path, nodes, segs,
                          bans=[(idx[(5, 8)], idx[(8, 9)]),
                                (idx[(7, 8)], idx[(8, 9)])])
        os.environ[R.ENV_OVERRIDE] = str(path)
        R.reset()
        with self.no_server():
            out = R.route(LATTICE[0], nodes[9], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertIn("banned", out.reason)

    def test_two_searches_running_back_to_back_do_not_bleed(self):
        idx = self.edges()
        g, _ = self.build(bans=[(idx[(3, 4)], idx[(4, 1)])])
        with self.no_server():
            first = R.route(LATTICE[3], LATTICE[1], allow_server=False)
            second = R.route(LATTICE[5], LATTICE[1], allow_server=False)
            third = R.route(LATTICE[3], LATTICE[1], allow_server=False)
        self.assertEqual(first.points, third.points)
        self.assertNotEqual(first.points, second.points)


class TestGraphVersions(LatticeTestCase):
    def test_an_rgraph01_graph_still_loads_and_routes(self):
        """The old format has no segment grid; junction snapping is the answer."""
        g, _ = self.build(version=1)
        self.assertEqual(g.version, 1)
        self.assertIsNone(g.snap_edge(*self.midpoint(3, 4)))
        self.assertEqual(g.ntrie, 0)
        with self.no_server():
            out = R.route(self.midpoint(3, 4), LATTICE[2], allow_server=False)
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(out.source, "offline")
        # it snapped to a junction, so the line starts a half-block away -
        # which is exactly the gap the new format closes
        self.assertAlmostEqual(
            R.haversine_m(out.points[0], self.midpoint(3, 4)),
            BLOCK_EW / 2, delta=1.0)

    def test_the_new_format_announces_itself(self):
        g, _ = self.build(version=2)
        self.assertEqual(g.version, 2)
        self.assertIsNotNone(g.sgrid_seg)

    def test_a_graph_from_the_future_is_refused_by_name(self):
        bad = Path(self._tmproot.name) / "x.rg"
        bad.write_bytes(b"RGRAPH99" + struct.pack("<5I", 0, 0, 0, 0, 0))
        with self.assertRaises(ValueError) as caught:
            R.Graph(bad)
        self.assertIn("RGRAPH02", str(caught.exception))

    def test_a_truncated_new_format_graph_is_an_error_not_a_wrong_route(self):
        path = Path(self._tmproot.name) / "cut.rg"
        _pack_graph(path, LATTICE, LATTICE_SEGS)
        whole = path.read_bytes()
        path.write_bytes(whole[:len(whole) - 40])
        with self.assertRaises(Exception):
            R.Graph(path)


# Graph class numbers as the shipped .rg uses them (measured against OSM
# tags: 0 is motorway, 1 is motorway_link, 2 is mostly motorway).
FWY, RAMP, FWY2, STREET, RES = 0, 1, 2, 4, 11


def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


class TestFreewayManeuvers(RoutingTestCase):
    """Exits, ramps, merges, forks and sharp turns on small made-up roads.

    Before this the offline router had no rule that fired on leaving a
    freeway: an unnamed ramp at 13 degrees is neither a name change nor a
    turn, so she was told nothing. Measured over 300 trips against Valhalla:
    it spoke at 11 of 428 exits.
    """

    def route(self, nodes, segs, a, b):
        path = Path(self._tmproot.name) / "fwy.rg"
        _pack_graph(path, nodes, segs)
        os.environ[R.ENV_OVERRIDE] = str(path)
        R.reset()
        with self.no_server():
            out = R.route(a, b, allow_server=False)
        self.assertTrue(out.ok, out.reason)
        return out

    #  3 . . . . . .           9
    #  |                       |
    #  2 <------ on-ramp ----- 8   Oak Street
    #  |                       |
    #  1 --- exit ramp --> 4   7
    #  |                  / \
    #  0              6-/   \-5  Exit Street
    FREEWAY = [(37.800, -122.2700), (37.810, -122.2700), (37.820, -122.2700),
               (37.830, -122.2700), (37.815, -122.2685), (37.815, -122.2600),
               (37.810, -122.2685), (37.810, -122.2715), (37.8155, -122.2715),
               (37.825, -122.2715)]
    FREEWAY_SEGS = [(0, 1, "Test Freeway", True, FWY), (1, 2, "Test Freeway", True, FWY),
                    (2, 3, "Test Freeway", True, FWY), (1, 4, "", True, RAMP),
                    (4, 5, "Exit Street", False, RES), (4, 6, "Exit Street", False, RES),
                    (7, 8, "Oak Street", False, STREET), (8, 9, "Oak Street", False, STREET),
                    (8, 2, "", True, RAMP)]

    def said(self, out):
        return [(m.instruction, m.kind) for m in out.maneuvers]

    def test_leaving_the_freeway_is_an_exit_on_its_side(self):
        f = self.FREEWAY
        out = self.route(f, self.FREEWAY_SEGS, _mid(f[0], f[1]), _mid(f[4], f[5]))
        self.assertEqual(self.said(out), [
            ("head north on Test Freeway", "start"),
            ("take the exit on the right", "exit"),
            ("turn right onto Exit Street", "turn-right"),
            ("arrive at your destination", "arrive")])

    def test_driving_past_an_exit_says_nothing(self):
        f = self.FREEWAY
        out = self.route(f, self.FREEWAY_SEGS, _mid(f[0], f[1]), _mid(f[2], f[3]))
        self.assertEqual([k for _, k in self.said(out)], ["start", "arrive"])

    def test_an_on_ramp_is_taken_by_its_angle_then_merged_from(self):
        f = self.FREEWAY
        out = self.route(f, self.FREEWAY_SEGS, _mid(f[7], f[8]), _mid(f[2], f[3]))
        self.assertEqual(self.said(out), [
            ("head north on Oak Street", "start"),
            ("take the ramp on the right", "slight-right"),
            ("merge onto Test Freeway", "merge"),
            ("arrive at your destination", "arrive")])

    #        3   2        West Freeway / North Freeway
    #         \ /
    #          1
    #          |          Split Freeway
    #          0
    FORK = [(37.800, -122.2700), (37.810, -122.2700), (37.820, -122.2672),
            (37.820, -122.2728)]
    FORK_SEGS = [(0, 1, "Split Freeway", True, FWY), (1, 2, "North Freeway", True, FWY),
                 (1, 3, "West Freeway", True, FWY)]

    def test_a_freeway_split_is_keep_right_or_keep_left(self):
        f = self.FORK
        right = self.route(f, self.FORK_SEGS, _mid(f[0], f[1]), _mid(f[1], f[2]))
        self.assertIn(("keep right onto North Freeway", "slight-right"), self.said(right))
        left = self.route(f, self.FORK_SEGS, _mid(f[0], f[1]), _mid(f[1], f[3]))
        self.assertIn(("keep left onto West Freeway", "slight-left"), self.said(left))

    def test_an_exit_onto_an_unnamed_freeway_grade_ramp_is_still_an_exit(self):
        # Exit 9 off I-80 at Emeryville is exactly this in the shipped graph:
        # the ramp is class 2 like the freeway, and has no name.
        nodes = [(37.800, -122.2700), (37.810, -122.2700), (37.820, -122.2700),
                 (37.815, -122.2685), (37.815, -122.2600)]
        segs = [(0, 1, "Bay Freeway", True, FWY2), (1, 2, "Bay Freeway", True, FWY2),
                (1, 3, "", True, FWY2), (3, 4, "Frontage Road", False, STREET)]
        out = self.route(nodes, segs, _mid(nodes[0], nodes[1]), _mid(nodes[3], nodes[4]))
        self.assertIn(("take the exit on the right", "exit"), self.said(out))

    def test_the_same_freeway_changing_class_past_an_exit_says_nothing(self):
        # Measured: "keep left onto J Arthur Younger Freeway" where the freeway
        # just went on under its own name while a ramp left it.
        nodes = [(37.800, -122.2700), (37.810, -122.2700), (37.820, -122.2700),
                 (37.815, -122.2685)]
        segs = [(0, 1, "Bay Freeway", True, FWY), (1, 2, "Bay Freeway", True, FWY2),
                (1, 3, "", True, FWY2)]
        out = self.route(nodes, segs, _mid(nodes[0], nodes[1]), _mid(nodes[1], nodes[2]))
        self.assertEqual([k for _, k in self.said(out)], ["start", "arrive"])

    def test_losing_the_name_is_not_an_exit_unless_the_name_goes_on_beside(self):
        # Two unnamed-and-other branches: nothing says which one is the
        # freeway, so it is a fork, not an exit.
        nodes = [(37.800, -122.2700), (37.810, -122.2700), (37.820, -122.2715),
                 (37.815, -122.2685)]
        segs = [(0, 1, "Bay Freeway", True, FWY2), (1, 2, "Other Freeway", True, FWY2),
                (1, 3, "", True, FWY2)]
        out = self.route(nodes, segs, _mid(nodes[0], nodes[1]), _mid(nodes[1], nodes[3]))
        self.assertNotIn("exit", [k for _, k in self.said(out)])
        self.assertIn(("keep right", "slight-right"), self.said(out))

    def test_a_hairpin_is_a_sharp_turn(self):
        nodes = [(37.800, -122.2700), (37.810, -122.2700), (37.805, -122.2665),
                 (37.820, -122.2700)]
        segs = [(0, 1, "A Street", False, RES), (1, 3, "A Street", False, RES),
                (1, 2, "B Street", False, RES)]
        out = self.route(nodes, segs, _mid(nodes[0], nodes[1]), _mid(nodes[1], nodes[2]))
        self.assertIn(("make a sharp right onto B Street", "sharp-right"), self.said(out))

    def test_every_offline_maneuver_has_a_kind(self):
        f = self.FREEWAY
        for a, b in ((_mid(f[7], f[8]), _mid(f[2], f[3])),
                     (_mid(f[0], f[1]), _mid(f[4], f[6]))):
            out = self.route(f, self.FREEWAY_SEGS, a, b)
            for m in out.maneuvers:
                self.assertIn(m.kind, R.KINDS, m.instruction)


# ------------------------------------------- the real graph, when installed

GRAPH = R.graph_path()
NEEDS_GRAPH = unittest.skipUnless(
    GRAPH, "no .rg road graph installed - offline routing not exercised "
           "(set AGEDASHII_ROUTE_GRAPH or install bayarea.rg)")

# Oakland City Center -> Fremont, the pair the spike was measured on.
OAKLAND = (37.8044, -122.2712)
FREMONT = (37.5485, -121.9886)


@NEEDS_GRAPH
class TestOfflineRouting(RoutingTestCase):
    """Only runs where a graph is installed; the CI box has none."""

    @classmethod
    def setUpClass(cls):
        cls.g = R.Graph(GRAPH)

    def test_snapping_lands_close_to_the_asked_for_point(self):
        node, metres = self.g.snap(*OAKLAND)
        self.assertGreaterEqual(node, 0)
        self.assertLess(metres, 200)

    def test_a_route_is_computed_offline_without_touching_the_network(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(out.source, "offline")
        self.assertTrue(out.offline)
        self.assertIn(Path(GRAPH).name, out.source_detail)

    def test_the_eta_is_californias_not_the_worlds(self):
        # nvii chose California road speeds ("31 realisticc i guess").
        # Valhalla on the California tiles says 31.5 min; the old SPEED_KMH,
        # fitted to a server using world-average speeds, said 39.3; the table
        # measured by tools/navi-routing/fit_speed_kmh.py says 33.9. One speed
        # per class cannot hit 31.5 exactly - but it must not drift back to 39.
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        self.assertTrue(out.ok, out.reason)
        self.assertTrue(28 * 60 <= out.duration_s <= 36 * 60, out.duration_s / 60)

    def test_the_route_is_plausible(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        straight = R.haversine_m(OAKLAND, FREMONT)
        # A road route is longer than the crow flies, but not absurdly so.
        self.assertGreater(out.distance_m, straight)
        self.assertLess(out.distance_m, straight * 1.6)
        self.assertGreater(out.duration_s, 60)
        self.assertGreater(len(out.points), 50)

    def test_the_drawn_line_starts_and_ends_where_it_was_asked_to(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        self.assertLess(R.haversine_m(out.points[0], OAKLAND), 200)
        self.assertLess(R.haversine_m(out.points[-1], FREMONT), 200)

    def test_the_turn_list_and_the_total_agree(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        self.assertGreater(len(out.maneuvers), 2)
        self.assertTrue(out.maneuvers[0].instruction.startswith("head "))
        self.assertIn("arrive", out.maneuvers[-1].instruction)
        # Each maneuver's run is the distance to the next one; they must sum
        # to the whole route, or the guidance counts down to the wrong place.
        self.assertAlmostEqual(sum(m.distance_m for m in out.maneuvers),
                               out.distance_m, delta=1.0)
        self.assertAlmostEqual(out.cum_m[-1], out.distance_m, delta=1.0)

    def test_maneuvers_are_in_order_along_the_route(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        cums = [m.cum_m for m in out.maneuvers]
        self.assertEqual(cums, sorted(cums))
        for m in out.maneuvers:
            self.assertLess(m.index, len(out.points))
            self.assertEqual(out.points[m.index], m.at)

    def test_a_point_outside_the_extract_is_named_as_such(self):
        """Not "routing failed" - the fix is a different graph, and it says so."""
        with mock.patch.object(R, "graph_path", lambda: GRAPH):
            out = R.route(OAKLAND, (48.8584, 2.2945), allow_server=False)
        self.assertFalse(out.ok)
        self.assertEqual(out.source, "offline")
        self.assertIn("outside", out.reason)

    def test_two_routes_at_once_do_not_corrupt_each_other(self):
        """astar_fast shares scratch buffers; the lock is what makes it safe."""
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            expected = R.route(OAKLAND, FREMONT)
            results = [None, None]

            def run(i, a, b):
                results[i] = R.route(a, b)

            threads = [threading.Thread(target=run, args=(0, OAKLAND, FREMONT)),
                       threading.Thread(target=run, args=(1, FREMONT, OAKLAND))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertTrue(results[0].ok and results[1].ok)
        self.assertAlmostEqual(results[0].distance_m, expected.distance_m,
                               delta=1.0)
        self.assertEqual(results[0].points, expected.points)

    def test_guidance_works_against_a_real_route(self):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(OAKLAND, FREMONT)
        mid = out.points[len(out.points) // 2]
        m, to_go, off = R.next_maneuver(out, mid)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(to_go, 0.0)
        self.assertLess(off, 1.0)
        left_m, left_s = R.remaining(out, mid)
        self.assertLess(left_m, out.distance_m)
        self.assertGreater(left_m, 0)

    def test_rerouting_from_the_middle_of_a_block_starts_there(self):
        """The case that matters. A rider who has missed a turn is not at a
        junction, and the new line has to begin under them."""
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            base = R.route(OAKLAND, FREMONT)
            here = base.points[len(base.points) // 3]
            again = R.route(here, FREMONT)
        self.assertTrue(again.ok, again.reason)
        if self.g.version >= 2:
            self.assertLess(R.haversine_m(again.points[0], here), 2.0)
        # and the two agree on what is left, which they cannot if one of
        # them quietly started somewhere else
        left, _ = R.remaining(base, here)
        self.assertAlmostEqual(again.distance_m, left,
                               delta=max(250.0, left * 0.05))


@NEEDS_GRAPH
class TestRealJunctions(RoutingTestCase):
    """Junctions in the shipped graph whose right answer is known.

    Each is one Valhalla (the server path) names the same way: the University
    Avenue on-ramp to I-80 ("Take the I 80 West ramp on the right"), I-80
    exit 9 at Powell Street ("Take exit 9 on the right"), and two turns
    Valhalla calls sharp - Doolittle Drive onto Davis Street (138 degrees) and
    San Benito Road onto Visitacion Avenue (143).
    """

    def route(self, a, b):
        with mock.patch.object(R, "graph_path", lambda: GRAPH), self.no_server():
            out = R.route(a, b, allow_server=False)
        self.assertTrue(out.ok, out.reason)
        return out

    def test_berkeley_to_emeryville_on_ramp_merge_and_exit(self):
        out = self.route((37.8699, -122.2953), (37.8385, -122.2930))
        said = [(m.instruction, m.kind) for m in out.maneuvers]
        ramp = said.index(("take the ramp on the right", "slight-right"))
        merge = said.index(("merge onto Eastshore Freeway", "merge"))
        exit_ = said.index(("take the exit on the right", "exit"))
        frontage = [i for i, (words, _) in enumerate(said) if "Frontage Road" in words][0]
        self.assertLess(ramp, merge)
        self.assertLess(merge, exit_)
        self.assertLess(exit_, frontage)
        self.assertLess(R.haversine_m(out.maneuvers[ramp].at, (37.86667, -122.30448)), 50)

    def test_doolittle_onto_davis_is_a_sharp_right(self):
        out = self.route((37.715378, -122.186418), (37.716587, -122.185716))
        self.assertEqual([(m.instruction, m.kind) for m in out.maneuvers][1],
                         ("make a sharp right onto Davis Street", "sharp-right"))

    def test_san_benito_onto_visitacion_is_a_sharp_right(self):
        out = self.route((37.68021, -122.403167), (37.680629, -122.40314))
        self.assertEqual([(m.instruction, m.kind) for m in out.maneuvers][1],
                         ("make a sharp right onto Visitacion Avenue", "sharp-right"))

    def test_a_long_route_has_a_kind_everywhere(self):
        out = self.route(OAKLAND, FREMONT)
        self.assertEqual(out.maneuvers[0].kind, "start")
        self.assertEqual(out.maneuvers[-1].kind, "arrive")
        for m in out.maneuvers:
            self.assertIn(m.kind, R.KINDS, m.instruction)


@NEEDS_GRAPH
class TestOfflineRestrictions(RoutingTestCase):
    """Turn restrictions, against whatever graph is actually installed."""

    @classmethod
    def setUpClass(cls):
        cls.g = R.Graph(GRAPH)
        cls.banned = {}
        if cls.g.version >= 2:
            stack = [(0, ())]
            while stack:
                t, pre = stack.pop()
                for c in range(cls.g.tr_off[t], cls.g.tr_off[t + 1]):
                    e, nxt = cls.g.tr_edge[c], cls.g.tr_next[c]
                    if nxt == cls.g.BANNED:
                        seq = pre + (e,)
                        cls.banned.setdefault(seq[0], []).append(seq)
                    else:
                        stack.append((nxt, pre + (e,)))

    def setUp(self):
        super().setUp()
        if self.g.version < 2:
            self.skipTest("the installed graph predates turn restrictions "
                          "(RGRAPH01) - rebuild it to exercise them")

    def violations(self, edges):
        found = []
        for i, k in enumerate(edges):
            for seq in self.banned.get(k, ()):
                if tuple(edges[i:i + len(seq)]) == seq:
                    found.append(seq)
        return found

    def probe(self, seq, restrictions=True):
        """Route from just before a banned sequence to just after it."""
        src = _tail_node(self.g, seq[0])
        dst = self.g.to[seq[-1]]
        if src == dst:
            return None
        _cost, edges, _s, _n = self.g.search(
            [(src, 0.0)], {dst: (0.0, -1)},
            (self.g.xy[2 * dst], self.g.xy[2 * dst + 1]), "time",
            restrictions=restrictions)
        return edges

    def sample(self, n=25):
        import random
        keys = sorted(self.banned)
        return [self.banned[k][0]
                for k in random.Random(4).sample(keys, min(n, len(keys)))]

    def test_the_installed_graph_actually_carries_restrictions(self):
        self.assertGreater(self.g.ntrie, 100)
        self.assertGreater(len(self.banned), 100)

    def test_no_route_takes_a_turn_the_map_forbids(self):
        checked = 0
        for seq in self.sample():
            edges = self.probe(seq)
            if edges is None:
                continue
            checked += 1
            self.assertEqual(self.violations(edges), [],
                             f"the search drove the banned sequence {seq}")
        self.assertGreater(checked, 10, "too few cases were exercised")

    def test_and_ignoring_them_would_have_taken_some_of_those_turns(self):
        """Otherwise the test above only proves the sample was harmless."""
        took = sum(1 for seq in self.sample()
                   if self.violations(self.probe(seq, restrictions=False) or []))
        self.assertGreater(took, 0)

    def test_a_real_mid_block_fix_lands_on_the_road_not_the_corner(self):
        mid = None
        for sid in range(2000, 80000):
            a, b = self.g.seg_off[sid], self.g.seg_off[sid + 1]
            if b - a < 3:
                continue
            ends = ((self.g.geom[2 * a] / 1e7, self.g.geom[2 * a + 1] / 1e7),
                    (self.g.geom[2 * (b - 1)] / 1e7,
                     self.g.geom[2 * (b - 1) + 1] / 1e7))
            if R.haversine_m(*ends) < 300:
                continue
            m = (a + b) // 2
            here = (self.g.geom[2 * m] / 1e7, self.g.geom[2 * m + 1] / 1e7)
            if self.g.snap_edge(*here) is not None:
                mid = here
                break
        if mid is None:                                # pragma: no cover
            self.skipTest("no long segment to stand in the middle of")
        off = (mid[0] + 8.0 / 111132.0, mid[1])        # 8 m to one side
        _node, junction_m = self.g.snap(*off)
        self.assertLess(self.g.snap_edge(*off).dist_m, 12.0)
        self.assertGreater(junction_m, 100.0)


@NEEDS_GRAPH
class TestHeadingAwareSnap(RoutingTestCase):
    """The divided-carriageway case, which distance alone cannot decide.

    Both fixtures are real places in the shipped Bay Area graph, found by
    sampling for two one-way segments running within 150 degrees of
    opposite and less than 90 m apart - which is what a divided highway
    looks like in the data. The point tested is the midpoint between them,
    where a GPS fix is genuinely ambiguous and the snap previously picked
    whichever was nearer by centimetres.
    """

    @classmethod
    def setUpClass(cls):
        cls.g = R.Graph(GRAPH)

    # (where, heading -> the segment you are actually on)
    CARRIAGEWAYS = [
        ("I-880 Oakland", 37.794887, -122.277091, {29: 679736, 201: 377550}),
        ("I-80 Emeryville", 37.837115, -122.302650, {86: 310933, 270: 490354}),
    ]

    def test_heading_picks_the_carriageway_you_are_on(self):
        for label, lat, lon, expected in self.CARRIAGEWAYS:
            for heading, want in expected.items():
                with self.subTest(place=label, heading=heading):
                    snap = self.g.snap_edge(lat, lon, heading=heading)
                    self.assertIsNotNone(snap)
                    self.assertEqual(snap.seg, want)

    def test_without_a_heading_nothing_changes(self):
        """The whole point of the default: existing callers are untouched.

        Every caller but the live navigator passes no heading, so the old
        path has to stay byte-identical rather than merely similar.
        """
        for label, lat, lon, _ in self.CARRIAGEWAYS:
            with self.subTest(place=label):
                a = self.g.snap_edge(lat, lon)
                b = self.g.snap_edge(lat, lon, heading=None)
                self.assertEqual((a.seg, a.i, a.t, a.dist_m),
                                 (b.seg, b.i, b.t, b.dist_m))

    def test_a_heading_never_reaches_past_a_clearly_nearer_road(self):
        """It re-orders a tie; it does not go shopping.

        The penalty is deliberately smaller than the distance to a
        different road, so even the worst possible heading may only move
        the answer by about the width of a junction - not onto the next
        street over.
        """
        for lat, lon in [OAKLAND, (37.7749, -122.4194)]:
            base = self.g.snap_edge(lat, lon)
            for heading in (0, 90, 180, 270):
                with self.subTest(at=(lat, lon), heading=heading):
                    got = self.g.snap_edge(lat, lon, heading=heading)
                    self.assertLess(got.dist_m - base.dist_m,
                                    R.HEADING_BAND_M)


def _tail_node(g, k):
    """Which node directed edge k leaves from (binary search in the CSR)."""
    lo, hi = 0, g.n
    while lo < hi:
        mid = (lo + hi) // 2
        if g.off[mid] <= k:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


if __name__ == "__main__":         # pragma: no cover
    unittest.main()
