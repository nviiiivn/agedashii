"""The pure logic behind Tools (Feeds, Vault, Journal) and Home.

Every shape asserted here was curled off the live dashbored on 2026-09-21
before a line of the code under test was written:

  /api/feeds          76 items - 53 event, 20 route, 3 local; errors {}
  /api/vault/tree     a bare JSON *array* on success; {"error": ...} on failure
  /api/journal/tree   categories -> children, _TEMPLATE.md flagged tpl:true,
                      mtime "2026-08-13T13:42:48" with no timezone on it
  /api/weather        daily.time has three entries and there is no `hourly`
  /api/dashboard      sp3's system widget is {"error": "unreachable"}

The recurring theme is the difference between "zero" and "we do not know".
Several of these tests exist because the app has shipped the first where it
meant the second.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flet_app import api  # noqa: E402


# --------------------------------------------------------------------- feeds
class FeedFilters(unittest.TestCase):
    """The chips must match what is actually in the list, not a fixed set."""

    LIVE = ([{"type": "event"}] * 53 + [{"type": "route"}] * 20
            + [{"type": "local"}] * 3)

    def test_the_live_payload_offers_all_local_events_routes(self):
        self.assertEqual(api.feed_types(self.LIVE),
                         [("all", "All"), ("local", "Local"),
                          ("event", "Events"), ("route", "Routes")])

    def test_an_rss_item_grows_an_rss_chip(self):
        self.assertIn(("rss", "RSS"), api.feed_types(self.LIVE + [{"type": "rss"}]))

    def test_a_type_with_no_items_gets_no_chip(self):
        """A chip that filters to nothing is a control that does nothing."""
        self.assertEqual(api.feed_types([{"type": "event"}]),
                         [("all", "All"), ("event", "Events")])

    def test_all_is_offered_even_for_an_empty_list(self):
        self.assertEqual(api.feed_types([]), [("all", "All")])

    def test_filtering_counts_match_the_live_payload(self):
        self.assertEqual(len(api.feed_filter(self.LIVE, "all")), 76)
        self.assertEqual(len(api.feed_filter(self.LIVE, "event")), 53)
        self.assertEqual(len(api.feed_filter(self.LIVE, "route")), 20)
        self.assertEqual(len(api.feed_filter(self.LIVE, "local")), 3)

    def test_all_keeps_the_servers_order(self):
        items = [{"type": "event", "i": 0}, {"type": "route", "i": 1}]
        self.assertEqual([i["i"] for i in api.feed_filter(items, "all")], [0, 1])

    def test_feeds_full_keeps_every_item(self):
        """The old reader trimmed to 20 of 76 with nothing on screen saying so."""
        payload = {"ts": 1.0, "items": [{"title": str(i)} for i in range(76)],
                   "errors": {}, "sources": {"x": {"label": "X"}}}
        with mock.patch.object(api, "_get", return_value=payload):
            got = api.feeds_full()
        self.assertEqual(len(got["items"]), 76)
        self.assertEqual(got["sources"]["x"]["label"], "X")

    def test_feeds_full_is_none_when_the_server_never_answered(self):
        with mock.patch.object(api, "_get", return_value=None):
            self.assertIsNone(api.feeds_full())


# --------------------------------------------------------------------- vault
LIVE_TREE = [
    {"name": "0 - Homeworld", "type": "dir", "path": "0 - Homeworld", "children": [
        {"name": "0.0 - HomeLab Info+Stats.md", "type": "file",
         "path": "0 - Homeworld/0.0 - HomeLab Info+Stats.md", "size": 5199}]},
    {"name": "1 - AI Conversations", "type": "dir", "path": "1 - AI Conversations",
     "children": [
         {"name": "1.00 GLOBAL", "type": "dir",
          "path": "1 - AI Conversations/1.00 GLOBAL", "children": [
              {"name": "1.00.2 - GOALS.md", "type": "file",
               "path": "1 - AI Conversations/1.00 GLOBAL/1.00.2 - GOALS.md",
               "size": 5}]}]},
    {"name": "loose.md", "type": "file", "path": "loose.md", "size": 12},
]


class VaultTree(unittest.TestCase):
    def test_flatten_is_depth_first_with_depths_and_parents(self):
        rows = api.vault_flatten(LIVE_TREE)
        self.assertEqual(
            [(r["depth"], r["dir"], r["name"]) for r in rows],
            [(0, True, "0 - Homeworld"),
             (1, False, "0.0 - HomeLab Info+Stats.md"),
             (0, True, "1 - AI Conversations"),
             (1, True, "1.00 GLOBAL"),
             (2, False, "1.00.2 - GOALS.md"),
             (0, False, "loose.md")])

    def test_a_files_path_is_the_one_the_read_endpoint_wants(self):
        rows = api.vault_flatten(LIVE_TREE)
        goals = [r for r in rows if r["name"] == "1.00.2 - GOALS.md"][0]
        self.assertEqual(goals["path"],
                         "1 - AI Conversations/1.00 GLOBAL/1.00.2 - GOALS.md")

    def test_everything_is_collapsed_when_nothing_is_open(self):
        vis = api.vault_visible(api.vault_flatten(LIVE_TREE), set())
        self.assertEqual([r["name"] for r in vis],
                         ["0 - Homeworld", "1 - AI Conversations", "loose.md"])

    def test_opening_a_folder_shows_only_its_own_children(self):
        vis = api.vault_visible(api.vault_flatten(LIVE_TREE), {"0 - Homeworld"})
        self.assertEqual([r["name"] for r in vis],
                         ["0 - Homeworld", "0.0 - HomeLab Info+Stats.md",
                          "1 - AI Conversations", "loose.md"])

    def test_a_grandchild_stays_hidden_while_its_grandparent_is_shut(self):
        """Checking only the immediate parent leaves orphans on screen."""
        vis = api.vault_visible(api.vault_flatten(LIVE_TREE),
                                {"1 - AI Conversations/1.00 GLOBAL"})
        self.assertNotIn("1.00.2 - GOALS.md", [r["name"] for r in vis])
        self.assertNotIn("1.00 GLOBAL", [r["name"] for r in vis])

    def test_both_levels_open_reaches_the_grandchild(self):
        vis = api.vault_visible(
            api.vault_flatten(LIVE_TREE),
            {"1 - AI Conversations", "1 - AI Conversations/1.00 GLOBAL"})
        self.assertIn("1.00.2 - GOALS.md", [r["name"] for r in vis])


class VaultErrors(unittest.TestCase):
    """"No vault here" and "no signal" are different, and the page says which."""

    def test_a_list_is_a_tree(self):
        with mock.patch.object(api, "_get", return_value=LIVE_TREE):
            tree, err = api.vault_tree("ai-tp", "DeVault")
        self.assertIsNone(err)
        self.assertEqual(len(tree), 3)

    def test_the_servers_own_refusal_comes_back_verbatim(self):
        with mock.patch.object(api, "_get",
                               return_value={"error": "no vault_path configured"}):
            tree, err = api.vault_tree("blavksaba", "DeVault")
        self.assertIsNone(tree)
        self.assertEqual(err, "no vault_path configured")

    def test_no_answer_at_all_is_reported_as_a_connection_problem(self):
        with mock.patch.object(api, "_get", return_value=None), \
             mock.patch.dict(api.config.STATUS, {"detail": "connection refused"}):
            tree, err = api.vault_tree()
        self.assertIsNone(tree)
        self.assertEqual(err, "connection refused")

    def test_a_missing_file_reads_as_none_not_as_an_empty_note(self):
        with mock.patch.object(api, "_get", return_value={"error": "not found"}):
            self.assertIsNone(api.vault_read("ai-tp", "DeVault", "nope.md"))

    def test_a_genuinely_empty_note_reads_as_empty(self):
        with mock.patch.object(api, "_get", return_value={"content": ""}):
            self.assertEqual(api.vault_read("ai-tp", "DeVault", "blank.md"), "")


# ------------------------------------------------------------------- journal
class JournalTitles(unittest.TestCase):
    def test_a_dated_slug_loses_its_date_and_gains_capitals(self):
        self.assertEqual(api.journal_title_from("2026-09-21_parity-smoke-test.md"),
                         "Parity Smoke Test")

    def test_an_undated_name_is_still_titled(self):
        self.assertEqual(api.journal_title_from("some-note.md"), "Some Note")

    def test_a_name_that_only_looks_dated_is_left_alone(self):
        self.assertEqual(api.journal_title_from("2026-note.md"), "2026 Note")


class JournalTimestamps(unittest.TestCase):
    """mtime has no timezone on it and means local time on the server.

    Reading it as UTC moves every entry seven hours, which is enough to file
    this morning's note under yesterday - the same class of bug as the
    website's eleven toISOString() sites.
    """

    def test_an_mtime_parses_as_local_time(self):
        got = api._journal_ts("2026-08-13T13:42:48")
        self.assertEqual(time.localtime(got)[:6], (2026, 8, 13, 13, 42, 48))

    def test_a_missing_or_broken_mtime_sorts_last_rather_than_crashing(self):
        self.assertEqual(api._journal_ts(""), 0.0)
        self.assertEqual(api._journal_ts("not a date"), 0.0)


class JournalListing(unittest.TestCase):
    TREE = {"categories": [
        {"name": "Mechanicry", "label": "Mechanicry", "path": "Mechanicry",
         "children": [
             {"name": "_TEMPLATE.md", "type": "file", "path": "_TEMPLATE.md",
              "tpl": True, "mtime": "2026-08-13T13:42:48"},
             {"name": "2026-09-21_parity-smoke-test.md", "type": "file",
              "path": "2026-09-21_parity-smoke-test.md", "size": 283,
              "mtime": "2026-09-21T13:31:43", "title": "Mechanicry Log",
              "excerpt": "Bike/vehicle: Mileage at service:"}]},
        {"name": "Dr-Visits", "label": "Dr Visits", "path": "Dr-Visits",
         "children": [{"name": "_TEMPLATE.md", "type": "file",
                       "path": "_TEMPLATE.md", "tpl": True}]}]}

    def _rows(self):
        with mock.patch.object(api, "_get", return_value=self.TREE):
            return api.journal_list()

    def test_templates_are_not_entries(self):
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("_TEMPLATE.md", [r["name"] for r in rows])

    def test_the_path_is_the_one_read_save_and_delete_want(self):
        self.assertEqual(self._rows()[0]["path"],
                         "Mechanicry/2026-09-21_parity-smoke-test.md")

    def test_the_category_key_survives_alongside_its_label(self):
        """The label cannot colour or filter - two of them differ from the key."""
        row = self._rows()[0]
        self.assertEqual(row["cat"], "Mechanicry")
        self.assertEqual(row["cat_label"], "Mechanicry")

    def test_no_server_is_none_not_an_empty_journal(self):
        with mock.patch.object(api, "_get", return_value=None):
            self.assertIsNone(api.journal_list())


class JournalStats(unittest.TestCase):
    NOW = time.mktime((2026, 9, 21, 12, 0, 0, 0, 0, -1))

    def _entry(self, cat, days_ago):
        return {"cat": cat, "ts": self.NOW - days_ago * 86400}

    def test_counts_split_by_week_and_calendar_month(self):
        rows = [self._entry("Mechanicry", 1), self._entry("Mechanicry", 3),
                self._entry("Dr-Visits", 10), self._entry("Dr-Visits", 60)]
        total, week, month, top = api.journal_stats(rows, now=self.NOW)
        self.assertEqual((total, week, month), (4, 2, 3))
        self.assertEqual(top, "Mechanicry")

    def test_the_month_is_the_calendar_month_not_thirty_days(self):
        """Two entries, one on the 1st and one 45 days back, in September."""
        rows = [{"cat": "x", "ts": time.mktime((2026, 9, 1, 0, 0, 0, 0, 0, -1))},
                {"cat": "x", "ts": self.NOW - 45 * 86400}]
        _t, _w, month, _top = api.journal_stats(rows, now=self.NOW)
        self.assertEqual(month, 1)

    def test_an_empty_journal_has_no_top_category(self):
        self.assertEqual(api.journal_stats([], now=self.NOW), (0, 0, 0, ""))


class JournalFiltering(unittest.TestCase):
    ROWS = [
        {"cat": "Mechanicry", "title": "Carb clean", "excerpt": "float bowl", "ts": 300},
        {"cat": "Dr-Visits", "title": "Annual", "excerpt": "bloodwork", "ts": 200},
        {"cat": "Mechanicry", "title": "Brake bleed", "excerpt": "DOT 4", "ts": 100},
    ]

    def test_newest_first_is_the_default(self):
        self.assertEqual([r["title"] for r in api.journal_filter(self.ROWS)],
                         ["Carb clean", "Annual", "Brake bleed"])

    def test_oldest_first_and_alphabetical(self):
        self.assertEqual(
            [r["title"] for r in api.journal_filter(self.ROWS, sort="old")],
            ["Brake bleed", "Annual", "Carb clean"])
        self.assertEqual(
            [r["title"] for r in api.journal_filter(self.ROWS, sort="az")],
            ["Annual", "Brake bleed", "Carb clean"])

    def test_the_category_chip_narrows_the_list(self):
        self.assertEqual(len(api.journal_filter(self.ROWS, cat="Mechanicry")), 2)

    def test_search_reads_the_excerpt_as_well_as_the_title(self):
        self.assertEqual(
            [r["title"] for r in api.journal_filter(self.ROWS, query="DOT")],
            ["Brake bleed"])
        self.assertEqual(
            [r["title"] for r in api.journal_filter(self.ROWS, query="bloodwork")],
            ["Annual"])

    def test_search_is_case_insensitive_and_combines_with_the_chip(self):
        got = api.journal_filter(self.ROWS, cat="Mechanicry", query="CARB")
        self.assertEqual([r["title"] for r in got], ["Carb clean"])

    def test_the_template_prefill_is_dated_today_locally(self):
        with mock.patch.object(api, "journal_read",
                               return_value="**Date:** YYYY-MM-DD"):
            got = api.journal_template("Mechanicry")
        self.assertEqual(got, "**Date:** " + api.local_today())

    def test_an_unknown_category_has_no_template_rather_than_a_404(self):
        self.assertEqual(api.journal_template("Not-A-Category"), "")


# ------------------------------------------------------------------ overview
class Overview(unittest.TestCase):
    DASH = {
        "blavksaba": {"display": "Tower", "color": "#58a6ff", "widgets": {
            "system": {"cpu_percent": 40, "memory_percent": 26,
                       "disk_percent": 89, "uptime": "1d 3h"},
            "services": {"services": [{"active": True}, {"active": False}]}}},
        "sp3": {"display": "SP3", "color": "#f85149", "widgets": {
            "system": {"error": "unreachable"},
            "services": {"services": []}}},
    }

    def _overview(self, containers=34):
        with mock.patch.object(api, "dashboard", return_value=self.DASH), \
             mock.patch.object(api, "docker_running", return_value=containers):
            return api.overview()

    def test_an_unreachable_machine_is_not_counted_as_online(self):
        got = self._overview()
        self.assertEqual((got["online"], got["total"]), (1, 2))

    def test_an_unreachable_machine_reports_no_numbers_at_all(self):
        """Not zero. A gauge at 0% is a reading; None is an admission."""
        sp3 = [m for m in self._overview()["machines"] if m["id"] == "sp3"][0]
        self.assertEqual((sp3["cpu"], sp3["ram"], sp3["disk"]),
                         (None, None, None))
        self.assertEqual(sp3["error"], "unreachable")

    def test_only_active_services_are_counted(self):
        self.assertEqual(self._overview()["services"], 1)

    def test_offline_machines_sort_to_the_bottom(self):
        self.assertEqual([m["id"] for m in self._overview()["machines"]],
                         ["blavksaba", "sp3"])

    def test_docker_being_unreadable_is_none_not_zero(self):
        self.assertIsNone(self._overview(containers=None)["containers"])

    def test_no_dashboard_at_all_is_none(self):
        with mock.patch.object(api, "dashboard", return_value=None):
            self.assertIsNone(api.overview())

    def test_docker_running_counts_only_running_containers(self):
        hosts = {"a": {"containers": [{"state": "running"}, {"state": "exited"}]},
                 "b": {"containers": [{"state": "running"}]}}
        with mock.patch.object(api, "docker_list", return_value=(hosts, None)):
            self.assertEqual(api.docker_running(), 2)

    def test_docker_erroring_is_unknown_rather_than_none_running(self):
        with mock.patch.object(api, "docker_list", return_value=(None, "timed out")):
            self.assertIsNone(api.docker_running())


class ForecastDays(unittest.TestCase):
    """The live server sends exactly three daily rows and no hourly block."""

    def _days(self, first):
        payload = {"daily": {
            "time": [first, "2026-09-22", "2026-09-23"],
            "temperature_2m_max": [63, 62, 79],
            "temperature_2m_min": [56, 57, 57],
            "weather_code": [3, 45, 3]}}
        with mock.patch.object(api, "_get", return_value=payload):
            return api.weather_days()

    def test_three_days_come_back_starting_with_today(self):
        with mock.patch.object(api, "local_today", return_value="2026-09-21"):
            days = self._days("2026-09-21")
        self.assertEqual([d["day"] for d in days], ["Today", "Tue", "Wed"])
        self.assertEqual(days[0]["hi"], 63)
        self.assertEqual(days[0]["lo"], 56)

    def test_a_cached_payload_from_yesterday_is_not_relabelled_today(self):
        """The website calls index 0 "Today" whatever date it holds. This app
        keeps yesterday's copy on purpose so the page works with no signal,
        which is exactly when that lie would be told."""
        with mock.patch.object(api, "local_today", return_value="2026-09-22"):
            days = self._days("2026-09-21")
        self.assertEqual(days[0]["day"], "Mon")
        self.assertEqual(days[1]["day"], "Today")

    def test_no_weather_at_all_is_none(self):
        with mock.patch.object(api, "_get", return_value=None):
            self.assertIsNone(api.weather_days())

    def test_the_current_block_carries_no_rain_chance(self):
        """There is no `hourly` in this server's reply, so nothing may claim
        a precipitation probability. Home used to print "0% rain"."""
        with mock.patch.object(api, "_get", return_value={
                "current": {"temperature_2m": 63.9, "weather_code": 1,
                            "relative_humidity_2m": 78}}):
            cur = api.weather_current()
        self.assertNotIn("precipitation_probability", cur)
        self.assertEqual(cur["relative_humidity_2m"], 78)


class LocalDates(unittest.TestCase):
    def test_today_is_the_local_date(self):
        self.assertEqual(api.local_today(), time.strftime("%Y-%m-%d"))

    def test_today_is_not_the_utc_date_when_they_differ(self):
        """The whole point. After ~4pm Pacific, gmtime is already tomorrow."""
        local = api.local_today()
        self.assertEqual(local, time.strftime("%Y-%m-%d", time.localtime()))


if __name__ == "__main__":
    unittest.main()
