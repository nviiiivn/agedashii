"""The ride log: what was sampled, what was derived, and what was not.

Her list: "TELEMETRY/SENSOR LOGGING (ACCELEROMETER/POTENTIOMETER/MIC/GPS.. FOR
G FORCES, TILT/ANGLES, ~EXACT LOCATION, SPEED, ELEVATION, VOLUME/FREQ
REDUCTION INTO EST. RPM/TACH.)"

This module is the honest half of that. Every channel below is one of three
things and is labelled as such in `CHANNELS`, so nothing on screen can imply
a sensor the phone is not actually reading:

    measured   read straight off a sensor
    derived    computed from measured channels, with the physics written down
    estimated  a best guess, and allowed to report nothing rather than guess

WHY SOME CHANNELS ARE ABSENT IN V1

`g_vert`, `tilt_deg` and `rpm_est` were once declared "absent - needs a custom
Dart extension", on the belief that Flet had no sensor or audio-stream API. That
was wrong, and expensively so: an extension was scaffolded before the mistake
surfaced. Flet 0.86 ships `Accelerometer`, `UserAccelerometer`, `Gyroscope`,
`Magnetometer`, `Barometer` and `ShakeDetector` as core services, and
`flet-audio-recorder` hands over raw PCM through `on_stream`. All of it is read
directly - see sensors.py and audio.py - and nothing here needs an extension.

Those channels arrive through `feed_extra`, which merges them into the sample the
latest GPS fix created rather than making rows of their own. The accelerometer
runs at 100 Hz and the position at 1 Hz; a log with a hundred rows a second would
be a different and much less useful file.

WHAT THE GPS GIVES ON ITS OWN

A GPS fix carries speed and heading computed by the receiver from Doppler
shift, not from differencing positions - so they are good at low speed and
they are good immediately. From those two, with time:

    longitudinal g   dv/dt / g             braking and drive
    yaw rate         dheading/dt           how fast the bike is turning
    lateral g        v * yaw_rate / g      the actual cornering load
    lean angle       atan(lateral g)       the angle that load implies
    grade            d(altitude)/distance  the hill

The lean angle is the interesting one. A bike in a steady corner leans until
the resultant of gravity and centripetal acceleration runs through the tyre
contact patch, so `theta = atan(v * omega / g)` is not an approximation of
lean, it *is* lean, for any bike, with no calibration and no mount
orientation to get wrong. What it cannot see is lean while stationary or
counter-steer transients, and it is noise below walking pace - hence
`MIN_LEAN_SPEED_MS`, under which the channel reports None rather than a
number that means nothing.

CRASH SAFETY

Samples are appended to a JSON-lines file as they arrive, not held until the
ride is stopped. A battery death, a force-stop or an Android low-memory kill
therefore costs at most `FLUSH_EVERY` samples, plus the one held back below,
instead of the whole ride. The file is opened in append mode, and the header
is a line like any other, so a torn file is still a readable file - every
complete line before the tear is intact.

ONE FIX LATE, ON PURPOSE. A row is written to disk when the NEXT fix arrives
(or at `stop()`), not the moment it is made. The sensors and the microphone
are merged into the newest row by `feed_extra` AFTER `add_fix` returns, and
while rows were written immediately that merge only ever reached the copy in
memory: every log on the phone was missing every sensor and audio channel,
and anything rebuilt from the log - a re-send from the Rides page, an export
- silently lost them too. Holding the newest row until it is complete costs
one more second in a crash and is the only way the file holds the ride.

ALTITUDE, AND WHICH ONE

`alt_m` - and the GPX `<ele>` written from it - is the altitude the phone's
GPS reported, which on Android is height above the WGS84 ellipsoid unless the
phone supplies a sea-level figure. Around the Bay Area the two differ by about
32 m (GeographicLib GeoidEval at 37.8, -122.2: EGM2008 geoid height
-31.96 m), which is why her rides along the Oakland flats read -18 to -23 m.
It is left as recorded and the GPX says so, rather than corrected here: a
number that changes meaning between two versions of the app is worse than one
that is consistently odd.

Nothing here touches the network. A ride recorded in a canyon is complete on
the device before anything is ever uploaded.
"""
import json
import math
import os
import re
import time

