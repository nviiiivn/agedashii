"""Her road test, 2026-09-27: four things the app said that were not true.

1. "tiles streets clicking it does nothing, changing the theme in settings or
   putting streets or topo changes nothing either it just the black tiles" -
   the offline archive always wins (right), it is a DARK rendering (the code
   said streets), and four styles were offered where one could be drawn.
2. "in settings there's 'show the gauges at all' and the in options there's
   gauges there and it only shows or hides it... sooooooo idk" - three
   controls for one switch, one of them a dead tap, one never saved.
3. No barometer altitude, climb rate or any sensor channel in any uploaded
   ride - `to_gpx` wrote eight named keys, and the log on disk was written
   before the sensors were merged into its rows. The auto-upload also sent
   only the last ten minutes.
4. "what happens when i upload/uploaded rides? where do they get saved to?
   moto?" - the Rides page did not say, and could not say which had gone.

Each test names the failure it guards against. Two were demonstrated on the
pre-fix code before it was changed - the log on disk holding none of the
merged channels, and `to_gpx` dropping climb and pressure - and the rest were
written from the pre-fix source rather than run against it: apps/navi is not
under version control, so there was no copy of the old code to run them on.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import flet as ft                                          # noqa: E402

from agedashii_washi import api as M                       # noqa: E402
from agedashii_washi import config, tileserver             # noqa: E402

from agedashii_navi import (gauges, live, prefs, telemetry,  # noqa: E402
                            tiles, uploads)
from agedashii_navi.pages import log as log_page            # noqa: E402
from agedashii_navi.pages import ride as ride_page          # noqa: E402
from agedashii_navi.pages import settings as settings_page  # noqa: E402

#: The archive every build ships, when this checkout has it.
REAL_ARCHIVE = APP_DIR.parents[1] / "assets" / "maps" / "bayarea.mbtiles"


# ------------------------------------------------------------------ harness

class _Task:
    def cancel(self):
        pass


class Page:
    """Enough ft.Page to build against, recording tasks and dialogs."""

    def __init__(self):
        self.controls, self.services, self.overlay = [], [], []
        self.tasks, self.dialogs = [], []
        self.width = self.height = None

    def update(self, *_a, **_k):
        pass

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))
        return _Task()

    def run_thread(self, fn, *args, **_k):
        self.tasks.append((fn, args))

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        pass


class Event:
    def __init__(self, control=None):
        self.control = control


class StoredPrefs:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value
        return True


def walk(node, seen=None):
    seen = set() if seen is None else seen
    if node is None or id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions"):
        for child in (getattr(node, attr, None) or []):
            yield from walk(child, seen)
    for attr in ("content", "title"):
        child = getattr(node, attr, None)
        # Any control, a bare Text included: a tile whose content IS its label
        # would otherwise be invisible, and "no Off tile" would pass for the
        # wrong reason.
        if isinstance(child, ft.BaseControl):
            yield from walk(child, seen)


def texts(node):
    return [str(c.value) for c in walk(node)
            if isinstance(c, ft.Text) and c.value]


def tappable_texts(node):
    """The words on every control that has a click handler."""
    out = []
    for control in walk(node):
        if getattr(control, "on_click", None) is not None:
            out.extend(texts(control))
    return out


def holder_of(node, words):
    """The innermost Container whose texts include `words`."""
    best = None
    for control in walk(node):
        if isinstance(control, ft.Container) and words in texts(control):
            best = control
    return best


def make_archive(folder, name="Offline OSM dark"):
    path = os.path.join(folder, "t.mbtiles")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER,"
                 " tile_row INTEGER, tile_data BLOB)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("name", name), ("format", "png"),
                      ("minzoom", "10"), ("maxzoom", "15")])
    conn.execute("INSERT INTO tiles VALUES (10, 1, 1, ?)",
                 (b"\x89PNG\r\n\x1a\n",))
    conn.commit()
    conn.close()
    return path


def open_ride_options(page):
    """Build Ride, tap the options button, return the sheet it showed."""
    built = ride_page.build(page)
    button = next(c for c in walk(built)
                  if getattr(c, "tooltip", None) == "Ride options")
    button.on_click(Event(button))
    return page.dialogs[-1]


class _Clean(unittest.TestCase):
    def setUp(self):
        prefs.reset()
        live.reset()
        ride_page.forget_map()
        tiles._ARCHIVE_STYLE.clear()                   # noqa: SLF001
        uploads._S["checked_at"] = 0.0                 # noqa: SLF001
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(tileserver.stop)
        self.addCleanup(live.reset)
        patcher = mock.patch.object(live, "ride_dir",
                                    return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = Page()


# ------------------------------------------------------------- 1. the tiles

class TheTilePickerTellsTheTruth(_Clean):
    def setUp(self):
        super().setUp()
        self.archive = make_archive(self.tmp.name)

    def test_the_archive_says_what_it_renders(self):
        self.assertEqual(tiles.archive_style(self.archive), "dark")

    @unittest.skipUnless(REAL_ARCHIVE.is_file(), "no archive in this checkout")
    def test_the_archive_every_build_ships_is_dark_not_streets(self):
        # The fact the old docstring had backwards.
        self.assertEqual(tiles.archive_style(str(REAL_ARCHIVE)), "dark")

    def test_asking_for_streets_offline_draws_dark_and_says_dark(self):
        got = tiles.pick("streets", archive=self.archive)
        self.assertTrue(got["offline"], "the archive-wins rule changed")
        self.assertEqual(got["style"], "dark")
        self.assertTrue(got["substituted"])
        self.assertIn("dark", got["note"])
        self.assertNotIn("streets", got["note"],
                         "the chip claims a rendering it is not drawing")

    def test_with_the_archive_only_its_own_style_is_offered(self):
        self.assertEqual(tiles.usable(archive=self.archive), ["dark"])
        _choice, rows = tiles.choices("topo", archive=self.archive)
        for row in rows:
            if row["key"] != "dark":
                self.assertEqual(row["why"], tiles.WHY_ARCHIVE_WINS)

    def test_without_an_archive_every_style_needs_signal_and_says_so(self):
        choice, rows = tiles.choices("topo", archive=False)
        self.assertFalse(choice["offline"])
        self.assertIn("needs signal", choice["note"])
        self.assertTrue(all(r["usable"] for r in rows))
        self.assertTrue(all("needs signal" in r["why"] for r in rows))

    def test_an_archive_that_does_not_say_is_unknown_not_guessed(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        vague = make_archive(other.name, name="My tiles")
        got = tiles.pick("dark", archive=vague)
        self.assertIsNone(got["style"])
        self.assertTrue(got["substituted"])
        self.assertIn("unknown", got["note"])

    def test_the_one_button_never_lands_on_a_style_it_cannot_draw(self):
        self.assertEqual(tiles.cycle("dark", only=["dark"]), "dark")
        self.assertEqual(tiles.cycle("streets", only=["dark"]), "dark")

    def test_settings_offers_only_the_style_that_can_be_drawn(self):
        prefs.set_value("tiles", "streets")
        with mock.patch.object(M, "mbtiles_path", return_value=self.archive):
            built = settings_page.build(self.page)
        offered = tappable_texts(built)
        self.assertIn("Dark", offered)
        for name in ("Streets", "Satellite", "Topo"):
            self.assertNotIn(name, offered, f"{name} offered, cannot be drawn")
        self.assertTrue(any("online only" in t for t in texts(built)))

    def test_ride_options_tile_row_is_not_a_button_with_the_archive(self):
        with mock.patch.object(M, "mbtiles_path", return_value=self.archive):
            sheet = open_ride_options(self.page)
        row = holder_of(sheet, "Map: Dark - offline")
        self.assertIsNotNone(row, texts(sheet))
        self.assertIsNone(getattr(row, "on_click", None),
                          "a tap that can change nothing is still offered")


# ------------------------------------------------------------ 2. the gauges

class TheGaugesAreOneSwitch(_Clean):
    def test_off_is_not_a_layout(self):
        self.assertNotIn("off", [k for k, _n, _d in gauges.LAYOUTS])

    def test_the_toggle_is_not_dead_when_an_old_layout_said_off(self):
        prefs.S["hud"] = "off"
        prefs.S["dash"] = True
        self.assertFalse(prefs.dash())
        self.assertTrue(prefs.toggle_dash(), "the dead tap is back")
        self.assertEqual(prefs.hud(), "ribbon")

    def test_the_toggle_flips_and_flips_back(self):
        self.assertTrue(prefs.dash())
        self.assertFalse(prefs.toggle_dash())
        self.assertTrue(prefs.toggle_dash())

    def test_hud_off_turns_the_switch_off_and_keeps_the_layout(self):
        prefs.set_value("hud", "corners")
        prefs.set_value("hud", "off")
        self.assertFalse(prefs.dash())
        self.assertEqual(prefs.hud(), "corners")

    def test_an_older_builds_stored_off_loads_as_gauges_off(self):
        stored = StoredPrefs({"navi.hud": "off", "navi.dash": "1"})
        asyncio.run(prefs.load(stored))
        self.assertFalse(prefs.dash())
        self.assertEqual(prefs.hud(), "ribbon")

    def test_settings_has_one_gauges_switch_and_no_off_tile(self):
        built = settings_page.build(self.page)
        words = texts(built)
        self.assertEqual(words.count("Show the gauges"), 1)
        self.assertNotIn("Show the gauges at all", words)
        self.assertNotIn("Off", tappable_texts(built))

    def test_the_settings_switch_goes_through_the_same_door_and_saves(self):
        built = settings_page.build(self.page)
        card = holder_of(built, "Show the gauges")
        switch = next(c for c in walk(card) if isinstance(c, ft.Switch))
        switch.on_change(Event(switch))
        self.assertFalse(prefs.dash())
        self.assertTrue(self.page.tasks, "the change was not saved")

    def test_the_ride_options_row_says_it_is_the_same_switch_and_saves(self):
        sheet = open_ride_options(self.page)
        row = holder_of(sheet, "Gauges: on")
        self.assertIn("Same switch as Settings > Gauges", " ".join(texts(row)))
        self.page.tasks.clear()
        row.on_click(Event(row))
        self.assertFalse(prefs.dash())
        self.assertIn("_save_prefs",
                      [getattr(fn, "__name__", "") for fn, _a in self.page.tasks],
                      "the Ride options toggle is forgotten at the next launch")


# -------------------------------------------------- 3. the channels, on disk

def _ride(folder, n=4, name="probe"):
    ride = telemetry.Ride(path=os.path.join(folder, "r.jsonl"), name=name)
    ride.start(when=1000.0)
    for i in range(n):
        ride.add_fix(lat=37.8 + i * 0.001, lon=-122.27, t=1000.0 + i,
                     speed_ms=15.0, heading=90.0 + i, alt_m=10.0 + i)
        ride.feed_extra({"rpm_est": 4000 + i, "climb_mps": 0.5 + i,
                         "pressure_hpa": 1012.0 - i, "alt_baro_m": 11.0 + i,
                         "g_vert": 1.0, "tilt_deg": -3.0,
                         "compass_deg": 91.0, "battery_pct": 80})
    return ride


class EveryChannelReachesTheLogAndTheGpx(_Clean):
    def test_sensor_channels_reach_the_log_on_disk(self):
        ride = _ride(self.tmp.name)
        ride.stop(when=1010.0)
        _h, samples, _i, _f = telemetry.read_log(ride.path)
        self.assertEqual(len(samples), 4)
        for i, row in enumerate(samples):
            self.assertEqual(row.get("rpm_est"), 4000 + i, row)
            self.assertEqual(row.get("climb_mps"), 0.5 + i, row)
            self.assertEqual(row.get("pressure_hpa"), 1012.0 - i, row)

    def test_the_newest_row_waits_for_its_merge(self):
        ride = _ride(self.tmp.name, n=2)
        self.assertIs(ride.pending, ride.samples[-1])
        ride.stop(when=1010.0)
        self.assertIsNone(ride.pending, "stop() did not write the last row")

    def test_the_gpx_carries_every_channel_not_eight(self):
        gpx = _ride(self.tmp.name).to_gpx()
        for key in ("climb_mps", "alt_baro_m", "pressure_hpa", "g_vert",
                    "tilt_deg", "compass_deg", "battery_pct", "rpm_est",
                    "lean_deg", "grade_pct"):
            self.assertIn(f"<navi:{key}>", gpx, key)
        self.assertIn("<ele>", gpx)

    def test_the_gpx_says_which_altitude_ele_is(self):
        self.assertIn("WGS84", _ride(self.tmp.name).to_gpx())

    def test_the_gpx_is_still_well_formed_xml(self):
        ride = _ride(self.tmp.name)
        ride.name = "ride\x01 name"
        ride.feed_extra({"bad key": 1, "odd<tag": 2, "fine_one": "a<b\x02",
                         "a_list": [1, 2], "x²": 3, "ª": 4})
        gpx = ride.to_gpx()
        root = ET.fromstring(gpx)
        ns = "{http://www.topografix.com/GPX/1/1}"
        self.assertEqual(len(root.findall(f".//{ns}trkpt")), 4)
        self.assertIn("fine_one", gpx)
        self.assertNotIn("a_list", gpx, "a list written as Python repr")

    def test_ele_comes_before_time_as_the_schema_requires(self):
        root = ET.fromstring(_ride(self.tmp.name).to_gpx())
        ns = "{http://www.topografix.com/GPX/1/1}"
        point = root.find(f".//{ns}trkpt")
        self.assertEqual([child.tag for child in point][:2],
                         [f"{ns}ele", f"{ns}time"])

    def test_a_ride_rebuilt_from_disk_sends_what_memory_would(self):
        ride = _ride(self.tmp.name)
        ride.stop(when=1010.0)
        from_disk = telemetry.load_ride(ride.path)
        self.assertEqual(from_disk.to_gpx(), ride.to_gpx())


# --------------------------------------------------- 3b. the whole ride goes

class TheWholeRideIsSent(_Clean):
    def test_a_ride_longer_than_the_window_is_sent_whole(self):
        n = telemetry.WINDOW + 150
        ride = _ride(self.tmp.name, n=n)
        ride.stop(when=5000.0)
        self.assertEqual(len(ride.samples), telemetry.WINDOW)
        sent = {}
        with mock.patch.object(M, "ride_upload",
                               side_effect=lambda name, gpx, note="":
                               (sent.update(gpx=gpx) or True, note)):
            ok, _msg = uploads.send(ride.path, ride, directory=self.tmp.name)
        self.assertTrue(ok)
        self.assertEqual(sent["gpx"].count("<trkpt"), n,
                         "the upload lost the start of the ride")

    def test_stopping_a_ride_uploads_the_log_not_the_window(self):
        n = telemetry.WINDOW + 100
        live.start_recording(directory=self.tmp.name)
        for i in range(n):
            live.set_position(37.8 + i * 0.0005, -122.27, speed_ms=12.0,
                              heading=0.0, alt_m=5.0, acc_m=5.0, t=1000.0 + i)
        built = ride_page.build(self.page)
        stop = next(c for c in walk(built)
                    if getattr(c, "tooltip", None) == "Stop recording")
        self.page.tasks.clear()
        stop.on_click(Event(stop))
        upload = next((fn, a) for fn, a in self.page.tasks
                      if getattr(fn, "__name__", "") == "_upload")
        sent = {}
        with mock.patch.object(M, "ride_upload",
                               side_effect=lambda name, gpx, note="":
                               (sent.update(gpx=gpx) or True, note)):
            asyncio.run(upload[0](*upload[1]))
        self.assertEqual(sent["gpx"].count("<trkpt"), n)


# ------------------------------------------- 4. where they go, and whether

class TheRidesPageSaysWhereAndWhether(_Clean):
    def _log(self, name="2026-09-27 13:50", n=3):
        ride = _ride(self.tmp.name, n=n, name=name)
        ride.stop(when=2000.0)
        return ride

    def test_the_server_name_is_the_one_the_dashboard_uses(self):
        # The files her three rides actually arrived as.
        self.assertEqual(uploads.server_name("2026-09-27-13:50"),
                         "2026-09-27-13-50.gpx")
        # Spaces become "-" on the phone BEFORE the server's own pass turns
        # the "&" into another, so three dashes is what the dashboard saves.
        self.assertEqual(uploads.server_name("Grizzly & Wildcat"),
                         "grizzly---wildcat.gpx")

    def test_a_sent_ride_is_recorded_as_sent_with_its_server_file(self):
        ride = self._log()
        with mock.patch.object(M, "ride_upload",
                               return_value=(True, "3 points")):
            ok, message = uploads.send(ride.path, directory=self.tmp.name)
        self.assertTrue(ok)
        self.assertIn("2026-09-27-13-50.gpx", message)
        entry = uploads.status(ride.path, directory=self.tmp.name)
        self.assertEqual(entry["state"], "sent")
        self.assertEqual(entry["server_file"], "2026-09-27-13-50.gpx")

    def test_a_queued_ride_is_recorded_as_waiting_through_the_real_upload(self):
        # Through Washi's real ride_upload, so QUEUED_WORDS is pinned to what
        # it actually says rather than to what this module assumes it says.
        ride = self._log()
        queued = M._Result(True, M.QUEUED_MESSAGE, queued=True)  # noqa: SLF001
        with mock.patch.object(M, "_queue_write", return_value=queued):
            ok, _message = uploads.send(ride.path, directory=self.tmp.name)
        self.assertTrue(ok)
        entry = uploads.status(ride.path, directory=self.tmp.name)
        self.assertEqual(entry["state"], "queued")
        self.assertEqual(uploads.describe(entry)[0],
                         "waiting on this phone for signal")

    def test_a_failed_send_says_why(self):
        ride = self._log()
        with mock.patch.object(M, "ride_upload",
                               return_value=(False, "server unreachable")):
            ok, _message = uploads.send(ride.path, directory=self.tmp.name)
        self.assertFalse(ok)
        words, colour = uploads.describe(
            uploads.status(ride.path, directory=self.tmp.name))
        self.assertEqual(words, "not sent: server unreachable")
        self.assertEqual(colour, "bad")

    def test_a_ride_the_server_lists_is_on_the_dashboard_even_unsent_here(self):
        ride = self._log()
        changed = uploads.confirm([{"id": "2026-09-27-13-50", "mtime": 1}],
                                  [ride.path], directory=self.tmp.name)
        self.assertEqual(changed, 1)
        self.assertEqual(
            uploads.status(ride.path, directory=self.tmp.name)["state"],
            "on_dashboard")

    def test_opening_rides_pushes_the_queue_then_asks_the_server(self):
        ride = self._log()
        fresh = {"rides": [{"id": "2026-09-27-13-50", "mtime": 3000}]}
        with mock.patch.object(M, "pending_count", return_value=1), \
                mock.patch.object(M, "sync") as sync, \
                mock.patch.object(M, "_fetch", return_value=fresh), \
                mock.patch.object(M, "rides_list") as cached:
            changed, why = uploads.refresh([ride.path],
                                           directory=self.tmp.name)
            again = uploads.refresh([ride.path], directory=self.tmp.name)
        sync.assert_called_once()
        cached.assert_not_called()   # the cache answers from before the send
        self.assertEqual((changed, why), (1, ""))
        self.assertEqual(again, (0, ""), "not throttled - asks on every build")

    def test_no_signal_is_said_rather_than_left_looking_current(self):
        with mock.patch.object(M, "pending_count", return_value=0), \
                mock.patch.object(M, "_fetch",
                                  side_effect=OSError("no route to host")):
            self.assertEqual(uploads.refresh([], directory=self.tmp.name),
                             (0, uploads.UNREACHABLE))

    def test_an_older_server_copy_does_not_confirm_a_queued_resend(self):
        ride = self._log()
        uploads.record(ride.path, "queued", "held", "2026-09-27-13-50.gpx",
                       when=5000.0, directory=self.tmp.name)
        old = [{"id": "2026-09-27-13-50", "mtime": 5000.0 - 3600}]
        self.assertEqual(uploads.confirm(old, [ride.path],
                                         directory=self.tmp.name), 0)
        self.assertEqual(uploads.status(ride.path,
                                        directory=self.tmp.name)["state"],
                         "queued", "an older copy turned a re-send green")
        new = [{"id": "2026-09-27-13-50", "mtime": 5010.0}]
        self.assertEqual(uploads.confirm(new, [ride.path],
                                         directory=self.tmp.name), 1)

    def test_a_ride_gone_from_the_server_says_so(self):
        ride = self._log()
        uploads.record(ride.path, "on_dashboard", "", "2026-09-27-13-50.gpx",
                       directory=self.tmp.name)
        uploads.confirm([], [ride.path], directory=self.tmp.name)
        words, _colour = uploads.describe(
            uploads.status(ride.path, directory=self.tmp.name))
        self.assertIn("not on the dashboard any more", words)

    def test_a_log_that_stopped_early_is_topped_up_from_memory(self):
        ride = _ride(self.tmp.name, n=10)
        ride.stop(when=2000.0)
        # Cut the log after its fourth row, as a full disk would.
        with open(ride.path, encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(ride.path, "w", encoding="utf-8") as fh:
            fh.writelines(lines[:5])
        whole, note = uploads.whole_ride(ride.path, ride)
        self.assertEqual(whole.count, 10, "memory's rows were not added")
        self.assertEqual(note, "")

    def test_the_ride_being_recorded_is_not_offered_to_send(self):
        self._log()
        live.start_recording(directory=self.tmp.name)
        live.set_position(37.8, -122.27, speed_ms=12.0, heading=0.0,
                          acc_m=5.0, t=1000.0)
        built = log_page.build(self.page)
        live_file = os.path.basename(live.LIVE["ride"].path)
        self.assertFalse(any(live_file in t for t in texts(built)),
                         "a half-written ride can be sent from Rides")

    def test_record_survives_two_threads_finishing_together(self):
        import threading
        paths = [os.path.join(self.tmp.name, f"r{i}.jsonl") for i in range(20)]
        threads = [threading.Thread(target=uploads.record,
                                    args=(p, "sent", "", "x.gpx"),
                                    kwargs={"directory": self.tmp.name})
                   for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(uploads.records(self.tmp.name)), 20)

    def test_the_page_says_where_rides_go_and_which_have_gone(self):
        self._log()
        with mock.patch.object(config, "SERVER_URL", "http://pi.test:8000"):
            built = log_page.build(self.page)
        words = " ".join(texts(built))
        self.assertIn("http://pi.test:8000", words)
        self.assertIn("dashbored-rides", words)
        self.assertIn("Trips > Rides", words)
        self.assertIn("not sent from this phone", words)
        self.assertIn("_check", [getattr(fn, "__name__", "")
                                 for fn, _a in self.page.tasks],
                      "the page never checks the server")

    def test_settings_shows_the_real_server_address(self):
        with mock.patch.object(config, "SERVER_URL", "http://pi.test:8000"):
            built = settings_page.build(self.page)
        self.assertIn("http://pi.test:8000", texts(built))


if __name__ == "__main__":
    unittest.main()
