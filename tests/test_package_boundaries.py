"""The rules the split depends on, enforced rather than remembered.

Every check here corresponds to a bug that actually happened during the
split and that NOTHING else caught: the packages imported, the apps built,
the suites passed, and the fault only appeared when a person tapped
something.

Run with the rest: `python -m unittest discover -s tests`.
"""
import ast
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

PACKAGES = {
    "agedashii_qi": ROOT / "packages/agedashii-qi/agedashii_qi",
    "agedashii_washi": ROOT / "packages/agedashii-washi/agedashii_washi",
}
APPS = {
    "agedashii_moto": ROOT / "apps/moto",
    "agedashii_watudo": ROOT / "apps/watudo",
    "agedashii_foodzen": ROOT / "apps/foodzen",
}


def _py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "build", "dist", ".venv")]
        for f in filenames:
            if f.endswith(".py"):
                yield pathlib.Path(dirpath) / f


def _imports(path):
    """(module, level, lineno) for every import in a file, nested ones too."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:                     # reported by its own test below
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield (node.module or "", node.level, node.lineno)
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield (a.name, 0, node.lineno)


class NoImportAboveAPackageRoot(unittest.TestCase):
    """A relative import past the top of a package raises when CALLED.

    agedashii_washi/forms.py carried `from .. import api as M` inside
    rebuilder(). It was correct at its old home, agedashii_tofu/pages/_forms.py,
    where api really was two levels up. As a top-level package there is no
    second level, so every call raised

        ImportError: attempted relative import beyond top-level package

    and because the line is INSIDE the function, nothing failed at import
    time. The packages imported. Three apps built. 758 + 62 + 42 tests
    passed. Every Save-then-repaint in all three apps was dead, and it
    would have been found by her, on her phone, after saving something.

    Depth is counted against the file's real position, so a legitimate
    `from ..pages import x` inside a nested subpackage still passes.
    """

    def test_no_relative_import_escapes_its_package(self):
        bad = []
        for pkg, root in {**PACKAGES, **APPS}.items():
            if not root.exists():
                continue
            for f in _py_files(root):
                # how deep this file sits below the package root
                depth = len(f.relative_to(root).parts) - 1
                for mod, level, line in _imports(f):
                    if level and level > depth + 1:
                        bad.append(
                            f"{f.relative_to(ROOT)}:{line} "
                            f"'from {'.' * level}{mod} import ...' "
                            f"escapes its package (depth {depth})")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


class TheSplitHoldsItsShape(unittest.TestCase):
    """Qi must not import Washi, and the apps must not import each other.

    A theme has no opinion about cards, and a card cannot be drawn without
    a theme, so the arrow points one way. If it ever points both ways the
    two stop being separable and the split is decorative.
    """

    def test_qi_does_not_import_washi(self):
        offenders = []
        for f in _py_files(PACKAGES["agedashii_qi"]):
            for mod, level, line in _imports(f):
                if mod.startswith("agedashii_washi"):
                    offenders.append(f"{f.relative_to(ROOT)}:{line} -> {mod}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_app_imports_another_app_or_home(self):
        forbidden_for = {
            name: {"agedashii_tofu"} | (set(APPS) - {name}) for name in APPS
        }
        offenders = []
        for name, root in APPS.items():
            if not root.exists():
                continue
            for f in _py_files(root):
                if "tests" in f.parts:       # a test may import anything
                    continue
                for mod, level, line in _imports(f):
                    top = mod.split(".")[0]
                    if top in forbidden_for[name]:
                        offenders.append(
                            f"{f.relative_to(ROOT)}:{line} -> {mod}")
        self.assertEqual(offenders, [], "\n".join(offenders))


class EveryModuleActuallyParses(unittest.TestCase):
    """A moved file with a mangled indent is a broken file.

    Rewriting the imports of thirty files by script produced exactly this
    once: a replacement kept the indentation of the line it replaced and
    left `from x import y` sitting inside a function body at the wrong
    depth. Python found it, but only when that module was first imported.
    """

    def test_all_package_and_app_modules_parse(self):
        bad = []
        for root in list(PACKAGES.values()) + list(APPS.values()):
            if not root.exists():
                continue
            for f in _py_files(root):
                try:
                    ast.parse(f.read_text())
                except SyntaxError as exc:
                    bad.append(f"{f.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
        self.assertEqual(bad, [], "\n".join(bad))


class NoExpandInsideAWrappingRow(unittest.TestCase):
    """`expand=True` inside `ft.Row(wrap=True)` is a layout bug, every time.

    A wrapping Row is a Flutter Wrap, and a Wrap hands its children
    UNBOUNDED main-axis constraints - that is how it decides where to
    break. An Expanded child of an unbounded box has no width to take a
    share of.

    It cost EatZen entirely. The food field was
    `ft.Container(expand=True, content=<filled TextField>)` inside a
    wrapping Row, so the field painted its grey fill over an effectively
    infinite rectangle: "What you ate" opened as a grey slab that ran off
    the bottom of the screen and could not be scrolled past, putting the
    totals, the sodium meter and the "Log it" button out of reach. Found
    on a real Android emulator, after the owner had reported it twice.

    Nothing catches this by rendering: the control tree is perfectly
    valid, the tests pass, and it only looks wrong on a screen.
    """

    def _offenders(self):
        out = []
        for root in ("agedashii_tofu", "apps", "packages"):
            base = ROOT / root
            if not base.exists():
                continue
            for f in _py_files(base):
                try:
                    tree = ast.parse(f.read_text())
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if (getattr(node.func, "attr", None)
                            or getattr(node.func, "id", None)) != "Row":
                        continue
                    if not any(k.arg == "wrap"
                               and getattr(k.value, "value", False) is True
                               for k in node.keywords):
                        continue
                    for kw in node.keywords:
                        if kw.arg != "controls" or not isinstance(kw.value, ast.List):
                            continue
                        for item in kw.value.elts:
                            if not isinstance(item, ast.Call):
                                continue
                            for k2 in item.keywords:
                                if (k2.arg == "expand"
                                        and getattr(k2.value, "value", None) is True):
                                    out.append(
                                        f"{f.relative_to(ROOT)}:{item.lineno} "
                                        f"{getattr(item.func, 'attr', '?')}"
                                        f"(expand=True) inside Row(wrap=True) "
                                        f"opened at line {node.lineno}")
        return out

    def test_no_expanded_child_of_a_wrapping_row(self):
        bad = self._offenders()
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


class TheRebuilderIsCallable(unittest.TestCase):
    """The specific thing that was broken, checked the specific way.

    The test above forbids the shape; this one proves the function works,
    because a rule and the thing it protects can drift apart.
    """

    def test_calling_rebuilder_does_not_raise_an_import_error(self):
        from agedashii_washi import forms
        try:
            forms.rebuilder(None, lambda *a: None)
        except ImportError as exc:                       # the bug
            self.fail(f"rebuilder() still cannot import its own package: {exc}")
        except Exception:
            pass    # anything else is the stub page, which is not the point


if __name__ == "__main__":
    unittest.main()
