"""The arithmetic and the data shapes behind the Trips page.

Two things are being pinned down here, and only two.

**The shapes.** The app and the website write the same JSON files on the
same server. A key spelled `miles` where the website writes `distance_mi`,
or a report written as a flat list where the website writes
``{"route": [], "waypoints": {}}``, does not raise anywhere - it renders as
a blank field in the browser and an afternoon of tagging is gone. So every
payload builder here is compared against the shape the website's `teSave`
and `saveTripDialog` produce, key for key.

**The refusals.** Valhalla, Overpass and the height service are live third
parties; there is nothing to queue and replay. Each one must come back with
a reason when there is no connection rather than hanging or, worse,
answering with a plausible-looking zero.

The page itself is also built - offline, online, and in each of its states -
because a shape change in a control tree is a black tab and nothing else
notices.
"""
import json
import unittest

import flet as ft

from agedashii_tofu import api, panels, prefs
from agedashii_tofu.pages import _trips_parts as T

from test_api_offline import ApiOfflineCase


# A real /api/valhalla/route answer, trimmed to two maneuvers and a short
# shape. Captured from the live server on 2026-09-21 so the parser is tested
# against what Valhalla actually sends, not what it is remembered as sending.
VALHALLA = {
    "trip": {
        "summary": {"time": 1224.719, "length": 13.6155},
        "legs": [{
            "shape": "gycegB|oizdG??",
            "summary": {"time": 1224.719, "length": 13.6155},
            "maneuvers": [
                {"type": 3, "instruction": "Drive northwest on San Leandro Street.",
                 "street_names": ["San Leandro Street"], "time": 22.0,
                 "length": 0.0568, "begin_shape_index": 0, "end_shape_index": 1},
                {"type": 15, "instruction": "Turn left onto High Street.",
                 "street_names": ["High Street"], "time": 61.0,
                 "length": 0.5, "begin_shape_index": 1, "end_shape_index": 2},
            ],
        }],
    }
}

LEG = {
    "id": "alameda-loop", "name": "Alameda loop", "type": "leg",
    "waypoints": [
        {"name": "High St south", "lat": 37.769, "lon": -122.188, "note": "bridge"},
        {"name": "Shoreline Dr", "lat": 37.7681, "lon": -122.301, "note": ""},
    ],
}

FORK_CHILD = {
    "id": "chabot-loop", "name": "Chabot loop", "type": "leg",
    "fork_origin": "berkeley-hills-south",
    "waypoints": [{"name": "Skyline", "lat": 37.81, "lon": -122.18}],
}


class Grouping(unittest.TestCase):
    """The two hard-coded families, and the catch-all. The website's order."""

    def test_families_come_out_in_the_websites_order(self):
        trips = [{"id": "zzz-standalone", "name": "Zed"},
                 {"id": "lake-merritt-addon", "name": "Lake Merritt"},
                 {"id": "berkeley-hills-north", "name": "North"},
                 {"id": "alameda-loop", "name": "Alameda"},
                 {"id": "berkeley-hills-ridge", "name": "Ridge"}]
        groups = T.group_by_family(trips)
        self.assertEqual([label for label, _ in groups],
                         [T.BERKELEY_LABEL, T.ALAMEDA_LABEL, T.OTHER_LABEL])
        # The spine sorts first inside its family, the island loop inside its.
        self.assertEqual([t["id"] for _, items in groups[:1] for t in items],
                         ["berkeley-hills-ridge", "berkeley-hills-north"])
        self.assertEqual([t["id"] for t in groups[1][1]],
                         ["alameda-loop", "lake-merritt-addon"])

    def test_an_empty_family_is_not_shown_as_an_empty_heading(self):
        groups = T.group_by_family([{"id": "solo", "name": "Solo"}])
        self.assertEqual([label for label, _ in groups], [T.OTHER_LABEL])

    def test_nothing_at_all_is_no_groups_rather_than_a_crash(self):
        self.assertEqual(T.group_by_family(None), [])
        self.assertEqual(T.group_by_family([]), [])

    def test_a_row_that_is_not_a_dict_is_skipped(self):
        self.assertEqual(T.group_by_family(["junk", None, {"id": "a", "name": "A"}]),
                         [(T.OTHER_LABEL, [{"id": "a", "name": "A"}])])


class Waypoints(unittest.TestCase):
    def test_a_waypoint_without_real_coordinates_is_not_drawable(self):
        trip = {"waypoints": [{"name": "ok", "lat": 1.0, "lon": 2.0},
                              {"name": "no lat", "lon": 2.0},
                              {"name": "string", "lat": "37", "lon": "-122"},
                              "not a dict"]}
        self.assertEqual([w["name"] for w in T.waypoints_of(trip)], ["ok"])

    def test_a_plain_leg_is_wrapped_with_home_at_both_ends(self):
        wrapped = T.home_wrap(LEG)
        self.assertEqual(len(wrapped), 4)
        self.assertEqual(wrapped[0]["name"], T.HOME["name"])
        self.assertEqual(wrapped[0]["note"], "Start")
        self.assertEqual(wrapped[-1]["note"], "Return home")

    def test_a_fork_child_keeps_its_own_start_and_only_gets_a_way_home(self):
        """Prepending Home to a leg that begins at the fork point would route
        it from Oakland to the ridge and back, which is not the leg."""
        wrapped = T.home_wrap(FORK_CHILD)
        self.assertEqual(wrapped[0]["name"], "Skyline")
        self.assertEqual(wrapped[-1]["name"], T.HOME["name"])
        self.assertEqual(len(wrapped), 2)

    def test_a_leg_that_already_starts_and_ends_at_home_is_left_alone(self):
        leg = {"id": "x", "waypoints": [dict(T.HOME), {"name": "mid", "lat": 1.0,
                                                       "lon": 2.0}, dict(T.HOME)]}
        self.assertEqual(len(T.home_wrap(leg)), 3)

    def test_a_leg_with_no_waypoints_stays_empty_rather_than_becoming_home(self):
        self.assertEqual(T.home_wrap({"id": "x", "waypoints": []}), [])

    def test_home_wrap_does_not_mutate_the_trip_it_was_given(self):
        before = json.dumps(LEG)
        T.home_wrap(LEG)
        self.assertEqual(json.dumps(LEG), before)


