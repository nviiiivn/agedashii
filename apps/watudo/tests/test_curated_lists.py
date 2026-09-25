"""The six hand-kept lists /api/future/curated ships.

Moved out of Tofu's tests/test_lab_extras.py when Future Finds left
Tofu for Watudo. Unchanged except for the import.
"""
import unittest


class CuratedLists(unittest.TestCase):
    """The six hand-kept lists /api/future/curated ships.

    The rows below are verbatim from the live payload. Counts that day:
    backroads 12, redwoods 17, weirdseed 10, ebt 178, movies 1362,
    events 46 - and 103 of the EBT rows have no coordinates at all.
    """

    CURATED = {
        "weirdseed": [
            {"name": "Winchester Mystery House",
             "note": "Staircases to nowhere, doors to walls",
             "lat": 37.318293, "lon": -121.950989},
            {"name": "Albany Bulb", "note": "Landfill turned art park",
             "lat": 37.889, "lon": -122.323},
        ],
        "ebt": [
            {"name": "Chabot Space & Science Center", "city": "Oakland, CA",
             "lat": 37.8188, "lon": -122.1808},
            {"name": "The African American Firefighter Museum",
             "city": "Los Angeles, CA", "lat": None, "lon": None},
            {"name": "A museum with no city at all", "lat": None, "lon": None},
        ],
        "events_stale_h": 7.4,
    }

    def test_placeable_rows_come_back_sorted_by_distance_from_home(self):
        from agedashii_watudo.pages import future
        spots, nameless = future.curated_spots(self.CURATED, "weirdseed")
        self.assertEqual([s["name"] for s in spots],
                         ["Albany Bulb", "Winchester Mystery House"])
        self.assertEqual(nameless, [])
        self.assertLess(spots[0]["dist_m"], spots[1]["dist_m"])

    def test_rows_with_no_coordinates_are_named_not_dropped(self):
        from agedashii_watudo.pages import future
        spots, nameless = future.curated_spots(self.CURATED, "ebt")
        self.assertEqual([s["name"] for s in spots],
                         ["Chabot Space & Science Center"])
        self.assertEqual(nameless,
                         ["The African American Firefighter Museum (Los Angeles, CA)",
                          "A museum with no city at all"])

    def test_an_unknown_list_is_empty_rather_than_an_exception(self):
        from agedashii_watudo.pages import future
        self.assertEqual(future.curated_spots(self.CURATED, "nope"), ([], []))
        self.assertEqual(future.curated_spots(None, "ebt"), ([], []))
        self.assertEqual(future.curated_spots(self.CURATED, "events_stale_h"),
                         ([], []), "the float sitting among the lists is not a list")

    def test_the_list_is_capped_the_way_the_website_caps_it(self):
        from agedashii_watudo.pages import future
        many = {"movies": [{"name": f"loc {i}", "lat": 37.8 + i / 10000,
                            "lon": -122.2} for i in range(400)]}
        spots, _ = future.curated_spots(many, "movies")
        self.assertEqual(len(spots), future.MAX_CURATED)

    def test_the_scrape_age_is_stated_so_stale_events_cannot_read_as_tonight(self):
        from agedashii_watudo.pages import future
        self.assertEqual(future._events_age(self.CURATED), "events scraped 7h ago")
        self.assertEqual(future._events_age({"events_stale_h": 0.4}),
                         "events scraped under an hour ago")
        self.assertEqual(future._events_age({"events_stale_h": 72}),
                         "events scraped 3 days ago")
        self.assertEqual(future._events_age({}), "")
        self.assertEqual(future._events_age(None), "")
