"""What this build is getting: read-outs on their own page, one card per subject.

They were the bottom half of Settings, and she read the road graph's "not
started" as the sensors' status because the two shared a card. These pin the
page, the subjects kept apart, a repaint that leaves her where she was, and
Android's Back going up to Settings from it instead of out of the app.

Own stub page and storage directory: importing test_shell or test_road_test
from here would load a second copy under discovery and move
FLET_APP_STORAGE_DATA underneath the tests that rely on it.
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

from agedashii_navi import boot, live, prefs, sensors, ui  # noqa: E402
from agedashii_navi.pages import diagnostics                # noqa: E402
from agedashii_navi.pages import settings as settings_page  # noqa: E402


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


class _Task:
    def cancel(self):
        pass


class StubView:
    """The root view's back-button contract: records every answer."""

    def __init__(self):
        self.can_pop, self.on_confirm_pop, self.answers = True, None, []

    async def confirm_pop(self, should_pop):
        self.answers.append(should_pop)


class StubPage:
    def __init__(self):
        self.controls, self.services, self.overlay, self.tasks = [], [], [], []
        self.views = [StubView()]
        self.title = self.theme = self.theme_mode = None
        self.bgcolor = self.padding = None
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


class Event:
    def __init__(self, control=None, value=None):
        self.control = control
        if value is not None:
            self.control = type("C", (), {"value": value})()


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
        if child is not None and not isinstance(child, (str, int, float)):
            yield from walk(child, seen)


def texts(node):
    return " | ".join(str(c.value) for c in walk(node)
                      if isinstance(c, ft.Text) and c.value)