class BuilderChain(unittest.TestCase):
    def test_the_chain_starts_and_ends_at_home_once(self):
        chain = T.builder_chain([LEG, FORK_CHILD])
        self.assertEqual(chain[0]["name"], T.HOME["name"])
        self.assertEqual(chain[-1]["name"], T.HOME["name"])
        self.assertEqual(sum(1 for w in chain if w["name"] == T.HOME["name"]), 2)

    def test_each_waypoint_is_stamped_with_the_leg_it_came_from(self):
        chain = T.builder_chain([LEG])
        middles = [w for w in chain if w.get("leg")]
        self.assertEqual({w["leg"] for w in middles}, {"alameda-loop"})
        self.assertEqual({w["leg_name"] for w in middles}, {"Alameda loop"})

    def test_a_repeated_stop_is_collapsed_so_the_router_does_not_choke(self):
        dupe = {"id": "d", "name": "D",
                "waypoints": [{"name": "a", "lat": 37.5, "lon": -122.0},
                              {"name": "a again", "lat": 37.5, "lon": -122.0},
                              {"name": "b", "lat": 37.6, "lon": -122.0}]}
        chain = T.builder_chain([dupe])
        self.assertEqual([w["name"] for w in chain],
                         [T.HOME["name"], "a", "b", T.HOME["name"]])

    def test_close_loop_adds_a_leg_back_to_the_first_waypoint(self):
        leg, why = T.close_loop_leg([LEG], now=1700000000)
        self.assertEqual(why, "")
        self.assertEqual(leg["waypoints"][0]["lat"], 37.769)
        self.assertTrue(leg["_close_loop"])
        self.assertEqual(leg["id"], "_closeloop_1700000000")

    def test_close_loop_says_why_when_it_cannot(self):
        self.assertEqual(T.close_loop_leg([])[1], "nothing stacked yet")
        self.assertEqual(T.close_loop_leg([{"id": "x", "waypoints": []}])[1],
                         "the first leg has no waypoints")
        closed, _ = T.close_loop_leg([LEG], now=1)
        self.assertEqual(T.close_loop_leg([LEG, closed])[1], "loop already closed")


class Slugs(unittest.TestCase):
    def test_the_slug_rule_matches_the_websites(self):
        self.assertEqual(T.slugify("Berkeley Hills — RIDGE (spine)"),
                         "berkeley-hills-ridge-spine")
        self.assertEqual(T.slugify("  --- "), "")
        self.assertEqual(T.slugify(None), "")


class ReportShapes(unittest.TestCase):
    """`report` is the field most likely to be quietly written wrong."""

    def test_a_legacy_flat_list_is_read_as_whole_route_tags(self):
        got = T.normalise_report([{"label": "gravel", "note": "mile 3"}])
        self.assertEqual(got, {"route": [{"label": "gravel", "note": "mile 3"}],
                               "waypoints": {}})

    def test_waypoint_keys_come_back_as_ints_and_go_out_as_strings(self):
        """JSON has no integer keys, so the on-disk form is "0", "1", ... and
        anything comparing them to a Python int silently never matches."""
        norm = T.normalise_report({"route": [], "waypoints": {"2": [{"label": "cops"}]}})
        self.assertEqual(list(norm["waypoints"]), [2])
        self.assertEqual(list(T.report_for_save(norm)["waypoints"]), ["2"])

    def test_junk_survives_without_taking_the_editor_down(self):
        for junk in (None, "nope", 42, {"waypoints": {"x": [1]}},
                     {"route": "no", "waypoints": []}):
            with self.subTest(junk=junk):
                got = T.normalise_report(junk)
                self.assertEqual(sorted(got), ["route", "waypoints"])

    def test_an_emptied_waypoint_is_dropped_rather_than_saved_as_an_empty_list(self):
        norm = {"route": [], "waypoints": {1: [], 2: [{"label": "cops"}]}}
        self.assertEqual(T.report_for_save(norm)["waypoints"], {"2": [{"label": "cops"}]})

    def test_the_card_shows_one_badge_per_label_not_one_per_waypoint(self):
        trip = {"report": {"route": [{"label": "gravel", "note": ""}],
                           "waypoints": {"0": [{"label": "bumpy", "note": "a"}],
                                         "1": [{"label": "bumpy", "note": "b"}]}}}
        rows = T.flatten_report(trip)
        self.assertEqual([r["label"] for r in rows], ["gravel", "bumpy"])
        self.assertEqual(rows[1]["scope"], "wp 1")

    def test_deleting_a_waypoint_shifts_every_tag_after_it_down(self):
        rep = {"route": [], "waypoints": {0: [{"label": "a"}], 1: [{"label": "b"}],
                                          2: [{"label": "c"}]}}
        got = T.delete_report_index(rep, 1)
        self.assertEqual({k: v[0]["label"] for k, v in got["waypoints"].items()},
                         {0: "a", 1: "c"})

    def test_moving_a_waypoint_carries_its_tags_with_it(self):
        rep = {"route": [], "waypoints": {0: [{"label": "a"}], 1: [{"label": "b"}],
                                          2: [{"label": "c"}]}}
        got = T.move_report_index(rep, 2, 0)
        self.assertEqual({k: v[0]["label"] for k, v in got["waypoints"].items()},
                         {0: "c", 1: "a", 2: "b"})

    def test_adding_to_several_scopes_writes_to_every_one(self):
        rep = T.add_report_entries({"route": [], "waypoints": {}},
                                   ["route", 1, 3], ["cops", "deer"], "watch out")
        self.assertEqual([r["label"] for r in rep["route"]], ["cops", "deer"])
        self.assertEqual(sorted(rep["waypoints"]), [1, 3])
        self.assertEqual(rep["waypoints"][3][0]["note"], "watch out")

    def test_removing_takes_it_out_of_every_selected_scope(self):
        rep = T.add_report_entries({"route": [], "waypoints": {}}, [1, 2], ["cops"], "")
        rep = T.remove_report_entries(rep, [1, 2], "cops", "")
        self.assertEqual(rep["waypoints"], {})

    def test_the_rows_shown_name_which_scopes_each_tag_is_on(self):
        rep = T.add_report_entries({"route": [], "waypoints": {}}, [0, 1], ["fun"], "")
        rows = T.report_rows(rep, [0, 1])
        self.assertEqual(rows, [{"label": "fun", "note": "", "scopes": [0, 1]}])

    def test_the_scope_label_reads_the_way_the_website_words_it(self):
        self.assertEqual(T.scope_label(["route"]), "whole-route")
        self.assertEqual(T.scope_label([0]), "wp 1")
        self.assertEqual(T.scope_label([0, 2]), "2 wps (1,3)")


