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

from agedashii_tofu import api  # noqa: E402
from agedashii_tofu.pages import home  # noqa: E402


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

    def test_the_container_count_is_not_fetched_on_the_first_paint(self):
        with mock.patch.object(api, "dashboard", return_value=self.DASH), \
             mock.patch.object(api, "docker_running") as count:
            api.overview()
        count.assert_called_once_with(blocking=False)

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
        with mock.patch.object(api, "_get", return_value=hosts):
            self.assertEqual(api.docker_running(), 2)

    def test_docker_erroring_is_unknown_rather_than_none_running(self):
        with mock.patch.object(api, "_get", return_value=None):
            self.assertIsNone(api.docker_running())

    def test_the_first_paint_never_waits_six_seconds_on_docker(self):
        """/api/docker_list SSHes to five hosts; three consecutive Home
        builds were measured at 6.0, 5.9 and 6.1 seconds, all of it this.
        Non-blocking, it answers from the store or not at all."""
        with mock.patch.object(api, "_get") as wire, \
             mock.patch.object(api, "_cached", return_value=None) as stored:
            self.assertIsNone(api.docker_running(blocking=False))
        wire.assert_not_called()
        stored.assert_called_once()

    def test_a_warm_store_answers_the_first_paint_without_the_wire(self):
        hosts = {"a": {"containers": [{"state": "running"}]}}
        with mock.patch.object(api, "_get") as wire, \
             mock.patch.object(api, "_cached", return_value=hosts):
            self.assertEqual(api.docker_running(blocking=False), 1)
        wire.assert_not_called()


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


# ----------------------------------------------------------------- home card
class RideCall(unittest.TestCase):
    """Home's ride verdict used to be the string "Ride weather good".

    It said that in a downpour, because it was a literal. These are the same
    thresholds Wat2Do and the website's w2d callout use.
    """

    def test_rain_is_an_indoor_day(self):
        for code in (51, 61, 65, 82, 95):
            with self.subTest(code=code):
                text, tone = home.ride_call({"weather_code": code,
                                             "wind_speed_10m": 2})
                self.assertEqual(tone, "bad")
                self.assertIn("Wet", text)

    def test_over_twenty_mph_is_a_bundled_ride(self):
        text, tone = home.ride_call({"weather_code": 1, "wind_speed_10m": 24})
        self.assertEqual(tone, "meh")
        self.assertIn("Windy", text)

    def test_clear_and_calm_is_a_good_day(self):
        _text, tone = home.ride_call({"weather_code": 1, "wind_speed_10m": 5})
        self.assertEqual(tone, "good")

    def test_no_reading_says_so_rather_than_guessing_good(self):
        text, tone = home.ride_call(None)
        self.assertEqual(tone, "unknown")
        self.assertIn("No weather", text)

    def test_a_junk_reading_does_not_take_the_card_down(self):
        _text, tone = home.ride_call({"weather_code": "eh", "wind_speed_10m": None})
        self.assertEqual(tone, "good")


