"""Weather: the map, the rain radar and the honest absence of an hourly strip.

The fixtures below are trimmed copies of what `/api/weather/multi` really
returned on 2026-09-21 and what `api.rainviewer_frames()` really built out
of RainViewer's index that afternoon - not shapes that seemed likely.
"""
import unittest

from flet_app import panels
from flet_app.pages import weather as W


def _spot(name, region, lat, lon, on_map, temp, code, pop0, precip=0.0,
          gust=None):
    return {
        "name": name, "region": region, "lat": lat, "lon": lon,
        "on_map": on_map,
        "current": {"time": "2026-09-21T13:15", "temperature_2m": temp,
                    "apparent_temperature": temp - 2, "weather_code": code,
                    "wind_speed_10m": 11.8, "relative_humidity_2m": 78,
                    "precipitation": precip, "wind_gusts_10m": gust},
        "daily": {"time": ["2026-09-21", "2026-09-22", "2026-09-23"],
                  "weather_code": [45, 45, 3],
                  "temperature_2m_max": [63.9, 78.0, 82.5],
                  "temperature_2m_min": [54.0, 55.4, 54.1],
                  "precipitation_probability_max": [pop0, 2, 0],
                  "wind_speed_10m_max": [14.4, 11.1, 8.1]},
    }


# Four of the real fourteen mapped stations, plus two of the seven the
# server marks on_map: false.
SPOTS = [
    _spot("Sausalito", "Marin", 37.8591, -122.4853, True, 63.5, 1, 1, gust=16.6),
    _spot("Oakland", "EB", 37.8044, -122.2712, True, 56.0, 3, 1),
    _spot("Twin Peaks", "SF", 37.7540, -122.4477, True, 53.0, 3, 1),
    _spot("Pacifica", "SF S", 37.6407, -122.4890, True, 55.0, 45, 1),
    _spot("Mt Diablo", "EB E", 37.8816, -121.8234, False, 60.0, 3, 1),
    _spot("Cupertino", "SB", 37.3230, -122.0322, False, 62.0, 3, 1),
]

FRAMES = [
    {"time": 1790014800,
     "url": "https://tilecache.rainviewer.com/v2/radar/aec1f59673a4"
            "/256/{z}/{x}/{y}/2/1_0.png"},
    {"time": 1790021400,
     "url": "https://tilecache.rainviewer.com/v2/radar/26e67fd39471"
            "/256/{z}/{x}/{y}/2/1_0.png"},
    {"time": 1790022000,
     "url": "https://tilecache.rainviewer.com/v2/radar/83be711f7c5b"
            "/256/{z}/{x}/{y}/2/1_0.png"},
]


def _texts(control, out=None):
    out = [] if out is None else out
    name = type(control).__name__
    if name == "Text":
        value = getattr(control, "value", None)
        if value not in (None, ""):
            out.append(str(value))
    for key in ("controls", "content", "actions", "title", "markers", "layers"):
        v = getattr(control, key, None)
        if isinstance(v, (list, tuple)):
            for y in v:
                if hasattr(y, "__dict__"):
                    _texts(y, out)
        elif v is not None and hasattr(v, "__dict__"):
            _texts(v, out)
    return out


class TemperatureColour(unittest.TestCase):
    """The website's exact eight stops, so a pin is the same colour in the
    browser and on the phone."""

    def test_the_stops_themselves(self):
        for degrees, colour in W.TEMP_STOPS:
            self.assertEqual(W.temp_color(degrees), colour, degrees)

    def test_clamped_at_both_ends(self):
        self.assertEqual(W.temp_color(10), "#2196f3")
        self.assertEqual(W.temp_color(120), "#f44336")

    def test_interpolates_between_stops(self):
        # halfway between 60 (#4caf50) and 65 (#8bc34a)
        mid = W.temp_color(62.5)
        self.assertNotIn(mid, ("#4caf50", "#8bc34a"))
        self.assertTrue(mid.startswith("#") and len(mid) == 7)

    def test_a_missing_reading_is_grey_not_freezing(self):
        # Blue would read as 45 degrees - a measurement nobody took.
        self.assertEqual(W.temp_color(None), "#888888")
        self.assertEqual(W.temp_color("n/a"), "#888888")


class Wetness(unittest.TestCase):
    """Both inputs are real: current precipitation in mm, and today's
    precipitation_probability_max."""

    def test_buckets(self):
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 3, 0)), "Dry")
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 3, 45)), "Maybe")
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 61, 90,
                                         precip=0.05)), "Damp")
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 65, 90,
                                         precip=2.0)), "Wet")

    def test_thirty_percent_is_the_maybe_threshold(self):
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 3, 29)), "Dry")
        self.assertEqual(W.wetness(_spot("x", "", 0, 0, True, 60, 3, 30)), "Maybe")

    def test_no_data_is_no_label_rather_than_dry(self):
        self.assertIsNone(W.wetness({"current": {}, "daily": {}}))
        self.assertIsNone(W.wetness({}))