class Tags(unittest.TestCase):
    def setUp(self):
        self._old = dict(prefs._V)
        self.addCleanup(lambda: (prefs._V.clear(), prefs._V.update(self._old)))
        prefs._V[prefs.KEY_TRIP_TAGS] = []

    def test_all_twenty_seven_presets_are_present_with_the_websites_labels(self):
        labels = [t["label"] for t in T.default_tags()]
        self.assertEqual(len(labels), 27)
        for expected in ("heavy potholage", "no cell", "cops", "sweepers",
                         "slippery when wet", "blind curves"):
            self.assertIn(expected, labels)

    def test_every_preset_has_an_icon_the_app_actually_ships(self):
        for tag in T.default_tags():
            with self.subTest(tag=tag["label"]):
                self.assertIsNotNone(T.tag_icon(tag["icon"]))
                self.assertNotEqual(T.tag_icon(tag["icon"]), ft.Icons.LABEL,
                                    "every preset should map to its own icon")

    def test_a_custom_tag_joins_the_gallery_and_can_be_taken_back_out(self):
        self.assertTrue(prefs.add_trip_tag("BIG POTHOLES", "danger", "warning"))
        labels = [t["label"] for t in T.all_tags()]
        self.assertIn("big potholes", labels)
        self.assertTrue(T.find_tag("big potholes")["custom"])
        self.assertEqual(T.find_tag("big potholes")["severity"], "danger")
        self.assertTrue(prefs.remove_trip_tag("big potholes"))
        self.assertNotIn("big potholes", [t["label"] for t in T.all_tags()])

    def test_a_duplicate_or_empty_custom_tag_is_refused(self):
        prefs.add_trip_tag("mud")
        self.assertFalse(prefs.add_trip_tag("mud"))
        self.assertFalse(prefs.add_trip_tag("  "))

    def test_a_corrupt_stored_tag_costs_that_tag_and_nothing_else(self):
        prefs._V[prefs.KEY_TRIP_TAGS] = ["good|warn|bolo", "no-pipes", 42, None,
                                         "weird|nonsense|nonsense"]
        got = prefs.trip_tags()
        self.assertEqual(got, [("good", "warn", "bolo"),
                               ("weird", "info", "attribute")])

    def test_an_unknown_label_still_renders_rather_than_raising(self):
        tag = T.find_tag("something she typed last year")
        self.assertEqual(tag["severity"], "info")
        self.assertEqual(T.tag_icon(tag["icon"]), ft.Icons.LABEL)


class SavePayloads(unittest.TestCase):
    """What actually lands in ~/www/dashbored/dashbored-trips/<id>.json."""

    def test_the_editor_writes_the_websites_field_names(self):
        out = T.editor_payload(LEG, "Alameda loop", LEG["waypoints"],
                               {"route": [], "waypoints": {}},
                               stats={"distance_mi": 22.3, "duration_min": 61})
        for key in ("id", "name", "type", "waypoints", "report", "tags",
                    "distance_mi", "duration_min"):
            self.assertIn(key, out)
        self.assertNotIn("miles", out)
        self.assertNotIn("date", out)
        self.assertNotIn("stops", out)
        self.assertEqual(out["report"], {"route": [], "waypoints": {}})
        self.assertEqual(out["distance_mi"], 22.3)

    def test_a_blank_fork_field_is_removed_rather_than_written_empty(self):
        original = dict(LEG, forks_at="Tilden", fork_origin="x")
        out = T.editor_payload(original, "n", LEG["waypoints"], None, "", "")
        self.assertNotIn("forks_at", out)
        self.assertNotIn("fork_origin", out)
        kept = T.editor_payload(original, "n", LEG["waypoints"], None,
                                " Tilden ", "berkeley-hills-ridge")
        self.assertEqual(kept["forks_at"], "Tilden")
        self.assertEqual(kept["fork_origin"], "berkeley-hills-ridge")

    def test_a_new_route_gets_a_slug_with_a_short_suffix(self):
        out = T.editor_payload({}, "Sunday Ridge Run", LEG["waypoints"], None,
                               is_new=True)
        self.assertTrue(out["id"].startswith("sunday-ridge-run-"), out["id"])
        self.assertEqual(len(out["id"]), len("sunday-ridge-run-") + 4)

    def test_an_edit_keeps_the_fields_the_editor_does_not_touch(self):
        rich = dict(LEG, biome="Flat urban island", difficulty="easy",
                    tags=["alameda"])
        out = T.editor_payload(rich, "Alameda loop", LEG["waypoints"], None)
        self.assertEqual(out["biome"], "Flat urban island")
        self.assertEqual(out["difficulty"], "easy")
        self.assertEqual(out["tags"], ["alameda"])
        self.assertEqual(out["id"], "alameda-loop")

    def test_the_builder_stamps_home_at_both_ends_and_numbers_the_legs(self):
        out = T.builder_payload([LEG, FORK_CHILD], "Big day", "big-day")
        self.assertEqual(out["waypoints"][0]["name"], T.HOME["name"])
        self.assertEqual(out["waypoints"][-1]["name"], T.HOME["name"])
        legs = [w.get("leg") for w in out["waypoints"] if w.get("leg")]
        self.assertEqual(legs, [1, 1, 2])
        self.assertEqual(out["legs"], ["alameda-loop", "chabot-loop"])
        self.assertEqual(out["type"], "loop")

    def test_unmeasured_totals_are_written_as_zero_not_as_a_guess(self):
        out = T.builder_payload([LEG], "x", "x")
        self.assertEqual(out["distance_mi"], 0.0)
        self.assertEqual(out["duration_min"], 0)

    def test_the_chained_route_beats_the_sum_of_per_leg_round_trips(self):
        """Each per-leg route is home-wrapped, so summing them counts the ride
        home once per leg. The chained route is the one actually measured."""
        legs = {"alameda-loop": {"ok": True, "distance_mi": 30.0, "duration_min": 60},
                "chabot-loop": {"ok": True, "distance_mi": 40.0, "duration_min": 80}}
        summed = T.builder_payload([LEG, FORK_CHILD], "x", "x", legs)
        self.assertEqual(summed["distance_mi"], 70.0)
        chained = T.builder_payload([LEG, FORK_CHILD], "x", "x", legs,
                                    combined={"ok": True, "distance_mi": 51.2,
                                              "duration_min": 103})
        self.assertEqual(chained["distance_mi"], 51.2)
        self.assertEqual(chained["duration_min"], 103)

    def test_tags_from_every_leg_merge_without_repeating(self):
        a = dict(LEG, tags=["ridge", "twisties"])
        b = dict(FORK_CHILD, tags=["twisties", "chabot"])
        self.assertEqual(T.builder_payload([a, b], "x", "x")["tags"],
                         ["ridge", "twisties", "chabot"])


