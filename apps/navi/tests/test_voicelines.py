"""Her recorded lines: which clip plays, what stands in for a missing one, and
that a missing or broken clip is never silence.

The player is faked the same way test_speech fakes the engine. The real one -
android.media.MediaPlayer through pyjnius - is proved on the emulator; these
pin the choices around it.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from agedashii_washi import routing  # noqa: E402

from agedashii_navi import (guidance, live, prefs, speech, voice_import,  # noqa: E402
                            voicelines)
from tests.test_speech import FakeEngine, wait_for  # noqa: E402


class ClipEngine(FakeEngine):
    """FakeEngine plus the three clip calls, and one ordered log of both."""

    def __init__(self, broken=()):
        super().__init__()
        self.broken = set(broken)
        self.heard, self.stopped = [], 0
        self.clip_until = 0.0

    def speak(self, text):
        self.heard.append(("tts", text))
        return super().speak(text)

    def play(self, path):
        if path in self.broken:
            raise RuntimeError("setDataSource failed")
        self.heard.append(("clip", os.path.basename(path)))
        self.clip_until = time.time() + self.seconds

    def is_playing(self):
        return time.time() < self.clip_until

    def stop_clip(self):
        self.stopped += 1


def clips(*names):
    """A clip_path that has exactly `names`."""
    return lambda name: f"/voice/{name}.m4a" if name in names else None


def fired(instruction, street="Grand Ave", metres=121.92, band="near"):
    return {"instruction": instruction, "street": street, "metres": metres,
            "band": band}


class WhichClip(unittest.TestCase):
    def test_every_word_the_offline_router_says_has_a_clip(self):
        for _lo, _hi, words in routing.TURNS:
            self.assertIsNotNone(voicelines.maneuver_name(words), words)
        self.assertEqual(voicelines.maneuver_name("make a U-turn"), "uturn")
        self.assertEqual(voicelines.maneuver_name("bear left"), "slight-left")
        self.assertEqual(voicelines.maneuver_name("continue straight"), "continue")
        self.assertEqual(voicelines.maneuver_name("arrive at your destination"),
                         "arrive")

    def test_the_street_name_never_picks_the_clip(self):
        self.assertEqual(voicelines.maneuver_name("continue onto Ramp Road"),
                         "continue")
        self.assertEqual(voicelines.maneuver_name("turn left onto Merge Street"),
                         "turn-left")
        self.assertIsNone(voicelines.maneuver_name(None))
        self.assertIsNone(voicelines.maneuver_name(""))

    def test_every_clip_name_is_one_of_hers_and_every_one_of_hers_is_used(self):
        names = set(voice_import.NAMES)
        used = ({n for _w, n in voicelines.MANEUVERS}
                | set(voice_import.FIXED_DISTANCES) | set(voicelines.SITUATIONS))
        self.assertEqual(used, names)
        self.assertEqual(set(voicelines.STAND_IN), {n for _w, n in voicelines.MANEUVERS})

    def test_keep_left_is_a_slight_left(self):
        # minion 4's new offline words; "left" alone would make it a turn.
        self.assertEqual(voicelines.maneuver_name("keep left onto I 80"), "slight-left")
        self.assertEqual(voicelines.maneuver_name("keep right"), "slight-right")

    def test_the_routers_kind_picks_the_clip_over_the_words(self):
        items = voicelines.playlist("turn right to take the ramp",
                                    dict(fired("turn right to take the ramp", band="now"),
                                         kind="slight-right"),
                                    clips("slight-right", "turn-right"))
        self.assertEqual(items, [("clip", "/voice/slight-right.m4a",
                                  "turn right to take the ramp")])

    def test_the_depart_is_never_her_start_line(self):
        # kind "start" is "head north on First St"; her "start" clip is the
        # situation line, already said once when the route begins.
        items = voicelines.playlist("head north on First St",
                                    dict(fired("head north", band="now"), kind="start"),
                                    clips("start"))
        self.assertEqual(items, [("tts", "head north on First St", None)])

    def test_a_roundabout_is_its_own_clip(self):
        self.assertEqual(voicelines.maneuver_name("take the 2nd exit at the roundabout"),
                         "roundabout")


#: Arnold's set, as AGEDASHII handed it to her: bare numbers, in feet.
ARNOLD = {"dist-200ft": "/voice/dist-200ft.mp3", "dist-400ft": "/voice/dist-400ft.mp3",
          "dist-800ft": "/voice/dist-800ft.mp3", "dist-1500ft": "/voice/dist-1500ft.mp3"}
FT = 1 / 3.280839895


def dist(feet, clips=ARNOLD):
    found = voicelines.distance_clip(feet * FT, clips)
    return found[0] if found else None


class WhichDistance(unittest.TestCase):
    """The nearest distance clip SHE HAS - within a factor of 1.5, or none.
    The same table is minion 2's, for the Kotlin app reading the same files."""

    def test_her_set_within_a_quarter_either_way(self):
        # x1.25 (AGEDASHII, from minion 4's audit): "fifteen hundred" for a
        # turn 2,200 ft away is a wrong instruction. Her set now covers
        # 160-250, 320-500, 640-1000 and 1200-1875 ft; between, the phone.
        self.assertEqual(dist(160), "dist-200ft")
        self.assertEqual(dist(250), "dist-200ft")
        self.assertEqual(dist(320), "dist-400ft")
        self.assertEqual(dist(500), "dist-400ft")
        self.assertEqual(dist(820), "dist-800ft")
        self.assertEqual(dist(1000), "dist-800ft")
        self.assertEqual(dist(1640), "dist-1500ft")
        self.assertEqual(dist(1875), "dist-1500ft")

    def test_outside_the_bound_it_is_the_phone_voice_not_a_wrong_number(self):
        # "Two hundred" for a turn a mile away is a wrong instruction.
        self.assertIsNone(dist(148))
        self.assertIsNone(dist(279))
        self.assertIsNone(dist(1100))
        self.assertIsNone(dist(1880))
        self.assertIsNone(dist(2247))
        self.assertIsNone(dist(5280))
        self.assertIsNone(voicelines.distance_clip(0, ARNOLD))
        self.assertIsNone(voicelines.distance_clip(None, ARNOLD))
        self.assertIsNone(voicelines.distance_clip(100, {}))

    def test_a_tie_goes_to_the_shorter_distance(self):
        # Never announce a turn as further away than it is.
        near = {"dist-200ft": "/200.mp3", "dist-240ft": "/240.mp3"}
        self.assertEqual(dist((200 * 240) ** 0.5, near), "dist-200ft")

    def test_named_and_mile_distances_count(self):
        clips = {"dist-quarter": "/q.mp3", "dist-0.5mile": "/h.mp3", "dist-2miles": "/2.mp3"}
        self.assertEqual(dist(1300, clips), "dist-quarter")
        self.assertEqual(dist(2700, clips), "dist-0.5mile")
        self.assertEqual(dist(10000, clips), "dist-2miles")
        self.assertIsNone(dist(5280, clips))


