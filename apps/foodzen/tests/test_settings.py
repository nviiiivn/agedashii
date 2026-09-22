"""Settings, the guide, and the navigation she asked to be able to reshape.

"where is the settings? ... each of these independent apps needs their own
icons/labels/readme wikis INSIDE on how to use/settings menu etc."

A settings screen is only worth having if every control on it does the thing
it says. So each one here is driven the way a thumb drives it - find the
control by its own label, fire its handler, and then ask the *store*, not the
widget, what changed. A switch that flips visually and writes nothing is the
bug this file exists to catch, and it is the bug the dashboard's theme picker
has had since it was written: it applies a theme and stores nothing, so it is
back to the default at the next launch. `ThemeSurvivesARestart` is that,
stated as a test.

Everything runs with the radio off and against a fake storage backend. No
test here touches the network, and none of them can: the only server call
Settings makes at all is `config.set_url`, which is stubbed, and which probes
a read-only endpoint even when it is not.

Run them::

    /home/nvii/venvs/flet/bin/python -m unittest discover -s apps/foodzen/tests -t .
"""
import asyncio
import importlib.util
import pathlib
import sys
import threading
import types
import unittest
from unittest import mock

APP = pathlib.Path(__file__).resolve().parent.parent      # apps/foodzen
REPO = APP.parent.parent                                  # the checkout root
for path in (str(REPO), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import flet as ft                                          # noqa: E402
from agedashii_qi import DEFAULT, S, THEME_NAMES, apply_theme   # noqa: E402
from agedashii_washi import api as M                       # noqa: E402
from agedashii_washi import config, panels, prefs          # noqa: E402

from agedashii_foodzen import prefs_local                  # noqa: E402
from agedashii_foodzen.pages import settings as SET        # noqa: E402

MAIN_NAME = "agedashii_foodzen_main"


def _load_main():
    """apps/foodzen/main.py, by path and under a name of its own.

    Shared with test_shell when that module got there first: loading the same
    file twice would give two Shell classes and two module-level states, and
    a test that set a preference through one would be read by neither.
    """
    if MAIN_NAME in sys.modules:
        return sys.modules[MAIN_NAME]
    spec = importlib.util.spec_from_file_location(MAIN_NAME, APP / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP_MAIN = _load_main()


class FakePrefs:
    """As much of ft.SharedPreferences as the two stores use.

    A dict, deliberately - the point of these tests is what gets *written*,
    so the store has to be inspectable. It is also the only way to test a
    restart: load() against the same dict is exactly what a relaunch does.
    """

    def __init__(self, data=None):
        self.data = dict(data or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value
        return True


class FakePage:
    """The shell's FakePage, kept here so this file stands on its own."""

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


# ------------------------------------------------------------------- walking
def walk(root):
    """Every control in a built tree, once each."""
    seen, out = set(), []

    def go(control):
        if control is None or id(control) in seen:
            return
        seen.add(id(control))
        out.append(control)
        for child in (getattr(control, "controls", None) or []):
            go(child)
        for child in (getattr(control, "actions", None) or []):
            go(child)
        inner = getattr(control, "content", None)
        if inner is not None and "flet" in type(inner).__module__:
            go(inner)

    go(root)
    return out


def texts_under(control):
    return [c.value for c in walk(control)
            if isinstance(c, ft.Text) and isinstance(c.value, str)]


def switch_labelled(root, label):
    """The Switch that sits in the same row as `label`.

    Found by the words beside it rather than by position, because position
    is what a reordered card silently changes - and a test that then drove
    the wrong switch would pass while the app was broken.
    """
    for row in walk(root):
        if not isinstance(row, ft.Row):
            continue
        switches = [c for c in (row.controls or []) if isinstance(c, ft.Switch)]
        if switches and label in texts_under(row):
            return switches[0]
    raise AssertionError(f"no switch labelled {label!r}")


def dropdown_labelled(root, label):
    for c in walk(root):
        if isinstance(c, ft.Dropdown) and c.label == label:
            return c
    raise AssertionError(f"no dropdown labelled {label!r}")


def button_saying(root, text):
    for c in walk(root):
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) \
                and c.content == text:
            return c
    raise AssertionError(f"no button saying {text!r}")


def tapped(root, tooltip):
    for c in walk(root):
        if getattr(c, "tooltip", None) == tooltip and getattr(c, "on_click", None):
            return c
    raise AssertionError(f"nothing tappable with tooltip {tooltip!r}")


def fire(handler, value=None):
    """Fire a Flet handler, sync or async, with an event carrying `value`."""
    event = types.SimpleNamespace(
        control=types.SimpleNamespace(value=value, update=lambda: None),
        data=value if isinstance(value, str) else None)
    result = handler(event)
    if asyncio.iscoroutine(result):
        asyncio.run(result)


# ------------------------------------------------------------------ the case
class SettingsCase(unittest.TestCase):
    """An offline page, fake storage, and not one preference left changed."""

    def setUp(self):
        self._status = dict(config.STATUS)
        config.STATUS.update({"ok": False, "detail": "test: offline"})
        self.addCleanup(lambda: config.STATUS.update(self._status))

        before = (S.theme, prefs.corner_radius(), prefs.accent_glow(),
                  prefs.crt_overlay(), prefs.crt_intensity(),
                  prefs.bottom_bar_enabled(), prefs.sodium_target(),
                  prefs.unit_system(), prefs.refresh_seconds(),
                  prefs_local.theme(), prefs_local.hamburger_enabled(),
                  list(prefs.panel_order("foodzen.grocery")),
                  list(prefs.hidden_panels("foodzen.grocery")))

        def restore():
            (theme, radius, glow, crt, crt_i, bar, sodium, units, refresh,
             mine, menu, order, hidden) = before
            S.theme = theme
            prefs.set_corner_radius(radius)
            prefs.set_accent_glow(glow)
            prefs.set_crt_overlay(crt)
            prefs.set_crt_intensity(crt_i)
            prefs.set_bottom_bar_enabled(bar)
            prefs.set_sodium_target(sodium)
            prefs.set_unit_system(units)
            prefs.set_refresh_seconds(refresh)
            prefs_local.set_theme(mine)
            prefs_local.set_hamburger_enabled(menu)
            prefs.reset_panels("foodzen.grocery")
            prefs._V[prefs._pkey("foodzen.grocery", "order")] = order
            prefs._V[prefs._pkey("foodzen.grocery", "hidden")] = hidden

        self.addCleanup(restore)

        self._prefs_service = S.prefs
        self.store = FakePrefs()
        S.prefs = self.store
        self.addCleanup(lambda: setattr(S, "prefs", self._prefs_service))

        self._rerender = getattr(S, "rerender", None)
        self.addCleanup(lambda: setattr(S, "rerender", self._rerender))

        self._editing = panels.editing()
        self.addCleanup(lambda: panels.set_editing(self._editing))

        self.page = FakePage()
        self.shell = APP_MAIN.Shell(self.page)
        self.repaints = []

    def build(self):
        """The Settings surface, with the shell's callbacks recorded."""
        return SET.build(self.page,
                         on_theme=lambda: self.repaints.append("theme"),
                         on_layout=lambda: self.repaints.append("layout"),
                         on_help=lambda: self.repaints.append("help"))


# ------------------------------------------------------------- it is there
class ItExists(SettingsCase):
    def test_settings_is_a_surface_the_app_can_render(self):
        self.shell.render(APP_MAIN.SETTINGS)
        self.assertEqual(self.shell.current, APP_MAIN.SETTINGS)
        self.assertIsNotNone(self.shell.body.content)

    def test_settings_is_on_the_bottom_bar(self):
        """The bar is this app's navigation, so Settings rides it."""
        self.assertIn(APP_MAIN.SETTINGS, APP_MAIN.PAGES)
        bar = self.shell.bottom_bar()
        labels = [d.label for d in bar.destinations]
        self.assertIn(APP_MAIN.LABELS[APP_MAIN.SETTINGS], labels)

    def test_settings_is_in_the_menu_too(self):
        found = []
        for row in self.shell.nav_drawer().controls:
            found += texts_under(row)
        self.assertIn(APP_MAIN.LABELS[APP_MAIN.SETTINGS], found)

    def test_it_offers_the_cards_the_dashboard_does_and_the_two_it_owns(self):
        page = self.build()
        headings = texts_under(page)
        for heading in ("Server", "Navigation", "Theme", "Appearance", "EatZen",
                        "Units", "Data", "Card layout", "How this works",
                        "Diagnostics"):
            with self.subTest(card=heading):
                self.assertIn(heading, headings)

    def test_nothing_expands_inside_a_wrapping_row(self):
        """The grey slab, checked on this surface too.

        The theme picker is 24 fixed-width swatches in a Wrap, and a Wrap
        hands its children unbounded width - an Expanded child of one paints
        over the whole screen. This has shipped twice on EatZen.
        """
        offenders = []
        seen = set()

        def go(control, in_wrap):
            if control is None or id(control) in seen:
                return
            seen.add(id(control))
            if in_wrap and getattr(control, "expand", None):
                offenders.append(f"{type(control).__name__}(expand="
                                 f"{control.expand})")
            wraps = in_wrap or (isinstance(control, ft.Row)
                                and getattr(control, "wrap", False))
            for child in (getattr(control, "controls", None) or []):
                go(child, wraps)
            inner = getattr(control, "content", None)
            if inner is not None and "flet" in type(inner).__module__:
                go(inner, wraps)

        for surface in (self.build(),
                        APP_MAIN.guide.build(self.page, back=lambda: None)):
            go(surface, False)
        self.assertEqual(offenders, [], ", ".join(offenders))


# -------------------------------------------------------------- the server
class TheServerCard(SettingsCase):
    def test_check_probes_off_the_event_loop(self):
        """A three-second socket wait on Flet's loop drops every tap in it.

        That is what "the button does nothing" has meant in this codebase
        twice, so the probe is asserted to happen on another thread - not
        merely to happen.
        """
        seen = {}

        def fake_set_url(url):
            seen["worker"] = threading.current_thread().name
            return True, "connected"

        card = SET.server_card(self.page)
        button = button_saying(card, "Check")

        async def drive():
            seen["loop"] = threading.current_thread().name
            await button.on_click(None)

        with mock.patch.object(config, "set_url", fake_set_url):
            asyncio.run(drive())

        self.assertIn("worker", seen, "Check never probed at all")
        self.assertNotEqual(seen["worker"], seen["loop"],
                            "the probe ran on the event loop")

    def test_check_uses_the_address_in_the_field(self):
        got = []
        card = SET.server_card(self.page)
        field = [c for c in walk(card) if isinstance(c, ft.TextField)][0]
        field.value = "10.0.0.9:6900"
        with mock.patch.object(config, "set_url",
                               lambda url: got.append(url) or (True, "connected")):
            fire(button_saying(card, "Check").on_click)
        self.assertEqual(got, ["10.0.0.9:6900"])

    def test_save_persists_the_address(self):
        card = SET.server_card(self.page)
        with mock.patch.object(config, "set_url", lambda url: (True, "connected")), \
             mock.patch.object(config, "save",
                               mock.AsyncMock(return_value=True)) as saved:
            fire(button_saying(card, "Save").on_click)
        self.assertTrue(saved.called, "Save never wrote the address")

    def test_the_card_never_issues_a_request_of_its_own(self):
        """Only config's own read-only probe, and only when asked.

        Her FoodZen data has been destroyed once by a careless poke at a
        save endpoint. Building this card must not reach the wire at all.
        """
        with mock.patch.object(M, "_request",
                               mock.Mock(side_effect=AssertionError("requested"))):
            SET.server_card(self.page)


# --------------------------------------------------------------- the theme
class TheThemePicker(SettingsCase):
    def _swatch(self, key):
        return tapped(self.build(), THEME_NAMES[key])

    def _other_theme(self):
        keys = list(THEME_NAMES)
        return keys[1] if keys[0] == S.theme else keys[0]

    def test_every_one_of_the_24_is_offered(self):
        page = self.build()
        for key in THEME_NAMES:
            with self.subTest(theme=key):
                self.assertIsNotNone(tapped(page, THEME_NAMES[key]))

    def test_picking_one_applies_it_and_repaints_the_whole_app(self):
        target = self._other_theme()
        fire(self._swatch(target).on_click)
        self.assertEqual(S.theme, target)
        self.assertIn("theme", self.repaints,
                      "the theme reached this page and nothing else")

    def test_picking_one_writes_it_to_storage(self):
        target = self._other_theme()
        fire(self._swatch(target).on_click)
        self.assertEqual(prefs_local.theme(), target)
        self.assertEqual(self.store.data.get(prefs_local.KEY_THEME), target)

    def test_the_theme_survives_a_restart(self):
        """The bug the dashboard's picker has had since it was written.

        A restart is: the process dies, the cache goes back to the shipped
        defaults, and load() runs against the same storage. Nothing else is
        simulated because nothing else is involved.
        """
        target = self._other_theme()
        fire(self._swatch(target).on_click)

        prefs_local.reset()                      # the process died
        apply_theme(self.page, DEFAULT)
        self.assertNotEqual(S.theme, target, "the test proved nothing")

        asyncio.run(prefs_local.load(self.store))   # and came back
        apply_theme(self.page, prefs_local.theme())
        self.assertEqual(S.theme, target,
                         "the theme did not survive the restart")

    def test_the_topbar_picker_persists_it_too(self):
        """Two ways in, one behaviour. The quick picker used to only apply."""
        target = self._other_theme()
        self.shell.attach()
        self.shell.set_theme(target)
        self.assertEqual(prefs_local.theme(), target)

    def test_a_theme_that_no_longer_exists_reads_as_the_default(self):
        asyncio.run(prefs_local.load(FakePrefs({prefs_local.KEY_THEME: "burnt"})))
        self.assertEqual(prefs_local.theme(), DEFAULT)


# ---------------------------------------------------------- the appearance
class TheAppearanceCard(SettingsCase):
    def test_the_corner_radius_is_stored_and_applied(self):
        page = self.build()
        want = [n for n in prefs._radius_choices()[0] if n != prefs.corner_radius()][0]
        fire(dropdown_labelled(page, "Corner rounding").on_select, str(want))
        self.assertEqual(prefs.corner_radius(), want)
        from agedashii_qi import appearance as ui
        self.assertEqual(ui.S.radius, want, "stored but never applied")
        self.assertIn("theme", self.repaints)

    def test_the_glow_switch_flips_the_glow(self):
        prefs.set_accent_glow(False)
        fire(switch_labelled(self.build(), "Accent glow").on_change, True)
        self.assertTrue(prefs.accent_glow())
        fire(switch_labelled(self.build(), "Accent glow").on_change, False)
        self.assertFalse(prefs.accent_glow())

    def test_the_crt_switch_flips_the_overlay(self):
        prefs.set_crt_overlay(False)
        fire(switch_labelled(self.build(), "CRT overlay").on_change, True)
        self.assertTrue(prefs.crt_overlay())
        self.shell.render(APP_MAIN.SETTINGS)
        self.assertIsInstance(self.page.controls[0], ft.Stack,
                              "the overlay is on but was not drawn")

    def test_the_intensity_slider_writes_on_release_not_on_every_tick(self):
        prefs.set_crt_intensity(40)
        page = self.build()
        slider = [c for c in walk(page) if isinstance(c, ft.Slider)][0]
        fire(slider.on_change, 75)          # dragging
        self.assertEqual(prefs.crt_intensity(), 40,
                         "a drag wrote to storage on an intermediate tick")
        fire(slider.on_change_end, 75)      # let go
        self.assertEqual(prefs.crt_intensity(), 75)

    def test_the_intensity_slider_is_dead_while_the_overlay_is_off(self):
        prefs.set_crt_overlay(False)
        slider = [c for c in walk(self.build()) if isinstance(c, ft.Slider)][0]
        self.assertTrue(slider.disabled)


# ---------------------------------------------------------- the navigation
class TheNavigationCard(SettingsCase):
    def test_the_bottom_bar_can_be_switched_off_from_here(self):
        prefs.set_bottom_bar_enabled(True)
        fire(switch_labelled(self.build(), "Bottom bar").on_change, False)
        self.assertFalse(prefs.bottom_bar_enabled())
        self.shell.render(APP_MAIN.SETTINGS)
        chrome = self.page.controls[0].content
        self.assertEqual(len(chrome.controls), 2, "the bar was still drawn")

    def test_the_menu_trigger_is_off_until_she_asks_for_it(self):
        """Bottom-nav-first: "these apps work better with the bottom
        navigation bar and less with the hamburger menu"."""
        self.assertFalse(prefs_local.DEFAULT_HAMBURGER)

    def test_showing_the_menu_trigger_puts_it_in_the_topbar(self):
        prefs.set_bottom_bar_enabled(True)
        prefs_local.set_hamburger_enabled(False)
        fire(switch_labelled(self.build(), "Menu button").on_change, True)
        self.assertTrue(prefs_local.hamburger_enabled())
        self.shell.render(APP_MAIN.SETTINGS)
        topbar = self.page.controls[0].content.controls[0].content
        icons = [c.icon for c in topbar.controls if isinstance(c, ft.IconButton)]
        self.assertIn(ft.Icons.MENU, icons)

    def test_hiding_it_takes_the_slot_away_rather_than_leaving_a_gap(self):
        prefs.set_bottom_bar_enabled(True)
        prefs_local.set_hamburger_enabled(True)
        fire(switch_labelled(self.build(), "Menu button").on_change, False)
        self.assertFalse(prefs_local.hamburger_enabled())
        self.shell.render(APP_MAIN.SETTINGS)
        topbar = self.page.controls[0].content.controls[0].content
        self.assertNotIn(ft.Icons.MENU,
                         [c.icon for c in topbar.controls
                          if isinstance(c, ft.IconButton)])
        self.assertEqual(getattr(topbar.controls[0].content, "value", None),
                         "Foodzen")

    def test_with_the_menu_hidden_every_surface_is_still_reachable(self):
        """The whole point of a rail. Hidden menu, bar on: walk the bar."""
        prefs.set_bottom_bar_enabled(True)
        prefs_local.set_hamburger_enabled(False)
        self.shell.attach()
        for index, key in enumerate(APP_MAIN.PAGES):
            with self.subTest(surface=key):
                event = mock.Mock()
                event.control.selected_index = index
                asyncio.run(self.shell.bottom_pick(event))
                self.assertEqual(self.shell.current, key)
        # And the one surface that is not on the bar, through the screen
        # that owns it.
        self.shell.render(APP_MAIN.SETTINGS)
        fire(button_saying(self.shell.body.content, "Open the guide").on_click)
        self.assertEqual(self.shell.current, APP_MAIN.GUIDE)

    def test_with_the_bar_off_the_menu_trigger_comes_back_regardless(self):
        """Neither one would leave an app with no navigation at all - and no
        way to reach the screen where you would undo it."""
        prefs_local.set_hamburger_enabled(False)
        prefs.set_bottom_bar_enabled(False)
        self.assertTrue(prefs_local.hamburger_shown())
        self.shell.render(APP_MAIN.FOODZEN)
        topbar = self.page.controls[0].content.controls[0].content
        self.assertIn(ft.Icons.MENU, [c.icon for c in topbar.controls
                                      if isinstance(c, ft.IconButton)])

    def test_the_switch_says_so_instead_of_lying_about_it(self):
        prefs.set_bottom_bar_enabled(False)
        switch = switch_labelled(self.build(), "Menu button")
        self.assertTrue(switch.disabled,
                        "a switch that cannot take effect must not look live")


# -------------------------------------------------------------- the domain
class TheDomainSettings(SettingsCase):
    def test_the_sodium_target_is_this_apps_own_setting(self):
        prefs.set_sodium_target(2300)
        fire(dropdown_labelled(self.build(), "Daily sodium target").on_select, "1500")
        self.assertEqual(prefs.sodium_target(), 1500)

    def test_the_sodium_target_is_what_eatzen_starts_from(self):
        """Stored is not the same as used; EatZen reads it at build time."""
        prefs.set_sodium_target(1500)
        with mock.patch.object(M, "STATUS_LIVE", lambda: True), \
             mock.patch.object(M, "eatzen_parse", return_value={"items": []}), \
             mock.patch.object(M, "eatzen_entries", return_value=[]), \
             mock.patch.object(M, "eatzen_photo_url", return_value=None):
            built = APP_MAIN.eatzen.build_eatzen(self.page)
        targets = [c for c in walk(built) if isinstance(c, ft.Dropdown)]
        self.assertTrue(targets)
        self.assertEqual(targets[0].value, "1500")

    def test_units_are_stored(self):
        prefs.set_unit_system("imperial")
        fire(dropdown_labelled(self.build(), "Measure things in").on_select, "metric")
        self.assertEqual(prefs.unit_system(), "metric")

    def test_the_refresh_interval_reaches_the_api(self):
        prefs.set_refresh_seconds(30)
        fire(dropdown_labelled(self.build(), "Reread from the server").on_select, "300")
        self.assertEqual(prefs.refresh_seconds(), 300)
        self.assertEqual(M.TTL, 300, "stored but never applied")

    def test_a_junk_choice_leaves_the_setting_alone(self):
        """A dropdown reporting something unrecognised must not reset it."""
        prefs.set_sodium_target(1500)
        fire(dropdown_labelled(self.build(), "Daily sodium target").on_select, "9999")
        self.assertEqual(prefs.sodium_target(), 1500)


# ---------------------------------------------------------- the card layout
class TheCardLayout(SettingsCase):
    SURFACE = "foodzen.grocery"

    def test_it_only_offers_this_apps_own_stacks(self):
        self.assertTrue(SET.SURFACES)
        for key in SET.SURFACES:
            self.assertTrue(key.startswith("foodzen."),
                            f"{key} is not this app's to arrange")

    def test_a_card_can_be_moved(self):
        before = prefs.panel_order(self.SURFACE)
        page = self.build()
        fire(tapped(page, "Move down").on_click)
        self.assertNotEqual(prefs.panel_order(self.SURFACE), before)
        self.assertIn("layout", self.repaints)

    def test_a_card_can_be_hidden_and_the_page_stops_drawing_it(self):
        first = prefs.panel_order(self.SURFACE)[0]
        fire(tapped(self.build(), "Hide this card").on_click)
        self.assertIn(first, prefs.hidden_panels(self.SURFACE))
        self.assertNotIn(first, prefs.panels(self.SURFACE))


# -------------------------------------------------------------- the guide
class TheGuide(SettingsCase):
    def test_settings_opens_it(self):
        fire(button_saying(self.build(), "Open the guide").on_click)
        self.assertIn("help", self.repaints)

    def test_it_renders_and_comes_back(self):
        self.shell.render(APP_MAIN.GUIDE)
        self.assertEqual(self.shell.current, APP_MAIN.GUIDE)
        fire(button_saying(self.shell.body.content, "Back to Settings").on_click)
        self.assertEqual(self.shell.current, APP_MAIN.SETTINGS)

    def test_the_topbar_offers_a_way_back_even_with_the_menu_hidden(self):
        prefs_local.set_hamburger_enabled(False)
        self.shell.render(APP_MAIN.GUIDE)
        topbar = self.page.controls[0].content.controls[0].content
        back = [c for c in topbar.controls
                if isinstance(c, ft.IconButton) and c.icon == ft.Icons.ARROW_BACK]
        self.assertTrue(back, "no way off the guide")
        fire(back[0].on_click)
        self.assertEqual(self.shell.current, APP_MAIN.SETTINGS)

    def test_the_navigation_lights_up_settings_while_you_read_it(self):
        """Not "Grocery", which is where an unguarded index would land."""
        self.shell.render(APP_MAIN.GUIDE)
        self.assertEqual(self.shell.bottom_bar().selected_index,
                         APP_MAIN.PAGES.index(APP_MAIN.SETTINGS))

    def test_it_answers_the_questions_it_promises_to(self):
        """The four things the guide exists to say, checked as prose.

        Loose on wording and strict on subject: these are the questions
        that otherwise have no answer anywhere in the app.
        """
        words = " ".join(texts_under(APP_MAIN.guide.build(self.page))).lower()
        for topic in ("offline", "outbox", "vision model", "food table",
                      "estimated", "idk", "vpn"):
            with self.subTest(topic=topic):
                self.assertIn(topic, words)

    def test_it_does_not_claim_the_model_produces_nutrition_numbers(self):
        words = " ".join(texts_under(APP_MAIN.guide.build(self.page))).lower()
        self.assertIn("never states one", words)


# ------------------------------------------------------- the storage itself
class TheLocalStore(unittest.TestCase):
    def setUp(self):
        before = (prefs_local.theme(), prefs_local.hamburger_enabled())
        self.addCleanup(lambda: (prefs_local.set_theme(before[0]),
                                 prefs_local.set_hamburger_enabled(before[1])))

    def test_a_false_stored_as_the_string_false_is_still_false(self):
        """`bool("false")` is True, and that has already shipped here once -
        every switch in the app came back on after a restart."""
        asyncio.run(prefs_local.load(FakePrefs({prefs_local.KEY_HAMBURGER: "false"})))
        self.assertFalse(prefs_local.hamburger_enabled())

    def test_a_true_round_trips(self):
        store = FakePrefs()
        prefs_local.set_hamburger_enabled(True)
        asyncio.run(prefs_local.save(store))
        prefs_local.reset()
        asyncio.run(prefs_local.load(store))
        self.assertTrue(prefs_local.hamburger_enabled())

    def test_no_storage_at_all_is_not_an_error(self):
        self.assertFalse(asyncio.run(prefs_local.load(None)))
        self.assertFalse(asyncio.run(prefs_local.save(None)))

    def test_storage_that_refuses_costs_the_setting_and_nothing_else(self):
        class Angry:
            async def get(self, key):
                raise OSError("no")

            async def set(self, key, value):
                raise OSError("no")

        self.assertFalse(asyncio.run(prefs_local.load(Angry())))
        self.assertFalse(asyncio.run(prefs_local.save(Angry())))
        self.assertIn(prefs_local.theme(), THEME_NAMES)

    def test_the_keys_are_this_apps_own(self):
        """Namespaced, so a key added to the shared store later cannot
        collide with one of these."""
        for key in (prefs_local.KEY_THEME, prefs_local.KEY_HAMBURGER):
            self.assertTrue(key.startswith("agedashii.foodzen."))


if __name__ == "__main__":
    unittest.main()
