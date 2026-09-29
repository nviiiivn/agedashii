"""The live ride: one place that knows where the bike is and where it is going.

Everything stateful about a ride in progress lives here, at module level, and
deliberately not inside a page. Two reasons, and both of them are bugs avoided
rather than tidiness:

* **Switching surfaces must not stop a recording.** If the `Ride` object hung
  off the map page, looking at the Log or changing a setting would tear down
  the page, drop the last reference, and silently end the ride she is halfway
  through. Module state outlives a repaint.
* **It can be tested.** Nothing in this file imports Flet. A test rides a whole
  route through `set_position` with a list of coordinates and asserts on what
  came back - the off-route decision, the re-route trigger, the recorded log -
  with no device and no map.

The one rule about routing here: `route_now` asks the on-device graph first and
only falls through to the server when she has allowed it, and whatever comes
back carries `source` saying which it was. A route silently produced by a
server while she believes the phone is self-sufficient is the failure that gets
someone stranded, so it is never guessed at.
"""
import os
import time

from agedashii_washi import routing

from . import guidance, prefs, sensors, telemetry

#: Oakland. Only ever used to centre the map before the first fix arrives.
HOME = (37.8044, -122.2712)

#: Seconds between automatic re-routes, so a rider who is off-route and staying
#: off-route does not burn the CPU searching once a second.
REROUTE_EVERY = 12.0

#: Movement that counts as "the bike is rolling", for autorecord. 2.5 m/s is
#: about 5.6 mph - faster than a GPS wandering while parked.
ROLLING_MS = 2.5

#: A fix worse than this is not used for anything. Matches the ride log's own
#: rejection threshold, deliberately: a 200 m error under a bridge must not be
#: good enough to declare her off route either.
MAX_ACC_M = 50.0

#: Seconds of GPS altitude behind the fallback climb rate. Much longer than the
#: barometer's six seconds, and it has to be: GPS altitude wanders by five to ten
#: metres, so a short window measures the noise rather than the hill.
CLIMB_GPS_WINDOW_S = 30.0

LIVE = {}


def reset():
    """Forget everything. A fresh launch, and what the tests start from."""
    LIVE.clear()
    LIVE.update({
        "pos": None,            # (lat, lon) of the last accepted fix
        "speed_ms": None,
        "heading": None,
        "alt_m": None,
        "acc_m": None,
        "t": None,
        "dest": None,           # (lat, lon, name)
        "route": None,
        "follower": None,
        "ride": None,           # a telemetry.Ride while recording
        "track": [],            # (lat, lon) of the ride so far, for the map
        "last_route_at": 0.0,
        "note": "",             # one line for the chip: why there is no route
        "prompt": "",           # the last thing guidance wanted said
        "prompt_at": 0.0,
        # Climb rate worked out from GPS altitude, for a phone with no
        # barometer. A Moto G Stylus has no barometer; a Pixel 8 Pro does.
        "climb_gps_mps": None,
        "_alt_history": [],
        # (mph, road class) for the road under the bike - guidance.limit_mph,
        # kept on every taken fix so the speeding cue and every page read one
        # current answer (it used to be worked out only while Ride painted).
        "limit": (None, ""),
        "_limit_at": 0.0,
        "_limit_at_pos": None,
    })


reset()


# -------------------------------------------------------------- destination

def set_destination(lat, lon, name=""):
    """Aim at a point. Does not route - `route_now` does, off the UI thread."""
    LIVE["dest"] = (float(lat), float(lon), name or "")
    prefs.set_last_dest(lat, lon, name)
    return LIVE["dest"]


def clear_destination():
    LIVE["dest"] = None
    LIVE["route"] = None
    LIVE["follower"] = None
    LIVE["note"] = ""
    LIVE["prompt"] = ""
    return None


def destination():
    return LIVE.get("dest")


# ------------------------------------------------------------------ routing

def graph_status():
    """What the router has to work with, in words. Never raises."""
    try:
        status = routing.status()
        installed = bool(routing.graph_path())
    except Exception:                    # noqa: BLE001 - never fatal
        return {"ready": False, "phase": "failed", "installed": False,
                "detail": "router unavailable"}
    # The phase and whether a file is installed, not just "ready": the graph
    # loads on the FIRST ROUTE, and "not ready" before that was shown on her
    # phone as "no graph" - read as offline routing dead. It was not.
    return {"ready": bool(routing.ready()), "phase": status.get("phase", ""),
            "installed": installed, "detail": status.get("detail", "")}


