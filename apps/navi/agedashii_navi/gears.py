"""Which gear she is in, from road speed and engine speed.

Her idea: "EST. GEAR (SPEED MIGHT MOSTLY DO IT ALONE, BUT ESPECIALLY PAIRED WITH
THE RPM SOUNDS I IMAGINE IT SHOULD BE PRETTY EASY TO GUESTTIMATE OR EVEN JUST
SHOW 'WHAT GEAR YOU SHOULD BE AT' lol) +++++ a 'change gear UP/DOWN' light would
be super cute".

It is easy, and it is exact rather than a guess - as long as both inputs are
real. In any gear the wheel and the crank are locked together through a fixed
ratio, so

    ratio = road speed / engine speed

is a constant for that gear and nothing else. Every gear has its own, they are
well separated, and the nearest one wins. No learning, no calibration.

WHAT MAKES IT WRONG, AND WHAT IS DONE ABOUT IT

* **The clutch.** Pulled in, or slipping away from a stop, the two are not
  locked and the ratio is meaningless. Below `MIN_SPEED_MS` nothing is reported.
* **The RPM estimate.** Gear is only as good as the engine speed behind it, and
  that comes from the microphone. With the mic off there is no gear, and
  `estimate` says so rather than inventing one from speed alone - speed alone
  answers "which gear does she usually use at this speed", which is a different
  question wearing the same hat.
* **Tyre wear and chain stretch.** These move every ratio by a percent or two
  together. `confidence` reports how well the best match actually fit, so a
  systematically-off table shows up as low confidence everywhere rather than as
  a confidently wrong gear.

THE RATIOS

Published overall ratios (primary x gear x final) converted to metres of road
per engine revolution with each bike's rolling circumference. They are close but
they are not measured on her bikes - `learn()` exists for that: feed it observed
(speed, rpm) pairs and it returns the ratios that actually fit, which can then be
written back into the table.
"""
import math

#: Below this the clutch is probably not fully out and the ratio is noise. 3 m/s
#: is about 7 mph - above a slipping clutch, below any gear's cruise.
MIN_SPEED_MS = 3.0
#: How far a measured ratio may sit from a gear's ratio and still match, as a
#: fraction. Gears are separated by 15-30%, so 12% accepts real wear and chain
#: stretch while never reaching the next gear.
MATCH_TOLERANCE = 0.12
#: Fraction of the redline at which the shift-up light comes on.
SHIFT_UP_FRACTION = 0.85
#: Engine speed below which the shift-down light comes on. Lugging an engine is
#: worse for it than revving one.
SHIFT_DOWN_RPM = 2200

#: Metres of road per engine revolution, per gear, lowest first.
#:
#: CB750: 5 speeds, 130/90-16 rear, about 2.02 m rolling circumference.
#: EX250: 6 speeds, 110/90-17 rear, about 1.88 m.
#:
#: Sanity check these against road speed rather than trusting them by eye - the
#: first version of this table was a factor of ten small, which put the CB750 at
#: 2.8 mph in first at 5000 rpm and made every gear unrecognisable, because the
#: implied speeds fell below the clutch floor. At 5000 rpm these give roughly
#: 27 / 38 / 48 / 56 / 64 mph on the four, which is what that bike actually does.
RATIOS = {
    "cb750": (0.148, 0.206, 0.258, 0.303, 0.345),
    "ex250": (0.108, 0.150, 0.186, 0.217, 0.243, 0.264),
    "other": (0.140, 0.200, 0.255, 0.300, 0.340),
}


def _table(machine=None):
    return RATIOS.get(machine or "cb750", RATIOS["cb750"])


def gear_count(machine=None):
    return len(_table(machine))


def ratio(speed_ms, rpm):
    """Metres of road per engine revolution, or None.

    The whole method in one line. Guarded against a zero or absent engine speed,
    which is what "the microphone is off" looks like from here.
    """
    if not speed_ms or not rpm or rpm <= 0 or speed_ms <= 0:
        return None
    return speed_ms / (rpm / 60.0)


