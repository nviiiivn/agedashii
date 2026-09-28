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
import sqlite3
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


class HerSevenSearches(unittest.TestCase):
    """The seven queries she typed on the phone, against the REAL list, from
    Oakland. Two bugs: distance barely counted (Sacramento's IKEA at 107 km
    beat Emeryville's at 3.5; a Japanese garden 93 km out came first), and a
    landmark named with its city ("berkeley stadium") found nothing, because
    OSM names the stadium, not the city. NAVI_PLACES_DB points at a list to
    check; otherwise the shipped one."""

    OAKLAND = (37.8044, -122.2712)

    @classmethod
    def setUpClass(cls):
        cls.db = os.environ.get("NAVI_PLACES_DB") or str(REPO / "assets" / "maps" / places.PLACES_FILENAME)
        if not os.path.isfile(cls.db):
            raise unittest.SkipTest("no places list installed in this checkout")

    def first(self, text, n=1):
        rows = places.search_places(text, near=self.OAKLAND, path=self.db)[0]
        self.assertTrue(rows, f"{text!r} found nothing")
        return rows[:n]

    def test_japanese_garden_is_lakesides_first(self):
        top = self.first("japanese garden")[0]
        self.assertLess(top["dist_m"], 1500, top)          # Lakeside Park, 0.7 mi - not one 93 km out

    def test_de_young_museum(self):
        self.assertEqual(self.first("de young museum")[0]["name"], "de Young Museum")

    def test_pacifica_beach_is_the_beach_not_the_hotel(self):
        self.assertEqual(self.first("pacifica beach")[0]["name"], "Pacifica State Beach")

    def test_pyramid_to_moses(self):
        self.assertEqual(self.first("pyramid to moses")[0]["name"], "Pyramid to Moses")

    def test_ikea_is_emeryville_first(self):
        top = self.first("ikea")[0]
        self.assertEqual(top["name"].lower(), "ikea")
        self.assertLess(top["dist_m"], 4000, top)          # Emeryville, 2.2 mi - not Sacramento's

    def test_uss_hornet(self):
        self.assertEqual(self.first("uss hornet")[0]["name"], "USS Hornet (CV 12)")

    def test_berkeley_stadium_finds_memorial_stadium(self):
        names = [r["name"] for r in self.first("berkeley stadium", n=3)]
        self.assertIn("California Memorial Stadium", names)

    def test_chabot_is_the_museum_not_its_car_park(self):
        # Her first complaint. A parking garage is part of a place, not a
        # destination, however big its outline.
        self.assertEqual(self.first("chabot space science")[0]["name"], "Chabot Space and Science Center")

    def test_ikea_is_the_store_before_its_car_park(self):
        names = [r["name"] for r in self.first("ikea", n=2)]
        self.assertNotIn("Ikea Parking Lot", names)

    def test_a_city_name_is_the_city(self):
        top = self.first("berkeley")[0]
        self.assertEqual((top["name"], top["detail"]), ("Berkeley", "city"))


class HowRankingWorks(unittest.TestCase):
    """The ranking rules, on a list small enough to see the reason in."""

    NEAR = (37.8044, -122.2712)

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, places.PLACES_FILENAME)

    def tearDown(self):
        self.dir.cleanup()

    def build(self, rows):
        build_places.write_db(iter(rows), self.path)

    def names(self, text):
        return [r["name"] for r in places.search_places(text, near=self.NEAR, path=self.path)[0]]

    def test_a_perfect_name_100_km_away_never_beats_a_poor_one_1_km_away(self):
        self.build([
            ("Rose Garden", "garden", 38.7044, -122.2712, [], 5e6, True, ["Far Town", None]),       # ~100 km, big, notable
            ("Old Rose Garden Tea Room", "cafe", 37.8134, -122.2712, [], 0.0, False, [None, None]),  # 1 km
        ])
        self.assertEqual(self.names("rose garden")[0], "Old Rose Garden Tea Room")

    def test_a_town_word_needs_a_name_word_too(self):
        self.build([("Memorial Stadium", "stadium", 37.87, -122.25, [], 30000.0, True, ["Berkeley", None])])
        self.assertEqual(self.names("berkeley stadium"), ["Memorial Stadium"])
        self.assertEqual(self.names("berkeley"), [])       # "berkeley" is not in its name

    def test_a_word_only_in_an_alternative_name_counts_for_less(self):
        self.build([
            ("Spees Building", "building", 37.8187, -122.1805, ["Chabot Science Center Spees Building"], 3000.0, False, [None, None]),
            ("Chabot Science Center", "museum", 37.8188, -122.1804, [], 0.0, False, [None, None]),
        ])
        self.assertEqual(self.names("chabot science")[0], "Chabot Science Center")

    def test_the_kind_she_names_wins_a_near_tie(self):
        self.build([
            ("Pacifica Beach Hotel", "hotel", 37.62, -122.49, [], 9000.0, False, ["Pacifica", None]),
            ("Pacifica State Beach", "beach", 37.62, -122.49, [], 9000.0, False, ["Pacifica", None]),
        ])
        self.assertEqual(self.names("pacifica beach")[0], "Pacifica State Beach")