class RouteParsing(unittest.TestCase):
    def test_a_real_valhalla_answer_becomes_miles_minutes_and_turns(self):
        got = T.parse_route(VALHALLA)
        self.assertTrue(got["ok"])
        self.assertEqual(got["distance_mi"], 13.6)
        self.assertEqual(got["duration_min"], 20)
        self.assertEqual([m["instruction"] for m in got["maneuvers"]],
                         ["Drive northwest on San Leandro Street.",
                          "Turn left onto High Street."])
        self.assertEqual(got["maneuvers"][1]["street"], "High Street")

    def test_elevation_valhalla_never_sent_is_absent_not_flat(self):
        """`/route` carries no elevation unless it is asked for. Reporting the
        missing sum as 0 shows a ridge ride as '↑0 ↓0 ft'."""
        got = T.parse_route(VALHALLA)
        self.assertIsNone(got["ascent_ft"])
        self.assertIsNone(got["descent_ft"])

    def test_elevation_that_is_sent_is_converted_to_feet(self):
        payload = json.loads(json.dumps(VALHALLA))
        payload["trip"]["legs"][0]["summary"]["elevation"] = 100.0
        payload["trip"]["legs"][0]["summary"]["elevation_change"] = -30.0
        got = T.parse_route(payload)
        self.assertEqual(got["ascent_ft"], 328)
        self.assertEqual(got["descent_ft"], 98)

    def test_a_routing_failure_is_a_reason_and_not_an_empty_route(self):
        for bad in ({}, {"trip": {}}, {"trip": {"legs": []}}, None, "nope"):
            with self.subTest(bad=bad):
                got = T.parse_route(bad)
                self.assertFalse(got["ok"])
                self.assertTrue(got["error"])

    def test_segments_carry_the_pace_each_maneuver_was_costed_at(self):
        seg = T.parse_route(VALHALLA)["segments"][1]
        self.assertEqual(seg["mph"], round(0.5 / 61.0 * 3600))
        self.assertEqual(seg["street"], "High Street")


class SpeedLimits(unittest.TestCase):
    def test_the_maxspeed_tag_is_read_in_whatever_unit_osm_used(self):
        self.assertEqual(T.parse_mph("35 mph"), 35.0)
        self.assertEqual(T.parse_mph("50 km/h"), 31)
        self.assertEqual(T.parse_mph("30"), 30.0)
        self.assertEqual(T.parse_mph(45), 45.0)
        self.assertEqual(T.parse_mph("none"), 80.0)
        self.assertIsNone(T.parse_mph("walk"))
        self.assertIsNone(T.parse_mph(None))
        self.assertIsNone(T.parse_mph(""))

    def test_the_colour_ramp_matches_the_websites_legend(self):
        self.assertEqual(T.maxspeed_colour(15), "#d73027")
        self.assertEqual(T.maxspeed_colour(35), "#fee08b")
        self.assertEqual(T.maxspeed_colour(65), "#1a9850")
        self.assertEqual(T.maxspeed_colour(None), "#888888")

    def test_a_road_with_no_posted_limit_is_inferred_and_marked_as_inferred(self):
        data = {"elements": [
            {"type": "way", "tags": {"highway": "residential", "name": "Elm"},
             "geometry": [{"lat": 1, "lon": 2}, {"lat": 1.1, "lon": 2.1}]},
            {"type": "way", "tags": {"highway": "primary", "maxspeed": "45 mph"},
             "geometry": [{"lat": 1, "lon": 2}, {"lat": 1.1, "lon": 2.1}]},
        ]}
        ways = T.parse_maxspeed(data)
        self.assertEqual([(w["mph"], w["inferred"]) for w in ways],
                         [(25, True), (45.0, False)])

    def test_a_way_with_no_shape_or_an_unknown_class_is_dropped(self):
        data = {"elements": [
            {"type": "way", "tags": {"highway": "residential"},
             "geometry": [{"lat": 1, "lon": 2}]},
            {"type": "node", "tags": {"highway": "residential"}},
            {"type": "way", "tags": {"highway": "cycleway"},
             "geometry": [{"lat": 1, "lon": 2}, {"lat": 2, "lon": 3}]},
        ]}
        self.assertEqual(T.parse_maxspeed(data), [])

    def test_a_point_far_from_any_tagged_road_falls_back_to_the_routed_pace(self):
        points = [(37.0, -122.0), (37.5, -122.5)]
        ways = [{"points": [(37.0, -122.0)], "mph": 45}]
        segments = [{"start": 0, "end": 1, "mph": 62}]
        self.assertEqual(T.speed_lookup(points, ways, segments), [45, 62])

    def test_with_nothing_to_go_on_at_all_it_says_thirty(self):
        self.assertEqual(T.speed_lookup([(0, 0), (1, 1)], [], []), [30, 30])

    def test_a_bbox_wider_than_overpass_can_answer_is_refused_with_advice(self):
        bbox = T.bbox_of([(37.0, -122.6), (38.0, -121.6)])
        self.assertGreater(T.bbox_area(bbox), T.MAXSPEED_MAX_DEG2)


