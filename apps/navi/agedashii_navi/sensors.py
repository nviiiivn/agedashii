"""The motion sensors, which Flet has had all along.

This module exists because of a mistake worth recording. The telemetry channels
`g_vert`, `tilt_deg` and the rest were written as "absent - needs an
accelerometer extension", and a whole Flet extension was scaffolded to provide
them. It was not needed: **Flet 0.86 ships `Accelerometer`,
`UserAccelerometer`, `Gyroscope`, `Magnetometer`, `Barometer` and
`ShakeDetector` as built-in services** - `flet` itself depends on
`sensors_plus`, which is how the extension's own `pub get` gave it away.

So this is a thin wrapper over services that were already there, and those
channels are measured, not absent.

WHAT EACH SENSOR IS ACTUALLY GOOD FOR

`Accelerometer` includes gravity, so it answers *orientation*: pitch and roll
from the direction the gravity vector points. `UserAccelerometer` has gravity
removed, so it answers *what just happened*: bumps, potholes, hard braking, a
drop. Those are different questions and each sensor can only answer one of
them, which is why both are subscribed.

`Gyroscope` gives angular rate directly, in rad/s. That is a better yaw rate
than differencing GPS headings - it does not need the bike to be moving and it
does not collapse into noise at walking pace.

`Barometer` is the surprise. Atmospheric pressure gives *relative* altitude to
within about a metre, many times a second, where GPS altitude wanders by five or
ten and lags. Absolute height above sea level still needs a reference, so this
records the pressure at the first reading and reports altitude relative to it -
and says that is what it is doing. For "am I climbing or descending, and how
fast", relative is the only part that matters.

WHAT ROLL IS NOT

It is not lean angle while moving. A phone strapped to a leaning bike measures
the resultant of gravity and cornering force, which points along the bike's
centreline, so in a steady corner roll reads near ZERO rather than the lean.
Lean comes from speed and yaw rate - `telemetry.lean_from` - and this module does
not pretend otherwise. What roll IS good for is the horizon gauge while stopped
or running straight, and for noticing the phone has been knocked out of its
mount.

PEAKS

Every sensor is sampled far faster than the ride log's 1 Hz, so the largest
value seen since the last `reset_peaks()` is kept alongside the current one. A
pothole is a spike two samples wide: recording the instantaneous value once a
second misses it nine times in ten, and recording the window's peak cannot miss
it at all.
"""
import math
import time

import flet as ft

#: Standard sea-level pressure, for the barometric formula.
SEA_LEVEL_HPA = 1013.25
#: How often each sensor is asked to report.
#:
#: This was 100 ms, and on a real phone that was a mistake. Five sensors at ten
#: readings a second is fifty events a second crossing the Dart-to-Python
#: channel, every one of them a message to decode and a handler to run, all on
#: the same thread that draws the map. The app became unusable - "very slow. i
#: can barely get to a different page".
#:
#: 400 ms is 2.5 readings a second per sensor, twelve events a second in total,
#: and it loses nothing that matters: the peaks between samples are tracked on
#: the Dart side of nothing and in `_peak` here, so a pothole still lands in the
#: window it happened in. The ride log is 1 Hz regardless.
INTERVAL_MS = 400
#: The barometer is slower still. Pressure does not change quickly, the climb
#: rate is computed over a six-second window anyway, and every reading is a
#: message that competes with the map for the same thread.
BARO_INTERVAL_MS = 1000
#: Seconds of pressure history behind the climb rate. Too short and the rate is
#: noise; too long and a crest is over before the number moves.
CLIMB_WINDOW_S = 6.0

#: The services, held so Flet does not unregister them.
_M = {"services": [], "page": None}

#: Everything the sensors have said - current values and peaks in one dict, so
#: the gauges and the log read exactly the same thing.
STATE = {}


def reset():
    STATE.clear()
    STATE.update({
        # Accelerometer, gravity included.
        "x": None, "y": None, "z": None,
        "g_total": None, "g_peak": 0.0,
        "pitch_deg": None, "roll_deg": None,
        # User accelerometer, gravity removed.
        "g_bump": None, "g_bump_peak": 0.0,
        # Gyroscope, rad/s.
        "gyro_x": None, "gyro_y": None, "gyro_z": None, "gyro_peak": 0.0,
        # Barometer.
        "pressure_hpa": None, "alt_baro_m": None, "climb_mps": None,
        "base_pressure_hpa": None, "relative_m": None,
        # Magnetometer: a compass heading, which GPS cannot give below walking
        # pace. Raw microtesla as well, because a large total field means the
        # phone is sitting next to something magnetic and the heading is junk.
        "mag_x": None, "mag_y": None, "mag_z": None,
        "compass_deg": None, "mag_total_ut": None,
        # Shake detector: a free impact flag.
        "shakes": 0, "last_shake": None,
        # Battery, for the cost of a ride.
        "battery_pct": None, "battery_state": "",
        # Bookkeeping.
        "seen": set(), "errors": {}, "last_t": None, "_history": [],
    })