def route_now(allow_server=False, timeout=25.0):
    """Route from the last fix to the destination. Blocking - call off the UI.

    `allow_server=False` by default, which is the posture of the whole app: the
    offline graph is the router, and the server is a thing she opts into.
    Always returns a Route (or None with no destination), never raises, and the
    reason lives on the Route when it failed.
    """
    dest = LIVE.get("dest")
    start = LIVE.get("pos") or HOME
    if dest is None:
        LIVE["note"] = "no destination yet"
        return None
    # Claim the slot BEFORE searching, not after.
    #
    # `last_route_at` was only stamped once routing returned, so while a
    # search was running every incoming fix still saw the old timestamp, still
    # passed the 12 s gate, and asked for another. They serialise on the
    # router's search lock, and pure-Python A* holds the GIL, so a queue of
    # them starves the Python side while the Flutter map stays live - which
    # looks exactly like "not frozen, but nothing updates".
    LIVE["routing_now"] = True
    LIVE["last_route_at"] = time.time()
    try:
        route = routing.route(start, (dest[0], dest[1]),
                              allow_server=allow_server, timeout=timeout,
                              heading=LIVE.get("heading"))
    finally:
        LIVE["routing_now"] = False
        LIVE["last_route_at"] = time.time()
    # A FAILED REROUTE KEEPS THE OLD ROUTE.
    #
    # This used to null it. `set_position` then skips the follower and the
    # reroute check for ever after, because both are guarded on the route not
    # being None - so a single failed search killed guidance permanently, the
    # screen kept showing the last status it had, and nothing ever retried.
    # A stale route is worse than a fresh one and enormously better than none.
    if route.ok:
        LIVE["route"] = route
    if route.ok:
        follower = LIVE.get("follower")
        if follower is None:
            LIVE["follower"] = guidance.Follower(route, prefs.offroute_m(),
                                                 prefs.imperial())
        else:
            # Re-read the two preferences rather than trusting the ones the
            # follower was built with: she can change either from Settings
            # mid-ride, and a re-route is exactly when that takes effect.
            follower.offroute_m = float(prefs.offroute_m())
            follower.imperial = prefs.imperial()
            follower.set_route(route)
        # Where her recorded distances are, so the far and near prompts fire
        # AT them (guidance.prompt_points). Every route, not once: a clip she
        # imports mid-ride counts from the next re-route.
        LIVE["follower"].anchors_ft = _voice_anchors()
        LIVE["note"] = ""
    else:
        LIVE["note"] = route.reason or "no route"
    return route


#: Fire the far and near prompts AT her recorded distances (guidance.
#: prompt_points) instead of on the 60 s / 20 s clock. OFF until she has heard
#: it: it changes when every prompt fires, and AGEDASHII is putting the
#: before/after table to her. Turning it on is this one line.
ANCHOR_PROMPTS = False


def _voice_anchors():
    """The distances her recorded voice can say, in feet, nearest first - hers
    and the bundled ones (voice_import.distance_clips). () if none, if the
    voice store cannot be read, or while ANCHOR_PROMPTS is off: prompts then
    stay on the clock."""
    if not ANCHOR_PROMPTS:
        return ()
    try:
        from . import voice_import
        feet = (voice_import.distance_feet(n) for n in voice_import.distance_clips())
        return tuple(sorted(f for f in feet if f))
    except Exception as exc:                              # noqa: BLE001
        print(f"[navi] voice distances unreadable: {type(exc).__name__}: {exc}", flush=True)
        return ()


async def route_async(**kw):
    """`route_now` on a worker thread. Await from a Flet handler."""
    import asyncio
    return await asyncio.to_thread(route_now, **kw)


# ---------------------------------------------------------------- recording

def ride_dir():
    """Where ride logs live: the app's own storage, not shared, not the card.

    FLET_APP_STORAGE_DATA is what Flet sets on a device to the app-private data
    directory. Falling back to a dotdir under home is what makes this work on
    the desktop and in a test.
    """
    base = (os.environ.get("FLET_APP_STORAGE_DATA")
            or os.path.join(os.path.expanduser("~"), ".agedashii-navi"))
    return os.path.join(base, "rides")


