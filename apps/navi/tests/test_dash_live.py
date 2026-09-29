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