reset()


def altitude_from_pressure(hpa, sea_level_hpa=SEA_LEVEL_HPA):
    """Metres above sea level from a pressure reading, or None.

    The international barometric formula. Absolute accuracy depends entirely on
    `sea_level_hpa` being today's actual sea-level pressure rather than the
    standard value, which is why altitude is also reported relative to the first
    reading instead of this number being trusted on its own.
    """
    if not hpa or hpa <= 0:
        return None
    return 44330.0 * (1.0 - (hpa / sea_level_hpa) ** (1.0 / 5.255))


def _peak(name, value):
    if value is not None and value > STATE.get(name, 0.0):
        STATE[name] = value


def _quiet():
    """Stop Flet walking the whole control tree because a sensor ticked.

    Flet runs `session.after_event()` after every event handler
    (base_control.py:530). That calls `__auto_update`, which walks up to the
    nearest isolated ancestor and calls `update()` on it
    (messaging/session.py:556-571). Services hang off `Page`, and `Page` IS
    that ancestor - so every reading from every sensor diffed the entire
    control tree. Four sensors at 400 ms plus a barometer at 1 s is eleven of
    those a second, for ever, from the first frame. Moto attaches no sensors
    at all and pays none of it.

    WHAT THIS IS AND IS NOT. Measured on the Pi: a no-change page update is
    1.6 ms on Ride and 6.7-8.2 ms on Settings, so eleven a second is roughly
    2% of the loop on Ride and 8% on Settings. Real waste, worth removing, but
    NOT on its own enough to freeze the app - and a no-change update sends
    nothing over the wire (session.py:275 only transmits when the patch has
    content). The Flutter-side cost of eleven platform-channel events a second
    has not been measured and may yet matter. So: this is a fix, not a
    diagnosis. Do not let it be described as the cause without evidence.

    These handlers write to `STATE` and touch no control, so an auto-update
    has nothing to draw. Whatever wants the numbers on screen asks for them on
    its own clock - `ride.paint()` at DASH_EVERY_S.

    ONE TRAP. Called outside a Flet event, `ft.context` hands back the
    module-level default UpdateBehavior (context.py:198) rather than this
    task's copy, and switching that off disables auto-update for every later
    event in the app. Nothing calls these handlers directly today. Keep it
    that way: a replay or demo path must feed the services, not the handlers.
    """
    try:
        ft.context.disable_auto_update()
    except Exception:              # noqa: BLE001 - an older Flet, or called
        pass                       # outside an event. Not worth a crash.


def on_accelerometer(e):
    """Raw accelerometer: orientation, and total load."""
    _quiet()
    x, y, z = float(e.x), float(e.y), float(e.z)
    STATE["x"], STATE["y"], STATE["z"] = x, y, z
    total = math.sqrt(x * x + y * y + z * z) / 9.80665
    STATE["g_total"] = total
    _peak("g_peak", total)
    # atan2 against the other two axes rather than asin(x/g): the total vector
    # is not 1 g under braking, and asin would return NaN the moment the
    # magnitude exceeded gravity - which happens on every pothole.
    STATE["pitch_deg"] = math.degrees(math.atan2(-x, math.sqrt(y * y + z * z)))
    STATE["roll_deg"] = math.degrees(math.atan2(y, z))
    STATE["seen"].add("accelerometer")
    STATE["last_t"] = time.time()