class Geometry(unittest.TestCase):
    def test_a_straight_line_has_no_curves_and_a_zigzag_is_all_curves(self):
        straight = [(37.0, -122.0), (37.01, -122.0), (37.02, -122.0)]
        self.assertEqual(T.corners(straight), [])
        zigzag = [(37.0, -122.0), (37.01, -122.0), (37.01, -121.99),
                  (37.02, -121.99)]
        self.assertEqual(T.corners(zigzag), [1, 2])

    def test_distance_between_two_known_points(self):
        # Vulcan Lofts to the Pinehurst/Redwood junction, 5.14 km by the
        # server's own GPX export.
        metres = T.haversine_m((37.769972, -122.216445), (37.8076, -122.1824))
        self.assertAlmostEqual(metres, 5143.6, delta=5)

    def test_sampling_keeps_the_last_point_so_the_profile_reaches_the_end(self):
        pts = [(i / 100.0, 0.0) for i in range(250)]
        got = T.sample_points(pts, 60)
        self.assertLessEqual(len(got), 64)
        self.assertEqual(got[0], pts[0])
        self.assertEqual(got[-1], pts[-1])

    def test_a_short_route_is_not_resampled_at_all(self):
        pts = [(0.0, 0.0), (1.0, 1.0)]
        self.assertEqual(T.sample_points(pts, 60), pts)

    def test_climb_is_counted_in_both_directions_and_ignores_gaps(self):
        profile = [{"ele_ft": 100}, {"ele_ft": 150}, {"ele_ft": None},
                   {"ele_ft": 120}, {"ele_ft": 200}]
        self.assertEqual(T.profile_climb(profile), (130, 30))

    def test_no_profile_means_no_elevation_badge_rather_than_a_zero(self):
        self.assertIsNone(T.elevation_delta_ft([(0, 0), (1, 1)], []))
        self.assertIsNone(T.elevation_delta_ft([], [{"ele_ft": 1}, {"ele_ft": 2}]))
        self.assertIsNone(T.elevation_delta_ft([(0, 0), (1, 1)],
                                               [{"ele_ft": None}, {"ele_ft": None}]))

    def test_the_next_hundred_feet_is_measured_from_where_you_are(self):
        # Four points ~30 m (100 ft) apart, climbing 50 ft each.
        pts = [(37.0 + i * 0.00027, -122.0) for i in range(4)]
        profile = [{"lat": a, "lon": b, "ele_ft": 100 + i * 50}
                   for i, (a, b) in enumerate(pts)]
        self.assertEqual(T.elevation_delta_ft(pts, profile, start=0), 50)


