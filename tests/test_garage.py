"""Garage: the record schema, the honest rendering of absent data, and the
tab indices the drawer jumps to.

Everything pinned here has been wrong in a shipped build at least once.
"""
import ast
import os
import unittest

from flet_app import api as M
from flet_app import panels
from flet_app.pages import garage


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _texts(control, out=None):
    """Every Text/Markdown value in a rendered control tree."""
    out = [] if out is None else out
    name = type(control).__name__
    if name == "Text":
        value = getattr(control, "value", None)
        if value not in (None, ""):
            out.append(str(value))
    elif name == "Markdown":
        out.append(str(getattr(control, "value", "")))
    elif name == "TextField":
        out.append("field:%s=%s" % (getattr(control, "hint_text", ""),
                                    getattr(control, "value", "")))
    for key in ("controls", "content", "actions", "title"):
        v = getattr(control, key, None)
        if isinstance(v, (list, tuple)):
            for y in v:
                if hasattr(y, "__dict__"):
                    _texts(y, out)
        elif v is not None and hasattr(v, "__dict__"):
            _texts(v, out)
    return out


def _tab_labels(module_file, key):
    """The tab labels a page module passes to _tabs(..., key=<key>).

    Read out of the AST rather than by building the page, because building
    it needs a server.
    """
    with open(os.path.join(ROOT, module_file)) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "_tabs":
            continue
        if not any(kw.arg == "key" and getattr(kw.value, "value", None) == key
                   for kw in node.keywords):
            continue
        listed = node.args[1]
        return [t.elts[0].value for t in listed.elts]
    return None


class RecordSchemaMatchesTheWebsite(unittest.TestCase):
    """The app wrote {label, odo, amount} for every type. Nothing reads it.

    These are the columns `garageRecordsTable` renders and the inputs
    `garageOpenRecord` builds in dashbored-web/app.js. A record written with
    any other key shows up on the website as a blank cell and contributes
    nothing to /api/garage/stats.
    """

    def test_the_three_schemas(self):
        self.assertEqual(M.GARAGE_RECORD_FIELDS["mileage"],
                         ("date", "odo", "gallons", "price_per_gal", "cost",
                          "notes"))
        self.assertEqual(M.GARAGE_RECORD_FIELDS["services"],
                         ("date", "odo", "type", "parts_cost", "labor_cost",
                          "notes"))
        self.assertEqual(M.GARAGE_RECORD_FIELDS["expenses"],
                         ("date", "category", "vendor", "amount", "notes"))

    def test_label_is_not_a_field_of_anything(self):
        for rtype, fields in M.GARAGE_RECORD_FIELDS.items():
            self.assertNotIn("label", fields, rtype)

    def test_amount_belongs_to_expenses_only(self):
        self.assertIn("amount", M.GARAGE_RECORD_FIELDS["expenses"])
        self.assertNotIn("amount", M.GARAGE_RECORD_FIELDS["mileage"])
        self.assertNotIn("amount", M.GARAGE_RECORD_FIELDS["services"])

    def test_every_record_type_the_server_accepts_has_a_schema(self):
        self.assertEqual(set(M.GARAGE_RECORD_FIELDS), set(M.GARAGE_RECORD_TYPES))


