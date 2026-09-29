"""The parts of Navi that can be wrong without anyone noticing.

Everything here runs with no device, no Flet page and no network. That is the
point: the derived telemetry channels, the off-route hysteresis and the prompt
banding all look fine on screen while being wrong by a factor, and the only
way to catch that is to state the arithmetic.

The three that would otherwise have shipped broken, pinned by name:

* `heading_delta` across north. 359 to 1 degrees is a two-degree right turn.
  Unwrapped it is -358, which at 1 Hz is -358 deg/s of yaw, which through
  `lean_from` is an 89-degree lean every time the bike points north.
* the off-route deadband. A bare threshold flickers on one noisy fix, and each
  flicker is an announcement and a re-route for a corner taken correctly.
* one prompt per band per maneuver. Crossing all three bands in a single fix
  has to say the nearest thing once, not three things at once.
"""
import json
import math
import os
import random
import tempfile
import time
import unittest
import unittest.mock

from agedashii_washi import routing, tileserver

from agedashii_navi import (audio, bikes, daylight, dsp, guidance, places,
                            prefs, telemetry, theme, tiles)


def _route(points, maneuvers=(), duration_s=600.0):
    """A real routing.Route over a straight line, with no graph involved.

    `routing.progress`, `next_maneuver` and `remaining` only read points,
    cum_m, maneuvers, distance_m and duration_s - so a Route built by hand is
    the honest way to test the follower without an 86 MB graph file.
    """
    cum = routing.cumulative_m(points)
    return routing.Route(ok=True, source="offline", points=points, cum_m=cum,
                         maneuvers=list(maneuvers), distance_m=cum[-1],
                         duration_s=duration_s)


class AnOfflineEtaSaysItIsAnEstimate(unittest.TestCase):
    """The offline router prices a road by ONE speed per class, so its arrival
    time is an estimate (median +2.4% against Valhalla, not exact). It is
    marked "~" - at minute resolution, because a time rounded to 5 minutes sits
    frozen for 5 minutes at 60 mph and reads as a broken app. A server
    (Valhalla) arrival time is unmarked."""

    POINTS = [(37.80 + i * 0.0005, -122.27) for i in range(41)]

    def _eta(self, source):
        route = _route(self.POINTS)
        route.source = source
        return guidance.Follower(route, offroute_m=45).update(
            self.POINTS[5], speed_ms=15.0, now=0)["eta"]

    def test_an_offline_arrival_time_is_marked(self):
        self.assertRegex(self._eta("offline"), r"^~\d\d:\d\d$")

    def test_a_server_arrival_time_is_not(self):
        self.assertRegex(self._eta("server"), r"^\d\d:\d\d$")

    def test_no_arrival_time_is_still_a_dash(self):
        self.assertEqual(guidance.eta_text(None, estimate=True), "—")


def _man(instruction, street, cum_m, index):
    return routing.Maneuver(instruction=instruction, street=street,
                            distance_m=0.0, at=(0.0, 0.0), index=index,
                            cum_m=cum_m)


class HeadingMaths(unittest.TestCase):
    def test_delta_wraps_across_north(self):
        self.assertAlmostEqual(telemetry.heading_delta(359, 1), 2.0)
        self.assertAlmostEqual(telemetry.heading_delta(1, 359), -2.0)
        self.assertAlmostEqual(telemetry.heading_delta(10, 20), 10.0)
        self.assertIsNone(telemetry.heading_delta(None, 20))

    def test_a_reversal_is_reported_as_positive_one_eighty(self):
        # Not -180. The sign of a full reversal is arbitrary, and returning the
        # negative makes `abs()` the only safe way to read the channel.
        self.assertEqual(telemetry.heading_delta(0, 180), 180.0)

    def test_lean_is_the_angle_the_load_implies(self):
        # 20 m/s through a 10 deg/s turn: a_lat = 20 * 0.1745 = 3.49 m/s^2,
        # atan(3.49 / 9.807) = 19.6 degrees.
        lean = telemetry.lean_from(20.0, 10.0)
        self.assertAlmostEqual(lean, math.degrees(
            math.atan(20.0 * math.radians(10.0) / telemetry.G)), places=6)
        self.assertAlmostEqual(lean, 19.58, places=1)

    def test_lean_is_none_below_walking_pace(self):
        self.assertIsNone(telemetry.lean_from(2.0, 40.0))
        self.assertIsNone(telemetry.lean_from(None, 10.0))
        self.assertIsNone(telemetry.lean_from(20.0, None))


class RideRecording(unittest.TestCase):
    def setUp(self):
        self.ride = telemetry.Ride(name="test")
        self.ride.start(when=1000.0)

    def test_the_first_fix_has_no_derived_channels(self):
        row = self.ride.add_fix(lat=37.80, lon=-122.27, t=1000.0, speed_ms=0.0)
        self.assertIsNotNone(row)
        for key in ("g_long", "yaw_dps", "g_lat", "lean_deg", "grade_pct"):
            self.assertIsNone(row[key], key)

    def test_derived_channels_come_out_of_two_fixes(self):
        self.ride.add_fix(lat=37.80, lon=-122.27, t=1000.0,
                          speed_ms=10.0, heading=0.0, alt_m=100.0)
        row = self.ride.add_fix(lat=37.801, lon=-122.27, t=1001.0,
                                speed_ms=12.0, heading=10.0, alt_m=105.0)
        # 2 m/s in 1 s is 2 / 9.807 g.
        self.assertAlmostEqual(row["g_long"], 2.0 / telemetry.G, places=3)
        self.assertAlmostEqual(row["yaw_dps"], 10.0, places=2)
        self.assertGreater(row["lean_deg"], 0.0)
        self.assertGreater(row["dist_m"], 100.0)

    def test_a_wild_fix_is_dropped_rather_than_logged(self):
        self.ride.add_fix(lat=37.80, lon=-122.27, t=1000.0, acc_m=5.0)
        self.assertIsNone(self.ride.add_fix(lat=37.90, lon=-122.27, t=1001.0,
                                            acc_m=120.0))
        self.assertEqual(self.ride.count, 1)

    def test_a_repeated_timestamp_is_dropped_rather_than_dividing_by_zero(self):
        self.ride.add_fix(lat=37.80, lon=-122.27, t=1000.0, speed_ms=5.0)
        self.assertIsNone(self.ride.add_fix(lat=37.801, lon=-122.27, t=1000.0,
                                            speed_ms=5.0))

    def test_jitter_while_stopped_adds_no_distance(self):
        self.ride.add_fix(lat=37.800000, lon=-122.270000, t=1000.0)
        # ~1.1 m north - under the 2 m floor.
        self.ride.add_fix(lat=37.800010, lon=-122.270000, t=1001.0)
        self.assertEqual(self.ride.distance_m, 0.0)

    def test_altitude_wander_does_not_accumulate_climb(self):
        for i, alt in enumerate((100.0, 100.5, 100.2, 100.6, 100.1)):
            self.ride.add_fix(lat=37.8 + i * 0.001, lon=-122.27,
                              t=1000.0 + i, alt_m=alt)
        self.assertEqual(self.ride.climb_m, 0.0)
        self.assertEqual(self.ride.descent_m, 0.0)

    def test_a_real_climb_does_accumulate(self):
        for i, alt in enumerate((100.0, 110.0, 125.0)):
            self.ride.add_fix(lat=37.8 + i * 0.001, lon=-122.27,
                              t=1000.0 + i, alt_m=alt)
        self.assertAlmostEqual(self.ride.climb_m, 25.0, places=1)

    def test_the_window_is_bounded_but_the_count_is_not(self):
        for i in range(telemetry.WINDOW + 50):
            self.ride.add_fix(lat=37.8 + i * 0.0002, lon=-122.27,
                              t=1000.0 + i, speed_ms=20.0, heading=90.0)
        self.assertEqual(len(self.ride.samples), telemetry.WINDOW)
        self.assertEqual(self.ride.count, telemetry.WINDOW + 50)

    def test_motion_attaches_to_the_latest_sample_not_a_new_one(self):
        self.ride.add_fix(lat=37.80, lon=-122.27, t=1000.0)
        self.ride.feed_motion(g_vert=1.8, tilt_deg=12.0)
        self.assertEqual(self.ride.count, 1)
        self.assertAlmostEqual(self.ride.samples[-1]["g_vert"], 1.8)