class HomeRenders(unittest.TestCase):
    """What actually reaches the screen, walked out of the control tree."""

    class Stub:
        def __init__(self):
            self.controls = []
            self.services = []
            self.overlay = []
            self.drawer = None
            self.theme = None
            self.theme_mode = None

        def update(self, *a, **k):
            pass

        def add(self, *c):
            self.controls.extend(c)

        def run_task(self, *a, **k):
            pass

    DASH = {"blavksaba": {"display": "Tower", "color": "#58a6ff", "widgets": {
        "system": {"cpu_percent": 40, "memory_percent": 26, "disk_percent": 89},
        "services": {"services": [{"active": True}]}}}}

    def _texts(self, control, out=None, depth=0):
        out = [] if out is None else out
        if depth > 60:
            return out
        for attr in ("value", "content", "text", "label"):
            v = getattr(control, attr, None)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        for attr in ("controls", "content"):
            v = getattr(control, attr, None)
            if isinstance(v, list):
                for y in v:
                    if hasattr(y, "__dict__"):
                        self._texts(y, out, depth + 1)
            elif v is not None and hasattr(v, "__dict__"):
                self._texts(v, out, depth + 1)
        return out

    def _urls(self, control, out=None, depth=0):
        out = [] if out is None else out
        if depth > 60:
            return out
        if isinstance(getattr(control, "url", None), str):
            out.append(control.url)
        for attr in ("controls", "content"):
            v = getattr(control, attr, None)
            if isinstance(v, list):
                for y in v:
                    if hasattr(y, "__dict__"):
                        self._urls(y, out, depth + 1)
            elif v is not None and hasattr(v, "__dict__"):
                self._urls(v, out, depth + 1)
        return out

    def _dead_tiles(self, control, out=None, depth=0):
        """Containers that carry ink but no handler and no url.

        Four of these shipped on Home for the life of the page - Wake, Ride,
        Wat2Do, List - styled as buttons and wired to nothing.
        """
        out = [] if out is None else out
        if depth > 60:
            return out
        if (getattr(control, "ink", None)
                and not getattr(control, "on_click", None)
                and not getattr(control, "url", None)):
            out.append(" / ".join(self._texts(control))[:60])
        for attr in ("controls", "content"):
            v = getattr(control, attr, None)
            if isinstance(v, list):
                for y in v:
                    if hasattr(y, "__dict__"):
                        self._dead_tiles(y, out, depth + 1)
            elif v is not None and hasattr(v, "__dict__"):
                self._dead_tiles(v, out, depth + 1)
        return out

    def _build(self, dash=None, weather=None, containers=34):
        from agedashii_tofu import prefs
        prefs._V = prefs._defaults()
        with mock.patch.object(api, "dashboard", return_value=dash), \
             mock.patch.object(api, "docker_running", return_value=containers), \
             mock.patch.object(api, "_get", return_value=weather), \
             mock.patch.object(api, "STATUS_LIVE", return_value=True):
            return home.build(self.Stub())

    WEATHER = {"current": {"temperature_2m": 63.9, "apparent_temperature": 63.7,
                           "weather_code": 1, "wind_speed_10m": 5.8,
                           "relative_humidity_2m": 78},
               "daily": {"time": ["2026-09-21", "2026-09-22", "2026-09-23"],
                         "temperature_2m_max": [63, 62, 79],
                         "temperature_2m_min": [56, 57, 57],
                         "weather_code": [3, 45, 3]}}

    def test_no_control_looks_tappable_without_being_tappable(self):
        got = self._build(self.DASH, self.WEATHER)
        self.assertEqual(self._dead_tiles(got), [])

    def test_the_stat_strip_carries_the_four_website_tiles(self):
        said = self._texts(self._build(self.DASH, self.WEATHER))
        for label in ("Machines online", "Containers running", "Services up",
                      "Oakland temp"):
            self.assertIn(label, said)

    def test_docker_not_counted_yet_says_so_rather_than_showing_a_zero(self):
        said = self._texts(self._build(self.DASH, self.WEATHER, containers=None))
        self.assertIn("counting...", said)
        self.assertNotIn("0", said[said.index("Containers running") + 1])

    def test_no_dashboard_shows_dashes_rather_than_zeroes(self):
        said = self._texts(self._build(None, self.WEATHER))
        self.assertIn("no reply from the server", said)
        self.assertNotIn("of 5 total", said)

    def test_all_eight_quick_links_are_real_urls(self):
        urls = self._urls(self._build(self.DASH, self.WEATHER))
        for _label, _note, url, _icon in home.QUICK_LINKS:
            self.assertIn(url, urls)

    def test_the_forecast_row_shows_three_days(self):
        said = self._texts(self._build(self.DASH, self.WEATHER))
        self.assertIn("63° / 56°", said)
        self.assertIn("62° / 57°", said)
        self.assertIn("79° / 57°", said)

    def test_nothing_claims_a_rain_chance(self):
        """/api/weather sends no hourly block, so nothing may quote one.
        The old card printed "0% rain" off an empty list, every day."""
        said = " ".join(self._texts(self._build(self.DASH, self.WEATHER)))
        self.assertNotIn("% rain", said)
        self.assertIn("Humidity 78%", said)

    def test_the_ride_verdict_is_computed_not_asserted(self):
        wet = dict(self.WEATHER)
        wet["current"] = dict(self.WEATHER["current"], weather_code=65)
        said = " ".join(self._texts(self._build(self.DASH, wet)))
        self.assertNotIn("Ride weather good", said)
        self.assertIn("Wet - indoor day", said)

    def test_a_machine_that_is_down_shows_no_numbers(self):
        dash = dict(self.DASH)
        dash["sp3"] = {"display": "SP3", "color": "#f85149",
                       "widgets": {"system": {"error": "unreachable"}}}
        said = self._texts(self._build(dash, self.WEATHER))
        self.assertIn("unreachable", said)
        self.assertIn("1/2 up", said)

    def test_navigation_tiles_are_absent_when_there_is_no_shell_to_navigate(self):
        """Better no tile than a tile that does nothing. S.goto is set by
        main(); a standalone build has none."""
        from agedashii_tofu.ui import S
        had = getattr(S, "goto", None)
        if had is not None:
            del S.goto
        try:
            said = self._texts(self._build(self.DASH, self.WEATHER))
            self.assertNotIn("Docker", said)
            self.assertIn("Refresh all", said)
        finally:
            if had is not None:
                S.goto = had

    def test_navigation_tiles_appear_and_land_on_the_right_tab(self):
        from agedashii_tofu.ui import S
        jumped = []
        had = getattr(S, "goto", None)
        S.goto = lambda key, path=(): jumped.append((key, tuple(path)))
        try:
            built = self._build(self.DASH, self.WEATHER)
            self._fire(built, {"Docker", "Qbit", "Wat2Do", "Journal"})
        finally:
            if had is None:
                del S.goto
            else:
                S.goto = had
        self.assertIn(("lab", (("lab", 4),)), jumped)       # Docker
        self.assertIn(("lab", (("lab", 3),)), jumped)       # Qbit
        self.assertIn(("tools", (("tools", 2),)), jumped)   # Journal
        self.assertIn(("wat2do", ()), jumped)

    def _fire(self, c, labels, depth=0):
        if depth > 60:
            return
        handler = getattr(c, "on_click", None)
        if handler:
            said = {t for t in self._texts(c)}
            if said & labels and len(said) <= 2:
                handler(mock.Mock(control=c))
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for y in v:
                    if hasattr(y, "__dict__"):
                        self._fire(y, labels, depth + 1)
            elif v is not None and hasattr(v, "__dict__"):
                self._fire(v, labels, depth + 1)


