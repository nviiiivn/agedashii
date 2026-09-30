"""The gauges read true values whether or not a ride records - and say why
when they cannot.

Hers, v1.17.0: "the sensors don't seem to work on the flet app". They did
(emulator-5556: all five subscribed, every chip green). The gauges read lean
and g only from the RECORDING's newest row, so outside a recording they sat
blank, and the phone's own roll was drawn only beside a known lean. The
Kotlin app keeps an always-on telemetry ride that writes nothing; so does
this one now, while the gauges are on screen.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import flet as ft  # noqa: E402

from agedashii_navi import gauges, live, prefs, sensors  # noqa: E402
from agedashii_navi.pages import ride  # noqa: E402
from tests.test_road_test import texts  # noqa: E402


class _Clean(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        ride._M.pop("live_ride", None)                          # noqa: SLF001
        self.folder = tempfile.mkdtemp(prefix="rides-")
        patcher = mock.patch.object(live, "ride_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        feed = mock.patch.object(sensors, "latest",
                                 return_value={"yaw_gyro_dps": 12.0, "tilt_deg": 3.0})
        feed.start()
        self.addCleanup(feed.stop)
        self.addCleanup(ride._M.pop, "live_ride", None)         # noqa: SLF001
        self.addCleanup(live.reset)

    def fixes(self, n=8):
        for i in range(n):
            live.set_position(37.80 + i * 0.0001, -122.27 + i * i * 0.00005,
                              speed_ms=12.0 if i else 0.0, heading=10.0 + i * 6,
                              acc_m=5.0, t=1000.0 + i)
            ride._feed_fix()                                     # noqa: SLF001


class TheGaugesAreLiveWithoutARecording(_Clean):
    def test_lean_and_g_read_true_values_with_nothing_recording(self):
        self.assertFalse(live.recording())
        self.fixes()
        state = ride._hud_state()                                # noqa: SLF001
        self.assertIsNotNone(state["lean_deg"])
        self.assertIsNotNone(state["g_lat"])
        self.assertEqual(os.listdir(self.folder), [])            # writes nothing

    def test_a_hidden_gauge_costs_nothing(self):
        prefs.set_value("dash", False)
        self.fixes()
        self.assertIsNone(ride._dash_ride())                     # noqa: SLF001
        self.assertNotIn("live_ride", ride._M)                   # noqa: SLF001

    def test_a_recording_is_what_the_gauges_read_when_there_is_one(self):
        recording = live.start_recording()
        self.assertIs(ride._dash_ride(), recording)              # noqa: SLF001
        live.stop_recording()


class TheMapOpensWhereSheIs(_Clean):
    """minion 1's Flet rides: parked at the destination, the map went back to
    downtown Oakland. A render re-mounts the reused map at its initial_center,
    and that was the first build's - Oakland, before the first fix."""

    def test_a_rebuild_opens_the_reused_map_at_her_last_fix(self):
        from tests.test_road_test import Page
        page = Page()
        ride.build(page)
        first = ride._MAP["map"]                                  # noqa: SLF001
        live.LIVE["pos"] = (37.9101, -122.3102)
        ride.build(page)
        self.assertIs(ride._MAP["map"], first, "the map is meant to be reused")
        centre = first.initial_center
        self.assertEqual((centre.latitude, centre.longitude), (37.9101, -122.3102))


class TheSpeedDoesNotOutliveTheFixes(unittest.TestCase):
    """Parked at the destination the gauge said 34 mph for forty seconds: the
    stream reports only movement, so no fix came to say she had stopped."""

    def setUp(self):
        live.reset()
        self.addCleanup(live.reset)

    def test_fresh_it_is_the_fix(self):
        live.LIVE.update(speed_ms=15.2, t=100.0)
        self.assertEqual(live.speed_now(now=103.0), 15.2)

    def test_stopped_coming_after_a_slow_fix_is_stopped(self):
        live.LIVE.update(speed_ms=1.1, t=100.0)
        self.assertEqual(live.speed_now(now=100.0 + live.SPEED_STALE_S + 1), 0.0)

    def test_stopped_coming_at_speed_is_unknown_not_a_number(self):
        live.LIVE.update(speed_ms=15.2, t=100.0)
        self.assertIsNone(live.speed_now(now=140.0))

    def test_the_gauges_read_it(self):
        live.LIVE.update(speed_ms=15.2, t=time.time() - 60)
        self.assertIsNone(ride._hud_state()["speed_ms"])        # noqa: SLF001


