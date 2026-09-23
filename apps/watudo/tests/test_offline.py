"""Offline is the product here, not a degraded mode.

Her words: "ONLY THE DB stuff needs to sync everything else should be stored
locally and not need internet access". So there are two halves to pin:

* **Nothing local asks the network.** The theme, the corner radius, the glow,
  the CRT overlay and the card layout are read from SharedPreferences and
  applied by `prefs.apply_runtime()`; changing any of them must not produce
  a single request. If one ever does, the app stops working in a tunnel for
  a reason that has nothing to do with the server.

* **What does sync never drops a write.** A tick made with no signal goes
  into the outbox and is coalesced per day, so an afternoon of ticking boxes
  in a tunnel becomes one queued write carrying the final state rather than
  forty that each have to be retried. That already worked before this app
  existed; these tests are here so it keeps working now that a second shell
  builds the same page.

Everything runs against a tempfile store and an opener that refuses every
request, so "offline" is a boolean rather than a firewall rule.
"""
import flet as ft

from agedashii_washi import prefs

from .harness import WatudoCase, first, texts

DAY = "2026-09-21"


class TheLocalHalfNeverAsksTheNetwork(WatudoCase):
    def test_changing_the_theme_costs_no_request(self):
        from agedashii_qi import PAL, THEMES, apply_theme
        page = self.build()
        before = len(self.opener.calls)
        apply_theme(page, "matrix")
        self.assertEqual(PAL()["bg"], THEMES["matrix"]["bg"])
        self.assertEqual(len(self.opener.calls), before)

    def test_changing_the_appearance_knobs_costs_no_request(self):
        from agedashii_qi import RAD, S
        self.build()
        before = len(self.opener.calls)
        prefs.set_accent_glow(True)
        prefs.set_crt_overlay(True)
        prefs.set_corner_radius(16)
        prefs.apply_runtime()
        self.assertTrue(S.glow and S.crt)
        self.assertEqual(RAD(8), 16)
        self.assertEqual(len(self.opener.calls), before)

    def test_rearranging_the_cards_costs_no_request(self):
        self.build()
        before = len(self.opener.calls)
        prefs.move_panel("wat2do", "vitals", -1)
        prefs.set_panel_hidden("wat2do", "weather", True)
        self.assertNotIn("weather", prefs.panels("wat2do"))
        self.assertEqual(len(self.opener.calls), before)

    def test_every_request_the_app_makes_is_for_server_data(self):
        """The strong form of "only the DB stuff syncs": a full launch plus a
        repaint with every appearance knob changed asks the wire for the
        day's record and for the address it lives at, and for nothing else.
        No theme, no layout, no preference ever leaves the device."""
        page = self.build()
        prefs.set_accent_glow(True)
        prefs.set_corner_radius(0)
        prefs.apply_runtime()
        self.rerender()
        self.assertEqual(len(page.controls), 1)
        for call in self.opener.calls:
            url = str(call)
            self.assertIn("/api/", url, f"{url} is not a server-data request")

    def test_the_preferences_that_shape_the_shell_survive_a_relaunch(self):
        """Written to SharedPreferences, which is on the device. No server
        is involved in any of it, and none is reachable in this test."""
        import asyncio
        self.build()
        prefs.set_bottom_bar_enabled(False)
        prefs.set_corner_radius(0)
        asyncio.run(prefs.save(self.stub_prefs))
        stored = dict(self.stub_prefs.data)
        prefs.reset_all()

        relaunched = self.build(stored)
        self.assertIsNone(first(self.shell(relaunched), ft.NavigationBar))
        self.assertEqual(prefs.corner_radius(), 0)


class AWat2DoTickSurvivesHavingNoSignal(WatudoCase):
    def tick(self, done, pain=2, spoons=3):
        from agedashii_watudo.pages import _wat2do_parts as W
        return W.save_state(DAY, {"Meds": done}, pain, spoons)

    def test_a_tick_with_no_signal_is_queued_and_says_so(self):
        ok, msg, record = self.tick(True)
        self.assertTrue(ok, msg)
        self.assertEqual(msg, self.api.QUEUED_MESSAGE)
        self.assertEqual(self.api.pending_count(), 1)
        self.assertTrue(record["checks"]["Meds"])

    def test_the_very_next_read_sees_it_without_a_round_trip(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.tick(True)
        self.assertTrue(W.state(DAY)["checks"]["Meds"])

    def test_an_afternoon_of_ticking_is_one_queued_write_not_forty(self):
        """Coalesced per day. Forty queued writes would each need their own
        retry, and thirty-nine of them would be carrying stale state."""
        for i in range(40):
            self.tick(bool(i % 2), pain=i % 10)
        self.assertEqual(self.api.pending_count(), 1)
        final = self.api._cached(f"/api/wat2do/state?date={DAY}")
        self.assertEqual(final["pain"], 9)
        self.assertTrue(final["checks"]["Meds"])

    def test_a_different_day_is_its_own_queued_write(self):
        from agedashii_watudo.pages import _wat2do_parts as W
        self.tick(True)
        W.save_state("2026-09-22", {"Meds": True}, 1, 1)
        self.assertEqual(self.api.pending_count(), 2)

    def test_nothing_reached_the_wire(self):
        before = len(self.opener.calls)
        self.tick(True)
        # The store probes at most once to confirm the verdict it already
        # holds; what must never happen is the write itself going out and
        # being thrown away when it fails.
        self.assertLessEqual(len(self.opener.calls) - before, 1)

    def test_the_queue_survives_the_process_and_the_shell_announces_it(self):
        """A queue nobody can see is a queue that is silently a data loss."""
        self.tick(True)
        page = self.build()
        said = " ".join(texts(self.shell(page)))
        self.assertIn("waiting", said)


class FutureFindsIsHonestWithNothingToShow(WatudoCase):
    def test_it_says_there_is_nothing_rather_than_showing_a_plausible_list(self):
        from agedashii_watudo.pages import future
        from agedashii_qi import PAL
        notice = future.not_connected(PAL(), None)
        said = " ".join(texts(notice))
        self.assertIn("Not connected", said)
        self.assertNotIn("sample data", said)
        self.assertNotIn("will not save", said)

    def test_a_someday_list_on_the_device_is_offered_as_this_devices_copy(self):
        from agedashii_watudo.pages import future
        from agedashii_qi import PAL
        said = " ".join(texts(future.not_connected(PAL(), [{"name": "x"}])))
        self.assertIn("queued", said)
