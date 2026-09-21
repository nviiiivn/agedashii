"""The four places the app was still online-only, pinned shut.

Same fake-server harness as test_api_offline (imported, not copied), so
nothing here touches a socket either.

  W6  Wat2Do's own save bypassed the outbox. `_wat2do_parts.save_state` used
      `api._post`, which returns None with no signal and drops the edit on
      the floor - every other editable page queued theirs. These tests fail
      the moment that regresses: they assert the write is queued, that it is
      still queued after a restart, and that nothing reached the wire.

  W8  The server used to reject the `eatzen`, `bpm` and `custom_foods`
      collections. It does not any more, and this pins the wire format the
      app has to speak to reach them.

  W9  Future Finds. Its reads answer from the device and its writes queue,
      and - the bit that actually costs data if it goes wrong - a gem that
      has not reached the server yet has no id, so it must not offer a
      delete that would send one.
"""
import json
import unittest

from test_api_offline import ApiOfflineCase

from flet_app import api
from flet_app.pages import _wat2do_parts as W
from flet_app.pages import future as F

def _all_text(node, out=None):
    """Every Text value in a built control tree, for asserting on wording."""
    import flet as ft
    out = [] if out is None else out
    if isinstance(node, ft.Text) and node.value:
        out.append(node.value)
    for child in (getattr(node, "controls", None) or []):
        _all_text(child, out)
    content = getattr(node, "content", None)
    if isinstance(content, ft.Control):
        _all_text(content, out)
    return out


DAY = "2026-09-20"
DAY_PATH = f"/api/wat2do/state?date={DAY}"
RECORD = {"date": DAY, "items": ["Meds", "> water", "Walk"],
          "checks": {}, "pain": 2, "spoons": 3}


# ============================================================== W6: Wat2Do
class TestWat2DoSaveGoesThroughTheOutbox(ApiOfflineCase):
    def seed_day(self, record=None):
        self.server.data[DAY_PATH] = json.loads(json.dumps(record or RECORD))
        got = W.state(DAY)
        self.assertIsNotNone(got, "the fake server should have answered")
        return got

    def test_an_offline_tick_is_queued_rather_than_lost(self):
        self.seed_day()
        self.go_offline()
        posted = len(self.server.posts)

        ok, msg, record = W.save_state(DAY, {"Meds": True}, 2, 3)

        self.assertTrue(ok, msg)
        self.assertEqual(msg, api.QUEUED_MESSAGE)
        self.assertEqual(len(self.server.posts), posted,
                         "nothing should have reached the wire")
        self.assertEqual(api.pending_count(), 1)
        self.assertIn("1 change waiting", api.sync_status()["label"])
        self.assertTrue(record["checks"]["Meds"],
                        "the caller repaints from this; it must carry the tick")

    def test_the_tick_is_there_on_the_very_next_read(self):
        self.seed_day()
        self.go_offline()
        W.save_state(DAY, {"Meds": True, "Meds / water": True}, 4, 2)

        again = W.state(DAY)
        self.assertEqual(again["checks"]["Meds"], True)
        self.assertEqual(again["checks"]["Meds / water"], True)
        self.assertEqual(again["pain"], 4)
        self.assertEqual(again["spoons"], 2)
        self.assertEqual(again["items"], RECORD["items"],
                         "the day's template must survive the merge")

    def test_the_tick_survives_the_app_being_killed(self):
        """The whole point of the outbox: a cold launch still owes the write."""
        self.seed_day()
        self.go_offline()
        W.save_state(DAY, {"Walk": True}, 2, 3)

        self.store.close()
        self.store = self.new_store()
        api.set_store(self.store)

        self.assertEqual(api.pending_count(), 1)
        self.assertTrue(W.state(DAY)["checks"]["Walk"])

    def test_it_reaches_the_server_when_signal_comes_back(self):
        self.seed_day()
        self.go_offline()
        W.save_state(DAY, {"Meds": True}, 5, 1)
        self.go_online()

        result = api.sync()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(api.pending_count(), 0)
        sent = self.server.posted_to("/api/wat2do/save")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], {"date": DAY, "checks": {"Meds": True},
                                   "pain": 5, "spoons": 1})

    def test_an_afternoon_of_ticking_collapses_into_one_write(self):
        self.seed_day()
        self.go_offline()
        for check in ("Meds", "Walk", "Meds / water"):
            W.save_state(DAY, {check: True}, 2, 3)
        self.assertEqual(api.pending_count(), 1, "coalesced per day")

        self.go_online()
        api.sync()
        sent = self.server.posted_to("/api/wat2do/save")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["checks"], {"Meds / water": True})

    def test_a_bad_date_is_refused_before_anything_is_queued(self):
        self.seed_day()
        ok, msg, record = W.save_state("not-a-date", {"Meds": True}, 2, 3)
        self.assertEqual((ok, msg, record), (False, "bad date", None))
        self.assertEqual(api.pending_count(), 0)

    def test_the_server_refusing_is_reported_not_swallowed(self):
        self.seed_day()
        self.server.post_error = (500, {"error": "wat2do disk full"})
        ok, msg, record = W.save_state(DAY, {"Meds": True}, 2, 3)
        self.assertFalse(ok)
        self.assertIn("disk full", msg)
        self.assertIsNone(record)