def archive_with_bounds(folder, bounds="-122.65,37.2,-121.75,38.1"):
    import sqlite3
    path = os.path.join(folder, "edge.mbtiles")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("name", "Offline OSM dark"), ("bounds", bounds)])
    conn.commit()
    conn.close()
    return path


class PastTheMapsEdgeItSaysSo(_Clean):
    """minion 1's Flet rides: past the offline map's edge the screen was black
    with her bike on it and no words. Kotlin says where she is."""

    def test_inside_outside_and_unknown(self):
        from agedashii_navi import tiles
        path = archive_with_bounds(self.folder)
        self.assertFalse(tiles.outside(37.8044, -122.2712, archive=path))   # Oakland
        self.assertTrue(tiles.outside(38.2975, -122.2869, archive=path))    # Napa
        self.assertIsNone(tiles.outside(38.2975, -122.2869,
                                        archive=archive_with_bounds(
                                            tempfile.mkdtemp(), bounds="nonsense")))

    def test_the_ride_page_says_it(self):
        from agedashii_navi import tiles
        from agedashii_washi import api
        from tests.test_road_test import Page, texts
        path = archive_with_bounds(self.folder)

        def band_shown():
            built = ride.build(Page())
            holders = [c for c in walk(built) if isinstance(c, ft.Container)
                       and tiles.OUTSIDE_WORDS in " ".join(texts(ft.Column(controls=[c])))
                       and getattr(c, "content", None) is not None
                       and getattr(c.content, "value", None) == tiles.OUTSIDE_WORDS]
            return bool(holders) and holders[0].visible

        with mock.patch.object(api, "mbtiles_path", return_value=path):
            live.LIVE.update(pos=(38.2975, -122.2869), t=time.time())
            self.assertTrue(band_shown())
            live.LIVE.update(pos=(37.8044, -122.2712))
            self.assertFalse(band_shown())


def walk(node, seen=None):
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls",):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    child = getattr(node, "content", None)
    if child is not None and not isinstance(child, (str, int, float)):
        yield from walk(child, seen)


class AnEmptyGaugeSaysWhy(unittest.TestCase):
    def words(self, control):
        return " | ".join(texts(ft.Column(controls=[control])))

    def test_standing_still_the_ball_is_the_phones_roll(self):
        said = self.words(gauges.horizon(lean_deg=None, roll_deg=12.0))
        self.assertIn("phone 12°", said)
        self.assertNotIn("lean", said)

    def test_riding_it_is_lean_as_before(self):
        # The thin needle beside a known lean still means "the mount is off
        # angle" - the caption must not make that look like the phone state.
        said = self.words(gauges.horizon(lean_deg=10.0, roll_deg=30.0))
        self.assertIn("lean 10°", said)
        self.assertNotIn("phone", said)

    def test_nothing_known_says_when_it_will_be(self):
        self.assertIn("lean · while riding", self.words(gauges.horizon()))
        self.assertIn("g · while riding", self.words(gauges.g_ball()))


class TheLevelSurvivesARestart(unittest.TestCase):
    """minion 1's find: "Set level here" lived in memory only, so every restart
    lost her mount calibration and the horizon was wrong until she noticed."""

    def setUp(self):
        prefs.reset()
        sensors.reset()
        self.addCleanup(prefs.reset)
        self.addCleanup(sensors.reset)

    def restart(self):
        stored = prefs.save_payload()                 # what goes to storage
        sensors.reset()
        prefs.reset()
        for key, raw in stored.items():
            prefs.apply_stored(key[len(prefs.KEY):], raw)

    def test_set_level_is_stored_and_comes_back(self):
        sensors.STATE.update(roll_deg=6.5, pitch_deg=-3.0)
        sensors.zero()
        self.restart()
        self.assertEqual(sensors.zeroed(), {"set": True, "roll": 6.5, "pitch": -3.0})
        # A real accelerometer reading carries g_total with the angles.
        sensors.STATE.update(roll_deg=6.5, pitch_deg=-3.0, g_total=1.0)
        self.assertEqual(sensors.latest().get("tilt_deg"), 0.0)

    def test_clear_is_stored_too(self):
        sensors.STATE.update(roll_deg=6.5, pitch_deg=-3.0)
        sensors.zero()
        sensors.unzero()
        self.restart()
        self.assertFalse(sensors.zeroed()["set"])

    def test_a_bad_stored_value_is_no_level_not_a_crash(self):
        prefs.apply_stored("level", "garbage")
        self.assertFalse(sensors.zeroed()["set"])


if __name__ == "__main__":
    unittest.main()