class TheRule(unittest.TestCase):
    """A missing clip is the phone's voice for that item alone. Never silence."""

    SAY = "In 400 ft, turn left onto Grand Ave"

    def test_with_none_of_hers_it_is_our_sentence_unchanged(self):
        self.assertEqual(voicelines.playlist(self.SAY, fired("turn left"), clips()),
                         [("tts", self.SAY, None)])
        self.assertEqual(voicelines.playlist(self.SAY, None, clips("turn-left")),
                         [("tts", self.SAY, None)])

    def test_her_turn_with_our_distance(self):
        items = voicelines.playlist(self.SAY, fired("turn left"), clips("turn-left"))
        self.assertEqual(items, [("tts", "In 400 ft,", None),
                                 ("clip", "/voice/turn-left.m4a", "turn left")])

    def test_her_distance_with_our_turn(self):
        items = voicelines.playlist(self.SAY, fired("turn left"), clips("dist-500ft"))
        self.assertEqual(items, [("clip", "/voice/dist-500ft.m4a", "In 400 ft,"),
                                 ("tts", "turn left", None)])

    def test_arnolds_distance_with_his_turn(self):
        items = voicelines.playlist("In 400 ft, turn left onto Grand Ave",
                                    fired("turn left"), clips("turn-left"),
                                    distances=ARNOLD)
        self.assertEqual(items, [("clip", "/voice/dist-400ft.mp3", "In 400 ft,"),
                                 ("clip", "/voice/turn-left.m4a", "turn left")])

    def test_a_distance_too_far_from_any_of_his_is_ours(self):
        items = voicelines.playlist("In 0.5 mi, turn left onto Grand Ave",
                                    fired("turn left", metres=804.67), clips("turn-left"),
                                    distances=ARNOLD)
        self.assertEqual(items, [("tts", "In 0.5 mi,", None),
                                 ("clip", "/voice/turn-left.m4a", "turn left")])

    def test_leaving_a_roundabout_is_words_never_her_entering_line(self):
        # #1: leaving has kind None. None means WORDS - never a word-match that
        # finds "roundabout" and plays "ROUNDABOUT" on the way out.
        say = "exit the roundabout onto Main St"
        items = voicelines.playlist(say, dict(fired("exit the roundabout", band="now"),
                                              kind=None), clips("roundabout", "exit"))
        self.assertEqual(items, [("tts", say, None)])

    def test_the_exit_number_follows_her_roundabout_line(self):
        # #2: her line replaces the verb, so the phone says which exit.
        items = voicelines.playlist(
            "enter the roundabout and take the 2nd exit",
            dict(fired("enter the roundabout and take the 2nd exit", band="now"),
                 kind="roundabout"), clips("roundabout"))
        self.assertEqual(items, [("clip", "/voice/roundabout.m4a",
                                  "enter the roundabout and take the 2nd exit"),
                                 ("tts", "take the 2nd exit", None)])

    def test_the_side_follows_her_exit_line(self):
        # #14: "on the right" exists only in the words.
        items = voicelines.playlist(
            "take the exit on the right",
            dict(fired("take the exit on the right", band="now"), kind="exit"),
            clips("exit"))
        self.assertEqual(items, [("clip", "/voice/exit.m4a", "take the exit on the right"),
                                 ("tts", "on the right", None)])

    def test_street_names_only_when_asked(self):
        both = clips("turn-left", "dist-500ft")
        items = voicelines.playlist(self.SAY, fired("turn left"), both)
        self.assertEqual([k for k, _v, _f in items], ["clip", "clip"])
        self.assertNotIn("Grand", repr(items))
        on = voicelines.playlist(self.SAY, fired("turn left"), both, streets=True)
        self.assertEqual(on[-1], ("tts", "onto Grand Ave", None))

    def test_arriving_is_never_onto_a_street(self):
        items = voicelines.playlist("arrive at your destination",
                                    fired("arrive at your destination", "Broadway"),
                                    clips("arrive"), streets=True)
        self.assertNotIn("onto", repr(items))

    def test_now_drops_the_distance(self):
        items = voicelines.playlist("turn left onto Grand Ave",
                                    fired("turn left", band="now"), clips("turn-left"))
        self.assertEqual(items, [("clip", "/voice/turn-left.m4a", "turn left")])

    def test_situations_fall_back_except_speeding(self):
        self.assertEqual(voicelines.situation("offroute", clips()),
                         [("tts", "Off route.", None)])
        self.assertEqual(voicelines.situation("offroute", clips("offroute")),
                         [("clip", "/voice/offroute.m4a", "Off route.")])
        self.assertEqual(voicelines.situation("speeding", clips()), [])
        self.assertEqual(voicelines.situation("speeding", clips("speeding")),
                         [("clip", "/voice/speeding.m4a", None)])

    def test_every_name_has_a_preview(self):
        for name in voice_import.NAMES:
            items = voicelines.preview(name, clips())
            if name == "speeding":
                self.assertEqual(items, [])
            else:
                self.assertEqual(items[0][0], "tts", name)
                self.assertTrue(items[0][1], name)
            self.assertEqual(voicelines.preview(name, clips(name))[0][0], "clip")


