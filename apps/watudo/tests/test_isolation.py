"""Watudo must not reach back into the dashboard or the other two apps.

The point of splitting `agedashii_tofu` into Qi, Washi and three apps is that each
app can be built, shipped and broken on its own. One `from agedashii_tofu...` line
anywhere under `apps/watudo/` undoes that silently - the app still runs from
a full checkout and only fails when someone builds it from its own
directory, which is the one moment nobody is watching.

Two checks, because either alone can be fooled:

* a **static** walk of every import statement in every file under
  `apps/watudo/`, which is true regardless of what a test run happens to
  have imported first;
* a **runtime** check that building the whole shell leaves none of the
  forbidden packages in `sys.modules`, which catches an import hidden
  inside a function where the static walk would still find it but a lazy
  one-line `importlib` call would not.
"""
import ast
import sys
import unittest
from pathlib import Path

from .harness import APP_DIR, WatudoCase, load_main

# The dashboard, and the other two apps in this repo. Watudo may depend on
# `agedashii_qi` and `agedashii_washi` and on nothing else of ours.
FORBIDDEN = ("agedashii_tofu", "agedashii_moto", "agedashii_foodzen")


def python_files():
    return sorted(p for p in APP_DIR.rglob("*.py")
                  if ".egg-info" not in p.parts and "build" not in p.parts)


def imported_modules(path):
    """Every module name imported by `path`, at any depth, absolute only."""
    names = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


class NoFileNamesTheDashboardOrASiblingApp(unittest.TestCase):
    def test_there_are_files_to_check(self):
        """A glob that matched nothing would pass every test below."""
        found = python_files()
        self.assertGreaterEqual(len(found), 5, found)
        self.assertIn(APP_DIR / "main.py", found)

    def test_no_import_statement_names_a_forbidden_package(self):
        offenders = []
        for path in python_files():
            for name in imported_modules(path):
                root = name.split(".")[0]
                if root in FORBIDDEN:
                    offenders.append(f"{path.relative_to(APP_DIR)}: {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_app_depends_on_qi_and_washi_and_nothing_else_of_ours(self):
        """Stated positively, so a fourth in-repo package cannot creep in."""
        allowed = {"agedashii_qi", "agedashii_washi", "agedashii_watudo"}
        ours = set()
        for path in python_files():
            for name in imported_modules(path):
                root = name.split(".")[0]
                if root.startswith("agedashii") or root == "agedashii_tofu":
                    ours.add(root)
        self.assertTrue(ours <= allowed, sorted(ours - allowed))

    def test_the_entry_point_does_not_import_the_dashboards_main(self):
        """Both files are called main.py and the repo root is on sys.path
        during a test run, so this is a real way to load the wrong one."""
        source = (APP_DIR / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("import main", source)


class BuildingTheAppLoadsNoneOfThem(WatudoCase):
    def forbidden_now(self):
        return sorted(n for n in list(sys.modules)
                      if n.split(".")[0] in FORBIDDEN)

    def test_importing_the_entry_point_loads_none_of_them(self):
        for name in list(sys.modules):
            if name.split(".")[0] in FORBIDDEN:
                self.skipTest(f"{name} was already imported by another test")
        load_main()
        self.assertEqual(self.forbidden_now(), [])

    def test_building_the_whole_shell_loads_none_of_them(self):
        for name in list(sys.modules):
            if name.split(".")[0] in FORBIDDEN:
                self.skipTest(f"{name} was already imported by another test")
        self.build()
        self.assertEqual(self.forbidden_now(), [])


class TheBuildManifestIsHonest(unittest.TestCase):
    """`flet build` reads these tables and writes the manifest from them.

    A permission that is declared but not needed is a permission she is
    asked for; one that is needed and not declared fails at the point of use
    with a message about the manifest, which is exactly what happened to
    XNAV's "Record". Both are worth a test.
    """

    @classmethod
    def setUpClass(cls):
        import tomllib
        cls.toml = tomllib.loads(
            (APP_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        cls.flet = cls.toml.get("tool", {}).get("flet", {})

    def test_the_shared_packages_are_pointed_at_this_checkout(self):
        """They are not on PyPI. Without this table a build stops at
        "No matching distribution found for agedashii-washi"."""
        dev = self.flet.get("dev_packages", {})
        for name in ("agedashii-qi", "agedashii-washi"):
            self.assertIn(name, dev)
            self.assertTrue((APP_DIR / dev[name]).is_dir(),
                            f"{dev[name]} does not exist relative to {APP_DIR}")

    def test_the_map_plugin_is_in_the_list_that_actually_ships(self):
        """`flet build` installs [project] dependencies, not the root
        requirements.txt, whenever the list is non-empty. flet-map is
        imported behind a try/except, so leaving it out does not fail the
        build - it ships an app with no map and no complaint."""
        deps = " ".join(self.toml["project"]["dependencies"])
        self.assertIn("flet-map", deps)
        self.assertIn("agedashii-washi", deps)
        self.assertIn("agedashii-qi", deps)

    def test_the_product_is_named_and_the_bundle_id_is_pinned(self):
        self.assertEqual(self.flet.get("product"), "Agedashii Watudo")
        self.assertEqual(self.flet.get("org"), "online.iamprettyfamous")
        self.assertEqual(self.flet.get("bundle_id"),
                         "online.iamprettyfamous.agedashii.watudo")

    def test_no_permission_is_declared_at_all(self):
        """It declared foreground location for a year without one line of
        code that asked the device anything: every distance came from a
        fixed origin and `future.scan()`'s `centre` had no caller. A
        permission declared ahead of a feature is what teaches an owner to
        stop reading permission lists, so it is gone - and the origin is a
        setting she types instead, which works offline and prompts for
        nothing.

        Stated as "none", not "not these three", so a permission added for a
        feature that does not exist cannot slip back in under another name.
        """
        perms = self.flet.get("android", {}).get("permission", {})
        self.assertEqual(perms, {}, "declare it in the commit that uses it")

    def test_no_location_plugin_ships_either(self):
        """The dependency list is what `flet build` installs. A permission
        and a plugin have to arrive together or the app fails at the point
        of use with a message about the manifest."""
        deps = " ".join(self.toml["project"]["dependencies"])
        self.assertNotIn("geolocator", deps)

    def test_the_ios_usage_strings_make_no_claim_either(self):
        ios = self.flet.get("ios", {}).get("info", {})
        for key in ("NSLocationWhenInUseUsageDescription",
                    "NSLocationAlwaysAndWhenInUseUsageDescription"):
            self.assertNotIn(key, ios)

    def test_the_app_says_in_words_that_it_does_not_ask(self):
        """The README and the help surface both have to agree with the
        manifest, because a manifest is the one thing nobody reads."""
        readme = (APP_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("no location permission", readme.lower())
        from agedashii_watudo.pages import help as help_page
        said = " ".join(" ".join(body) for _i, _h, body in help_page.TOPICS)
        self.assertIn("does not ask the device where it is", said)

    def test_no_secret_is_written_down_anywhere_in_the_app(self):
        needles = ("api_key", "apikey", "secret", "password", "token=",
                   "Authorization:")
        offenders = []
        for path in python_files() + [APP_DIR / "pyproject.toml"]:
            text = path.read_text(encoding="utf-8").lower()
            for needle in needles:
                if f"{needle}=" in text.replace('"', "").replace(" ", ""):
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [], offenders)


if __name__ == "__main__":
    unittest.main()