class CoerceRecord(unittest.TestCase):

    def test_blank_number_is_null_not_zero(self):
        rec, err = garage.coerce_record(
            "mileage", {"date": "2026-09-21", "odo": "10212", "gallons": "",
                        "price_per_gal": "", "cost": "", "notes": ""})
        self.assertIsNone(err)
        # gallons 0 would tell the server a fill-up of nothing happened, and
        # its mpg would be computed from it.
        self.assertIsNone(rec["gallons"])
        self.assertIsNone(rec["cost"])

    def test_whole_numbers_stay_whole(self):
        rec, _ = garage.coerce_record(
            "mileage", {"date": "2026-09-21", "odo": "10212", "gallons": "3.4"})
        self.assertEqual(rec["odo"], 10212)
        self.assertIsInstance(rec["odo"], int)
        self.assertEqual(rec["gallons"], 3.4)

    def test_a_word_in_a_number_box_is_refused_by_name(self):
        rec, err = garage.coerce_record(
            "services", {"date": "2026-09-21", "parts_cost": "twenty"})
        self.assertIsNone(rec)
        self.assertIn("Parts $", err)

    def test_a_date_on_its_own_is_not_a_record(self):
        rec, err = garage.coerce_record("expenses", {"date": "2026-09-21"})
        self.assertIsNone(rec)
        self.assertIn("nothing to record", err)

    def test_every_field_of_the_type_is_present(self):
        rec, _ = garage.coerce_record(
            "expenses", {"date": "2026-09-21", "category": "reg",
                         "vendor": "DMV", "amount": "91", "notes": ""})
        self.assertEqual(set(rec), set(M.GARAGE_RECORD_FIELDS["expenses"]))


class CoerceSpec(unittest.TestCase):
    """/api/garage/spec/save rewrites _BIKE.json whole - an absent key is a
    deleted key."""

    def test_every_key_is_sent_even_when_blank(self):
        data = garage.coerce_spec({"year": "1993", "make": "Honda"})
        self.assertEqual(set(data), set(M.GARAGE_SPEC_FIELDS))
        self.assertEqual(data["vin"], "")

    def test_numbers_stay_numbers(self):
        data = garage.coerce_spec({"year": "1993", "cc": "750",
                                   "odo_current": "10212"})
        self.assertEqual(data["year"], 1993)
        self.assertIsInstance(data["cc"], int)
        self.assertEqual(data["odo_current"], 10212)

    def test_an_unparseable_number_is_kept_as_typed(self):
        # The website does the same: a purchase price of "traded" survives.
        self.assertEqual(garage.coerce_spec({"purchase_price": "traded"}
                                            )["purchase_price"], "traded")


class AbsentRendersAsAbsent(unittest.TestCase):
    """Null is not zero. The old page printed "0 since svc" for a bike whose
    miles_since_service the server reports as null."""

    def test_none_and_blank_are_em_dashes(self):
        for renderer in (garage._dash, garage._int, garage._money):
            self.assertEqual(renderer(None), "—")
            self.assertEqual(renderer(""), "—")

    def test_a_real_zero_is_still_shown(self):
        self.assertEqual(garage._int(0), "0")
        self.assertEqual(garage._money(0), "$0.00")

    def test_thousands_separators(self):
        self.assertEqual(garage._int(10212), "10,212")
        self.assertEqual(garage._money(1234.5, digits=0), "$1,234")

    def test_stats_strip_with_everything_null(self):
        stats = {"total_miles": 0, "total_spent": 0.0, "mpg_avg": None,
                 "cost_per_mile": None, "miles_since_service": None,
                 "days_since_service": None, "mileage_count": 0,
                 "service_count": 0, "expense_count": 0}
        shown = _texts(garage._stats_strip("cb750-nighthawk-1993", stats))
        self.assertEqual(shown.count("—"), 6)
        self.assertNotIn("0 mi", shown)
        self.assertTrue(any("Nothing logged" in s for s in shown))

    def test_stats_strip_with_real_numbers(self):
        stats = {"total_miles": 10212.0, "total_spent": 113.5, "mpg_avg": None,
                 "cost_per_mile": 0.01, "miles_since_service": 0.0,
                 "days_since_service": 0, "mileage_count": 1,
                 "service_count": 1, "expense_count": 1}
        shown = _texts(garage._stats_strip("x", stats))
        self.assertIn("10,212", shown)
        self.assertIn("$114", shown)
        self.assertIn("$0.01", shown)
        # measured now, so a zero here is a measurement and must be shown
        self.assertIn("0 mi", shown)
        self.assertIn("0d", shown)
        # mpg still cannot be computed from one fill-up
        self.assertIn("—", shown)
        self.assertFalse(any("Nothing logged" in s for s in shown))