class Exports(unittest.TestCase):
    def setUp(self):
        self.ride = telemetry.Ride(name="export test")
        self.ride.start(when=1700000000.0)
        for i in range(4):
            self.ride.add_fix(lat=37.80 + i * 0.001, lon=-122.27,
                              t=1700000000.0 + i, speed_ms=10.0 + i,
                              heading=i * 5.0, alt_m=100.0 + i)

    def test_csv_has_every_channel_and_blanks_for_the_absent_ones(self):
        csv = self.ride.to_csv()
        self.assertEqual(csv.splitlines()[0].split(","), telemetry.ORDER)
        # rpm_est has no backend, so its column exists and is empty.
        col = telemetry.ORDER.index("rpm_est")
        self.assertEqual(csv.splitlines()[1].split(",")[col], "")

    def test_gpx_is_timestamped_in_utc_and_carries_the_derived_channels(self):
        gpx = self.ride.to_gpx()
        self.assertIn("<gpx", gpx)
        self.assertIn(time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(1700000000.0)), gpx)
        self.assertIn("<navi:lean_deg>", gpx.replace("\n", ""))

    def test_json_carries_the_channel_dictionary_with_the_data(self):
        blob = json.loads(self.ride.to_json())
        self.assertEqual(blob["channels"]["g_lat"]["kind"], "derived")
        # rpm_est is "estimated", not "measured" and no longer "absent": the
        # microphone is real (audio.py) but the number is a peak-pick in a noise
        # band and is allowed to come back empty.
        self.assertEqual(blob["channels"]["rpm_est"]["kind"], "estimated")
        self.assertEqual(blob["channels"]["g_vert"]["kind"], "measured")
        self.assertEqual(len(blob["samples"]), 4)

    def test_every_channel_declares_one_of_the_known_kinds(self):
        kinds = {v[0] for v in telemetry.CHANNELS.values()}
        self.assertTrue(kinds <= {"measured", "derived", "estimated"}, kinds)

    def test_sensor_channels_merge_into_the_latest_sample(self):
        self.ride.feed_extra({"g_vert": 1.8, "g_bump": 0.9, "tilt_deg": -12.0,
                              "climb_mps": 1.4, "rpm_est": 4200})
        row = self.ride.samples[-1]
        self.assertAlmostEqual(row["g_vert"], 1.8)
        self.assertEqual(self.ride.count, 4, "feed_extra made a new sample")
        summary = self.ride.summary()
        self.assertAlmostEqual(summary["max_g_bump"], 0.9)
        self.assertAlmostEqual(summary["max_climb_mps"], 1.4)
        self.assertEqual(summary["max_rpm_est"], 4200)

    def test_a_ride_with_no_sensors_reports_none_not_zero(self):
        # A zero maximum is indistinguishable from a phone with no
        # accelerometer, so the summary omits the figure instead.
        self.assertIsNone(self.ride.summary()["max_g_bump"])

    def test_a_name_with_an_ampersand_does_not_break_the_gpx(self):
        self.ride.name = "Grizzly & Wildcat"
        self.assertIn("Grizzly &amp; Wildcat", self.ride.to_gpx())


class LogOnDisk(unittest.TestCase):
    def test_a_ride_survives_being_torn_off_mid_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ride.jsonl")
            ride = telemetry.Ride(path=path, name="torn")
            ride.start(when=1000.0)
            for i in range(20):
                ride.add_fix(lat=37.8 + i * 0.001, lon=-122.27, t=1000.0 + i,
                             speed_ms=15.0, heading=90.0)
            ride.note_incident("hard brake", t=1010.0)
            ride.stop(when=1020.0)

            # Simulate a battery death mid-write by lopping off half a line.
            with open(path, "r+", encoding="utf-8") as fh:
                data = fh.read()
                fh.seek(0)
                fh.write(data[:-12])
                fh.truncate()

            header, samples, incidents, _footer = telemetry.read_log(path)
            self.assertEqual(header["name"], "torn")
            self.assertGreaterEqual(len(samples), 18)
            self.assertEqual(incidents[0]["what"], "hard brake")

    def test_an_unwritable_path_does_not_stop_the_ride(self):
        ride = telemetry.Ride(path="/proc/definitely/not/writable/r.jsonl")
        ride.start(when=1000.0)
        row = ride.add_fix(lat=37.8, lon=-122.27, t=1000.0)
        self.assertIsNotNone(row)
        self.assertTrue(ride.errors)


class RpmFromTheExhaustNote(unittest.TestCase):
    def test_a_synthetic_tone_comes_back_as_the_rpm_that_made_it(self):
        # A 2-cylinder 4-stroke fires once per revolution, so 3000 rpm is
        # 50 Hz. Build 50 Hz and expect 3000 back.
        rate, hz, n = 4000, 50.0, 1024
        samples = [math.sin(2 * math.pi * hz * i / rate) for i in range(n)]
        rpm = telemetry.rpm_from_samples(samples, rate, cylinders=2, strokes=4)
        self.assertIsNotNone(rpm)
        self.assertAlmostEqual(rpm, 3000.0, delta=150.0)

    def test_a_dc_offset_does_not_become_the_answer(self):
        rate, hz, n = 4000, 50.0, 1024
        samples = [5.0 + math.sin(2 * math.pi * hz * i / rate)
                   for i in range(n)]
        self.assertAlmostEqual(telemetry.rpm_from_samples(samples, rate),
                               3000.0, delta=150.0)

    def test_nothing_useful_returns_none_rather_than_zero(self):
        self.assertIsNone(telemetry.rpm_from_samples([], 4000))
        self.assertIsNone(telemetry.rpm_from_samples([0.0] * 10, 4000))
        self.assertIsNone(telemetry.rpm_from_samples([0.1] * 512, 0))


def _engine_note(rpm, machine, rate=8000, n=4096,
                 harmonics=((1, 0.5), (2, 0.35), (3, 0.2)), noise=0.0, seed=7):
    """A synthetic exhaust note: a fundamental and two harmonics, plus wind."""
    hz = bikes.hz_from_rpm(rpm, machine)
    random.seed(seed)
    return [sum(amp * math.sin(2 * math.pi * hz * h * i / rate)
                for h, amp in harmonics)
            + (random.uniform(-noise, noise) if noise else 0.0)
            for i in range(n)]