class Edges(unittest.TestCase):
    """Situations are said when they START, not on every fix they are true."""

    OK = {"ok": True, "off_route": False}
    OFF = {"ok": True, "off_route": True}

    def test_start_once_per_destination_not_per_reroute(self):
        # Built the way live.set_destination builds it: a new tuple per call.
        # (A literal would be one folded constant, the same object twice.)
        def chosen(lat=37.8, lon=-122.2, name="home"):
            return (float(lat), float(lon), name)
        cues, dest = voicelines.Cues(), chosen()
        self.assertEqual(cues.update(self.OK, dest, False, False, 0), ["start"])
        self.assertEqual(cues.update(self.OK, dest, False, False, 1), [])
        self.assertEqual(cues.update({"ok": False}, chosen(1, 2), False, False, 2), [])
        self.assertEqual(cues.update(self.OK, chosen(), False, False, 3),
                         ["start"])            # chosen again: a new start

    def test_off_route_and_rerouting_once_per_spell(self):
        cues = voicelines.Cues()
        self.assertEqual(cues.update(self.OFF, None, True, False, 0),
                         ["offroute", "rerouting"])
        for t in (12, 24, 36):                 # the search keeps failing
            self.assertEqual(cues.update(self.OFF, None, True, False, t), [])
        self.assertEqual(cues.update(self.OK, None, False, False, 40), [])
        self.assertEqual(cues.update(self.OFF, None, False, False, 50), ["offroute"])
        self.assertEqual(cues.update(self.OFF, None, True, False, 62), ["rerouting"])

    def test_a_refused_reroute_is_said_once_per_spell(self):
        cues = voicelines.Cues()
        cues.update(self.OFF, None, True, False, 0)
        self.assertTrue(cues.refused())
        self.assertFalse(cues.refused())               # the 12 s retries
        cues.update(self.OK, None, False, False, 30)
        cues.update(self.OFF, None, True, False, 40)
        self.assertTrue(cues.refused())

    def test_the_refusal_is_said_in_words_she_can_act_on(self):
        from agedashii_washi import routing as R
        outside = voicelines.refusal(R.START_OUTSIDE)
        self.assertEqual(outside[0][0], "tts")
        self.assertIn("outside", outside[0][1])
        self.assertLess(len(outside[0][1]), len(R.START_OUTSIDE))   # made for the ear
        self.assertEqual(voicelines.refusal("no route: something else")[0][0], "tts")

    def test_speeding_on_the_edge_and_not_again_within_a_minute(self):
        cues = voicelines.Cues()
        self.assertEqual(cues.update(None, None, False, True, 0), ["speeding"])
        self.assertEqual(cues.update(None, None, False, True, 5), [])
        cues.update(None, None, False, False, 10)
        self.assertEqual(cues.update(None, None, False, True, 20), [])
        cues.update(None, None, False, False, 70)
        self.assertEqual(cues.update(None, None, False, True, 80), ["speeding"])


