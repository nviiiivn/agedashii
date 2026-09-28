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
        self.src = tempfile.mkdtemp(dir=_TMP.name, prefix="laptop-")
        vi.clear_voice()
        self.addCleanup(vi.clear_voice)            # runs before env.stop


class WhereTheVoiceLives(_Case):
    def test_it_is_the_apps_own_storage_under_voice(self):
        self.assertEqual(vi.voice_dir(), os.path.join(_TMP.name, "voice"))

    def test_the_names_are_the_twenty_three_fixed_ones(self):
        self.assertEqual(len(vi.NAMES), 23)
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
        self.assertEqual(removed, 23)
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


class StatusSaysWhatIsThere(_Case):
    def test_nothing_yet(self):
        st = vi.status()
        self.assertEqual(st["present"], [])
        self.assertEqual(st["missing"], list(vi.NAMES))
        self.assertIn("No recorded voice", vi.status_text())

    def test_some_of_them(self):
        vi.import_files([_write(self.src, "arrive.mp3"),
                         _write(self.src, "start.mp3")])
        st = vi.status()
        self.assertEqual(st["present"], ["arrive", "start"])
        self.assertIn("2 of 23", vi.status_text())
        self.assertIn("turn-left", vi.status_text())

    def test_all_of_them(self):
        vi.import_files([_write(self.src, n + ".mp3") for n in vi.NAMES])
        self.assertIn("23 of 23", vi.status_text())
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
        self.assertIn("2 of 23", _texts(panel))
        self.assertIn("Imported 2", _texts(panel))
        self.assertGreater(page.updates, 0)

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

    def test_nothing_in_the_panel_flexes(self):
        # A control with `expand` set at all, inside a wrapping Row, is a grey
        # box in a release build (v1.11.2). This panel sits in Settings.
        panel = vi.panel(StubPage(), picker=FakePicker([]))
        for c in _walk(panel):
            self.assertIsNone(getattr(c, "expand", None),
                              f"{type(c).__name__} sets expand")


if __name__ == "__main__":
    unittest.main()
