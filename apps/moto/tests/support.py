"""What the Moto shell tests are built out of.

Three things live here, and all three exist so that a test never touches a
socket, a real preferences store, or the user's own database.

``StubPage``      enough of ``ft.Page`` to run the shell against. It has no
                  event loop on purpose: `run_task` records the coroutine
                  instead of scheduling it, so a test can decide which
                  background jobs to run and, crucially, never start the
                  outbox watcher's infinite loop.

``ShellCase``     a TestCase that swaps ``config._opener`` for something
                  that always refuses, points ``api`` at a throwaway store,
                  and puts every module-level page state back afterwards.
                  Offline is the default here because offline is the state
                  this app is designed for: every surface must render, and
                  say what it does not know, with the radio off.

``load_main``     imports ``apps/moto/main.py`` by path. Not by name: it is
                  a script at the root of the app, not a module inside the
                  package, and importing it by path is also a check that it
                  is still where the build expects to find it.
"""
import importlib.util
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

import flet as ft

from agedashii_qi import appearance
from agedashii_washi import api, config, panels, prefs
from agedashii_washi.store import Store
from agedashii_washi.surface import _TAB_INDEX

#: apps/moto - the app root, two levels up from this file.
APP_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = APP_DIR / "main.py"


def load_main():
    """Import apps/moto/main.py, once, as the module `moto_main`."""
    if "moto_main" in sys.modules:
        return sys.modules["moto_main"]
    if not MAIN_PY.is_file():
        raise AssertionError(f"the app has no entry point at {MAIN_PY}")
    # The app root goes on the path because main.py imports `agedashii_moto`,
    # which is a sibling of it. A packaged build has the same layout.
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    spec = importlib.util.spec_from_file_location("moto_main", MAIN_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["moto_main"] = module
    spec.loader.exec_module(module)
    return module


class DeadOpener:
    """config._opener, with the network unplugged. Never blocks, never waits."""

    def __init__(self):
        self.attempts = []

    def open(self, target, timeout=None):
        url = target if isinstance(target, str) else target.full_url
        self.attempts.append(url)
        raise urllib.error.URLError(ConnectionRefusedError("no route to host"))


class StubPrefs:
    """A SharedPreferences that has never been written to.

    Returns None for every key, which is exactly the shape `config.load` and
    `prefs.load` are written to survive - a first launch, or a platform that
    refuses storage. Writes are kept so a test can assert one happened.
    """

    def __init__(self):
        self.written = {}

    async def get(self, key):
        return self.written.get(key)

    async def set(self, key, value):
        self.written[key] = value
        return True


class StubPage:
    """Enough of ft.Page to run the shell against. No event loop on purpose."""

    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []
        self.tasks = []          # (coroutine fn, args) recorded, not run
        self.threads = []        # (fn, kwargs) recorded, not run
        self.dialogs = []
        self.drawer = None
        self.drawer_open = False
        self.title = None
        self.theme = None
        self.theme_mode = None
        self.on_app_lifecycle_state_change = None
        self.updates = 0

    # -- what apply_theme and render use ------------------------------------
    def update(self, *a, **k):
        self.updates += 1

    def add(self, *controls):
        self.controls.extend(controls)

    # -- dialogs -------------------------------------------------------------
    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self, *a, **k):
        if self.dialogs:
            self.dialogs.pop()

    def launch_url(self, *a, **k):
        pass

    # -- the drawer ----------------------------------------------------------
    async def show_drawer(self, *a, **k):
        self.drawer_open = True

    async def close_drawer(self, *a, **k):
        self.drawer_open = False

    # -- background work -----------------------------------------------------
    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return ("task", fn)

    def run_thread(self, fn, *args, **kwargs):
        self.threads.append((fn, args, kwargs))