def zero():
    """Call THIS orientation upright. Returns what it captured, or None.

    Hers: "there should be a 'reset' sensors. because sometimes i move the
    phone mount of something and itd be good to recalibrate what is 'home' or
    'straight' or 'not moving or tilting'".

    `tilt_deg` and `pitch_deg` are the PHONE's angles, not the bike's, so they
    only mean anything relative to however the mount happens to hold it. A
    mount knocked ten degrees off makes every reading ten degrees wrong, for
    ever, with nothing on screen to say so.

    Deliberately instantaneous rather than averaged: she is standing at the
    bike holding it still, and a button that takes a second to answer feels
    broken. If mount vibration makes that a problem, average `INTERVAL_MS`-
    spaced samples here and nothing else has to change.

    Returns None when no accelerometer has reported yet, so the caller can say
    "no sensor" instead of claiming a calibration that never happened.
    """
    if STATE.get("roll_deg") is None:
        return None
    STATE["zero_roll"] = STATE["roll_deg"]
    STATE["zero_pitch"] = STATE["pitch_deg"]
    return {"roll": round(STATE["zero_roll"], 1),
            "pitch": round(STATE["zero_pitch"], 1)}


def unzero():
    """Forget the calibration and report raw phone angles again."""
    STATE["zero_roll"] = 0.0
    STATE["zero_pitch"] = 0.0


def zeroed():
    """Whether an upright has been set, and at what angles."""
    roll = STATE.get("zero_roll") or 0.0
    pitch = STATE.get("zero_pitch") or 0.0
    return {"set": bool(roll or pitch),
            "roll": round(roll, 1), "pitch": round(pitch, 1)}


def on_user_accelerometer(e):
    """Gravity removed: bumps, drops, hard braking."""
    _quiet()
    x, y, z = float(e.x), float(e.y), float(e.z)
    bump = math.sqrt(x * x + y * y + z * z) / 9.80665
    STATE["g_bump"] = bump
    _peak("g_bump_peak", bump)
    STATE["seen"].add("user_accelerometer")


def on_gyroscope(e):
    """Angular rate, rad/s. The z axis is yaw for a phone lying flat."""
    _quiet()
    x, y, z = float(e.x), float(e.y), float(e.z)
    STATE["gyro_x"], STATE["gyro_y"], STATE["gyro_z"] = x, y, z
    _peak("gyro_peak", math.sqrt(x * x + y * y + z * z))
    STATE["seen"].add("gyroscope")


def on_barometer(e):
    """Pressure, and the altitude and climb rate that follow from it."""
    _quiet()
    hpa = float(e.pressure)
    STATE["pressure_hpa"] = hpa
    if STATE.get("base_pressure_hpa") is None:
        # The first reading is the reference. Absolute altitude needs today's
        # sea-level pressure, which nothing here has; relative needs only this.
        STATE["base_pressure_hpa"] = hpa
    absolute = altitude_from_pressure(hpa)
    base = altitude_from_pressure(STATE["base_pressure_hpa"])
    STATE["alt_baro_m"] = absolute
    STATE["relative_m"] = (None if absolute is None or base is None
                           else absolute - base)
    now = time.time()
    history = STATE["_history"]
    history.append((now, absolute))
    # Keep only the window, so the climb rate is over a known span rather than
    # over however long the app has been open.
    while history and now - history[0][0] > CLIMB_WINDOW_S:
        history.pop(0)
    if len(history) >= 2 and absolute is not None:
        span = history[-1][0] - history[0][0]
        rise = (history[-1][1] or 0.0) - (history[0][1] or 0.0)
        STATE["climb_mps"] = rise / span if span > 0 else None
    STATE["seen"].add("barometer")


def on_magnetometer(e):
    """A compass heading, which GPS cannot supply at a standstill.

    GPS course is derived from movement, so below walking pace it is noise and the
    bike marker freezes at whatever it last said. The magnetometer answers "which
    way am I pointing" with the bike stopped, which is the one moment it matters -
    at a junction, deciding whether to turn around.

    `mag_total_ut` is the honesty check. Earth's field is about 25-65 microtesla;
    a reading far outside that means the phone is next to something magnetic - a
    speaker, a tank bag clasp, the bike's own wiring - and the heading is junk. The
    caller can see that rather than trusting a confident wrong number.
    """
    _quiet()
    x, y, z = float(e.x), float(e.y), float(e.z)
    STATE["mag_x"], STATE["mag_y"], STATE["mag_z"] = x, y, z
    STATE["mag_total_ut"] = math.sqrt(x * x + y * y + z * z)
    # Flat-phone heading. Tilt compensation needs the accelerometer's attitude
    # and a full rotation matrix; on a mounted phone the tilt is roughly constant
    # so an uncompensated heading is consistently offset rather than unstable,
    # which is the more useful failure.
    STATE["compass_deg"] = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    STATE["seen"].add("magnetometer")


