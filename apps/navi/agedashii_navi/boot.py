"""The loading screen, and the work it is actually waiting for.

Her ask: "i want a loading screen -- overlay black if need be and just have the
loader over everything ... 'LOADING MAPS..' 3 SEC; 'LOADING TELEMETRY..' 3 SEC;
'LOADING POI/ROUTING..' 3 SEC; 'LOADING UI..' :: that should give enough time
hopefully to 'wait' and stagger the loads?"

The staggering is the real point, and it is worth being precise about why: the
app was crashing on her phone because everything competed at once - the map, the
sensors and an 86 MB routing graph, all in the first second, on one thread. Doing
them in order, one at a time, is the fix. The screen in front of it is how that
stops looking like a hang.

FIXED THREE-SECOND WAITS ARE NOT WHAT SHE ACTUALLY WANTS

Twelve seconds of sleeping would make a fast launch slow and a slow one no more
honest. So each stage does its work and moves on when the work is done, with
`MIN_DWELL_S` so a stage that finishes instantly is still readable rather than a
flicker. Same look, and the words on screen are true.

WHAT EACH STAGE IS FOR

    maps       start the tile server on the on-device archive and confirm it
               answers. First because it is what the map draws from, and cheap.
    telemetry  attach the sensors and see which ones actually report. Skipped
               entirely when she has telemetry set to load on demand.
    routing    warm the cached route and POI lists. NOT the road graph: that is
               86 MB, it is what Android killed the app over, and it loads on
               the first routing request instead.
    ui         the theme tables and the daylight phase, so the first frame is
               already the right colour rather than correcting itself.

ONCE PER LAUNCH, NOT ONCE PER GLANCE

Her "hopefully the app doesn't need the loader upon every open if it's in the
background or recent apps". `booted()` is module state, so it survives every
repaint, every surface change and every trip through the recent-apps list -
Android only clears it by killing the process, which is exactly when the work
genuinely does have to happen again.
"""
import time

#: (key, what the screen says, whether it can be skipped on demand)
STAGES = (
    ("maps", "LOADING MAPS…", False),
    ("telemetry", "LOADING TELEMETRY…", True),
    ("routing", "LOADING POI/ROUTING…", False),
    ("ui", "LOADING UI…", False),
)

#: Minimum seconds a stage stays on screen.
#:
#: Was 0.6, and her answer was "the 'loaders' fly by, they don't take 3 seconds
#: and i doubt htey give time for anything to load". 1.5 is long enough to read
#: the words and to see that four distinct things happened, which is the whole
#: reason the screen exists - the staggering does the work, the screen is how
#: she can tell it is happening.
MIN_DWELL_S = 1.5

_S = {"booted": False, "at": 0.0, "notes": {}}


def booted():
    """Whether this process has already done its startup work."""
    return bool(_S["booted"])


def mark_booted():
    _S["booted"] = True
    _S["at"] = time.time()
    return True


def reset():
    _S["booted"] = False
    _S["at"] = 0.0
    _S["notes"] = {}


def notes():
    """What each stage found, for Settings and the help screen."""
    return dict(_S["notes"])


def stage_maps():
    """Start the tile server and say what it is serving."""
    from . import prefs, tiles
    return tiles.pick(prefs.tiles())["note"]


def stage_telemetry(page=None, force=False):
    """Attach the sensors, unless she asked for them on demand.

    THIS STAGE USED TO SUBSCRIBE UNCONDITIONALLY, and that was half the bug.
    `prefs.sensors_on_demand` ships True and was never once consulted, so five
    sensors came up during the loading screen - before any surface existed to
    read them - and stayed up for the life of the process. Four fire every
    400 ms and the barometer every second: roughly a dozen trips across the
    Python/Flutter channel every second, for ever, whether or not a single
    gauge was on screen.

    Agedashii Moto has no telemetry at all, and Moto is fast. That is the
    whole difference between the two apps.

    With on-demand set, nothing is subscribed here; the Ride page attaches only
    when the dash is actually drawn. The stage reports "on demand" rather than
    naming sensors it has not subscribed, because listing hardware it did not
    ask about would be inventing a reading.

    `force=True` is how Settings' re-probe button still works: that is a
    deliberate act, so it subscribes regardless of the preference.
    """
    from . import prefs, sensors
    if prefs.sensors_on_demand() and not force:
        return "on demand"
    if page is not None:
        sensors.attach(page)
    deadline = time.time() + 1.2
    while time.time() < deadline and not sensors.STATE["seen"]:
        time.sleep(0.1)
    seen = sensors.available()["seen"]
    return f"{len(seen)} sensors" if seen else "no sensors reported"


def stage_routing():
    """Warm the cached route and POI lists. NOT the road graph.

    Deliberately not the graph: it is 86 MB and unpacks into several hundred
    megabytes, which is what Android was killing the app over. It loads on the
    first actual routing request, on a worker thread.
    """
    from . import places
    rows, note = places.db_routes()
    return note or f"{len(rows)} saved routes"


def stage_ui():
    """Settle the theme before the first frame, so nothing corrects itself."""
    from . import daylight, prefs, theme
    if prefs.auto_theme():
        phase = daylight.phase()
        theme.set_theme(daylight.theme_for(phase))
        return f"{phase}, {theme.THEME_NAMES.get(theme.S['theme'], '')}"
    return theme.THEME_NAMES.get(prefs.theme_key(), prefs.theme_key())


def run_stage(key, page=None):
    """Do one stage's work. Never raises - a failed stage is a note, not a stop.

    A stage that throws must not strand her on a loading screen. Whatever went
    wrong is recorded and shown; the app still opens.
    """
    started = time.time()
    try:
        if key == "maps":
            note = stage_maps()
        elif key == "telemetry":
            note = stage_telemetry(page)
        elif key == "routing":
            note = stage_routing()
        elif key == "ui":
            note = stage_ui()
        else:
            note = ""
    except Exception as exc:                 # noqa: BLE001 - see the docstring
        note = f"failed: {exc}"
    _S["notes"][key] = note
    # The dwell is measured AROUND the work, not added to it: a stage that took
    # two seconds does not then sit for another.
    remaining = MIN_DWELL_S - (time.time() - started)
    if remaining > 0:
        time.sleep(remaining)
    return note


def stages_for(skip_telemetry=False):
    """The stages to run, in order."""
    return [(key, label) for key, label, skippable in STAGES
            if not (skippable and skip_telemetry)]
