"""Her recorded voice: from files she picks on the phone to clips Navi plays.

No adb and no cable. She moves the clips to the phone however she likes - a
ZIP in Drive, Quick Share into Download, an email attachment - and picks them
from the phone's own file picker inside Navi. These pin what happens to the
files after the picker hands them over; the picker itself is faked, because it
is the phone's (Android's DocumentsUI) and a test cannot open it.

`FLET_APP_STORAGE_DATA` points at a temporary directory for each test and is
put back after it, so nothing here touches a real voice - and nothing here
moves another module's storage during a discover run (test_shell sets its own
at import).
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from agedashii_navi import voice_import as vi                # noqa: E402

_TMP = tempfile.TemporaryDirectory()
_REAL_BUILTIN_DIR = getattr(vi, "builtin_dir", None)


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


def _write(folder, name, data=b"ID3fake-audio"):
    path = os.path.join(folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class _Case(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FLET_APP_STORAGE_DATA": _TMP.name})
        env.start()
        self.addCleanup(env.stop)
        # No bundled voice unless a test makes one: the dev tree and the web
        # build have none, and these must behave exactly as before there.
        self.builtin = tempfile.mkdtemp(dir=_TMP.name, prefix="apk-voice-")
        os.rmdir(self.builtin)
        bundled = mock.patch.object(vi, "builtin_dir", return_value=self.builtin)
        bundled.start()
        self.addCleanup(bundled.stop)
        self.src = tempfile.mkdtemp(dir=_TMP.name, prefix="laptop-")
        vi.clear_voice()
        self.addCleanup(vi.clear_voice)            # runs before env.stop

    def bundle(self, *names):
        """Put clips where the APK's bundled voice would be."""
        os.makedirs(self.builtin, exist_ok=True)
        return [_write(self.builtin, n, b"arnold:" + n.encode()) for n in names]


class WhereTheVoiceLives(_Case):
    def test_it_is_the_apps_own_storage_under_voice(self):
        self.assertEqual(vi.voice_dir(), os.path.join(_TMP.name, "voice"))

    def test_the_names_are_the_twenty_three_fixed_ones_and_the_optional_five(self):
        self.assertEqual(len(vi.NAMES), 28)
        self.assertEqual(vi.NAMES[-4:], ("open", "exit-left", "exit-right", "and-then"),
                         "appended, so no index moves")
        self.assertEqual(vi.OPTIONAL, frozenset({"roundabout", "open", "exit-left",
                                                 "exit-right", "and-then"}))
        for name in ("turn-left", "dist-quarter", "dist-2miles", "offroute",
                     "rerouting", "start", "speeding", "uturn"):
            self.assertIn(name, vi.NAMES)

    def test_no_clip_before_anything_is_imported(self):
        self.assertIsNone(vi.clip_path("turn-left"))

    def test_an_unknown_name_is_none_not_an_error(self):
        self.assertIsNone(vi.clip_path("turn-leftish"))

    def test_clip_path_is_cheap_enough_to_call_at_every_prompt(self):
        vi.import_files([_write(self.src, "arrive.wav")])
        started = time.perf_counter()
        for _ in range(2000):
            vi.clip_path("turn-left")
            vi.clip_path("arrive")
        per_call = (time.perf_counter() - started) / 4000
        self.assertLess(per_call, 0.001)


class ImportingFiles(_Case):
    def test_a_known_name_lands_where_clip_path_finds_it(self):
        result = vi.import_files([_write(self.src, "turn-left.mp3", b"left")])
        self.assertEqual(result["imported"], ["turn-left"])
        path = vi.clip_path("turn-left")
        self.assertEqual(path, os.path.join(vi.voice_dir(), "turn-left.mp3"))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"left")

    def test_every_accepted_extension_imports(self):
        exts = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"]
        names = vi.NAMES[:len(exts)]
        paths = [_write(self.src, n + e) for n, e in zip(names, exts)]
        result = vi.import_files(paths)
        self.assertEqual(sorted(result["imported"]), sorted(names))
        for n, e in zip(names, exts):
            self.assertTrue(vi.clip_path(n).endswith(n + e))

    def test_case_and_underscores_are_forgiven(self):
        result = vi.import_files([_write(self.src, "Turn_Left.MP3"),
                                  _write(self.src, "DIST 2MILES.wav")])
        self.assertEqual(sorted(result["imported"]), ["dist-2miles", "turn-left"])
        self.assertTrue(vi.clip_path("turn-left").endswith("turn-left.mp3"))

    def test_a_file_that_is_not_one_of_the_names_is_left_out_and_said(self):
        result = vi.import_files([_write(self.src, "song.mp3"),
                                  _write(self.src, "notes.txt"),
                                  _write(self.src, "arrive.mp3")])
        self.assertEqual(result["imported"], ["arrive"])
        self.assertEqual(sorted(result["ignored"]), ["notes.txt", "song.mp3"])
        self.assertIn("song.mp3", vi.summary(result))

    def test_an_empty_file_is_refused(self):
        result = vi.import_files([_write(self.src, "arrive.mp3", b"")])
        self.assertEqual(result["imported"], [])
        self.assertIsNone(vi.clip_path("arrive"))
        self.assertIn("arrive.mp3", vi.summary(result))

    def test_a_file_too_big_to_be_a_prompt_is_refused(self):
        big = _write(self.src, "arrive.wav", b"\0" * (vi.MAX_CLIP_BYTES + 1))
        result = vi.import_files([big])
        self.assertEqual(result["imported"], [])
        self.assertIsNone(vi.clip_path("arrive"))

    def test_a_file_that_cannot_be_read_keeps_the_old_clip(self):
        vi.import_files([_write(self.src, "arrive.mp3", b"old")])
        result = vi.import_files([os.path.join(self.src, "gone", "arrive.wav")])
        self.assertEqual(result["imported"], [])
        self.assertTrue(result["failed"])
        with open(vi.clip_path("arrive"), "rb") as fh:
            self.assertEqual(fh.read(), b"old")

    def test_a_file_that_grows_after_it_was_measured_is_still_refused(self):
        big = _write(self.src, "arrive.wav", b"\0" * (vi.MAX_CLIP_BYTES + 1))
        with mock.patch.object(vi.os.path, "getsize", return_value=10):
            result = vi.import_files([big])
        self.assertEqual(result["imported"], [])
        self.assertIsNone(vi.clip_path("arrive"))
        self.assertEqual(os.listdir(vi.voice_dir()), [])

    def test_the_source_file_is_never_touched(self):
        src = _write(self.src, "arrive.mp3", b"keep me")
        vi.import_files([src])
        vi.clear_voice()
        with open(src, "rb") as fh:
            self.assertEqual(fh.read(), b"keep me")