class TheEngineMaths(unittest.TestCase):
    """The ratio that turns an exhaust note into engine speed.

    Her bikes: "my 93 honda nighthawk cb750 4 cylinder" and "my wife has a 2006
    kawasaki ex250F". Those are a four and a twin, which fire at different rates,
    so the same note means two different engine speeds.
    """

    def test_a_four_fires_twice_per_revolution_and_a_twin_once(self):
        self.assertEqual(bikes.firing_ratio("cb750"), 2.0)
        self.assertEqual(bikes.firing_ratio("ex250"), 1.0)

    def test_the_same_note_is_two_different_engine_speeds(self):
        # 100 Hz is 3000 rpm on the four and 6000 on the twin.
        self.assertAlmostEqual(bikes.rpm_from_hz(100.0, "cb750"), 3000.0)
        self.assertAlmostEqual(bikes.rpm_from_hz(100.0, "ex250"), 6000.0)

    def test_hz_and_rpm_are_inverses(self):
        for machine in bikes.PROFILES:
            hz = bikes.hz_from_rpm(5000, machine)
            self.assertAlmostEqual(bikes.rpm_from_hz(hz, machine), 5000.0,
                                   places=6)

    def test_the_band_reaches_the_redline_of_every_bike(self):
        # The bug this exists to prevent: a fixed 20-200 Hz band was written for
        # a twin and would have capped the CB750 at about 6000 rpm.
        for machine in bikes.PROFILES:
            lo, hi = bikes.band_hz(machine)
            self.assertGreater(hi, bikes.redline_hz(machine), machine)
            idle_hz = bikes.hz_from_rpm(
                bikes.profile(machine)["idle_rpm"], machine)
            # Never above idle, except where idle is under the 15 Hz floor - a
            # single at 1200 rpm has a 10 Hz fundamental, which is below what a
            # phone microphone reproduces and squarely inside road rumble. That
            # is a real limit of the method, not a mis-set constant.
            self.assertLessEqual(lo, max(15.0, idle_hz), machine)

    def test_a_single_cannot_be_heard_at_idle_and_that_is_admitted(self):
        idle_hz = bikes.hz_from_rpm(bikes.profile("other")["idle_rpm"], "other")
        self.assertLess(idle_hz, bikes.band_hz("other")[0])

    def test_the_cb750_band_reaches_past_three_hundred_hertz(self):
        self.assertGreater(bikes.band_hz("cb750")[1], 316.0)

    def test_an_impossible_engine_speed_is_refused(self):
        self.assertFalse(bikes.plausible(300, "cb750"))
        self.assertFalse(bikes.plausible(20000, "cb750"))
        self.assertFalse(bikes.plausible(None, "cb750"))
        self.assertTrue(bikes.plausible(4000, "cb750"))
        # The twin revs a long way further than the four.
        self.assertTrue(bikes.plausible(13000, "ex250"))
        self.assertFalse(bikes.plausible(13000, "cb750"))

    def test_an_unknown_bike_falls_back_rather_than_raising(self):
        self.assertEqual(bikes.profile("harley"), bikes.profile(bikes.DEFAULT))


class FindingTheEngine(unittest.TestCase):
    """The detector, against synthetic notes. This is the whole RPM claim."""

    def test_it_reads_the_rev_range_of_both_bikes(self):
        for machine, speeds in (("cb750", (1500, 3000, 5000, 6500, 9000)),
                                ("ex250", (1500, 4000, 8000, 11000, 13000))):
            for rpm in speeds:
                got = audio.analyse(_engine_note(rpm, machine), 8000, machine)
                with self.subTest(machine=machine, rpm=rpm):
                    self.assertIsNotNone(got["rpm"], got["rejected"])
                    self.assertAlmostEqual(got["rpm"], rpm, delta=rpm * 0.06)

    def test_a_loud_second_harmonic_does_not_double_the_answer(self):
        # The octave error: with the 2nd harmonic louder than the fundamental, a
        # peak-pick returns 8000 for an engine at 4000. Measured before the
        # harmonic product went in: naive said 8027.
        trap = _engine_note(4000, "cb750",
                            harmonics=((1, 0.2), (2, 0.9), (3, 0.3)))
        got = audio.analyse(trap, 8000, "cb750")
        self.assertAlmostEqual(got["rpm"], 4000, delta=250)

    def test_the_top_of_the_range_is_not_read_as_a_third_of_itself(self):
        # The sub-harmonic trap: at 9000 rpm on the four every harmonic is above
        # the band, so a candidate at a third of the fundamental used to win on
        # substituted medians alone and report 2988.
        got = audio.analyse(_engine_note(9000, "cb750"), 8000, "cb750")
        self.assertAlmostEqual(got["rpm"], 9000, delta=400)

    def test_noise_mostly_refused_rather_than_answered(self):
        """Most broadband noise is refused. NOT all of it - and that is the deal.

        This used to seed the generator with 1, get None, and claim the gate
        works. It passes on seed 1 and FAILS on seed 0, which returns exactly
        the 3691 rpm the gate was written to prevent. A test that passes by
        luck is worse than no test, because it reads as covered.

        Measured over 200 seeds: contrast on pure noise runs p50 3.70, p90
        6.96, p95 8.02, p99 12.40. MIN_CONTRAST is 8.0, so the gate sits at
        about the 95th percentile of noise and roughly one window in twenty
        gets through. Raising it would cost real readings at idle, and her
        call was to leave it: "yeah thats fine i might turn off that in
        settings right now its fine its fine".

        So this asserts the real behaviour with a ceiling. It cannot pass by
        luck, and it fails loudly if a change to the DSP makes noise cheaper
        to believe.
        """
        answered = 0
        for seed in range(200):
            random.seed(seed)
            noise = [random.uniform(-0.6, 0.6) for _ in range(4096)]
            if audio.analyse(noise, 8000, "cb750")["rpm"] is not None:
                answered += 1
        self.assertLessEqual(answered, 15,
                             f"{answered}/200 noise windows produced an rpm; "
                             "the gate got weaker")
        # And it must not go the other way either: a gate that refuses
        # everything would pass the line above while silently killing the
        # readout on a real engine. TheEngineMaths covers that from the other
        # side, so this only pins the floor of the trade.
        self.assertLess(answered, 200)
        # A refusal must also SAY why, so Settings can show a reason rather
        # than an unexplained dash. Seed 1 is one of the refused ones.
        random.seed(1)
        noise = [random.uniform(-0.6, 0.6) for _ in range(4096)]
        got = audio.analyse(noise, 8000, "cb750")
        self.assertIsNone(got["rpm"])
        self.assertIn("no clear fundamental", got["rejected"])

    def test_an_engine_survives_heavy_wind(self):
        got = audio.analyse(_engine_note(5000, "cb750", noise=0.9), 8000,
                            "cb750")
        self.assertIsNotNone(got["rpm"], got["rejected"])
        self.assertAlmostEqual(got["rpm"], 5000, delta=400)

    def test_an_implausible_jump_is_thrown_away(self):
        got = audio.analyse(_engine_note(8000, "cb750"), 8000, "cb750",
                            previous_rpm=1200)
        self.assertIsNone(got["rpm"])
        self.assertIn("jump", got["rejected"])

    def test_the_band_meter_shows_where_the_energy_is(self):
        got = audio.analyse(_engine_note(2000, "cb750"), 8000, "cb750")
        bands = dict(got["bands"])
        self.assertEqual(len(bands), 3)
        # 2000 rpm on the four is 67 Hz, near the bottom of a 25-396 Hz band.
        self.assertGreaterEqual(bands["low"], bands["high"])
        self.assertAlmostEqual(max(bands.values()), 1.0, places=6)

    def test_it_is_fast_enough_to_run_once_a_second(self):
        note = _engine_note(4000, "cb750")
        started = time.monotonic()
        audio.analyse(note, 8000, "cb750")
        self.assertLess(time.monotonic() - started, 0.5)


