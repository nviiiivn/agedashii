"""The offline places list: finding a museum or a park with no signal.

Her report: search "seems to not find normal shit or places like chabot space
science center or parks or anything". The road graph holds only streets and
the online geocoder only towns, so a separate list is built by
tools/build_places.py. These tests build a tiny one with the real builder
and search it with the real search, so the two cannot drift apart.
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO = APP_DIR.parent.parent
sys.path[:0] = [str(APP_DIR), str(REPO / "packages" / "agedashii-washi"),
                str(REPO / "packages" / "agedashii-qi")]

from agedashii_navi import places                                   # noqa: E402
from agedashii_navi.pages import routes as routes_page              # noqa: E402

_spec = importlib.util.spec_from_file_location("build_places", REPO / "tools" / "build_places.py")
build_places = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_places)

OAKLAND = (37.8044, -122.2712)
ROWS = [
    ("Chabot Space & Science Center Amphitheater", "theatre", 37.8187, -122.1803, []),
    ("Chabot Space and Science Center", "museum", 37.8188, -122.1805, []),
    # The same museum drawn a second time, as a building outline 30 m away.
    ("Chabot Space and Science Center", "museum", 37.8190, -122.1807, []),
    ("Mt. Diablo Coal Field", "memorial", 37.9500, -121.8500, []),
    ("Mount Diablo", "peak", 37.8816, -121.9142, []),
    ("Starbucks Coffee", "cafe", 37.8000, -122.2700, ["Starbucks"]),
    ("Café Rouge", "restaurant", 37.8700, -122.2700, []),
    ("Tilden Regional Park", "protected area", 37.8950, -122.2450, []),
]


class TheOfflinePlacesList(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, places.PLACES_FILENAME)
        self.kept = build_places.write_db(iter(ROWS), self.path, meta={"source": "test"})

    def tearDown(self):
        self.dir.cleanup()

    def search(self, text):
        return places.search_places(text, near=OAKLAND, path=self.path)[0]

    def test_chabot_is_found_with_no_signal_and_the_museum_ranks_first(self):
        rows = self.search("chabot space science")
        self.assertEqual(rows[0]["name"], "Chabot Space and Science Center")
        self.assertEqual(rows[0]["detail"], "museum")
        self.assertEqual(rows[0]["kind"], "poi")
        self.assertAlmostEqual(rows[0]["lat"], 37.8188, places=4)

    def test_the_ampersand_spelling_finds_it_too(self):
        self.assertEqual(self.search("Chabot Space & Science Center")[0]["name"],
                         "Chabot Space and Science Center")

    def test_a_park_is_found(self):
        self.assertEqual(self.search("tilden")[0]["name"], "Tilden Regional Park")

    def test_mt_finds_mount_and_ranks_the_peak_first(self):
        self.assertEqual(self.search("mt diablo")[0]["name"], "Mount Diablo")

    def test_a_brand_finds_the_branch(self):
        self.assertEqual(self.search("starbucks")[0]["name"], "Starbucks Coffee")

    def test_accents_do_not_matter(self):
        self.assertEqual(self.search("cafe rouge")[0]["name"], "Café Rouge")

    def test_one_letter_is_not_a_search(self):
        # One letter matched a quarter of the real list and took ~0.5 s on the Pi.
        self.assertEqual(self.search("c"), [])

    def test_the_same_place_drawn_twice_is_one_place(self):
        self.assertEqual(self.kept, len(ROWS) - 1)
        names = [r["name"] for r in self.search("chabot space and science center")]
        self.assertEqual(names.count("Chabot Space and Science Center"), 1)

    def test_it_says_how_many_places_it_holds(self):
        self.assertEqual(places.search_places("zzzz", path=self.path), ([], len(ROWS) - 1))

    def test_no_list_installed_is_an_answer_not_a_crash(self):
        with mock.patch.object(places, "places_path", return_value=None):
            self.assertEqual(places.search_places("chabot"), ([], 0))


class StreetResultsAreWhereTheStreetIs(unittest.TestCase):
    """Every offline street result used to be at a place like (-10933, 4281).

    segment_point decoded the graph's SEGMENT geometry with the NODE encoding
    (origin + scaled offset, routing.py:1370-1371). Segment geometry is
    absolute degrees x 1e7, lat then lon (routing.py:618-645, 1122). So every
    street she chose from the device search routed to nowhere - and no test
    had ever looked at the coordinates.
    """

    class _Graph:
        # Two segments; the second is three points along Grand Avenue, Oakland.
        seg_off = [0, 2, 5]
        geom = [378000000, -1222700000, 378001000, -1222701000,
                378109000, -1222560000, 378112000, -1222545000, 378115000, -1222530000]
        olat, olon, mx, my = 37.94, -121.95, 87862.3, 110996.4   # the NODE encoding - must not be used

    def test_the_middle_point_of_a_segment_in_degrees(self):
        lat, lon = places.segment_point(self._Graph(), 1)
        self.assertAlmostEqual(lat, 37.8112, places=6)
        self.assertAlmostEqual(lon, -122.2545, places=6)

    def test_grand_avenue_near_oakland_is_in_oakland_on_the_real_graph(self):
        rg = REPO / "assets" / "maps" / "bayarea.rg"
        if not rg.is_file():
            self.skipTest("the 86 MB graph is not in this checkout")
        from agedashii_washi import routing
        rows = places.search_streets("grand avenue", graph=routing.Graph(str(rg)), near=OAKLAND)
        self.assertTrue(rows)
        # The shipped graph reaches past the Bay Area (sampled: 36.8-38.9 N,
        # -123.6 to -121.1), so a result may be far away - but it must be real
        # Northern California degrees, and the nearest one must be in Oakland.
        for row in rows:
            with self.subTest(street=row["name"]):
                self.assertTrue(36.0 <= row["lat"] <= 39.5 and -124.5 <= row["lon"] <= -120.5,
                                (row["lat"], row["lon"]))
        self.assertLess(rows[0]["dist_m"], 5000, rows[0])


class WhatTheBoxSaysWhenNothingIsFound(unittest.TestCase):
    """An empty list reads as a broken app. These say why, with the real count."""

    def test_names_the_exact_number_of_places_searched(self):
        text = routes_page.nothing_found("zzzq", 83177)
        self.assertIn("not a street", text)
        self.assertIn("83,177 places", text)

    def test_says_when_the_list_is_not_installed(self):
        self.assertIn("not installed", routes_page.nothing_found("chabot", 0))

    def test_one_letter_asks_for_more(self):
        self.assertEqual(routes_page.nothing_found("c", 83177), "type at least two letters")


class TheBuilder(unittest.TestCase):
    def test_builder_and_app_split_names_the_same_way(self):
        self.assertIs(build_places.norm_tokens, places.norm_tokens)

    def test_what_counts_as_a_place(self):
        self.assertEqual(build_places.kind_of({"tourism": "museum"}), "museum")
        self.assertEqual(build_places.kind_of({"amenity": "fast_food"}), "fast food")
        self.assertEqual(build_places.kind_of({"boundary": "protected_area"}), "protected area")
        self.assertEqual(build_places.kind_of({"building": "yes"}), "building")
        self.assertIsNone(build_places.kind_of({"amenity": "bench"}))
        self.assertIsNone(build_places.kind_of({"highway": "residential"}))   # streets are the graph's
        self.assertIsNone(build_places.kind_of({"public_transport": "stop_position"}))

    def test_an_area_is_placed_at_its_middle(self):
        square = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
        self.assertEqual(build_places.centre(square), (1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