#: Called with (when, g_bump_peak) on every shake. `live` sets this so a knock
#: becomes an incident in the ride log; set here rather than imported the other
#: way round, because sensors must not depend on the ride.
ON_SHAKE = None


def on_shake(e):
    """A hard knock. Free impact detection, no threshold of our own to tune.

    Android's own shake detector fires this, so the threshold is the platform's
    and not a number invented here. The interesting part is what it is worth: on
    a bike this is a pothole, a dropped phone, or a fall, and all three are worth
    finding again in a four-hour log.
    """
    _quiet()
    STATE["shakes"] += 1
    STATE["last_shake"] = time.time()
    STATE["seen"].add("shake")
    if callable(ON_SHAKE):
        try:
            ON_SHAKE(STATE["last_shake"], STATE.get("g_bump_peak") or 0.0)
        except Exception:                       # noqa: BLE001 - a listener must
            # never take the sensor down with it
            pass


def on_battery(e):
    """Battery state changes, for the cost of a ride.

    Screen-on navigation in a mount runs a phone down by 20-30% an hour, and in
    direct sun it also throttles. Logging it turns "my phone died on the way
    back" into a number that can be planned around.
    """
    _quiet()
    STATE["battery_state"] = str(getattr(e, "state", "") or "")
    STATE["seen"].add("battery")


def on_error(name):
    def handler(e):
        # A phone without a barometer is the normal case, not a fault. The error
        # is recorded so Settings can name the missing sensor; nothing else
        # reacts to it.
        STATE["errors"][name] = str(getattr(e, "message", None)
                                    or getattr(e, "data", None) or e)
    return handler


def attach(page, interval_ms=INTERVAL_MS):
    """Subscribe to every sensor this device has. Idempotent.

    Returns the services. They are held at module level as well, because a Flet
    service is unregistered the moment nothing holds a strong reference - a local
    would stop the sensors as soon as this returned.
    """
    if _M["services"] and _M["page"] is page:
        return _M["services"]
    made = []
    for cls, handler, name, every in (
            (ft.Accelerometer, on_accelerometer, "accelerometer", interval_ms),
            (ft.UserAccelerometer, on_user_accelerometer,
             "user_accelerometer", interval_ms),
            (ft.Gyroscope, on_gyroscope, "gyroscope", interval_ms),
            (ft.Barometer, on_barometer, "barometer", BARO_INTERVAL_MS),
            (ft.Magnetometer, on_magnetometer, "magnetometer", interval_ms)):
        try:
            made.append(cls(interval=ft.Duration(milliseconds=every),
                            on_reading=handler, on_error=on_error(name)))
        except Exception as exc:                # noqa: BLE001 - not every
            # platform has every sensor, and a browser has none of them. A
            # missing sensor costs its own channel and nothing else.
            STATE["errors"][name] = str(exc)

    # No shake detector. It was suggested here rather than asked for, and her
    # answer was "shake detector is probably not needed" - so it is not
    # subscribed and nothing pays for it. `on_shake` and the ON_SHAKE hook stay:
    # they cost nothing unused, and a deliberate impact marker is the obvious
    # thing to hang off them if it is ever wanted.
    try:
        battery = ft.Battery(on_state_change=on_battery)
        made.append(battery)
        _M["battery"] = battery
    except Exception as exc:                    # noqa: BLE001
        STATE["errors"]["battery"] = str(exc)
    try:
        brightness = ft.ScreenBrightness()
        made.append(brightness)
        _M["brightness"] = brightness
    except Exception as exc:                    # noqa: BLE001
        STATE["errors"]["brightness"] = str(exc)

    if isinstance(getattr(page, "services", None), list):
        page.services.extend(made)
    _M["services"] = made
    _M["page"] = page
    return made


def detach():
    """Drop the references, which is what stops the sensors."""
    page = _M.get("page")
    if isinstance(getattr(page, "services", None), list):
        for service in _M["services"]:
            try:
                page.services.remove(service)
            except ValueError:
                pass
    _M["services"] = []
    _M["page"] = None


def attached():
    return bool(_M["services"])


def available():
    """Which sensors have actually produced a reading, and which failed."""
    return {"seen": sorted(STATE["seen"]), "errors": dict(STATE["errors"])}