class RainStamp(unittest.TestCase):

    def test_reads_as_frame_of_total_and_how_old(self):
        stamp = W.rain_stamp(FRAMES, 2, now=1790022600)
        self.assertTrue(stamp.startswith("3 / 3 · "))
        self.assertIn("(10m ago)", stamp)

    def test_the_newest_frame_is_now(self):
        self.assertIn("(now)", W.rain_stamp(FRAMES, 2, now=1790022000))

    def test_the_index_wraps(self):
        self.assertTrue(W.rain_stamp(FRAMES, 3, now=1790022600).startswith("1 / 3"))

    def test_nothing_to_stamp_says_nothing(self):
        self.assertEqual(W.rain_stamp([], 0), "")
        self.assertEqual(W.rain_stamp(None, 0), "")


class RadarStacking(unittest.TestCase):
    """The radar goes above the base tiles and below the station pins, at
    the opacity the website uses."""

    def setUp(self):
        self.saved = dict(W._rain)

    def tearDown(self):
        W._rain.clear()
        W._rain.update(self.saved)

    def test_off_means_no_layer(self):
        W._rain.update(on=False, frames=FRAMES, index=0)
        self.assertIsNone(W._rain_layer())
        self.assertEqual(len(W._stack("base", ["pins"])), 2)

    def test_on_with_no_frames_means_no_layer(self):
        W._rain.update(on=True, frames=[], index=0)
        self.assertIsNone(W._rain_layer())

    @unittest.skipUnless(W.MAPS_AVAILABLE, "needs flet-map")
    def test_on_puts_the_frame_between_base_and_pins(self):
        W._rain.update(on=True, frames=FRAMES, index=1)
        stack = W._stack("base", ["pins"])
        self.assertEqual(len(stack), 3)
        self.assertEqual(stack[0], "base")
        self.assertEqual(stack[2], "pins")
        self.assertEqual(stack[1].url_template, FRAMES[1]["url"])
        self.assertEqual(stack[1].opacity, W.RAIN_OPACITY)
        self.assertEqual(stack[1].max_native_zoom, W.RAIN_MAX_NATIVE_ZOOM)

    @unittest.skipUnless(W.MAPS_AVAILABLE, "needs flet-map")
    def test_the_index_wraps_rather_than_raising(self):
        W._rain.update(on=True, frames=FRAMES, index=7)
        self.assertEqual(W._rain_layer().url_template, FRAMES[1]["url"])


class MappedAndReference(unittest.TestCase):
    """Seven of the twenty-one spots are reference points well outside the
    frame; fitting the map to them zooms the Bay into one blob."""

    def test_only_the_mapped_ones_get_pins(self):
        self.assertEqual([s["name"] for s in W._mapped(SPOTS)],
                         ["Sausalito", "Oakland", "Twin Peaks", "Pacifica"])

    def test_a_spot_with_no_coordinates_cannot_be_pinned(self):
        broken = dict(SPOTS[0])
        broken["lat"] = None
        self.assertNotIn(broken, W._mapped([broken]))

    def test_a_spot_with_no_on_map_key_counts_as_mapped(self):
        # The server sends on_map on every row today, but absent must not
        # silently drop a station off the map.
        s = {"name": "x", "lat": 37.8, "lon": -122.3}
        self.assertEqual(W._mapped([s]), [s])

    def test_bounds_and_zoom(self):
        lat, lon, dlat, dlon = W._bounds(W._mapped(SPOTS))
        self.assertAlmostEqual(lat, 37.7646, places=3)
        self.assertAlmostEqual(lon, -122.4233, places=3)
        self.assertGreater(W._zoom_for(dlat, dlon), 8)
        self.assertLess(W._zoom_for(dlat, dlon), 14)
        # a wide span zooms out, a tight one zooms in
        self.assertLess(W._zoom_for(3.0, 3.0), W._zoom_for(0.02, 0.02))


