"""How bright it is outside, computed rather than measured.

Her ask: "a 'auto brightness' might be good. the brighter it is the brighter i
need it. the dark it is the less it needs to be bright. at night it's even
preferable to be all red / positive red only filters for night time."

The obvious way is an ambient light sensor, and Flet has no API for one. But the
app already knows where it is and what time it is, and the sun's position is a
solved problem - so the phase of the day is *calculated*, to well under a degree,
with no sensor, no network and no permission.

That is better than a light sensor for this job anyway. A sensor reads the shadow
of an overpass as night and a headlight behind you as day, so a sensor-driven
screen hunts between levels every few seconds on a road with trees. The sun's
altitude changes over minutes, so the phase is stable by construction.

THE PHASES, AND WHY THESE ANGLES

    day        sun above 6 deg     full brightness, the sunlight theme
    golden     6 deg down to 0     bright, but the glare is off the screen
    dusk       0 down to -6        civil twilight; headlights on, eyes adapting
    night      below -6 deg        red on black, screen well down

-6 degrees is civil twilight, the standard definition of when it is dark enough
to need artificial light, and the point where dark adaptation starts to matter.
Above +6 the sun is high enough that a dark screen is unreadable.

RED AT NIGHT IS NOT A STYLE CHOICE

Rod cells, which carry night vision, are nearly blind to deep red. A red-on-black
screen is readable without costing the dark adaptation that took twenty minutes
to build - which on an unlit road is the difference between seeing a deer and
not. That is why `theme_for` hands back a red palette rather than just a dim one.

The algorithm is the NOAA solar position calculation, the same one behind every
sunrise table, trimmed to what is needed here.
"""
import math
import time

#: (name, lowest sun altitude in degrees) - highest phase first.
PHASES = (("day", 6.0), ("golden", 0.0), ("dusk", -6.0), ("night", -90.0))

#: Screen brightness for each phase, 0.0 to 1.0.
BRIGHTNESS = {"day": 1.0, "golden": 0.8, "dusk": 0.5, "night": 0.22}

#: Which theme each phase asks for. Sunlight is unreadable indoors and right with
#: the sun behind you; redshift preserves night vision.
THEMES = {"day": "sunlight", "golden": "day", "dusk": "night",
          "night": "redshift"}


def _julian_day(when):
    """Julian day number for a unix timestamp."""
    return when / 86400.0 + 2440587.5


def solar_altitude(lat, lon, when=None):
    """The sun's altitude above the horizon in degrees. Negative after dark.

    Never raises: a missing position is answered with None, and the caller falls
    back to the clock.
    """
    if lat is None or lon is None:
        return None
    when = float(when if when is not None else time.time())
    centuries = (_julian_day(when) - 2451545.0) / 36525.0

    mean_long = (280.46646
                 + centuries * (36000.76983 + centuries * 0.0003032)) % 360.0
    mean_anom = 357.52911 + centuries * (35999.05029 - 0.0001537 * centuries)
    eccentricity = (0.016708634
                    - centuries * (0.000042037 + 0.0000001267 * centuries))

    anom_rad = math.radians(mean_anom)
    centre = (math.sin(anom_rad)
              * (1.914602 - centuries * (0.004817 + 0.000014 * centuries))
              + math.sin(2 * anom_rad) * (0.019993 - 0.000101 * centuries)
              + math.sin(3 * anom_rad) * 0.000289)
    apparent = (mean_long + centre - 0.00569 - 0.00478
                * math.sin(math.radians(125.04 - 1934.136 * centuries)))

    obliquity = 23.0 + (26.0 + (21.448 - centuries * (46.815 + centuries * (
        0.00059 - centuries * 0.001813))) / 60.0) / 60.0
    obliquity += 0.00256 * math.cos(math.radians(125.04 - 1934.136 * centuries))

    declination = math.degrees(math.asin(
        math.sin(math.radians(obliquity)) * math.sin(math.radians(apparent))))

    # The equation of time, in minutes: the gap between clock noon and the sun's
    # own noon, which runs to a quarter of an hour either way across a year.
    var_y = math.tan(math.radians(obliquity / 2.0)) ** 2
    eq_time = 4.0 * math.degrees(
        var_y * math.sin(2 * math.radians(mean_long))
        - 2 * eccentricity * math.sin(anom_rad)
        + 4 * eccentricity * var_y * math.sin(anom_rad)
        * math.cos(2 * math.radians(mean_long))
        - 0.5 * var_y * var_y * math.sin(4 * math.radians(mean_long))
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anom_rad))

    minutes_utc = (when % 86400.0) / 60.0
    true_solar = (minutes_utc + eq_time + 4.0 * lon) % 1440.0
    hour_angle = true_solar / 4.0
    hour_angle = hour_angle - 180.0 if hour_angle >= 0 else hour_angle + 180.0

    lat_rad = math.radians(lat)
    dec_rad = math.radians(declination)
    cos_zenith = (math.sin(lat_rad) * math.sin(dec_rad)
                  + math.cos(lat_rad) * math.cos(dec_rad)
                  * math.cos(math.radians(hour_angle)))
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def phase(lat=None, lon=None, when=None, altitude=None):
    """Which phase of the day it is: day, golden, dusk or night.

    With no position - before the first fix - it falls back to the clock. Cruder,
    never wrong by more than an hour or two, and only ever used for the few
    seconds before GPS answers.
    """
    if altitude is None:
        altitude = solar_altitude(lat, lon, when)
    if altitude is None:
        hour = time.localtime(when if when is not None else time.time()).tm_hour
        if 9 <= hour < 16:
            return "day"
        if 7 <= hour < 9 or 16 <= hour < 19:
            return "golden"
        if 19 <= hour < 21 or 5 <= hour < 7:
            return "dusk"
        return "night"
    for name, floor in PHASES:
        if altitude >= floor:
            return name
    return "night"


def brightness_for(name):
    """Screen brightness, 0.0 to 1.0, for a phase."""
    return BRIGHTNESS.get(name, 1.0)


def theme_for(name):
    """Which theme a phase asks for."""
    return THEMES.get(name, "night")


def sun_times(lat, lon, when=None, step_minutes=5):
    """(next sunrise, next sunset) as unix timestamps, or (None, None).

    The NEXT of each, searching forward from `when`, rather than the pair
    belonging to a calendar day. Two reasons, and the first is a bug this had:
    walking a UTC day at a Californian longitude finds that day's sunset before
    its sunrise, because local midnight falls in the middle of the UTC day - so
    the pair came back in the wrong order and any code comparing them was wrong
    for half the planet.

    The second is that "next" is the question actually being asked. A rider
    wanting to know how long the light will last wants the next sunset, not
    today's, and at 2 a.m. those are different answers.

    Found by walking forward and watching the altitude cross zero, rather than by
    inverting the position calculation: 288 steps at five minutes is a few
    milliseconds, and it cannot disagree with `solar_altitude`, which is what
    actually drives the phases.
    """
    if lat is None or lon is None:
        return None, None
    when = float(when if when is not None else time.time())
    rise = fall = None
    previous = solar_altitude(lat, lon, when)
    for index in range(1, (24 * 60) // step_minutes + 2):
        moment = when + index * step_minutes * 60.0
        current = solar_altitude(lat, lon, moment)
        if previous is not None and current is not None:
            if previous < 0 <= current and rise is None:
                rise = moment
            elif previous >= 0 > current and fall is None:
                fall = moment
        if rise is not None and fall is not None:
            break
        previous = current
    return rise, fall
