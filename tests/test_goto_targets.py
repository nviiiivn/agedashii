"""Every jump on a page has to land somewhere that exists.

This is the test that was missing. Removing Wat2Do from Tofu left two
controls on Home pointing at it - a tile and a whole mini-card - and
nothing failed, because `_goto` returns None when there is no shell and a
tile with a None handler is skipped rather than drawn dead. The tests build
pages without a shell, so those tiles were never drawn and could not be
found to be broken.

It also catches the other kind, which was wrong from the start rather than
newly broken: Home sent Docker to Lab tab 4 and Qbit to tab 3, when Lab is
Machines(0), Network(1), Services(2), Docker(3), Qbit(4). Tapping Docker
opened Qbit for as long as those tiles existed.

Read off the source with `ast` rather than off a built tree, for exactly
that reason - what reaches the screen depends on a shell these tests do
not have.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.join(ROOT, "agedashii_tofu", "pages")

PAGE_FILES = {"home.py", "lab.py", "tools.py", "settings.py", "help.py"}
#: Which module owns each tab group, for checking an index against it.
TAB_SOURCE = {"lab": "lab.py", "tools": "tools.py"}


def _module_source(name):
    with open(os.path.join(PAGES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _main_constant(name):
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in main.py")


def _goto_calls(source):
    """Every `_goto("page", [("group", index), ...])` in a module."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_goto"
                and node.args):
            continue
        try:
            key = ast.literal_eval(node.args[0])
            path = ast.literal_eval(node.args[1]) if len(node.args) > 1 else []
        except ValueError:
            continue            # computed at runtime; nothing to check here
        out.append((key, list(path), node.lineno))
    return out


def _tab_labels(module_file, key):
    """The tab labels of a `_tabs(..., key=...)` call, in order."""
    for node in ast.walk(ast.parse(_module_source(module_file))):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_tabs"):
            continue
        named = {kw.arg: kw.value for kw in node.keywords}
        if "key" not in named or ast.literal_eval(named["key"]) != key:
            continue
        return [ast.literal_eval(e.elts[0]) for e in node.args[1].elts]
    return None


class EveryJumpLandsSomewhere(unittest.TestCase):
    def setUp(self):
        self.pages = (set(_main_constant("PAGES"))
                      | set(_main_constant("EXTRA_PAGES")))

    def test_every_goto_names_a_page_that_exists(self):
        for name in sorted(PAGE_FILES):
            for key, _path, line in _goto_calls(_module_source(name)):
                with self.subTest(module=name, line=line, key=key):
                    self.assertIn(
                        key, self.pages,
                        f"{name}:{line} jumps to {key!r}, which is not a page. "
                        f"A surface that leaves takes its jumps with it.")

    def test_every_goto_tab_index_is_the_tab_it_means(self):
        for name in sorted(PAGE_FILES):
            for key, path, line in _goto_calls(_module_source(name)):
                for group, index in path:
                    source = TAB_SOURCE.get(group)
                    if source is None:
                        continue
                    labels = _tab_labels(source, group)
                    with self.subTest(module=name, line=line, group=group):
                        self.assertIsNotNone(labels, f"no _tabs(key={group!r})")
                        self.assertLess(
                            index, len(labels),
                            f"{name}:{line} wants {group}[{index}], past the "
                            f"end of {labels}")

    def test_the_lab_tiles_on_home_open_what_they_say(self):
        """The specific bug, pinned by name rather than by shape."""
        labels = _tab_labels("lab.py", "lab")
        jumps = {}
        for node in ast.walk(ast.parse(_module_source("home.py"))):
            if not (isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "jumps" for t in node.targets)):
                continue
            for entry in node.value.elts:
                jumps[ast.literal_eval(entry.elts[0])] = (
                    ast.literal_eval(entry.elts[2]),
                    ast.literal_eval(entry.elts[3]))
        self.assertIn("Docker", jumps)
        self.assertIn("Qbit", jumps)
        for label in ("Docker", "Qbit"):
            key, path = jumps[label]
            self.assertEqual(key, "lab")
            self.assertEqual(labels[path[0][1]], label,
                             f"the {label} tile opens {labels[path[0][1]]}")


if __name__ == "__main__":         # pragma: no cover
    unittest.main()
