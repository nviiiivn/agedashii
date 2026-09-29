"""Keep the screen on actually keeps the screen on.

It never did: main.py set `page.wakelock_enabled`, which Flet 0.86's Page does
not have, so the switch in Settings was a control that did nothing and the
phone slept mid-ride - and a Flet app whose screen locks stops guiding and
stops recording. These pin the real service being asked, and asked again
whenever the switch or the window changes.

Own stub page and own storage directory rather than test_shell's: importing
test_shell from another module loads a second copy of it under discovery and
repoints FLET_APP_STORAGE_DATA underneath the tests that rely on it.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("FLET_APP_STORAGE_DATA", _TMP.name)

import flet as ft                                          # noqa: E402

from agedashii_navi import awake, boot, live, prefs         # noqa: E402


def tearDownModule():                                       # noqa: N802
    _TMP.cleanup()


class FakeWakelock:
    """ft.Wakelock's two calls, counted; `refuse` makes the phone say no."""

    def __init__(self, refuse=False):
        self.calls = []
        self.refuse = refuse

    async def enable(self):
        self.calls.append("enable")
        if self.refuse:
            raise RuntimeError("no window")

    async def disable(self):
        self.calls.append("disable")
        if self.refuse:
            raise RuntimeError("no window")


class _Task:
    def cancel(self):
        pass


class StubPage:
    """Enough ft.Page for main() to start against; run_task only records."""

    def __init__(self):
        self.controls, self.services, self.overlay, self.tasks = [], [], [], []
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


def load_main():
    """apps/navi/main.py by path: the repo root holds a different main.py."""
    spec = importlib.util.spec_from_file_location("navi_main_awake",
                                                  APP_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def follow(page, force=False):
    return asyncio.run(awake.follow(page, force))


def follows(page):
    """The awake.follow tasks main() queued, as their `force` flags."""
    return [bool(args[1]) if len(args) > 1 else False
            for fn, args in page.tasks if fn is awake.follow]


class TheSwitchReachesTheScreen(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        self.page = StubPage()
        self.lock = self.page._navi_wakelock = FakeWakelock()

    def test_on_by_default_asks_the_phone_to_stay_awake(self):
        self.assertTrue(prefs.keep_awake())
        self.assertIs(follow(self.page), True)
        self.assertEqual(self.lock.calls, ["enable"])

    def test_off_lets_it_sleep(self):
        prefs.set_value("keep_awake", False)
        self.assertIs(follow(self.page), False)
        self.assertEqual(self.lock.calls, ["disable"])

    def test_flipping_the_switch_takes_effect_without_a_restart(self):
        follow(self.page)
        prefs.set_value("keep_awake", False)
        follow(self.page)
        prefs.set_value("keep_awake", True)
        follow(self.page)
        self.assertEqual(self.lock.calls, ["enable", "disable", "enable"])

    def test_no_change_asks_nothing(self):
        # Called after every rebuild, so it must be free when nothing moved.
        follow(self.page)
        follow(self.page)
        follow(self.page)
        self.assertEqual(self.lock.calls, ["enable"])

    def test_force_asks_again(self):
        # Back from another app: the window may have been rebuilt without it.
        follow(self.page)
        follow(self.page, force=True)
        self.assertEqual(self.lock.calls, ["enable", "enable"])

    def test_a_refusal_is_not_believed(self):
        self.lock.refuse = True
        self.assertIsNone(follow(self.page))
        self.lock.refuse = False
        self.assertIs(follow(self.page), True)
        self.assertEqual(self.lock.calls, ["enable", "enable"])


class TheServiceIsTheRealOne(unittest.TestCase):
    def test_a_wakelock_is_made_once_and_parked_on_the_page(self):
        page = StubPage()
        first = awake._service(page)                        # noqa: SLF001
        self.assertIsInstance(first, ft.Wakelock)
        self.assertIs(awake._service(page), first)          # noqa: SLF001
        self.assertEqual(page.services, [first])

    def test_each_page_gets_its_own(self):
        # A new activity on a live process runs main() with a new page.
        a, b = StubPage(), StubPage()
        self.assertIsNot(awake._service(a), awake._service(b))  # noqa: SLF001

    def test_the_dead_field_is_gone(self):
        self.assertNotIn("wakelock_enabled",
                         (APP_DIR / "main.py").read_text(encoding="utf-8"))
        self.assertNotIn("wakelock_enabled",
                         getattr(ft.Page, "__dataclass_fields__", {}))


class TheShellKeepsItFollowing(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        boot.reset()
        self.main = load_main()

    def tearDown(self):
        boot.reset()

    def test_startup_asks_with_force(self):
        page = StubPage()
        asyncio.run(self.main.main(page))
        self.assertIn(True, follows(page))

    def test_every_rebuild_checks_the_switch(self):
        boot.mark_booted()
        page = StubPage()
        asyncio.run(self.main.main(page))
        before = len(follows(page))
        page._navi_rerender()                               # noqa: SLF001
        self.assertEqual(len(follows(page)), before + 1)

    def test_coming_back_to_the_front_asks_again(self):
        page = StubPage()
        asyncio.run(self.main.main(page))
        handler = page.on_app_lifecycle_state_change
        page.tasks.clear()
        handler(ft.AppLifecycleStateChangeEvent(
            name="app_lifecycle_state_change", control=page,
            state=ft.AppLifecycleState.PAUSE))
        self.assertEqual(follows(page), [])
        handler(ft.AppLifecycleStateChangeEvent(
            name="app_lifecycle_state_change", control=page,
            state=ft.AppLifecycleState.RESUME))
        self.assertEqual(follows(page), [True])


if __name__ == "__main__":
    unittest.main()