#: Standard gravity, for turning an acceleration into a g.
G = 9.80665
#: Below this, heading from a GPS receiver is mostly noise and a lean angle
#: computed from it would be a random number. 5 m/s is ~11 mph.
MIN_LEAN_SPEED_MS = 5.0
#: Samples between flushes to disk. At 1 Hz this is five seconds of loss in
#: the worst case; at 10 Hz, half a second.
FLUSH_EVERY = 5
#: How many samples the live HUD keeps in memory for its sparklines. Ten
#: minutes at 1 Hz, one minute at 10 Hz - enough to draw, small enough that a
#: four-hour ride does not grow without bound.
WINDOW = 600

#: Every channel, what kind it is, its unit, and what it means. The UI reads
#: this table rather than hard-coding a list, so a channel cannot appear on
#: screen without saying which of the three kinds it is.
CHANNELS = {
    "t":          ("measured", "s", "unix time of the fix"),
    "lat":        ("measured", "deg", "latitude"),
    "lon":        ("measured", "deg", "longitude"),
    "speed_ms":   ("measured", "m/s", "ground speed from the receiver"),
    "heading":    ("measured", "deg", "course over ground"),
    "alt_m":      ("measured", "m", "altitude"),
    "acc_m":      ("measured", "m", "horizontal accuracy, 68% confidence"),
    "g_long":     ("derived", "g", "dv/dt: braking negative, drive positive"),
    "yaw_dps":    ("derived", "deg/s", "rate of change of heading"),
    "g_lat":      ("derived", "g", "v * yaw rate: the cornering load"),
    "lean_deg":   ("derived", "deg", "atan(g_lat), the lean that load implies"),
    "grade_pct":  ("derived", "%", "rise over run between fixes"),
    "dist_m":     ("derived", "m", "distance travelled since the start"),
    # The accelerometer, gyroscope and barometer, through sensors.py. These were
    # written off as "absent, needs an extension" and that was wrong - Flet 0.86
    # ships all three as built-in services. See sensors.py.
    "g_vert":     ("measured", "g", "total acceleration, gravity included"),
    "g_peak":     ("measured", "g", "hardest total load since the last sample"),
    "g_bump":     ("measured", "g", "worst jolt with gravity removed"),
    "tilt_deg":   ("measured", "deg", "roll of the PHONE, not the bike's lean"),
    "pitch_deg":  ("measured", "deg", "pitch of the phone"),
    "yaw_gyro_dps": ("measured", "deg/s", "yaw rate from the gyroscope"),
    "gyro_peak":  ("measured", "deg/s", "fastest rotation since the last sample"),
    "pressure_hpa": ("measured", "hPa", "atmospheric pressure"),
    "alt_baro_m": ("derived", "m", "altitude from pressure - better than GPS"),
    "climb_mps":  ("derived", "m/s", "rate of climb from pressure"),
    # The microphone, through audio.py. Only present while she has switched
    # listening on.
    "compass_deg": ("measured", "deg", "heading from the magnetometer, "
                                       "usable while stopped"),
    "battery_pct": ("measured", "%", "what the ride is costing the phone"),
    "loudness_db": ("measured", "dBFS", "how loud everything is, wind included"),
    "engine_db":  ("derived", "dB", "level in the engine's band alone"),
    "exhaust_hz": ("estimated", "Hz", "the exhaust fundamental that was found"),
    "rpm_est":    ("estimated", "rpm", "engine speed from the exhaust "
                                       "fundamental - absent in wind noise"),
}

#: The order channels are written in, for CSV and for the JSONL header.
ORDER = list(CHANNELS)


def kinds(kind):
    """Every channel of one kind, in declaration order."""
    return [k for k, v in CHANNELS.items() if v[0] == kind]


def haversine_m(a, b):
    """Metres between two (lat, lon) pairs."""
    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, h)))


