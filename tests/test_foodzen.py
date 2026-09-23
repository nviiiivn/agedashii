"""Tests for agedashii_tofu/foodzen.py — the write path shared with the website.

The app and the website both POST whole FoodZen collections to the same
server, which does no merging. So the app's records have to be shaped exactly
like the browser's, or the site renders them wrong and the mismatch is only
discovered later, in data.

`test_categoriser_matches_the_website_exactly` is the load-bearing one: it
re-runs the website's own fzAutoCat under node over every catalog key and
diffs it against the port. It skips if node is unavailable rather than
pretending to have checked.

Known and deliberate: the categoriser reproduces a real bug in the website.
fzAutoCat substring-matches in both directions in table order, so
"shampoo" -> Meat (contains "ham"), "tea" -> Meat (inside "steak"),
"bread" -> Frozen (inside "garlic bread"). Fixing that is a change to make in
BOTH clients at once; diverging here would put the same item in different
aisles depending on where it was added.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agedashii_tofu import foodzen as fz  # noqa: E402
from agedashii_tofu.foodzen_catalog import CATALOG  # noqa: E402

WEB_APP_JS = Path.home() / "www/dashbored/dashbored-web/app.js"


class TestHelpers(unittest.TestCase):
    def test_norm_matches_js_fznorm(self):
        self.assertEqual(fz.norm("  Greek  YOGURT! 2% "), "greek yogurt")
        self.assertEqual(fz.norm("AA batteries"), "aa batteries")
        self.assertEqual(fz.norm(None), "")
        self.assertEqual(fz.norm("123"), "")

    def test_title_case_matches_js(self):
        self.assertEqual(fz.title_case("oat milk"), "Oat Milk")
        self.assertEqual(fz.title_case("la croix"), "La Croix")
        self.assertEqual(fz.title_case(""), "")

    def test_auto_cat_defaults(self):
        self.assertEqual(fz.auto_cat(""), "Other")
        self.assertEqual(fz.auto_cat("zzzz nonsense"), "Pantry")

    def test_auto_cat_reproduces_the_websites_bug(self):
        # Not a typo. See the module docstring - parity beats correctness here.
        self.assertEqual(fz.auto_cat("shampoo"), "Meat")
        self.assertEqual(fz.auto_cat("tea"), "Meat")
        self.assertEqual(fz.auto_cat("bread"), "Frozen")

    def test_catalog_order_is_preserved_not_sorted(self):
        keys = list(CATALOG)
        self.assertNotEqual(keys, sorted(keys),
                            "catalog must keep JS insertion order - first match wins")


class TestCategoriserParity(unittest.TestCase):
    def test_categoriser_matches_the_website_exactly(self):
        if shutil.which("node") is None:
            self.skipTest("node not installed - cannot run the website's JS")
        if not WEB_APP_JS.is_file():
            self.skipTest(f"website source not found at {WEB_APP_JS}")

        js = WEB_APP_JS.read_text(encoding="utf-8")
        i = js.find("const FZ_CATALOG")
        j = js.find("};", i) + 2
        k = js.find("function fzNorm")
        m = js.find("function fzFillDatalist")
        self.assertNotEqual(i, -1, "FZ_CATALOG not found in the website source")
        self.assertNotEqual(k, -1, "fzNorm not found in the website source")

        names = list(CATALOG) + ["chicken", "pizza", "oat milk", "Greek Yogurt",
                                 "zzzz nonsense", "shampoo", "tea", "bread", "",
                                 "Cherry Tomatoes", "AA batteries", "la croix"]
        with tempfile.TemporaryDirectory() as td:
            harness = Path(td) / "cat.js"
            namefile = Path(td) / "names.json"
            harness.write_text(
                js[i:j] + "\n" + js[k:m] +
                "\nconst ns=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
                "const o={};for(const n of ns)o[n]=fzAutoCat(n);"
                "console.log(JSON.stringify(o));",
                encoding="utf-8")
            namefile.write_text(json.dumps(names), encoding="utf-8")
            out = subprocess.run(["node", str(harness), str(namefile)],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[:400])
            website = json.loads(out.stdout)

        mismatches = {n: (website[n], fz.auto_cat(n))
                      for n in names if website[n] != fz.auto_cat(n)}
        self.assertEqual(mismatches, {},
                         "app and website disagree on these categories")


class TestMutations(unittest.TestCase):
    def setUp(self):
        self.state = fz.normalise_state({})

    def test_normalise_fills_missing_collections(self):
        s = fz.normalise_state({"lists": None, "pantry": []})
        for k in ("templates", "lists", "need", "recipes", "purchases"):
            self.assertIsInstance(s[k], list)
        self.assertEqual(s["pantry"], {"items": []})

    def test_add_item_creates_the_first_list_with_the_website_shape(self):
        changed = fz.add_item(self.state, "bananas")
        self.assertEqual(changed, {"lists"})
        self.assertEqual(len(self.state["lists"]), 1)
        lst = self.state["lists"][0]
        self.assertEqual(set(lst), {"id", "name", "date", "items"})
        self.assertTrue(lst["id"].startswith("fz-l-"))
        self.assertTrue(lst["name"].startswith("grocery-list-"))
        self.assertRegex(lst["date"], r"^\d{4}-\d{2}-\d{2}$")
        item = lst["items"][0]
        self.assertEqual(set(item), {"name", "qty", "note", "cat", "done"})
        self.assertEqual(item, {"name": "Bananas", "qty": 1, "note": "",
                                "cat": "Produce", "done": False})

    def test_adding_twice_bumps_qty_rather_than_duplicating(self):
        fz.add_item(self.state, "bananas")
        fz.add_item(self.state, "  BANANAS ")
        items = self.state["lists"][0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 2)

    def test_blank_names_are_ignored(self):
        for bad in ("", "   ", "123", None):
            with self.subTest(bad=bad):
                self.assertEqual(fz.add_item(self.state, bad), set())
        self.assertEqual(self.state["lists"], [])

    def test_toggle_and_remove(self):
        fz.add_item(self.state, "milk")
        self.assertEqual(fz.set_done(self.state, "MILK", True), {"lists"})
        self.assertTrue(self.state["lists"][0]["items"][0]["done"])
        self.assertEqual(fz.remove_item(self.state, "milk"), {"lists"})
        self.assertEqual(self.state["lists"][0]["items"], [])

    def test_remove_missing_item_reports_no_change(self):
        fz.add_item(self.state, "milk")
        self.assertEqual(fz.remove_item(self.state, "not there"), set())

    def test_need_shape_and_dedupe(self):
        self.assertEqual(fz.add_need(self.state, "paper towels"), {"need"})
        n = self.state["need"][0]
        self.assertEqual(set(n), {"name", "qty", "note", "cat", "added"})
        self.assertIsInstance(n["added"], int)
        self.assertEqual(fz.add_need(self.state, "Paper Towels"), set())
        self.assertEqual(len(self.state["need"]), 1)

    def test_pantry_shape_and_touch(self):
        self.assertEqual(fz.add_pantry(self.state, "rice"), {"pantry"})
        item = self.state["pantry"]["items"][0]
        self.assertEqual(set(item), {"name", "cat", "updated"})
        first = item["updated"]
        time.sleep(0.002)
        fz.add_pantry(self.state, "RICE")
        self.assertEqual(len(self.state["pantry"]["items"]), 1)
        self.assertGreaterEqual(self.state["pantry"]["items"][0]["updated"], first)

    def test_need_to_list_moves_and_clears(self):
        fz.add_need(self.state, "eggs")
        fz.add_need(self.state, "butter")
        changed = fz.need_to_list(self.state)
        self.assertIn("need", changed)
        self.assertIn("lists", changed)
        self.assertEqual(self.state["need"], [])
        names = [i["name"] for i in self.state["lists"][0]["items"]]
        self.assertEqual(sorted(names), ["Butter", "Eggs"])

    def test_list_names_do_not_collide(self):
        fz.add_item(self.state, "milk")
        first = self.state["lists"][0]["name"]
        second = fz.new_list_name(self.state)
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("-2"))

    def test_state_stays_json_serialisable(self):
        fz.add_item(self.state, "milk")
        fz.add_need(self.state, "eggs")
        fz.add_pantry(self.state, "rice")
        json.dumps(self.state)  # must not raise


if __name__ == "__main__":
    unittest.main()


class TemplatesRecipesPurchases(unittest.TestCase):
    """The five collections the app never touched.

    Shapes are copied from the website (app.js fzScrapeSave, the purchase at
    app.js:7791, the template at app.js:7438) because both edit the same JSON
    files - a key spelled differently here shows up as a blank field there.
    """

    def setUp(self):
        self.s = fz.blank_state()
        fz.add_item(self.s, "eggs")
        fz.add_item(self.s, "milk")

    def test_template_round_trip(self):
        self.assertEqual(fz.template_save(self.s, "weekly"), {"templates"})
        tpl = self.s["templates"][0]
        self.assertEqual([i["name"] for i in tpl["items"]], ["Eggs", "Milk"])
        self.assertTrue(tpl["id"].startswith("fz-t-"))

        other = fz.blank_state()
        other["templates"] = self.s["templates"]
        fz.template_apply(other, tpl["id"])
        self.assertEqual([i["name"] for i in other["lists"][0]["items"]],
                         ["Eggs", "Milk"])

    def test_recipe_keeps_the_website_key_for_time(self):
        # The website writes time_min; a recipe saved from the app with a
        # different key would show no cooking time there.
        fz.recipe_save(self.s, {"title": "Carbonara", "time": 25,
                                "ingredients": ["spaghetti"]})
        r = self.s["recipes"][0]
        self.assertEqual(r["time_min"], 25)
        self.assertTrue(r["id"].startswith("fz-r-"))
        self.assertEqual(r["ingredients"], [{"qty": "", "name": "spaghetti"}])

    def test_recipe_to_list(self):
        fz.recipe_save(self.s, {"title": "x", "ingredients": [
            {"qty": "200g", "name": "spaghetti"}]})
        fz.recipe_to_list(self.s, self.s["recipes"][0]["id"])
        self.assertIn("Spaghetti",
                      [i["name"] for i in self.s["lists"][0]["items"]])

    def test_purchase_totals_and_categorises(self):
        fz.purchase_save(self.s, {"store": "Safeway", "items": [
            {"name": "Bananas", "qty": 2, "price": 0.99},
            {"name": "Milk", "qty": 1, "price": 4.49}]})
        pur = self.s["purchases"][0]
        self.assertEqual(pur["total"], 6.47)
        self.assertEqual([i["cat"] for i in pur["items"]], ["Produce", "Dairy"])
        self.assertTrue(pur["id"].startswith("fz-p-"))

    def test_a_stated_total_wins_over_the_arithmetic(self):
        # Receipts carry tax and deposits the line items do not.
        fz.purchase_save(self.s, {"total": 9.99, "items": [
            {"name": "Milk", "qty": 1, "price": 4.49}]})
        self.assertEqual(self.s["purchases"][0]["total"], 9.99)

    def test_refusals_say_why(self):
        for fn, arg, expect in (
            (fz.template_save, None, "empty"),
            (fz.template_apply, "nope", "gone"),
            (fz.recipe_to_list, "nope", "gone"),
            (fz.purchase_save, {"items": []}, "no lines"),
        ):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(ValueError) as cm:
                    fn(fz.blank_state(), arg)
                self.assertIn(expect, str(cm.exception))