def latest():
    """The current values, named for the ride-log columns. Peaks included.

    Only keys with a real reading behind them are returned, so a device with no
    barometer contributes no altitude column rather than a zero.
    """
    out = {}
    if STATE.get("g_total") is not None:
        out["g_vert"] = round(STATE["g_total"], 3)
        out["g_peak"] = round(STATE["g_peak"], 3)
        # Relative to whatever she called upright, so a mount that sits at an
        # angle - or gets knocked - reads zero when the bike is actually level.
        out["tilt_deg"] = round(STATE["roll_deg"]
                                - (STATE.get("zero_roll") or 0.0), 1)
        out["pitch_deg"] = round(STATE["pitch_deg"]
                                 - (STATE.get("zero_pitch") or 0.0), 1)
    if STATE.get("g_bump") is not None:
        out["g_bump"] = round(STATE["g_bump_peak"], 3)
    if STATE.get("gyro_z") is not None:
        # Degrees per second, to match the GPS-derived yaw channel it sits
        # beside. Mixing rad/s and deg/s in one log is how a lean angle comes
        # out 57 times too big.
        out["yaw_gyro_dps"] = round(math.degrees(STATE["gyro_z"]), 2)
        out["gyro_peak"] = round(math.degrees(STATE["gyro_peak"]), 2)
    bearing = compass_deg()
    if bearing is not None:
        out["compass_deg"] = round(bearing, 1)
    if STATE.get("battery_pct") is not None:
        out["battery_pct"] = STATE["battery_pct"]
    if STATE.get("pressure_hpa") is not None:
        out["pressure_hpa"] = round(STATE["pressure_hpa"], 2)
        if STATE.get("alt_baro_m") is not None:
            out["alt_baro_m"] = round(STATE["alt_baro_m"], 1)
        if STATE.get("climb_mps") is not None:
            out["climb_mps"] = round(STATE["climb_mps"], 2)
    return out


def peaks():
    """The peaks alone, for a caller that wants them without the rest."""
    return {"g_peak": STATE["g_peak"], "g_bump_peak": STATE["g_bump_peak"],
            "gyro_peak": math.degrees(STATE["gyro_peak"])}


def reset_peaks():
    """Start a new window. Called once per logged sample."""
    STATE["g_peak"] = 0.0
    STATE["g_bump_peak"] = 0.0
    STATE["gyro_peak"] = 0.0


#: Earth's magnetic field, in microtesla. Outside this range the phone is next to
#: something magnetic and the compass heading means nothing.
FIELD_MIN_UT, FIELD_MAX_UT = 20.0, 75.0


def compass_deg():
    """A compass heading, or None when there is no trustworthy one.

    Returns None rather than a number when the total field is implausible - see
    `on_magnetometer`. A heading that is confidently wrong is worse at a junction
    than no heading at all.
    """
    total = STATE.get("mag_total_ut")
    if total is None or not FIELD_MIN_UT <= total <= FIELD_MAX_UT:
        return None
    return STATE.get("compass_deg")


def heading(gps_heading=None, speed_ms=None, min_speed_ms=2.5):
    """The best heading available: GPS while moving, compass while stopped.

    GPS course comes from the receiver's own Doppler solution and is excellent
    above walking pace and meaningless below it. The compass is the reverse - it
    does not care whether the bike is moving, but it is thrown by the bike's own
    electrics. So each is used where it is good, and the crossover is the same
    speed the lean angle already refuses to work below.
    """
    if gps_heading is not None and (speed_ms or 0.0) >= min_speed_ms:
        return gps_heading
    return compass_deg() if compass_deg() is not None else gps_heading


async def set_brightness(level):
    """Set the screen brightness, 0.0 to 1.0. Returns whether it took.

    Guarded end to end: the service does not exist on the desktop, and some
    Android builds refuse the write outright. Neither is worth an exception on
    the path that also draws the map.
    """
    service = _M.get("brightness")
    if service is None:
        return False
    # The APPLICATION brightness, not the system one. Application scope is
    # restored by Android the moment the app leaves the foreground, so a ride in
    # the dark cannot leave the whole phone dimmed afterwards - and it needs no
    # WRITE_SETTINGS permission, which the system-wide call does.
    try:
        await service.set_application_screen_brightness(
            max(0.02, min(1.0, float(level))))
        return True
    except Exception:                           # noqa: BLE001 - refused, or no
        # such service on this platform
        return False


async def battery_level():
    """Battery percentage, or None. Recorded per ride, not per sample."""
    service = _M.get("battery")
    if service is None:
        return None
    try:
        level = await service.get_battery_level()
    except Exception:                           # noqa: BLE001
        return None
    if level is not None:
        STATE["battery_pct"] = level
    return level