class RecordLinesShowEveryColumn(unittest.TestCase):
    """The website's tables have six columns; a phone row must not drop any."""

    def setUp(self):
        from flet_app.ui import PAL
        self.p = PAL()

    def test_mileage(self):
        shown = " ".join(_texts(garage._record_line(self.p, "mileage", {
            "date": "2026-09-21", "odo": 10212, "gallons": 3.4,
            "price_per_gal": 4.79, "cost": 16.29, "notes": "topped up"})))
        for bit in ("2026-09-21", "10,212", "3.4 gal", "$4.79", "$16.29",
                    "topped up"):
            self.assertIn(bit, shown)

    def test_services(self):
        shown = " ".join(_texts(garage._record_line(self.p, "services", {
            "date": "2026-09-21", "odo": 10212, "type": "oil",
            "parts_cost": 22.5, "labor_cost": 40, "notes": "and a filter"})))
        for bit in ("2026-09-21", "oil", "10,212", "$22.50", "$40.00",
                    "and a filter", "$62.50"):
            self.assertIn(bit, shown)

    def test_expenses(self):
        shown = " ".join(_texts(garage._record_line(self.p, "expenses", {
            "date": "2026-09-21", "category": "reg", "vendor": "DMV",
            "amount": 91.0, "notes": "two years"})))
        for bit in ("2026-09-21", "reg", "DMV", "$91.00", "two years"):
            self.assertIn(bit, shown)


class NotePaths(unittest.TestCase):
    """The tree reports a folder as "<slug>/Mods" and a file inside it as
    "Mods/x.md" - relative to the bike, not to the garage - and
    /api/garage/read wants the long form. Curled: the short form answers
    HTTP 404."""

    def test_the_missing_slug_is_put_back(self):
        self.assertEqual(
            garage._note_path("ex500f-ninja-2002", "Mods/2026-09-21_x.md"),
            "ex500f-ninja-2002/Mods/2026-09-21_x.md")

    def test_a_path_that_already_has_it_is_left_alone(self):
        full = "ex500f-ninja-2002/Mods/2026-09-21_x.md"
        self.assertEqual(garage._note_path("ex500f-ninja-2002", full), full)

    def test_a_slug_that_is_a_prefix_of_another_is_not_confused(self):
        # "ex250f-ninja-2005" starts with neither of these.
        self.assertEqual(garage._note_path("ex250f-ninja-2006",
                                           "ex250f-ninja-2005/Mods/x.md"),
                         "ex250f-ninja-2006/ex250f-ninja-2005/Mods/x.md")

    def test_empty(self):
        self.assertEqual(garage._note_path("bike", ""), "")


class FlipperMeta(unittest.TestCase):
    """"9 sources failed" with no detail is how it took a browser console to
    find out every Reddit source answers HTTP 429."""

    def setUp(self):
        from flet_app.ui import PAL
        self.p = PAL()

    def test_median_sources_and_grouped_errors(self):
        data = {"count": 639, "market_median": 8500,
                "sources_summary": {"craigslist:sfbay": 347,
                                    "craigslist:fresno": 19},
                "errors": {f"reddit:r{i}": "HTTP Error 429: Too Many Requests"
                           for i in range(9)}}
        shown = " ".join(_texts(garage._flipper_meta(self.p, data)))
        self.assertIn("639 listings", shown)
        self.assertIn("$8,500", shown)
        self.assertIn("craigslist:sfbay 347", shown)
        self.assertIn("9 sources failed", shown)
        self.assertIn("HTTP 429", shown)
        self.assertIn("reddit:r0", shown)

    def test_no_median_says_why(self):
        shown = " ".join(_texts(garage._flipper_meta(
            self.p, {"count": 2, "market_median": None})))
        self.assertIn("needs 5 priced listings", shown)


