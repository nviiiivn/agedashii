"""What the Watudo shell tests are driven with. No socket, no display.

Three stubs and a base case:

``NoServer``    replaces ``config._opener``. Every request raises URLError,
                which is what "offline" is to this app - the same door the
                store's own probe knocks on, so `check_online()` answers no
                without a firewall rule or a timeout.
``StubPrefs``   a dict behind Flet's SharedPreferences interface. The real
                one is a registered service and needs a live page context;
                these tests have neither.
``StubPage``    enough of ``ft.Page`` for the shell to build against, and
                nothing more. It *records* work handed to ``run_task`` and
                ``run_thread`` rather than running it, so a test decides
                when a background read happens instead of discovering one
                mid-assertion.

The store is a tempfile per test (FLET_APP_STORAGE_DATA), thrown away with
it, so nothing here can touch the real outbox on this machine.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
REPO = APP_DIR.parents[1]

# The app package is pip-installed editable, but a checkout that has not
# been installed must still be able to run its own tests.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def load_main():
    """Import ``apps/watudo/main.py`` by path, as its own module.

    By path and not by name on purpose. The repo root is on sys.path during
    discovery and it holds the dashboard's own ``main.py``, so ``import
    main`` would quietly load the wrong app - and that app imports
    ``agedashii_tofu``, which is exactly what test_isolation is there to prove
    this one does not.

    The module name is not ``__main__``, so the ``ft.run`` guard at the
    bottom of the file does not fire.
    """
    path = APP_DIR / "main.py"
    spec = importlib.util.spec_from_file_location("watudo_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoServer:
    """config._opener with nobody on the other end."""

    def __init__(self):
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(getattr(req, "full_url", req))
        raise urllib.error.URLError("offline (test)")


class StubPrefs:
    """Flet's SharedPreferences, as a dict."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value
        return True

    async def remove(self, key):
        self.data.pop(key, None)
        return True


class _Task:
    """What run_task hands back. Never scheduled; never awaited."""

    def cancel(self):
        pass


class StubPage:
    """Enough ft.Page for a shell to build against."""

    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []
        self.drawer = None
        self.title = None
        self.theme = None
        self.theme_mode = None
        self.on_app_lifecycle_state_change = None
        # Work the shell handed off rather than doing inline. Recorded so a
        # test can run exactly the one it means to.
        self.tasks = []
        self.threads = []
        self.dialogs = []
        self.drawer_open = False
        self.updates = 0

    # --- what the shell calls -------------------------------------------
    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *a, **k):
        self.updates += 1

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **kwargs):
        self.threads.append((fn, args, kwargs))

    async def show_drawer(self, *a, **k):
        self.drawer_open = True

    async def close_drawer(self, *a, **k):
        self.drawer_open = False

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self, *a, **k):
        if self.dialogs:
            self.dialogs.pop()

    # --- what a test calls ----------------------------------------------
    def run_last_task(self):
        """Run the most recently deferred coroutine, to completion."""
        fn, args = self.tasks[-1]
        return asyncio.run(fn(*args))

    def take_task(self, fn_name):
        """Run the newest deferred coroutine whose function has this name."""
        for fn, args in reversed(self.tasks):
            if getattr(fn, "__name__", "") == fn_name:
                return asyncio.run(fn(*args))
        raise AssertionError(f"no deferred task named {fn_name!r}")


class Event:
    """The `e` a Flet handler is given: only ever `e.control` here."""

    def __init__(self, control=None):
        self.control = control


def walk(node, seen=None):
    """Every control in a built tree, once each.

    Flet trees branch through `controls`, `content`, `actions` and `title`,
    and a shell built out of Containers-inside-Containers uses all four.
    """
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions", "destinations", "tabs"):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    for attr in ("content", "title", "leading", "label"):
        child = getattr(node, attr, None)
        if hasattr(child, "_c") or hasattr(child, "controls"):
            yield from walk(child, seen)


def texts(node):
    """Every Text value in a tree, for asserting on wording."""
    import flet as ft
    out = []
    for c in walk(node):
        if isinstance(c, ft.Text) and c.value:
            out.append(str(c.value))
        label = getattr(c, "label", None)
        if isinstance(label, str) and label:
            out.append(label)
    return out


def first(node, kind):
    for c in walk(node):
        if isinstance(c, kind):
            return c
    return None


def every(node, kind):
    return [c for c in walk(node) if isinstance(c, kind)]


