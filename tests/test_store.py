"""Tests for agedashii_tofu/store.py — the offline-first cache and write queue.

Every test here runs with the radio off, by construction: the store never
opens a socket, and `flush` is handed a fake `send_fn`. That is the whole
point of injecting the transport — the promise this module makes ("a phone
with no signal is fully usable and nothing is lost") is only believable if it
can be checked without a server.

The cases that matter most are the ones about *not losing writes*:

  - a failed send keeps the item, with its payload byte-for-byte intact
  - being offline costs an item no retries at all, so a week in a tunnel does
    not quietly park the queue
  - an item that can never succeed stops blocking the ones behind it, and is
    parked rather than deleted

and the one about the user's actual experience: a write she just made is
visible to the next read immediately, before any server has heard of it.
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu import store as S  # noqa: E402


class StoreCase(unittest.TestCase):
    """Base: a real SQLite file in a throwaway directory, torn down after."""

    max_attempts = 5

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "store.sqlite3"
        self.s = S.Store(self.db, max_attempts=self.max_attempts)
        self.addCleanup(self.s.close)


class Recorder:
    """A send_fn that records what it was asked to send, and can misbehave."""

    def __init__(self, behaviour=None):
        self.seen = []
        self.behaviour = behaviour or (lambda item: None)

    def __call__(self, item):
        self.seen.append(item)
        return self.behaviour(item)

    @property
    def paths(self):
        return [i.path for i in self.seen]

    @property
    def ids(self):
        return [i.id for i in self.seen]


# --------------------------------------------------------------------- cache
class TestCache(StoreCase):
    def test_round_trip_preserves_structure(self):
        data = {"lists": [{"id": "fz-l-1", "items": [{"name": "Milk", "qty": 2}]}],
                "need": [], "n": 1.5, "flag": True, "nil": None}
        self.s.put("/api/foodzen/state", data)
        hit = self.s.get("/api/foodzen/state")
        self.assertEqual(hit.data, data)
        self.assertEqual(hit.source, "server")
        self.assertEqual(hit.rev, S.revision(data))

    def test_missing_is_none_and_is_distinct_from_a_cached_null(self):
        # The UI needs to tell "never fetched" from "the server said null":
        # one shows a spinner, the other shows an empty state.
        self.assertIsNone(self.s.get("/api/nope"))
        self.s.put("/api/null", None)
        hit = self.s.get("/api/null")
        self.assertIsNotNone(hit)
        self.assertIsNone(hit.data)

    def test_put_overwrites_in_place(self):
        self.s.put("/api/trips", [1])
        self.s.put("/api/trips", [1, 2])
        self.assertEqual(self.s.get_data("/api/trips"), [1, 2])
        self.assertEqual(self.s.stats()["cached"], 1)

    def test_staleness_is_measured_against_fetched_at(self):
        self.s.put("/api/weather", {"t": 12}, fetched_at=1000.0)
        hit = self.s.get("/api/weather")
        self.assertEqual(hit.age(now=1030.0), 30.0)
        self.assertFalse(hit.is_stale(ttl=60, now=1030.0))
        self.assertTrue(hit.is_stale(ttl=10, now=1030.0))
        self.assertFalse(self.s.is_stale("/api/weather", ttl=60, now=1030.0))
        self.assertTrue(self.s.is_stale("/api/weather", ttl=10, now=1030.0))

    def test_a_path_we_have_never_seen_counts_as_stale(self):
        self.assertTrue(self.s.is_stale("/api/never"))

    def test_forget_and_clear(self):
        self.s.put("/a", 1)
        self.s.put("/b", 2)
        self.assertTrue(self.s.forget("/a"))
        self.assertFalse(self.s.forget("/a"))
        self.assertEqual(self.s.clear_cache(), 1)
        self.assertIsNone(self.s.get("/b"))

    def test_clear_cache_leaves_the_outbox_alone(self):
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "lists"})
        self.s.clear_cache()
        self.assertEqual(self.s.pending_count(), 1)

    def test_default_db_path_prefers_flets_durable_dir(self):
        import os
        old = os.environ.get("FLET_APP_STORAGE_DATA")
        os.environ["FLET_APP_STORAGE_DATA"] = self.tmp.name
        try:
            self.assertEqual(S.default_db_path().parent, Path(self.tmp.name))
        finally:
            if old is None:
                del os.environ["FLET_APP_STORAGE_DATA"]
            else:
                os.environ["FLET_APP_STORAGE_DATA"] = old

    def test_memory_databases_are_refused(self):
        # Per-thread connections would each get a private empty database, so
        # this has to fail loudly rather than behave as several stores.
        with self.assertRaises(ValueError):
            S.Store(":memory:")


class TestRead(StoreCase):
    def test_read_serves_cache_without_calling_the_network(self):
        self.s.put("/api/feeds", ["cached"])
        calls = []
        hit = self.s.read("/api/feeds", lambda: calls.append(1) or ["fresh"],
                          ttl=9999, background=False)
        self.assertEqual(hit.data, ["cached"])
        self.assertEqual(calls, [], "a fresh cache entry must not trigger a fetch")

    def test_read_refreshes_when_stale(self):
        self.s.put("/api/feeds", ["old"], fetched_at=0.0)
        self.s.set_online(True)
        hit = self.s.read("/api/feeds", lambda: ["new"], ttl=1, background=False)
        self.assertEqual(hit.data, ["new"])
        self.assertEqual(self.s.get("/api/feeds").source, "server")

    def test_read_with_nothing_cached_returns_none_and_does_not_raise(self):
        self.assertIsNone(self.s.read("/api/cold", None))

    def test_a_failed_refresh_keeps_the_last_good_value(self):
        # Stale data the user has seen before beats a blank screen; it is
        # labelled by fetched_at either way.
        self.s.put("/api/feeds", ["good"], fetched_at=0.0)
        self.s.set_online(True)

        def boom():
            raise OSError("no route to host")

        self.assertFalse(self.s.refresh("/api/feeds", boom))
        self.assertEqual(self.s.get_data("/api/feeds"), ["good"])
        self.assertFalse(self.s.online, "a failed fetch should mark us offline")

    def test_a_fetch_returning_none_is_treated_as_a_failure_not_as_data(self):
        # api.py's _get returns None on failure. Caching that would replace
        # real data with a null.
        self.s.put("/api/feeds", ["good"], fetched_at=0.0)
        self.assertFalse(self.s.refresh("/api/feeds", lambda: None))
        self.assertEqual(self.s.get_data("/api/feeds"), ["good"])


class TestOnline(StoreCase):
    def test_flag_defaults_to_offline_until_something_says_otherwise(self):
        self.assertFalse(self.s.online)
        self.assertEqual(self.s.status["detail"], "not checked yet")

    def test_probe_hook_is_injected(self):
        s = S.Store(Path(self.tmp.name) / "p.sqlite3", probe=lambda: True)
        self.addCleanup(s.close)
        self.assertTrue(s.check_online())

    def test_probe_accepts_configs_ok_detail_tuple(self):
        s = S.Store(Path(self.tmp.name) / "q.sqlite3",
                    probe=lambda: (False, "blocked by auth"))
        self.addCleanup(s.close)
        self.assertFalse(s.check_online())
        self.assertEqual(s.status["detail"], "blocked by auth")

    def test_a_probe_that_raises_means_offline_not_a_crash(self):
        s = S.Store(Path(self.tmp.name) / "r.sqlite3",
                    probe=lambda: (_ for _ in ()).throw(OSError("down")))
        self.addCleanup(s.close)
        self.assertFalse(s.check_online())
        self.assertIn("down", s.status["detail"])


# -------------------------------------------------------------------- outbox
class TestOutbox(StoreCase):
    def test_enqueue_records_the_write_verbatim(self):
        payload = {"kind": "lists", "data": [{"id": "x", "items": []}]}
        item_id = self.s.enqueue("post", "/api/foodzen/save", payload)
        [item] = self.s.pending()
        self.assertEqual(item.id, item_id)
        self.assertEqual(item.method, "POST")
        self.assertEqual(item.path, "/api/foodzen/save")
        self.assertEqual(item.payload, payload)
        self.assertEqual(item.attempts, 0)
        self.assertEqual(item.state, S.PENDING)
        self.assertEqual(item.last_error, "")

    def test_items_come_back_in_the_order_they_were_queued(self):
        for i in range(5):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})
        self.assertEqual([i.payload["i"] for i in self.s.pending()], [0, 1, 2, 3, 4])
        self.assertEqual(self.s.pending_count(), 5)

    def test_a_queued_write_survives_the_app_being_killed(self):
        self.s.enqueue("POST", "/api/trips/save", {"id": "t1", "name": "Tahoe"})
        self.s.close()
        reopened = S.Store(self.db)
        self.addCleanup(reopened.close)
        [item] = reopened.pending()
        self.assertEqual(item.payload["name"], "Tahoe")

    def test_discard_is_the_only_way_data_leaves_unsent(self):
        item_id = self.s.enqueue("POST", "/api/x", {})
        self.assertTrue(self.s.discard(item_id))
        self.assertFalse(self.s.discard(item_id))
        self.assertEqual(self.s.pending_count(), 0)


class TestLocalVisibility(StoreCase):
    """The core offline promise: a write shows up before the server sees it."""

    PATH = "/api/foodzen/state"

    def test_a_queued_write_is_visible_to_reads_immediately(self):
        self.s.put(self.PATH, {"need": []})
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "need", "data": ["Eggs"]},
                       local={self.PATH: {"need": ["Eggs"]}})

        hit = self.s.get(self.PATH)
        self.assertEqual(hit.data, {"need": ["Eggs"]})
        self.assertTrue(hit.is_local, "unsynced writes must be labelled as such")
        self.assertEqual(self.s.pending_count(), 1)

    def test_the_local_effect_survives_a_restart(self):
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "need"},
                       local={self.PATH: {"need": ["Eggs"]}})
        self.s.close()
        reopened = S.Store(self.db)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_data(self.PATH), {"need": ["Eggs"]})
        self.assertEqual(reopened.pending_count(), 1)

    def test_a_successful_send_promotes_the_local_copy_to_server_truth(self):
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "need"},
                       local={self.PATH: {"need": ["Eggs"]}})
        self.s.flush(Recorder())
        hit = self.s.get(self.PATH)
        self.assertFalse(hit.is_local)
        self.assertEqual(hit.data, {"need": ["Eggs"]}, "the screen must not flash back")

    def test_an_edit_made_during_the_send_stays_marked_local(self):
        # Otherwise the newer value would be labelled server truth and become
        # the baseline for the next conflict check, which would then lie.
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "need"},
                       local={self.PATH: {"need": ["Eggs"]}})

        def send(item):
            self.s.put(self.PATH, {"need": ["Eggs", "Butter"]}, source="local")

        self.s.flush(send)
        self.assertTrue(self.s.get(self.PATH).is_local)
        self.assertEqual(self.s.get_data(self.PATH), {"need": ["Eggs", "Butter"]})

    def test_a_failed_send_leaves_the_local_change_on_screen(self):
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "need"},
                       local={self.PATH: {"need": ["Eggs"]}})
        self.s.flush(Recorder(lambda i: (_ for _ in ()).throw(RuntimeError("500"))))
        self.assertEqual(self.s.get_data(self.PATH), {"need": ["Eggs"]})
        self.assertTrue(self.s.get(self.PATH).is_local)


class TestCoalescing(StoreCase):
    PATH = "/api/foodzen/state"

    def test_repeated_edits_collapse_into_one_pending_write(self):
        first = self.s.enqueue("POST", "/api/foodzen/save", {"data": [1]},
                               coalesce_key="foodzen:lists")
        second = self.s.enqueue("POST", "/api/foodzen/save", {"data": [1, 2]},
                                coalesce_key="foodzen:lists")
        self.assertEqual(first, second, "coalescing must keep the original slot")
        [item] = self.s.pending()
        self.assertEqual(item.payload, {"data": [1, 2]}, "the newest state wins")
        self.assertEqual(self.s.pending_count(), 1)

    def test_different_keys_do_not_coalesce(self):
        self.s.enqueue("POST", "/api/foodzen/save", {}, coalesce_key="foodzen:lists")
        self.s.enqueue("POST", "/api/foodzen/save", {}, coalesce_key="foodzen:need")
        self.assertEqual(self.s.pending_count(), 2)

    def test_writes_with_no_key_never_coalesce(self):
        # Append endpoints (garage records, journal/new) are not replacements;
        # collapsing two of them would silently drop one.
        self.s.enqueue("POST", "/api/garage/records/add", {"miles": 100})
        self.s.enqueue("POST", "/api/garage/records/add", {"miles": 200})
        self.assertEqual(self.s.pending_count(), 2)

    def test_coalescing_keeps_the_original_server_baseline(self):
        self.s.put(self.PATH, {"v": 0})
        base = S.revision({"v": 0})
        self.s.enqueue("POST", "/api/foodzen/save", {"v": 1},
                       local={self.PATH: {"v": 1}}, coalesce_key="k")
        self.s.enqueue("POST", "/api/foodzen/save", {"v": 2},
                       local={self.PATH: {"v": 2}}, coalesce_key="k")
        [item] = self.s.pending()
        self.assertEqual(item.base_rev, base,
                         "the baseline is the last SERVER copy, not our own edits")

    def test_base_rev_is_taken_from_the_cached_server_copy(self):
        self.s.put(self.PATH, {"v": "srv"})
        self.s.enqueue("POST", "/api/foodzen/save", {"v": "mine"},
                       local={self.PATH: {"v": "mine"}})
        [item] = self.s.pending()
        self.assertEqual(item.base_rev, S.revision({"v": "srv"}))

    def test_base_rev_is_none_when_we_never_had_a_server_copy(self):
        self.s.enqueue("POST", "/api/foodzen/save", {"v": 1},
                       local={self.PATH: {"v": 1}})
        self.assertIsNone(self.s.pending()[0].base_rev)


# --------------------------------------------------------------------- flush
class TestFlushSuccess(StoreCase):
    def test_a_drained_queue_is_empty_and_was_sent_in_order(self):
        for i in range(3):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})
        rec = Recorder()
        result = self.s.flush(rec)
        self.assertEqual(result.sent, 3)
        self.assertTrue(result.ok)
        self.assertEqual(rec.paths, ["/api/p0", "/api/p1", "/api/p2"])
        self.assertEqual(rec.ids, sorted(rec.ids))
        self.assertEqual(self.s.pending_count(), 0)

    def test_send_fn_receives_the_payload_and_the_baseline(self):
        self.s.put("/api/foodzen/state", {"v": 0})
        self.s.enqueue("POST", "/api/foodzen/save", {"kind": "lists", "data": [1]},
                       local={"/api/foodzen/state": {"v": 1}})
        rec = Recorder()
        self.s.flush(rec)
        item = rec.seen[0]
        self.assertEqual(item.payload, {"kind": "lists", "data": [1]})
        self.assertEqual(item.base_rev, S.revision({"v": 0}))
        self.assertIn("/api/foodzen/state", item.local)

    def test_flushing_an_empty_queue_is_a_no_op(self):
        rec = Recorder()
        self.assertEqual(self.s.flush(rec), S.FlushResult())
        self.assertEqual(rec.seen, [])

    def test_success_can_be_reported_as_true_none_or_an_ok_tuple(self):
        for reply in (None, True, (True, "saved")):
            with self.subTest(reply=reply):
                self.s.enqueue("POST", "/api/x", {})
                self.assertEqual(self.s.flush(Recorder(lambda i, r=reply: r)).sent, 1)
                self.assertEqual(self.s.pending_count(), 0)

    def test_a_successful_flush_marks_us_online(self):
        self.s.enqueue("POST", "/api/x", {})
        self.s.flush(Recorder())
        self.assertTrue(self.s.online)


class TestFlushFailure(StoreCase):
    def test_a_rejected_write_is_kept_intact_and_the_error_recorded(self):
        payload = {"kind": "lists", "data": [{"name": "Milk"}]}
        self.s.enqueue("POST", "/api/foodzen/save", payload)
        result = self.s.flush(Recorder(
            lambda i: (_ for _ in ()).throw(RuntimeError("HTTP 500"))))

        self.assertEqual(result.sent, 0)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.ok)
        [item] = self.s.pending()
        self.assertEqual(item.payload, payload, "the payload must survive verbatim")
        self.assertEqual(item.attempts, 1)
        self.assertIn("HTTP 500", item.last_error)
        self.assertEqual(item.state, S.PENDING)

    def test_a_falsy_return_counts_as_failure(self):
        for reply in (False, (False, "server refused the write")):
            with self.subTest(reply=reply):
                item_id = self.s.enqueue("POST", "/api/x", {})
                self.s.flush(Recorder(lambda i, r=reply: r))
                self.assertEqual(self.s.pending_count(), 1)
                self.s.discard(item_id)

    def test_a_failure_backs_off_before_the_next_try(self):
        self.s.enqueue("POST", "/api/x", {})
        boom = Recorder(lambda i: (_ for _ in ()).throw(RuntimeError("nope")))
        self.s.flush(boom, now=1000.0)
        self.assertEqual(len(boom.seen), 1)

        # Immediately after, the item is still pending but is not retried.
        result = self.s.flush(boom, now=1001.0)
        self.assertEqual(len(boom.seen), 1, "should not hammer a failing endpoint")
        self.assertEqual(result.skipped, 1)

        # Once the backoff has elapsed it is tried again.
        self.s.flush(boom, now=1000.0 + S.BACKOFF_BASE + 1)
        self.assertEqual(len(boom.seen), 2)
        self.assertEqual(self.s.pending()[0].attempts, 2)

    def test_a_failure_does_not_hold_up_other_collections(self):
        self.s.enqueue("POST", "/api/bad", {})
        self.s.enqueue("POST", "/api/good", {})

        def send(item):
            if item.path == "/api/bad":
                raise RuntimeError("nope")

        result = self.s.flush(send, now=1000.0)
        self.assertEqual(result.sent, 1)
        self.assertEqual([i.path for i in self.s.pending()], ["/api/bad"])

    def test_a_failure_does_hold_back_later_writes_to_the_same_path(self):
        # Sending #2 before #1 would push a state the server never saw the
        # predecessor of, silently resurrecting whatever #1 removed.
        self.s.enqueue("POST", "/api/same", {"n": 1})
        self.s.enqueue("POST", "/api/same", {"n": 2})
        rec = Recorder(lambda i: (_ for _ in ()).throw(RuntimeError("nope")))
        result = self.s.flush(rec, now=1000.0)
        self.assertEqual(len(rec.seen), 1, "the second write must wait its turn")
        self.assertEqual(result.skipped, 1)
        self.assertEqual(self.s.pending_count(), 2)


class TestOffline(StoreCase):
    def test_being_offline_costs_the_queue_no_retries(self):
        # The bug this rule exists to prevent: a week with no signal must not
        # burn every item's attempts and park the whole outbox.
        for i in range(3):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})

        rec = Recorder(lambda i: (_ for _ in ()).throw(S.Unreachable("no route")))
        for _ in range(20):
            result = self.s.flush(rec)
            self.assertTrue(result.aborted)

        self.assertEqual(self.s.pending_count(), 3)
        self.assertEqual([i.attempts for i in self.s.pending()], [0, 0, 0])
        self.assertEqual(self.s.stats()[S.PARKED], 0)

    def test_the_drain_stops_at_the_first_unreachable(self):
        for i in range(3):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})
        rec = Recorder(lambda i: (_ for _ in ()).throw(S.Unreachable("down")))
        self.s.flush(rec)
        self.assertEqual(len(rec.seen), 1, "no point trying the rest")
        self.assertFalse(self.s.online)

    def test_everything_goes_out_when_the_signal_returns(self):
        for i in range(3):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})
        self.s.flush(Recorder(lambda i: (_ for _ in ()).throw(S.Unreachable("down"))))

        rec = Recorder()
        result = self.s.flush(rec)
        self.assertEqual(result.sent, 3)
        self.assertEqual(rec.paths, ["/api/p0", "/api/p1", "/api/p2"])
        self.assertEqual(self.s.pending_count(), 0)


class TestPoison(StoreCase):
    max_attempts = 3

    def _fail_until_parked(self, path="/api/poison"):
        """Flush until `path` is parked. Every other path succeeds normally."""
        def send(item):
            if item.path == path:
                raise RuntimeError("always 400")

        boom = Recorder(send)
        t = 1000.0
        for _ in range(self.max_attempts):
            self.s.flush(boom, now=t)
            t += S.BACKOFF_CAP + 1
        return boom

    def test_an_item_that_never_succeeds_is_parked_not_deleted(self):
        self.s.enqueue("POST", "/api/poison", {"important": "data"})
        self._fail_until_parked()

        self.assertEqual(self.s.pending_count(), 0, "parked items leave the queue")
        [item] = self.s.pending(state=S.PARKED)
        self.assertEqual(item.payload, {"important": "data"},
                         "a parked payload must still be there")
        self.assertEqual(item.attempts, self.max_attempts)
        self.assertIn("always 400", item.last_error)
        self.assertEqual(self.s.stats()[S.PARKED], 1)

    def test_a_parked_item_is_never_retried_again(self):
        self.s.enqueue("POST", "/api/poison", {})
        self._fail_until_parked()
        rec = Recorder()
        self.assertEqual(self.s.flush(rec, now=9e9).sent, 0)
        self.assertEqual(rec.seen, [])

    def test_a_parked_item_stops_blocking_everything_behind_it(self):
        self.s.enqueue("POST", "/api/poison", {})
        self.s.enqueue("POST", "/api/fine", {})
        self._fail_until_parked()

        # The good write went out on the very first pass (different path), and
        # the queue is now clear rather than jammed behind the poison.
        self.assertEqual(self.s.pending_count(), 0)
        self.assertEqual(self.s.stats()[S.PARKED], 1)

        self.s.enqueue("POST", "/api/later", {})
        self.assertEqual(self.s.flush(Recorder(), now=9e9).sent, 1)

    def test_retry_parked_puts_it_back_with_a_clean_slate(self):
        self.s.enqueue("POST", "/api/poison", {"important": "data"})
        self._fail_until_parked()
        self.assertEqual(self.s.retry_parked(), 1)
        [item] = self.s.pending()
        self.assertEqual(item.attempts, 0)
        self.assertEqual(item.last_error, "")
        self.assertEqual(item.payload, {"important": "data"})
        self.assertEqual(self.s.flush(Recorder()).sent, 1)

    def test_reviving_a_parked_item_cannot_collide_with_a_newer_pending_one(self):
        # A parked item keeps its coalesce_key's slot free, so a later edit to
        # the same collection queues normally. Reviving must not then create
        # two pending writes under one key.
        self.s.enqueue("POST", "/api/foodzen/save", {"v": 1}, coalesce_key="lists")
        self._fail_until_parked(path="/api/foodzen/save")
        self.s.enqueue("POST", "/api/foodzen/save", {"v": 2}, coalesce_key="lists")

        self.assertEqual(self.s.retry_parked(), 1)
        pend = self.s.pending()
        self.assertEqual(len(pend), 2)
        # Older payload first, newer second: the newest state ends up on the
        # server, and nothing was thrown away to get there.
        self.assertEqual([i.payload["v"] for i in pend], [1, 2])


class TestConflict(StoreCase):
    PATH = "/api/foodzen/state"

    def test_a_conflict_parks_the_write_instead_of_clobbering(self):
        self.s.put(self.PATH, {"items": ["from server"]})
        self.s.enqueue("POST", "/api/foodzen/save", {"items": ["from phone"]},
                       local={self.PATH: {"items": ["from phone"]}})

        def send(item):
            raise S.Conflict("the website changed this list while we were offline")

        result = self.s.flush(send)
        self.assertEqual(result.parked, 1)
        self.assertEqual(result.sent, 0)
        [item] = self.s.pending(state=S.CONFLICT)
        self.assertEqual(item.payload, {"items": ["from phone"]},
                         "a conflicted write is kept for a human, never dropped")
        self.assertEqual(self.s.stats()[S.CONFLICT], 1)

    def test_a_conflict_is_not_counted_as_a_retryable_failure(self):
        self.s.enqueue("POST", "/api/foodzen/save", {})
        self.s.flush(lambda item: (_ for _ in ()).throw(S.Conflict("moved")))
        self.assertEqual(self.s.pending(state=S.CONFLICT)[0].attempts, 0)

    def test_the_default_is_last_write_wins_when_send_fn_does_not_check(self):
        # Documented and deliberate: a send_fn that ignores base_rev overwrites
        # the server's copy, destroying whatever the website put there.
        self.s.put(self.PATH, {"items": ["from server"]})
        self.s.enqueue("POST", "/api/foodzen/save", {"items": ["from phone"]},
                       local={self.PATH: {"items": ["from phone"]}})
        rec = Recorder()
        self.assertEqual(self.s.flush(rec).sent, 1)
        self.assertEqual(rec.seen[0].payload, {"items": ["from phone"]})


# ----------------------------------------------------------------- threading
class TestConcurrency(StoreCase):
    def test_many_threads_can_queue_writes_at_once(self):
        threads, errors = [], []

        def work(n):
            try:
                for i in range(20):
                    self.s.enqueue("POST", f"/api/t{n}", {"n": n, "i": i})
                    self.s.put(f"/api/cache/{n}", {"i": i})
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                errors.append(e)

        for n in range(8):
            t = threading.Thread(target=work, args=(n,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.s.pending_count(), 160)

    def test_two_flushes_cannot_send_the_same_item_twice(self):
        for i in range(30):
            self.s.enqueue("POST", f"/api/p{i}", {"i": i})

        seen, lock, errors = [], threading.Lock(), []

        def send(item):
            with lock:
                seen.append(item.id)

        def work():
            try:
                self.s.flush(send)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(seen), len(set(seen)), "an item was sent twice")
        self.assertEqual(self.s.pending_count(), 0)

    def test_reads_work_while_another_thread_writes(self):
        self.s.put("/api/x", {"v": 0})
        stop = threading.Event()
        errors = []

        def writer():
            try:
                i = 0
                while not stop.is_set():
                    i += 1
                    self.s.put("/api/x", {"v": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=writer)
        t.start()
        try:
            for _ in range(200):
                self.assertIsNotNone(self.s.get("/api/x"))
        finally:
            stop.set()
            t.join()
        self.assertEqual(errors, [])


class TestSerialisation(StoreCase):
    def test_payloads_round_trip_through_json_unchanged(self):
        payload = {"s": "café ☕", "n": 0.5, "b": False, "z": None,
                   "nested": {"list": [1, [2, {"three": 3}]]}}
        self.s.enqueue("POST", "/api/x", payload)
        self.assertEqual(self.s.pending()[0].payload, payload)

    def test_revision_is_stable_across_key_order(self):
        self.assertEqual(S.revision({"a": 1, "b": 2}), S.revision({"b": 2, "a": 1}))
        self.assertNotEqual(S.revision({"a": 1}), S.revision({"a": 2}))

    def test_canonical_json_is_parseable(self):
        self.assertEqual(json.loads(S.canonical({"a": [1, 2]})), {"a": [1, 2]})


if __name__ == "__main__":
    unittest.main()