class Rides(unittest.TestCase):
    TIMED = [{"lat": 0.0, "lon": 0.0, "time_ms": 1000, "speed_mph": 0,
              "dist_m": 0.0},
             {"lat": 1.0, "lon": 0.0, "time_ms": 3000, "speed_mph": 20,
              "dist_m": 100.0},
             {"lat": 2.0, "lon": 0.0, "time_ms": 5000, "speed_mph": 40,
              "dist_m": 100.0}]

    def test_timestamps_become_offsets_from_the_first_fix(self):
        tl, dur, has_time = T.ride_timeline(self.TIMED)
        self.assertEqual(tl, [0.0, 2000.0, 4000.0])
        self.assertEqual(dur, 4000.0)
        self.assertTrue(has_time)

    def test_a_gap_in_the_timestamps_is_clamped_rather_than_dropped(self):
        pts = [dict(self.TIMED[0]), dict(self.TIMED[1], time_ms=None),
               dict(self.TIMED[2])]
        tl, _, _ = T.ride_timeline(pts)
        self.assertEqual(tl, [0.0, 0.0, 4000.0])

    def test_a_gpx_with_no_clock_is_paced_and_flagged_as_paced(self):
        pts = [dict(p, time_ms=None) for p in self.TIMED]
        tl, dur, has_time = T.ride_timeline(pts)
        self.assertFalse(has_time)
        self.assertGreater(dur, 0)
        # 200 m at 35 mph is about 12.8 s.
        self.assertAlmostEqual(dur / 1000.0, 200 / (35 * 0.44704), delta=0.1)

    def test_a_ride_too_short_to_replay_says_so_instead_of_dividing_by_zero(self):
        self.assertEqual(T.ride_timeline([]), ([], 0.0, False))
        self.assertEqual(T.ride_timeline([self.TIMED[0]]), ([], 0.0, False))

    def test_the_playhead_interpolates_between_two_fixes(self):
        tl, dur, _ = T.ride_timeline(self.TIMED)
        lat, lon, idx, mph = T.ride_head(self.TIMED, tl, 1000.0)
        self.assertAlmostEqual(lat, 0.5)
        self.assertEqual(idx, 0)
        self.assertEqual(mph, 20)

    def test_the_playhead_is_clamped_to_the_ride(self):
        tl, dur, _ = T.ride_timeline(self.TIMED)
        self.assertEqual(T.ride_head(self.TIMED, tl, -5000)[:2], (0.0, 0.0))
        self.assertEqual(T.ride_head(self.TIMED, tl, 99999)[:2], (2.0, 0.0))
        self.assertIsNone(T.ride_head([], [], 0))

    def test_the_speed_ramp_runs_blue_to_red(self):
        self.assertEqual(T.speed_colour(0), "#313695")
        self.assertEqual(T.speed_colour(80), "#a50026")
        self.assertEqual(T.speed_colour(200), T.speed_colour(80))
        self.assertEqual(T.speed_colour(None), "#888888")

    def test_the_clock_and_the_durations_read_the_way_a_person_would_say_them(self):
        self.assertEqual(T.fmt_clock(0), "0:00")
        self.assertEqual(T.fmt_clock(65000), "1:05")
        self.assertEqual(T.fmt_clock(3725000), "1:02:05")
        self.assertEqual(T.fmt_minutes(45), "45m")
        self.assertEqual(T.fmt_minutes(60), "1h")
        self.assertEqual(T.fmt_minutes(130), "2h 10m")
        self.assertEqual(T.fmt_minutes(None), "0m")
        self.assertEqual(T.fmt_ms(None), "—")
        self.assertEqual(T.fmt_ms(65000), "1m 5s")
        self.assertEqual(T.fmt_ms(3725000), "1h 2m")

    def test_the_ride_list_dates_in_seconds_not_in_1970(self):
        """`/api/rides` sends Unix seconds; treating them as milliseconds is
        what dated every recording 21/01/1970 in the browser."""
        self.assertNotIn("1970", T.fmt_mtime(1786680690))
        self.assertEqual(T.fmt_mtime("not a time"), "—")

    def test_distances_on_the_next_turn_card_shorten_sensibly(self):
        self.assertEqual(T.fmt_miles(0.005), "now")
        self.assertEqual(T.fmt_miles(0.05), "264 ft")
        self.assertEqual(T.fmt_miles(1.44), "1.4 mi")
        # None is NOT "now". This used to conflate them, which meant a turn
        # card with no distance told the rider to turn immediately - the
        # app saying "now" when what it meant was "I do not know". Unknown
        # reads as a dash, the same as every other unknown in the app; only
        # a distance that really is ~zero says "now".
        self.assertEqual(T.fmt_miles(None), "—")
        self.assertEqual(T.fmt_miles(0.0), "now")


class PoiQueries(unittest.TestCase):
    def test_the_bbox_scopes_each_statement_not_the_group(self):
        """`(node[amenity=fuel];)(bbox)` 504s through the proxy;
        `node(bbox)[amenity=fuel];` answers. Getting this backwards is why
        the website's POI chips did nothing at all."""
        ql = T.poi_query([T.POI_BY_ID["fuel"]], (37.0, -122.5, 37.1, -122.4))
        self.assertIn("node(37.00000,-122.50000,37.10000,-122.40000)[amenity=fuel];",
                      ql)
        self.assertNotIn("](3", ql)

    def test_a_way_category_is_scoped_too(self):
        ql = T.poi_query([T.POI_BY_ID["nature"]], (1.0, 2.0, 3.0, 4.0))
        self.assertIn("way(1.00000,2.00000,3.00000,4.00000)[leisure=nature_reserve];",
                      ql)

    def test_a_ways_centre_counts_as_its_position(self):
        data = {"elements": [
            {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"name": "Shell"}},
            {"type": "way", "center": {"lat": 3.0, "lon": 4.0}, "tags": {}},
            {"type": "way", "tags": {"name": "no position"}},
        ]}
        got = T.parse_pois(data, "Fuel")
        self.assertEqual([(q["lat"], q["name"]) for q in got],
                         [(1.0, "Shell"), (3.0, "Fuel")])

    def test_the_twenty_website_categories_are_all_here(self):
        self.assertEqual(len(T.POI_CATS), 20)
        for cat in T.POI_CATS:
            with self.subTest(cat=cat["id"]):
                self.assertTrue(cat["q"].endswith(";"))
                self.assertIsNotNone(cat["icon"])