class ThePlacesListSaysWhetherItIsThere(unittest.TestCase):
    """Where-to states, before any search, whether the offline list is on the
    phone and how big it is - so "not installed" can never pass for "no
    results". The count is COUNTED, not read from the file's own claim: a line
    saying "found" can be true while the list holds four rows."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, places.PLACES_FILENAME)

    def tearDown(self):
        self.dir.cleanup()

    def test_an_installed_list_gives_its_real_count(self):
        build_places.write_db(iter(ROWS), self.path)
        count, words = places.places_status(self.path)
        self.assertEqual(count, len(ROWS) - 1)
        self.assertIn(f"{len(ROWS) - 1:,} places on this phone", words)

    def test_no_list_says_what_that_means_in_words_she_can_act_on(self):
        with mock.patch.object(places, "places_path", return_value=None):
            count, words = places.places_status()
        self.assertIsNone(count)
        self.assertIn("street names", words)
        self.assertNotIn(".sqlite", words)

    def test_an_unreadable_list_says_so_and_leaves_a_trace(self):
        with open(self.path, "wb") as f:
            f.write(b"not a database" * 100)
        with mock.patch("traceback.print_exc") as trace:
            count, words = places.places_status(self.path)
        self.assertIsNone(count)
        self.assertIn("could not be read", words)
        trace.assert_called()

    def test_a_list_whose_rows_disagree_with_its_own_count_is_damaged(self):
        build_places.write_db(iter(ROWS), self.path)
        con = sqlite3.connect(self.path)
        con.execute("DELETE FROM place WHERE id > 2")          # truncated
        con.commit()
        con.close()
        count, words = places.places_status(self.path)
        self.assertIsNone(count)
        self.assertIn("damaged", words)

    def test_where_to_shows_the_line_before_any_search(self):
        build_places.write_db(iter(ROWS), self.path)
        with mock.patch.object(places, "places_path", return_value=self.path):
            tree = routes_page.build(_StubPage(), go=lambda key: None)
        self.assertTrue(any("places on this phone" in t for t in _texts(tree)))


# Built here, not imported from test_shell. Importing test_shell from another
# test module loads a second copy whenever discover knows it by a different
# name (`test_shell` under `-s tests`, `tests.test_shell` under `-t .`), and
# that copy's module-level FLET_APP_STORAGE_DATA repoints the first copy's
# storage - which failed LiveState's ride-directory test.

class _Task:
    def cancel(self):
        pass


class _StubPage:
    """Enough ft.Page for the Where-to surface to build against."""

    def __init__(self):
        self.controls, self.services, self.overlay, self.tasks = [], [], [], []
        self.updates = 0

    def update(self, *_a, **_k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **_kwargs):
        self.tasks.append((fn, args))


def _walk(node, seen=None):
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions", "destinations"):
        for child in (getattr(node, attr, None) or []):
            yield from _walk(child, seen)
    for attr in ("content", "title", "leading"):
        child = getattr(node, attr, None)
        if hasattr(child, "controls") or hasattr(child, "content"):
            yield from _walk(child, seen)


def _texts(node):
    return [str(c.value) for c in _walk(node)
            if type(c).__name__ == "Text" and getattr(c, "value", None)]


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

    def test_a_places_town_is_its_own_then_a_neighbours_then_the_nearest_town(self):
        # Scored on 71,678 places whose town is known: nearest town node alone
        # was right 75.6% (San Jose shown as Campbell 2,327 times); borrowing
        # the nearest labelled neighbour's within 300 m, 98.3%.
        towns = build_places._Nearest(build_places.TOWN_CELL)
        towns.add("Piedmont", 37.8244, -122.2316)               # the nearer town node
        towns.add("Oakland", 37.8044, -122.2712)
        labelled = build_places._Nearest(build_places.BORROW_CELL)
        labelled.add("Oakland", 37.81880, -122.18050)           # the museum, addr:city=Oakland
        pick = build_places.pick_town
        self.assertEqual(pick("Emeryville", 37.8187, -122.1803, labelled, towns), "Emeryville")
        self.assertEqual(pick(None, 37.8187, -122.1803, labelled, towns), "Oakland")     # 20 m from the museum
        self.assertEqual(pick(None, 37.8244, -122.2300, labelled, towns), "Piedmont")    # nobody labelled nearby

    def test_an_area_is_placed_at_its_middle(self):
        square = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
        self.assertEqual(build_places.centre(square), (1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