class ReplacingAVoice(_Case):
    def test_importing_again_replaces_that_clip_only(self):
        vi.import_files([_write(self.src, "turn-left.mp3", b"one"),
                         _write(self.src, "turn-right.mp3", b"one")])
        result = vi.import_files([_write(self.src, "turn-left.mp3", b"two")])
        self.assertEqual(result["replaced"], ["turn-left"])
        with open(vi.clip_path("turn-left"), "rb") as fh:
            self.assertEqual(fh.read(), b"two")
        with open(vi.clip_path("turn-right"), "rb") as fh:
            self.assertEqual(fh.read(), b"one")

    def test_a_new_format_does_not_leave_the_old_one_behind(self):
        vi.import_files([_write(self.src, "arrive.mp3", b"old")])
        vi.import_files([_write(self.src, "arrive.wav", b"new")])
        self.assertTrue(vi.clip_path("arrive").endswith("arrive.wav"))
        self.assertEqual(sorted(os.listdir(vi.voice_dir())), ["arrive.wav"])
        self.assertEqual(vi.status()["duplicates"], {})

    def test_remove_voice_clears_every_clip(self):
        vi.import_files([_write(self.src, n + ".mp3") for n in vi.NAMES])
        removed = vi.clear_voice()
        self.assertEqual(removed, len(vi.NAMES))
        self.assertTrue(all(vi.clip_path(n) is None for n in vi.NAMES))
        self.assertEqual(vi.status()["present"], [])

    def test_remove_voice_with_nothing_there_is_fine(self):
        self.assertEqual(vi.clear_voice(), 0)


class TwoFilesForOneName(_Case):
    def test_one_is_picked_the_same_way_every_time_and_it_is_said(self):
        result = vi.import_files([_write(self.src, "arrive.wav", b"w"),
                                  _write(self.src, "arrive.mp3", b"m")])
        self.assertEqual(result["imported"], ["arrive"])
        self.assertEqual(result["duplicates"], {"arrive": ["arrive.mp3", "arrive.wav"]})
        self.assertTrue(vi.clip_path("arrive").endswith("arrive.mp3"))
        self.assertIn("arrive", vi.summary(result))

    def test_status_reports_two_files_left_in_the_folder_some_other_way(self):
        folder = vi.voice_dir()
        _write(folder, "arrive.wav")
        _write(folder, "arrive.mp3")
        st = vi.status()
        self.assertEqual(st["duplicates"], {"arrive": ["arrive.mp3", "arrive.wav"]})
        self.assertTrue(vi.clip_path("arrive").endswith("arrive.mp3"))
        self.assertIn("arrive", vi.status_text())