class LiveServicesRefuseOffline(ApiOfflineCase):
    """Valhalla and Overpass answer or they do not. Nothing here is queued."""

    def test_routing_with_no_signal_says_so_and_does_not_queue(self):
        self.go_offline()
        got = T.valhalla_route([{"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}])
        self.assertFalse(got["ok"])
        self.assertIn("no connection", got["error"])
        self.assertEqual(api.pending_count(), 0)
        self.assertEqual(self.server.posts, [])

    def test_two_points_are_required_before_anything_is_asked_for(self):
        got = T.valhalla_route([{"lat": 1.0, "lon": 2.0}])
        self.assertIn("two waypoints", got["error"])
        self.assertEqual(self.server.posts, [])

    def test_elevation_with_no_signal_says_so(self):
        self.go_offline()
        profile, err = T.elevations([(1.0, 2.0), (3.0, 4.0)])
        self.assertIsNone(profile)
        self.assertIn("no connection", err)

    def test_speed_limits_with_no_signal_say_so(self):
        self.go_offline()
        ways, err = T.maxspeed_ways((37.0, -122.1, 37.01, -122.09))
        self.assertIsNone(ways)
        self.assertIn("no connection", err)

    def test_pois_with_no_signal_name_every_category_as_unasked(self):
        self.go_offline()
        found, failed, err = T.fetch_pois(["fuel", "coffee"], (0, 0, 1, 1))
        self.assertEqual(found, {})
        self.assertEqual(failed, ["fuel", "coffee"])
        self.assertIn("no connection", err)

    def test_a_view_too_wide_for_overpass_is_refused_before_the_request(self):
        self.go_online()
        self.server.gets.clear()
        ways, err = T.maxspeed_ways((37.0, -122.6, 38.0, -121.6))
        self.assertIsNone(ways)
        self.assertIn("zoom in", err)
        self.assertEqual(self.server.gets, [])

    def test_the_height_service_shape_is_the_one_valhalla_really_sends(self):
        """Valhalla answers {"shape": [...], "height": [m, ...]} - a parallel
        array. Reading `elevation` off each shape point, as the website does,
        finds nothing and reports every route as having no elevation."""
        self.go_online()
        self.server.data["/api/valhalla/height"] = None    # POST, not GET
        self.server.post_reply = None
        profile, err = T.elevations([(1.0, 2.0), (3.0, 4.0)])
        # The fake server answers {"ok": true} to every POST, which has no
        # height array: that has to be reported, not silently drawn flat.
        self.assertIsNone(profile)
        self.assertIn("shape we do not know", err)

    def go_online(self):
        self.server.up = True
        self.store.set_online(True, "test: plugged in")
        api._get("/api/ping") if False else None
        self.server.data["/api/trips"] = []
        api._get("/api/trips")


class PanelSurfaces(unittest.TestCase):
    def test_every_trips_tab_is_a_surface_the_layout_editor_can_reach(self):
        for surface in ("trips.selekt", "trips.produce", "trips.rides",
                        "trips.xnav"):
            with self.subTest(surface=surface):
                self.assertIn(surface, panels.SURFACES)
                self.assertIn(surface, panels.SURFACE_LABELS)
                self.assertTrue(panels.panel_keys(surface))


class DrawerReachesEveryTab(unittest.TestCase):
    """The drawer listed sub-items for six pages and not for Trips, so the
    four tabs the website shows as buttons were reachable only by landing on
    Trips and hunting."""

    def test_the_drawer_lists_all_four_trips_tabs_at_the_right_index(self):
        import ast
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # main.py calls ft.run() at import, so read SUBPAGES out of the AST.
        tree = ast.parse(open(os.path.join(root, "main.py")).read())
        subpages = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "SUBPAGES" for t in node.targets):
                subpages = ast.literal_eval(node.value)
        self.assertIn("trips", subpages)
        self.assertEqual([(label, path[-1][1]) for label, path, _ in subpages["trips"]],
                         [("Selekt", 0), ("Produce", 1), ("Rides", 2), ("xNav", 3)])


class StubPage:
    """Enough of ft.Page to build against. No event loop on purpose."""

    def __init__(self):
        self.controls = []
        self.services = []
        self.overlay = []
        self.tasks = []

    def update(self, *a, **k):
        pass

    def add(self, *controls):
        self.controls.extend(controls)

    def show_dialog(self, *a, **k):
        pass

    def pop_dialog(self, *a, **k):
        pass

    def launch_url(self, *a, **k):
        pass

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))


def _texts(control, out=None, depth=0):
    out = [] if out is None else out
    if depth > 50:
        return out
    value = getattr(control, "value", None)
    if isinstance(value, str):
        out.append(value)
    for attr in ("content", "label", "title", "hint_text"):
        got = getattr(control, attr, None)
        if isinstance(got, str):
            out.append(got)
        elif isinstance(got, ft.Control):
            _texts(got, out, depth + 1)
    for attr in ("controls", "actions", "tabs", "options", "layers"):
        items = getattr(control, attr, None)
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, ft.Control):
                    _texts(item, out, depth + 1)
                else:
                    label = getattr(item, "text", None)
                    if isinstance(label, str):
                        out.append(label)
    return out