class TheSignalProcessing(unittest.TestCase):
    def test_decimation_reduces_the_rate_and_the_work(self):
        samples = [math.sin(2 * math.pi * 50 * i / 8000) for i in range(4096)]
        narrow, rate = dsp.decimate(samples, 8000)
        self.assertEqual(rate, 1000.0)
        self.assertEqual(len(narrow), 512)

    def test_decimation_leaves_an_already_slow_signal_alone(self):
        narrow, rate = dsp.decimate([0.1] * 100, 500)
        self.assertEqual(rate, 500)
        self.assertEqual(len(narrow), 100)

    def test_the_high_pass_removes_a_slow_drift_and_keeps_the_note(self):
        drift = [0.5 * math.sin(2 * math.pi * 2 * i / 8000) for i in range(4096)]
        note = [0.5 * math.sin(2 * math.pi * 120 * i / 8000) for i in range(4096)]
        self.assertLess(dsp.rms(dsp.highpass(drift, 8000)),
                        dsp.rms(drift) * 0.25)
        self.assertGreater(dsp.rms(dsp.highpass(note, 8000)),
                           dsp.rms(note) * 0.7)

    def test_contrast_separates_a_tone_from_noise(self):
        tone = [math.sin(2 * math.pi * 100 * i / 1000) for i in range(512)]
        random.seed(3)
        noise = [random.uniform(-1, 1) for _ in range(512)]
        tone_bins = dsp.spectrum(tone, 1000, 20, 400)
        noise_bins = dsp.spectrum(noise, 1000, 20, 400)
        self.assertGreater(dsp.contrast(tone_bins, 100.0), 50.0)
        self.assertLess(dsp.contrast(noise_bins,
                                     dsp.harmonic_pick(noise_bins) or 100.0),
                        audio.MIN_CONTRAST)

    def test_a_hann_window_is_zero_at_the_edges(self):
        window = dsp.hann(64)
        self.assertAlmostEqual(window[0], 0.0)
        self.assertAlmostEqual(window[-1], 0.0)
        # Not exactly 1.0 for an even length: the peak falls between the two
        # middle samples, so the largest sample is a hair under it.
        self.assertAlmostEqual(max(window), 1.0, places=2)

    def test_nothing_useful_returns_none_rather_than_a_number(self):
        self.assertEqual(dsp.spectrum([], 8000, 20, 200), [])
        self.assertEqual(dsp.spectrum([0.1] * 8, 8000, 20, 200), [])
        self.assertIsNone(dsp.harmonic_pick([]))
        self.assertIsNone(dsp.harmonic_pick([(50.0, 0.0), (60.0, 0.0)]))
        self.assertEqual(dsp.contrast([], 100.0), 0.0)
        self.assertEqual(dsp.to_db(0.0), -90.0)


class TheSun(unittest.TestCase):
    """Auto day/night, computed offline from where she is and what time it is."""

    #: Oakland, and a midsummer day, in UTC.
    LAT, LON = 37.8044, -122.2712
    NOON = 1750000000.0          # 2025-06-15 around local morning

    def test_the_sun_is_up_in_the_middle_of_a_summer_day(self):
        # 20:00 UTC is 13:00 local in California.
        when = 1750017600.0
        self.assertGreater(daylight.solar_altitude(self.LAT, self.LON, when),
                           40.0)

    def test_the_sun_is_down_in_the_middle_of_the_night(self):
        # 09:00 UTC is 02:00 local.
        when = 1749978000.0
        self.assertLess(daylight.solar_altitude(self.LAT, self.LON, when), -10.0)

    def test_every_phase_has_a_theme_and_a_brightness(self):
        for name, _floor in daylight.PHASES:
            self.assertIn(daylight.theme_for(name), theme.THEMES)
            self.assertTrue(0.0 < daylight.brightness_for(name) <= 1.0)

    def test_brightness_falls_as_the_sun_does(self):
        order = [daylight.brightness_for(name)
                 for name, _floor in daylight.PHASES]
        self.assertEqual(order, sorted(order, reverse=True))

    def test_night_asks_for_the_red_theme(self):
        self.assertEqual(daylight.theme_for("night"), "redshift")
        self.assertEqual(daylight.theme_for("day"), "sunlight")

    def test_no_position_falls_back_to_the_clock_instead_of_failing(self):
        self.assertIsNone(daylight.solar_altitude(None, None))
        self.assertIn(daylight.phase(None, None), dict(daylight.PHASES))

    def test_the_next_sunrise_and_sunset_are_both_within_a_day(self):
        rise, fall = daylight.sun_times(self.LAT, self.LON, self.NOON)
        self.assertIsNotNone(rise)
        self.assertIsNotNone(fall)
        for moment in (rise, fall):
            self.assertLess(moment - self.NOON, 86400.0 + 600.0)
            self.assertGreater(moment, self.NOON)

    def test_the_sun_is_actually_at_the_horizon_at_those_moments(self):
        # The real check: whatever it calls sunrise must be a moment when the
        # altitude is about zero. This is what makes the walk trustworthy without
        # a second, independent implementation to compare against.
        for moment in daylight.sun_times(self.LAT, self.LON, self.NOON):
            self.assertAlmostEqual(
                daylight.solar_altitude(self.LAT, self.LON, moment), 0.0,
                delta=1.5)

    def test_the_red_theme_is_red_everywhere(self):
        # A single green chip would undo the whole point of a red screen.
        palette = theme.THEMES["redshift"]
        for key in ("text", "text2", "accent", "good", "warn", "bad"):
            red, green, blue = (int(palette[key][i:i + 2], 16)
                                for i in (1, 3, 5))
            self.assertGreaterEqual(red, green, key)
            self.assertGreaterEqual(red, blue, key)


class OffRouteHysteresis(unittest.TestCase):
    def setUp(self):
        # A straight line due north, ~1.1 km.
        self.points = [(37.80 + i * 0.001, -122.27) for i in range(11)]
        self.route = _route(self.points)
        self.follower = guidance.Follower(self.route, offroute_m=45)

    def test_one_bad_fix_does_not_say_off_route(self):
        self.follower.update((37.805, -122.2718))       # ~17 m off
        self.follower.update((37.805, -122.2695))       # ~40 m off
        self.assertFalse(self.follower.off_route)

    def test_three_consecutive_bad_fixes_do(self):
        for _ in range(guidance.CONFIRM_FIXES):
            self.follower.update((37.805, -122.2660))   # ~350 m off
        self.assertTrue(self.follower.off_route)
        self.assertTrue(self.follower.should_reroute())

    def test_coming_back_needs_to_clear_the_deadband(self):
        for _ in range(guidance.CONFIRM_FIXES):
            self.follower.update((37.805, -122.2660))
        # Just inside the threshold is NOT enough to clear it.
        self.follower.update((37.805, -122.26955))      # ~40 m off
        self.assertTrue(self.follower.off_route)
        # On the line is.
        self.follower.update((37.805, -122.27))
        self.assertFalse(self.follower.off_route)

    def test_arrival_stops_asking_for_a_re_route(self):
        self.follower.update(self.points[-1])
        self.assertTrue(self.follower.arrived)
        self.assertFalse(self.follower.should_reroute())


class Prompts(unittest.TestCase):
    def setUp(self):
        self.points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        self.route = _route(self.points)
        cum = self.route.cum_m
        self.route.maneuvers = [
            _man("head north", "First St", 0.0, 0),
            _man("turn left", "Grand Ave", cum[20], 20),
            _man("arrive at your destination", "", cum[-1], 40)]
        self.follower = guidance.Follower(self.route, offroute_m=45)

    def test_prompt_distances_scale_with_speed(self):
        slow = guidance.prompt_points(10.0)
        fast = guidance.prompt_points(30.0)
        self.assertGreater(fast["far"], slow["far"])
        self.assertAlmostEqual(fast["near"], 30.0 * 20.0)

    def test_below_a_crawl_the_distances_are_fixed(self):
        self.assertEqual(guidance.prompt_points(0.0), guidance.CRAWL_BANDS_M)

    def test_each_band_speaks_once(self):
        said = []
        for point in self.points[:20]:
            status = self.follower.update(point, speed_ms=15.0)
            if status.get("say"):
                said.append(status["say"])
        self.assertTrue(said, "nothing was ever announced")
        self.assertEqual(len(said), len(set(said)),
                         "the same prompt was said twice")
        self.assertLessEqual(len(said), len(guidance.PROMPT_BANDS))

    def test_crossing_every_band_in_one_fix_says_the_nearest_thing_once(self):
        # Jump straight to one point short of the turn at high speed: all three
        # bands are behind us in a single update.
        status = self.follower.update(self.points[19], speed_ms=30.0)
        self.assertIsNotNone(status["say"])
        self.assertNotIn("In ", status["say"])
        self.assertIsNone(
            self.follower.update(self.points[19], speed_ms=30.0)["say"])

    def test_a_re_route_forgets_what_it_already_said(self):
        for point in self.points[:20]:
            self.follower.update(point, speed_ms=15.0)
        self.follower.set_route(self.route)
        again = [self.follower.update(p, speed_ms=15.0)["say"]
                 for p in self.points[:20]]
        self.assertTrue(any(again), "a re-route went silent")

    def test_the_fired_parts_name_the_maneuver(self):
        # One flourish per maneuver across its far/near/now prompts needs to
        # know which maneuver a prompt is for.
        indexes = set()
        for point in self.points[:20]:
            if self.follower.update(point, speed_ms=15.0)["say"]:
                indexes.add(self.follower.fired["index"])
        self.assertEqual(indexes, {20})

    def test_riding_off_does_not_repeat_what_was_just_said(self):
        # minion 2's find: picked at a light, crawl "far" fires; riding off
        # makes the speed-scaled "near" due at almost the same distance, and
        # the same "In 600 ft" played twice, 6 s apart.
        self.assertIsNotNone(
            self.follower.update(self.points[16], speed_ms=0.0)["say"])
        self.assertIsNone(
            self.follower.update(self.points[17], speed_ms=15.0)["say"])

    def test_the_turn_itself_always_speaks(self):
        self.follower.update(self.points[16], speed_ms=0.0)
        self.follower.update(self.points[17], speed_ms=15.0)
        now = self.follower.update(self.points[19], speed_ms=15.0)["say"]
        self.assertIsNotNone(now)
        self.assertNotIn("In ", now)

    def test_a_prompt_well_nearer_than_the_last_still_speaks(self):
        self.follower.update(self.points[16], speed_ms=0.0)
        near = self.follower.update(self.points[18], speed_ms=15.0)["say"]
        self.assertIsNotNone(near, "a real closer prompt was swallowed")

    def test_the_head_instruction_is_never_a_prompt(self):
        # routing.next_maneuver starts at maneuvers[1]; "head north" describes
        # what she is already doing.
        status = self.follower.update(self.points[0], speed_ms=15.0)
        self.assertNotIn("head", (status["say"] or "").lower())


