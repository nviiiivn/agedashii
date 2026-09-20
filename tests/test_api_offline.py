"""The API layer has to keep working when the network does not.

Every test here runs against a fake opener - `config._opener` is swapped for
an object that answers out of a dict - so nothing in this file touches a
socket, and "the server is down" is a boolean rather than a firewall rule.
The store is a tempfile, thrown away with the test.

What is being pinned down:

  * a read still answers after the server goes away, and after a restart
  * a write made with no signal is queued, not lost, and says so
  * that write is visible to the very next read, with no round trip
  * the queue drains when the server comes back, carrying the final state
  * being offline never costs a queued write one of its retry attempts

The last one is the one that matters most: charging an attempt for "no
signal" is how a week in a faraday cage parks every pending write.
"""
import email.message
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from flet_app import api, config
from flet_app.store import Store


class Response:
    """What `_opener.open` hands back: a context manager with .read()."""

    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else str(body).encode()

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, payload, headers=None):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    fp = io.BytesIO(body.encode() if isinstance(body, str) else body)
    return urllib.error.HTTPError(url, code, "test", hdrs, fp)


class FakeServer:
    """A dashbored that can be switched off, with no socket anywhere near it."""

    MISSING = object()

    def __init__(self, base):
        self.base = base
        self.data = {}          # path -> what a GET answers with
        self.posts = []         # (path, decoded body) in the order received
        self.gets = []
        self.up = True
        self.mode = "up"        # 'up' | 'down' | 'gateway' | 'cloudflare'
        self.post_error = None  # (code, payload) every POST answers with

    # -- the bit config._opener provides -------------------------------------
    def open(self, target, timeout=None):
        url = target if isinstance(target, str) else target.full_url
        path = url[len(self.base):] if url.startswith(self.base) else url
        method = "GET" if isinstance(target, str) else target.get_method()
        body = b"" if isinstance(target, str) else (target.data or b"")

        if not self.up or self.mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError("no route to host"))
        if self.mode == "gateway":
            raise http_error(url, 302, "", {"Location": "https://auth.example/login"})
        if self.mode == "cloudflare":
            raise http_error(url, 403, "<html>denied</html>", {"Server": "cloudflare"})

        if method == "GET":
            self.gets.append(path)
            if path == config.PROBE_PATH:
                return Response(b'{"ok": true}')
            value = self.data.get(path, self.MISSING)
            if value is self.MISSING:
                raise http_error(url, 404, {"error": f"no such path {path}"})
            return Response(json.dumps(value))

        self.posts.append((path, self._decode(body)))
        if self.post_error:
            raise http_error(url, self.post_error[0], self.post_error[1])
        return Response(b'{"ok": true}')

    @staticmethod
    def _decode(body):
        text = body.decode() if isinstance(body, bytes) else str(body)
        try:
            return json.loads(text)
        except ValueError:
            return text

    # -- convenience ---------------------------------------------------------
    def posted_to(self, path):
        return [b for p, b in self.posts if p.split("?")[0] == path]


BLANK_FOODZEN = {"templates": [], "lists": [], "need": [],
                 "pantry": {"items": []}, "recipes": [], "purchases": []}


class ApiOfflineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "store.sqlite3"

        self.base = "http://dashbored.test:6900"
        self.server = FakeServer(self.base)

        self._old_opener, config._opener = config._opener, self.server
        self._old_url, config.SERVER_URL = config.SERVER_URL, self.base
        self._old_status = dict(config.STATUS)
        config.STATUS.update({"ok": False, "detail": "not checked yet",
                              "url": self.base, "at": 0.0})

        self.store = self.new_store()
        self._old_store = api.set_store(self.store)
        self._old_ttl, api.TTL = api.TTL, 3600   # a read stays fresh unless asked
        api._invalid.clear()
        api._invalid_all = 0.0
        self.addCleanup(self.restore)

    def new_store(self):
        """A store on the same file - i.e. what a restart of the app sees."""
        return Store(self.db, ttl=3600, probe=lambda: config.probe(config.SERVER_URL))

    def restore(self):
        self.settle()
        api.set_store(self._old_store)
        api.TTL = self._old_ttl
        api._invalid.clear()
        api._invalid_all = 0.0
        config._opener = self._old_opener
        config.SERVER_URL = self._old_url
        config.STATUS.clear()
        config.STATUS.update(self._old_status)
        self.store.close()

    @staticmethod
    def settle(timeout=2.0):
        """Wait for any background refresh/probe thread the read path started."""
        for t in threading.enumerate():
            if t is threading.current_thread():
                continue
            if t.name.startswith(("store-refresh", "nvii-probe")):
                t.join(timeout)

    # -- helpers -------------------------------------------------------------
    def seed_foodzen(self, state=None):
        """One successful connection, so the device has a copy to edit."""
        self.server.data[api.FOODZEN_PATH] = json.loads(
            json.dumps(state or BLANK_FOODZEN))
        got = api._get(api.FOODZEN_PATH)
        self.assertIsNotNone(got, "the fake server should have answered")
        return got

    def go_offline(self):
        self.server.up = False
        self.store.set_online(False, "test: unplugged")

    def go_online(self):
        self.server.up = True
        self.store.set_online(True, "test: plugged back in")

    def grocery_names(self):
        fz = api.foodzen() or {}
        return list(fz.get("list") or [])