class ThePageBuilds(ApiOfflineCase):
    """A shape change in a control tree is a black tab and nothing catches it."""

    def setUp(self):
        super().setUp()
        from agedashii_tofu.pages import trips, xnav
        self.trips = trips
        self.xnav = xnav
        self._reset_state()
        self.addCleanup(self._reset_state)

    def _reset_state(self):
        self.trips._S.update({"builder": [], "leg_routes": {}, "combined": None,
                              "detail": set(), "editor": None, "ride": None,
                              "note": ""})
        self.trips._PLAN.update({"route": None, "from": None, "to": None,
                                 "from_text": "", "to_text": ""})
        self.xnav._NAV.update({"a": None, "b": None, "route": None,
                               "limits": None, "speeds": [], "corners": [],
                               "profile": None, "profile_error": "", "pois": {},
                               "poi_failed": [], "poi_open": False,
                               "active": set(), "note": "", "trip_id": ""})

    def build(self):
        page = StubPage()
        built = self.trips.build(page)
        self.assertIsInstance(built, ft.Control)
        return built, page

    def test_it_builds_with_no_server_and_says_so_without_inventing_trips(self):
        self.go_offline()
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("Not connected", said)
        # api.TRIPS falls back to mock.TRIPS; this page must not.
        self.assertNotIn("Grizzly Peak full", said)
        self.assertNotIn("grizzly-peak.gpx", said)

    def test_cached_trips_are_labelled_as_a_last_sync_never_as_sample_data(self):
        """They are real - fetched and kept - so the wording the other pages
        use would be a lie. What is missing is the measurement, not the trip."""
        self.server.data["/api/trips"] = [LEG]
        self.server.data["/api/rides"] = {"rides": []}
        api._get("/api/trips")
        self.go_offline()
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("Alameda loop", said)
        self.assertIn("as of the last sync", said)
        self.assertNotIn("sample data", said)

    def test_an_empty_server_gets_an_empty_state_not_a_placeholder_row(self):
        self.server.data["/api/trips"] = []
        self.server.data["/api/rides"] = {"rides": []}
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("No trips saved yet", said)
        self.assertIn("No rides uploaded yet", said)

    def test_a_real_trip_shows_its_own_fields_and_no_measured_number(self):
        self.server.data["/api/trips"] = [dict(
            LEG, biome="Flat urban island", vibe="Low-key city ride",
            difficulty="easy", tags=["alameda", "flat"],
            report={"route": [{"label": "gravel", "note": "mile 3"}],
                    "waypoints": {}})]
        self.server.data["/api/rides"] = {"rides": []}
        built, page = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("Alameda loop", said)
        self.assertIn("Flat urban island", said)
        self.assertIn("alameda", said)
        self.assertIn("gravel", said)
        # Nothing has routed yet, so the card's distance and duration are
        # dashes. A trip card that says "0 mi" reads as a measured zero.
        card = said.split("Alameda loop\nleg")[1].split("Edit")[0]
        self.assertIn("—", card)
        self.assertNotIn("mi", card)
        self.assertNotIn("0m", card)

    def test_the_produce_editor_builds_with_the_gallery_and_the_waypoints(self):
        self.server.data["/api/trips"] = [LEG]
        self.server.data["/api/rides"] = {"rides": []}
        self.trips._S["editor"] = self.trips._editor_state(LEG)
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("High St south", said)
        self.assertIn("heavy potholage", said)
        self.assertIn("Trip report", said)
        self.assertIn("whole-route", said)

    def test_a_brand_new_route_starts_at_home_with_nothing_else_filled_in(self):
        self.server.data["/api/trips"] = []
        self.server.data["/api/rides"] = {"rides": []}
        self.trips._S["editor"] = self.trips._editor_state(
            self.trips._blank_route(), is_new=True)
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn(T.HOME["name"], said)
        self.assertIn("new", said)

    def test_the_ride_viewer_builds_and_flags_a_paced_replay(self):
        self.server.data["/api/trips"] = []
        self.server.data["/api/rides"] = {"rides": []}
        points = [{"lat": 37.0 + i / 100, "lon": -122.0, "speed_mph": None,
                   "time_ms": None, "dist_m": 100.0} for i in range(5)]
        timeline, duration, has_time = T.ride_timeline(points)
        self.trips._S["ride"] = {
            "id": "berkeley-hills-north-loop", "points": points,
            "stats": {"distance_mi": 107.63, "duration_ms": None,
                      "max_speed_mph": None, "point_count": 5},
            "timeline": timeline, "duration": duration, "has_time": has_time,
            "playing": False, "head": 0.0, "speed_x": 60, "limits": None}
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("berkeley-hills-north-loop", said)
        # One decimal, not two. The distance now goes through the units
        # formatter, which is also what makes it follow the miles/km
        # setting; 107.63 mi implied a precision a GPX track does not have.
        self.assertIn("107.6", said)
        self.assertIn("synthetic", said)
        # A missing max speed is a dash, never a zero.
        self.assertIn("—", said)

    def test_the_trip_builder_shows_what_is_stacked_and_that_it_is_unrouted(self):
        self.server.data["/api/trips"] = [LEG]
        self.server.data["/api/rides"] = {"rides": []}
        self.trips._S["builder"] = [LEG]
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("Trip builder", said)
        self.assertIn("Alameda loop", said)
        self.assertIn("Save trip", said)

    def test_xnav_builds_with_a_route_in_hand(self):
        self.server.data["/api/trips"] = [LEG]
        self.server.data["/api/rides"] = {"rides": []}
        route = T.parse_route(VALHALLA)
        self.xnav._NAV.update({"route": route, "corners": [0],
                               "speeds": [35] * len(route["points"]),
                               "profile": [{"lat": 0, "lon": 0, "ele_ft": 10},
                                           {"lat": 1, "lon": 1, "ele_ft": 90}],
                               "poi_open": True})
        built, _ = self.build()
        said = "\n".join(_texts(built))
        self.assertIn("Turn left onto High Street.", said)
        self.assertIn("Curves", said)
        self.assertIn("Fuel", said)          # the POI picker is open

    def test_every_tab_still_has_its_sub_tab_label(self):
        self.server.data["/api/trips"] = []
        self.server.data["/api/rides"] = {"rides": []}
        built, _ = self.build()
        said = _texts(built)
        for label in ("Selekt", "Produce", "Rides", "xNav"):
            self.assertIn(label, said)


class WritesGoThroughTheOutbox(ApiOfflineCase):
    """Our own server, our own data - these queue and sync. Nothing else does."""

    def test_a_trip_saved_with_no_signal_is_queued_and_shows_up_locally(self):
        self.server.data["/api/trips"] = [LEG]
        api._get("/api/trips")
        self.go_offline()
        payload = T.builder_payload([LEG], "Sunday run", "sunday-run")
        ok, msg = api.trip_save(payload)
        self.assertTrue(ok)
        self.assertEqual(api.pending_count(), 1)
        self.assertIn("sunday-run", [t["id"] for t in T.trips_all()])
        self.server.up = True
        self.store.set_online(True, "test: back")
        api.sync()
        sent = self.server.posted_to("/api/trips/save")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["id"], "sunday-run")
        self.assertEqual(sent[0]["waypoints"][0]["name"], T.HOME["name"])

    def test_a_gpx_upload_with_no_signal_is_queued_with_its_filename(self):
        self.go_offline()
        ok, msg = T.ride_upload("morning-ride", "<gpx/>")
        self.assertTrue(ok)
        self.assertIn("will sync", msg)
        self.server.up = True
        self.store.set_online(True, "test: back")
        api.sync()
        sent = self.server.posted_to("/api/rides/upload")
        self.assertEqual(sent[0]["filename"], "morning-ride.gpx")
        self.assertEqual(sent[0]["content"], "<gpx/>")

    def test_an_empty_file_is_refused_before_anything_is_queued(self):
        ok, msg = T.ride_upload("x.gpx", "   ")
        self.assertFalse(ok)
        self.assertEqual(api.pending_count(), 0)


if __name__ == "__main__":
    unittest.main()