class AllSpotsPanel(unittest.TestCase):

    def setUp(self):
        from flet_app.ui import PAL
        self.p = PAL()
        self.shown = _texts(W._all_spots(self.p, SPOTS, "Oakland", lambda n: None))

    def test_mapped_and_reference_are_separated_and_counted(self):
        self.assertIn("Reference spots — not mapped", self.shown)
        self.assertIn("6 spots · 4 mapped + 2 reference · temps °F · winds mph "
                      "· 3-day forecast · precip % = chance", self.shown)

    def test_every_spot_gets_a_tile(self):
        for name in ("Sausalito", "Oakland", "Twin Peaks", "Pacifica",
                     "Mt Diablo", "Cupertino"):
            self.assertIn(name, self.shown)

    def test_a_tile_carries_feels_wind_gust_humidity_and_sky(self):
        tile = _texts(W._spot_tile(self.p, SPOTS[0], "Oakland", lambda n: None))
        self.assertIn("feels", tile)
        self.assertIn("wind", tile)
        self.assertIn("12 mph g17", tile)     # 11.8 -> 12, gust 16.6 -> 17
        self.assertIn("hum", tile)
        self.assertIn("sky", tile)
        self.assertIn("Dry", tile)

    def test_a_tile_carries_the_three_day_forecast(self):
        tile = _texts(W._spot_tile(self.p, SPOTS[0], "Oakland", lambda n: None))
        self.assertIn("Today", tile)
        self.assertEqual(tile.count("64°/54°"), 1)   # 63.9 / 54.0
        self.assertIn("78°/55°", tile)
        self.assertIn("82°/54°", tile)

    def test_it_says_outright_that_there_is_no_hourly_series(self):
        self.assertTrue(any("no hourly series" in s for s in self.shown))

    def test_no_hour_by_hour_strip_is_drawn(self):
        # /api/weather/multi has never returned one. A strip that repeated
        # the single current reading twelve times would be invented.
        for line in self.shown:
            self.assertNotIn(":00", line)


class ARainChipComesFromRealData(unittest.TestCase):
    """The old page showed "0% rain" forever because it read an
    hourly.precipitation_probability array this server has never sent."""

    def test_zero_percent_is_the_servers_own_zero(self):
        dry = _spot("x", "", 0, 0, True, 60, 3, 0)
        self.assertIn("0% rain today", _texts(W._hero(dry)))

    def test_a_missing_probability_says_so_instead_of_zero(self):
        no_pop = _spot("x", "", 0, 0, True, 60, 3, 0)
        no_pop["daily"]["precipitation_probability_max"] = []
        self.assertIn("rain chance n/a", _texts(W._hero(no_pop)))


class TappedPointCard(unittest.TestCase):

    def setUp(self):
        from flet_app.ui import PAL
        self.p = PAL()
        self.saved = dict(W._point)

    def tearDown(self):
        W._point.clear()
        W._point.update(self.saved)

    def test_nothing_tapped_yet(self):
        W._point.update(lat=None, lon=None, data=None, error="", busy=False)
        self.assertTrue(any("Tap the map" in s
                            for s in _texts(W._point_card(self.p))))

    def test_while_loading_it_says_so(self):
        W._point.update(name="Santa Cruz", lat=36.97, lon=-122.03, busy=True,
                        data=None, error="")
        self.assertTrue(any("reading Santa Cruz" in s
                            for s in _texts(W._point_card(self.p))))

    def test_a_failure_names_the_reason(self):
        W._point.update(name="Santa Cruz", lat=36.97, lon=-122.03, busy=False,
                        data=None, error="no forecast for this point - "
                                         "unreachable")
        shown = _texts(W._point_card(self.p))
        self.assertIn("no forecast for this point - unreachable", shown)

    def test_coordinates_are_not_printed_twice_for_a_tap(self):
        W._point.update(name="36.974, -122.031", lat=36.9741, lon=-122.0308,
                        busy=False, error="",
                        data={"current": {"temperature_2m": 63.3,
                                          "weather_code": 2},
                              "daily": {"time": ["2026-09-21"],
                                        "weather_code": [45],
                                        "temperature_2m_max": [65.0],
                                        "temperature_2m_min": [56.0],
                                        "precipitation_probability_max": [0]}})
        shown = _texts(W._point_card(self.p))
        self.assertIn("36.974, -122.031 · tapped point", shown)


class WeatherSurfacesAreRegistered(unittest.TestCase):

    def test_the_bay_map_is_an_arrangeable_card(self):
        self.assertIn("map", panels.panel_keys("weather"))
        self.assertIn("weather", panels.SURFACE_LABELS)

    def test_explore_is_a_surface_too(self):
        self.assertIn("weather.explore", panels.SURFACES)
        self.assertIn("weather.explore", panels.SURFACE_LABELS)
        keys = panels.panel_keys("weather.explore")
        self.assertEqual(keys, ["search", "cats", "map", "point", "results"])

    def test_no_surface_repeats_a_panel_key(self):
        for surface in ("weather", "weather.explore"):
            keys = panels.panel_keys(surface)
            self.assertEqual(len(keys), len(set(keys)), surface)


if __name__ == "__main__":
    unittest.main()
