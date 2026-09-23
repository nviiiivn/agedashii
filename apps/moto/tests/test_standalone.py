"""Moto has to be shippable on its own, and these are the ways it would not be.

Two kinds of check live here.

**Boundaries.** The whole point of the split is that Moto builds without the
dashboard and without the other two apps. That is easy to believe and easy
to break: one `from agedashii_tofu.ui import PAL` left behind, or one page
importing a helper out of Wat2Do, and the build either fails or quietly
drags nine other pages and their network reads into a 3-surface app.

Both directions are checked - the source is read for a forbidden import
(which catches one inside a function, where a runtime check never looks)
and `sys.modules` is read after importing the app (which catches one that
arrives indirectly, through something Moto does import).

**Packaging.** `flet build` reads pyproject.toml and nothing else. A
missing permission is not a build error; it is a feature that fails at the
point of use, months later, with a message about a manifest. So the blocks
that have to be there are asserted here rather than trusted.
"""
import ast
import sys
import tomllib
import unittest
from pathlib import Path

from .support import APP_DIR, load_main

#: What this app must never reach for. `agedashii_tofu` is the dashboard's own
#: package - the shell Moto was modelled on and deliberately does not
#: import; the other two are the apps being split out alongside it.
FORBIDDEN = ("agedashii_tofu", "agedashii_watudo", "agedashii_foodzen")

#: What it is allowed to reach for, on purpose: the two shared packages.
EXPECTED_SHARED = ("agedashii_qi", "agedashii_washi")


def source_files():
    """Every .py file this app ships, entry point included."""
    return sorted(p for p in APP_DIR.rglob("*.py")
                  if "egg-info" not in p.parts and "__pycache__" not in p.parts)


def imported_modules(path):
    """Every top-level module name `path` imports, at any nesting depth.

    Walks the AST rather than the text, so a name that merely appears in a
    docstring or a comment - `agedashii_washi/api.py` has "Pages do
    `from .. import api`" in its own module docstring - is not mistaken for
    an import.
    """
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import: it cannot name another app.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


class ItDoesNotImportTheOtherApps(unittest.TestCase):

    def test_no_source_file_imports_the_dashboard_or_a_sibling_app(self):
        offenders = []
        for path in source_files():
            for name in imported_modules(path) & set(FORBIDDEN):
                offenders.append(f"{path.relative_to(APP_DIR)} imports {name}")
        self.assertEqual(offenders, [])

    def test_the_test_files_themselves_are_in_the_sweep(self):
        """Otherwise the check above could pass while the only thing proving
        the app works is wired to the dashboard."""
        swept = {p.name for p in source_files()}
        self.assertIn("main.py", swept)
        self.assertIn("test_standalone.py", swept)

    def test_the_shipped_package_does_not_even_mention_the_old_module_paths(self):
        """Comments are not imports, but they are directions.

        `xnav.py` pointed at `agedashii_tofu/tileserver.py` for months after that
        file stopped existing. A stale pointer in a docstring costs the next
        reader the same search every time.
        """
        offenders = []
        for path in source_files():
            if path.relative_to(APP_DIR).parts[0] == "tests":
                continue
            text = path.read_text()
            for name in FORBIDDEN:
                if name in text:
                    offenders.append(f"{path.relative_to(APP_DIR)} names {name}")
        self.assertEqual(offenders, [])

    def test_importing_the_app_does_not_pull_in_a_forbidden_package(self):
        """The source sweep cannot see an import that arrives second-hand.
        This one can: it loads the entry point and reads what is loaded."""
        for name in FORBIDDEN:
            sys.modules.pop(name, None)
        load_main()
        import agedashii_moto.pages.garage  # noqa: F401
        import agedashii_moto.pages.trips   # noqa: F401
        import agedashii_moto.pages.xnav    # noqa: F401
        for name in FORBIDDEN:
            self.assertNotIn(name, sys.modules,
                             f"{name} was loaded by importing Moto")

    def test_it_does_still_sit_on_the_two_shared_packages(self):
        """The inverse mistake: a copy of Qi and Washi vendored in here
        would make the app standalone and the look impossible to keep in
        step with the other two."""
        load_main()
        for name in EXPECTED_SHARED:
            self.assertIn(name, sys.modules)

    def test_the_entry_point_is_where_a_flet_build_looks_for_it(self):
        self.assertTrue((APP_DIR / "main.py").is_file())
        module = load_main()
        self.assertTrue(callable(module.main))

    def test_the_shipped_code_reaches_the_network_in_one_place_only(self):
        """Offline-first is the product, so this is worth pinning down.

        Themes, layout, preferences, the routing graph and the map tiles are
        local. The dashbored server's own data goes through
        `agedashii_washi.api`, which sits on the store's cache and outbox -
        reads answer from the device and writes queue.

        `_trips_parts` is the one exception and an honest one: Valhalla,
        Overpass and the height service are live third parties with nothing
        to queue and replay, so it talks to them directly and refuses with
        a reason when it cannot. Anything else opening a socket would be a
        feature that stops existing with the radio off.
        """
        allowed = {Path("agedashii_moto/pages/_trips_parts.py")}
        net = {"urllib", "socket", "requests", "httpx", "aiohttp", "http"}
        offenders = []
        for path in source_files():
            rel = path.relative_to(APP_DIR)
            if rel in allowed or rel.parts[0] == "tests":
                continue
            for name in sorted(imported_modules(path) & net):
                offenders.append(f"{rel} imports {name}")
        self.assertEqual(offenders, [])