def _on_shake(when, force_g):
    """A knock while recording becomes an incident, with what it measured.

    Wired here rather than inside sensors.py so that module stays ignorant of
    whether a ride exists. The force is the accelerometer's peak with gravity
    removed, which is the honest measure of how hard the hit was.
    """
    ride = LIVE.get("ride")
    if ride is None or not ride.recording:
        return None
    return ride.note_incident(f"impact {force_g:.1f} g", t=when)


def start_recording(name="", when=None, directory=None):
    """Begin a ride log. Returns the Ride, or the one already running."""
    existing = LIVE.get("ride")
    if existing is not None and existing.recording:
        return existing
    stamp = time.strftime("%Y%m%d-%H%M%S",
                          time.localtime(when if when else time.time()))
    folder = directory if directory is not None else ride_dir()
    from . import bikes
    key = prefs.machine()
    bike = bikes.profile(key)
    ride = telemetry.Ride(
        path=os.path.join(folder, f"ride-{stamp}.jsonl"),
        # Seconds, not just minutes.
        #
        # The FILE has always carried seconds (`stamp` above), but the NAME
        # stopped at the minute - and the server keys one file per name. So
        # two rides started in the same minute overwrote each other on the
        # dashboard, and the first one was simply gone. Stopping a ride and
        # immediately starting another is exactly what she does at a junction.
        name=name or time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(when if when else time.time())),
        hz=prefs.hz(),
        # The whole profile, not just its id: a reader of this log in two years
        # must not have to find the version of bikes.py that was current today.
        machine={"machine": key, "machine_name": bike["name"],
                 "cylinders": bike["cylinders"], "strokes": bike["strokes"],
                 "firing_per_rev": bikes.firing_ratio(key),
                 "redline_rpm": bike["redline_rpm"]})
    ride.start(when=when)
    LIVE["ride"] = ride
    LIVE["track"] = []
    # A knock is only worth logging while there is a log. Registered at the start
    # of a ride and left in place: `_on_shake` checks for itself.
    sensors.ON_SHAKE = _on_shake
    return ride


def stop_recording(when=None):
    """End the ride log. Returns its summary, or None if nothing was running."""
    ride = LIVE.get("ride")
    if ride is None or not ride.recording:
        return None
    ride.stop(when=when)
    return ride.summary()


def recording():
    ride = LIVE.get("ride")
    return bool(ride is not None and ride.recording)


# --------------------------------------------------------------- every fix

def set_position(lat, lon, *, speed_ms=None, heading=None, alt_m=None,
                 acc_m=None, t=None):
    """Take one GPS fix and work out everything that follows from it.

    Returns a dict the UI draws from:

        moved       False when the fix was rejected as too inaccurate
        status      guidance.Follower's status, or None with no route
        say         the prompt to announce, or ""
        reroute     True when a re-route should be asked for now
        rolling     True when the bike is moving, for autorecord

    The re-route decision is rate-limited here rather than in the page, because
    a page repaints for reasons that have nothing to do with a fix and would
    otherwise re-ask on every one of them.
    """
    t = float(t if t is not None else time.time())
    out = {"moved": False, "status": None, "say": "", "reroute": False,
           "rolling": False}

    if acc_m is not None and acc_m > MAX_ACC_M:
        return out

    LIVE["pos"] = (float(lat), float(lon))
    LIVE["speed_ms"] = speed_ms
    LIVE["heading"] = heading
    LIVE["alt_m"] = alt_m
    LIVE["acc_m"] = acc_m
    LIVE["t"] = t
    out["moved"] = True
    out["rolling"] = bool(speed_ms is not None and speed_ms >= ROLLING_MS)
    _track_gps_climb(alt_m, t)
    _refresh_limit(t)

    ride = LIVE.get("ride")
    if ride is not None and ride.recording:
        row = ride.add_fix(lat=lat, lon=lon, t=t, speed_ms=speed_ms,
                           heading=heading, alt_m=alt_m, acc_m=acc_m)
        if row is not None:
            LIVE["track"].append((row["lat"], row["lon"]))

    follower = LIVE.get("follower")
    if follower is not None and LIVE.get("route") is not None:
        status = follower.update(LIVE["pos"], speed_ms=speed_ms, now=t)
        out["status"] = status
        if status.get("say"):
            out["say"] = status["say"]
            # Which turn, how far, which band - for her recorded lines.
            out["say_parts"] = status.get("say_parts")
            LIVE["prompt"] = status["say"]
            LIVE["prompt_at"] = t
        if (follower.should_reroute()
                and not LIVE.get("routing_now")
                and t - LIVE.get("last_route_at", 0.0) >= REROUTE_EVERY):
            out["reroute"] = True
    return out


