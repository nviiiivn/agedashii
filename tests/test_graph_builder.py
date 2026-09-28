"""The road-graph builder end to end: a small hand-made OSM file in, a graph
the router reads out, and the maneuvers the rider hears.

tools/navi-routing/build_graph.py is the real builder (recovered and proved
byte-identical to the shipped bayarea.rg). These run it on a few dozen nodes
so what it writes and what routing.py makes of it are tested together.

Why RGRAPH03: a graph class is a SPEED BUCKET, not a highway type, so a ramp
with a posted speed is invisible to rules that look for bucket 1 - 11% of the
Bay Area's motorway_links and 91% of its trunk_links. RGRAPH03 also carries
each segment's real highway class and a roundabout flag.
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agedashii_tofu import routing as R  # noqa: E402


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_graph", REPO / "tools" / "navi-routing" / "build_graph.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _esc(v):
    return "".join(f"%{ord(ch):x}%" if ch in " ,=%@" else ch for ch in v)


def _opl(nodes, ways):
    lines = [f"n{i} v1 dV c1 t2026-01-01T00:00:00Z i1 u T x{lon} y{lat}"
             for i, (lat, lon) in sorted(nodes.items())]
    for wid, (refs, tags) in sorted(ways.items()):
        t = ",".join(f"{k}={_esc(v)}" for k, v in tags.items())
        lines.append(f"w{wid} v1 dV c1 t2026-01-01T00:00:00Z i1 u T{t} "
                     "N" + ",".join(f"n{r}" for r in refs))
    return "\n".join(lines) + "\n"


# A freeway (both carriageways), an exit ramp posted at 25 mph, a trunk_link
# on-ramp from Oak Street, and a four-arm roundabout east of the exit, all
# tied together by local roads so the whole thing is one connected graph.
NODES = {
    1: (37.800, -122.2700), 2: (37.810, -122.2700), 3: (37.820, -122.2700),
    4: (37.830, -122.2700), 11: (37.830, -122.2703), 12: (37.820, -122.2703),
    13: (37.810, -122.2703), 14: (37.800, -122.2703), 20: (37.7990, -122.2701),
    5: (37.815, -122.2685), 6: (37.815, -122.2600),
    31: (37.815, -122.2575), 32: (37.8146, -122.2570), 33: (37.815, -122.2565),
    34: (37.8154, -122.2570),
    41: (37.8200, -122.2570), 42: (37.815, -122.2500), 43: (37.8100, -122.2570),
    44: (37.7990, -122.2570),
    51: (37.8100, -122.2720), 52: (37.8155, -122.2720), 53: (37.8250, -122.2720),
    54: (37.8310, -122.2720),
}
FWY = {"highway": "motorway", "name": "Test Freeway", "maxspeed": "65 mph"}
RES = {"highway": "residential"}
WAYS = {
    100: ([1, 2, 3, 4], FWY), 101: ([11, 12, 13, 14], FWY),
    102: ([2, 5], {"highway": "motorway_link", "maxspeed": "25 mph"}),
    103: ([5, 6, 31], dict(RES, name="Exit Street")),
    104: ([31, 32, 33, 34, 31], {"highway": "tertiary", "junction": "roundabout",
                                 "name": "Circle"}),
    105: ([34, 41], dict(RES, name="North Road")),
    106: ([33, 42], dict(RES, name="East Road")),
    107: ([32, 43, 44], dict(RES, name="South Road")),
    108: ([44, 20, 1], dict(RES, name="Back Road")),
    109: ([14, 20], dict(RES, name="Back Road")),
    110: ([4, 11], dict(RES, name="Turnaround")),
    111: ([51, 52, 53, 54, 11], dict(RES, name="Oak Street")),
    112: ([20, 51], dict(RES, name="Oak Street")),
    113: ([52, 3], {"highway": "trunk_link", "oneway": "yes"}),
    114: ([41, 54], dict(RES, name="North Road")),
    115: ([42, 54], dict(RES, name="East Road")),
}


def _mid(a, b):
    return ((NODES[a][0] + NODES[b][0]) / 2, (NODES[a][1] + NODES[b][1]) / 2)


class _Built(unittest.TestCase):
    FORMAT = 3

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        opl = Path(cls.tmp.name) / "test.osm.opl"
        opl.write_text(_opl(NODES, WAYS))
        cls.path = Path(cls.tmp.name) / "test.rg"
        with contextlib.redirect_stdout(io.StringIO()):
            _builder().main(str(opl), str(cls.path), fmt=cls.FORMAT)
        cls.g = R.Graph(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def route(self, a, b):
        R.reset()
        with mock.patch.dict(os.environ, {R.ENV_OVERRIDE: str(self.path)}), \
                mock.patch.object(R, "_post_json", side_effect=AssertionError("no server")):
            out = R.route(a, b, allow_server=False)
        self.assertTrue(out.ok, out.reason)
        return [(m.instruction, m.kind) for m in out.maneuvers]

    def segs_named(self, name):
        return [s for s in range(self.g.s) if self.g.name(s) == name]


class TheNewFormat(_Built):
    def test_it_is_rgraph03(self):
        self.assertEqual(self.g.version, 3)

    def test_every_segment_knows_its_real_highway(self):
        ramp = next(s for s in range(self.g.s)
                    if self.g.highway(s) == R.CLASSES.index("motorway_link"))
        k = next(k for k in range(self.g.e) if self.g.seg[k] & 0x7FFFFFFF == ramp)
        # the whole problem, in one assertion: its speed bucket is not 1
        self.assertNotEqual(self.g.cls[k], 1)
        self.assertEqual(self.g.highway(self.segs_named("Oak Street")[0]),
                         R.CLASSES.index("residential"))

    def test_the_ring_is_flagged_and_the_arms_are_not(self):
        self.assertTrue(all(self.g.roundabout(s) for s in self.segs_named("Circle")))
        self.assertFalse(any(self.g.roundabout(s) for s in self.segs_named("East Road")))


class HiddenRamps(_Built):
    def test_a_posted_speed_exit_is_still_an_exit(self):
        said = self.route(_mid(1, 2), _mid(5, 6))
        self.assertIn(("take the exit on the right", "exit"), said)

    def test_a_trunk_link_on_ramp_is_a_ramp_and_then_a_merge(self):
        said = self.route(_mid(51, 52), _mid(3, 4))
        self.assertIn(("take the ramp on the right", "slight-right"), said)
        self.assertIn(("merge onto Test Freeway", "merge"), said)


class Roundabouts(_Built):
    # Counter-clockwise, as in the US: in from the west, the exits come
    # south, then east, then north.
    def test_the_second_exit(self):
        said = self.route(_mid(5, 6), _mid(33, 42))
        self.assertIn(("enter the roundabout and take the 2nd exit onto East Road",
                       "roundabout"), said)
        self.assertIn(("exit the roundabout onto East Road", None), said)

    def test_the_first_and_third_exits(self):
        self.assertIn(("enter the roundabout and take the 1st exit onto South Road",
                       "roundabout"), self.route(_mid(5, 6), _mid(43, 44)))
        self.assertIn(("enter the roundabout and take the 3rd exit onto North Road",
                       "roundabout"), self.route(_mid(5, 6), _mid(34, 41)))

    def test_nothing_is_said_inside_the_ring(self):
        said = self.route(_mid(5, 6), _mid(34, 41))
        self.assertEqual([k for _, k in said], ["start", "roundabout", None, "arrive"])


class TheOldFormatStillBuilds(_Built):
    """--format 2 is what reproduces the shipped bayarea.rg byte for byte."""
    FORMAT = 2

    def test_it_is_rgraph02_with_no_highway_or_flags(self):
        self.assertEqual(self.g.version, 2)
        self.assertIsNone(self.g.highway(0))
        self.assertFalse(self.g.roundabout(0))

    def test_it_still_routes(self):
        said = self.route(_mid(1, 2), _mid(5, 6))
        self.assertEqual(said[0][1], "start")
        self.assertEqual(said[-1][1], "arrive")


if __name__ == "__main__":
    unittest.main()