# ====================================================== W8: the food kinds
class TestFoodKindsReachTheServer(ApiOfflineCase):
    """dashbored-v3.py used to answer 'bad kind' for these three."""

    def test_the_wire_format_for_a_food_log_is_a_whole_collection_write(self):
        self.go_offline()
        entry = {"id": "e1", "ts": f"{DAY}T12:00", "totals": {"na": 400}}
        api._queue_write("/api/foodzen/save",
                         json_body={"kind": "eatzen", "data": [entry]},
                         coalesce_key="foodzen:eatzen")
        self.go_online()
        api.sync()

        sent = self.server.posted_to("/api/foodzen/save")
        self.assertEqual(sent, [{"kind": "eatzen", "data": [entry]}])

    def test_a_reading_made_offline_is_queued_not_refused(self):
        self.go_offline()
        reading = {"id": "b1", "ts": f"{DAY}T08:00", "sys": 128, "dia": 82}
        result = api._queue_write("/api/foodzen/save",
                                  json_body={"kind": "bpm", "data": [reading]},
                                  coalesce_key="foodzen:bpm")
        self.assertTrue(result.ok)
        self.assertEqual(api.pending_count(), 1)


# =========================================================== W9: the Future
SCAN = {"elements": [
    {"type": "g", "id": 10897, "lat": 37.77129, "lon": -122.218549, "dist": 236,
     "tags": {"name": "San Leandro Street", "bridge": "yes", "highway": "secondary"}},
    {"type": "g", "id": 4242, "lat": 37.715573, "lon": -119.676892, "dist": 999,
     "tags": {"name": "Tunnel View", "tourism": "viewpoint",
              "image": "Tunnel View.jpg"}},
    {"type": "g", "id": 77, "lat": 37.80, "lon": -122.25, "dist": 10,
     "tags": {"highway": "residential"}},          # no name: not a gem
], "count": 3}


class TestFutureLogic(unittest.TestCase):
    """Pure ports of the website's functions. No server, no store."""

    def test_categories_match_the_websites_order_of_precedence(self):
        self.assertEqual(F.gem_classify({"tourism": "viewpoint"}), "pretty")
        self.assertEqual(F.gem_classify({"waterway": "waterfall"}), "falls")
        self.assertEqual(F.gem_classify({"tunnel": "yes", "highway": "secondary",
                                         "bridge": "yes"}), "tunnels")
        self.assertEqual(F.gem_classify({"bridge": "yes", "highway": "secondary"}),
                         "bridges")
        self.assertEqual(F.gem_classify({}, "Old Water Temple"), "wtemples")
        self.assertEqual(F.gem_classify({"shop": "charity"}), "thrift")
        self.assertEqual(F.gem_classify({"nothing": "known"}), "pretty")

    def test_every_category_it_can_return_has_a_chip_to_show_it_with(self):
        for cat in ("pretty", "falls", "caves", "ruins", "ancient", "dams",
                    "tunnels", "bridges", "lakes", "wtemples", "forests",
                    "beaches", "gardens", "temples", "thrift", "flea", "weird"):
            self.assertIn(cat, F.CAT_BY_ID)

    def test_the_radii_are_the_websites_metres(self):
        self.assertEqual(dict(F.DISTANCES), {25: 40234, 50: 80467, 100: 160934})

    def test_a_nameless_row_is_dropped_rather_than_shown_as_unnamed(self):
        spots = F.spots_from(SCAN["elements"])
        self.assertEqual([s["name"] for s in spots],
                         ["San Leandro Street", "Tunnel View"])
        self.assertEqual(spots[0]["oid"], "g/10897")
        self.assertEqual(spots[0]["cat"], "bridges")
        self.assertLess(spots[0]["dist_m"], spots[1]["dist_m"])

    def test_a_spots_own_photo_tags_are_carried_to_the_photo_lookup(self):
        spots = F.spots_from(SCAN["elements"])
        self.assertEqual(spots[1]["imgs"], ["File:Tunnel View.jpg"])
        self.assertEqual(F.tag_images({"image": "https://example/x.jpg"}), [],
                         "a bare URL is not a Commons title")

    def test_the_street_view_key_is_the_bare_gem_id(self):
        self.assertEqual(F.gid("g/10897"), "10897")
        self.assertEqual(F.gid("curated:weird:Mystery Spot"),
                         "curated_weird_Mystery_Spot")
        self.assertEqual(F.gid(""), "")


