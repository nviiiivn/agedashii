"""His line on a tap: opening a search, and picking a destination.

Her "WANT 2" on the search button. character.tap() is minion 3's and decides
whether anything plays (the Voice and taps switches, never over a prompt);
these pin only that the pages call it at the two moments, and not on a
repaint of the page she is already on.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("FLET_APP_STORAGE_DATA", _TMP.name)

import flet as ft                                          # noqa: E402

from agedashii_navi import boot, character, live, prefs    # noqa: E402
from agedashii_navi.pages import routes as routes_page      # noqa: E402
from agedashii_navi.pages import search as search_page      # noqa: E402


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


class _Task:
    def cancel(self):
        pass


class StubPage:
    def __init__(self):
        self.controls, self.services, self.overlay, self.tasks = [], [], [], []
        self.views = []
        self.title = self.theme = self.theme_mode = None
        self.bgcolor = self.padding = self.drawer = None
        self.updates = 0

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *_a, **_k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **_kwargs):
        self.tasks.append((fn, args))

    async def close_drawer(self):
        pass


def walk(node, seen=None):
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions"):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    for attr in ("content", "title"):
        child = getattr(node, attr, None)
        if isinstance(child, ft.BaseControl):
            yield from walk(child, seen)


def texts(node):
    return [str(c.value) for c in walk(node)
            if isinstance(c, ft.Text) and c.value]


def row_saying(tree, words):
    return next(c for c in walk(tree) if getattr(c, "on_click", None)
                and words in texts(c))


ROW = {"name": "Chabot Space Center", "lat": 37.8188, "lon": -122.1807,
       "kind": "poi", "detail": "Oakland", "dist_m": 9000.0}


class PickingADestination(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        patches = [mock.patch.object(character, "tap", return_value=[]),
                   mock.patch.object(live, "set_destination")]
        self.tap, self.aim = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def _pick(self, module):
        saved = module._S.get("rows")
        module._S["rows"] = [dict(ROW)]
        self.addCleanup(module._S.__setitem__, "rows", saved)
        tree = module.build(StubPage(), go=lambda key: None)
        row_saying(tree, ROW["name"]).on_click(None)

    def test_where_to(self):
        self._pick(routes_page)
        self.aim.assert_called_once()
        self.tap.assert_called_once_with()

    def test_around(self):
        self._pick(search_page)
        self.aim.assert_called_once()
        self.tap.assert_called_once_with()


class OpeningASearch(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        boot.mark_booted()
        spec = importlib.util.spec_from_file_location("navi_main_taps",
                                                      APP_DIR / "main.py")
        self.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.main)
        self.main._HERE["key"] = "ride"
        self.page = StubPage()
        asyncio.run(self.main.main(self.page))
        patch = mock.patch.object(character, "tap", return_value=[])
        self.tap = patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        boot.reset()

    def open_from_the_menu(self, label):
        row = row_saying(self.page.drawer, label)
        self.page.tasks.clear()
        row.on_click(None)
        fn, args = self.page.tasks[-1]
        asyncio.run(fn(*args))

    def test_opening_where_to_gets_his_line(self):
        self.open_from_the_menu("Where to")
        self.tap.assert_called_once_with()

    def test_opening_around_gets_his_line(self):
        self.open_from_the_menu("Around")
        self.tap.assert_called_once_with()

    def test_not_on_the_page_she_is_already_on(self):
        self.open_from_the_menu("Where to")
        self.open_from_the_menu("Where to")
        self.assertEqual(self.tap.call_count, 1)

    def test_not_on_other_pages(self):
        self.open_from_the_menu("Rides")
        self.open_from_the_menu("Settings")
        self.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
