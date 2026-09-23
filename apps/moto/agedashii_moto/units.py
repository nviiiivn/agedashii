"""Miles or kilometres, everywhere a distance is drawn.

`agedashii_washi.prefs.unit_system()` has existed the whole time and the
dashboard's Settings has always offered it. Nothing in this app read it:
every distance on every surface was written as a literal - `f"{mi:.1f} mi"`
in Trips, `/ 1609.344` in XNAV's recorder, `f"{odo} mi"` in the Garage
records - so switching the setting to metric changed nothing at all and the
control was a lie.

This module is the one place that converts, and the one place that names a
unit. A page asks for a formatted string; it never divides by 1609.344 and
never writes "mi" itself.

WHAT THE SERVER SENDS, AND WHAT THAT MEANS HERE
    The dashbored server and the Valhalla routes speak imperial: fields
    are named `distance_mi`, `max_speed_mph`, `ascent_ft`, `odo` (miles).
    Those are the *stored* numbers and they are not touched - converting
    on the way into the database would make every record ambiguous the
    first time the setting changed. Conversion happens on the way to the
    screen, once, here.

    So every function below takes the imperial number the data already is
    and returns what to show. `metres()` is the exception and exists
    because the GPS recorder accumulates in metres, which is neither.

ABSENT IS NOT ZERO
    A ride that has not been routed has no distance, and this app has
    previously shown invented numbers. Every formatter takes a `dash` and
    returns it for None, "", a non-number or an already-dashed value, so a
    missing measurement stays missing through the conversion rather than
    becoming a confident 0.0 km.
"""
from agedashii_washi import prefs

#: Exact, not approximate: the international mile is defined as 1609.344 m.
M_PER_MI = 1609.344
KM_PER_MI = 1.609344
M_PER_FT = 0.3048
FT_PER_MI = 5280

#: What an em dash, or anything else already standing in for "we do not
#: know", looks like arriving at a formatter. Passed straight back out.
_ALREADY_DASHED = ("—", "-", "–", "", "?")


def metric():
    """True when she has asked for km. Read fresh - the setting is live."""
    return prefs.unit_system() == "metric"


def distance_unit():
    return "km" if metric() else "mi"


def speed_unit():
    return "km/h" if metric() else "mph"


def elevation_unit():
    return "m" if metric() else "ft"


def short_unit():
    """The unit used below a tenth of the big one - feet, or metres."""
    return "m" if metric() else "ft"


def _number(value):
    """`value` as a float, or None if it is not a number at all."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip() in _ALREADY_DASHED:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- conversion
def distance(mi):
    """`mi` miles in the chosen unit, as a number. None stays None."""
    n = _number(mi)
    return None if n is None else (n * KM_PER_MI if metric() else n)


def speed(mph):
    """`mph` in the chosen unit, as a number. None stays None."""
    n = _number(mph)
    return None if n is None else (n * KM_PER_MI if metric() else n)


def elevation(feet):
    """`feet` in the chosen unit, as a number. None stays None."""
    n = _number(feet)
    return None if n is None else (n * M_PER_FT if metric() else n)


def metres_to_distance(m):
    """Metres - what the GPS recorder counts in - in the chosen unit."""
    n = _number(m)
    return None if n is None else (n / 1000.0 if metric() else n / M_PER_MI)


def per_distance(per_mile):
    """Something *per mile* as the same thing per the chosen unit.

    Garage's "cost / mile". The conversion runs the other way from
    `distance()` - a cost spread over more kilometres than miles is a
    smaller number per kilometre - and getting that backwards would make
    riding to Reno look like it cost 2.6x what it did.
    """
    n = _number(per_mile)
    return None if n is None else (n / KM_PER_MI if metric() else n)


# -------------------------------------------------------------- formatting
def _fmt(value, digits, dash):
    if value is None:
        return dash
    return f"{value:.{digits}f}" if digits else f"{round(value):.0f}"


def distance_value(mi, digits=1, dash="—"):
    """Just the number, for a stat tile that draws its unit separately."""
    return _fmt(distance(mi), digits, dash)


def distance_text(mi, digits=1, dash="—"):
    """'12.3 mi' / '19.8 km'. The dash carries no unit - it is not a
    measurement, and "— mi" beside "— km" would imply we knew which."""
    out = distance_value(mi, digits, dash)
    return out if out == dash else f"{out} {distance_unit()}"


def metres_text(m, digits=1, dash="—"):
    """A recorded track's running total, from metres."""
    value = _fmt(metres_to_distance(m), digits, dash)
    return value if value == dash else f"{value} {distance_unit()}"


def speed_value(mph, digits=0, dash="—"):
    return _fmt(speed(mph), digits, dash)


def speed_text(mph, digits=0, dash="—"):
    out = speed_value(mph, digits, dash)
    return out if out == dash else f"{out} {speed_unit()}"


def elevation_value(feet, digits=0, dash="—"):
    return _fmt(elevation(feet), digits, dash)


def elevation_text(feet, digits=0, dash="—"):
    out = elevation_value(feet, digits, dash)
    return out if out == dash else f"{out} {elevation_unit()}"


def odometer_text(miles, dash="—"):
    """A whole-number odometer or service interval - '8,240 mi', '13,261 km'.

    Thousands separated, because an odometer is the one number in this app
    that regularly runs to five digits and `82401` is unreadable.
    """
    n = distance(miles)
    if n is None:
        return dash
    return f"{round(n):,} {distance_unit()}"


def near_text(mi, dash="—"):
    """'now' / '300 ft' / '1.4 mi', or 'now' / '90 m' / '2.3 km'.

    The turn-by-turn countdown. Below a tenth of the big unit it switches
    to the small one, because "0.0 mi to the turn" is not a distance anyone
    can act on.
    """
    n = _number(mi)
    if n is None:
        return dash
    if n < 0.01:
        return "now"
    if n < 0.1:
        small = round(n * (1000 * KM_PER_MI) if metric() else n * FT_PER_MI)
        return f"{small} {short_unit()}"
    return distance_text(n)
