"""Spoken turns: the prompt that fires is said, the newest wins, and silence
that looks like working is impossible.

The engine is faked. The real one - android.speech.tts through pyjnius - was
proved on emulator-5556 (bind 0.11 s, offline voice, speak() -> 0); these pin
the behaviour around it that a phone cannot show in a test.
"""
import array
import math
import os
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


class _JLocale:
    def __init__(self, lang, country):
        self.lang, self.country = lang, country

    def getLanguage(self):
        return self.lang

    def getCountry(self):
        return self.country


class _JVoice:
    """A voice as Google's engine lists it. `writes` is what it really produces."""

    def __init__(self, name, country="US", quality=400, latency=200,
                 network=False, installed=True, writes=20000):
        self.name, self.locale = name, _JLocale("en", country)
        self.quality, self.latency = quality, latency
        self.network, self.installed, self.writes = network, installed, writes

    def getName(self):
        return self.name

    def getLocale(self):
        return self.locale

    def getQuality(self):
        return self.quality

    def getLatency(self):
        return self.latency

    def isNetworkConnectionRequired(self):
        return self.network

    def getFeatures(self):
        installed = self.installed
        return type("F", (), {"contains": lambda _s, f: f == "notInstalled" and not installed})()


class _JTts:
    """android.speech.tts.TextToSpeech as it behaved for minion 1 on the
    emulator: every call says success, and some voices write no audio at all."""

    def __init__(self, voices):
        self.voices, self.voice, self.probed = voices, voices[0], []
        self.throws = set()

    def setLanguage(self, _loc):
        return 0

    def getDefaultEngine(self):
        return "com.google.android.tts"

    def getVoices(self):
        items = list(self.voices)
        it = type("It", (), {"hasNext": lambda _s: bool(items),
                             "next": lambda _s: items.pop(0)})()
        return type("Set", (), {"iterator": lambda _s: it})()

    def setVoice(self, voice):
        self.voice = voice
        return 0

    def getVoice(self):
        return self.voice

    def stop(self):
        return 0

    def setAudioAttributes(self, _a):
        return 0

    def synthesizeToFile(self, _text, params, path):
        if params is None:
            # What the real (String, HashMap, String) overload does on API 34:
            # it reads the map without checking it.
            raise RuntimeError("java.lang.NullPointerException: HashMap.get() on null")
        self.probed.append(self.voice.name)
        if self.voice.name in self.throws:
            raise RuntimeError("JVM exception occurred")
        if self.voice.writes:
            with open(path, "wb") as f:
                f.write(b"\0" * self.voice.writes)
        return 0                                  # "accepted", as ever


class EveryVoiceIsHeardBeforeItIsTrusted(unittest.TestCase):
    """Minion 1's find: Google's engine lists voices as installed that fail
    every prompt (error -4) while speak() returns 0. A voice is used only after
    it has written a test word to a file."""

    def backend(self, voices):
        import tempfile
        folder = tempfile.mkdtemp(prefix="tts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        # A silent voice is waited on for PROBE_EACH_S; a test need not.
        each, speech.PROBE_EACH_S = speech.PROBE_EACH_S, 0.3
        self.addCleanup(setattr, speech, "PROBE_EACH_S", each)
        backend = speech.AndroidBackend.__new__(speech.AndroidBackend)
        backend.tts = _JTts(voices)
        backend.attrs = object()
        backend._autoclass = (lambda n: dict if n == "java.util.HashMap"  # noqa: SLF001
                              else type("L", (), {"US": _JLocale("en", "US")}))
        cache = type("D", (), {"getAbsolutePath": lambda _s: folder})()
        backend._ctx = type("C", (), {"getCacheDir": lambda _s: cache})()  # noqa: SLF001
        self.folder = folder
        return backend

    def test_the_first_voice_that_speaks_not_the_first_that_claims_to(self):
        voices = [_JVoice("en-US-language", quality=300, writes=44),
                  _JVoice("en-us-x-tpf-local", writes=0),
                  _JVoice("en-us-x-sfg-local", latency=300),
                  _JVoice("en-us-x-net", network=True),
                  _JVoice("en-us-x-iob-local", installed=False)]
        backend = self.backend(voices)
        ok, problem, _engine, voice, offline = backend.init(time.time() + 5)
        self.assertEqual((ok, problem, voice, offline), (True, "", "en-us-x-sfg-local", True))
        self.assertEqual(backend.tts.getVoice().name, "en-us-x-sfg-local")
        self.assertNotIn("en-us-x-net", backend.tts.probed)
        # The test word is not left behind; the voice that passed is remembered.
        self.assertEqual(os.listdir(self.folder), ["navi-voice-ok.txt"])
        # Next start: that voice is tried first, and the silent ones not at all.
        again = self.backend(voices)
        again._ctx = backend._ctx                           # noqa: SLF001
        self.assertEqual(again.init(time.time() + 5)[3], "en-us-x-sfg-local")
        self.assertEqual(again.tts.probed, ["en-us-x-sfg-local"])

    def test_a_voice_that_throws_is_skipped_not_fatal(self):
        backend = self.backend([_JVoice("en-us-x-tpf-local"), _JVoice("en-us-x-sfg-local")])
        backend.tts.throws.add("en-us-x-tpf-local")
        ok, problem, _engine, voice, _offline = backend.init(time.time() + 5)
        self.assertEqual((ok, problem, voice), (True, "", "en-us-x-sfg-local"))

    def test_no_voice_that_speaks_is_said_on_screen(self):
        backend = self.backend([_JVoice("en-US-language", writes=44),
                                _JVoice("en-us-x-tpf-local", writes=0)])
        ok, problem, _engine, _voice, _offline = backend.init(time.time() + 5)
        self.assertFalse(ok)
        self.assertEqual(problem, speech.NO_WORKING_VOICE)
        self.assertIn("text-to-speech", problem)


class TheFirstPromptOfARoute(_Clean):
    """Minion 1's other find, in the Kotlin app: the first prompt after she
    picks a destination was consumed without being spoken. It is the one she
    most needs, and it fires while the engine may still be coming up."""

    def test_a_prompt_fired_before_the_engine_is_up_is_still_said(self):
        engine = self.engine

        def slow(deadline):
            time.sleep(0.4)                 # binding, and the test word
            return FakeEngine.init(engine, deadline)
        engine.init = slow
        speech.say("In 0.5 mi, turn left onto Grand Ave")   # nothing started yet
        self.assertFalse(speech.STATE["ready"])
        self.assertTrue(wait_for(lambda: engine.said, timeout=3))
        self.assertEqual(engine.said, ["In 0.5 miles, turn left onto Grand Ave"])

    def test_the_first_fix_on_a_new_route_fires_a_prompt(self):
        from agedashii_washi import routing
        points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        cum = routing.cumulative_m(points)
        route = routing.Route(ok=True, source="offline", points=points, cum_m=cum,
                              maneuvers=[
                                  routing.Maneuver(instruction="head north", street="First St",
                                                   distance_m=0.0, at=(0.0, 0.0), index=0,
                                                   cum_m=0.0),
                                  routing.Maneuver(instruction="turn left", street="Grand Ave",
                                                   distance_m=0.0, at=(0.0, 0.0), index=5,
                                                   cum_m=cum[5])],
                              distance_m=cum[-1], duration_s=600.0)
        follower = guidance.Follower(route, offroute_m=45)
        first = follower.update(points[0], speed_ms=15.0)
        self.assertTrue(first.get("say"))
        self.assertIn("turn left", first["say"])


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


if __name__ == "__main__":
    unittest.main()