MPH = 1 / 2.2369362920544
FT = 1 / 3.280839895
#: Arnold's four distances, as his clips actually say them (Whisper, minion 5).
ARNOLD_FT = (528.0, 1320.0, 2640.0, 5280.0)


class PromptsFireWhereHerVoiceHasADistance(unittest.TestCase):
    """His four distances (0.1 mi, a quarter, a half, a mile) are the points
    Waze ANNOUNCES AT, not a scale to round to. Fired at a fixed 60 s and 20 s,
    the near prompt on every 25-35 mph street fell between his clips and the
    phone said the number. Fired when she REACHES one of his distances - the
    one nearest the band's time, within ANCHOR_WINDOW - every distance is his,
    and exact. With no distance clips nothing changes."""

    def points(self, mph):
        return guidance.prompt_points(mph * MPH, anchors_ft=ARNOLD_FT)

    def test_thirty_mph_is_a_half_then_a_quarter(self):
        p = self.points(30)
        self.assertAlmostEqual(p["far"], 2640 * FT, places=3)
        self.assertAlmostEqual(p["near"], 1320 * FT, places=3)

    def test_twenty_five_mph_is_a_half_then_a_tenth(self):
        p = self.points(25)
        self.assertAlmostEqual(p["far"], 2640 * FT, places=3)
        self.assertAlmostEqual(p["near"], 528 * FT, places=3)

    def test_forty_five_mph_is_a_mile_then_a_quarter(self):
        p = self.points(45)
        self.assertAlmostEqual(p["far"], 5280 * FT, places=3)
        self.assertAlmostEqual(p["near"], 1320 * FT, places=3)

    def test_now_is_never_moved(self):
        for mph in (15, 30, 45, 65):
            self.assertAlmostEqual(self.points(mph)["now"], mph * MPH * 6.0)

    def test_far_is_always_before_near_and_near_before_now(self):
        for mph in range(5, 90):
            p = self.points(mph)
            self.assertGreater(p["far"], p["near"], mph)
            self.assertGreater(p["near"], p["now"], mph)

    def test_nothing_is_moved_further_than_the_window(self):
        for mph in range(8, 90):
            ms = mph * MPH
            timed = guidance.prompt_points(ms)
            for band in ("far", "near"):
                ratio = self.points(mph)[band] / timed[band]
                self.assertLessEqual(ratio, guidance.ANCHOR_WINDOW + 1e-9, (mph, band))
                self.assertGreaterEqual(ratio, 1 / guidance.ANCHOR_WINDOW - 1e-9, (mph, band))

    def test_no_distance_clips_changes_nothing(self):
        for mph in (10, 30, 60):
            self.assertEqual(guidance.prompt_points(mph * MPH, anchors_ft=()),
                             guidance.prompt_points(mph * MPH))

    def test_a_crawl_is_unchanged(self):
        self.assertEqual(guidance.prompt_points(1.0, anchors_ft=ARNOLD_FT),
                         guidance.CRAWL_BANDS_M)

    def test_the_follower_fires_the_near_prompt_at_his_quarter_mile(self):
        points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        route = _route(points)
        route.maneuvers = [_man("head north", "A St", 0.0, 0),
                           _man("turn left", "Grand Ave", route.cum_m[30], 30),
                           _man("arrive at your destination", "", route.cum_m[-1], 40)]
        follower = guidance.Follower(route, offroute_m=45, anchors_ft=ARNOLD_FT)
        fired = []
        for p in points[:30]:
            if follower.update(p, speed_ms=30 * MPH)["say"]:
                fired.append((follower.fired["band"], follower.fired["metres"]))
        near = [m for band, m in fired if band == "near"]
        self.assertEqual(len(near), 1, fired)
        # at or just inside a quarter mile, the first fix past it
        self.assertLessEqual(near[0], 1320 * FT)
        self.assertGreater(near[0], 1320 * FT - 60)


class TheFollowerKnowsHerDistances(unittest.TestCase):
    """live builds the Follower; it has to hand it the distances her voice can
    say, or prompts stay on the clock. Read on every route, so a clip imported
    mid-ride counts from the next re-route."""

    def setUp(self):
        from agedashii_navi import live, voice_import
        self.live, self.voice_import = live, voice_import
        live.reset()
        prefs.reset()
        self.addCleanup(live.reset)
        points = [(37.80 + i * 0.0005, -122.27) for i in range(5)]
        self.route = _route(points)
        self.route.maneuvers = [_man("head north", "A St", 0.0, 0),
                                _man("arrive at your destination", "", self.route.cum_m[-1], 4)]
        live.LIVE["pos"] = points[0]
        live.LIVE["dest"] = (points[-1][0], points[-1][1], "there")

    def route_with(self, clips, on=True):
        with unittest.mock.patch.object(routing, "route", return_value=self.route), \
                unittest.mock.patch.object(self.voice_import, "distance_clips", return_value=clips), \
                unittest.mock.patch.object(self.live, "ANCHOR_PROMPTS", on):
            self.live.route_now()
        return self.live.LIVE["follower"].anchors_ft

    def test_it_ships_switched_off_until_she_has_heard_it(self):
        # It changes WHEN every prompt fires; she decides, with the table.
        self.assertFalse(self.live.ANCHOR_PROMPTS)
        self.assertEqual(self.route_with({"dist-half": "/v/h.mp3"}, on=False), ())

    def test_a_route_hands_the_follower_her_distances(self):
        got = self.route_with({"dist-half": "/v/h.mp3", "dist-quarter": "/v/q.mp3"})
        self.assertEqual(got, (1320.0, 2640.0))

    def test_a_re_route_picks_up_a_new_clip(self):
        self.route_with({"dist-half": "/v/h.mp3"})
        got = self.route_with({"dist-half": "/v/h.mp3", "dist-1mile": "/v/m.mp3"})
        self.assertEqual(got, (2640.0, 5280.0))

    def test_no_distance_clips_means_clock_timed_prompts(self):
        self.assertEqual(self.route_with({}), ())