class _Clean(unittest.TestCase):
    def setUp(self):
        speech.reset()
        prefs.reset()
        live.reset()
        self.engine = ClipEngine(broken={"/voice/broken.m4a"})
        speech._M["factory"] = lambda: self.engine          # noqa: SLF001
        self.folder = tempfile.mkdtemp(prefix="voice-")
        self.old = os.environ.get("FLET_APP_STORAGE_DATA")
        os.environ["FLET_APP_STORAGE_DATA"] = self.folder
        os.makedirs(voice_import.voice_dir())
        # The bundled set, empty unless a test puts a clip in it.
        self.bundle = os.path.join(self.folder, "bundle")
        os.makedirs(self.bundle)
        real_builtin = voice_import.builtin_dir
        voice_import.builtin_dir = lambda: self.bundle
        self.addCleanup(setattr, voice_import, "builtin_dir", real_builtin)

    def tearDown(self):
        speech.reset()
        if self.old is None:
            os.environ.pop("FLET_APP_STORAGE_DATA", None)
        else:
            os.environ["FLET_APP_STORAGE_DATA"] = self.old
        shutil.rmtree(self.folder, ignore_errors=True)

    def add(self, name, ext=".m4a", folder=None):
        path = os.path.join(folder or voice_import.voice_dir(), name + ext)
        Path(path).write_bytes(b"not really audio")
        return path

    def built_in(self, name, ext=".mp3"):
        return self.add(name, ext, folder=self.bundle)

    def capture_play(self):
        """Swap speech.play for a recorder until the test ends."""
        played, real = [], speech.play
        self.urgent = []

        def record(items, urgent=False):
            played.append(list(items))
            self.urgent.append(urgent)
        speech.play = record
        self.addCleanup(setattr, speech, "play", real)
        return played