class TestReadsSurviveTheServer(ApiOfflineCase):
    def test_a_read_answers_from_the_device_with_the_server_down(self):
        self.seed_foodzen()
        self.go_offline()
        before = len(self.server.gets)

        self.assertEqual(api._get(api.FOODZEN_PATH), BLANK_FOODZEN)
        self.assertEqual(len(self.server.gets), before,
                         "a fresh cached read must not go near the network")
        self.assertEqual(api.provenance(api.FOODZEN_PATH), api.LIVE)

    def test_a_read_survives_a_restart_with_no_network(self):
        """The whole point: kill the app, lose the signal, still have the data."""
        self.seed_foodzen({"lists": [{"id": "l1", "name": "shopping",
                                      "items": [{"name": "Rice", "qty": 1}]}]})
        self.store.close()
        self.go_offline()

        self.store = self.new_store()          # a cold launch, same database
        api.set_store(self.store)
        before = len(self.server.gets)
        self.assertEqual(self.grocery_names(), ["Rice"])
        self.assertEqual(len(self.server.gets), before,
                         "and without pretending it could ask")

    def test_stale_data_is_still_returned_when_the_refresh_fails(self):
        self.seed_foodzen()
        self.go_offline()
        api.refresh()                          # what a write or a settings change does

        self.assertEqual(api._get(api.FOODZEN_PATH), BLANK_FOODZEN)
        self.settle()
        self.assertEqual(api.provenance(api.FOODZEN_PATH), api.CACHED)

    def test_an_endpoint_never_fetched_stays_none_rather_than_inventing_data(self):
        """No copy means no copy. The pages show sample data and say so."""
        self.go_offline()
        self.assertIsNone(api._get("/api/weather"))
        self.assertEqual(api.provenance("/api/weather"), api.NONE)

    def test_status_live_still_tracks_the_last_request(self):
        self.seed_foodzen()
        self.assertTrue(api.STATUS_LIVE())
        self.go_offline()
        api._get("/api/trips")                 # never cached, so it goes to the wire
        self.assertFalse(api.STATUS_LIVE())

    def test_the_old_cache_shim_marks_stale_instead_of_forgetting(self):
        """pages/_wat2do_parts.py calls M._cache.pop to force a re-read."""
        self.seed_foodzen()
        api._cache.pop(api.FOODZEN_PATH, None)
        self.go_offline()

        self.assertEqual(api._get(api.FOODZEN_PATH), BLANK_FOODZEN)
        self.settle()