def heading_delta(a, b):
    """Signed degrees from heading `a` to heading `b`, in (-180, 180].

    Wrapped, because 359 to 1 degrees is a two-degree right turn and not a
    358-degree left one. Getting this wrong puts a spike of 300 deg/s of yaw
    into the log every time the bike points north.
    """
    if a is None or b is None:
        return None
    d = (b - a + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def lean_from(speed_ms, yaw_dps):
    """Lean angle in degrees from speed and yaw rate, or None.

    `theta = atan(v * omega / g)`. See the module docstring for why this is
    lean itself rather than a proxy for it. None below `MIN_LEAN_SPEED_MS`,
    where the heading a receiver reports is noise.
    """
    if speed_ms is None or yaw_dps is None:
        return None
    if speed_ms < MIN_LEAN_SPEED_MS:
        return None
    a_lat = speed_ms * math.radians(yaw_dps)
    return math.degrees(math.atan(a_lat / G))


def rpm_from_samples(samples, sample_rate, cylinders=2, strokes=4,
                     lo_hz=20.0, hi_hz=200.0):
    """Estimated engine RPM from a mono audio buffer, or None.

    Her "VOLUME/FREQ REDUCTION INTO EST. RPM/TACH". The firing frequency of a
    four-stroke is `rpm / 60 * cylinders / 2`, so the fundamental of the
    exhaust note maps straight back to RPM. The band is narrowed to 20-200 Hz
    first because that is where the firing fundamental lives for any bike
    (600-12000 rpm on a twin) and because wind noise above it is louder than
    the engine at speed.

    Implemented and tested here against synthetic tones, and wired to
    nothing: there is no live audio buffer on this platform yet. When there
    is, `Ride.feed_audio()` calls this. A naive DFT over the band rather than
    an FFT - it is a few hundred bins over a short buffer, numpy is not in
    the bundle, and this runs at most once a second.
    """
    if not samples or not sample_rate or sample_rate <= 0:
        return None
    n = len(samples)
    if n < 64:
        return None
    # Remove DC first: a buffer with an offset puts all its energy in bin 0,
    # and the peak search then finds the offset rather than the note.
    mean = sum(samples) / n
    buf = [s - mean for s in samples]
    best_hz, best_mag = None, 0.0
    step = sample_rate / n
    lo = max(1, int(lo_hz / step))
    hi = min(n // 2, int(hi_hz / step) + 1)
    for k in range(lo, hi):
        w = 2.0 * math.pi * k / n
        re = im = 0.0
        for i, s in enumerate(buf):
            re += s * math.cos(w * i)
            im -= s * math.sin(w * i)
        mag = re * re + im * im
        if mag > best_mag:
            best_mag, best_hz = mag, k * step
    if not best_hz:
        return None
    firings_per_rev = cylinders / (strokes / 2.0)
    return best_hz * 60.0 / firings_per_rev


class Ride:
    """One recording. Append-only on disk, a bounded window in memory.

    Deliberately not a subclass of anything and deliberately ignorant of
    Flet: a test drives it with plain numbers and no device, which is how the
    derived channels are proved.
    """

    def __init__(self, path=None, name="", hz=1, machine=None):
        self.path = str(path) if path else None
        self.name = name or time.strftime("%Y-%m-%d %H:%M")
        self.hz = hz
        # Which motorcycle, and the two numbers that turn an exhaust frequency
        # into an engine speed. Recorded in the header rather than left implicit,
        # because `exhaust_hz` and `rpm_est` in the samples are only meaningful
        # alongside them - and if the profile turns out to be wrong, a log with
        # the frequency and the ratio can be recomputed while one with only the
        # RPM cannot.
        self.machine = machine
        self.started = None
        self.stopped = None
        self.samples = []            # the in-memory window
        self.count = 0               # every sample ever, flushed ones included
        self.distance_m = 0.0
        self.max_speed_ms = 0.0
        self.max_lean_deg = 0.0
        self.max_g_lat = 0.0
        self.max_g_long = 0.0
        self.min_g_long = 0.0
        self.climb_m = 0.0
        self.descent_m = 0.0
        self.incidents = []
        # Sensor extremes, tracked separately from the GPS-derived ones because
        # they arrive by a different door (`feed_extra`) and may be absent
        # entirely on a device without the hardware.
        self.max_g_vert = 0.0
        self.max_g_bump = 0.0
        self.max_climb_mps = 0.0
        self.min_climb_mps = 0.0
        self.max_rpm_est = 0.0
        self._prev = None            # the last accepted sample
        self._pending = None         # the newest row, not on disk yet
        self._fh = None
        self._since_flush = 0
        self._errors = []

    # ------------------------------------------------------------- lifecycle
    def start(self, when=None):
        self.started = float(when if when is not None else time.time())
        self._open()
        return self.started

    def _open(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            # Append, never truncate. If this file already exists the right
            # answer is to add to it - a crash mid-ride followed by a restart
            # should extend the ride, not erase what survived.
            self._fh = open(self.path, "a", encoding="utf-8")
            header = {"kind": "header", "name": self.name,
                      "started": self.started, "hz": self.hz,
                      "channels": ORDER,
                      "app": "agedashii-navi", "version": 1}
            if self.machine:
                header.update(self.machine if isinstance(self.machine, dict)
                              else {"machine": self.machine})
            self._write(header)
        except OSError as exc:
            self._fh = None
            self._errors.append(f"cannot write the ride log: {exc}")

    def stop(self, when=None):
        self.stopped = float(when if when is not None else time.time())
        # The last row is complete now - nothing more will be merged into it.
        self._write_pending()
        if self._fh:
            try:
                self._write({"kind": "footer", "stopped": self.stopped,
                             "samples": self.count,
                             "distance_m": round(self.distance_m, 1)})
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except (OSError, ValueError) as exc:
                self._errors.append(f"cannot close the ride log: {exc}")
            finally:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
        return self.stopped

    @property
    def recording(self):
        return self.started is not None and self.stopped is None

    @property
    def duration_s(self):
        if self.started is None:
            return 0.0
        return (self.stopped or time.time()) - self.started

    @property
    def errors(self):
        return list(self._errors)

    # ---------------------------------------------------------------- input
    def add_fix(self, *, lat, lon, t=None, speed_ms=None, heading=None,
                alt_m=None, acc_m=None, max_acc_m=50.0):
        """Take one GPS fix, derive from it, log it. Returns the row or None.

        None means the fix was rejected, which is not an error: a fix worse
        than `max_acc_m` under a bridge otherwise adds a kilometre of phantom
        distance and a corner that never happened.
        """
        if lat is None or lon is None:
            return None
        if acc_m is not None and max_acc_m and acc_m > max_acc_m:
            return None
        t = float(t if t is not None else time.time())
        prev = self._prev

        row = {"t": round(t, 2), "lat": round(float(lat), 6),
               "lon": round(float(lon), 6),
               "speed_ms": _num(speed_ms), "heading": _num(heading),
               "alt_m": _num(alt_m), "acc_m": _num(acc_m)}

        step_m = 0.0
        if prev is not None:
            dt = t - prev["t"]
            # A repeated or out-of-order fix would divide by zero or produce a
            # negative rate. Both happen in practice when a receiver
            # re-reports a fix, so they are dropped rather than logged.
            if dt <= 0:
                return None
            step_m = haversine_m((prev["lat"], prev["lon"]),
                                 (row["lat"], row["lon"]))
            # Under two metres while stopped is GPS jitter, not movement.
            if step_m < 2.0:
                step_m = 0.0
            row["g_long"] = _round(
                _rate(row["speed_ms"], prev.get("speed_ms"), dt, G), 3)
            row["yaw_dps"] = _round(
                _rate_deg(prev.get("heading"), row["heading"], dt), 2)
            row["g_lat"] = _round(_lat_g(row["speed_ms"], row["yaw_dps"]), 3)
            row["lean_deg"] = _round(
                lean_from(row["speed_ms"], row["yaw_dps"]), 1)
            row["grade_pct"] = _round(
                _grade(prev.get("alt_m"), row["alt_m"], step_m), 1)
        else:
            for key in ("g_long", "yaw_dps", "g_lat", "lean_deg", "grade_pct"):
                row[key] = None

        # Before `_prev` is replaced: the climb/descent accumulator needs the
        # previous altitude, and `_track_maxima` reads it off `_prev`.
        self._track_maxima(row)
        self.distance_m += step_m
        row["dist_m"] = round(self.distance_m, 1)

        self._prev = row
        self.count += 1
        self.samples.append(row)
        if len(self.samples) > WINDOW:
            del self.samples[:len(self.samples) - WINDOW]
        # The previous row is complete - its sensor and audio channels were
        # merged while it was the newest - so it goes to disk now, and this one
        # waits for its own. See "ONE FIX LATE" in the module docstring.
        self._write_pending()
        self._pending = row
        return row

    @property
    def pending(self):
        """The newest row, held off the disk until it is complete, or None."""
        return self._pending

    def _write_pending(self):
        if self._pending is not None:
            row, self._pending = self._pending, None
            self._append(row)

    def feed_extra(self, values):
        """Merge sensor or audio channels into the most recent sample.

        Attaches to the latest row rather than making one of its own, which is
        the whole point: the accelerometer runs at 100 Hz and the barometer at
        10, and a log whose position is 1 Hz must not become a log with a
        hundred rows a second. `sensors.latest()` and `audio.latest()` both
        return exactly this shape, so the caller does no translating.

        Unknown keys are kept. A channel added to `CHANNELS` later should not
        need an edit here to be recorded, and a key that is not in `ORDER` is
        simply not in the CSV - it is still in the JSONL, which is the archive.
        """
        if not self.samples or not values:
            return None
        row = self.samples[-1]
        for key, value in values.items():
            if value is not None:
                row[key] = value
        self._track_sensor_maxima(row)
        return row

    def feed_motion(self, *, g_vert=None, tilt_deg=None):
        """Backwards-compatible shim for the two channels that had names first.

        Kept because the tests and the first version of the ride page call it;
        `feed_extra` is what the sensors actually go through now.
        """
        return self.feed_extra({"g_vert": _round(g_vert, 3)
                                if g_vert is not None else None,
                                "tilt_deg": _round(tilt_deg, 1)
                                if tilt_deg is not None else None})

    def feed_audio(self, samples, sample_rate, **kw):
        """The door a live audio buffer comes in through when it exists."""
        rpm = rpm_from_samples(samples, sample_rate, **kw)
        if rpm is None or not self.samples:
            return None
        self.samples[-1]["rpm_est"] = round(rpm, 0)
        return rpm

    def note_incident(self, what, t=None):
        """Mark a moment worth finding again - a hard brake, a drop, a stop.

        Its own list and its own line, so that finding the interesting six
        seconds of a four-hour ride does not mean scanning every sample.
        """
        entry = {"t": round(float(t if t is not None else time.time()), 2),
                 "what": str(what)}
        self.incidents.append(entry)
        self._append(dict(entry, kind="incident"))
        return entry

    # --------------------------------------------------------------- output
    def summary(self):
        """The numbers the Rides list and the end of a ride both want."""
        return {"name": self.name, "started": self.started,
                "stopped": self.stopped, "samples": self.count,
                "duration_s": round(self.duration_s, 1),
                "distance_m": round(self.distance_m, 1),
                "max_speed_ms": round(self.max_speed_ms, 2),
                "max_lean_deg": round(self.max_lean_deg, 1),
                "max_g_lat": round(self.max_g_lat, 3),
                "max_g_long": round(self.max_g_long, 3),
                "min_g_long": round(self.min_g_long, 3),
                "climb_m": round(self.climb_m, 1),
                "descent_m": round(self.descent_m, 1),
                "incidents": len(self.incidents),
                # Only reported when a sensor actually fed them. A zero here
                # would be indistinguishable from a phone with no accelerometer.
                "max_g_vert": round(self.max_g_vert, 3) or None,
                "max_g_bump": round(self.max_g_bump, 3) or None,
                "max_climb_mps": round(self.max_climb_mps, 2) or None,
                "min_climb_mps": round(self.min_climb_mps, 2) or None,
                "max_rpm_est": round(self.max_rpm_est) or None,
                "path": self.path}

    def to_csv(self, rows=None):
        """Every channel, one row per sample, in `ORDER`.

        A flat CSV rather than something cleverer because the point is that it
        opens in anything - "ALL APPROPRIATE DATA CONVERGRED" - and a blank
        cell for an absent channel is the honest representation of a sensor
        this build did not have.
        """
        rows = self.samples if rows is None else rows
        out = [",".join(ORDER)]
        for row in rows:
            out.append(",".join(
                "" if row.get(k) is None else str(row.get(k)) for k in ORDER))
        return "\n".join(out) + "\n"

    def to_gpx(self, rows=None):
        """A GPX track, with every other channel in an extensions block.

        GPX because every mapping tool on earth reads it; the extensions
        because lean angle and lateral g have nowhere to live in the base
        schema and dropping them would make the export lossy.

        EVERY channel the row carries, not a list. This used to name eight
        keys, and so every ride that reached the server was missing the
        barometer's altitude and climb rate, the accelerometer, the gyroscope,
        the compass and the battery - on any phone, whatever it measured.
        `extension_keys` decides, and it only leaves out what the base schema
        already holds.
        """
        rows = self.samples if rows is None else rows
        head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                # "Agedashii Navi", not xnav.f: the file format is shared
                # with xnav.k and the family's tools, and Kotlin's golden test
                # compares byte for byte (AGEDASHII's call).
                '<gpx version="1.1" creator="Agedashii Navi" '
                'xmlns="http://www.topografix.com/GPX/1/1" '
                'xmlns:navi="https://alola.lol/navi/1">\n'
                f'  <metadata><name>{_xml(self.name)}</name>'
                f'<desc>{_xml(ELE_NOTE)}</desc></metadata>\n'
                '  <trk>\n'
                f'    <name>{_xml(self.name)}</name>\n'
                '    <trkseg>\n')
        body = []
        for row in rows:
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                 time.gmtime(row.get("t") or 0))
            # <ele> BEFORE <time>: GPX 1.1's schema fixes the order, and a
            # strict importer refuses the file the other way round.
            parts = [f'      <trkpt lat="{row["lat"]}" lon="{row["lon"]}">']
            if row.get("alt_m") is not None:
                parts.append(f'        <ele>{row["alt_m"]}</ele>')
            parts.append(f'        <time>{when}</time>')
            extra = {k: _xml(row[k]) for k in extension_keys(row)}
            if extra:
                parts.append('        <extensions>')
                parts += [f'          <navi:{k}>{v}</navi:{k}>'
                          for k, v in extra.items()]
                parts.append('        </extensions>')
            parts.append('      </trkpt>')
            body.append("\n".join(parts))
        return head + "\n".join(body) + "\n    </trkseg>\n  </trk>\n</gpx>\n"

    def to_json(self, rows=None):
        """Summary, channel dictionary and samples together.

        The channel dictionary travels with the data on purpose: a CSV column
        called `g_lat` means nothing in six months, and an export that has to
        be read alongside this source file is not an export.
        """
        rows = self.samples if rows is None else rows
        return json.dumps({"summary": self.summary(),
                           "channels": {k: {"kind": v[0], "unit": v[1],
                                            "about": v[2]}
                                        for k, v in CHANNELS.items()},
                           "incidents": self.incidents,
                           "samples": rows}, indent=1)

    # -------------------------------------------------------------- private
    def _track_maxima(self, row):
        speed = row.get("speed_ms")
        if speed is not None:
            self.max_speed_ms = max(self.max_speed_ms, speed)
        lean = row.get("lean_deg")
        if lean is not None:
            self.max_lean_deg = max(self.max_lean_deg, abs(lean))
        g_lat = row.get("g_lat")
        if g_lat is not None:
            self.max_g_lat = max(self.max_g_lat, abs(g_lat))
        g_long = row.get("g_long")
        if g_long is not None:
            self.max_g_long = max(self.max_g_long, g_long)
            self.min_g_long = min(self.min_g_long, g_long)
        alt = row.get("alt_m")
        prev_alt = (self._prev or {}).get("alt_m")
        if alt is not None and prev_alt is not None:
            rise = alt - prev_alt
            # One metre of dead band: GPS altitude wanders by a metre or two
            # standing still, and without this a two-hour stop at a cafe
            # accumulates hundreds of metres of "climb".
            if rise > 1.0:
                self.climb_m += rise
            elif rise < -1.0:
                self.descent_m += -rise

    def _track_sensor_maxima(self, row):
        """The extremes of the channels that arrive through `feed_extra`."""
        for key, attr in (("g_vert", "max_g_vert"), ("g_peak", "max_g_vert"),
                          ("g_bump", "max_g_bump"),
                          ("rpm_est", "max_rpm_est")):
            value = row.get(key)
            if value is not None:
                setattr(self, attr, max(getattr(self, attr), abs(value)))
        climb = row.get("climb_mps")
        if climb is not None:
            self.max_climb_mps = max(self.max_climb_mps, climb)
            self.min_climb_mps = min(self.min_climb_mps, climb)

    def _append(self, obj):
        if self._fh is None:
            return
        try:
            self._write(obj)
            self._since_flush += 1
            if self._since_flush >= FLUSH_EVERY:
                self._fh.flush()
                self._since_flush = 0
        except (OSError, ValueError) as exc:
            # Losing the log must not stop the ride. Say so once, keep
            # sampling into memory, and let the UI show the error.
            self._errors.append(f"ride log stopped: {exc}")
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def _write(self, obj):
        self._fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------ helpers

#: What the GPX base schema already carries, so never repeated as an extension.
GPX_BASE = ("t", "lat", "lon", "alt_m")

#: What `<ele>` is. Written into every GPX, see "ALTITUDE, AND WHICH ONE".
ELE_NOTE = ("ele is the altitude the phone's GPS reported: height above the "
            "WGS84 ellipsoid unless the phone supplied a sea-level figure. "
            "Around the Bay Area the ellipsoid figure reads about 32 m below "
            "sea level. Barometric altitude, when the phone has a barometer, "
            "is navi:alt_baro_m.")


def extension_keys(row):
    """The row's channels that go in a GPX extensions block, in log order.

    Known channels in `ORDER` first, then anything `feed_extra` merged that is
    not declared yet - sorted, so two exports of one ride are identical - as
    long as its name is a valid XML tag. Absent values are left out, never
    written as empty or zero.
    """
    known = [k for k in ORDER
             if k not in GPX_BASE and _scalar(row.get(k))]
    unknown = sorted(k for k in row
                     if k not in CHANNELS and k not in GPX_BASE
                     and _scalar(row.get(k)) and _tag_ok(k))
    return known + unknown


def _scalar(value):
    """A value one extension element can hold. A list would land as Python repr.

    None is not one: an absent channel is left out, never written empty.
    """
    return isinstance(value, (int, float, str))


#: An extension tag this module will write: ASCII only. `str.isalnum` also
#: passes "x²" and "ª", and a strict parser - the dashboard's - rejects those
#: and then reads the whole ride as empty.
_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tag_ok(key):
    return isinstance(key, str) and _TAG.fullmatch(key) is not None


def _num(v):
    try:
        return None if v is None else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _round(v, places):
    return None if v is None else round(v, places)


def _rate(now, before, dt, scale=1.0):
    if now is None or before is None or not dt:
        return None
    return (now - before) / dt / scale


def _rate_deg(before, now, dt):
    d = heading_delta(before, now)
    if d is None or not dt:
        return None
    return d / dt


def _lat_g(speed_ms, yaw_dps):
    if speed_ms is None or yaw_dps is None:
        return None
    return speed_ms * math.radians(yaw_dps) / G


def _grade(before_alt, now_alt, run_m):
    if before_alt is None or now_alt is None or not run_m:
        return None
    return (now_alt - before_alt) / run_m * 100.0


def _xml(text):
    # Control characters are not allowed in XML 1.0 at all, escaped or not, so
    # they are dropped - one in a ride name made the whole GPX unreadable.
    clean = "".join(c for c in str(text) if c >= " " or c in "\t\n\r")
    return (clean.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def read_log(path):
    """Read a JSONL ride log back. Tolerates a file torn by a crash.

    Returns (header, samples, incidents, footer). A line that will not parse
    is skipped rather than raising - the whole reason for the format is that a
    partial file is still worth something.
    """
    header, footer = {}, {}
    samples, incidents = [], []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue                 # the torn last line
                kind = obj.get("kind")
                if kind == "header":
                    header = obj
                elif kind == "footer":
                    footer = obj
                elif kind == "incident":
                    incidents.append(obj)
                elif "lat" in obj:
                    samples.append(obj)
    except OSError:
        return {}, [], [], {}
    return header, samples, incidents, footer


def load_ride(path):
    """A `Ride` rebuilt from a log on disk - the WHOLE ride, not the window.

    The maxima are replayed through `_track_maxima` rather than recomputed
    inline, so the summary of a finished ride comes out of exactly the same
    code that produced it live. A summary computed two different ways is a
    summary that eventually disagrees with itself. Moved here from the Rides
    page so the upload path can use it without importing a page.
    """
    header, samples, incidents, footer = read_log(path)
    ride = Ride(path=None, name=header.get("name") or "ride")
    ride.started = header.get("started")
    ride.stopped = footer.get("stopped")
    ride.samples = samples
    ride.count = len(samples)
    ride.incidents = incidents
    if samples:
        ride.distance_m = samples[-1].get("dist_m") or 0.0
        for row in samples:
            ride._track_maxima(row)                    # noqa: SLF001 - ours
            ride._track_sensor_maxima(row)             # noqa: SLF001 - ours
            ride._prev = row                           # noqa: SLF001 - ours
    return ride


def header_name(path):
    """The ride's name from the first line of its log, without reading the rest.

    For a list of forty rides, where parsing forty logs to label them would be
    the whole cost of opening the page. None when the line is not a header.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            first = json.loads(fh.readline() or "{}")
    except (OSError, ValueError):
        return None
    return first.get("name") if first.get("kind") == "header" else None