class ItRunsHeadlessAndOnADesktop(unittest.TestCase):
    """The guard at the bottom of main.py, exercised rather than read.

    Hardcoding `view=None, port=8550` is right on the headless Pi and wrong
    everywhere else: it opens no window on a laptop, and a second copy dies
    with "address already in use" because the port is fixed. The guard is
    four lines and every one of them is a decision, so each is checked.
    """

    @classmethod
    def setUpClass(cls):
        tree = ast.parse((APP_DIR / "main.py").read_text())
        for node in tree.body:
            if (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and getattr(node.test.left, "id", None) == "__name__"):
                cls.guard = compile(ast.Module(body=node.body, type_ignores=[]),
                                    "main.py", "exec")
                return
        raise AssertionError("main.py has no `if __name__ == '__main__'` guard")

    def run_guard(self, **env):
        """Execute the guard with a stubbed flet, and report what it asked for."""
        import os
        from unittest import mock

        calls = []

        class FakeAppView:
            FLET_APP = "FLET_APP"

        fake_flet = type("ft", (), {
            "run": staticmethod(lambda *a, **k: calls.append(k)),
            "AppView": FakeAppView,
        })
        namespace = {"ft": fake_flet, "main": lambda page: None}
        with mock.patch.dict(os.environ, env, clear=True):
            exec(self.guard, namespace)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_a_display_gets_a_real_window(self):
        got = self.run_guard(DISPLAY=":0")
        self.assertEqual(got["view"], "FLET_APP")

    def test_wayland_counts_as_a_display_too(self):
        got = self.run_guard(WAYLAND_DISPLAY="wayland-0")
        self.assertEqual(got["view"], "FLET_APP")

    def test_no_display_serves_over_http_instead_of_opening_nothing(self):
        got = self.run_guard()
        self.assertIsNone(got["view"])

    def test_the_environment_variable_forces_headless_even_with_a_display(self):
        got = self.run_guard(DISPLAY=":0", AGEDASHII_HEADLESS="1")
        self.assertIsNone(got["view"])

    def test_headless_defaults_to_a_port_of_its_own(self):
        """Not 8550. The dashboard is already there, and two apps that
        cannot run at once are two apps that cannot be compared."""
        got = self.run_guard(AGEDASHII_HEADLESS="1")
        self.assertEqual(got["port"], 8552)

    def test_with_a_window_the_os_picks_the_port_so_two_copies_cannot_collide(self):
        got = self.run_guard(DISPLAY=":0")
        self.assertEqual(got["port"], 0)

    def test_the_port_can_be_pinned(self):
        got = self.run_guard(AGEDASHII_HEADLESS="1", AGEDASHII_PORT="8600")
        self.assertEqual(got["port"], 8600)