#: The speed limit is a spatial search of the road graph, so it is redone at
#: most this often and only once the bike has moved this far - roads do not
#: move, and in between the last answer is still true.
LIMIT_EVERY_S = 4.0
LIMIT_MOVE_M = 25.0


def _loaded_graph():
    """The routing graph only if it is already loaded: never blocks, never
    starts the load (routing.graph starts it on its way past - see
    pages.ride._graph)."""
    try:
        return routing.graph(timeout=0.0) if routing.ready() else None
    except Exception:                                     # noqa: BLE001
        return None


def _refresh_limit(t):
    """Keep LIVE["limit"] for where the bike is, from any page."""
    pos, at = LIVE["pos"], LIVE.get("_limit_at_pos")
    if at is not None and (telemetry.haversine_m(at, pos) <= LIMIT_MOVE_M
                           or t - LIVE.get("_limit_at", 0.0) < LIMIT_EVERY_S):
        return
    g = _loaded_graph()
    if g is None:
        return
    try:
        LIVE["limit"] = guidance.limit_mph(g, pos[0], pos[1], heading=LIVE.get("heading"))
    except Exception as exc:                              # noqa: BLE001
        print(f"[navi] speed limit failed: {type(exc).__name__}: {exc}", flush=True)
        return
    LIVE["_limit_at"], LIVE["_limit_at_pos"] = t, pos


def _track_gps_climb(alt_m, t):
    """Keep a climb rate from GPS altitude, for a phone with no barometer.

    A least-squares slope rather than (last - first) / span, because GPS altitude
    is noisy enough that two endpoints can disagree by ten metres while the
    thirty points between them describe a perfectly flat road. The slope uses
    every sample and is not moved much by one bad one.
    """
    if alt_m is None:
        return None
    history = LIVE["_alt_history"]
    history.append((t, float(alt_m)))
    while history and t - history[0][0] > CLIMB_GPS_WINDOW_S:
        history.pop(0)
    if len(history) < 4:
        LIVE["climb_gps_mps"] = None
        return None
    n = len(history)
    mean_t = sum(q[0] for q in history) / n
    mean_a = sum(q[1] for q in history) / n
    numerator = sum((q[0] - mean_t) * (q[1] - mean_a) for q in history)
    denominator = sum((q[0] - mean_t) ** 2 for q in history)
    LIVE["climb_gps_mps"] = (numerator / denominator) if denominator else None
    return LIVE["climb_gps_mps"]


def climb_mps():
    """Rate of climb, from the best source this phone has. None when unknown.

    The barometer wins wherever there is one: it resolves a metre and updates ten
    times a second, where GPS altitude wanders by five to ten metres and lags. On
    a phone without one - most mid-range Android, including a Moto G Stylus - the
    GPS slope above is the answer, and the gauge shows a rate rather than a dash.
    """
    barometric = sensors.STATE.get("climb_mps")
    if barometric is not None:
        return barometric
    return LIVE.get("climb_gps_mps")


def altitude_m():
    """Height, from the best source. Barometric if present, else the GPS fix."""
    baro = sensors.STATE.get("alt_baro_m")
    return baro if baro is not None else LIVE.get("alt_m")


def heading():
    """Which way the bike is pointing, from the best source.

    GPS course above walking pace, the magnetometer below it. Without this the
    marker freezes at whatever the last moving fix said, so rolling to a stop at a
    junction and turning the bike leaves the arrow pointing the way you came -
    which is exactly when knowing the direction matters most.
    """
    return sensors.heading(LIVE.get("heading"), LIVE.get("speed_ms"))


def summary_line():
    """One line about the ride so far, for the strip under the map."""
    ride = LIVE.get("ride")
    if ride is None:
        return ""
    return (f"{guidance.distance_text(ride.distance_m, prefs.imperial())} · "
            f"{int(ride.duration_s // 60)} min · {ride.count} samples")