class ThePlayer(_Clean):
    def test_clips_and_words_play_in_order_under_one_focus(self):
        speech.play([("clip", "/voice/dist-500ft.m4a", "In 400 ft,"),
                     ("tts", "turn left", None)])
        self.assertTrue(wait_for(lambda: speech.STATE["spoken"] == 1))
        self.assertEqual(self.engine.heard, [("clip", "dist-500ft.m4a"),
                                             ("tts", "turn left")])
        self.assertEqual(self.engine.focus, ["request", "abandon"])
        self.assertEqual(self.engine.stopped, 1)          # released, not leaked

    def test_a_clip_that_will_not_play_is_said_instead(self):
        speech.play([("clip", "/voice/broken.m4a", "In 400 ft,"),
                     ("clip", "/voice/turn-left.m4a", "turn left")])
        self.assertTrue(wait_for(lambda: speech.STATE["spoken"] == 1))
        self.assertEqual(self.engine.heard, [("tts", "In 400 feet,"),
                                             ("clip", "turn-left.m4a")])

    def test_her_clips_play_when_no_voice_passes_the_test_word(self):
        self.engine.ok, self.engine.problem = False, speech.NO_WORKING_VOICE
        speech.play([("clip", "/voice/turn-left.m4a", "turn left"),
                     ("tts", "onto Grand Ave", None)])
        self.assertTrue(wait_for(lambda: speech.STATE["spoken"] == 1))
        self.assertEqual(self.engine.heard, [("clip", "turn-left.m4a")])
        self.assertEqual(speech.STATE["problem"], speech.NO_WORKING_VOICE)

    def test_a_file_that_will_not_prepare_releases_its_player(self):
        # Seen on emulator-5556: a corrupt turn-right.mp3 left a MediaPlayer
        # idle in `dumpsys audio` - one native player leaked per bad prompt.
        made = []

        class Player:
            def __init__(self):
                self.released = False
                made.append(self)

            def setAudioAttributes(self, _a):
                pass

            def setDataSource(self, _p):
                pass

            def prepare(self):
                raise OSError("Prepare failed.: status=0x1")

            def release(self):
                self.released = True

        backend = speech.AndroidBackend.__new__(speech.AndroidBackend)
        backend._autoclass, backend.attrs = (lambda _name: Player), object()  # noqa: SLF001
        with self.assertRaises(OSError):
            backend.play("/voice/turn-right.mp3")
        self.assertTrue(made[0].released)
        self.assertFalse(backend.is_playing())

    def test_the_spoken_copy_says_whole_numbers(self):
        self.assertEqual(speech.for_the_ear("In 2.0 mi,"), "In 2 miles,")
        self.assertEqual(speech.for_the_ear("In 1.0 mi,"), "In 1 mile,")
        self.assertEqual(speech.for_the_ear("In 0.1 mi,"), "In 0.1 miles,")
        self.assertEqual(speech.for_the_ear("In 11 mi,"), "In 11 miles,")
        self.assertEqual(speech.for_the_ear("In 400 ft,"), "In 400 feet,")