class ThePackagingIsComplete(unittest.TestCase):
    """`flet build` reads this file and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.toml = tomllib.loads((APP_DIR / "pyproject.toml").read_text())
        cls.flet = cls.toml.get("tool", {}).get("flet", {})

    def test_the_app_names_itself_rather_than_inheriting_the_directory_name(self):
        self.assertEqual(self.flet.get("product"), "Agedashii Moto")
        self.assertEqual(self.flet.get("org"), "online.iamprettyfamous")
        self.assertTrue(self.flet.get("company"))
        self.assertTrue(self.flet.get("copyright"))

    def test_the_bundle_id_is_its_own_so_it_installs_beside_the_dashboard(self):
        bundle = self.flet.get("bundle_id")
        self.assertEqual(bundle, "online.iamprettyfamous.agedashii.moto")
        # Not equal to the dashboard's, or installing one would replace the
        # other; still under it, so they read as one family.
        self.assertNotEqual(bundle, "online.iamprettyfamous.agedashii")
        self.assertTrue(bundle.startswith("online.iamprettyfamous.agedashii."))

    def test_the_location_permissions_a_ride_recorder_actually_needs(self):
        """All four, and the last two especially: without BACKGROUND and
        FOREGROUND_SERVICE_LOCATION the track stops when the screen locks,
        which is most of every ride."""
        perms = self.flet.get("android", {}).get("permission", {})
        for name in ("ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION",
                     "ACCESS_BACKGROUND_LOCATION",
                     "FOREGROUND_SERVICE_LOCATION"):
            with self.subTest(permission=name):
                self.assertIs(perms.get(f"android.permission.{name}"), True)

    def test_it_does_not_ask_for_permissions_it_has_no_use_for(self):
        """The camera belongs to FoodZen. An app that asks for something it
        never opens teaches its owner to wave prompts through."""
        perms = self.flet.get("android", {}).get("permission", {})
        for name in ("CAMERA", "READ_MEDIA_IMAGES", "RECORD_AUDIO"):
            self.assertNotIn(f"android.permission.{name}", perms)

    def test_gps_hardware_is_declared_as_not_required(self):
        """Declared `true` - or left to be inferred from the permission,
        which is what Android does - makes the app uninstallable on a device
        without GPS, for a feature that degrades perfectly well to network
        location."""
        features = self.flet.get("android", {}).get("feature", {})
        self.assertIs(features.get("android.hardware.location.gps"), False)
        self.assertIs(features.get("android.hardware.location.network"), False)

    def test_ios_has_a_usage_string_for_every_location_key_it_uses(self):
        """The App Store rejects a build that calls the API without one,
        and the string is shown to the user verbatim."""
        info = self.flet.get("ios", {}).get("info", {})
        for key in ("NSLocationWhenInUseUsageDescription",
                    "NSLocationAlwaysAndWhenInUseUsageDescription"):
            with self.subTest(key=key):
                text = info.get(key, "")
                self.assertTrue(text.endswith("."), f"{key} is not a sentence")
                self.assertIn("Agedashii Moto", text)
                self.assertGreater(len(text), 40)

    def test_the_background_string_says_it_keeps_reading_with_the_screen_off(self):
        """The Always prompt is the one people refuse. It has to say why."""
        info = self.flet.get("ios", {}).get("info", {})
        text = info["NSLocationAlwaysAndWhenInUseUsageDescription"].lower()
        self.assertIn("recording", text)
        self.assertIn("screen is off", text)

    def test_the_dependency_list_is_what_goes_into_the_bundle(self):
        """flet reads the TOML list when it is non-empty and only falls back
        to requirements.txt when it is empty. This list is non-empty, so
        anything missing from it is missing from the built app - and
        flet-map is imported behind a try/except, so leaving it out would
        not fail the build, it would ship an app with no map."""
        deps = self.toml["project"]["dependencies"]
        names = [d.split(">")[0].split("=")[0].split("<")[0].strip()
                 for d in deps]
        for needed in ("flet", "flet-map", "flet-geolocator",
                       "agedashii-washi", "agedashii-qi"):
            self.assertIn(needed, names)
        # flet-cli in the bundle would ship the build tool inside the app.
        self.assertNotIn("flet-cli", names)
        for name in names:
            self.assertNotIn(name, ("agedashii-watudo", "agedashii-foodzen"))

    def test_the_two_shared_packages_resolve_to_this_checkout(self):
        """Neither is on PyPI, so without this the build stops at "No
        matching distribution found for agedashii-washi"."""
        dev = self.flet.get("dev_packages", {})
        for name in ("agedashii-qi", "agedashii-washi"):
            with self.subTest(package=name):
                path = dev.get(name)
                self.assertIsNotNone(path, f"{name} has no dev_packages entry")
                # Resolved by flet against this app's own directory.
                self.assertTrue((APP_DIR / path).is_dir(),
                                f"{name} -> {path} is not a directory")
                self.assertTrue((APP_DIR / path / "pyproject.toml").is_file())

    def test_every_dev_package_is_also_a_declared_dependency(self):
        """A dev_packages entry only rewrites a dependency that is already
        in the list; one for a package nobody depends on does nothing."""
        names = {d.split(">")[0].split("=")[0].split("<")[0].strip()
                 for d in self.toml["project"]["dependencies"]}
        for name in self.flet.get("dev_packages", {}):
            self.assertIn(name, names)


class TheReadmeExists(unittest.TestCase):
    """A shippable app says what it is and how to run it."""

    @classmethod
    def setUpClass(cls):
        cls.text = (APP_DIR / "README.md").read_text()

    def test_it_says_how_to_run_it_and_how_to_build_it(self):
        self.assertIn("main.py", self.text)
        self.assertIn("flet build", self.text)

    def test_it_is_honest_about_what_needs_a_server(self):
        low = self.text.lower()
        self.assertIn("offline", low)
        self.assertIn("outbox", low)

    def test_it_points_at_the_tests_that_prove_the_shell(self):
        self.assertIn("apps/moto/tests", self.text)

    def test_it_documents_the_settings_this_app_now_carries(self):
        """It used to say "Moto has no Settings page", which was true and is
        not any more. A README that is wrong about the main thing that
        changed is worse than no README."""
        self.assertNotIn("Moto has no Settings page", self.text)
        low = self.text.lower()
        for word in ("settings", "hamburger", "bottom bar", "units", "theme"):
            self.assertIn(word, low)

    def test_it_does_not_claim_a_bug_that_has_since_been_fixed(self):
        """`forms.rebuilder` had a relative import past the top of its own
        package and raised the moment it was called. It does not now, and a
        known-issues section naming a fixed bug sends a reader hunting."""
        from agedashii_washi import forms

        source = Path(forms.__file__).read_text()
        self.assertNotIn("from .. import api", source)
        self.assertNotIn("from .. import api as M", self.text)


if __name__ == "__main__":
    unittest.main()