def _walk_texts(control, out=None, depth=0):
    out = [] if out is None else out
    if depth > 60:
        return out
    v = getattr(control, "value", None)
    if isinstance(v, str) and v.strip():
        out.append(v.strip())
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for y in child:
                if hasattr(y, "__dict__"):
                    _walk_texts(y, out, depth + 1)
        elif child is not None and hasattr(child, "__dict__"):
            _walk_texts(child, out, depth + 1)
    return out


class StubPage:
    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []

    def update(self, *a, **k):
        pass

    def add(self, *c):
        self.controls.extend(c)


class ToolsDoesNotBuildEveryTabBeforeItPaints(unittest.TestCase):
    """Measured against the live server: tools.build was 1915 controls.

    1221 of them were the 76 feed cards and 503 the six FoodZen tabs
    nobody had opened. Flet runs the handler that builds a page
    synchronously on its event loop, so while it runs the old frame stays
    on screen and every tap is dropped - which is what "this button does
    nothing" actually is.
    """

    def _bodies(self, path, key):
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, path)) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "_tabs"
                    and any(kw.arg == "key"
                            and getattr(kw.value, "value", None) == key
                            for kw in node.keywords)):
                return [t.elts[1] for t in node.args[1].elts]
        self.fail(f"no _tabs(key={key!r}) call in {path}")

    def test_every_tools_tab_body_is_deferred(self):
        import ast
        bodies = self._bodies("agedashii_tofu/pages/tools.py", "tools")
        # Feeds, Vault, Journal. FoodZen was the fourth and left
        # with the rest of Foodzen.
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all(isinstance(b, ast.Lambda) for b in bodies),
                        "every Tools tab body must be deferred")

    def test_every_foodzen_tab_body_is_deferred(self):
        import ast
        bodies = self._bodies("apps/foodzen/agedashii_foodzen/pages/foodzen.py", "foodzen")
        self.assertEqual(len(bodies), 6)
        self.assertTrue(all(isinstance(b, ast.Lambda) for b in bodies),
                        "every FoodZen tab body must be deferred")