class DealCards(unittest.TestCase):

    def setUp(self):
        from flet_app.ui import PAL
        self.p = PAL()

    def test_a_scored_listing_shows_what_it_earned(self):
        shown = " ".join(_texts(garage._deal_card(self.p, {
            "source": "craigslist", "region": "modesto", "score": 95,
            "title": "1999 Harley Davidson Road King runs great",
            "price": 5000, "location": "Riverbank", "starts": True,
            "severity": "low", "repair_cost": 0, "profit": 3500,
            "net_profit": 2323, "est_hrs": 2, "pearl": True,
            "found": [], "suspected": [],
            "url": "https://example.invalid/x"})))
        for bit in ("95", "craigslist · modesto", "$5,000", "net +$2,323",
                    "gross +$3,500", "PEARL", "Riverbank", "runs", "~2h work"):
            self.assertIn(bit, shown)

    def test_a_not_a_deal_wears_no_score_and_no_profit(self):
        shown = " ".join(_texts(garage._deal_card(self.p, {
            "source": "craigslist", "region": "fresno", "score": None,
            "title": "Yuko oil top grade full synthetic", "price": 59,
            "profit": None, "net_profit": None, "pearl": False,
            "not_a_deal": "asking price $59 is not a real price"})))
        self.assertIn("not a deal", shown)
        self.assertIn("asking price $59 is not a real price", shown)
        self.assertNotIn("net", shown)
        self.assertNotIn("PEARL", shown)

    def test_a_dead_bike_says_no_start(self):
        shown = " ".join(_texts(garage._deal_card(self.p, {
            "source": "craigslist", "score": 40, "title": "project",
            "price": 300, "starts": False, "severity": "high",
            "repair_cost": 800, "profit": -100, "net_profit": -250,
            "found": ["bent forks"], "suspected": ["seized"]})))
        self.assertIn("NO START", shown)
        self.assertIn("repair ~$800", shown)
        self.assertIn("net -$250", shown)
        self.assertIn("bent forks", shown)
        self.assertIn("seized", shown)


class GarageSurfacesAreRegistered(unittest.TestCase):

    def test_every_stack_is_arrangeable(self):
        for surface in ("garage.home", "garage.mechanic", "garage.flipper"):
            self.assertIn(surface, panels.SURFACES)
            self.assertIn(surface, panels.SURFACE_LABELS)
            keys = panels.panel_keys(surface)
            self.assertTrue(keys)
            self.assertEqual(len(keys), len(set(keys)))


class DrawerReachesTheGarageAndWeatherTabs(unittest.TestCase):
    """SUBPAGES hardcodes tab indices. A tab reordered in the page module
    without updating them sends you silently to the wrong place."""

    def setUp(self):
        with open(os.path.join(ROOT, "main.py")) as fh:
            tree = ast.parse(fh.read())
        self.subpages = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "SUBPAGES" for t in node.targets):
                self.subpages = ast.literal_eval(node.value)
        self.assertIsNotNone(self.subpages)

    def _check(self, page_key, module_file):
        real = _tab_labels(module_file, page_key)
        self.assertIsNotNone(real, f"no _tabs(key={page_key!r}) in {module_file}")
        entries = self.subpages.get(page_key)
        self.assertIsNotNone(entries, f"{page_key} missing from SUBPAGES")
        self.assertEqual(len(entries), len(real))
        for label, path, _depth in entries:
            group, index = path[-1]
            self.assertEqual(group, page_key)
            self.assertLess(index, len(real),
                            f"{label}: index {index} past the end of {real}")
            self.assertEqual(real[index], label.split(" ")[0],
                             f"{label} points at {group}[{index}], which is "
                             f"{real[index]}")

    def test_garage(self):
        self._check("garage", "flet_app/pages/garage.py")

    def test_weather(self):
        self._check("weather", "flet_app/pages/weather.py")


if __name__ == "__main__":
    unittest.main()
