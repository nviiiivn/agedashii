"""SPEED_KMH is indexed by GRAPH class, whatever the CLASSES label says.

The .rg graph's class numbers are not the OSM highway names in
routing.CLASSES: class 2, labelled "trunk", is 81% motorway by length. The
first per-class fit filed each measured speed under the slot with the
matching NAME, so every speed landed on the wrong kind of road - class 2 at
58.4 km/h - and the router, believing those freeways were slow, sent routes
around them. Totals still validated, because errors on different classes
cancel in a sum. tools/navi-routing/fit_speed_kmh.py measures per graph class.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu import routing as R  # noqa: E402

GRAPH = R.graph_path()


class TheTableFitsTheGraph(unittest.TestCase):
    def test_one_speed_per_graph_class(self):
        self.assertEqual(len(R.SPEED_KMH), len(R.CLASSES))

    def test_freeway_grade_class_2_is_priced_like_a_freeway(self):
        self.assertGreater(R.SPEED_KMH[2], 80)
        self.assertGreater(R.SPEED_KMH[2], R.SPEED_KMH[4])

    def test_a_ramp_is_slower_than_the_freeway_it_serves(self):
        self.assertLess(R.SPEED_KMH[1], R.SPEED_KMH[0])
        self.assertLess(R.SPEED_KMH[1], R.SPEED_KMH[2])


@unittest.skipUnless(GRAPH, "no .rg road graph installed - set AGEDASHII_ROUTE_GRAPH")
class TheEmptyClassesAreEmpty(unittest.TestCase):
    def test_the_classes_said_to_be_empty_are_empty(self):
        # Saying which slots hold nothing is what stops the next fit putting a
        # number into a hole.
        used = set(R.Graph(GRAPH).cls)
        self.assertEqual(set(range(len(R.CLASSES))) - used, set(R.EMPTY_CLASSES))


if __name__ == "__main__":
    unittest.main()