class TestFutureOffline(ApiOfflineCase):
    def scan_path(self, cats=("pretty",), radius=80467):
        joined = ",".join(sorted(cats))
        lat, lon = F.HOME
        return (f"/api/future/scan?lat={lat}&lon={lon}&r={radius}"
                f"&cats={joined.replace(',', '%2C')}")

    def seed_scan(self, cats=("pretty",)):
        self.server.data[self.scan_path(cats)] = json.loads(json.dumps(SCAN))
        got = F.scan(list(cats), 80467)
        self.assertIsNotNone(got, "the fake server should have answered")
        return got

    def seed_gems(self, gems=()):
        self.server.data[F.GEMS_PATH] = {"gems": list(gems)}
        self.assertIsNotNone(F.saved())

    def test_a_scan_answers_from_the_device_with_the_server_down(self):
        self.seed_scan()
        self.go_offline()
        got = F.scan(["pretty"], 80467)
        self.assertEqual([s["name"] for s in got],
                         ["San Leandro Street", "Tunnel View"])

    def test_a_scan_never_run_stays_none_rather_than_inventing_gems(self):
        self.go_offline()
        self.assertIsNone(F.scan(["falls"], 160934))
        self.assertIsNone(F.saved())

    def test_no_categories_asks_the_server_nothing(self):
        before = len(self.server.gets)
        self.assertEqual(F.scan([], 80467), [])
        self.assertEqual(len(self.server.gets), before)

    def test_saving_a_gem_offline_queues_it_and_shows_it_at_once(self):
        self.seed_gems()
        spots = self.seed_scan()
        self.go_offline()

        ok, msg = F.save_gem(spots[1])

        self.assertTrue(ok, msg)
        self.assertEqual(msg, api.QUEUED_MESSAGE)
        self.assertEqual(api.pending_count(), 1)
        names = [g["name"] for g in F.saved()]
        self.assertEqual(names, ["Tunnel View"])
        self.assertTrue(F.saved()[0]["pending"],
                        "a gem the server has not seen must say so")

    def test_a_queued_gem_has_no_id_so_it_cannot_be_deleted_by_one(self):
        """Deleting by an id the server never issued would leave it saved."""
        self.seed_gems()
        spots = self.seed_scan()
        self.go_offline()
        F.save_gem(spots[1])

        self.assertEqual(F.saved()[0]["id"], "")
        ok, msg = F.delete_gem(F.saved()[0]["id"])
        self.assertFalse(ok)
        self.assertEqual(api.pending_count(), 1, "and nothing extra was queued")

    def test_the_queued_gem_reaches_the_server_with_signal(self):
        self.seed_gems()
        spots = self.seed_scan()
        self.go_offline()
        F.save_gem(spots[1])
        self.go_online()

        self.assertEqual(api.sync()["sent"], 1)
        sent = self.server.posted_to(F.GEMS_PATH)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["oid"], "g/4242")
        self.assertEqual(sent[0]["name"], "Tunnel View")
        self.assertEqual(sent[0]["cat"], "pretty")

    def test_saving_one_already_on_the_list_does_not_queue_a_duplicate(self):
        self.seed_gems([{"id": "g1", "oid": "g/4242", "name": "Tunnel View",
                         "lat": 37.715573, "lon": -119.676892, "cat": "pretty"}])
        spots = self.seed_scan()
        self.go_offline()

        ok, msg = F.save_gem(spots[1])
        self.assertTrue(ok)
        self.assertEqual(msg, "already on the list")
        self.assertEqual(api.pending_count(), 0)

    def test_removing_a_saved_gem_offline_queues_and_disappears(self):
        self.seed_gems([{"id": "g1", "oid": "g/4242", "name": "Tunnel View",
                         "lat": 37.715573, "lon": -119.676892, "cat": "pretty"}])
        self.go_offline()

        ok, msg = F.delete_gem("g1")

        self.assertTrue(ok, msg)
        self.assertEqual(F.saved(), [])
        self.go_online()
        self.assertEqual(api.sync()["sent"], 1)
        self.assertEqual(self.server.posted_to("/api/future/gems/delete"),
                         [{"id": "g1"}])

    def test_street_view_says_no_rather_than_showing_a_broken_frame(self):
        self.server.data["/api/future/sv?gid=10897"] = {"ok": 0}
        url, why = F.street_view({"oid": "g/10897"})
        self.assertIsNone(url)
        self.assertEqual(why, "no street view for this spot")

    def test_street_view_url_carries_the_server_it_came_from(self):
        self.server.data["/api/future/sv?gid=10897"] = {
            "ok": 1, "img": "/api/future/sv?img=10897.jpg", "date": "2025-05"}
        url, caption = F.street_view({"oid": "g/10897"})
        self.assertEqual(url, self.base + "/api/future/sv?img=10897.jpg")
        self.assertEqual(caption, "Imagery 2025-05")