class TestCacheIsScopedToItsServer(ApiOfflineCase):
    """A cached row is one server's answer. It may never stand in for another's."""

    def point_at(self, server):
        config._opener = server
        config.SERVER_URL = server.base

    def test_a_cached_answer_is_not_served_for_a_different_address(self):
        self.server.data["/api/trips"] = [{"id": "t0", "name": "from A"}]
        self.assertEqual([t["name"] for t in api.trips()], ["from A"])

        elsewhere = FakeServer("http://elsewhere.test:6900")
        elsewhere.up = False
        self.point_at(elsewhere)
        self.assertIsNone(api._get("/api/trips"),
                          "this server has never answered; we have nothing of its")
        self.assertEqual(api.provenance("/api/trips"), api.NONE)

        self.point_at(self.server)
        self.assertEqual([t["name"] for t in api.trips()], ["from A"],
                         "and switching back finds its rows waiting")

    def test_a_restart_pointed_somewhere_else_shows_nothing_rather_than_the_old_server(self):
        self.server.data["/api/trips"] = [{"id": "t0", "name": "from A"}]
        self.assertEqual(len(api.trips()), 1)
        self.store.close()

        elsewhere = FakeServer("http://elsewhere.test:6900")
        elsewhere.up = False
        self.point_at(elsewhere)
        self.store = self.new_store()          # cold launch, same database
        api.set_store(self.store)
        self.assertIsNone(api._get("/api/trips"))

    def test_refresh_makes_the_next_read_ask_again_before_answering(self):
        """What Settings relies on: a new address takes effect immediately."""
        self.server.data["/api/trips"] = [{"id": "t0", "name": "old"}]
        self.assertEqual([t["name"] for t in api.trips()], ["old"])

        self.server.data["/api/trips"] = [{"id": "t0", "name": "new"}]
        self.assertEqual([t["name"] for t in api.trips()], ["old"],
                         "still inside the TTL, so no round trip")

        api.refresh()
        self.assertEqual([t["name"] for t in api.trips()], ["new"],
                         "refresh means ask again now, not show the old one meanwhile")

    def test_a_queued_write_is_not_shown_against_a_different_address(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        elsewhere = FakeServer("http://elsewhere.test:6900")
        elsewhere.up = False
        self.point_at(elsewhere)
        self.assertIsNone(api._get(api.FOODZEN_PATH))
        self.assertEqual(api.pending_count(), 1, "but the write is still queued")


class TestWritesAreQueuedNotLost(ApiOfflineCase):
    def test_an_offline_write_is_queued_and_reports_it_honestly(self):
        self.seed_foodzen()
        self.go_offline()

        ok, msg = api.foodzen_add("Milk")
        self.assertTrue(ok, msg)
        self.assertEqual(msg, api.QUEUED_MESSAGE)
        self.assertNotEqual(msg, "saved")      # the server has NOT got it
        self.assertEqual(api.pending_count(), 1)
        self.assertEqual(self.server.posts, [], "nothing can have gone out")

    def test_the_queued_write_is_visible_to_the_very_next_read(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        self.assertIn("Milk", self.grocery_names())
        self.assertEqual(api.provenance(api.FOODZEN_PATH), api.QUEUED)

    def test_a_queued_write_survives_a_restart(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")
        self.store.close()

        self.store = self.new_store()
        api.set_store(self.store)
        self.assertEqual(api.pending_count(), 1)
        self.assertIn("Milk", self.grocery_names())

    def test_the_queue_drains_when_the_server_comes_back(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")
        self.go_online()

        result = api.sync()
        self.assertEqual(result["sent"], 1, result)
        self.assertFalse(result["offline"])
        self.assertEqual(api.pending_count(), 0)

        sent = self.server.posted_to("/api/foodzen/save")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["kind"], "lists")
        self.assertEqual([i["name"] for i in sent[0]["data"][0]["items"]], ["Milk"])
        self.assertEqual(api.provenance(api.FOODZEN_PATH), api.LIVE,
                         "once the server has it, it is no longer ours alone")

    def test_a_write_made_online_says_saved_and_goes_straight_out(self):
        self.seed_foodzen()
        ok, msg = api.foodzen_add("Milk")
        self.assertEqual((ok, msg), (True, "saved"))
        self.assertEqual(api.pending_count(), 0)
        self.assertEqual(len(self.server.posted_to("/api/foodzen/save")), 1)

    def test_foodzen_refuses_to_guess_when_the_device_has_no_copy(self):
        self.go_offline()
        ok, msg = api.foodzen_add("Milk")
        self.assertFalse(ok)
        self.assertIn("this device", msg)
        self.assertEqual(api.pending_count(), 0)


class TestOfflineCostsNoAttempts(ApiOfflineCase):
    def test_being_offline_never_burns_a_retry_attempt(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        for _ in range(8):          # more than the store's max_attempts
            self.assertTrue(api.sync()["offline"])

        item = self.store.pending()[0]
        self.assertEqual(item.attempts, 0)
        self.assertEqual(item.state, "pending")
        self.assertEqual(self.store.stats()["parked"], 0)

    def test_an_auth_gateway_is_offline_not_a_bad_write(self):
        """A 302 to a login page is the off-LAN state, not a rejected write."""
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        self.server.up = True
        self.server.mode = "gateway"
        self.store.set_online(True, "test")
        for _ in range(8):
            api.sync()

        item = self.store.pending()[0]
        self.assertEqual(item.attempts, 0)
        self.assertIn("auth", item.last_error)

    def test_cloudflares_edge_403_is_also_offline(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        self.server.up = True
        self.server.mode = "cloudflare"
        self.store.set_online(True, "test")
        api.sync()

        self.assertEqual(self.store.pending()[0].attempts, 0)

    def test_a_real_rejection_does_count_and_keeps_the_servers_own_words(self):
        self.seed_foodzen()
        self.server.post_error = (500, {"error": "bad bike"})

        ok, msg = api.garage_record_add("cb350", "services", {"label": "oil"})
        self.assertFalse(ok)
        self.assertEqual(msg, "bad bike", "not 'server returned HTTP 500'")

        item = self.store.pending()[0]
        self.assertEqual(item.attempts, 1)
        self.assertEqual(item.last_error, "bad bike")
        self.assertEqual(api.pending_count(), 1, "a refused write is kept, not dropped")


class TestCoalescing(ApiOfflineCase):
    def test_an_afternoon_of_list_edits_collapses_to_one_queued_write(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")
        api.foodzen_add("Eggs")
        api.foodzen_toggle("Milk", True)
        api.foodzen_remove("Eggs")
        api.foodzen_add("Bread")

        self.assertEqual(api.pending_count(), 1)
        self.go_online()
        api.sync()

        sent = self.server.posted_to("/api/foodzen/save")
        self.assertEqual(len(sent), 1, "one POST carrying the final list")
        items = {i["name"]: i for i in sent[0]["data"][0]["items"]}
        self.assertEqual(set(items), {"Milk", "Bread"})
        self.assertTrue(items["Milk"]["done"])

    def test_different_collections_queue_separately(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")        # lists
        api.foodzen_need_add("Rice")   # need
        api.foodzen_pantry_add("Salt")  # pantry

        self.assertEqual(api.pending_count(), 3)
        self.assertEqual({i.coalesce_key for i in self.store.pending()},
                         {"foodzen:lists", "foodzen:need", "foodzen:pantry"})

    def test_appends_are_never_coalesced(self):
        """Two services logged offline are two services. The server assigns ids."""
        self.go_offline()
        api.garage_record_add("cb350", "services", {"label": "oil change"})
        api.garage_record_add("cb350", "services", {"label": "chain"})
        api.journal_new("Mechanicry", "monday")
        api.journal_new("Mechanicry", "tuesday")

        self.assertEqual(api.pending_count(), 4)
        self.assertEqual([i.coalesce_key for i in self.store.pending()],
                         [None, None, None, None])

        self.go_online()
        api.sync()
        self.assertEqual(len(self.server.posted_to("/api/garage/records/add")), 2)
        self.assertEqual(len(self.server.posted_to("/api/journal/new")), 2)

    def test_repeated_edits_to_one_note_collapse_per_file(self):
        self.go_offline()
        api.journal_save("Mechanicry/mon.md", "one")
        api.journal_save("Mechanicry/mon.md", "two")
        api.journal_save("Mechanicry/tue.md", "other")

        self.assertEqual(api.pending_count(), 2)
        self.go_online()
        api.sync()
        bodies = {b["path"]: b["content"]
                  for b in self.server.posted_to("/api/journal/save")}
        self.assertEqual(bodies, {"Mechanicry/mon.md": "two",
                                  "Mechanicry/tue.md": "other"})


class TestOtherWritesKeepTheirContract(ApiOfflineCase):
    def test_every_write_helper_still_returns_ok_and_a_message(self):
        self.go_offline()
        calls = [
            lambda: api.journal_save("a/b.md", "hi"),
            lambda: api.journal_delete("a/b.md"),
            lambda: api.journal_new("Mechanicry", "title"),
            lambda: api.garage_save("cb350/notes.md", "hi"),
            lambda: api.garage_spec_save("cb350", {"engine": "350cc"}),
            lambda: api.garage_record_add("cb350", "mileage", {"odo": 100}),
            lambda: api.trip_save({"id": "t1", "name": "coast"}),
            lambda: api.vault_save("aitp", "notes/x.md", "hi"),
            lambda: api.wat2do_save({"date": "2026-09-20", "items": [], "pain": 2,
                                     "spoons": 3}),
            lambda: api.ride_save("sunday", [{"lat": 1, "lon": 2, "t": 0}], 1609.0),
        ]
        for call in calls:
            with self.subTest(call=call):
                ok, msg = call()
                self.assertTrue(ok, msg)
                self.assertIsInstance(msg, str)
                self.assertTrue(msg)
        self.assertEqual(api.pending_count(), len(calls))

        self.go_online()
        result = api.sync()
        self.assertEqual(result["sent"], len(calls), result)
        self.assertEqual(api.pending_count(), 0)

    def test_validation_failures_are_unchanged_and_never_queue(self):
        cases = [
            (api.journal_new("Nope", "x"), "unknown category"),
            (api.journal_new("Mechanicry", "  "), "title required"),
            (api.garage_record_add("cb350", "nope", {"a": 1}), "unknown record type"),
            (api.garage_record_add("cb350", "mileage", {}), "empty record"),
            (api.trip_save({"name": "no id"}), "id and name required"),
            (api.vault_save("", "x", "y"), "host and path required"),
            (api.ride_save("x", []), "no points recorded"),
        ]
        for (ok, msg), expected in cases:
            self.assertFalse(ok)
            self.assertIn(expected, msg)
        self.assertEqual(api.pending_count(), 0)

    def test_a_queued_trip_shows_up_in_the_cached_list(self):
        self.server.data["/api/trips"] = [{"id": "t0", "name": "old"}]
        self.assertEqual(len(api.trips()), 1)
        self.go_offline()

        ok, msg = api.trip_save({"id": "t1", "name": "coast run"})
        self.assertEqual((ok, msg), (True, api.QUEUED_MESSAGE))
        self.assertEqual([t["name"] for t in api.trips()], ["old", "coast run"])

    def test_a_queued_garage_record_shows_up_in_the_cached_records(self):
        path = api._garage_records_path("cb350", "services")
        self.server.data[path] = {"records": [{"id": 1, "label": "valves"}]}
        self.assertEqual(len(api.garage_records("cb350", "services")), 1)
        self.go_offline()

        api.garage_record_add("cb350", "services", {"label": "oil"})
        self.assertEqual([r["label"] for r in api.garage_records("cb350", "services")],
                         ["valves", "oil"])

    def test_a_queued_note_reads_back_as_what_was_typed(self):
        rel = "Mechanicry/mon.md"
        self.server.data[f"/api/journal/read?path={urllib.parse.quote(rel)}"] = {
            "content": "old text"}
        self.assertEqual(api.journal_read(rel), "old text")
        self.go_offline()

        api.journal_save(rel, "new text")
        self.assertEqual(api.journal_read(rel), "new text")

    def test_a_queued_wat2do_tick_reads_back_ticked(self):
        path = "/api/wat2do/state?date=2026-09-20"
        self.server.data[path] = {"date": "2026-09-20", "items": ["Meds", "Walk"],
                                  "checks": {}, "pain": 0, "spoons": 3}
        state = api.wat2do_state("2026-09-20")
        self.assertFalse(state["items"][0]["done"])
        self.go_offline()

        state["items"][0]["done"] = True
        ok, msg = api.wat2do_save(state)
        self.assertEqual((ok, msg), (True, api.QUEUED_MESSAGE))

        again = api.wat2do_state("2026-09-20")
        self.assertTrue(again["items"][0]["done"])
        self.assertEqual(again["pain"], 0)


class TestSyncSurface(ApiOfflineCase):
    def test_pending_count_and_label_are_what_the_ui_needs(self):
        self.seed_foodzen()
        self.go_offline()
        self.assertEqual(api.sync_status()["label"], "")

        api.foodzen_add("Milk")
        self.assertEqual(api.pending_count(), 1)
        self.assertEqual(api.sync_status()["label"], "1 change waiting")

        api.foodzen_need_add("Rice")
        api.foodzen_pantry_add("Salt")
        status = api.sync_status()
        self.assertEqual(status["pending"], 3)
        self.assertEqual(status["label"], "3 changes waiting")
        self.assertFalse(status["online"])
        self.assertFalse(status["live"])

    def test_sync_reports_offline_without_raising(self):
        self.seed_foodzen()
        self.go_offline()
        api.foodzen_add("Milk")

        result = api.sync()
        self.assertTrue(result["offline"])
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["pending"], 1)

    def test_a_parked_write_is_visible_and_can_be_retried(self):
        self.seed_foodzen()
        self.server.post_error = (500, {"error": "bad bike"})
        api.foodzen_add("Milk")
        for _ in range(6):
            self.store.set_online(True, "test")
            api.sync(limit=None)
            for item in self.store.pending():
                self.store._note_error(item.id, item.last_error, bump=False,
                                       next_try_at=0)     # skip the backoff wait

        status = api.sync_status()
        self.assertEqual(status["parked"], 1)
        self.assertIn("refused", status["label"])

        self.server.post_error = None
        self.assertEqual(api.retry_parked(), 1)
        self.assertEqual(api.sync()["sent"], 1)
        self.assertEqual(api.pending_count(), 0)


class TestPagesStillBuild(ApiOfflineCase):
    """The read path feeds real pages; a shape change here is a black screen."""

    def test_tools_garage_and_extras_build_offline(self):
        import flet as ft

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

        from flet_app.pages import extras, garage, tools
        self.go_offline()
        for module in (tools, garage, extras):
            with self.subTest(page=module.__name__):
                built = module.build(StubPage())
                self.assertIsInstance(built, ft.Control)


if __name__ == "__main__":
    unittest.main()
