"""Wat2Do's Overpass scan, and what it is allowed to say about a blank one.

Measured against the live server on 2026-09-21:

  15 venue categories, batched 3 at a time at r=4000, took 29.7 s end to end
  and dropped one batch (Nature, Obs Tower, Dog Park) - the page names those
  three rather than presenting the short list as complete.

  Every batch that *did* answer came back with ``"elements": []`` and a
  generator timestamp of "117180". There are cafes within 4 km of Vulcan
  Lofts, so that is a mirror running on an empty extract, not an empty
  neighbourhood - and the page is not allowed to report it as the second.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flet_app.pages import _wat2do_parts as W  # noqa: E402


class EmptyScanIsNotAnEmptyNeighbourhood(unittest.TestCase):
    def test_no_elements_and_no_failures_blames_the_mirror(self):
        note = W.empty_scan_note([], [])
        self.assertIn("mirror", note)
        self.assertNotIn("Nothing nearby", note)

    def test_elements_that_are_all_unnamed_really_is_nothing_to_show(self):
        note = W.empty_scan_note([{"id": 1, "tags": {}}], [])
        self.assertIn("Nothing nearby", note)

    def test_a_dropped_batch_is_mentioned_when_the_rest_was_blank(self):
        note = W.empty_scan_note([], ["nature"])
        self.assertIn("dropped", note)


class Batching(unittest.TestCase):
    """Three at a time, each free to fail alone. Fifteen in one query 504s."""

    CATS = [{"id": f"c{i}", "q": "node[x=y];"} for i in range(7)]

    def setUp(self):
        W._POI_CACHE.clear()

    def tearDown(self):
        W._POI_CACHE.clear()

    def test_the_query_is_sent_in_batches_of_three(self):
        seen = []

        def once(group, radius, timeout):
            seen.append([c["id"] for c in group])
            return [{"id": 1}]

        with mock.patch.object(W, "_overpass_once", side_effect=once):
            elems, failed = W.overpass(self.CATS, 4000)
        self.assertEqual(seen, [["c0", "c1", "c2"], ["c3", "c4", "c5"], ["c6"]])
        self.assertEqual(failed, [])
        self.assertEqual(len(elems), 3)

    def test_one_dead_batch_does_not_take_the_scan_down(self):
        def once(group, radius, timeout):
            return None if group[0]["id"] == "c3" else [{"id": 1}]

        with mock.patch.object(W, "_overpass_once", side_effect=once):
            elems, failed = W.overpass(self.CATS, 4000)
        self.assertEqual(failed, ["c3", "c4", "c5"])
        self.assertEqual(len(elems), 2)

    def test_every_batch_failing_is_none_rather_than_an_empty_list(self):
        """None means "we do not know"; [] means "there is nothing there"."""
        with mock.patch.object(W, "_overpass_once", return_value=None):
            elems, failed = W.overpass(self.CATS, 4000)
        self.assertIsNone(elems)
        self.assertEqual(len(failed), 7)

    def test_each_batch_gets_its_own_timeout_inside_one_budget(self):
        timeouts = []

        def once(group, radius, timeout):
            timeouts.append(timeout)
            return []

        with mock.patch.object(W, "_overpass_once", side_effect=once):
            W.overpass(self.CATS, 4000, timeout=35, budget=80)
        self.assertEqual(len(timeouts), 3)
        for t in timeouts:
            self.assertLessEqual(t, 35)
            self.assertGreaterEqual(t, 5.0)

    def test_no_categories_is_an_empty_answer_not_a_request(self):
        with mock.patch.object(W, "_overpass_once") as once:
            self.assertEqual(W.overpass([], 4000), ([], []))
        once.assert_not_called()


class CheckKeysMatchTheWebsite(unittest.TestCase):
    """A sub-task is stored as "<root> / <sub>" at both ends.

    Keying by the raw template line, which this used to do, made the phone
    and the browser disagree about every single sub-task. The live template
    has 14 lines: 5 roots, 3 of which own 8 sub-tasks between them.
    """

    LIVE = ["Feed selves", "Shave / shower", "Cleaning home", "> vacuum",
            "> clean kitchen", "> dishes", "Laundry", "Dog Walk", "> morning",
            "> because", "> before go", "Groceries", "> List",
            "> in-person or order"]

    def test_sub_tasks_are_keyed_under_their_root(self):
        flat = W.flatten(self.LIVE, {})
        keys = [it["key"] for it in flat]
        self.assertIn("Cleaning home / vacuum", keys)
        self.assertIn("Groceries / in-person or order", keys)
        self.assertNotIn("> vacuum", keys)

    def test_progress_counts_leaves_so_a_root_is_not_counted_twice(self):
        flat = W.flatten(self.LIVE, {})
        self.assertEqual(W.progress(flat), (0, 11))

    def test_tapping_a_root_ticks_every_sub_task_under_it(self):
        flat = W.flatten(self.LIVE, {})
        i = [n for n, it in enumerate(flat) if it["display"] == "Cleaning home"][0]
        got = W.toggle_checks(flat, i, {})
        self.assertEqual(sorted(k for k, v in got.items() if v),
                         ["Cleaning home", "Cleaning home / clean kitchen",
                          "Cleaning home / dishes", "Cleaning home / vacuum"])

    def test_tapping_it_again_clears_them_all(self):
        flat = W.flatten(self.LIVE, {})
        i = [n for n, it in enumerate(flat) if it["display"] == "Cleaning home"][0]
        ticked = W.toggle_checks(flat, i, {})
        flat2 = W.flatten(self.LIVE, ticked)
        cleared = W.toggle_checks(flat2, i, ticked)
        self.assertEqual([k for k, v in cleared.items() if v], [])


class DatesAreLocal(unittest.TestCase):
    def test_today_matches_the_api_helper(self):
        from flet_app import api
        self.assertEqual(W.today_str(), api.local_today())

    def test_shifting_a_day_crosses_a_month_boundary_correctly(self):
        self.assertEqual(W.shift_date("2026-09-30", 1), "2026-10-01")
        self.assertEqual(W.shift_date("2026-10-01", -1), "2026-09-30")

    def test_shifting_never_moves_a_day_by_a_timezone(self):
        """Round-tripping through UTC is what put Wat2Do on the wrong day."""
        for delta in (-3, -1, 0, 1, 7):
            back = W.shift_date(W.shift_date("2026-09-21", delta), -delta)
            self.assertEqual(back, "2026-09-21")


if __name__ == "__main__":
    unittest.main()