def estimate(speed_ms, rpm, machine=None):
    """(gear, confidence) or (None, 0.0).

    `gear` is 1-based. `confidence` is 1.0 for an exact match and falls to 0.0 at
    the edge of `MATCH_TOLERANCE`, so the caller can draw a firm number or a
    hesitant one instead of treating a poor fit as a good one.

    Returns None - not a guess - when the clutch may be in, when there is no
    engine speed, or when nothing fits. A gear indicator that is confidently
    wrong is worse than one that admits it does not know.
    """
    if speed_ms is None or speed_ms < MIN_SPEED_MS:
        return None, 0.0
    measured = ratio(speed_ms, rpm)
    if measured is None:
        return None, 0.0
    best, best_error = None, None
    for index, value in enumerate(_table(machine)):
        error = abs(measured - value) / value
        if best_error is None or error < best_error:
            best, best_error = index + 1, error
    if best_error is None or best_error > MATCH_TOLERANCE:
        return None, 0.0
    return best, max(0.0, 1.0 - best_error / MATCH_TOLERANCE)


def shift_hint(rpm, gear, machine=None, redline_rpm=None):
    """"up", "down" or "" - what the shift light should say.

    Up near the redline, down when the engine is lugging. Neither fires without a
    gear, because "shift up" in top gear is not advice, and neither fires without
    an engine speed at all.
    """
    if rpm is None or gear is None:
        return ""
    from . import bikes
    redline = redline_rpm or bikes.redline_rpm(machine)
    if rpm >= redline * SHIFT_UP_FRACTION and gear < gear_count(machine):
        return "up"
    if rpm <= SHIFT_DOWN_RPM and gear > 1:
        return "down"
    return ""


def learn(samples, gears=None):
    """Work out a bike's real ratios from observed (speed_ms, rpm) pairs.

    The published ratios are close; a worn tyre or a stretched chain moves them.
    This splits the measured ratios into `gears` groups and returns each group's
    median, which is what should be written back into `RATIOS`.

    Split at the largest gaps, because gear ratios are separated by far more than
    the scatter within one gear - the biggest gaps ARE the gear changes. Median
    rather than mean, because a sample taken mid-shift with the clutch in lands
    nowhere near its neighbours and would drag a mean across a gap.
    """
    values = sorted(r for r in (ratio(s, p) for s, p in samples)
                    if r is not None)
    if not values:
        return []
    gears = gears or 5
    gaps = sorted(((values[i + 1] - values[i], i)
                   for i in range(len(values) - 1)), reverse=True)
    cuts = sorted(index for _gap, index in gaps[:max(0, gears - 1)])
    groups, start = [], 0
    for cut in cuts + [len(values) - 1]:
        group = values[start:cut + 1]
        if group:
            groups.append(group[len(group) // 2])
        start = cut + 1
    return groups


def speed_for(gear, rpm, machine=None):
    """How fast a given gear and engine speed implies, in m/s. The inverse."""
    table = _table(machine)
    if not 1 <= (gear or 0) <= len(table) or not rpm:
        return None
    return table[gear - 1] * (rpm / 60.0)


def best_gear_for(speed_ms, machine=None, target_rpm=4000):
    """Which gear puts the engine nearest `target_rpm` at this road speed.

    Her "or even just show 'what gear you should be at' lol" - and unlike
    `estimate` this needs no microphone at all, because it is a question about
    the gearbox rather than a measurement of it.

    Compared in log space so being 1000 rpm under the target counts the same as
    being the same factor over it; in linear terms a tall gear would always look
    closer simply because its error has less room to grow.
    """
    if not speed_ms or speed_ms < MIN_SPEED_MS:
        return None
    best, best_error = None, None
    for index, value in enumerate(_table(machine)):
        rpm = (speed_ms / value) * 60.0
        error = abs(math.log(rpm / target_rpm))
        if best_error is None or error < best_error:
            best, best_error = index + 1, error
    return best
