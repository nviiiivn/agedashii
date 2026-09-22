"""The app shell: does it build, does it paint, and does the menu reach it all.

These do not test FoodZen, EatZen or BPM - those pages came over from the
dashboard with their own tests and their own history. They test the thing
that was actually written here: the shell around them. Every one of them runs
with the radio off, because that is the normal case for this app and any test
that needed a server would be testing the server.

Three of them exist because of specific damage:

`test_rendering_never_reaches_the_network` - her FoodZen data has already been
destroyed once by something that poked a save endpoint to see what it did. A
render is a read of what is already held; if one of these pages ever starts
issuing a request just to paint, this fails before it can be a habit.

`test_no_cross_app_imports` - the whole point of the split. Three apps on two
shared packages, and nothing of one app inside another.

`test_the_drawer_sub_rows_match_the_pages_real_tabs` - the drawer jumps
straight to a tab by index. Reorder the tabs in pages/foodzen.py and, with no
check, the menu quietly sends you to the wrong one.

Run them::

    /home/nvii/venvs/flet/bin/python -m unittest discover -s apps/foodzen/tests -t .
"""
import asyncio
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

APP = pathlib.Path(__file__).resolve().parent.parent      # apps/foodzen
REPO = APP.parent.parent                                  # the checkout root
for path in (str(REPO), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import flet as ft                                          # noqa: E402
from agedashii_qi import PAL, S, THEME_NAMES, apply_theme  # noqa: E402
from agedashii_washi import api as M                       # noqa: E402
from agedashii_washi import config, panels, prefs          # noqa: E402
from agedashii_washi.surface import _TAB_INDEX             # noqa: E402


def _load_main():
    """Import apps/foodzen/main.py under a name of its own.

    By path rather than by `import main`, because the checkout root has a
    main.py too - the dashboard's - and whichever is on sys.path first would
    win. Naming it here means the test can never be testing the wrong app.
    """
    spec = importlib.util.spec_from_file_location("agedashii_foodzen_main",
                                                  APP / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP_MAIN = _load_main()


class FakePage:
    """As much of ft.Page as the shell touches, and not one attribute more.

    Notably `run_task` runs nothing: it closes the coroutine it is handed and
    returns. Every background job in this app (the outbox watcher, _tabs
    filling in the tabs you are not looking at) is deliberately fire and
    forget, so a test that let them run would be testing the event loop.
    """

    def __init__(self):
        self.controls = []
        self.services = []
        self.drawer = None
        self.dialog = None
        self.title = None
        self.theme = None
        self.theme_mode = None
        self.on_app_lifecycle_state_change = None
        self.updates = 0
        self.threads = []

    def update(self):
        self.updates += 1

    def add(self, *controls):
        self.controls.extend(controls)

    def run_task(self, fn, *a):
        result = fn(*a)
        if asyncio.iscoroutine(result):
            result.close()
        return None

    def run_thread(self, fn, *a, **kw):
        self.threads.append((fn, a, kw))

    async def close_drawer(self):
        self.drawer = None

    async def show_drawer(self, drawer=None):
        self.drawer = drawer or self.drawer

    def show_dialog(self, dlg):
        self.dialog = dlg

    def pop_dialog(self):
        self.dialog = None


class ShellCase(unittest.TestCase):
    """Common ground: an offline page, a shell on it, and no state left behind."""

    def setUp(self):
        # Offline is the case that must work; a test that depended on the LAN
        # would pass or fail on whether the Pi is plugged in.
        self._status = dict(config.STATUS)
        config.STATUS.update({"ok": False, "detail": "test: offline"})
        self.addCleanup(lambda: config.STATUS.update(self._status))

        self._theme = S.theme
        self.addCleanup(lambda: setattr(S, "theme", self._theme))
        self._rerender = getattr(S, "rerender", None)
        self.addCleanup(lambda: setattr(S, "rerender", self._rerender))

        self._tabs_saved = dict(_TAB_INDEX)
        self.addCleanup(lambda: (_TAB_INDEX.clear(), _TAB_INDEX.update(self._tabs_saved)))

        self._editing = panels.editing()
        self.addCleanup(lambda: panels.set_editing(self._editing))

        bar, crt = prefs.bottom_bar_enabled(), prefs.crt_overlay()
        self.addCleanup(lambda: (prefs.set_bottom_bar_enabled(bar),
                                 prefs.set_crt_overlay(crt)))

        self.page = FakePage()
        self.shell = APP_MAIN.Shell(self.page)

    def root(self):
        """The one control the shell puts on the page."""
        self.assertEqual(len(self.page.controls), 1,
                         "the shell must leave exactly one root on the page")
        return self.page.controls[0]

    def chrome(self):
        """The topbar/body/bottom-bar column, from under SafeArea and the CRT."""
        root = self.root()
        if isinstance(root, ft.Stack):
            root = root.controls[0]
        self.assertIsInstance(root, ft.SafeArea)
        return root.content

    def drawer_rows(self):
        return [c for c in self.shell.nav_drawer().controls
                if getattr(c, "on_click", None) or isinstance(c, ft.Container)]

    def tap(self, control):
        """Fire a control's on_click, sync or async, and wait for it."""
        handler = control.on_click
        result = handler(None)
        if asyncio.iscoroutine(result):
            asyncio.run(result)


# ------------------------------------------------------------ it builds at all
class ItBuilds(ShellCase):
    def test_a_shell_paints_a_first_screen(self):
        self.shell.attach()
        self.assertEqual(self.shell.current, APP_MAIN.FOODZEN)
        self.assertIsNotNone(self.root())
        self.assertGreater(self.page.updates, 0, "nothing was ever pushed to Flet")

    def test_attach_registers_the_repaint_pages_write_through(self):
        """forms.rebuilder looks for S.rerender after every write.

        Without it a write replaces page.controls outright and takes the
        topbar and the nav bar with it - you land on a page with no way off.
        """
        self.shell.attach()
        self.assertTrue(callable(getattr(S, "rerender", None)))
        self.shell.render(APP_MAIN.BPM)
        S.rerender()
        self.assertEqual(self.shell.current, APP_MAIN.BPM,
                         "the repaint must return to the page you were on")

    def test_every_surface_renders(self):
        for key in APP_MAIN.PAGES:
            with self.subTest(surface=key):
                self.shell.render(key)
                self.assertEqual(self.shell.current, key)
                self.assertIsNotNone(self.shell.body.content,
                                     f"{key} built nothing")

    def test_every_surface_renders_with_the_server_answering(self):
        """The offline path is not the only one. Canned answers, no wire."""
        state = {"lists": [], "need": [], "pantry": {"items": []}, "recipes": [],
                 "purchases": [], "templates": [], "eatzen": [], "bpm": []}
        with mock.patch.object(M, "STATUS_LIVE", lambda: True), \
             mock.patch.object(M, "foodzen_state", return_value=state), \
             mock.patch.object(M, "_get", return_value=None), \
             mock.patch.object(M, "bpm_readings", return_value=[]):
            for key in APP_MAIN.PAGES:
                with self.subTest(surface=key):
                    self.shell.render(key)
                    self.assertIsNotNone(self.shell.body.content)

    def test_an_unknown_surface_lands_somewhere_real(self):
        """A stale deep link is a wrong page, never a blank one."""
        self.shell.render("no-such-page")
        self.assertEqual(self.shell.current, APP_MAIN.FOODZEN)
        self.assertIsNotNone(self.shell.body.content)

    def test_each_surface_is_the_page_its_label_promises(self):
        marks = {}
        for name, sentinel in (("build_eatzen", ft.Text("EATZEN")),
                               ("build_bpm", ft.Text("BPM"))):
            marks[name] = sentinel
        with mock.patch.object(APP_MAIN.eatzen, "build_eatzen",
                               return_value=marks["build_eatzen"]), \
             mock.patch.object(APP_MAIN.eatzen, "build_bpm",
                               return_value=marks["build_bpm"]), \
             mock.patch.object(APP_MAIN.foodzen, "build",
                               return_value=ft.Text("FOODZEN")):
            self.assertEqual(self.shell.body_for(APP_MAIN.EATZEN).value, "EATZEN")
            self.assertEqual(self.shell.body_for(APP_MAIN.BPM).value, "BPM")
            self.assertEqual(self.shell.body_for(APP_MAIN.FOODZEN).value, "FOODZEN")


# ------------------------------------------------------------------ the chrome
class Chrome(ShellCase):
    def test_the_pill_says_foodzen(self):
        """Two Agedashii icons side by side on a home screen; one word each."""
        pill = self.shell.brand_chip()
        self.assertEqual(pill.content.value, "Foodzen")
        self.assertEqual(pill.gradient.colors,
                         [APP_MAIN.ORANGE, APP_MAIN.PINK, APP_MAIN.CYAN])

    def test_the_topbar_carries_the_menu_the_pill_and_the_palette(self):
        self.shell.attach()
        bar = self.chrome().controls[0].content
        icons = [c.icon for c in bar.controls if isinstance(c, ft.IconButton)]
        self.assertIn(ft.Icons.MENU, icons)
        self.assertIn(ft.Icons.PALETTE, icons)
        self.assertIn("Foodzen", [getattr(c.content, "value", None)
                                  for c in bar.controls
                                  if isinstance(c, ft.Container)])

    def test_the_safearea_wraps_the_chrome_and_not_the_crt(self):
        """A screen effect that stopped short of the cutout would draw a
        bright band exactly where the cutout is."""
        prefs.set_crt_overlay(True)
        self.shell.attach()
        root = self.root()
        self.assertIsInstance(root, ft.Stack)
        self.assertEqual(len(root.controls), 2)
        self.assertIsInstance(root.controls[0], ft.SafeArea)
        self.assertTrue(root.controls[1].ignore_interactions,
                        "the overlay must not eat taps")

    def test_no_crt_means_no_extra_controls_at_all(self):
        prefs.set_crt_overlay(False)
        self.shell.attach()
        self.assertIsInstance(self.root(), ft.SafeArea)

    def test_the_bottom_bar_can_be_turned_off(self):
        prefs.set_bottom_bar_enabled(True)
        self.shell.attach()
        self.assertEqual(len(self.chrome().controls), 3)
        prefs.set_bottom_bar_enabled(False)
        self.shell.render(self.shell.current)
        self.assertEqual(len(self.chrome().controls), 2)

    def test_the_sync_chip_is_invisible_with_an_empty_outbox(self):
        """An always-present "all synced" badge is noise, and worse, reads as
        a claim about the server when it is only a claim about the queue."""
        with mock.patch.object(M, "sync_status",
                               return_value={"label": "", "parked": 0,
                                             "conflict": 0, "online": False}):
            self.shell.paint_sync()
        self.assertFalse(self.shell.sync_chip.visible)

    def test_the_sync_chip_says_what_is_waiting(self):
        with mock.patch.object(M, "sync_status",
                               return_value={"label": "3 changes waiting",
                                             "parked": 0, "conflict": 0,
                                             "online": False}):
            self.shell.paint_sync()
        self.assertTrue(self.shell.sync_chip.visible)
        self.assertEqual(self.shell.sync_text.value, "3 changes waiting")

    def test_a_parked_write_looks_different_from_a_queued_one(self):
        """Asserted on `.icon`, which is the property ft.Icon actually has.

        The control is not slotted, so writing `.name` binds a stray attribute
        and changes nothing on screen while reading back perfectly in a test.
        """
        with mock.patch.object(M, "sync_status",
                               return_value={"label": "1 change stuck", "parked": 1,
                                             "conflict": 0, "online": True}):
            self.shell.paint_sync()
        self.assertEqual(self.shell.sync_icon.icon, ft.Icons.CLOUD_OFF)
        with mock.patch.object(M, "sync_status",
                               return_value={"label": "1 change waiting", "parked": 0,
                                             "conflict": 0, "online": False}):
            self.shell.paint_sync()
        self.assertEqual(self.shell.sync_icon.icon, ft.Icons.CLOUD_UPLOAD)

    def test_the_arrange_button_only_appears_where_there_is_a_stack(self):
        """A button that does nothing is what the dead search icon was."""
        panels.set_editing(False)
        self.shell.render(APP_MAIN.BPM)          # BPM is not a panel surface
        bar = self.chrome().controls[0].content
        icons = [c.icon for c in bar.controls if isinstance(c, ft.IconButton)]
        self.assertNotIn(ft.Icons.DASHBOARD_CUSTOMIZE, icons)


# ------------------------------------------------------------------ navigation
class Navigation(ShellCase):
    def test_the_drawer_lists_every_surface(self):
        self.shell.attach()
        self.assertEqual(sorted(APP_MAIN.PAGES), sorted(APP_MAIN.LABELS))
        self.assertEqual(sorted(APP_MAIN.PAGES), sorted(APP_MAIN.ICONS))
        labels = self._drawer_labels()
        for key in APP_MAIN.PAGES:
            self.assertIn(APP_MAIN.LABELS[key], labels)

    def _drawer_labels(self):
        found = []

        def walk(control):
            value = getattr(control, "value", None)
            if isinstance(value, str):
                found.append(value)
            for child in (getattr(control, "controls", None) or []):
                walk(child)
            inner = getattr(control, "content", None)
            if inner is not None and not isinstance(inner, str):
                walk(inner)

        for row in self.shell.nav_drawer().controls:
            walk(row)
        return found

    def test_the_drawer_reaches_every_surface(self):
        """Walk the menu the way a thumb does: find the row, tap it, look."""
        for key in APP_MAIN.PAGES:
            with self.subTest(surface=key):
                self.shell.render(APP_MAIN.PAGES[0])
                row = self._row_for(APP_MAIN.LABELS[key])
                self.tap(row)
                self.assertEqual(self.shell.current, key,
                                 f"tapping {APP_MAIN.LABELS[key]} went nowhere")

    def _row_for(self, label):
        """The tappable container whose label is `label`."""
        for row in self.shell.nav_drawer().controls:
            for candidate in (row, *(getattr(getattr(row, "content", None),
                                             "controls", None) or [])):
                if getattr(candidate, "on_click", None) is None:
                    continue
                if label in self._labels_under(candidate):
                    return candidate
        self.fail(f"no drawer row labelled {label!r}")

    def _labels_under(self, control):
        out = []

        def walk(c):
            value = getattr(c, "value", None)
            if isinstance(value, str):
                out.append(value)
            for child in (getattr(c, "controls", None) or []):
                walk(child)
            inner = getattr(c, "content", None)
            if inner is not None and not isinstance(inner, str):
                walk(inner)

        walk(control)
        return out

    def test_the_drawer_sub_rows_match_the_pages_real_tabs(self):
        """The menu jumps to a tab by index, so the indices have to be right.

        Read back off the page itself rather than restated here: reorder the
        tabs in pages/foodzen.py and this is what notices.
        """
        captured = {}

        def fake_tabs(page, tabs, key=None):
            captured["labels"] = [label for label, _ in tabs]
            captured["key"] = key
            return ft.Text("tabs")

        with mock.patch("agedashii_foodzen.pages.foodzen._tabs", fake_tabs):
            APP_MAIN.foodzen.build(self.page)

        self.assertEqual(captured["key"], "foodzen",
                         "the drawer writes _TAB_INDEX['foodzen']")
        for label, index in APP_MAIN.SUBPAGES[APP_MAIN.FOODZEN]:
            with self.subTest(tab=label):
                self.assertEqual(captured["labels"][index], label)

    def test_a_sub_row_lands_on_its_tab(self):
        self.shell.render(APP_MAIN.FOODZEN)
        for label, index in APP_MAIN.SUBPAGES[APP_MAIN.FOODZEN]:
            with self.subTest(tab=label):
                _TAB_INDEX["foodzen"] = 0
                self.tap(self._row_for(label))
                self.assertEqual(self.shell.current, APP_MAIN.FOODZEN)
                self.assertEqual(_TAB_INDEX["foodzen"], index)

    def test_eatzen_and_bpm_are_not_listed_twice(self):
        """They are top-level pages here, so they are not also sub-rows."""
        sub_labels = [label for label, _ in APP_MAIN.SUBPAGES[APP_MAIN.FOODZEN]]
        self.assertNotIn("EatZen", sub_labels)
        self.assertNotIn("BPM", sub_labels)

    def test_the_chevron_folds_without_rebuilding_the_page(self):
        """A slow rebuild eating a tap is a bug this codebase has shipped."""
        self.shell.render(APP_MAIN.FOODZEN)
        built = self.shell.body.content
        chevron = self._chevron_for(APP_MAIN.FOODZEN)
        self.assertTrue(self.shell._section_open(APP_MAIN.FOODZEN))
        self.tap(chevron)
        self.assertFalse(self.shell._section_open(APP_MAIN.FOODZEN))
        self.assertIs(self.shell.body.content, built,
                      "folding the menu rebuilt the page body")

    def _chevron_for(self, key):
        for row in self.shell.nav_drawer().controls:
            children = getattr(getattr(row, "content", None), "controls", None) or []
            for child in children:
                icon = getattr(child, "content", None)
                if getattr(icon, "icon", None) in (ft.Icons.EXPAND_MORE,
                                                   ft.Icons.CHEVRON_RIGHT):
                    return child
        self.fail(f"no chevron for {key}")

    def test_a_folded_section_draws_no_sub_rows(self):
        self.shell.render(APP_MAIN.FOODZEN)
        self.shell._open[APP_MAIN.FOODZEN] = False
        labels = self._drawer_labels()
        for label, _ in APP_MAIN.SUBPAGES[APP_MAIN.FOODZEN]:
            self.assertNotIn(label, labels)

    def test_the_bottom_bar_reaches_every_surface(self):
        prefs.set_bottom_bar_enabled(True)
        self.shell.attach()
        for index, key in enumerate(APP_MAIN.PAGES):
            with self.subTest(surface=key):
                event = mock.Mock()
                event.control.selected_index = index
                asyncio.run(self.shell.bottom_pick(event))
                self.assertEqual(self.shell.current, key)

    def test_the_bottom_bar_highlights_where_you_are(self):
        prefs.set_bottom_bar_enabled(True)
        for index, key in enumerate(APP_MAIN.PAGES):
            self.shell.render(key)
            self.assertEqual(self.shell.bottom_bar().selected_index, index)


# --------------------------------------------------------------------- theming
class Theming(ShellCase):
    def test_the_theme_applies_to_the_body_and_the_drawer(self):
        for key in ("matrix", "white"):
            with self.subTest(theme=key):
                apply_theme(self.page, key)
                self.shell.render(self.shell.current)
                self.assertEqual(S.theme, key)
                self.assertEqual(self.shell.body.bgcolor, PAL()["bg"])
                self.assertEqual(self.shell.nav_drawer().bgcolor, PAL()["bg2"])

    def test_a_theme_change_repaints_the_page_you_are_on(self):
        """Not a bounce back to the first one - that loses your place."""
        self.shell.render(APP_MAIN.BPM)
        apply_theme(self.page, "matrix")
        self.shell.render(self.shell.current)
        self.assertEqual(self.shell.current, APP_MAIN.BPM)

    def test_the_picker_offers_every_theme_and_closes(self):
        self.shell.attach()
        self.shell.open_theme_picker()
        dlg = self.page.dialog
        self.assertIsNotNone(dlg, "the theme picker never opened")
        swatches = dlg.content.content.controls
        self.assertEqual(len(swatches), len(THEME_NAMES))
        dlg.actions[-1].on_click(None)
        self.assertIsNone(self.page.dialog)

    def test_picking_a_theme_applies_it(self):
        self.shell.attach()
        self.shell.open_theme_picker()
        keys = list(THEME_NAMES)
        target = keys[1] if keys[0] == S.theme else keys[0]
        row = dict(zip(keys, self.page.dialog.content.content.controls))[target]
        row.on_click(None)
        self.assertEqual(S.theme, target)

    def test_the_corner_radius_knob_reaches_this_app(self):
        """RAD() is what every rounded corner in the shell goes through."""
        saved = S.radius
        self.addCleanup(lambda: setattr(S, "radius", saved))
        S.radius = 0
        square = self.shell.brand_chip().border_radius
        S.radius = 16
        soft = self.shell.brand_chip().border_radius
        self.assertNotEqual(square, soft)


# ------------------------------------------------------------------ the rules
class TheRules(ShellCase):
    THIS_APP = APP / "agedashii_foodzen"
    FORBIDDEN = ("agedashii_tofu", "agedashii_moto", "agedashii_watudo")

    def _sources(self):
        return [APP / "main.py", *sorted(self.THIS_APP.rglob("*.py"))]

    @staticmethod
    def _imported_names(path):
        """Every module name this file imports, however it spells it.

        Parsed rather than grepped: the rule is about imports, and a docstring
        that explains *why* a package is not imported would fail a text scan
        for naming it. Relative imports are skipped - `from . import foodzen`
        is this app's own.
        """
        import ast
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names.add(node.module)
        return names

    @staticmethod
    def _string_literals(path):
        """Every string this file could actually *use*, docstrings excluded.

        A docstring is prose, the same as a comment: the surrounding code has
        no handle on it and cannot POST to it. Counting them would mean a file
        could not explain a rule without breaking it.
        """
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            first = (node.body or [None])[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                prose.add(id(first.value))
        return [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in prose]

    def test_no_cross_app_imports_in_the_source(self):
        for path in self._sources():
            imported = self._imported_names(path)
            for name in self.FORBIDDEN:
                with self.subTest(source=path.name, forbidden=name):
                    offenders = [m for m in imported
                                 if m == name or m.startswith(name + ".")]
                    self.assertEqual(
                        offenders, [],
                        f"{path} imports {offenders}; Foodzen stands on Qi and "
                        f"Washi and on nothing that belongs to another app")

    def test_nothing_reaches_another_app_by_name_at_runtime_either(self):
        """importlib and __import__ would slip past the import scan above."""
        for path in self._sources():
            for literal in self._string_literals(path):
                for name in self.FORBIDDEN:
                    if literal == name or literal.startswith(name + "."):
                        self.fail(f"{path} names {literal!r} as a string")

    def test_no_cross_app_imports_at_runtime(self):
        """Not just the text: render everything, then look at what got loaded."""
        self.shell.attach()
        for key in APP_MAIN.PAGES:
            self.shell.render(key)
        loaded = [m for m in sys.modules
                  if any(m == n or m.startswith(n + ".") for n in self.FORBIDDEN)]
        self.assertEqual(loaded, [], f"these were imported: {loaded}")

    def test_rendering_never_reaches_the_network(self):
        """Her FoodZen data has been destroyed once by a careless probe of a
        save endpoint. Painting a screen is a read of what is already held;
        it must not issue a request at all, let alone a write."""
        def refuse(method, path, **kw):
            raise AssertionError(f"a render issued {method} {path}")

        state = {"lists": [], "need": [], "pantry": {"items": []}, "recipes": [],
                 "purchases": [], "templates": [], "eatzen": [], "bpm": []}
        with mock.patch.object(M, "_request", refuse), \
             mock.patch.object(M, "foodzen_state", return_value=state), \
             mock.patch.object(M, "_get", return_value=None), \
             mock.patch.object(M, "bpm_readings", return_value=[]):
            for live in (False, True):
                with mock.patch.object(M, "STATUS_LIVE", lambda: live):
                    for key in APP_MAIN.PAGES:
                        with self.subTest(surface=key, live=live):
                            self.shell.render(key)

    def test_the_shell_names_no_save_endpoint(self):
        """Writes go through api's helpers, which queue through the outbox.

        No file in this app should be holding a save path as a string it could
        POST to. Literals only, not prose: pages/foodzen.py explains in a
        comment why the meal plan has no `kind` of its own, and a comment is
        not a request.
        """
        for path in self._sources():
            for literal in self._string_literals(path):
                with self.subTest(source=path.name):
                    self.assertNotIn("/api/foodzen/save", literal,
                                     f"{path} holds a save endpoint")

    def test_no_secret_looking_literals(self):
        needles = ("api_key", "apikey", "secret =", "password =", "token =")
        for path in self._sources():
            lowered = path.read_text(encoding="utf-8").lower()
            for needle in needles:
                with self.subTest(source=path.name, needle=needle):
                    self.assertNotIn(needle, lowered)


# --------------------------------------------------------------- the entry point
class EntryPoint(ShellCase):
    def test_headless_is_forced_by_the_environment(self):
        view, port = APP_MAIN._view_and_port({"AGEDASHII_HEADLESS": "1",
                                              "DISPLAY": ":0"})
        self.assertIsNone(view, "AGEDASHII_HEADLESS=1 must beat a display")
        self.assertEqual(port, 8552)

    def test_headless_when_there_is_no_display(self):
        view, port = APP_MAIN._view_and_port({})
        self.assertIsNone(view)
        self.assertEqual(port, 8552)

    def test_a_window_when_there_is_a_display(self):
        for env in ({"DISPLAY": ":0"}, {"WAYLAND_DISPLAY": "wayland-0"}):
            with self.subTest(env=env):
                view, port = APP_MAIN._view_and_port(env)
                self.assertEqual(view, ft.AppView.FLET_APP)
                self.assertEqual(port, 0, "an unpinned port lets two copies run")

    def test_the_port_can_be_pinned(self):
        _, port = APP_MAIN._view_and_port({"AGEDASHII_HEADLESS": "1",
                                           "AGEDASHII_PORT": "9001"})
        self.assertEqual(port, 9001)

    def test_the_headless_default_does_not_collide_with_the_dashboard(self):
        """Both are expected to be served off the same Pi at once."""
        _, port = APP_MAIN._view_and_port({"AGEDASHII_HEADLESS": "1"})
        self.assertNotEqual(port, 8550)

    def test_main_titles_the_window_and_paints(self):
        """The whole entry point, with storage and the probe stubbed out."""
        async def nothing(*a, **kw):
            return None

        with mock.patch.object(ft, "SharedPreferences", lambda: object()), \
             mock.patch.object(APP_MAIN.config, "load", nothing), \
             mock.patch.object(APP_MAIN.prefs, "load", nothing):
            asyncio.run(APP_MAIN.main(self.page))
        self.assertEqual(self.page.title, "Agedashii Foodzen")
        self.assertEqual(len(self.page.controls), 1)
        self.assertIsNotNone(self.page.theme, "apply_theme never ran")
        self.assertTrue(callable(getattr(S, "rerender", None)))

    def test_main_queues_a_drain_at_launch(self):
        """Launch is itself a resume: what the last session queued is still
        queued, and the store kept it across the process dying."""
        async def nothing(*a, **kw):
            return None

        with mock.patch.object(ft, "SharedPreferences", lambda: object()), \
             mock.patch.object(APP_MAIN.config, "load", nothing), \
             mock.patch.object(APP_MAIN.prefs, "load", nothing):
            asyncio.run(APP_MAIN.main(self.page))
        self.assertTrue(self.page.threads, "nothing tried to flush the outbox")


if __name__ == "__main__":
    unittest.main()