class WatudoCase(unittest.TestCase):
    """A Watudo shell, built offline, against a throwaway store."""

    def setUp(self):
        from agedashii_qi import DEFAULT, apply_theme
        from agedashii_washi import api, config, panels, prefs
        from agedashii_washi.store import Store
        from agedashii_washi.surface import _TAB_INDEX

        self.api, self.config, self.prefs, self.panels = api, config, prefs, panels

        # 1. Nothing reaches a network, and the address the module holds is
        #    put back afterwards - config.SERVER_URL is a module global and
        #    a Settings test moves it.
        self.opener = NoServer()
        self._real_opener = config._opener
        config._opener = self.opener
        self.addCleanup(setattr, config, "_opener", self._real_opener)
        self.addCleanup(setattr, config, "SERVER_URL", config.SERVER_URL)
        self.addCleanup(setattr, config, "CANDIDATES", list(config.CANDIDATES))
        self.addCleanup(config.STATUS.update, dict(config.STATUS))

        # 2. The store is a tempfile, and the module forgets it afterwards.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["FLET_APP_STORAGE_DATA"] = self.tmp.name
        self.addCleanup(os.environ.pop, "FLET_APP_STORAGE_DATA", None)
        store = Store(Path(self.tmp.name) / "store.sqlite3",
                      probe=lambda: config.probe(config.SERVER_URL))
        self._real_store = api.set_store(store)
        self.addCleanup(api.set_store, self._real_store)
        self.addCleanup(store.close)

        # 3. Preferences, the theme and the panel editor all start shipped.
        #    Both stores: washi's, and this app's own.
        from agedashii_watudo import prefs as wprefs
        from agedashii_watudo.pages import settings as settings_page
        self.wprefs = wprefs
        prefs.reset_all()
        self.addCleanup(prefs.reset_all)
        wprefs.reset_all()
        self.addCleanup(wprefs.reset_all)
        # Settings keeps its last status line at module level so a message
        # survives the repaint the setting itself caused; one test's "saved"
        # must not be another test's starting condition.
        settings_page._STATUS.update({"msg": "", "bad": False})
        self.addCleanup(settings_page._STATUS.update, {"msg": "", "bad": False})
        panels.set_editing(False)
        self.addCleanup(panels.set_editing, False)
        _TAB_INDEX.pop("wat2do", None)
        self.addCleanup(_TAB_INDEX.pop, "wat2do", None)
        self.addCleanup(apply_theme, StubPage(), DEFAULT)

        # 4. The page's own module-level session state, so one test's scan
        #    is not another test's starting condition.
        from agedashii_watudo.pages import future, wat2do
        self.addCleanup(wat2do._S.update, dict(wat2do._S))
        self.addCleanup(future._S.update, dict(future._S))

        self.main = load_main()

    def build(self, stored_prefs=None):
        """Run the shell's `main()` against a stub page. Returns the page."""
        import flet as ft
        from unittest import mock

        page = StubPage()
        store = StubPrefs(stored_prefs)
        self.stub_prefs = store
        with mock.patch.object(ft, "SharedPreferences", lambda *a, **k: store):
            asyncio.run(self.main.main(page))
        return page

    # --- reading the built shell ----------------------------------------
    def shell(self, page):
        """The one control the shell adds to the page."""
        self.assertEqual(len(page.controls), 1,
                         "the shell adds exactly one root control")
        return page.controls[0]

    def chrome(self, page):
        """The topbar/body/bar column, from under whatever is over it.

        With the CRT overlay on the root is a Stack with the SafeArea
        underneath, so a test that reaches for `.content` straight off the
        root works until someone turns the overlay on.
        """
        import flet as ft
        root = self.shell(page)
        if isinstance(root, ft.Stack):
            root = root.controls[0]
        return root.content

    def topbar(self, page):
        return self.chrome(page).controls[0]

    def body(self, page):
        """The surface currently on screen, without the chrome around it."""
        return self.chrome(page).controls[1]

    def bar(self, page):
        import flet as ft
        return first(self.shell(page), ft.NavigationBar)

    def nav_to(self, page, key):
        """Go somewhere the way she would: tap the bottom bar."""
        bar = self.bar(page)
        self.assertIsNotNone(bar, "no bottom bar to navigate with")
        bar.selected_index = self.main.BAR_PAGES.index(key)
        bar.on_change(Event(bar))
        self.assertEqual(self.main.surface_of(), key)

    def show_menu(self):
        """Turn the hamburger on, as Settings > Navigation does."""
        from agedashii_watudo import prefs as wprefs
        wprefs.set_menu_button(True)
        self.rerender()

    def rerender(self):
        """The whole-app repaint the shell parks on S, as a write would."""
        from agedashii_qi import S
        S.rerender()