class ShellCase(unittest.TestCase):
    """Offline, on a throwaway store, with every global put back afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        # 1. No network. Every probe and every read refuses immediately.
        self.opener = DeadOpener()
        real_opener = config._opener
        config._opener = self.opener
        self.addCleanup(lambda: setattr(config, "_opener", real_opener))

        status = dict(config.STATUS)
        self.addCleanup(lambda: (config.STATUS.clear(),
                                 config.STATUS.update(status)))
        url, candidates = config.SERVER_URL, list(config.CANDIDATES)
        self.addCleanup(lambda: (setattr(config, "SERVER_URL", url),
                                 setattr(config, "CANDIDATES", candidates)))
        # One candidate, not five: `config.load` probes each in turn and
        # there is nothing to learn from watching four more refusals.
        # SERVER_URL is set as well as CANDIDATES because `select` only
        # adopts a URL that answered - so with every candidate refusing, the
        # module keeps whatever address it started with, and api's own reads
        # would go to the real house.
        config.CANDIDATES = ["http://127.0.0.1:1"]
        config.SERVER_URL = "http://127.0.0.1:1"

        # 2. A store of our own, so nothing here reads or writes ~/.agedashii.
        store = Store(Path(self.tmp.name) / "store.sqlite3", ttl=30,
                      probe=lambda: (False, "test: offline"))
        old_store = api.set_store(store)
        self.addCleanup(lambda: (api.set_store(old_store), store.close()))
        api.refresh()

        # 3. Appearance globals, restored. A test that picks a theme must not
        #    leave the next one on it.
        was = {k: getattr(appearance.S, k)
               for k in ("theme", "radius", "glow", "crt", "crt_intensity",
                         "prefs", "page")}
        self.addCleanup(lambda: [setattr(appearance.S, k, v)
                                 for k, v in was.items()])
        # The shell parks these three on S. They hold a closure over the
        # page it built, so leaving one behind would let a finished test
        # repaint into a dead page.
        for name in ("rerender", "goto", "sync_watcher"):
            self.addCleanup(lambda n=name: setattr(appearance.S, n, None))

        saved_prefs = dict(prefs._V)
        loaded = prefs.LOADED
        self.addCleanup(lambda: (prefs._V.clear(), prefs._V.update(saved_prefs),
                                 setattr(prefs, "LOADED", loaded)))

        # Moto's own two keys, restored the same way. A test that picks a
        # theme or hides the hamburger must not leave the next one with it.
        from agedashii_moto import motoprefs

        saved_moto = dict(motoprefs._V)
        moto_loaded = motoprefs.LOADED
        self.addCleanup(lambda: (motoprefs._V.clear(),
                                 motoprefs._V.update(saved_moto),
                                 setattr(motoprefs, "LOADED", moto_loaded)))

        tabs = dict(_TAB_INDEX)
        self.addCleanup(lambda: (_TAB_INDEX.clear(), _TAB_INDEX.update(tabs)))
        self.addCleanup(lambda: panels.set_editing(False))

        # 4. The pages keep their own state between rebuilds, on purpose -
        #    a stacked trip must survive a tab switch. Between tests it is
        #    just contamination.
        self.addCleanup(reset_page_state)
        reset_page_state()

    def build_shell(self, page=None, store=None):
        """Run `main(page)` to completion and hand back the page it built.

        `store` is a StubPrefs to launch against. Passing one that a
        previous shell already wrote to is how a *restart* is tested: the
        second launch reads exactly what the first one stored, which is the
        only way to prove a preference survives one.
        """
        import asyncio

        page = page or StubPage()
        module = load_main()
        real = ft.SharedPreferences
        # `main()` constructs the service itself - it has to, a Flet service
        # is registered by being constructed inside the page context - so a
        # specific store is supplied by making the class hand that one back.
        ft.SharedPreferences = (lambda: store) if store is not None else StubPrefs
        try:
            asyncio.run(module.main(page))
        finally:
            ft.SharedPreferences = real
        # The outbox watcher is an infinite loop. Nothing in a test should
        # ever run it, so it is dropped here rather than left for a later
        # drain to trip over.
        page.tasks = [t for t in page.tasks
                      if getattr(t[0], "__name__", "") != "watch_outbox"]
        return page

    def run_task(self, page, index=-1):
        """Run one recorded coroutine - a drawer tap, say - to completion."""
        import asyncio

        fn, args = page.tasks[index]
        asyncio.run(fn(*args))

    def tap(self, page, control):
        """Fire a control's on_click the way Flet would, sync or async.

        Two kinds of handler are wired into this shell: plain functions that
        hand the real work to `page.run_task`, and coroutine functions Flet
        awaits itself. A test that only knew about the first kind would call
        the second, get a coroutine back, and quietly assert on a tap that
        never happened - which is how three of these tests passed for the
        wrong reason the first time they were written.
        """
        import asyncio
        import inspect

        before = len(page.tasks)
        result = control.on_click(None)
        if inspect.isawaitable(result):
            asyncio.run(result)
            return
        for fn, args in page.tasks[before:]:
            asyncio.run(fn(*args))


def reset_page_state():
    """Put every page module's between-rebuild state back to empty."""
    from agedashii_moto.pages import garage, trips, xnav

    trips._S.update({"builder": [], "leg_routes": {}, "combined": None,
                     "detail": set(), "editor": None, "ride": None, "note": ""})
    trips._PLAN.update({"route": None, "from": None, "to": None,
                        "from_text": "", "to_text": ""})
    trips._REFRESH.update({"cards": {}, "builder": None})
    garage._state.update({"bike": None, "section": "dashboard", "note": None})
    garage._flip.update({"query": "motorcycle", "min": "", "max": "",
                         "data": None, "error": "", "note": "", "ran": False})
    xnav._NAV.update({"a": None, "b": None, "route": None, "limits": None,
                      "speeds": [], "corners": [], "profile": None,
                      "profile_error": "", "pois": {}, "poi_failed": [],
                      "poi_open": False, "active": set(), "note": "",
                      "trip_id": ""})


# ------------------------------------------------------------ tree walking
def walk(control, depth=0, limit=60):
    """Every control in a tree, parents first. Cycles are not possible here."""
    if depth > limit or not isinstance(control, ft.Control):
        return
    yield control
    for attr in ("content", "leading", "trailing"):
        yield from walk(getattr(control, attr, None), depth + 1, limit)
    for attr in ("controls", "actions", "tabs", "destinations", "layers",
                 "polylines", "markers"):
        for item in (getattr(control, attr, None) or []):
            yield from walk(item, depth + 1, limit)


def texts(control):
    """Every string a tree would put on screen, flattened."""
    out = []
    for node in walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        for attr in ("label", "title", "hint_text", "tooltip", "text"):
            got = getattr(node, attr, None)
            if isinstance(got, str):
                out.append(got)
        for item in (getattr(node, "options", None) or []):
            label = getattr(item, "text", None)
            if isinstance(label, str):
                out.append(label)
    return out


def find(control, predicate):
    """The first control in the tree that `predicate` says yes to, or None."""
    for node in walk(control):
        try:
            if predicate(node):
                return node
        except Exception:  # noqa: BLE001 - a probe that throws is a no
            continue
    return None