class ANameChangeIsShownNotSaid(unittest.TestCase):
    """The router marks a straight-on name change `spoken=False`. The banner
    still shows it; the voice skips it and warns about the next thing she has
    to DO - so a turn just past a name change keeps its far warning."""

    def setUp(self):
        self.points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        self.route = _route(self.points)
        cum = self.route.cum_m
        self.route.maneuvers = [
            _man("head north", "A St", 0.0, 0),
            routing.Maneuver("continue straight onto B St", "B St", 0.0, (0.0, 0.0),
                             10, cum[10], "continue", False),
            _man("turn left", "Grand Ave", cum[20], 20),
            _man("arrive at your destination", "", cum[-1], 40)]
        self.follower = guidance.Follower(self.route, offroute_m=45)

    def test_the_name_change_is_never_said(self):
        said = [self.follower.update(p, speed_ms=15.0)["say"] for p in self.points[:20]]
        self.assertFalse([s for s in said if s and "continue" in s], said)

    def test_the_turn_after_it_is_warned_about_before_the_name_change(self):
        said = []
        for p in self.points[:10]:                  # still short of the name change
            s = self.follower.update(p, speed_ms=15.0)["say"]
            if s:
                said.append(s)
        self.assertTrue(any("turn left" in s for s in said), said)

    def test_the_banner_still_shows_it(self):
        status = self.follower.update(self.points[2], speed_ms=15.0)
        self.assertEqual(status["instruction"], "continue straight onto B St")


class ArrivingAtWalkingPace(unittest.TestCase):
    """Arrival is 25 m from the end; so is the "now" band below a crawl. The
    fix that arrived used to swallow the only prompt that says she is there."""

    def test_the_arriving_fix_says_so(self):
        points = [(37.80 + i * 0.0001, -122.27) for i in range(41)]   # ~11 m apart
        route = _route(points)
        route.maneuvers = [_man("head north", "A St", 0.0, 0),
                           _man("arrive at your destination", "", route.cum_m[-1], 40)]
        follower = guidance.Follower(route, offroute_m=45)
        for p in points[:37]:                          # ~44 m out, and closer
            follower.update(p, speed_ms=1.0)
        status = follower.update(points[39], speed_ms=1.0)   # ~11 m: arrived
        self.assertTrue(status["arrived"])
        self.assertEqual(status["say"], "arrive at your destination")


class _Snap:
    def __init__(self, seg, k):
        self.seg, self.fwd, self.bwd, self.u, self.v = seg, k, -1, 0, 1


class _LimitGraph:
    """The three things limit_mph reads: a snap, the bucket, the real class."""

    def __init__(self, bucket, highway):
        self.cls = [bucket]
        self._hw = highway

    def snap_edge(self, lat, lon, heading=None):
        return _Snap(0, 0)

    def highway(self, sid):
        return None if self._hw is None else routing.CLASSES.index(self._hw)


class SpeedLimitsFromTheMap(unittest.TestCase):
    """A graph "class" is a SPEED BUCKET - the builder files each road under
    the default speed nearest its posted maxspeed - so the bucket IS the posted
    limit, quantized. Reading a typical limit off the bucket's NAME called a
    35 mph street a "trunk link" limited to 40."""

    def limit(self, bucket, highway):
        return guidance.limit_mph(_LimitGraph(bucket, highway), 37.8, -122.27)

    def test_a_posted_35_mph_street_is_35_and_is_not_a_trunk_link(self):
        # bucket 3 is 55 km/h; a tertiary's own default is bucket 5, so 55 km/h
        # is a posted limit: 34.2 mph, said as 35.
        self.assertEqual(self.limit(3, "tertiary"), (35, "tertiary"))

    def test_a_posted_30_is_30(self):
        self.assertEqual(self.limit(5, "secondary"), (30, "secondary"))

    def test_a_55_mph_freeway_is_55(self):
        self.assertEqual(self.limit(2, "motorway"), (55, "motorway"))

    def test_an_unposted_road_gets_the_typical_limit_for_what_it_really_is(self):
        # The builder's residential default is 30 km/h (19 mph); California's
        # is 25 - so a road sitting on its class's default bucket gets typical.
        self.assertEqual(self.limit(11, "residential"), (25, "residential"))
        self.assertEqual(self.limit(0, "motorway"), (65, "motorway"))

    def test_an_older_graph_with_no_real_class_uses_the_bucket(self):
        self.assertEqual(self.limit(3, None)[0], 35)

    def test_the_buckets_are_the_builders_own(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[3] / "tools" / "navi-routing" / "build_graph.py"
        spec = importlib.util.spec_from_file_location("build_graph", path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertEqual(tuple(builder.SPEED_KMH), guidance.BUCKET_KMH)


class TheLimitFollowsTheBike(unittest.TestCase):
    """The limit was worked out only while the Ride page painted, so on any
    other page it was whatever road she had last been on - and the speeding
    cue compared her speed against it. Now every taken fix keeps it."""

    def setUp(self):
        from agedashii_navi import live
        self.live = live
        live.reset()
        self.addCleanup(live.reset)
        self.calls = []

        def fake_limit(graph, lat, lon, heading=None):
            self.calls.append((lat, lon))
            return (35, "tertiary") if lat < 37.81 else (65, "motorway")

        for target, value in ((guidance, fake_limit),):
            patcher = unittest.mock.patch.object(target, "limit_mph", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        ready = unittest.mock.patch.object(routing, "ready", return_value=True)
        graph = unittest.mock.patch.object(routing, "graph", return_value=object())
        ready.start(), graph.start()
        self.addCleanup(ready.stop)
        self.addCleanup(graph.stop)

    def test_a_fix_sets_the_limit_with_no_page_involved(self):
        self.live.set_position(37.800, -122.27, speed_ms=10.0, t=100.0)
        self.assertEqual(self.live.LIVE["limit"], (35, "tertiary"))

    def test_it_is_not_searched_again_until_the_bike_has_moved(self):
        self.live.set_position(37.800, -122.27, t=100.0)
        self.live.set_position(37.8001, -122.27, t=200.0)       # 11 m
        self.assertEqual(len(self.calls), 1)

    def test_a_new_road_gets_its_own_limit(self):
        self.live.set_position(37.800, -122.27, t=100.0)
        self.live.set_position(37.820, -122.27, t=200.0)        # 2.2 km on
        self.assertEqual(self.live.LIVE["limit"], (65, "motorway"))

    def test_with_the_graph_not_loaded_nothing_is_claimed_and_nothing_loads(self):
        with unittest.mock.patch.object(routing, "ready", return_value=False):
            self.live.set_position(37.800, -122.27, t=100.0)
        self.assertEqual(self.live.LIVE["limit"], (None, ""))
        self.assertEqual(self.calls, [])


class Wording(unittest.TestCase):
    def test_distances_are_rounded_to_something_sayable(self):
        self.assertEqual(guidance.distance_text(122.0), "400 ft")
        self.assertEqual(guidance.distance_text(1609.344), "1.0 mi")
        self.assertEqual(guidance.distance_text(30.0, imperial=False), "30 m")
        self.assertEqual(guidance.distance_text(1500.0, imperial=False),
                         "1.5 km")
        self.assertEqual(guidance.distance_text(None), "—")

    def test_speed_has_no_decimal_point(self):
        self.assertEqual(guidance.speed_text(26.8224), "60")
        self.assertEqual(guidance.speed_text(26.8224, imperial=False), "97")
        self.assertEqual(guidance.speed_text(None), "—")

    def test_now_drops_the_distance(self):
        near = guidance.spoken("turn left", "Grand Ave", 100.0, band="near")
        now = guidance.spoken("turn left", "Grand Ave", 20.0, band="now")
        self.assertTrue(near.startswith("In "))
        self.assertEqual(now, "turn left onto Grand Ave")

    def test_a_street_already_in_the_instruction_is_not_repeated(self):
        said = guidance.spoken("turn left onto Grand Ave", "Grand Ave", 20.0,
                               band="now")
        self.assertEqual(said.count("Grand Ave"), 1)

    def test_turn_icons_read_the_words(self):
        self.assertEqual(guidance.turn_icon("Turn sharp left"), "sharp_left")
        self.assertEqual(guidance.turn_icon("Bear right"), "slight_right")
        self.assertEqual(guidance.turn_icon("Make a U-turn"), "u_turn")
        self.assertEqual(guidance.turn_icon("Arrive at your destination"),
                         "flag")
        self.assertEqual(guidance.turn_icon("Continue"), "straight")


class SpeedLimits(unittest.TestCase):
    def test_every_road_class_has_a_typical_limit(self):
        for name in routing.CLASSES:
            self.assertIn(name, guidance.TYPICAL_MPH, name)

    def test_the_typical_limits_are_not_the_routing_speeds(self):
        # The graph's SPEED_KMH is fitted door-to-door speed, a different
        # quantity. Showing it as a speed limit would under-report a
        # residential street by 10 mph.
        residential = routing.SPEED_KMH[
            routing.CLASSES.index("residential")] / 1.609344
        self.assertGreater(guidance.TYPICAL_MPH["residential"], residential)

    def test_no_graph_means_no_limit_rather_than_a_guess(self):
        self.assertEqual(guidance.limit_mph(None, 37.8, -122.27), (None, ""))

    def test_over_the_limit_needs_a_real_margin(self):
        self.assertFalse(guidance.over_limit(None, 25))
        self.assertFalse(guidance.over_limit(12.0, None))
        self.assertFalse(guidance.over_limit(12.0, 25))     # 26.8 mph
        self.assertTrue(guidance.over_limit(20.0, 25))       # 44.7 mph


class ParsingAPlace(unittest.TestCase):
    def test_a_bare_pair(self):
        self.assertEqual(places.parse_point("37.8044, -122.2712")[:2],
                         (37.8044, -122.2712))

    def test_a_share_sheet_geo_uri(self):
        got = places.parse_point("geo:37.8044,-122.2712?z=15")
        self.assertEqual(got[:2], (37.8044, -122.2712))
        self.assertEqual(got[2], "shared pin")

    def test_a_maps_url_prefers_the_pair_after_the_at(self):
        url = ("https://www.google.com/maps/place/Somewhere/"
               "@37.8044,-122.2712,15z/data=!3m1!4b1")
        self.assertEqual(places.parse_point(url)[:2], (37.8044, -122.2712))

    def test_a_maps_url_with_a_query_parameter(self):
        url = "https://maps.apple.com/?q=37.8044,-122.2712&t=m"
        self.assertEqual(places.parse_point(url)[:2], (37.8044, -122.2712))

    def test_degrees_minutes_seconds(self):
        got = places.parse_point("""37°48'15.8"N 122°16'16.3"W""")
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], 37.8044, places=3)
        self.assertAlmostEqual(got[1], -122.2712, places=3)

    def test_a_point_off_the_planet_is_refused(self):
        self.assertIsNone(places.parse_point("91.0, 0.0"))
        self.assertIsNone(places.parse_point("0.0, 181.0"))

    def test_nonsense_is_none_rather_than_an_exception(self):
        for text in ("", None, "somewhere nice", "---"):
            self.assertIsNone(places.parse_point(text))


class ImportingGpx(unittest.TestCase):
    TRACK = """<?xml version="1.0"?>
    <gpx version="1.1"><trk><name>Grizzly Peak</name><trkseg>
      <trkpt lat="37.8800" lon="-122.2400"><ele>380</ele></trkpt>
      <trkpt lat="37.8810" lon="-122.2390"></trkpt>
    </trkseg></trk></gpx>"""

    def test_a_track_comes_back_with_its_name(self):
        name, points = places.parse_gpx(self.TRACK)
        self.assertEqual(name, "Grizzly Peak")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0], (37.88, -122.24))

    def test_a_track_wins_over_stray_waypoints(self):
        both = self.TRACK.replace(
            "</gpx>", '<wpt lat="1.0" lon="2.0"></wpt></gpx>')
        _name, points = places.parse_gpx(both)
        self.assertEqual(len(points), 2)
        self.assertNotIn((1.0, 2.0), points)

    def test_a_planned_route_in_rtept_is_still_read(self):
        route = ('<gpx><rte><name>Plan</name>'
                 '<rtept lat="37.1" lon="-122.1"></rtept>'
                 '<rtept lat="37.2" lon="-122.2"></rtept></rte></gpx>')
        name, points = places.parse_gpx(route)
        self.assertEqual(name, "Plan")
        self.assertEqual(len(points), 2)

    def test_a_file_that_is_not_gpx_gives_no_points_rather_than_raising(self):
        self.assertEqual(places.parse_gpx("hello")[1], [])
        self.assertEqual(places.parse_gpx("")[1], [])


