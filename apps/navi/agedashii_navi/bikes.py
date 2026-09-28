"""Which motorcycle, because the RPM maths is different for each one.

This is not a cosmetic setting. A four-stroke fires `cylinders / 2` times per
revolution, so the fundamental of the exhaust note maps to engine speed through a
ratio that changes with the engine:

    CB750, inline four    2 firings per rev    RPM = Hz x 30
    EX250, parallel twin  1 firing  per rev    RPM = Hz x 60

Get that wrong and every reading is out by a factor of two - exactly the kind of
error that looks plausible on a gauge.

IT ALSO SETS THE SEARCH BAND, AND THAT WAS A REAL BUG

The first version searched 20-200 Hz, which was written for a twin. On the CB750
the band has to reach 316 Hz to see 9500 rpm, so a four-cylinder would have been
silently capped around 6000 rpm and then started reporting harmonics as the
fundamental. The band is now derived from each bike's own idle and redline with a
margin either side: wide enough for the engine in front of it and no wider, since
every extra hertz is another chance for wind noise to win.

WHY IT IS A SETTING AND NOT A PROMPT

Her question: "so then also have a 'select bike' when recording or entering the
app? idk." It changes twice a year. A modal on every launch is friction on every
single ride for a decision that is almost never new - so the bike is a remembered
preference, it is shown on the ride surface so it can never be silently wrong,
switching it is one tap from there, and the ride log records it in the header so
an export is never ambiguous about whose engine produced it.
"""

#: Margin either side of the idle-to-redline span, as a fraction. The fundamental
#: at idle is not the lowest an engine produces - it dips below idle on overrun -
#: and a redline is a limit rather than a wall.
BAND_MARGIN = 0.25

PROFILES = {
    "cb750": {
        "name": "Nighthawk CB750",
        "detail": "1993 Honda, inline four, no baffles",
        # Inline four, four-stroke: two firing events per revolution.
        "cylinders": 4,
        "strokes": 4,
        "idle_rpm": 1000,
        "redline_rpm": 9500,
    },
    "ex250": {
        "name": "Ninja EX250",
        "detail": "2006 Kawasaki, parallel twin",
        "cylinders": 2,
        "strokes": 4,
        "idle_rpm": 1300,
        # The EX250F revs a long way further than anything else here, which is
        # what pushes the top of its search band up despite the four having twice
        # the firing rate.
        "redline_rpm": 14000,
    },
    "other": {
        "name": "Something else",
        "detail": "single-cylinder four-stroke, wide band",
        "cylinders": 1,
        "strokes": 4,
        "idle_rpm": 1200,
        "redline_rpm": 9000,
    },
}

DEFAULT = "cb750"


def names():
    """[(id, display name)] in declaration order, for a picker."""
    return [(key, value["name"]) for key, value in PROFILES.items()]


def profile(key=None):
    """One profile. An unknown id falls back rather than raising."""
    return PROFILES.get(key or DEFAULT, PROFILES[DEFAULT])


def firing_ratio(key=None):
    """Firing events per revolution. 2.0 for a four, 1.0 for a twin.

    `cylinders / (strokes / 2)`: a four-stroke completes one power stroke every
    two revolutions per cylinder, so a four fires twice per rev and a single
    fires once every two.
    """
    bike = profile(key)
    return bike["cylinders"] / (bike["strokes"] / 2.0)


def rpm_from_hz(hz, key=None):
    """Engine speed from the exhaust fundamental, or None."""
    if not hz or hz <= 0:
        return None
    return hz * 60.0 / firing_ratio(key)


def hz_from_rpm(rpm, key=None):
    """The fundamental a given engine speed should produce. The inverse."""
    if not rpm or rpm <= 0:
        return None
    return rpm * firing_ratio(key) / 60.0


def band_hz(key=None):
    """(low, high) of the band worth searching for this bike.

    Derived from its own idle and redline rather than fixed, for the reason in the
    module docstring. The floor is clamped at 15 Hz: below that is road rumble and
    the phone moving in its mount, never an engine.
    """
    bike = profile(key)
    low = hz_from_rpm(bike["idle_rpm"], key) or 20.0
    high = hz_from_rpm(bike["redline_rpm"], key) or 200.0
    return (max(15.0, low * (1.0 - BAND_MARGIN)), high * (1.0 + BAND_MARGIN))


def redline_hz(key=None):
    """The fundamental at the redline, for scaling a gauge."""
    return hz_from_rpm(profile(key)["redline_rpm"], key)


def redline_rpm(key=None):
    return profile(key)["redline_rpm"]


def plausible(rpm, key=None):
    """Whether an estimate is inside what this engine can actually do.

    A peak-pick can land on a harmonic or on a gust of wind. An engine cannot turn
    at 400 rpm and it cannot turn at 20,000, so a figure outside its own range -
    with a margin - is discarded rather than shown.
    """
    if rpm is None:
        return False
    bike = profile(key)
    return (bike["idle_rpm"] * (1.0 - BAND_MARGIN) <= rpm
            <= bike["redline_rpm"] * (1.0 + BAND_MARGIN))