class TheRidePage(_Clean):
    """ride._announce: one fix, one playlist, her clips where she has them."""

    def setUp(self):
        super().setUp()
        from agedashii_navi.pages import ride
        self.ride = ride
        ride._M.pop("cues", None)                          # noqa: SLF001
        self.addCleanup(ride._M.pop, "cues", None)         # noqa: SLF001
        self.played = self.capture_play()

    def announce(self, status=None, say="", parts=None, reroute=False):
        out = {"moved": True, "status": status, "say": say, "say_parts": parts,
               "reroute": reroute}
        return self.ride._announce(out, None)              # noqa: SLF001

    def test_an_open_ended_distance_clip_is_played_from_a_fix(self):
        turn, far = self.add("turn-left", ".mp3"), self.add("dist-400ft", ".mp3")
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("clip", turn, "turn left")]])

    def test_a_built_in_clip_plays_with_no_import(self):
        turn, far = self.built_in("turn-left"), self.built_in("dist-400ft")
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("clip", turn, "turn left")]])

    def test_her_turn_clip_is_played_from_a_fix(self):
        path = self.add("turn-left")
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("tts", "In 400 ft,", None),
                                        ("clip", path, "turn left")]])

    def test_on_the_now_band_the_turn_plays_before_a_situation(self):
        # #10: a 1.5 s speeding line must not eat the 6 s "now" warning.
        turn, fast = self.add("turn-left"), self.add("speeding")
        prefs.set_value("voice", True)
        live.LIVE["speed_ms"] = 40.0
        self.ride._announce({"moved": True, "status": {"ok": True, "off_route": False},  # noqa: SLF001
                             "say": "turn left onto Grand Ave",
                             "say_parts": fired("turn left", band="now"),
                             "reroute": False}, 25)
        self.assertEqual(self.played, [[("clip", turn, "turn left"), ("clip", fast, None)]])
        self.assertEqual(self.urgent, [True])

    def test_a_refused_reroute_is_spoken(self):
        from agedashii_washi import routing as R
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.played.clear()
        failed = R.Route(ok=False, source="offline", reason=R.START_OUTSIDE)
        self.ride._refused(failed)                           # noqa: SLF001
        self.ride._refused(failed)                           # noqa: SLF001 - a retry
        self.assertEqual(len(self.played), 1)
        self.assertIn("outside", self.played[0][0][1])

    def test_off_route_and_rerouting_go_as_one_playlist(self):
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.assertEqual(self.played, [[("tts", "Off route.", None),
                                        ("tts", "Re-routing.", None)]])

    def test_announce_off_says_nothing_and_does_not_blurt_later(self):
        prefs.set_value("voice", False)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.assertEqual(self.played, [])
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.assertEqual(self.played, [])      # still the same spell

    def test_the_streets_switch_is_hers_and_off(self):
        self.assertFalse(prefs.clip_streets())
        prefs.set_value("clip_streets", True)
        self.assertTrue(prefs.clip_streets())

    def test_a_follower_fires_the_routers_kind(self):
        points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        cum = routing.cumulative_m(points)
        route = routing.Route(ok=True, source="offline", points=points, cum_m=cum,
                              maneuvers=[
                                  routing.Maneuver(instruction="head north", street="First St",
                                                   distance_m=0.0, at=(0.0, 0.0), index=0,
                                                   cum_m=0.0, kind="start"),
                                  routing.Maneuver(instruction="keep left", street="I 80",
                                                   distance_m=0.0, at=(0.0, 0.0), index=20,
                                                   cum_m=cum[20], kind="slight-left")],
                              distance_m=cum[-1], duration_s=600.0)
        follower = guidance.Follower(route, offroute_m=45)
        for point in points[:20]:
            status = follower.update(point, speed_ms=15.0)
            if status.get("say"):
                self.assertEqual(status["say_parts"]["kind"], "slight-left")
                self.assertEqual(status["icon"], "slight_left")
                return
        self.fail("no prompt fired")

    def test_a_follower_fires_the_parts_as_well_as_the_sentence(self):
        points = [(37.80 + i * 0.0005, -122.27) for i in range(41)]
        cum = routing.cumulative_m(points)
        route = routing.Route(ok=True, source="offline", points=points, cum_m=cum,
                              maneuvers=[], distance_m=cum[-1], duration_s=600.0)
        route.maneuvers = [
            routing.Maneuver(instruction="head north", street="First St",
                             distance_m=0.0, at=(0.0, 0.0), index=0, cum_m=0.0),
            routing.Maneuver(instruction="turn left", street="Grand Ave",
                             distance_m=0.0, at=(0.0, 0.0), index=20, cum_m=cum[20])]
        follower = guidance.Follower(route, offroute_m=45)
        for point in points[:20]:
            status = follower.update(point, speed_ms=15.0)
            if status.get("say"):
                parts = status["say_parts"]
                self.assertEqual(parts["instruction"], "turn left")
                self.assertEqual(parts["street"], "Grand Ave")
                self.assertIn(parts["band"], ("far", "near", "now"))
                return
        self.fail("no prompt fired")