class TestFuturePageBuilds(ApiOfflineCase):
    """A shape change in the page is a black tab; build it both ways."""

    class StubPage:
        def __init__(self):
            self.controls = []
            self.services = []
            self.overlay = []

        def update(self, *a, **k):
            pass

        def add(self, *controls):
            self.controls.extend(controls)

        def open(self, *a, **k):
            pass

        def close(self, *a, **k):
            pass

        def run_task(self, *a, **k):
            raise RuntimeError("no event loop in a test")

    def build_once(self):
        import flet as ft
        built = F.build(self.StubPage())
        self.assertIsInstance(built, ft.Control)
        return built

    def test_it_builds_with_nothing_cached_and_no_server(self):
        F._S.update({"cats": list(F.DEFAULT_CATS), "dist": 50,
                     "spots": None, "note": ""})
        self.go_offline()
        self.build_once()

    def test_the_offline_notice_never_claims_sample_data_or_a_lost_write(self):
        """There is no mock Future data, and a queued gem does reach the server.

        Which is why this page does not use `_forms.offline_banner`: its
        wording says both of those things, and both would be false here.
        """
        import flet as ft
        from flet_app.ui import PAL
        for somedays in (None, [], [{"name": "x"}]):
            with self.subTest(somedays=somedays):
                notice = F.not_connected(PAL(), somedays)
                self.assertIsInstance(notice, ft.Control)
                said = " ".join(_all_text(notice))
                self.assertIn("Not connected", said)
                self.assertNotIn("sample data", said)
                self.assertNotIn("will not save", said)

    def test_it_builds_with_a_scan_and_a_saved_list(self):
        self.server.data[F.GEMS_PATH] = {"gems": [
            {"id": "g1", "oid": "g/4242", "name": "Tunnel View",
             "lat": 37.715573, "lon": -119.676892, "cat": "pretty"}]}
        F._S.update({"cats": ["pretty"], "dist": 25,
                     "spots": F.spots_from(SCAN["elements"]), "note": "2 found"})
        self.addCleanup(F._S.update, {"spots": None, "note": ""})
        self.build_once()

    def test_extras_still_builds_with_the_future_tab_in_it(self):
        import flet as ft
        from flet_app.pages import extras
        self.go_offline()
        self.assertIsInstance(extras.build(self.StubPage()), ft.Control)


if __name__ == "__main__":
    unittest.main()
