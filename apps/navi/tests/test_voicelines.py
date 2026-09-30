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
from unittest import mock
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import flet as ft  # noqa: E402

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
        used = ({n for _w, n in voicelines.MANEUVERS} | set(voicelines.SIDED)
                | set(voicelines.JOINS)
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
            if name in ("speeding", "open"):
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

    def test_the_spam_cap_matches_kotlin_timeline(self):
        # AGEDASHII's ruling; minion 2's Kotlin timeline, fix for fix. The first
        # line of a spell always speaks; then at most one situation playlist per
        # SITUATION_EVERY_S; the spell ends after REJOIN_S on route.
        cues = voicelines.Cues()
        up = lambda status, t, reroute=False: cues.update(status, None, reroute, False, t)
        self.assertEqual(up(self.OK, 0), [])
        self.assertEqual(up(self.OFF, 10, True), ["offroute", "rerouting"])
        self.assertEqual(up(self.OK, 11), [])
        self.assertEqual(up(self.OFF, 17.5), [])                 # held
        self.assertEqual(up(self.OFF, 22.6, True), [])           # held
        self.assertEqual(up(self.OK, 23.6), [])
        self.assertEqual(up(self.OFF, 30.7), [])                 # held
        self.assertEqual(up(self.OK, 40.5), [])
        self.assertEqual(up(self.OFF, 41), ["offroute"])         # 31 s after the last
        for t in range(45, 77):
            self.assertEqual(up(self.OK, t), [])                 # 31 s on: spell over
        self.assertEqual(up(self.OFF, 80, True), ["offroute", "rerouting"])

    def test_a_refused_reroute_is_said_once_per_spell(self):
        cues = voicelines.Cues()
        cues.update(self.OFF, None, True, False, 0)
        self.assertTrue(cues.refused())
        self.assertFalse(cues.refused())               # the 12 s retries
        cues.update(self.OK, None, False, False, 5)    # a brief "on": same spell
        cues.update(self.OFF, None, True, False, 8)
        self.assertFalse(cues.refused())
        for t in range(10, 42):
            cues.update(self.OK, None, False, False, t)   # 31 s on route: spell over
        cues.update(self.OFF, None, True, False, 50)
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


class Always:
    """An rng that always says yes and picks the first choice."""

    def random(self):
        return 0.0

    def choice(self, seq):
        return seq[0]


class Never(Always):
    def random(self):
        return 0.999


POOL = {"flourish-urgent-do-it-now": "/v/a.mp3", "flourish-urgent-move": "/v/b.mp3"}


class CharacterLines(unittest.TestCase):
    """Hers: "DO IT NOW and yeaaararrghhhhh and get to the choppaa ... peppered
    in randomly and accordingly". AFTER the instruction, never instead of it."""

    def test_the_rate_is_a_named_constant_per_mood(self):
        self.assertEqual(voicelines.FLOURISH_RATE["urgent"], 0.25)
        self.assertEqual(set(voicelines.FLOURISH_RATE), set(voicelines.FLOURISH_MOODS))
        self.assertIsNone(voicelines.Flourish(Never()).pick("urgent", POOL))
        self.assertIsNotNone(voicelines.Flourish(Always()).pick("urgent", POOL))

    def test_never_the_same_one_twice_running(self):
        f = voicelines.Flourish(Always())
        first = f.pick("urgent", POOL)[0]
        second = f.pick("urgent", POOL)[0]
        self.assertNotEqual(first, second)
        self.assertIsNone(voicelines.Flourish(Always()).pick("urgent", {}))
        # A mood of one line holds nothing back (k = n // 2 = 0, agreed with
        # minion 2): skipping it every other time would only thin it out.
        lone = voicelines.Flourish(Always())
        lone.pick("urgent", {"flourish-urgent-move": "/v/b.mp3"})
        self.assertIsNotNone(lone.pick("urgent", {"flourish-urgent-move": "/v/b.mp3"}))

    def test_at_most_one_per_maneuver(self):
        f = voicelines.Flourish(Always())
        self.assertIsNotNone(f.pick("urgent", POOL, maneuver=7))     # far
        self.assertIsNone(f.pick("urgent", POOL, maneuver=7))        # near
        self.assertIsNotNone(f.pick("urgent", POOL, maneuver=8))     # the next turn

    def test_the_mood_follows_the_moment(self):
        mood = voicelines.flourish_mood
        self.assertEqual(mood([], fired("turn left")), "urgent")             # near
        # AGEDASHII's ruling (minion 2's Kotlin matches): urgent on NEAR and
        # NOW, never FAR - "In half a mile... NOW!" could send her in early.
        self.assertIsNone(mood([], fired("turn left", band="far")))
        self.assertEqual(mood([], fired("turn left", band="now")), "urgent")
        # "now" takes urgent or arrival and nothing else.
        self.assertEqual(mood(["speeding"], fired("turn left", band="now")), "urgent")
        self.assertEqual(mood(["speeding"], fired("turn left", band="far")), "scold")
        self.assertEqual(mood([], dict(fired("arrive at your destination", band="now"),
                                      kind="arrive")), "arrival")
        self.assertEqual(mood(["offroute", "rerouting"], None), "angry")
        self.assertEqual(mood(["speeding"], fired("turn left")), "scold")
        self.assertEqual(mood(["start"], fired("turn left")), "start")
        # Arrival only on the arrive maneuver's OWN "now" prompt (minion 2):
        # nothing follows it. Its far and near prompts get none.
        self.assertIsNone(mood([], dict(fired("arrive at your destination"), kind="arrive")))
        self.assertEqual(mood(["offroute"], dict(fired("arrive at your destination"),
                                                 kind="arrive")), "angry")
        self.assertEqual(mood(["offroute"], dict(fired("arrive", band="now"),
                                                 kind="arrive")), "arrival")
        self.assertIsNone(mood([], None))

    def test_one_angry_line_a_minute_the_next_mood_may_fire(self):
        mood = voicelines.flourish_mood
        self.assertEqual(mood(["offroute", "speeding"], None), "angry")
        self.assertEqual(mood(["offroute", "speeding"], None, angry_ok=False), "scold")
        self.assertIsNone(mood(["offroute"], None, angry_ok=False))
        f = voicelines.Flourish(Always())
        self.assertTrue(f.angry_ok(now=0))
        f.pick("angry", {"flourish-angry-big-mistake": "/v/m.mp3",
                         "flourish-angry-bastard": "/v/b.mp3"}, now=0)
        self.assertFalse(f.angry_ok(now=30))
        self.assertTrue(f.angry_ok(now=61))


class Script(Always):
    """An rng that answers random() from a list, then yes for ever."""

    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def random(self):
        return self.rolls.pop(0) if self.rolls else 0.0


COUGHS = {"interject-cough": "/v/cough.mp3"}
COUGH = ("flourish", "/v/cough.mp3", None)


def piece(instruction="turn left", band="near", have=("turn-left", "dist-400ft"),
          halves=None, **extra):
    return voicelines.core(dict(fired(instruction, band=band), **extra),
                           clips(*have), distances=dists(*have), halves=halves)


def dists(*have):
    return {n: f"/voice/{n}.m4a" for n in have if n.startswith("dist-")}


def split(slot, path):
    return [f"/voice/part-{slot}-a.m4a", f"/voice/part-{slot}-b.m4a"]


D = ("clip", "/voice/dist-400ft.m4a", "In 400 ft,")
D_AFTER = ("clip", "/voice/dist-400ft.m4a", "in 400 ft")
M = ("clip", "/voice/turn-left.m4a", "turn left")
A = ("clip", "/voice/part-turn-left-a.m4a", "turn left")
B = ("clip", "/voice/part-turn-left-b.m4a", "turn left")


class Composition(unittest.TestCase):
    """AGEDASHII, from her: COMPOSITION, not a template - "turn left" + "in 200
    meters", "continue -" + cough + "- straight", "exit left human" + "do it
    now" + "damn it". Rules agreed with minion 2 (voicelines.Arranger)."""

    def test_the_maneuver_can_come_first(self):
        # order roll under P_MANEUVER_FIRST; no coughs to roll for.
        got = voicelines.Arranger(Script(0.1)).arrange(piece(), "near")
        self.assertEqual(got, [M, D_AFTER])
        got = voicelines.Arranger(Never()).arrange(piece(), "near")
        self.assertEqual(got, [D, M])

    def test_the_tail_and_street_go_with_the_maneuver(self):
        core = voicelines.core(dict(fired("take the exit on the right"), kind="exit"),
                               clips("exit", "dist-400ft"), streets=True,
                               distances=dists("dist-400ft"))
        got = voicelines.Arranger(Script(0.1)).arrange(core, "near")
        self.assertEqual(got, [("clip", "/voice/exit.m4a", "take the exit on the right"),
                               ("tts", "on the right", None),
                               ("tts", "onto Grand Ave", None),
                               ("clip", "/voice/dist-400ft.m4a", "in 400 ft")])

    def test_now_is_the_maneuver_alone(self):
        got = voicelines.Arranger(Always()).arrange(
            piece(band="now", halves=split), "now", coughs=COUGHS)
        self.assertEqual(got, [M])

    def test_the_same_shape_never_twice_running(self):
        a = voicelines.Arranger(Never())
        self.assertEqual(a.arrange(piece(), "far"), [D, M])
        self.assertEqual(a.arrange(piece(), "near"), [M, D_AFTER])     # flipped
        a.arrange(piece(band="now"), "now")                           # not counted
        self.assertEqual(a.arrange(piece(), "far"), [D, M])
        # A flourish makes it a different shape: no flip needed.
        f = [("flourish", "/v/now.mp3", None)]
        self.assertEqual(a.arrange(piece(), "near", f), [D, M] + f)

    def test_the_cough_goes_inside_a_line_in_halves(self):
        got = voicelines.Arranger(Script(0.9, 0.0)).arrange(
            piece(halves=split), "near", coughs=COUGHS)
        self.assertEqual(got, [D, A, COUGH, B])
        # The same shape again flips the order; the cough stays inside.
        a = voicelines.Arranger(Script(0.9, 0.0, 0.9, 0.0))
        a.arrange(piece(halves=split), "far", coughs=COUGHS)
        a.coughed = False                        # as if a prompt came between
        got = a.arrange(piece(halves=split), "near", coughs=COUGHS)
        self.assertEqual(got, [A, COUGH, B, D_AFTER])

    def test_without_halves_the_cough_takes_the_join(self):
        got = voicelines.Arranger(Script(0.9, 0.0)).arrange(piece(), "near", coughs=COUGHS)
        self.assertEqual(got, [D, COUGH, M])
        got = voicelines.Arranger(Script(0.1, 0.0)).arrange(piece(), "near", coughs=COUGHS)
        self.assertEqual(got, [M, COUGH, D_AFTER])

    def test_no_cough_twice_running_on_now_or_with_nowhere_to_go(self):
        a = voicelines.Arranger(Script(0.9, 0.0, 0.9, 0.0))
        self.assertIn(COUGH, a.arrange(piece(), "near", coughs=COUGHS))
        self.assertNotIn(COUGH, a.arrange(piece(), "near", coughs=COUGHS))
        self.assertNotIn(COUGH, voicelines.Arranger(Always()).arrange(
            piece(band="now"), "now", coughs=COUGHS))
        no_d = piece(metres=None)
        self.assertEqual(voicelines.Arranger(Always()).arrange(no_d, "near", coughs=COUGHS), [M])
        self.assertNotIn(COUGH, voicelines.Arranger(Never()).arrange(
            piece(), "near", coughs=COUGHS))
        self.assertLess(voicelines.COUGH_RATE, voicelines.FLOURISH_RATE["urgent"])

    def test_the_instruction_survives_every_arrangement(self):
        # Direction and distance whole in every shape, nothing but the cough
        # before the instruction ends, and his lines only after it.
        import random as r
        f = [("flourish", "/v/now.mp3", None), ("flourish", "/v/damn.mp3", None)]
        for seed in range(400):
            a = voicelines.Arranger(r.Random(seed))
            for band, halves in (("far", split), ("near", None), ("now", split)):
                got = a.arrange(piece(band=band, halves=halves), band, f, COUGHS)
                self.assertEqual(got[-2:], f)
                body = got[:-2]
                self.assertTrue(M in body or (A in body and B in body), body)
                if band == "now":
                    self.assertEqual(body, [M])
                else:
                    self.assertTrue(D in body or D_AFTER in body, body)
                    self.assertEqual([i for i in body if i[0] == "flourish"],
                                     [COUGH] if COUGH in body else [])
                    self.assertNotIn(body[0], [COUGH])
                    self.assertNotEqual(body[-1], COUGH)

    def test_our_own_sentence_stays_whole(self):
        got = voicelines.Arranger(Always()).arrange(None, "near", [("flourish", "/v/x", None)],
                                                    COUGHS, text="In 400 ft, turn left")
        self.assertEqual(got, [("tts", "In 400 ft, turn left", None), ("flourish", "/v/x", None)])


class InHalves(unittest.TestCase):
    """part-<slot>-a / -b: the plain line cut at a silence, for the cough."""

    def halves(self, path="/v/continue.mp3", where=None):
        where = where or {}
        return voicelines.halves("continue", path,
                                 source=lambda n: where.get(n, "built in"),
                                 clip_path=lambda n: f"/v/{n}.mp3")

    def test_both_halves_from_the_same_place(self):
        self.assertEqual(self.halves(), ["/v/part-continue-a.mp3", "/v/part-continue-b.mp3"])

    def test_never_her_half_and_his(self):
        self.assertIsNone(self.halves(where={"part-continue-a": "yours"}))
        self.assertIsNone(self.halves(where={"continue": "yours"}))
        self.assertIsNotNone(self.halves(where={"continue": "yours", "part-continue-a": "yours",
                                                "part-continue-b": "yours"}))

    def test_never_over_one_of_his_wild_takes(self):
        self.assertIsNone(self.halves(path="/v/wild-continue-go-on.mp3"))

    def test_a_half_that_will_not_play_says_the_whole_line(self):
        got = piece(halves=split)
        self.assertEqual([h[2] for h in got.halves], ["turn left", "turn left"])


class TheExitSaysItsSide(unittest.TestCase):
    """minion 2's rule E: exit-left / exit-right say the side themselves."""

    def exit(self, *have, instruction="take the exit on the right"):
        return voicelines.playlist(instruction, dict(fired(instruction), kind="exit"),
                                   clips(*have), distances=dists(*have))

    def test_the_sided_line_plays_and_the_phone_does_not_repeat_the_side(self):
        self.assertEqual(self.exit("exit", "exit-right", "dist-400ft"),
                         [D, ("clip", "/voice/exit-right.m4a", "take the exit on the right")])

    def test_without_one_it_is_exit_and_the_side(self):
        self.assertEqual(self.exit("exit", "exit-left", "dist-400ft"),
                         [D, ("clip", "/voice/exit.m4a", "take the exit on the right"),
                          ("tts", "on the right", None)])

    def test_an_exit_with_no_side_is_the_plain_exit(self):
        self.assertEqual(self.exit("exit", "exit-right", "dist-400ft", instruction="take the exit"),
                         [D, ("clip", "/voice/exit.m4a", "take the exit")])

    def test_a_sided_exit_is_a_direction_slot(self):
        self.assertLessEqual(set(voicelines.SIDED), voicelines.DIRECTION_SLOTS)


URGENT = {"flourish-urgent-do-it-now": "/v/now.mp3", "flourish-urgent-move": "/v/move.mp3"}
ANGRY = {"flourish-angry-damn-it": "/v/damn.mp3"}


class ASecondLine(unittest.TestCase):
    """"do it now" + "damn it": a second flourish after the first, sometimes."""

    def test_where_it_may_come_from(self):
        second = voicelines.second_moods
        self.assertEqual(second([], fired("turn left")), ["urgent", "angry"])
        self.assertEqual(second([], fired("turn left"), angry_ok=False), ["urgent"])
        self.assertEqual(second([], fired("turn left", band="now")), [])
        self.assertEqual(second([], fired("turn left", band="far")), [])
        self.assertEqual(second(["offroute", "speeding"], None), ["angry", "scold"])
        self.assertEqual(second([], dict(fired("arrive at your destination"), kind="arrive")), [])

    def test_never_the_first_ones_line_and_at_the_named_rate(self):
        f = voicelines.Flourish(Always())
        first = f.pick("urgent", URGENT, now=0)[0]
        got = f.second({"urgent": URGENT}, first, now=0)
        self.assertNotEqual(got[0], first)
        self.assertIsNone(voicelines.Flourish(Never()).second({"urgent": URGENT}, first, now=0))
        self.assertIsNone(voicelines.Flourish(Always()).second(
            {"urgent": {first: "/v/now.mp3"}}, first, now=0))
        self.assertIsNone(voicelines.Flourish(Always()).second({"urgent": URGENT}, None, now=0))
        self.assertEqual(voicelines.P_SECOND, 0.35)

    def test_an_angry_second_line_waits_out_the_minute(self):
        f = voicelines.Flourish(Always())
        f.pick("angry", {"flourish-angry-no": "/v/no.mp3"}, now=0)
        self.assertIsNone(f.second({"angry": ANGRY}, "flourish-urgent-move", now=30))
        got = f.second({"angry": ANGRY}, "flourish-urgent-move", now=61)
        self.assertEqual(got, ("flourish-angry-damn-it", "/v/damn.mp3"))
        self.assertFalse(f.angry_ok(now=90))                    # it counts, too


def two_turns(gap, step=0.0005, n=41, at=20, second="turn-right", words="turn right"):
    """A straight route north with a turn at point `at` and another `gap`
    points after it, then the arrival."""
    points = [(37.80 + i * step, -122.27) for i in range(n)]
    cum = routing.cumulative_m(points)
    mans = [routing.Maneuver(instruction="head north", street="First St", distance_m=0.0,
                             at=(0.0, 0.0), index=0, cum_m=0.0, kind="start"),
            routing.Maneuver(instruction="turn left", street="Grand Ave", distance_m=0.0,
                             at=(0.0, 0.0), index=at, cum_m=cum[at], kind="turn-left"),
            routing.Maneuver(instruction=f"{words} onto Main St", street="Main St",
                             distance_m=0.0, at=(0.0, 0.0), index=at + gap,
                             cum_m=cum[at + gap], kind=second),
            routing.Maneuver(instruction="arrive at your destination", street="Main St",
                             distance_m=0.0, at=(0.0, 0.0), index=n - 1,
                             cum_m=cum[-1], kind="arrive")]
    route = routing.Route(ok=True, source="offline", points=points, cum_m=cum,
                          maneuvers=mans, distance_m=cum[-1], duration_s=600.0)
    return route, points


def ride_to(route, points, upto, speed):
    """Every prompt fired riding points[:upto], as (say, parts)."""
    follower = guidance.Follower(route, offroute_m=45)
    fired = []
    for point in points[:upto]:
        status = follower.update(point, speed_ms=speed)
        if status.get("say"):
            fired.append((status["say"], status["say_parts"]))
    return fired


class AndThen(unittest.TestCase):
    """Her AndThen.mp3 (minion 1's proposal, AGEDASHII approved; the rules
    agreed with minion 2): a turn too close to get its own warning is said on
    the "now" of the one before it."""

    def now_of_the_first(self, route, points, speed, upto):
        return next(p for _s, p in ride_to(route, points, upto, speed)
                    if p["band"] == "now" and p["kind"] == "turn-left")

    def test_a_turn_too_close_for_its_own_warning_rides_on_the_one_before(self):
        route, points = two_turns(gap=1)                       # 55 m
        fired = ride_to(route, points, 20, 15.0)
        say, parts = fired[-1]
        self.assertEqual(parts["band"], "now")
        self.assertEqual(parts["then"]["kind"], "turn-right")
        self.assertTrue(say.endswith(", and then turn right"), say)
        # Only on "now": a far or near prompt is early enough for the next
        # turn to get its own.
        self.assertTrue(all("then" not in p for _s, p in fired[:-1]), fired)
        self.assertGreater(len(fired), 1)

    def test_a_turn_far_enough_away_gets_its_own(self):
        route, points = two_turns(gap=3)                       # 165 m > 9 s at 15 m/s
        self.assertNotIn("then", self.now_of_the_first(route, points, 15.0, 20))
        route, points = two_turns(gap=2)                       # 110 m < 135 m
        self.assertIn("then", self.now_of_the_first(route, points, 15.0, 20))

    def test_its_own_now_still_fires(self):
        route, points = two_turns(gap=5, step=0.0001, n=140, at=100)    # 55 m
        nows = [(p["kind"], "then" in p) for _s, p in ride_to(route, points, 106, 15.0)
                if p["band"] == "now"]
        self.assertEqual(nows, [("turn-left", True), ("turn-right", False)])

    def test_not_for_an_arrival_a_continue_or_a_merge(self):
        for kind, words in (("continue", "continue straight"), ("merge", "merge")):
            route, points = two_turns(gap=1, second=kind, words=words)
            self.assertNotIn("then", self.now_of_the_first(route, points, 15.0, 20), kind)

    def test_crawling_it_is_sixty_metres(self):
        # 1 m/s is a crawl: the reach is THEN_MIN_M, not 9 s of speed.
        route, points = two_turns(gap=5, step=0.0001, n=140, at=100)    # 55 m
        self.assertIn("then", self.now_of_the_first(route, points, 1.0, 100))
        route, points = two_turns(gap=7, step=0.0001, n=140, at=100)    # 78 m
        self.assertNotIn("then", self.now_of_the_first(route, points, 1.0, 100))

    def test_it_says_and_then_and_the_next_line_whole(self):
        then = {"instruction": "take the exit on the right", "kind": "exit",
                "street": "I 80", "band": "now"}
        self.assertEqual(voicelines.and_then(then, clips("and-then", "exit-right")),
                         [("clip", "/voice/and-then.m4a", "and then"),
                          ("clip", "/voice/exit-right.m4a", "take the exit on the right")])
        self.assertEqual(voicelines.and_then(then, clips("exit")),
                         [("tts", "and then", None),
                          ("clip", "/voice/exit.m4a", "take the exit on the right"),
                          ("tts", "on the right", None)])
        leave = {"instruction": "exit the roundabout", "kind": None, "band": "now"}
        self.assertEqual(voicelines.and_then(leave, clips("and-then")),
                         [("clip", "/voice/and-then.m4a", "and then"),
                          ("tts", "exit the roundabout", None)])


def lines(prefix, n):
    """n different phrases - letters, since a trailing number is a take."""
    return {f"{prefix}-{chr(97 + i)}x": f"/v/{prefix}-{chr(97 + i)}x.mp3" for i in range(n)}


def windows(seq, size):
    return [seq[i:i + size] for i in range(len(seq) - size + 1)]


class TheLastFewAreHeldBack(unittest.TestCase):
    """She heard the same few lines (AGEDASHII, relayed): each pool holds back
    its last k = min(4, n // 2) picks. Agreed with minion 2."""

    def test_how_many_are_held_back(self):
        # With every phrase but the held ones used up, a pool of n holds back
        # exactly its last k: the next k + 1 picks are all different.
        for n, k in ((2, 1), (4, 2), (9, 4), (20, 4)):
            recent = voicelines.Recent()
            names = sorted(lines(f"p{n}", n))
            said = [recent.choose(Always(), n, names)[0] for _ in range(3 * n)]
            for window in windows(said, k + 1):
                self.assertEqual(len(set(window)), k + 1, (n, said))
            self.assertIn(names[0], said[k + 1:], "held back for longer than k")

    def test_a_mood_of_nine_never_repeats_inside_five(self):
        f = voicelines.Flourish(Always())
        pool = lines("flourish-angry", 9)
        said = [f.pick("angry", pool)[0] for _ in range(20)]
        for window in windows(said, 5):
            self.assertEqual(len(set(window)), 5, said)

    def test_each_mood_remembers_its_own(self):
        # One memory for every mood let a scold line in between clear the
        # urgent one, and the same urgent line came straight back.
        f = voicelines.Flourish(Always())
        urgent = {"flourish-urgent-a": "/v/a.mp3", "flourish-urgent-b": "/v/b.mp3"}
        said = [f.pick("urgent", urgent)[0],
                f.pick("scold", {"flourish-scold-z": "/v/z.mp3"})[0],
                f.pick("urgent", urgent)[0]]
        self.assertEqual(said, ["flourish-urgent-a", "flourish-scold-z", "flourish-urgent-b"])

    def test_a_slot_of_four_takes_holds_back_two(self):
        w = voicelines.Wild(Always())
        takes = lines("wild-offroute", 4)
        said = [w.resolve("offroute", source=lambda n: "built in",
                          clip_path=lambda n: f"/v/{n}.mp3",
                          wilds=lambda n: takes) for _ in range(12)]
        for window in windows(said, 3):
            self.assertEqual(len(set(window)), 3, said)
        self.assertNotIn("/v/offroute.mp3", said)              # the Waze line: none

    def test_a_second_line_falls_back_to_a_held_one_rather_than_none(self):
        # Kotlin's rule: if F1 and the memory leave nothing, drop the memory
        # (F1 stays excluded).
        f = voicelines.Flourish(Always())
        f.recent.heard("urgent", "flourish-urgent-b")
        pool = {"flourish-urgent-a": "/v/a.mp3", "flourish-urgent-b": "/v/b.mp3"}
        self.assertEqual(f.second({"urgent": pool}, "flourish-urgent-a", now=0),
                         ("flourish-urgent-b", "/v/b.mp3"))

    def test_a_lone_take_and_the_waze_line_take_turns(self):
        w = voicelines.Wild(Always())
        said = [w.resolve("start", source=lambda n: "built in",
                          clip_path=lambda n: f"/v/{n}.mp3",
                          wilds=lambda n: {"wild-start-come": "/v/come.mp3"})
                for _ in range(4)]
        self.assertEqual(said, ["/v/come.mp3", "/v/start.mp3"] * 2)

    def cough_run(self, coughs):
        a = voicelines.Arranger(Script(0.9, 0.0, 0.9, 0.0, 0.9, 0.0))
        heard = []
        for band in ("far", "near", "far"):
            a.coughed = False                     # as if a prompt came between
            got = a.arrange(piece(band=band), band, coughs=coughs)
            heard += [i[1] for i in got if i[0] == "flourish"]
        return heard

    def test_two_cough_phrases_take_turns(self):
        self.assertEqual(self.cough_run({"interject-cough": "/v/c.mp3",
                                         "interject-sneeze": "/v/s.mp3"}),
                         ["/v/c.mp3", "/v/s.mp3", "/v/c.mp3"])

    def test_two_takes_of_the_cough_are_one_phrase(self):
        # One phrase: nothing held back, so the same take may come again.
        self.assertEqual(self.cough_run({"interject-cough": "/v/c1.mp3",
                                         "interject-cough-2": "/v/c2.mp3"}),
                         ["/v/c1.mp3"] * 3)

    def test_takes_of_one_phrase_are_one_line(self):
        f = voicelines.Flourish(Always())
        pool = {"flourish-angry-what-the-hell-4": "/v/h4.mp3",
                "flourish-angry-what-the-hell-6": "/v/h6.mp3",
                "flourish-angry-what-the-hell-7": "/v/h7.mp3",
                "flourish-angry-x-mistake": "/v/m.mp3",
                "flourish-angry-x-oops": "/v/o.mp3"}
        import re
        said = [re.sub(r"-[0-9]+$", "", f.pick("angry", pool)[0]) for _ in range(9)]
        for window in windows(said, 2):
            self.assertEqual(len(set(window)), 2, said)

    def test_the_phrase_is_picked_first_then_the_take(self):
        import random as r
        rng = r.Random(7)
        names = ["wild-offroute-what-1", "wild-offroute-what-2", "wild-offroute-what-3",
                 "wild-offroute-mistake"]
        picks = [voicelines.Recent().choose(rng, "offroute", names)[0] for _ in range(4000)]
        share = sum(p == "wild-offroute-mistake" for p in picks) / len(picks)
        self.assertAlmostEqual(share, 0.5, delta=0.04)          # not 1 in 4

    def test_the_second_line_is_never_the_first_ones_phrase(self):
        f = voicelines.Flourish(Always())
        pool = {"flourish-urgent-now-1": "/v/n1.mp3", "flourish-urgent-now-2": "/v/n2.mp3",
                "flourish-urgent-quickly": "/v/q.mp3"}
        got = f.second({"urgent": pool}, "flourish-urgent-now-1", now=0)
        self.assertEqual(got, ("flourish-urgent-quickly", "/v/q.mp3"))


class ThePeek(unittest.TestCase):
    """Follower.peek: the band a fresh route's first prompt will be said in,
    without saying it - for the held situation line."""

    def test_the_band_it_will_say_and_nothing_marked(self):
        route, points = two_turns(gap=10, step=0.0001, n=140, at=100)
        follower = guidance.Follower(route, offroute_m=45)
        self.assertEqual(follower.peek(points[95], 15.0), "now")      # 55 m
        self.assertEqual(follower.peek(points[80], 15.0), "near")     # 220 m
        self.assertIsNone(follower.peek(points[0], 15.0))             # 1.1 km
        self.assertEqual(follower.update(points[95], speed_ms=15.0)["say_parts"]["band"], "now")

    def test_a_near_prompt_held_back_for_now_counts_as_now(self):
        # 100 m at 15 m/s: "near" is due, but "now" (90 m) is under a second
        # away, so the Follower waits and says "now".
        route, points = two_turns(gap=10, step=0.0001, n=140, at=100)
        follower = guidance.Follower(route, offroute_m=45)
        self.assertEqual(follower.peek(points[91], 15.0), "now")


class HisWildLinesLead(unittest.TestCase):
    """Hers: "id rather him scream things that make kind of sense than the
    normal waze arnold talking. only use that shit as fall back". Per slot: her
    import, else a wild line, else the Waze built-in, else the phone."""

    WILD = {"wild-arrive-choppa": "/v/choppa.mp3", "wild-arrive-game-over": "/v/over.mp3"}

    def resolve(self, wild, name, *, source=None, wilds=None, character=True, band="now"):
        return wild.resolve(
            name, source=source or (lambda n: "built in"),
            clip_path=lambda n: f"/v/{n}.mp3",
            wilds=wilds if wilds is not None else (lambda n: self.WILD if n == "arrive" else {}),
            character=character, band=band)

    def test_a_wild_line_leads_the_waze_one_falls_back(self):
        w = voicelines.Wild(Always())
        self.assertEqual(self.resolve(w, "arrive"), "/v/choppa.mp3")
        self.assertEqual(self.resolve(w, "offroute"), "/v/offroute.mp3")   # no wild: Waze

    def test_never_the_same_one_twice_running(self):
        w = voicelines.Wild(Always())
        said = [self.resolve(w, "arrive") for _ in range(3)]
        self.assertNotEqual(said[0], said[1])
        self.assertNotEqual(said[1], said[2])
        lone = voicelines.Wild(Always())
        one = lambda n: {"wild-start-come-with-me": "/v/come.mp3"} if n == "start" else {}
        said = [self.resolve(lone, "start", wilds=one) for _ in range(3)]
        self.assertEqual(said, ["/v/come.mp3", "/v/start.mp3", "/v/come.mp3"])

    def test_her_own_recording_always_wins(self):
        w = voicelines.Wild(Always())
        self.assertEqual(self.resolve(w, "arrive", source=lambda n: "yours"), "/v/arrive.mp3")

    def test_character_off_is_the_plain_voice(self):
        w = voicelines.Wild(Always())
        self.assertEqual(self.resolve(w, "arrive", character=False), "/v/arrive.mp3")

    def test_the_arrive_wild_lines_wait_for_arriving(self):
        # AGEDASHII: on arrive's far and near prompts, the plain arrive line;
        # the wild one ("GET TO THE CHOPPA") is for the moment she arrives.
        w = voicelines.Wild(Always())
        self.assertEqual(self.resolve(w, "arrive", band="near"), "/v/arrive.mp3")
        self.assertEqual(self.resolve(w, "arrive", band="now"), "/v/choppa.mp3")

    def test_a_direction_wild_line_plays_only_when_its_name_says_so(self):
        # AGEDASHII's ruling: WE never pick a direction line from a pool - that
        # would be guessing whether a clip is unmistakable about left or right.
        # A file SHE names wild-turn-left-... is her deliberate act ("GO LEFT
        # IF YOU WANT TO LIVE" were her first words on this feature).
        w = voicelines.Wild(Always())
        live_ = "wild-turn-left-go-left-if-you-want-to-live"
        hers = {live_: "/v/live.mp3"}
        mine = lambda n: "yours" if n == live_ else "built in"
        self.assertEqual(self.resolve(w, "turn-left", band="near", source=mine,
                                      wilds=lambda n: hers if n == "turn-left" else {}),
                         "/v/live.mp3")
        # The same file shipped BUILT IN is not used: never ours to pick.
        self.assertEqual(self.resolve(voicelines.Wild(Always()), "turn-left", band="near",
                                      wilds=lambda n: hers if n == "turn-left" else {}),
                         "/v/turn-left.mp3")
        # A file not named for THIS slot is never used for it, whatever hands it
        # over - checked on a slot with no direction gate, where only the name
        # can stop it.
        self.assertEqual(self.resolve(voicelines.Wild(Always()), "offroute",
                                      wilds=lambda n: {"wild-arrive-choppa": "/v/c.mp3"}),
                         "/v/offroute.mp3")
        stray = {"wild-turn-right-x": "/v/right.mp3", "wild-arrive-choppa": "/v/c.mp3"}
        for turn in ("turn-left", "slight-left", "sharp-left", "uturn"):
            self.assertEqual(self.resolve(w, turn, band="near", wilds=lambda n: stray),
                             f"/v/{turn}.mp3", turn)


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

    def test_a_character_line_is_the_first_thing_cut(self):
        # minion 2: any newer prompt stops a flourish at once - it can never
        # be the thing still playing when a turn needs saying.
        self.engine.seconds = 2.0
        speech.play([("flourish", "/voice/flourish-urgent-do-it-now.mp3", None)])
        self.assertTrue(wait_for(lambda: self.engine.heard))
        t0 = time.time()
        speech.play([("tts", "In 1 mile, turn left", None)])      # not urgent
        self.assertTrue(wait_for(lambda: len(self.engine.heard) == 2))
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(self.engine.heard[0], ("clip", "flourish-urgent-do-it-now.mp3"))

    def test_a_broken_character_line_is_skipped_silently(self):
        speech.play([("tts", "turn left", None), ("flourish", "/voice/broken.m4a", None)])
        self.assertTrue(wait_for(lambda: speech.STATE["spoken"] == 1))
        self.assertEqual(self.engine.heard, [("tts", "turn left")])

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
        ride._M.pop("held", None)                          # noqa: SLF001
        self.addCleanup(ride._M.pop, "held", None)         # noqa: SLF001
        self.played = self.capture_play()
        # Distance first, no cough - unless a test says otherwise.
        from agedashii_navi import character
        arrange = mock.patch.object(character, "ARRANGE", voicelines.Arranger(Never()))
        arrange.start()
        self.addCleanup(arrange.stop)

    def announce(self, status=None, say="", parts=None, reroute=False):
        out = {"moved": True, "status": status, "say": say, "say_parts": parts,
               "reroute": reroute}
        return self.ride._announce(out, None)              # noqa: SLF001

    def answer(self, band=None, ok=True):
        """The re-route answers; its first prompt would be in `band`."""
        from agedashii_washi import routing as R
        route = (R.Route(ok=True, source="offline") if ok else
                 R.Route(ok=False, source="offline", reason=R.START_OUTSIDE))
        follower = mock.Mock()
        follower.peek.return_value = band
        live.LIVE["follower"] = follower
        return self.ride._release(route)                   # noqa: SLF001

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

    def test_sorry_follows_the_spoken_refusal_and_needs_a_voice(self):
        from agedashii_washi import routing as R
        self.flourishes({"sorry": {"flourish-sorry-my-bad": "/v/sorry.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.played.clear()
        speech.STATE["ready"] = True
        self.ride._refused(R.Route(ok=False, source="offline", reason=R.START_OUTSIDE))  # noqa: SLF001
        self.assertEqual(self.played[0][0][0], "tts")                    # the words first
        self.assertEqual(self.played[0][-1], ("flourish", "/v/sorry.mp3", None))
        # No voice: the words cannot be said, so neither is the apology.
        self.ride._M.pop("cues", None)                                   # noqa: SLF001
        self.announce({"ok": True, "off_route": False})
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.played.clear()
        speech.STATE["ready"] = False
        self.ride._refused(R.Route(ok=False, source="offline", reason=R.START_OUTSIDE))  # noqa: SLF001
        self.assertNotIn("/v/sorry.mp3", repr(self.played))

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

    def flourishes(self, pools, coughs=None):
        for attr, fake in (("flourishes", lambda mood, *a, **k: dict(pools.get(mood, {}))),
                           ("interjections", lambda *a, **k: dict(coughs or {}))):
            real = getattr(voice_import, attr, None)
            setattr(voice_import, attr, fake)
            self.addCleanup(lambda a=attr, r=real: setattr(voice_import, a, r)
                            if r else delattr(voice_import, a))
        from agedashii_navi import character
        patcher = mock.patch.object(character, "FLOURISH", voicelines.Flourish(Always()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_character_line_goes_after_the_whole_instruction(self):
        turn, far = self.add("turn-left"), self.add("dist-400ft")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("clip", turn, "turn left"),
                                        ("flourish", "/v/now.mp3", None)]])

    def test_a_ride_prompt_can_cough_between_distance_and_turn(self):
        from agedashii_navi import character
        turn, far = self.add("turn-left"), self.add("dist-400ft")
        self.flourishes({}, coughs={"interject-cough": "/v/cough.mp3"})
        arrange = mock.patch.object(character, "ARRANGE", voicelines.Arranger(Script(0.9, 0.0)))
        arrange.start()
        self.addCleanup(arrange.stop)
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("flourish", "/v/cough.mp3", None),
                                        ("clip", turn, "turn left")]])

    def test_a_near_turn_can_get_two_of_his_lines(self):
        turn, far = self.add("turn-left"), self.add("dist-400ft")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3"},
                         "angry": {"flourish-angry-damn-it": "/v/damn.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False},
                      "In 400 ft, turn left onto Grand Ave", fired("turn left"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("clip", turn, "turn left"),
                                        ("flourish", "/v/now.mp3", None),
                                        ("flourish", "/v/damn.mp3", None)]])

    def test_the_corner_gets_one_line_at_most(self):
        turn = self.add("turn-left")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3",
                                    "flourish-urgent-move": "/v/move.mp3"},
                         "angry": {"flourish-angry-damn-it": "/v/damn.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False}, "turn left onto Grand Ave",
                      fired("turn left", band="now"))
        self.assertEqual(self.played, [[("clip", turn, "turn left"),
                                        ("flourish", "/v/now.mp3", None)]])

    def test_a_ride_prompt_coughs_inside_a_line_in_halves(self):
        from agedashii_navi import character
        turn, far = self.built_in("continue"), self.built_in("dist-400ft")
        a, b = self.built_in("part-continue-a"), self.built_in("part-continue-b")
        self.flourishes({}, coughs={"interject-cough": "/v/cough.mp3"})
        arrange = mock.patch.object(character, "ARRANGE", voicelines.Arranger(Script(0.9, 0.0)))
        arrange.start()
        self.addCleanup(arrange.stop)
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False}, "In 400 ft, continue straight",
                      dict(fired("continue straight"), kind="continue"))
        self.assertEqual(self.played, [[("clip", far, "In 400 ft,"),
                                        ("clip", a, "continue straight"),
                                        ("flourish", "/v/cough.mp3", None),
                                        ("clip", b, "continue straight")]])

    def test_and_then_rides_on_the_corner_with_no_line_of_his(self):
        left, right, joint = self.add("turn-left"), self.add("turn-right"), self.add("and-then")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3"}})
        prefs.set_value("voice", True)
        parts = dict(fired("turn left", band="now"), kind="turn-left",
                     then={"instruction": "turn right onto Main St", "kind": "turn-right",
                           "band": "now"})
        self.announce({"ok": True, "off_route": False},
                      "turn left onto Grand Ave, and then turn right", parts)
        self.assertEqual(self.played, [[("clip", left, "turn left"),
                                        ("clip", joint, "and then"),
                                        ("clip", right, "turn right")]])
        self.assertEqual(self.urgent, [True])

    def test_no_urgent_line_on_the_far_prompt(self):
        self.add("turn-left")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False}, "In 0.5 mi, turn left",
                      fired("turn left", band="far", metres=800.0))
        self.assertNotIn("/v/now.mp3", repr(self.played))

    def test_the_switch_turns_the_theatre_off_and_keeps_the_voice(self):
        turn = self.add("turn-left")
        self.flourishes({"urgent": {"flourish-urgent-do-it-now": "/v/now.mp3"}})
        prefs.set_value("voice", True)
        self.assertTrue(prefs.flourishes())                     # on by default
        prefs.set_value("flourishes", False)
        # A far prompt, where one WOULD be added (the rng always says yes).
        self.announce({"ok": True, "off_route": False}, "In 400 ft, turn left",
                      fired("turn left"))
        self.assertEqual(self.played, [[("tts", "In 400 ft,", None),
                                        ("clip", turn, "turn left")]])

    def test_an_angry_one_after_off_route(self):
        self.flourishes({"angry": {"flourish-angry-big-mistake": "/v/mistake.mp3"}})
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.answer("far")
        self.assertEqual(self.played, [[("tts", "Off route.", None),
                                        ("tts", "Re-routing.", None),
                                        ("flourish", "/v/mistake.mp3", None)]])

    def test_arriving_plays_one_of_his_wild_lines(self):
        from agedashii_navi import character
        self.built_in("arrive")
        choppa = self.built_in("wild-arrive-choppa")
        wild = mock.patch.object(character, "WILD", voicelines.Wild(Always()))
        wild.start()
        self.addCleanup(wild.stop)
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": False}, "arrive at your destination",
                      dict(fired("arrive at your destination", band="now"), kind="arrive"))
        self.assertEqual(self.played[0][0], ("clip", choppa, "arrive at your destination"))

    def test_a_tap_gets_a_line_of_his_unless_a_prompt_is_playing(self):
        from agedashii_navi import character
        # Two lines, so "never the same twice running" cannot be what stops
        # the second and third taps below - only the guard under test can.
        self.flourishes({"ui": {"flourish-ui-what-do-you-want": "/v/want.mp3",
                                "flourish-ui-ok": "/v/ok.mp3"}})
        patcher = mock.patch.object(character, "FLOURISH", voicelines.Flourish(Always()))
        patcher.start()
        self.addCleanup(patcher.stop)
        prefs.set_value("voice", True)
        self.assertEqual(character.tap(), [("flourish", "/v/ok.mp3", None)])
        with mock.patch.object(speech, "voice_active", return_value=True):
            self.assertEqual(character.tap(), [])           # never over a prompt
        prefs.set_value("flourish_taps", False)
        self.assertEqual(character.tap(), [])

    def open_clean(self):
        from agedashii_navi import character
        character._OPENED.clear()                           # noqa: SLF001
        self.addCleanup(character._OPENED.clear)            # noqa: SLF001
        wild = mock.patch.object(character, "WILD", voicelines.Wild(Always()))
        wild.start()
        self.addCleanup(wild.stop)
        return character

    def test_her_launch_line_plays_once_per_launch(self):
        character = self.open_clean()
        self.built_in("open")
        prefs.set_value("voice", True)
        self.assertEqual(len(character.opening()), 1)
        self.assertEqual(character.opening(), [])            # not on a repaint

    def test_the_launch_line_is_a_slot_not_theatre(self):
        # Character lines off: her open line still plays; the wild takes do not.
        character = self.open_clean()
        plain = self.built_in("open")
        self.built_in("wild-open-hello")
        prefs.set_value("voice", True)
        prefs.set_value("flourishes", False)
        self.assertEqual(character.opening(), [("clip", plain, None)])

    def test_no_clip_or_no_voice_is_silence(self):
        character = self.open_clean()
        prefs.set_value("voice", True)
        self.assertEqual(character.opening(), [])            # no clip: no robot greeting
        character._OPENED.clear()                           # noqa: SLF001
        self.built_in("open")
        prefs.set_value("voice", False)
        self.assertEqual(character.opening(), [])

    def test_the_launch_line_never_talks_over_a_prompt(self):
        character = self.open_clean()
        self.built_in("open")
        prefs.set_value("voice", True)
        with mock.patch.object(speech, "voice_active", return_value=True):
            self.assertEqual(character.opening(), [])

    def test_off_route_and_rerouting_go_as_one_playlist(self):
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.answer(None)
        self.assertEqual(self.played, [[("tts", "Off route.", None),
                                        ("tts", "Re-routing.", None)]])

    def test_the_line_waits_for_the_reroute_to_answer(self):
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.assertEqual(self.played, [])                         # held
        self.answer("near")
        self.assertEqual(len(self.played), 1)

    def test_it_is_dropped_when_the_new_route_starts_with_a_turn(self):
        # 5ca2f16 on 5554: "what the h-" cut after 0.3 s by the turn.
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.assertEqual(self.answer("now"), [])
        self.assertEqual(self.played, [])
        self.assertNotIn("held", self.ride._M)                   # noqa: SLF001

    def test_refused_it_is_the_line_then_the_refusal_as_one(self):
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        self.answer(ok=False)
        self.assertEqual(len(self.played), 1)
        words = [v for _k, v, _f in self.played[0]]
        self.assertEqual(words[:2], ["Off route.", "Re-routing."])
        self.assertIn("outside", words[2])

    def test_with_no_answer_it_plays_after_the_hold(self):
        import asyncio
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=True)
        with mock.patch.object(self.ride, "HOLD_S", 0.0):
            asyncio.run(self.ride._let_go(self.ride._M["held"]["id"]))  # noqa: SLF001
        self.assertEqual(len(self.played), 1)
        self.assertEqual(self.answer("now"), [])              # nothing left to drop
        self.assertEqual(len(self.played), 1)

    def test_a_line_with_no_reroute_on_its_fix_plays_at_once(self):
        prefs.set_value("voice", True)
        self.announce({"ok": True, "off_route": True}, reroute=False)
        self.assertEqual(self.played, [[("tts", "Off route.", None)]])

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

    def test_the_character_lines_switch_is_there_and_on(self):
        from tests.test_road_test import holder_of, texts
        built = self.build()
        row = holder_of(built, "Character lines")
        self.assertIsNotNone(row, texts(built))
        switch = next(c for c in __import__("tests.test_road_test", fromlist=["walk"]).walk(row)
                      if isinstance(c, ft.Switch))
        self.assertTrue(switch.value)

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