class Preferences(unittest.TestCase):
    def setUp(self):
        prefs.reset()

    def test_the_screen_stays_on_by_default(self):
        self.assertTrue(prefs.keep_awake())
        self.assertTrue(prefs.voice())

    def test_recording_is_off_by_default(self):
        # Recording a ride she did not mean to record is a privacy decision.
        self.assertFalse(prefs.autorecord())

    def test_a_bad_value_is_refused_and_the_old_one_kept(self):
        prefs.set_value("theme", "cluster")
        prefs.set_value("theme", "chartreuse")
        self.assertEqual(prefs.theme_key(), "cluster")

    def test_a_free_integer_is_clamped_not_refused(self):
        prefs.set_value("offroute_m", 5)
        self.assertEqual(prefs.offroute_m(), 30)
        prefs.set_value("offroute_m", 9000)
        self.assertEqual(prefs.offroute_m(), 200)

    def test_an_off_list_sample_rate_is_refused(self):
        prefs.set_value("hz", 5)
        prefs.set_value("hz", 7)
        self.assertEqual(prefs.hz(), 5)

    def test_setting_the_theme_moves_the_palette_too(self):
        prefs.set_value("theme", "sunlight")
        self.assertEqual(theme.S["theme"], "sunlight")

    def test_storage_round_trips_every_key(self):
        prefs.set_value("theme", "day")
        prefs.set_value("bike", "black")
        prefs.set_value("units", "metric")
        prefs.set_value("voice", False)
        prefs.set_value("hz", 10)
        prefs.set_last_dest(37.8044, -122.2712, "Home")
        payload = prefs.save_payload()
        prefs.reset()
        for key, raw in payload.items():
            prefs.apply_stored(key[len(prefs.KEY):], raw)
        self.assertEqual(prefs.theme_key(), "day")
        self.assertEqual(prefs.bike(), "black")
        self.assertFalse(prefs.imperial())
        self.assertFalse(prefs.voice())
        self.assertEqual(prefs.hz(), 10)
        self.assertEqual(prefs.last_dest()[2], "Home")

    def test_a_flag_stored_as_zero_comes_back_false(self):
        prefs.apply_stored("keep_awake", "0")
        self.assertFalse(prefs.keep_awake())

    def test_a_corrupt_last_destination_is_none_not_a_crash(self):
        prefs.set_value("last_dest", "not,a,point")
        self.assertIsNone(prefs.last_dest())


class Themes(unittest.TestCase):
    def test_every_theme_has_every_key(self):
        keys = set(theme.THEMES["night"])
        for name, palette in theme.THEMES.items():
            self.assertEqual(set(palette), keys, name)

    def test_every_theme_is_named_and_has_a_tile_style(self):
        for name in theme.THEMES:
            self.assertIn(name, theme.THEME_NAMES)
            self.assertIn(theme.THEME_TILES[name], tiles.STYLES)

    def test_an_unknown_theme_falls_back_rather_than_raising(self):
        self.assertEqual(theme.PAL("chartreuse"), theme.THEMES["night"])

    def test_the_warning_colour_is_never_the_accent(self):
        # Off-route has to be findable by colour alone.
        for name, palette in theme.THEMES.items():
            self.assertNotEqual(palette["bad"], palette["accent"], name)
            self.assertNotEqual(palette["warn"], palette["accent"], name)

    def test_her_two_bike_colours_and_nothing_else(self):
        self.assertEqual(set(theme.BIKE_COLOURS), {"yellow", "black"})
        self.assertEqual(set(theme.BIKE_OUTLINE), {"yellow", "black"})