class TheSettingsPage(_Clean):
    def build(self):
        from agedashii_navi.pages import settings
        from tests.test_road_test import Page
        self.page = Page()
        return settings.build(self.page)

    def test_every_name_is_listed_and_tappable(self):
        from tests.test_road_test import tappable_texts
        tapped = tappable_texts(self.build())
        for name in voice_import.NAMES:
            self.assertIn(name, tapped)

    def test_a_tile_says_whose_voice_and_plays_it(self):
        from tests.test_road_test import holder_of, texts
        path = self.add("turn-left", ".wav")
        built = self.build()
        mine, not_yet = holder_of(built, "turn-left"), holder_of(built, "turn-right")
        self.assertIn("yours", texts(mine))
        self.assertIn("phone voice", texts(not_yet))
        self.assertIn("silent", texts(holder_of(built, "speeding")))
        played = self.capture_play()
        mine.on_click(None)
        not_yet.on_click(None)
        self.assertEqual(played, [[("clip", path, "turn left")],
                                  [("tts", "turn right", None)]])

    def test_a_distance_she_named_herself_gets_a_tile(self):
        from tests.test_road_test import holder_of, texts
        self.add("dist-400ft", ".mp3")
        built = self.build()
        tile = holder_of(built, "dist-400ft")
        self.assertIsNotNone(tile)
        self.assertIn("yours", texts(tile))
        # Once she has any distance, the fixed names she never recorded go:
        # the player rounds to hers, so they would only be nagging.
        self.assertIsNone(holder_of(built, "dist-100ft"))

    def test_hers_built_in_and_phone_voice_are_told_apart(self):
        from tests.test_road_test import holder_of, texts
        mine = self.add("turn-left", ".wav")
        self.built_in("turn-left")                     # hers wins over it
        arnold = self.built_in("turn-right")
        built = self.build()
        self.assertIn("yours", texts(holder_of(built, "turn-left")))
        self.assertIn("built in", texts(holder_of(built, "turn-right")))
        self.assertIn("phone voice", texts(holder_of(built, "uturn")))
        played = self.capture_play()
        holder_of(built, "turn-left").on_click(None)
        holder_of(built, "turn-right").on_click(None)
        self.assertEqual(played, [[("clip", mine, "turn left")],
                                  [("clip", arnold, "turn right")]])

    def test_the_import_panel_refreshes_the_list(self):
        from tests.test_road_test import holder_of, texts
        seen, real = {}, voice_import.panel

        def spy(page, picker=None, on_change=None):
            seen["on_change"] = on_change
            return real(page, picker=picker, on_change=on_change)
        voice_import.panel = spy
        self.addCleanup(setattr, voice_import, "panel", real)
        built = self.build()
        self.assertIn("phone voice", texts(holder_of(built, "uturn")))
        self.add("uturn", ".mp3")
        seen["on_change"]()                    # what the panel does after an import
        self.assertIn("yours", texts(holder_of(built, "uturn")))


if __name__ == "__main__":
    unittest.main()