class ImportingAZip(_Case):
    def _zip(self, entries, name="voice.zip"):
        path = os.path.join(self.src, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc, data in entries:
                zf.writestr(arc, data)
        return path

    def test_a_zip_of_all_of_them_is_one_pick(self):
        path = self._zip([(n + ".mp3", b"x" + n.encode()) for n in vi.NAMES])
        result = vi.import_files([path])
        self.assertEqual(sorted(result["imported"]), sorted(vi.NAMES))
        self.assertEqual(vi.status()["missing"], [])

    def test_folders_inside_the_zip_do_not_matter(self):
        path = self._zip([("my voice/prompts/turn-left.mp3", b"l"),
                          ("my voice/arrive.wav", b"a")])
        result = vi.import_files([path])
        self.assertEqual(sorted(result["imported"]), ["arrive", "turn-left"])

    def test_mac_debris_is_not_a_clip(self):
        path = self._zip([("__MACOSX/voice/._turn-left.mp3", b"junk"),
                          ("voice/._arrive.mp3", b"junk"),
                          (".DS_Store", b"junk"),
                          ("voice/turn-left.mp3", b"real")])
        result = vi.import_files([path])
        self.assertEqual(result["imported"], ["turn-left"])
        self.assertEqual(result["ignored"], [])
        with open(vi.clip_path("turn-left"), "rb") as fh:
            self.assertEqual(fh.read(), b"real")

    def test_a_path_that_climbs_out_stays_in_the_voice_folder(self):
        path = self._zip([("../../../arrive.mp3", b"a"),
                          ("/abs/turn-left.mp3", b"l")])
        result = vi.import_files([path])
        self.assertEqual(sorted(result["imported"]), ["arrive", "turn-left"])
        self.assertEqual(sorted(os.listdir(vi.voice_dir())),
                         ["arrive.mp3", "turn-left.mp3"])
        self.assertFalse(os.path.exists(os.path.join(_TMP.name, "arrive.mp3")))

    def test_an_entry_too_big_to_be_a_prompt_is_refused(self):
        path = self._zip([("arrive.wav", b"\0" * (vi.MAX_CLIP_BYTES + 1)),
                          ("start.mp3", b"s")])
        result = vi.import_files([path])
        self.assertEqual(result["imported"], ["start"])
        self.assertIsNone(vi.clip_path("arrive"))

    def test_a_broken_zip_is_said_not_raised(self):
        vi.import_files([_write(self.src, "arrive.mp3", b"old")])
        bad = _write(self.src, "voice.zip", b"PK this is not a zip")
        result = vi.import_files([bad])
        self.assertEqual(result["imported"], [])
        self.assertTrue(result["failed"])
        self.assertIn("voice.zip", vi.summary(result))
        self.assertIsNotNone(vi.clip_path("arrive"))

    def test_a_clip_that_breaks_halfway_leaves_the_old_one_playable(self):
        vi.import_files([_write(self.src, "arrive.mp3", b"old")])
        path = os.path.join(self.src, "voice.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("arrive.mp3", b"new-data" * 100)
        with open(path, "rb") as fh:
            raw = fh.read()
        with open(path, "wb") as fh:                 # same size, wrong CRC
            fh.write(raw.replace(b"new-data", b"NEW-data", 1))
        result = vi.import_files([path])
        self.assertEqual(result["imported"], [])
        self.assertEqual([label for label, _ in result["failed"]], ["arrive.mp3"])
        with open(vi.clip_path("arrive"), "rb") as fh:
            self.assertEqual(fh.read(), b"old")
        self.assertEqual(os.listdir(vi.voice_dir()), ["arrive.mp3"])

    def test_zips_and_loose_files_in_one_pick(self):
        path = self._zip([("turn-left.mp3", b"l")])
        result = vi.import_files([path, _write(self.src, "arrive.mp3")])
        self.assertEqual(sorted(result["imported"]), ["arrive", "turn-left"])


class DistancesAreOpenEnded(_Case):
    """Her Arnold ZIP says 400, 800 and 1500 feet, not the eight we listed.

    A distance clip is any dist-<N>ft or dist-<N>mile(s); the player rounds to
    the nearest one she has.
    """

    def _zip(self, names):
        path = os.path.join(self.src, "arnold.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for n in names:
                zf.writestr("arnold/" + n, b"x" + n.encode())
        return path

    def test_her_arnold_distances_import(self):
        result = vi.import_files([self._zip(
            ["dist-200ft.mp3", "dist-400ft.mp3", "dist-800ft.mp3", "dist-1500ft.mp3"])])
        self.assertEqual(sorted(result["imported"]),
                         ["dist-1500ft", "dist-200ft", "dist-400ft", "dist-800ft"])
        self.assertEqual(result["ignored"], [])
        self.assertTrue(vi.clip_path("dist-800ft").endswith("dist-800ft.mp3"))

    def test_miles_and_the_usual_forgiveness(self):
        result = vi.import_files([_write(self.src, "DIST 3MILES.wav"),
                                  _write(self.src, "dist_5_mile.mp3"),
                                  _write(self.src, "Dist-1Mile.mp3")])
        self.assertEqual(sorted(result["imported"]),
                         ["dist-1mile", "dist-3miles", "dist-5mile"])
        self.assertTrue(vi.clip_path("dist-5mile").endswith("dist-5mile.mp3"))

    def test_a_decimal_distance_is_a_distance(self):
        result = vi.import_files([_write(self.src, "dist-0.5mile.mp3"),
                                  _write(self.src, "dist-1.5miles.wav"),
                                  _write(self.src, "dist-250.5ft.mp3")])
        self.assertEqual(sorted(result["imported"]),
                         ["dist-0.5mile", "dist-1.5miles", "dist-250.5ft"])
        self.assertTrue(vi.clip_path("dist-0.5mile").endswith("dist-0.5mile.mp3"))
        self.assertEqual(sorted(vi.distance_clips()),
                         ["dist-0.5mile", "dist-1.5miles", "dist-250.5ft"])

    def test_nonsense_distances_are_still_left_out(self):
        bad = ["dist-abcft.mp3", "dist-0ft.mp3", "dist-0.0mile.mp3", "dist-012ft.mp3",
               "dist-12km.mp3", "dist--5ft.mp3", "dist-ft.mp3", "dist-.5mile.mp3",
               "dist-5.ft.mp3", "dist-1e3ft.mp3"]
        result = vi.import_files([_write(self.src, n) for n in bad])
        self.assertEqual(result["imported"], [])
        self.assertEqual(sorted(result["ignored"]), sorted(bad))
        self.assertIsNone(vi.clip_path("dist-0ft"))
        self.assertIsNone(vi.clip_path("dist-abcft"))

    def test_how_far_each_distance_name_is(self):
        self.assertEqual(vi.distance_feet("dist-400ft"), 400)
        self.assertEqual(vi.distance_feet("dist-0.5mile"), 2640)
        self.assertEqual(vi.distance_feet("dist-2miles"), 10560)
        self.assertEqual(vi.distance_feet("dist-quarter"), 1320)
        self.assertEqual(vi.distance_feet("dist-half"), 2640)
        self.assertIsNone(vi.distance_feet("turn-left"))
        self.assertIsNone(vi.distance_feet("dist-0ft"))

    def test_distance_clips_lists_fixed_and_open_ended(self):
        vi.import_files([_write(self.src, "dist-quarter.mp3"),
                         _write(self.src, "dist-400ft.wav"),
                         _write(self.src, "arrive.mp3")])
        got = vi.distance_clips()
        self.assertEqual(sorted(got), ["dist-400ft", "dist-quarter"])
        self.assertTrue(got["dist-400ft"].endswith("dist-400ft.wav"))

    def test_no_distances_means_none(self):
        self.assertEqual(vi.distance_clips(), {})

    def test_other_distances_stop_the_fixed_ones_being_missing(self):
        vi.import_files([_write(self.src, "dist-400ft.mp3"),
                         _write(self.src, "arrive.mp3")])
        st = vi.status()
        self.assertIn("dist-400ft", st["present"])
        self.assertFalse([n for n in st["missing"] if n.startswith("dist-")])
        self.assertEqual(st["unknown"], [])
        self.assertNotIn("dist-100ft", vi.status_text())
        self.assertIn("1 distance", vi.status_text())


class TheBuiltInVoice(_Case):
    """Arnold, baked into the APK: it works with no import at all. Hers wins
    wherever she has one, and Remove voice goes back to it - never silence."""

    def test_the_bundled_folder_is_the_app_roots_assets_voice(self):
        # The same base washi finds assets/maps/bayarea.mbtiles from on the
        # phone: the staged app root. (setUp patches it; this is the real one.)
        self.assertIsNotNone(_REAL_BUILTIN_DIR, "voice_import has no builtin_dir()")
        self.assertEqual(_REAL_BUILTIN_DIR(), str(APP_DIR / "assets" / "voice"))

    def test_a_built_in_clip_plays_with_nothing_imported(self):
        self.bundle("arrive.mp3")
        self.assertEqual(vi.clip_path("arrive"), os.path.join(self.builtin, "arrive.mp3"))
        self.assertEqual(vi.clip_source("arrive"), "built in")

    def test_hers_wins_over_the_built_in_one(self):
        self.bundle("arrive.mp3")
        vi.import_files([_write(self.src, "arrive.wav", b"hers")])
        self.assertEqual(vi.clip_path("arrive"), os.path.join(vi.voice_dir(), "arrive.wav"))
        self.assertEqual(vi.clip_source("arrive"), "yours")

    def test_neither_is_none(self):
        self.bundle("arrive.mp3")
        self.assertIsNone(vi.clip_path("turn-left"))
        self.assertIsNone(vi.clip_source("turn-left"))

    def test_source_and_path_never_disagree(self):
        self.bundle("arrive.mp3", "turn-left.mp3", "dist-400ft.mp3")
        vi.import_files([_write(self.src, "turn-left.mp3", b"hers"),
                         _write(self.src, "start.mp3", b"hers")])
        for name in list(vi.NAMES) + ["dist-400ft", "dist-800ft"]:
            path, source = vi.clip_path(name), vi.clip_source(name)
            self.assertEqual(path is None, source is None, name)
            if source == "yours":
                self.assertTrue(path.startswith(vi.voice_dir()), name)
            if source == "built in":
                self.assertTrue(path.startswith(self.builtin), name)

    def test_remove_voice_goes_back_to_the_built_in_one(self):
        self.bundle("arrive.mp3")
        vi.import_files([_write(self.src, "arrive.mp3", b"hers")])
        vi.clear_voice()
        self.assertEqual(vi.clip_source("arrive"), "built in")
        self.assertTrue(os.path.isfile(os.path.join(self.builtin, "arrive.mp3")),
                        "Remove voice deleted a bundled clip")

    def test_importing_over_a_built_in_clip_is_not_a_replacement_of_hers(self):
        self.bundle("arrive.mp3")
        result = vi.import_files([_write(self.src, "arrive.mp3", b"hers")])
        self.assertEqual(result["replaced"], [])

    def test_distances_are_the_union_hers_winning(self):
        self.bundle("dist-400ft.mp3", "dist-800ft.mp3", "dist-1500ft.mp3")
        vi.import_files([_write(self.src, "dist-200ft.mp3"),
                         _write(self.src, "dist-800ft.wav", b"hers")])
        got = vi.distance_clips()
        self.assertEqual(sorted(got), ["dist-1500ft", "dist-200ft", "dist-400ft", "dist-800ft"])
        self.assertTrue(got["dist-800ft"].startswith(vi.voice_dir()))
        self.assertTrue(got["dist-400ft"].startswith(self.builtin))

    def test_status_counts_built_in_clips_and_says_so(self):
        self.bundle(*(n + ".mp3" for n in vi.LINES), "dist-400ft.mp3", "dist-800ft.mp3")
        st = vi.status()
        self.assertEqual(st["missing"], [])
        self.assertEqual(sorted(st["builtin"]), sorted(list(vi.LINES) + ["dist-400ft", "dist-800ft"]))
        self.assertIn("15 of 15 lines (15 built in), 2 distances", vi.status_text())

    def test_her_clip_is_not_counted_as_built_in(self):
        self.bundle("arrive.mp3", "start.mp3")
        vi.import_files([_write(self.src, "start.mp3", b"hers")])
        self.assertEqual(vi.status()["builtin"], ["arrive"])
        self.assertIn("2 of 15 lines (1 built in)", vi.status_text())

    def test_no_bundled_folder_is_exactly_as_before(self):
        self.assertFalse(os.path.exists(self.builtin))
        self.assertIsNone(vi.clip_path("arrive"))
        self.assertEqual(vi.status()["builtin"], [])
        self.assertIn("No recorded voice", vi.status_text())

    def test_clip_path_stays_cheap_with_two_folders(self):
        self.bundle("arrive.mp3")
        started = time.perf_counter()
        for _ in range(2000):
            vi.clip_path("turn-left")
            vi.clip_path("arrive")
        self.assertLess((time.perf_counter() - started) / 4000, 0.001)


class Flourishes(_Case):
    """Character lines that play alongside the instructions: flourish-<mood>-
    <slug>, mood one of start, arrival, angry, urgent, scold (agreed with
    minion 5 and the Kotlin app). Never "missing" - they are extras."""

    def test_they_import_with_the_usual_forgiveness(self):
        result = vi.import_files([_write(self.src, "Flourish_Angry_Hasta La Vista.mp3"),
                                  _write(self.src, "flourish-start-ill-be-back.wav")])
        self.assertEqual(sorted(result["imported"]),
                         ["flourish-angry-hasta-la-vista", "flourish-start-ill-be-back"])
        self.assertTrue(vi.clip_path("flourish-angry-hasta-la-vista").endswith(".mp3"))

    def test_an_unknown_mood_is_left_out_and_named(self):
        bad = ["flourish-happy-yay.mp3", "flourish-start-.mp3", "flourish-urgent.mp3",
               "flourish-scold-café.mp3", "flourishes-angry-x.mp3"]
        result = vi.import_files([_write(self.src, n) for n in bad])
        self.assertEqual(result["imported"], [])
        self.assertEqual(sorted(result["ignored"]), sorted(bad))
        self.assertIn("flourish-happy-yay.mp3", vi.summary(result))

    def test_flourishes_by_mood_are_hers_and_the_bundled_ones(self):
        self.bundle("flourish-angry-get-down.mp3", "flourish-angry-hasta.mp3",
                    "flourish-arrival-done.mp3")
        vi.import_files([_write(self.src, "flourish-angry-hasta.mp3", b"hers"),
                         _write(self.src, "flourish-angry-mine.mp3", b"hers")])
        got = vi.flourishes("angry")
        self.assertEqual(sorted(got), ["flourish-angry-get-down", "flourish-angry-hasta",
                                       "flourish-angry-mine"])
        self.assertTrue(got["flourish-angry-hasta"].startswith(vi.voice_dir()))
        self.assertTrue(got["flourish-angry-get-down"].startswith(self.builtin))
        self.assertEqual(list(vi.flourishes("arrival")), ["flourish-arrival-done"])
        self.assertEqual(vi.flourishes("scold"), {})

    def test_they_are_never_missing_and_counted_apart(self):
        vi.import_files([_write(self.src, "arrive.mp3"),
                         _write(self.src, "flourish-urgent-move.mp3"),
                         _write(self.src, "flourish-scold-slow.mp3")])
        st = vi.status()
        self.assertEqual(st["flourishes"], ["flourish-scold-slow", "flourish-urgent-move"])
        self.assertNotIn("flourish-urgent-move", st["present"])
        self.assertFalse([n for n in st["missing"] if n.startswith("flourish")])
        self.assertEqual(st["unknown"], [])
        self.assertIn("2 character lines", vi.status_text())

    def test_the_moods_are_the_agreed_eight(self):
        # banter: AGEDASHII's ruling after minion 1's Flet rides; Kotlin reads
        # the same flourish-banter-* files.
        self.assertEqual(vi.FLOURISH_MOODS,
                         ("start", "arrival", "angry", "urgent", "scold", "sorry",
                          "ui", "banter"))
        self.assertEqual(vi._canonical("flourish-banter-haha"), "flourish-banter-haha")

    def test_sorry_is_a_mood(self):
        result = vi.import_files([_write(self.src, "flourish-sorry-my-bad.mp3")])
        self.assertEqual(result["imported"], ["flourish-sorry-my-bad"])
        self.assertEqual(list(vi.flourishes("sorry")), ["flourish-sorry-my-bad"])


class Interjections(_Case):
    """Not flourishes: sounds that drop INTO a prompt (a cough between the
    distance and the turn). interject-<slug>; never missing."""

    def test_they_import_with_the_usual_forgiveness(self):
        result = vi.import_files([_write(self.src, "interject-cough.mp3"),
                                  _write(self.src, "Interject_Cough-2.wav")])
        self.assertEqual(sorted(result["imported"]), ["interject-cough", "interject-cough-2"])

    def test_bad_names_are_left_out(self):
        bad = ["interject-.mp3", "interjects-cough.mp3", "interject-cöugh.mp3"]
        result = vi.import_files([_write(self.src, n) for n in bad])
        self.assertEqual(result["imported"], [])
        self.assertEqual(sorted(result["ignored"]), sorted(bad))

    def test_by_prefix_hers_and_the_bundled_ones(self):
        self.bundle("interject-cough.mp3", "interject-cough-2.mp3", "interject-sneeze.mp3")
        vi.import_files([_write(self.src, "interject-cough-2.mp3", b"hers")])
        got = vi.interjections()
        self.assertEqual(sorted(got), ["interject-cough", "interject-cough-2"])
        self.assertTrue(got["interject-cough-2"].startswith(vi.voice_dir()))
        self.assertTrue(got["interject-cough"].startswith(self.builtin))
        self.assertEqual(list(vi.interjections("interject-sneeze")), ["interject-sneeze"])

    def test_never_missing_and_not_a_prompt(self):
        vi.import_files([_write(self.src, "interject-cough.mp3")])
        st = vi.status()
        self.assertEqual(st["interjections"], ["interject-cough"])
        self.assertNotIn("interject-cough", st["present"])
        self.assertEqual(st["unknown"], [])


class TheRoundaboutLine(_Case):
    def test_it_is_a_name_she_may_record(self):
        self.assertIn("roundabout", vi.NAMES)
        result = vi.import_files([_write(self.src, "Roundabout.mp3")])
        self.assertEqual(result["imported"], ["roundabout"])
        self.assertIn("roundabout", vi.status()["present"])

    def test_it_is_never_nagged_about(self):
        vi.import_files([_write(self.src, "arrive.mp3")])
        self.assertNotIn("roundabout", vi.status()["missing"])
        self.assertNotIn("roundabout", vi.status_text())


class StatusSaysWhatIsThere(_Case):
    def test_nothing_yet(self):
        st = vi.status()
        self.assertEqual(st["present"], [])
        self.assertEqual(st["missing"], [n for n in vi.NAMES if n not in vi.OPTIONAL])
        self.assertIn("No recorded voice", vi.status_text())

    def test_some_of_them(self):
        vi.import_files([_write(self.src, "arrive.mp3"),
                         _write(self.src, "start.mp3")])
        st = vi.status()
        self.assertEqual(st["present"], ["arrive", "start"])
        self.assertIn("2 of 15 lines", vi.status_text())
        self.assertIn("turn-left", vi.status_text())

    def test_all_of_them(self):
        vi.import_files([_write(self.src, n + ".mp3") for n in vi.NAMES])
        self.assertIn("15 of 15 lines, 8 distances", vi.status_text())
        self.assertNotIn("Missing", vi.status_text())

    def test_a_stray_file_in_the_folder_is_reported(self):
        _write(vi.voice_dir(), "hello.mp3")
        self.assertEqual(vi.status()["unknown"], ["hello.mp3"])


# ------------------------------------------------------------------ the panel

class _Task:
    def cancel(self):
        pass


class StubPage:
    def __init__(self):
        self.services = []
        self.updates = 0
        self.tasks = []

    def update(self, *_a, **_k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()


class _Picked:
    def __init__(self, name, path):
        self.name, self.path, self.size, self.bytes = name, path, 0, None


class FakePicker:
    """ft.FilePicker's one call this panel makes, answering with `files`."""

    def __init__(self, files):
        self.files, self.calls = files, []

    async def pick_files(self, **kw):
        self.calls.append(kw)
        return self.files


def _walk(control):
    yield control
    for attr in ("content", "controls"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for c in child:
                yield from _walk(c)
        elif child is not None and hasattr(child, "__dict__"):
            yield from _walk(child)


def _texts(control):
    return " | ".join(str(c.value) for c in _walk(control)
                      if type(c).__name__ == "Text" and c.value)


def _button(control, words):
    for c in _walk(control):
        if getattr(c, "on_click", None) and words in _texts(c):
            return c
    raise AssertionError(f"no button saying {words!r} in {_texts(control)!r}")


def _tap(button):
    out = button.on_click(None)
    if asyncio.iscoroutine(out):
        asyncio.run(out)


class ThePanel(_Case):
    def test_it_builds_and_says_what_is_there(self):
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        self.assertIn("No recorded voice", _texts(panel))

    def test_the_real_picker_is_a_page_service(self):
        page = StubPage()
        vi.panel(page)
        vi.panel(page)
        pickers = [s for s in page.services if type(s).__name__ == "FilePicker"]
        self.assertEqual(len(pickers), 1)

    def test_picking_imports_and_the_line_changes(self):
        page = StubPage()
        paths = [_write(self.src, "arrive.mp3"), _write(self.src, "start.mp3")]
        picker = FakePicker([_Picked(os.path.basename(p), p) for p in paths])
        panel = vi.panel(page, picker=picker)
        _tap(_button(panel, "Import"))
        self.assertTrue(picker.calls[0]["allow_multiple"])
        self.assertEqual(vi.status()["present"], ["arrive", "start"])
        self.assertIn("2 of 15 lines", _texts(panel))
        self.assertIn("Imported 2", _texts(panel))
        self.assertGreater(page.updates, 0)

    def test_files_left_out_are_never_reported_in_green(self):
        # v1.14.0 on her phone: "Imported 16 clips. Left out, ...: dist-400ft.mp3"
        # in the colour of success. She reads the first three words.
        from agedashii_navi.theme import PAL
        paths = [_write(self.src, "arrive.mp3"), _write(self.src, "song.mp3")]
        panel = vi.panel(StubPage(), picker=FakePicker(
            [_Picked(os.path.basename(p), p) for p in paths]))
        _tap(_button(panel, "Import"))
        said = next(c for c in _walk(panel) if type(c).__name__ == "Text"
                    and str(c.value).startswith("Imported"))
        self.assertIn("song.mp3", said.value)
        self.assertEqual(said.color, PAL()["warn"])

    def test_a_clean_import_is_green(self):
        from agedashii_navi.theme import PAL
        path = _write(self.src, "arrive.mp3")
        panel = vi.panel(StubPage(), picker=FakePicker([_Picked("arrive.mp3", path)]))
        _tap(_button(panel, "Import"))
        said = next(c for c in _walk(panel) if type(c).__name__ == "Text"
                    and str(c.value).startswith("Imported"))
        self.assertEqual(said.color, PAL()["good"])

    def test_cancelling_the_picker_changes_nothing(self):
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        before = _texts(panel)
        _tap(_button(panel, "Import"))
        self.assertEqual(_texts(panel), before)

    def test_a_pick_with_no_readable_path_is_said(self):
        panel = vi.panel(StubPage(), picker=FakePicker([_Picked("arrive.mp3", None)]))
        _tap(_button(panel, "Import"))
        self.assertIn("arrive.mp3", _texts(panel))
        self.assertIsNone(vi.clip_path("arrive"))

    def test_remove_asks_once_more_before_it_removes(self):
        vi.import_files([_write(self.src, "arrive.mp3")])
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        _tap(_button(panel, "Remove"))
        self.assertIsNotNone(vi.clip_path("arrive"))
        _tap(_button(panel, "Tap again"))
        self.assertIsNone(vi.clip_path("arrive"))
        self.assertIn("No recorded voice", _texts(panel))

    def test_whoever_shows_the_clips_hears_when_they_change(self):
        # Settings' per-name list goes stale unless it is told.
        page, calls = StubPage(), []
        path = _write(self.src, "arrive.mp3")
        panel = vi.panel(page, picker=FakePicker([_Picked("arrive.mp3", path)]),
                         on_change=lambda: calls.append(vi.clip_path("arrive")))
        _tap(_button(panel, "Import"))
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0], "told before the clip was there")
        _tap(_button(panel, "Remove"))
        self.assertEqual(len(calls), 1, "arming Remove changes nothing")
        _tap(_button(panel, "Tap again"))
        self.assertEqual(calls[1:], [None])

    def test_a_listener_that_breaks_does_not_hide_what_happened(self):
        path = _write(self.src, "arrive.mp3")
        panel = vi.panel(StubPage(), picker=FakePicker([_Picked("arrive.mp3", path)]),
                         on_change=lambda: 1 / 0)
        _tap(_button(panel, "Import"))
        self.assertIn("Imported 1", _texts(panel))

    def test_remove_says_it_removes_hers_and_what_speaks_after(self):
        self.bundle("arrive.mp3")
        vi.import_files([_write(self.src, "arrive.mp3", b"hers")])
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        _tap(_button(panel, "Remove"))
        self.assertIn("your clips", _texts(panel))
        _tap(_button(panel, "Tap again"))
        self.assertIn("built-in voice", _texts(panel))
        self.assertEqual(vi.clip_source("arrive"), "built in")

    def test_nothing_in_the_panel_flexes(self):
        # A control with `expand` set at all, inside a wrapping Row, is a grey
        # box in a release build (v1.11.2). This panel sits in Settings.
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        for c in _walk(panel):
            self.assertIsNone(getattr(c, "expand", None),
                              f"{type(c).__name__} sets expand")


if __name__ == "__main__":
    unittest.main()


class TheTheatreCard(_Case):
    """Her character lines, grouped by when they play, each a tap to hear.

    Hers to curate: she asked for them by mood with a tap to hear each one.
    """

    def setUp(self):
        super().setUp()
        from agedashii_navi import prefs
        prefs.reset()
        self.played = []

    def card(self, page=None):
        return vi.theatre_card(page or StubPage(), play=self.played.append)

    def test_every_mood_and_the_cough_in_ride_order(self):
        order = [mood for mood, *_rest in vi.theatre()]
        self.assertEqual(order, ["start", "banter", "urgent", "angry", "scold", "sorry",
                                 "arrival", "ui", "cough"])
        self.assertEqual(sorted(order[:-1]), sorted(vi.FLOURISH_MOODS))

    def test_how_often_is_the_rides_own_rate(self):
        from agedashii_navi import voicelines
        often = {mood: o for mood, _t, _w, o, _c in vi.theatre()}
        self.assertEqual(often["start"], "every time")
        self.assertEqual(often["urgent"],
                         f"1 time in {round(1 / voicelines.FLOURISH_RATE['urgent'])}")
        self.assertEqual(often["cough"],
                         f"1 time in {round(1 / voicelines.COUGH_RATE)}")

    def test_a_clip_is_shown_by_what_it_says(self):
        self.assertEqual(vi.words_of("flourish-angry-you-idiot"), "you idiot")
        self.assertEqual(vi.words_of("interject-cough-2"), "cough 2")

    def test_each_clip_is_a_glove_sized_tile_in_its_group(self):
        from agedashii_navi import ui
        self.bundle("flourish-angry-you-idiot.mp3", "flourish-angry-no.mp3",
                    "interject-cough.mp3")
        vi.import_files([_write(self.src, "flourish-start-ill-be-back.mp3")])
        card, _refresh = self.card()
        text = _texts(card)
        for words in ("you idiot", "no", "ill be back", "cough"):
            self.assertIn(words, text)
        self.assertIn("yours", text)
        self.assertIn("built in", text)
        tiles = [c for c in _walk(card) if getattr(c, "on_click", None)]
        self.assertEqual(len(tiles), 4)
        self.assertTrue(all(t.height >= ui.TOUCH for t in tiles))

    def test_an_empty_group_says_how_to_fill_it(self):
        card, _refresh = self.card()
        self.assertIn("flourish-scold-<words>.mp3", _texts(card))
        self.assertIn("interject-cough-<words>.mp3", _texts(card))

    def test_a_tap_plays_that_clip_and_lights_its_tile(self):
        import flet as ft
        (path,) = self.bundle("flourish-arrival-goodbye.mp3")
        page = StubPage()
        card, _refresh = self.card(page)
        _tap(_button(card, "goodbye"))
        self.assertEqual(self.played, [[("flourish", path, None)]])
        self.assertIn('Playing "goodbye", built in.', _texts(card))
        icons = [c.icon for c in _walk(card) if type(c).__name__ == "Icon"]
        self.assertEqual(icons.count(ft.Icons.VOLUME_UP), 1)
        self.assertGreater(page.updates, 0)

    def test_only_the_last_tapped_tile_is_lit(self):
        import flet as ft
        self.bundle("flourish-urgent-now.mp3", "flourish-urgent-hurry.mp3")
        card, _refresh = self.card()
        _tap(_button(card, "now"))
        _tap(_button(card, "hurry"))
        icons = [c.icon for c in _walk(card) if type(c).__name__ == "Icon"]
        self.assertEqual(icons.count(ft.Icons.VOLUME_UP), 1)

    def test_it_says_when_character_lines_are_off(self):
        from agedashii_navi import prefs
        card, _refresh = self.card()
        self.assertNotIn("Character lines is off", _texts(card))
        prefs.set_value("flourishes", False)
        card, _refresh = self.card()
        self.assertIn("Character lines is off", _texts(card))

    def test_refresh_sees_a_new_import(self):
        card, refresh = self.card()
        self.assertNotIn("hasta la vista", _texts(card))
        vi.import_files([_write(self.src, "flourish-angry-hasta-la-vista.mp3")])
        refresh()
        self.assertIn("hasta la vista", _texts(card))


class WildTakesAndCounts(_Case):
    """minion 5's pool: wild-<slot>-<slug> beside a line, flourish-ui-*, and
    number-/hours-/seconds-<n>. All extras: never missing, never unknown."""

    def test_wild_takes_by_slot_hers_winning(self):
        self.bundle("wild-arrive-choppa.mp3", "wild-arrive-game-over.mp3",
                    "wild-offroute-big-mistake.mp3")
        vi.import_files([_write(self.src, "wild-arrive-choppa.mp3", b"hers")])
        got = vi.wilds("arrive")
        self.assertEqual(sorted(got), ["wild-arrive-choppa", "wild-arrive-game-over"])
        self.assertTrue(got["wild-arrive-choppa"].startswith(vi.voice_dir()))
        self.assertEqual(list(vi.wilds("offroute")), ["wild-offroute-big-mistake"])
        self.assertEqual(vi.wilds("speeding"), {})

    def test_a_slot_with_a_dash_is_read_whole(self):
        self.bundle("wild-slight-left-lean.mp3")
        self.assertEqual(list(vi.wilds("slight-left")), ["wild-slight-left-lean"])

    def test_bad_wild_names_are_left_out(self):
        bad = ["wild-dist-100ft-x.mp3", "wild-arrive-.mp3", "wild-nowhere-x.mp3",
               "wild-arrive.mp3"]
        result = vi.import_files([_write(self.src, n) for n in bad])
        self.assertEqual(result["imported"], [])

    def test_ui_is_a_mood(self):
        self.bundle("flourish-ui-ok.mp3", "flourish-ui-what-do-you-want.mp3")
        self.assertEqual(list(vi.flourishes("ui")),
                         ["flourish-ui-ok", "flourish-ui-what-do-you-want"])

    def test_counts_by_number(self):
        self.bundle("number-1.mp3", "number-10.mp3", "number-2.mp3",
                    "hours-1.mp3", "seconds-1.mp3")
        self.assertEqual(list(vi.counts("number")), [1, 2, 10])
        self.assertEqual(list(vi.counts("hours")), [1])
        self.assertEqual(list(vi.counts("seconds")), [1])

    def test_bad_counts_are_left_out(self):
        bad = ["number-0.mp3", "number-01.mp3", "number-x.mp3", "minutes-2.mp3"]
        result = vi.import_files([_write(self.src, n) for n in bad])
        self.assertEqual(result["imported"], [])

    def test_none_of_them_is_missing_or_unknown(self):
        vi.import_files([_write(self.src, n) for n in (
            "wild-start-come-with-me.mp3", "number-3.mp3",
            "flourish-ui-correct.mp3")])
        st = vi.status()
        self.assertEqual(st["unknown"], [])
        self.assertEqual(st["wilds"], ["wild-start-come-with-me"])
        self.assertEqual(st["counts"], ["number-3"])
        self.assertNotIn("wild-start-come-with-me", st["present"])
        self.assertFalse([n for n in st["missing"] if n not in vi.NAMES])


class TheCardShowsHisOwnLines(_Case):
    """Wild takes, grouped PER SLOT: they replace the slot's line rather than
    follow it as the moods do."""

    def setUp(self):
        super().setUp()
        from agedashii_navi import prefs
        prefs.reset()

    def test_grouped_per_slot_with_the_rule_on_the_card(self):
        self.bundle("wild-arrive-choppa.mp3", "wild-arrive-game-over.mp3",
                    "wild-offroute-big-mistake.mp3")
        card, _refresh = vi.theatre_card(StubPage(), play=lambda items: None)
        text = _texts(card)
        self.assertIn("His own lines", text)
        self.assertIn("INSTEAD of the plain line", text)
        self.assertIn("Arriving", text)
        self.assertIn("only as you arrive", text)
        self.assertIn("Off route", text)
        for words in ("choppa", "game over", "big mistake"):
            self.assertIn(words, text)
        self.assertNotIn("wild arrive", text)

    def test_only_slots_that_have_takes(self):
        self.bundle("wild-start-come-with-me.mp3")
        self.assertEqual([g[0] for g in vi.wild_groups()], ["start"])

    def test_her_own_line_for_the_slot_wins_and_the_card_says_so(self):
        self.bundle("wild-speeding-cop.mp3")
        vi.import_files([_write(self.src, "speeding.mp3", b"hers")])
        (_slot, _title, note, _takes), = vi.wild_groups()
        self.assertIn("your own speeding line wins", note)

    def test_a_built_in_turn_take_is_said_not_to_play(self):
        self.bundle("wild-turn-left-hard.mp3")
        (_slot, _title, note, _takes), = vi.wild_groups()
        self.assertIn("only the ones you imported play", note)

    def test_a_turn_take_she_imported_plays_with_no_warning(self):
        vi.import_files([_write(self.src,
                                "wild-turn-left-go-left-if-you-want-to-live.mp3")])
        (_slot, _title, note, _takes), = vi.wild_groups()
        self.assertEqual(note, "")

    def test_a_wild_tile_plays_its_clip(self):
        (path,) = self.bundle("wild-rerouting-hold-on.mp3")
        played = []
        card, _refresh = vi.theatre_card(StubPage(), play=played.append)
        _tap(_button(card, "hold on"))
        self.assertEqual(played, [[("flourish", path, None)]])