def load_main():
    spec = importlib.util.spec_from_file_location("navi_main_diag",
                                                  APP_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Clean(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        diagnostics.STATE.update(sensors="", build="")
        self.page = StubPage()
        self.went = []

    def build(self):
        return diagnostics.build(self.page, go=self.went.append)


class ThePage(_Clean):
    def test_it_builds_with_every_subject_under_its_own_heading(self):
        said = texts(self.build())
        for heading in (diagnostics.TITLE, "Map and routing", "Sensors",
                        "What a ride log records", "This build", "Server"):
            self.assertIn(heading, said)

    def test_the_road_graph_and_the_sensors_are_not_one_card(self):
        # Her v1.17.0 panel: "not started" under one heading read as the
        # sensors, and it was the graph.
        graph_card = next(c for c in walk(self.build())
                          if isinstance(c, ft.Container)
                          and "Road graph:" in texts(c)
                          and diagnostics.TITLE not in texts(c))
        self.assertIn("Map and routing", texts(graph_card))
        self.assertNotIn("Load the sensors now", texts(graph_card))
        self.assertNotIn("no sensor has reported yet", texts(graph_card))

    def test_there_is_a_way_back_to_settings(self):
        back = next(c for c in walk(self.build())
                    if getattr(c, "on_click", None) and texts(c) == "Settings")
        back.on_click(None)
        self.assertEqual(self.went, ["settings"])

    def test_every_tap_is_glove_sized(self):
        for control in walk(self.build()):
            if getattr(control, "on_click", None) is not None:
                self.assertGreaterEqual(control.height or 0, ui.TOUCH,
                                        texts(control))


class TheRouterChipTellsTheTruth(_Clean):
    """Her v1.17.0 panel read "offline maps · no graph / not started" - the
    graph's LAZY state: it loads on the first route."""

    def words(self, status):
        with mock.patch.object(live, "graph_status", return_value=status):
            return texts(self.build())

    def test_not_loaded_yet_is_not_no_graph(self):
        said = self.words({"ready": False, "phase": "idle", "installed": True,
                           "detail": "not started"})
        self.assertNotIn("no graph", said)
        self.assertNotIn("not started", said)
        self.assertIn("loads on the first route", said)

    def test_a_missing_graph_still_says_so(self):
        said = self.words({"ready": False, "phase": "absent", "installed": False,
                           "detail": "no offline road graph installed"})
        self.assertIn("no graph", said)
        self.assertIn("Road graph: no offline road graph installed", said)

    def test_a_loaded_graph_says_what_it_loaded(self):
        said = self.words({"ready": True, "phase": "ready", "installed": True,
                           "detail": "358,142 nodes from bayarea.rg in 122 ms"})
        self.assertIn("Road graph: 358,142 nodes", said)
        self.assertIn("offline router", said)


class ARepaintLeavesHerWhereSheWas(_Clean):
    def test_a_switch_repaints_inside_the_same_column(self):
        page = self.build()
        first = page.controls[0]
        switch = next(c for c in walk(page) if isinstance(c, ft.Switch))
        wanted = not prefs.sensors_on_demand()
        switch.on_change(Event(value=wanted))
        self.assertEqual(prefs.sensors_on_demand(), wanted)
        # Same Column, new contents: the scroll offset belongs to the Column,
        # so she is not thrown back to the top.
        self.assertIsNot(page.controls[0], first)
        self.assertGreater(self.page.updates, 0)

    def test_set_level_with_no_accelerometer_says_so(self):
        with mock.patch.object(sensors, "attached", return_value=True):
            page = self.build()
            set_level = next(c for c in walk(page)
                             if getattr(c, "on_click", None)
                             and "Set level here" in texts(c))
            with mock.patch.object(sensors, "zero", return_value=None):
                set_level.on_click(None)
            self.assertIn("no accelerometer has reported yet", texts(page))


class BackGoesUpNotOut(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        boot.mark_booted()
        self.main = load_main()
        self.main.UP_ON_BACK = True
        self.page = StubPage()
        asyncio.run(self.main.main(self.page))
        self.view = self.page.views[0]

    def tearDown(self):
        boot.reset()

    def back(self):
        asyncio.run(self.view.on_confirm_pop(Event(self.view)))
        return self.view.answers[-1]

    def test_off_the_root_view_is_left_alone(self):
        main = load_main()
        self.assertFalse(main.UP_ON_BACK, "on before a phone has pressed it")
        page = StubPage()
        asyncio.run(main.main(page))
        self.assertTrue(page.views[0].can_pop)
        self.assertIsNone(page.views[0].on_confirm_pop)

    def test_the_root_view_asks_before_it_pops(self):
        self.assertFalse(self.view.can_pop)
        self.assertTrue(callable(self.view.on_confirm_pop))

    def test_from_the_map_back_leaves_as_it_always_did(self):
        self.main._HERE["key"] = "ride"
        self.assertIs(self.back(), True)

    def test_from_diagnostics_back_goes_to_settings(self):
        self.main._HERE["key"] = "diagnostics"
        self.assertIs(self.back(), False)
        self.assertEqual(self.main._HERE["key"], "settings")

    def test_a_surface_can_take_back_for_itself(self):
        self.main._HERE["key"] = "settings"
        with mock.patch.object(settings_page, "back", create=True,
                               return_value=True):
            self.assertIs(self.back(), False)
        self.assertEqual(self.main._HERE["key"], "settings")

    def test_a_failing_handler_still_answers(self):
        self.main._HERE["key"] = "settings"
        with mock.patch.object(settings_page, "back", create=True,
                               side_effect=RuntimeError("boom")):
            self.assertIs(self.back(), True)

    def test_it_is_a_surface_but_not_a_drawer_row(self):
        self.assertIn("diagnostics", self.main.PAGES)
        self.assertNotIn("diagnostics", self.main.DRAWER_PAGES)
        self.assertNotIn("diagnostics", self.main.BAR_PAGES)

    def test_the_shell_can_build_it(self):
        self.main._HERE["key"] = "diagnostics"
        self.page._navi_rerender()
        self.assertIn(diagnostics.TITLE, texts(self.page.controls[-1]))


if __name__ == "__main__":
    unittest.main()
