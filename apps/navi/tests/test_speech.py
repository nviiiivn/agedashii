"""Spoken turns: the prompt that fires is said, the newest wins, and silence
that looks like working is impossible.

The engine is faked. The real one - android.speech.tts through pyjnius - was
proved on emulator-5556 (bind 0.11 s, offline voice, speak() -> 0); these pin
the behaviour around it that a phone cannot show in a test.
"""
import array
import math
import sys
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from agedashii_navi import audio, guidance, live, prefs, speech  # noqa: E402


class FakeEngine:
    """The four things speech.py asks of an engine, with a clock."""

    def __init__(self, ok=True, problem="", seconds=0.15):
        self.ok, self.problem, self.seconds = ok, problem, seconds
        self.said, self.focus = [], []
        self.until = 0.0

    def init(self, deadline):
        return self.ok, self.problem, "fake.engine", "fake-voice", self.ok

    def speak(self, text):
        self.said.append(text)
        self.until = time.time() + self.seconds
        return 0

    def is_speaking(self):
        return time.time() < self.until

    def request_focus(self):
        self.focus.append("request")
        return 1

    def abandon_focus(self):
        self.focus.append("abandon")


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class _Clean(unittest.TestCase):
    def setUp(self):
        speech.reset()
        prefs.reset()
        live.reset()
        self.engine = FakeEngine()
        speech._M["factory"] = lambda: self.engine          # noqa: SLF001

    def tearDown(self):
        speech.reset()


class ThePromptIsSaid(_Clean):
    def test_a_prompt_is_spoken_with_the_units_spelled_out(self):
        speech.say("In 400 ft, turn right onto Broadway")
        self.assertTrue(wait_for(lambda: self.engine.said))
        self.assertEqual(self.engine.said[0], "In 400 feet, turn right onto Broadway")

    def test_miles_are_said_as_miles(self):
        self.assertEqual(speech.for_the_ear("In 0.3 mi, bear left"), "In 0.3 miles, bear left")
        self.assertEqual(speech.for_the_ear("In 1.0 mi, turn left"), "In 1 mile, turn left")

    def test_the_newest_prompt_wins(self):
        self.engine.seconds = 1.0
        speech.say("In 900 ft, turn left")
        self.assertTrue(wait_for(lambda: self.engine.said))
        speech.say("turn left onto Grand")
        self.assertTrue(wait_for(lambda: len(self.engine.said) == 2))
        self.assertEqual(self.engine.said[-1], "turn left onto Grand")
        self.assertTrue(wait_for(lambda: not speech.STATE["speaking"], 3.0))

    def test_it_ducks_the_music_and_gives_it_back(self):
        speech.say("turn right")
        self.assertTrue(wait_for(lambda: "abandon" in self.engine.focus))
        self.assertEqual(self.engine.focus, ["request", "abandon"])

    def test_say_never_blocks_the_page(self):
        self.engine.seconds = 2.0
        t = time.time()
        speech.say("turn right")
        self.assertLess(time.time() - t, 0.1)


class TheTachStandsDown(_Clean):
    def test_voice_active_while_speaking_and_for_the_tail(self):
        self.engine.seconds = 0.2
        speech.say("turn right")
        self.assertTrue(wait_for(lambda: speech.STATE["speaking"]))
        self.assertTrue(speech.voice_active())
        self.assertTrue(wait_for(lambda: not speech.STATE["speaking"]))
        self.assertTrue(speech.voice_active(), "the tail is missing")
        self.assertFalse(speech.voice_active(time.time() + speech.TAIL_S + 0.1))

    def test_the_rpm_is_blanked_not_computed_while_a_prompt_plays(self):
        speech.STATE["quiet_until"] = time.time() + 5
        audio.STATE["rpm_est"] = 3000
        tone = array.array("h", (int(8000 * math.sin(2 * math.pi * 50 * i / audio.SAMPLE_RATE))
                                 for i in range(audio.WINDOW_SAMPLES)))

        class Chunk:
            chunk = tone.tobytes()
        audio.on_stream(Chunk())
        self.assertIsNone(audio.STATE["rpm_est"])
        self.assertEqual(audio.STATE["rpm_rejected"], "voice prompt playing")
        self.assertEqual(len(audio.STATE["_buf"]), 0, "the voice was left in the window")


class SilenceIsNeverQuiet(_Clean):
    def test_no_offline_voice_says_what_to_do(self):
        self.engine = FakeEngine(ok=False, problem=speech.NO_VOICE)
        speech._M["factory"] = lambda: self.engine          # noqa: SLF001
        speech.say("turn right")
        self.assertTrue(wait_for(lambda: speech.STATE["problem"]))
        self.assertIn("text-to-speech", speech.STATE["problem"])
        self.assertIn("Install voice data", speech.status_text())
        self.assertEqual(self.engine.said, [], "spoke with no offline voice")

    def test_no_engine_at_all_is_said_and_does_not_crash(self):
        def broken():
            raise ImportError("No module named 'jnius'")
        speech._M["factory"] = broken                       # noqa: SLF001
        speech.say("turn right")
        self.assertTrue(wait_for(lambda: speech.STATE["problem"]))
        self.assertEqual(speech.STATE["problem"], speech.NO_ENGINE)

    def test_the_ride_page_shows_the_problem_when_announce_is_on(self):
        from agedashii_navi.pages import ride as ride_page
        from tests.test_road_test import Page, texts
        ride_page.forget_map()
        speech.STATE["problem"] = speech.NO_VOICE
        speech.STATE["started"] = True          # keep build() from starting a worker
        prefs.set_value("voice", True)
        built = ride_page.build(Page())
        self.assertIn(speech.NO_VOICE, texts(built))
        prefs.set_value("voice", False)
        built = ride_page.build(Page())
        self.assertNotIn(speech.NO_VOICE, texts(built))


class TheWordsAreTrue(unittest.TestCase):
    def test_arrival_is_not_onto_a_street(self):
        line = guidance.spoken("arrive at your destination", "Broadway", 120, True, "near")
        self.assertNotIn("onto", line)
        self.assertEqual(guidance.spoken("turn left", "Grand Avenue", 120, True, "now"),
                         "turn left onto Grand Avenue")

    def test_no_screen_still_says_the_app_cannot_speak(self):
        for page in ("settings.py", "help.py", "ride.py"):
            src = (APP_DIR / "agedashii_navi" / "pages" / page).read_text()
            self.assertNotIn("no text-to-speech on", src, page)
            self.assertNotIn("Flet has no text-to-speech on any platform", src, page)


if __name__ == "__main__":
    unittest.main()


class TheBannerDoesNotInvent(unittest.TestCase):
    """Minion 2's catch from the Kotlin port: absence must not look like a reading."""

    def test_no_instruction_draws_no_arrow(self):
        self.assertIsNone(guidance.turn_icon(""))
        self.assertIsNone(guidance.turn_icon(None))
        self.assertEqual(guidance.turn_icon("turn left"), "left")

    def test_no_distance_is_ever_zero_feet(self):
        self.assertEqual(guidance.distance_text(3.0), "—")
        self.assertEqual(guidance.distance_text(3.0, imperial=False), "—")
        self.assertEqual(guidance.distance_text(30.0), "100 ft")