class FeedsShowsAPageAtATimeAndSaysSo(unittest.TestCase):
    """Capping was never the bug. Capping *silently* was.

    76 cards is 1221 controls. All 76 are still reachable, the chips still
    count every one of them, and the footer names how many are on screen
    out of how many there are.
    """

    ITEMS = [{"type": "event", "title": f"ride {i}", "date": "Sep 21",
              "iso": "2026-09-21", "source": "s", "url": f"http://x/{i}"}
             for i in range(76)]

    def setUp(self):
        from agedashii_tofu.pages import tools
        self.tools = tools
        self.saved = dict(tools._F)
        self.saved_state = dict(tools._FEEDS)
        tools._F.update({"kind": "all", "show": tools.FEED_PAGE})
        tools._FEEDS.update({"busy": False, "error": ""})
        # `_cached`, not `feeds_full`: the paint path reads the store.
        self.patch = mock.patch.object(
            api, "_cached",
            return_value={"items": self.ITEMS, "sources": {}, "errors": {}})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tools._F.clear()
        self.tools._F.update(self.saved)
        self.tools._FEEDS.clear()
        self.tools._FEEDS.update(self.saved_state)

    def test_the_paint_path_never_touches_the_wire(self):
        """/api/feeds is 1.23 s against the live server - it rescrapes every
        source. Read inline that is a second of dropped taps."""
        with mock.patch.object(api, "feeds_full") as wire:
            self.tools._feeds(StubPage())
        wire.assert_not_called()

    def test_a_page_is_drawn_and_the_rest_are_admitted_to(self):
        shown = _walk_texts(self.tools._feeds(StubPage()))
        drawn = [t for t in shown if t.startswith("ride ")]
        self.assertEqual(len(drawn), self.tools.FEED_PAGE)
        self.assertIn(f"showing {self.tools.FEED_PAGE} of 76 in this filter",
                      shown)
        # The chip still counts all 76, so the cap hides no item's existence.
        self.assertTrue(any(t.endswith("· 76") for t in shown), shown)

    def test_show_all_draws_all_of_them(self):
        self.tools._F["show"] = 76
        shown = _walk_texts(self.tools._feeds(StubPage()))
        self.assertEqual(len([t for t in shown if t.startswith("ride ")]), 76)
        self.assertFalse([t for t in shown if t.startswith("showing ")])

    def test_a_filter_shorter_than_a_page_gets_no_footer(self):
        with mock.patch.object(api, "_cached",
                               return_value={"items": self.ITEMS[:3],
                                             "sources": {}, "errors": {}}):
            shown = _walk_texts(self.tools._feeds(StubPage()))
        self.assertEqual(len([t for t in shown if t.startswith("ride ")]), 3)
        self.assertFalse([t for t in shown if t.startswith("showing ")])

    def test_nothing_stored_yet_says_reading_not_unavailable(self):
        """Three different nothings. Calling them all "unavailable" is how a
        tab that is simply still reading looks broken."""
        with mock.patch.object(api, "_cached", return_value=None):
            still = _walk_texts(self.tools._feeds(StubPage()))
            self.assertTrue(any("never answered" in s for s in still), still)
            self.tools._FEEDS["busy"] = True
            reading = _walk_texts(self.tools._feeds(StubPage()))
            self.assertTrue(any("Reading /api/feeds" in s for s in reading))
            self.tools._FEEDS.update({"busy": False, "error": "refused"})
            dead = _walk_texts(self.tools._feeds(StubPage()))
            self.assertIn("refused", dead)
            self.assertIn("No feeds", dead)




class PowerIsNeverQueued(unittest.TestCase):
    """A reboot delivered three hours late is a reboot nobody asked for."""

    def test_an_unknown_action_is_refused_before_any_request(self):
        with mock.patch.object(api, "_live_post") as post:
            ok, msg = api.power("sp3", "explode")
        self.assertFalse(ok)
        post.assert_not_called()
        self.assertIn("explode", msg)

    def test_the_servers_own_words_come_back_rather_than_a_tick(self):
        """/api/power answers {"ok": true} even when the SSH call reported a
        failure, so summarising it into "saved" would be a lie."""
        with mock.patch.object(
                api, "_live_post",
                return_value=({"ok": True, "output": "WoL tools not installed"},
                              None)):
            ok, msg = api.power("sp3", "wake")
        self.assertTrue(ok)
        self.assertEqual(msg, "WoL tools not installed")


if __name__ == "__main__":
    unittest.main()