class TileChoice(unittest.TestCase):
    def test_no_archive_means_the_online_style_she_asked_for(self):
        got = tiles.pick("topo", archive=False)
        self.assertFalse(got["offline"])
        self.assertEqual(got["style"], "topo")
        self.assertIn("opentopomap", got["url"])

    def test_an_unknown_style_falls_back_to_the_default(self):
        # Read as the default; what is DRAWN online for it is whatever can be
        # (online Dark has no source - tiles.WHY_NOT_ONLINE).
        got = tiles.pick("fuchsia", archive=False)
        self.assertEqual(got["asked"], tiles.DEFAULT)
        self.assertTrue(got["url"])

    def test_cycling_walks_every_style_and_comes_back(self):
        seen, style = [], tiles.DEFAULT
        for _ in range(len(tiles.STYLES)):
            style = tiles.cycle(style)
            seen.append(style)
        self.assertEqual(set(seen), set(tiles.STYLES))
        self.assertEqual(style, tiles.DEFAULT)

    def test_every_style_names_its_attribution(self):
        for key in tiles.STYLES:
            self.assertTrue(tiles.attribution(key))


class GpxImportDoesNotCareAboutAttributeOrder(unittest.TestCase):
    """Found by the Kotlin port, not by anyone using the app.

    The pattern demanded `lat` before `lon` inside the tag. XML attribute
    order carries no meaning and plenty of tools write `lon` first, so those
    files returned an EMPTY LIST - not an error, not a warning, nothing. An
    import of a perfectly good route just appeared to do nothing at all.
    """

    def _one(self, tag):
        return places.parse_gpx(
            f"<gpx><trk><trkseg>{tag}</trkseg></trk></gpx>")[1]

    def test_lat_first(self):
        self.assertEqual(self._one('<trkpt lat="37.8044" lon="-122.2712"/>'),
                         [(37.8044, -122.2712)])

    def test_lon_first(self):
        self.assertEqual(self._one('<trkpt lon="-122.2712" lat="37.8044"/>'),
                         [(37.8044, -122.2712)])

    def test_other_attributes_in_between(self):
        self.assertEqual(
            self._one('<trkpt ele="12" lon="-122.2712" lat="37.8044" t="x"/>'),
            [(37.8044, -122.2712)])

    def test_a_tag_missing_one_of_them_is_skipped_not_guessed(self):
        self.assertEqual(self._one('<trkpt lat="37.8044"/>'), [])


class OffRouteIsMeasuredToTheLine(unittest.TestCase):
    """The bug that stopped turn-by-turn a few turns into every ride.

    `routing.progress` scanned the route's VERTICES and returned the distance
    to the nearest one. Route points are raw OSM shape points, never
    densified, and on a straight road they sit 183-449 m apart - measured on
    her real Oakland-to-Chabot routes. So a rider exactly on the line, midway
    between two of them, read as up to 224 m off route against a 45 m
    threshold.

    Off-route then latched after three fixes, every prompt was suppressed
    while it held, a reroute fired and cleared it, and it repeated: 17-19
    false reroutes per ride in simulation. Her words: "it worked for several
    turns then just stopped ... it didn't look like it was frozen".
    """

    class _Route:
        # A straight north-south road with shape points 400 m apart, which is
        # entirely ordinary for an OSM way.
        points = [(37.8000, -122.2000), (37.8036, -122.2000),
                  (37.8072, -122.2000)]
        cum_m = [0.0, 400.0, 800.0]

    def test_on_the_line_between_two_points_is_not_off_route(self):
        _i, _along, off = routing.progress(self._Route, (37.8018, -122.2000))
        self.assertLess(off, 5.0,
                        "a rider dead on the line reads as off route")

    def test_along_interpolates_instead_of_jumping(self):
        # This also drives "in X ft" and when the 60/20/6 s prompts fire, so a
        # vertex-sized jump made both of those wrong too.
        _i, along, _off = routing.progress(self._Route, (37.8018, -122.2000))
        self.assertAlmostEqual(along, 200.0, delta=10.0)

    def test_a_real_deviation_is_still_caught(self):
        # 60 m to the side, which SHOULD read as off route. The fix must not
        # work by simply reporting zero.
        east = -122.2000 + 60.0 / (111_320.0 * math.cos(math.radians(37.8)))
        _i, _along, off = routing.progress(self._Route, (37.8018, east))
        self.assertAlmostEqual(off, 60.0, delta=5.0)

    def test_past_the_end_measures_to_the_end(self):
        # Not to an imaginary extension of the road.
        _i, _along, off = routing.progress(self._Route, (37.8100, -122.2000))
        self.assertGreater(off, 250.0)


class TheTileServerUnderARealMapWidget(unittest.TestCase):
    """The bug that froze the app on both her phones.

    flutter_map takes its tile and then drops the socket with an RST instead
    of closing it. Stock `http.server` lets that out of `handle()`, so
    `socketserver` prints a fifteen-line traceback - and on Android every
    line is a synchronous write to logcat. Forty tiles a pan, plus the
    widget retrying what it thought had failed, and the app spends its
    entire budget formatting stack traces about a socket closing normally.

    So: a client that hangs up mid-conversation must cost nothing and say
    nothing. Asserting on stderr rather than on an internal flag because the
    cost WAS the printing.
    """

    def setUp(self):
        import sqlite3
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.mbtiles")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE tiles (zoom_level int, tile_column int, "
                  "tile_row int, tile_data blob)")
        c.execute("CREATE TABLE metadata (name text, value text)")
        # Deliberately claims z19 in metadata while holding only z12-z14, the
        # way a real archive from a third-party builder does.
        c.execute("INSERT INTO metadata VALUES ('maxzoom', '19')")
        for z in (12, 13, 14):
            c.execute("INSERT INTO tiles VALUES (?,?,?,?)", (z, 1, 1, png))
        c.commit()
        c.close()
        self.addCleanup(tileserver.stop)

    def test_a_client_that_yanks_the_cord_says_nothing_and_costs_nothing(self):
        import contextlib
        import io
        import socket
        import struct

        self.assertTrue(tileserver.start(self.path))
        port = tileserver.status()["port"]

        def rst_after_a_tile():
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(b"GET /12/1/2/.png HTTP/1.1\r\nHost: x\r\n\r\n")
            s.recv(65536)
            # SO_LINGER 0 turns close() into an RST, which is exactly what a
            # Dart HttpClient does when the map widget disposes a tile.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                         struct.pack("ii", 1, 0))
            s.close()

        noise = io.StringIO()
        with contextlib.redirect_stderr(noise):
            for _ in range(25):
                rst_after_a_tile()
            time.sleep(0.5)
        self.assertEqual(noise.getvalue(), "", "the tile server printed a "
                         "traceback for a client hanging up - this is the "
                         "freeze")

    def test_it_reports_the_zooms_the_archive_really_has(self):
        self.assertTrue(tileserver.start(self.path))
        # Not (0, 19) from the metadata table: that is a lie this archive
        # tells, and believing it is what made the map blank above z15.
        self.assertEqual(tileserver.status()["zooms"], (12, 14))

    def test_a_tile_above_the_range_is_still_answered_not_errored(self):
        # The layer is told the range and should stop asking, but an empty
        # 200 rather than a 404 is what keeps a widget from drawing error
        # tiles if it asks anyway.
        import urllib.request
        self.assertTrue(tileserver.start(self.path))
        port = tileserver.status()["port"]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/17/1/1.png", timeout=5) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":         # pragma: no cover
    unittest.main()
