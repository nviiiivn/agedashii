"""The surface she actually rides with: a map, and four numbers on top of it.

The layout is the argument. A navigation screen on a bike gets looked at for
about a third of a second at a time, so the page is three bands and nothing
else:

    top      the next turn - a glyph, the instruction, the distance
    middle   the map, filling everything left over
    bottom   speed, the typical limit, lean, and what the ride has done

The turn band is at the TOP rather than floating over the map, because a banner
on tiles has to carry its own background to stay legible and then it is a band
anyway - one that hides the road ahead of her instead of sitting above it. It
is absent entirely when there is no route, so the map gets the height back.

Speed is the biggest thing on the screen. It is read most often, it is the one
number that matters when there is no route at all, and it is the only one she
can be fined for.

WHAT UPDATES WHEN

A fix arrives roughly once a second. Redrawing the whole page at 1 Hz would
rebuild the map control and throw away its pan and zoom, so `on_position`
mutates the controls that already exist - the marker's coordinates, the
polyline's points, the text of the readouts - and calls `page.update()`. The
page is only rebuilt from scratch when something structural changes: the theme,
the tile style, a new route.

HANDLERS ON THE MAP ARE RE-BOUND ON EVERY BUILD

The Map outlives `build()` (see `_map_parts`: it is built once and reused, or
every repaint refetched forty tiles). A handler set on it once keeps the
closures of the build that set it - its `page`, its `paint`, its bands - and
after the next repaint it quietly acts on controls that are no longer on
screen. So anything assigned to the Map (`on_long_press` for drop-a-pin) is
assigned again at the end of every `build()`. This bit the tile refetch,
centre-on-bike and drop-a-pin; the fourth person does not have to find it.

SPOKEN PROMPTS

Every prompt is shown - large, at the top, for `PROMPT_HOLD_S` seconds - and,
when "Announce turns" (`prefs.voice()`) is on, also SAID, through speech.py:
the phone's own text-to-speech engine, reached through pyjnius because Flet has
no speech control of its own. It is polled rather than callback-driven -
pyjnius under Flet cannot implement a Java listener - and it asks for ducking
audio focus with the navigation-guidance attributes, so her music dips and
comes back. When no offline voice is installed, `voice_band` says so on this
page in words she can act on, rather than going quietly silent.

Her own recorded lines come first where she has them (voicelines.py, clips
from voice_import): one per maneuver, a distance line before it, and four
situations - start, off route, re-routing, speeding - each said on its edge
(`_announce`). A clip she has not recorded yet is the phone's voice for that
item alone, never silence.
"""
import time

import flet as ft

from agedashii_washi.routing import haversine_m

from .. import (audio, bikes, character, daylight, gauges, gears, guidance, live, location,
                prefs, sensors, speech, telemetry, theme, tiles, ui, uploads,
                voice_import, voicelines)
from ..theme import BRD, PAD, PAL, RAD

try:                      # optional, so the app still starts without them
    import flet_map as fmap
    import flet_geolocator as fgeo
    MAPS_AVAILABLE = True
except ImportError:       # pragma: no cover - depends on the build
    fmap = fgeo = None
    MAPS_AVAILABLE = False

#: How long a prompt stays on screen after it fires.
PROMPT_HOLD_S = 8.0
#: The lean angle that fills the bar. 45 degrees is a lot on a road bike, which
#: makes the bar useful across the range she will actually see.
LEAN_FULL_DEG = 45.0
#: Seconds between battery readings. The level moves a percent every few minutes;
#: reading it on every fix would be a platform call a second for nothing.
BATTERY_EVERY_S = 120.0
#: Seconds between dash rebuilds. Once a second: every rebuild is forty-odd
#: controls serialised and sent to Flutter, and on a phone that was a large part
#: of why the map would not pan.
DASH_EVERY_S = 1.0
#: Degrees the heading must move before the bike marker is rebuilt.
MARKER_DEGREES = 8.0
#: How much of the recorded track is drawn. The rest is in the log.
TRACK_POINTS = 400
#: Height to keep clear at the bottom when the ribbon is along that edge, so the
#: floating buttons do not sit on top of the numbers.
RIBBON_CLEARANCE = 104

#: Controls and services that must survive a repaint. The geolocator in
#: particular is a Flet *service*: it is unregistered the moment nothing holds
#: a strong reference, so rebuilding the page would silently stop the GPS.
_M = {}


#: How long a situation line that fired on the fix asking for a re-route is
#: held for the answer. If the new route's first prompt is "now", the line is
#: dropped and the turn plays alone: "what the h-" cut after 0.3 s by "turn
#: left" was the stutter (minion 2, 5554). The router answers in 16-300 ms.
#: AGEDASHII's ruling; the Kotlin app holds the same way.
HOLD_S = 1.5


def _announce(out, limit):
    """Say what one taken fix calls for: situation lines on their edges, then
    the prompt, as ONE playlist - speech plays the newest playlist and drops
    the rest, so "re-routing" queued separately would cut "off route" off.

    The edges are tracked whether or not "Announce turns" is on, so switching
    it on mid-ride does not blurt out a state she is already in. Returns the
    items handed to speech, for tests.
    """
    cues = _M.setdefault("cues", voicelines.Cues())
    over = guidance.over_limit(live.LIVE.get("speed_ms"), limit)
    fired = cues.update(out.get("status"), live.LIVE.get("dest"),
                        out.get("reroute"), over, time.time())
    names = _speeding_alone(fired, out, over)
    if not prefs.voice():
        return []
    # Every clip through character.clip: her recording, else his wild line,
    # else the Waze one, else the phone (voicelines.Wild).
    parts = out.get("say_parts") if out.get("say") else None
    band = (parts or {}).get("band")
    situations = [item for name in names
                  for item in voicelines.situation(name, character.clip)]
    # His lines are chosen first: how many there are is part of the prompt's
    # shape, which the Arranger never repeats twice running.
    # Moods from the cues as they FIRED: a speeding edge held back from this
    # turn still rules out banter on it - "haha" as she goes over the limit
    # (AGEDASHII's ruling).
    lines = character.flourishes(names, parts, fired)
    prompt = []
    if out.get("say"):
        piece = voicelines.core(parts, lambda name: character.clip(name, band),
                                prefs.clip_streets(), prefs.imperial(),
                                distances=voice_import.distance_clips(),
                                halves=character.halves)
        prompt = character.arrange(piece, band, text=out["say"])
        # "and then" the next turn, when it comes too soon for its own
        # warning. Our own sentence already says it (guidance.spoken).
        if piece is not None and parts.get("then"):
            prompt += voicelines.and_then(
                parts["then"], lambda name: character.clip(name, "now"))
    # On the "now" band the TURN goes first and cuts in: six seconds from the
    # junction, a 1.5 s speeding line in front of it ate a quarter of the
    # warning (minion 4's audit, #10).
    now = band == "now"
    items = prompt + situations if now else situations + prompt
    # His lines always LAST, after the whole instruction and any situation,
    # so they can never come before the turn or delay it.
    if items:
        items += lines
        if out.get("reroute") and ("offroute" in names or "rerouting" in names):
            # Held for the re-route's answer (HOLD_S, _release).
            _M["holds"] = _M.get("holds", 0) + 1
            _M["held"] = {"items": items, "id": _M["holds"]}
            return []
        speech.play(items, urgent=now)
    return items


def _speeding_alone(names, out, over):
    """`names` with "speeding" only where it may be said: ALONE.

    minion 1's Flet rides: "big mistake, 0.1 mile, turn left, drunk"; on
    arriving, "mission accomplished, big mistake, investigation over"; "cop,
    stop that" 1.6 s after the choppa. A heckler, not a character. So the
    line is never in a turn's playlist, never with another situation line,
    never over a line already playing, and never once she has arrived. Its
    edge (Cues, SPEEDING_EVERY_S) is kept as OWED until a quiet fix while she
    is still over the limit, and forgotten when she is not, or when Announce
    turns is off.
    """
    names = list(names)
    if "speeding" in names:
        names.remove("speeding")
        _M["speeding_owed"] = True
    follower = live.LIVE.get("follower")
    if (not over or not prefs.voice()
            or getattr(follower, "arrived", False)):
        _M.pop("speeding_owed", None)
        return names
    quiet = not names and not out.get("say") and not speech.voice_active()
    if _M.get("speeding_owed") and quiet:
        _M.pop("speeding_owed", None)
        names.append("speeding")
    return names


def _release(route):
    """The re-route has answered: say or drop the situation line held for it.

    Dropped when the new route's first prompt is "now" (Follower.peek) - the
    turn plays alone and clean. Otherwise it plays, and the new route's first
    prompt waits for it, as before. A refused re-route says the held line and
    then the refusal, as one playlist. Returns the items handed to speech.
    """
    held = _M.pop("held", None)
    before = held["items"] if held else []
    if route is not None and getattr(route, "ok", False):
        follower = live.LIVE.get("follower")
        band = (follower.peek(live.LIVE.get("pos"), live.LIVE.get("speed_ms"))
                if follower is not None else None)
        if before and band == "now":
            print("[navi] speech: dropped "
                  + " + ".join(v if k == "tts" else f"[{v.rsplit('/', 1)[-1]}]"
                               for k, v, _f in before)
                  + " - the new route starts with a turn", flush=True)
            return []
        if before:
            speech.play(before)
        return before
    return _refused(route, before)


async def _let_go(hold):
    """Say a held line whose re-route has not answered within HOLD_S."""
    import asyncio
    await asyncio.sleep(HOLD_S)
    held = _M.get("held")
    if held is not None and held["id"] == hold:
        _M.pop("held", None)
        speech.play(held["items"])


def _refused(route, before=()):
    """Say a refused re-route - once per off-route spell (Cues.refused) -
    after `before`, the situation line held for it (_release).

    It was silence: "off route", "re-routing", then nothing, while the banner
    said re-routing and the search retried every 12 s. The re-route is
    offline-only and the map ends at the Bay, so this is where she meets it.
    Returns the items handed to speech, for tests.
    """
    if route is not None and getattr(route, "ok", False):
        return []
    cues = _M.setdefault("cues", voicelines.Cues())
    items = list(before)
    if cues.refused() and prefs.voice():
        words = voicelines.refusal(getattr(route, "reason", "") or "")
        # "sorry" only AFTER the words - and with no working voice the words
        # are dropped (speech.py), so an apology alone would be an apology
        # for nothing.
        if speech.STATE.get("ready"):
            words += character.flourish("sorry")
        items += words
    if items:
        speech.play(items)
    return items


def _update(page):
    """Push the mutated controls, and SAY SO when that fails.

    This was `except Exception: pass`. It is called once per GPS fix, so a
    page update that had started failing would have gone on failing silently
    for ever - the screen would simply stop changing, which is exactly what
    "the app is frozen" looks like from outside.

    Two days were spent guessing at that symptom with this line in place. It
    prints now. A failure here is at most one line per fix, and if it is
    printing every second then that is the answer rather than the noise.
    """
    try:
        page.update()
    except Exception as exc:                           # noqa: BLE001
        now = time.time()
        # One line a second at most: a failure that repeats at the fix rate
        # must not become its own traffic problem.
        if now - _M.get("update_err_at", 0.0) > 1.0:
            _M["update_err_at"] = now
            print(f"[navi] page.update failed: {type(exc).__name__}: {exc}",
                  flush=True)


def _showing_ride(page):
    """Is the map the surface on screen right now?

    The shell records it on the page. Absent - in a test, or before the first
    render - assume yes, because the old behaviour was to always paint and
    this should only ever REMOVE work, never add a way to miss a frame.
    """
    return getattr(page, "_navi_surface", "ride") == "ride"


def _graph():
    """The routing graph ONLY if it is already loaded. Never blocks, never loads.

    `routing.ready()` first, and that is the whole point of this function.
    `routing.graph()` calls `start_loading()` on its way past - even with
    `timeout=0` - so asking it "are you ready?" is what actually *starts* the
    86 MB read. This is drawn from the repaint path, so that one line would
    trigger a several-hundred-megabyte load on the first frame of the map and
    hand Android every reason to kill the app.
    """
    from agedashii_washi import routing
    try:
        if not routing.ready():
            return None
        return routing.graph(timeout=0.0)
    except Exception as exc:                           # noqa: BLE001
        # Said, at most once a minute: this is on the repaint path, and a
        # router that has started failing looks like "no graph" otherwise.
        now = time.time()
        if now - _M.get("graph_err_at", 0.0) > 60.0:
            _M["graph_err_at"] = now
            print(f"[navi] routing graph unavailable: "
                  f"{type(exc).__name__}: {exc}", flush=True)
        return None


def current_phase():
    """Which phase of the day it is, from the last fix. Never raises."""
    pos = live.LIVE.get("pos")
    return daylight.phase(pos[0] if pos else None, pos[1] if pos else None)


def is_wide(page):
    """Whether the phone is on its side. Falls back to portrait when unknown.

    Read off the page rather than a platform API, because it is the only thing
    that is true for a window on a desktop as well as a rotated phone - and the
    fallback matters: page.width is None for the first frame or two, and guessing
    landscape there would lay the whole HUD out wrong and then jump.
    """
    width = getattr(page, "width", None) or 0
    height = getattr(page, "height", None) or 0
    return bool(width and height and width > height)


def _dash_ride():
    """The ride whose newest row the gauges read, or None.

    The recording when there is one. Otherwise, while the gauges are on
    screen, a live ride that derives exactly what a recording does and writes
    nothing - the Kotlin app's always-on telemetry (minion 1, phase 3). Outside
    a recording the gauges read ONLY the recording's row, so they sat blank
    and she concluded "the sensors don't seem to work" while every sensor was
    reporting. With the gauges off nothing is kept: a hidden gauge costs
    nothing, which is where her "hopefully making the front end lighter" pays.
    """
    recording = live.LIVE.get("ride")
    if recording is not None and recording.recording:
        _M.pop("live_ride", None)
        return recording
    if not prefs.dash():
        _M.pop("live_ride", None)
        return None
    return _M.setdefault("live_ride", telemetry.Ride())


def _feed_fix():
    """Hand one taken fix, and this second's sensor and microphone window, to
    the ride the gauges read. A recording has its fix already (live.set_position
    adds it); the live ride takes the same one here. Returns that ride."""
    ride = _dash_ride()
    if ride is None:
        return None
    if ride is not live.LIVE.get("ride"):
        here = live.LIVE.get("pos")
        if here is None:
            return ride
        ride.add_fix(lat=here[0], lon=here[1], t=live.LIVE.get("t"),
                     speed_ms=live.LIVE.get("speed_ms"),
                     heading=live.LIVE.get("heading"),
                     alt_m=live.LIVE.get("alt_m"), acc_m=live.LIVE.get("acc_m"))
    # The sensors and the microphone run far faster than this 1 Hz fix, and
    # they are merged INTO the sample the fix just created rather than logged
    # on their own clock. That is what keeps one row per fix while still
    # carrying the worst jolt of the second it covers - the peaks are reset
    # immediately afterwards so the next window starts clean.
    extra = dict(sensors.latest())
    extra.update(audio.latest())
    # The GPS climb rate only goes in when the barometer did not supply one,
    # so the column means one thing per ride rather than switching source
    # halfway down the file.
    if "climb_mps" not in extra:
        gps_climb = live.LIVE.get("climb_gps_mps")
        if gps_climb is not None:
            extra["climb_mps"] = round(gps_climb, 2)
    if extra:
        ride.feed_extra(extra)
    sensors.reset_peaks()
    audio.reset_peaks()
    return ride


def _hud_state():
    """Every number the overlay draws, gathered once.

    One flat dict so no two gauges can disagree, and so `gauges.overlay` can be
    handed to a test without a page. Everything may be None; every gauge
    handles that.
    """
    ride = _dash_ride()
    row = ride.samples[-1] if (ride is not None and ride.samples) else {}
    speed = live.speed_now()
    rpm = audio.STATE.get("rpm_est")
    machine = prefs.machine()
    gear, _confidence = gears.estimate(speed, rpm, machine)
    return {
        "imperial": prefs.imperial(),
        "machine": machine,
        "speed_ms": speed,
        "limit": (live.LIVE.get("limit") or (None, ""))[0],
        "lean_deg": row.get("lean_deg"),
        "roll_deg": sensors.STATE.get("roll_deg"),
        "pitch_deg": sensors.STATE.get("pitch_deg"),
        "g_lat": row.get("g_lat"),
        "g_long": row.get("g_long"),
        "alt_m": live.altitude_m(),
        "climb_mps": live.climb_mps(),
        "rpm": rpm,
        "gear": gear,
        "shift": gears.shift_hint(rpm, gear, machine),
        "bands": audio.STATE.get("bands"),
        "loudness": (audio.STATE.get("engine_db")
                     if audio.STATE.get("engine_db") is not None
                     else audio.STATE.get("loudness_db")),
        "loudness_peak": audio.STATE.get("engine_peak"),
    }


def _dash_row():
    """The sensor dash, built from whatever the sensors currently know.

    A module function rather than a closure so `paint()` can swap it into the
    holder on every fix without rebuilding the map. Every value may be None and
    every gauge handles that: an empty gauge is the honest picture of a phone
    with no barometer, and it must not look like a reading of zero.
    """
    ride = _dash_ride()
    row = ride.samples[-1] if (ride is not None and ride.samples) else {}
    state = sensors.STATE
    return gauges.dash(
        # Lean is the GPS-derived one - the bike's actual lean. `tilt` from the
        # accelerometer is the phone's roll and is passed separately so the
        # gauge can show the disagreement rather than confuse the two.
        lean_deg=row.get("lean_deg"),
        roll_deg=state.get("roll_deg"),
        pitch_deg=state.get("pitch_deg"),
        g_lat=row.get("g_lat"),
        g_long=row.get("g_long"),
        # Both of these pick the best source this phone has: the barometer where
        # there is one, the GPS fix where there is not. A Moto G Stylus has no
        # barometer and would otherwise show a dash for the whole ride.
        alt_m=live.altitude_m(),
        climb_mps=live.climb_mps(),
        # The ENGINE band's level, falling back to the microphone's total only
        # when the spectrum has not been computed yet. The distinction matters: at
        # 60 mph the total is mostly wind.
        loudness=(audio.STATE.get("engine_db")
                  if audio.STATE.get("engine_db") is not None
                  else audio.STATE.get("loudness_db")),
        loudness_peak=audio.STATE.get("engine_peak"),
        rpm=audio.STATE.get("rpm_est"),
        bands=audio.STATE.get("bands"),
        machine=prefs.machine(),
        imperial=prefs.imperial())


def build(page, go=None):
    p = PAL()
    if not MAPS_AVAILABLE:
        return ui.error_card(
            "This build has no map.",
            ImportError("flet-map and flet-geolocator are missing from the "
                        "bundle - see apps/navi/pyproject.toml"))

    choice = tiles.pick(prefs.tiles())
    _M["tile_note"] = choice["note"]
    start = live.LIVE.get("pos") or live.HOME

    # The accelerometer, gyroscope and barometer. Idempotent, and attached here
    # rather than in main() because this is the only surface that reads them -
    # though they keep running once attached, so a ride recorded while she is
    # looking at the Rides list still gets its sensor channels.
    #
    # Attached only when something will actually read them: the dash is drawn,
    # or a ride is recording and wants the channels in its log. Five sensors at
    # 400 ms is about a dozen crossings of the Python/Flutter channel every
    # second, and paying that to feed nothing is what made this app unusable
    # while Moto - which has no telemetry at all - stayed fast.
    #
    # `sensors_on_demand` ships True. Turn it off and they behave as they used
    # to: up from launch, never released.
    if prefs.dash() or live.recording() or not prefs.sensors_on_demand():
        sensors.attach(page)
    elif sensors.attached():
        # She turned the dash off while they were running. Let them go, or the
        # setting would only take effect on the next cold start.
        sensors.detach()
    # The microphone is built but NOT started: `audio.start()` is a deliberate
    # act, from the button below or from Settings.
    audio.attach(page)

    parts = _map_parts(start, choice, p)
    marker, dest_layer = parts["marker"], parts["dest"]
    track_line, route_line = parts["track"], parts["route"]
    the_map = parts["map"]

    # Bring the reused controls up to date. Field assignments on objects the
    # shell already holds - no new widgets, so no tile refetch.
    marker.coordinates = fmap.MapLatitudeLongitude(*start)
    # And where the map opens. A full render re-mounts the reused map, and a
    # re-mounted map starts at `initial_center` - the FIRST build's, which is
    # Oakland when the app opened before its first fix. Parked at the
    # destination no fix comes to move it back (the stream only reports 3 m
    # of movement), so it sat over Oakland (minion 1's Flet rides, 2 of 4).
    the_map.initial_center = fmap.MapLatitudeLongitude(*start)
    marker.content = ui.bike_marker(prefs.bike(), live.heading())
    track_line.coordinates = [fmap.MapLatitudeLongitude(*q)
                              for q in live.LIVE.get("track", [])]
    # The theme, applied in place. Switching to Red night at dusk is exactly
    # when she is riding, so it must not cost a tile refetch.
    the_map.bgcolor = p["bg"]
    track_line.color = p["good"]
    route_line.color = p["road"]
    route_line.border_color = p["bg"]

    # ---------------------------------------------------------- the readouts
    speed_value = ft.Text("—", size=ui.HUGE, weight=ft.FontWeight.W_900,
                          color=p["text"])
    speed_unit = ft.Text(guidance.speed_unit(prefs.imperial()), size=ui.MID,
                         weight=ft.FontWeight.W_700, color=p["text2"])
    limit_value = ft.Text("—", size=ui.BIG, weight=ft.FontWeight.W_900,
                          color=p["text"])
    limit_note = ft.Text("limit", size=ui.SMALL, color=p["text2"])
    turn_glyph = ft.Icon(ft.Icons.NAVIGATION, size=44, color=p["accent"])
    turn_text = ft.Text("", size=ui.BIG, weight=ft.FontWeight.W_800,
                        color=p["text"], max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS)
    turn_dist = ft.Text("", size=ui.MID, weight=ft.FontWeight.W_700,
                        color=p["text2"])
    eta_text = ft.Text("—", size=ui.MID, weight=ft.FontWeight.W_700,
                       color=p["text"])
    left_text = ft.Text("—", size=ui.MID, weight=ft.FontWeight.W_700,
                        color=p["text"])
    lean_text = ft.Text("—", size=ui.MID, weight=ft.FontWeight.W_700,
                        color=p["text"])
    lean_bar = ui.bar(0.0, p["accent"])
    ride_text = ft.Text("", size=ui.SMALL, color=p["text2"])
    fix_text = ft.Text("waiting for GPS…", size=ui.SMALL, color=p["text2"])
    prompt_text = ft.Text("", size=ui.BIG, weight=ft.FontWeight.W_800,
                          color=p["bg"])
    # Both bands start clear of the floating button column.
    #
    # Hers: the menu button "seems adamently placed above and over the turn by
    # turn area". It was: the buttons float in the shell's Stack at left=12,
    # and these bands run the full width at the top of this page, so the
    # instruction she needs to read at 60 mph had a button sitting on it.
    # 76 = the column's left edge (12) plus its width (52) plus a 12 gap.
    BAND_INSET = 76
    prompt_band = ft.Container(
        visible=False, bgcolor=p["accent"], content=prompt_text,
        padding=ft.Padding(left=BAND_INSET, top=10, right=14, bottom=10))
    turn_band = ft.Container(
        visible=False, bgcolor=p["surface"],
        padding=ft.Padding(left=BAND_INSET, top=10, right=12, bottom=10),
        border=ft.Border(bottom=ft.BorderSide(1, p["border"])),
        content=ft.Row(spacing=12, controls=[
            turn_glyph,
            ft.Column(spacing=0, expand=True, tight=True,
                      controls=[turn_text, turn_dist]),
            ft.Column(spacing=0, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.END,
                      controls=[eta_text, ft.Text("arrive", size=ui.SMALL,
                                                  color=p["text2"])])]))

    # ------------------------------------------------------------- behaviour
    def paint():
        """Put the current live state into the controls that already exist."""
        pal = PAL()
        speed = live.speed_now()
        # The speed limit is kept by live.set_position on every taken fix,
        # rate-limited there (a spatial search of the road graph), so this page,
        # the speeding cue and every other surface read one current answer. It
        # used to be worked out here, so off this page it went stale.
        limit, road = live.LIVE.get("limit") or (None, "")
        speed_value.value = guidance.speed_text(speed, prefs.imperial())
        speed_value.color = (pal["bad"] if guidance.over_limit(speed, limit)
                             else pal["text"])
        speed_unit.value = guidance.speed_unit(prefs.imperial())
        limit_value.value = "—" if not limit else str(limit)
        limit_note.value = road.replace("_", " ") if road else "no graph"

        follower = live.LIVE.get("follower")
        status = follower.status() if follower is not None else None
        if status and status.get("ok"):
            turn_band.visible = True
            # No maneuver, no glyph: an arrow is an instruction.
            turn_glyph.visible = bool(status["icon"])
            if status["icon"]:
                turn_glyph.icon = ui.glyph(status["icon"])
            turn_glyph.color = (pal["bad"] if status["off_route"]
                                else pal["accent"])
            if status["off_route"]:
                turn_text.value = "off route"
                turn_dist.value = (
                    guidance.distance_text(status["off_route_m"],
                                           prefs.imperial())
                    + " from the line - re-routing")
            elif status["arrived"]:
                turn_text.value = "arrived"
                turn_dist.value = (live.LIVE.get("dest") or ("", "", ""))[2]
            else:
                # Not "continue": with no instruction the banner does not know
                # that, and on a nav banner the word is an instruction.
                turn_text.value = status["instruction"] or "—"
                turn_dist.value = " · ".join(x for x in (
                    guidance.distance_text(status["turn_in_m"],
                                           prefs.imperial()),
                    status["street"]) if x and x != "—")
            eta_text.value = status["eta"]
            left_text.value = guidance.distance_text(status["left_m"],
                                                     prefs.imperial())
        else:
            turn_band.visible = False
            eta_text.value = "—"
            left_text.value = "—"

        ride = _dash_ride()
        lean = ride.samples[-1].get("lean_deg") if (
            ride is not None and ride.samples) else None
        lean_text.value = "—" if lean is None else f"{abs(lean):.0f}°"
        filled = max(1, int(min(1.0, abs(lean or 0.0) / LEAN_FULL_DEG) * 1000))
        lean_bar.content.controls[0].expand = filled
        lean_bar.content.controls[1].expand = max(1, 1000 - filled)
        ride_text.value = live.summary_line() or _M.get("tile_note", "")

        acc, pos = live.LIVE.get("acc_m"), live.LIVE.get("pos")
        fix_text.value = ("waiting for GPS…" if pos is None else
                          f"{pos[0]:.5f}, {pos[1]:.5f}"
                          + (f"  ±{acc:.0f} m" if acc else ""))

        held = time.time() - (live.LIVE.get("prompt_at") or 0.0)
        prompt_band.visible = (bool(live.LIVE.get("prompt"))
                               and held < PROMPT_HOLD_S and prefs.voice())
        prompt_text.value = live.LIVE.get("prompt", "")
        # Announce turns is on and nothing can say them: tell her, here, how to
        # fix it. Silence that looks like it is working is this app's oldest bug.
        problem = speech.STATE.get("problem") if prefs.voice() else ""
        voice_band.visible = bool(problem)
        voice_text.value = problem or ""
        # Past the offline map's edge the map is black with her bike on it;
        # say why instead (minion 1's Flet rides, as Kotlin does).
        map_band.visible = bool(pos is not None and tiles.outside(pos[0], pos[1]))

        # The gauges are rebuilt rather than mutated: each one is a stack of
        # transformed Containers whose angles ARE their construction, so there is
        # no property to poke.
        #
        # But rebuilding forty controls on every repaint, with the sensors firing
        # several times a second, is most of why the app crawled on a real phone.
        # Twice a second is faster than an eye reads a gauge and a fifth of the
        # work.
        if time.time() - _M.get("dash_at", 0.0) > DASH_EVERY_S:
            holder = _M.get("dash_holder")
            if holder is not None:
                holder.content = _dash_row()
                _M["dash_at"] = time.time()
            over = _M.get("overlay_holder")
            if over is not None:
                over.content = gauges.overlay(
                    _hud_state(), _M.get("layout", "ribbon"),
                    _M.get("wide", False), _M.get("anchor", "bottom"))
                _M["dash_at"] = time.time()

    _M["paint"] = paint

    def draw_route():
        route = live.LIVE.get("route")
        route_line.coordinates = [
            fmap.MapLatitudeLongitude(q[0], q[1])
            for q in (route.points if route is not None else [])]
        dest = live.LIVE.get("dest")
        pal = PAL()
        dest_layer.markers = [] if dest is None else [fmap.Marker(
            coordinates=fmap.MapLatitudeLongitude(dest[0], dest[1]),
            width=26, height=26,
            content=ft.Container(width=22, height=22, border_radius=11,
                                 bgcolor=pal["warn"], border=BRD(pal["bg"], 2)))]
        pin = _M.get("pin")
        if pin is not None:
            # The point she long-pressed, offered but not yet chosen: accent,
            # bigger, so it is not mistaken for the destination she has.
            dest_layer.markers = dest_layer.markers + [fmap.Marker(
                coordinates=fmap.MapLatitudeLongitude(pin[0], pin[1]),
                width=32, height=32,
                content=ft.Container(width=28, height=28, border_radius=14,
                                     bgcolor=pal["accent"], border=BRD(pal["bg"], 3)))]

    draw_route()

    # ---------------------------------------------------------- drop a pin
    # Hers: "i can't even just 'place pin on map' and use that as destination
    # easily". A LONG press, so a pan or a stray tap never sets a destination;
    # and nothing routes until she says so - the band offers the point and
    # waits, because the app never quietly substitutes something for what she
    # asked. flet_map hands the pressed point over as MapTapEvent.coordinates
    # (flet_map/types.py:775).
    pin_title = ft.Text("", size=ui.MID, weight=ft.FontWeight.W_800, color=p["text"])
    pin_detail = ft.Text("", size=ui.SMALL, color=p["text2"])

    def _pin_words(title):
        pin = _M.get("pin")
        if pin is None:
            return
        here = live.LIVE.get("pos")
        away = (guidance.distance_text(haversine_m(here, pin), prefs.imperial())
                if here else "")
        pin_title.value = title
        pin_detail.value = " · ".join(x for x in (
            f"{pin[0]:.5f}, {pin[1]:.5f}", f"{away} away" if away else "") if x)

    def _on_long_press(e):
        point = getattr(e, "coordinates", None)
        if point is None:
            return
        _M["pin"] = (float(point.latitude), float(point.longitude))
        _pin_words("Route to this point?")
        pin_band.visible = True
        draw_route()
        _update(page)

    def _pin_cancel(_e=None):
        _M["pin"] = None
        pin_band.visible = False
        draw_route()
        _update(page)

    def _pin_route(_e=None):
        pin = _M.pop("pin", None)
        if pin is None:
            return
        live.set_destination(pin[0], pin[1], "Dropped pin")
        pin_band.visible = False
        draw_route()
        _update(page)
        page.run_task(_route_to_pin, pin)

    async def _route_to_pin(pin):
        # Off the UI thread, like every route: the graph read would freeze the map.
        route = await live.route_async(allow_server=False)
        if route is None or not route.ok:
            # Say why, and leave the pin up so she can try again or cancel.
            _M["pin"] = pin
            _pin_words("No route to that point")
            pin_detail.value = (live.LIVE.get("note")
                                or getattr(route, "reason", "") or "no route")
            pin_band.visible = True
        else:
            # A route is up without the page rebuilding: switch the screen-off
            # service on now, and ask for the notification that shows it.
            location.sync()
            page.run_task(location.ask_notifications, page)
        draw_route()
        paint()
        _update(page)

    pin_band = ft.Container(
        visible=_M.get("pin") is not None, bgcolor=p["surface"],
        padding=ft.Padding(left=BAND_INSET, top=10, right=12, bottom=10),
        border=ft.Border(bottom=ft.BorderSide(1, p["border"])),
        content=ft.Column(spacing=8, tight=True, controls=[
            pin_title, pin_detail,
            ft.Row(spacing=10, controls=[
                ui.button("Route here", _pin_route, icon=ft.Icons.DIRECTIONS,
                          primary=True, expand=True),
                ui.button("Cancel", _pin_cancel, expand=True)])]))
    _M["pin_band"] = pin_band
    voice_text = ft.Text("", size=ui.SMALL, weight=ft.FontWeight.W_700, color=p["bg"])
    voice_band = ft.Container(
        visible=False, bgcolor=p["warn"], content=voice_text,
        padding=ft.Padding(left=BAND_INSET, top=8, right=12, bottom=8))
    map_band = ft.Container(
        visible=False, bgcolor=p["warn"],
        content=ft.Text(tiles.OUTSIDE_WORDS, size=ui.SMALL,
                        weight=ft.FontWeight.W_700, color=p["bg"]),
        padding=ft.Padding(left=BAND_INSET, top=8, right=12, bottom=8))
    _pin_words("Route to this point?")
    # Re-bound on every build: the Map outlives this function (see _map_parts),
    # and a handler bound once would call the closures of the first build.
    the_map.on_long_press = _on_long_press

    def on_position(e):
        pos = getattr(e, "position", None) or e
        lat = getattr(pos, "latitude", None)
        lon = getattr(pos, "longitude", None)
        if lat is None or lon is None:
            return
        out = live.set_position(
            lat, lon, speed_ms=getattr(pos, "speed", None),
            heading=getattr(pos, "heading", None),
            alt_m=getattr(pos, "altitude", None),
            acc_m=getattr(pos, "accuracy", None))
        if not out["moved"]:
            return
        # A new prompt fires once per band; say it as it fires. Handed to the
        # speech worker, so this fix handler never waits on the engine.
        _announce(out, (live.LIVE.get("limit") or (None, ""))[0])
        if out.get("reroute") and _M.get("held"):
            page.run_task(_let_go, _M["held"]["id"])
        # This second's sensors into the recording, or into the live ride the
        # gauges read when nothing records (see _dash_ride).
        _feed_fix()

        marker.coordinates = fmap.MapLatitudeLongitude(lat, lon)

        # Only rebuild the marker when it would actually look different.
        #
        # It was being rebuilt on every fix - a whole new control, serialised
        # and sent to Flutter once a second for a picture that had not changed.
        # A heading has to move a few degrees before the eye can tell, so that
        # is the threshold.
        heading = live.heading()
        last_heading = _M.get("marker_heading")
        if (last_heading is None or heading is None
                or abs(heading - last_heading) > MARKER_DEGREES
                or _M.get("marker_colour") != prefs.bike()):
            marker.content = ui.bike_marker(prefs.bike(), heading)
            _M["marker_heading"] = heading
            _M["marker_colour"] = prefs.bike()

        # Draw the tail of the track, not all of it.
        #
        # The polyline is re-sent in full whenever it changes, so a four-hour
        # ride was shipping thousands of coordinates to Flutter every second and
        # redrawing them. The recent part is the only part that tells her
        # anything while riding; the whole thing is in the log and on the
        # server, which is where it matters.
        track = live.LIVE.get("track", [])
        if len(track) != _M.get("track_len"):
            track_line.coordinates = [fmap.MapLatitudeLongitude(q[0], q[1])
                                      for q in track[-TRACK_POINTS:]]
            _M["track_len"] = len(track)
        if prefs.autorecord() and out["rolling"] and not live.recording():
            live.start_recording()
            location.sync()
            page.run_task(location.ask_notifications, page)
        if out["reroute"]:
            page.run_task(_reroute)
        # Checked on every fix and acts only when the phase changes, which is
        # four times a day. The first fix is also the first moment the sun's
        # position is knowable at all.
        if prefs.auto_theme():
            page.run_task(_apply_daylight)
        if time.time() - _M.get("battery_at", 0.0) > BATTERY_EVERY_S:
            page.run_task(_read_battery)
        # Do not draw the map's readouts while she is looking at something
        # else. The geolocator is a module-level service that outlives this
        # page (see _M above), and its handler is only re-pointed when
        # `build()` runs - which opening Settings does not do. So this closure
        # kept painting controls that were no longer mounted, then calling a
        # page update, once per fix, for as long as she stayed off the map.
        #
        # Everything above this line still runs: the fix is recorded, the ride
        # log gets its row, the route is still re-checked. Only the drawing
        # stops, because there is nothing on screen to draw it on.
        if not _showing_ride(page):
            return
        # Keep the map under the bike.
        #
        # The marker moved and the map did not, which is why it "followed a
        # little" during turn-by-turn and otherwise sat on Oakland: the only
        # thing that ever set the centre was `initial_center` at build time,
        # and the map is now reused across rebuilds so that stopped firing too.
        # Re-centred on the same clock as the readouts, not on every fix, so
        # panning by hand is not fought for a full second.
        if prefs.gps() and time.time() - _M.get("centre_at", 0.0) > DASH_EVERY_S:
            _M["centre_at"] = time.time()
            centre = _M.get("centre")
            if callable(centre):
                page.run_task(centre, lat, lon)
        paint()
        _update(page)

    def on_geo_error(e):
        fix_text.value = f"GPS error: {getattr(e, 'data', e)}"
        _update(page)

    geo = _M.get("geo")
    if not prefs.gps():
        # Dropping the reference is what unsubscribes: a Flet service lives
        # exactly as long as something holds it. With it gone there is no
        # stream, no fix, no repaint and no radio - which is the whole point of
        # a phone sitting in a garage with the app open.
        if geo is not None and isinstance(getattr(page, "services", None), list):
            try:
                page.services.remove(geo)
            except ValueError:
                pass
        _M["geo"] = geo = None
        location.forget()
    elif geo is None:
        # HIGH, not BEST, and an explicit Android configuration.
        #
        # BEST maps to the fused provider's PRIORITY_HIGH_ACCURACY, which is
        # what makes Android insist on Wi-Fi scanning - "the phone keeps dying
        # and demanding to use wifi in order to gps itself". HIGH is the same
        # satellite fix without the framework nagging for network assistance,
        # which is the right trade for an app whose whole point is working with
        # the radio off.
        #
        # `interval_duration` follows her sample rate instead of defaulting to
        # five seconds, and `distance_filter` of 3 m stops the stream firing
        # while the bike is parked - a stationary GPS wanders, and every wander
        # was a message, a log line and a repaint.
        #
        # And the location FOREGROUND SERVICE, so fixes keep coming with the
        # screen off - without it the app went silent and the ride log became
        # a straight line the moment her screen locked. See location.py for
        # the Flet 0.86.5 bug this works around, and the emulator A/B.
        mode = location.current_mode()
        geo = fgeo.Geolocator(
            configuration=location.android_config(mode[1], foreground=mode[0]),
            on_position_change=on_position, on_error=on_geo_error)
        location.register(geo, mode)
        _M["geo"] = geo
        if isinstance(getattr(page, "services", None), list):
            page.services.append(geo)
    else:
        # Re-point the existing service at this render's handlers: the closures
        # above capture controls the old ones no longer hold, and a stream that
        # kept updating the previous render's Text controls would look like a
        # GPS that had stopped.
        geo.on_position_change = on_position
        geo.on_error = on_geo_error
        location.adopt(geo)
        location.sync()
    # A route is up: from here the location service matters, so this is where
    # the notification permission is asked - with its reason, not at launch.
    # A ROUTE, not a remembered destination (main.py restores that at launch).
    if geo is not None and live.LIVE.get("route") is not None:
        page.run_task(location.ask_notifications, page)

    async def _locate(_e=None):
        if geo is None:
            return
        try:
            await geo.request_permission()
            pos = await geo.get_current_position()
        except Exception as exc:                        # noqa: BLE001
            fix_text.value = f"no fix: {exc}"
            _update(page)
            return
        if pos is not None:
            on_position(pos)

    async def _reroute(_e=None):
        route = await live.route_async(allow_server=False)
        follower = live.LIVE.get("follower")
        if route is not None and route.ok and follower is not None:
            follower.note_reroute()
        _release(route)
        # The new route's first prompt now, at the answer, not on the next
        # fix: a second of the 7-11 s minion 1 measured from leaving the line
        # (Kotlin, 5-7 s, runs its Follower at the answer).
        if route is not None and route.ok:
            out = live.guide_now()
            if out.get("say"):
                _announce(out, (live.LIVE.get("limit") or (None, ""))[0])
        draw_route()
        paint()
        _update(page)

    async def _upload(summary):
        """Push the finished ride to the dashboard, where Moto's Rides sees it.

        Her "EASY LINK/PUSH TO AGEDASHI MOTO". Queued through Washi's outbox,
        so a ride finished in a dead zone goes up when signal comes back rather
        than being lost at the moment it is complete.

        Through `uploads.send`, the one door. This sent `ride.to_gpx()`, which
        is the ride IN MEMORY - the last `telemetry.WINDOW` samples, ten
        minutes at 1 Hz - so every longer ride reached the server without its
        beginning, and nothing said so. `send` reads the whole log off the
        disk, and writes down that it went so the Rides page can say so.
        """
        ride = live.LIVE.get("ride")
        if ride is None or not ride.samples:
            return
        import asyncio
        ok, message = await asyncio.to_thread(
            uploads.send, ride.path, ride,
            f"{ride.count} samples, "
            f"{guidance.distance_text(summary.get('distance_m', 0.0), prefs.imperial())}")
        ride_text.value = message if ok else f"not sent: {message}"
        _update(page)

    async def _read_battery(_e=None):
        """What the ride is costing the phone.

        Polled rather than streamed: the level moves by one percent every few
        minutes, so a reading at each end of a ride and one every `BATTERY_EVERY_S`
        in between is the whole story, and it costs a platform call instead of a
        subscription. Screen-on navigation in a mount runs a phone down 20-30% an
        hour, and in direct sun it throttles as well.
        """
        await sensors.battery_level()
        _M["battery_at"] = time.time()

    def _farewell():
        """"Hasta la vista, baby" - and only here.

        Her rule, 2026-10-12: "HASTA LA VISTA BABY should only play when i
        quit or when i stop gps/recording". It used to be an ARRIVAL line,
        where it said goodbye to someone who had just turned up. The clips
        now live in their own mood, which no moment on the road can reach, so
        this is the only place in the app that plays them.
        """
        items = character.farewell()
        if items:
            speech.play(items)

    def _toggle_record(_e=None):
        if live.recording():
            summary = live.stop_recording()
            if summary:
                page.run_task(_upload, summary)
            _farewell()
        else:
            live.start_recording()
            page.run_task(location.ask_notifications, page)
        page.run_task(_read_battery)
        _rerender(page)

    def _toggle_gps(_e=None):
        going_off = prefs.gps()
        prefs.set_value("gps", not prefs.gps())
        if going_off:
            _farewell()
        page.run_task(_save_prefs)
        _rerender(page)

    def _cycle_style(_e=None):
        # Only through the styles that can be drawn. With the offline map
        # installed that is one, the row below is not tappable, and this is
        # never reached - see tiles.choices.
        prefs.set_value("tiles", tiles.cycle(
            prefs.tiles(), only=tiles.usable(prefs.tiles())))
        page.run_task(_save_prefs)
        _rerender(page)

    def _toggle_dash(_e=None):
        # The same switch as Settings > Gauges, through the same door, and
        # SAVED. It was `set_value("dash", not prefs.dash())`: a dead tap
        # whenever the layout was "off", because dash() ANDs the two, and
        # forgotten at the next launch either way.
        prefs.toggle_dash()
        page.run_task(_save_prefs)
        _rerender(page)

    def _next_bike(_e=None):
        """One tap cycles the bike. It is shown beside it, so it cannot be wrong.

        Cycling rather than a dialog: there are three profiles and this is a thing
        done with gloves on, at a stop, twice a year.
        """
        keys = [key for key, _name in bikes.names()]
        current = prefs.machine()
        nxt = keys[(keys.index(current) + 1) % len(keys)] if current in keys \
            else keys[0]
        prefs.set_value("machine", nxt)
        # The RPM continuity reference belonged to the old engine.
        audio.set_machine(nxt)
        page.run_task(_save_prefs)
        _rerender(page)

    async def _save_prefs():
        await prefs.save(prefs.store())

    async def _apply_daylight(_e=None):
        """Put the theme and the screen brightness where the sun says they go.

        The theme is written through `theme_auto`, which does NOT store it: a red
        screen tonight must not become the remembered choice tomorrow, and
        switching the automation off has to put back whatever she last picked by
        hand. Only repaints when the phase has actually changed, so this costs one
        comparison on the fixes where nothing is different - which is all of them
        but four a day.
        """
        want = current_phase()
        if _M.get("phase") == want:
            return
        _M["phase"] = want
        await sensors.set_brightness(daylight.brightness_for(want))
        if prefs.set_value("theme_auto", daylight.theme_for(want)):
            theme.apply(page)
            _rerender(page)

    async def _toggle_mic():
        if audio.listening():
            await audio.stop()
            prefs.set_value("mic", False)
        else:
            ok, message = await audio.start()
            prefs.set_value("mic", bool(ok))
            if not ok:
                ride_text.value = f"microphone: {message}"
        _rerender(page)

    async def _centre_now(lat, lon):
        """Actually move the map. `center_on` is ASYNC.

        This was called synchronously - `the_map.center_on(...)` - which builds
        a coroutine, never awaits it, and returns. The map never moved. The
        bare `except: pass` around it then swallowed the "coroutine was never
        awaited" warning, so it failed in perfect silence.

        That is both "centre on the bike does nothing" and "follow the bike
        snaps back to Oakland": nothing ever told the map to go anywhere, so it
        sat on whatever `initial_center` it was built with - and once the map
        control started being reused across rebuilds, that centre stopped being
        refreshed too.
        """
        try:
            await the_map.center_on(fmap.MapLatitudeLongitude(lat, lon), 16)
        except Exception as exc:                        # noqa: BLE001
            print(f"[navi] center_on failed: {type(exc).__name__}: {exc}",
                  flush=True)

    def _recentre(_e=None):
        pos = live.LIVE.get("pos")
        if pos is None:
            page.run_task(_locate)
            return
        page.run_task(_centre_now, pos[0], pos[1])

    # So the GPS handler can keep the map under the bike without rebuilding it.
    _M["centre"] = _centre_now

    # Ask for a fix only when following is on and there is nothing yet.
    #
    # The map no longer opens on Oakland and then jumps: `start` below is the
    # last known position when there is one, and when there is not, the map sits
    # where it is and the marker simply is not drawn until a real fix exists.
    # Her "it centers on oakland then kicks over once it finds gps".
    if prefs.gps() and live.LIVE.get("pos") is None:
        page.run_task(_locate)

    # Pick the microphone back up if she left it on. Not on a bare default -
    # `prefs.mic()` ships False - so this only ever resumes a choice she made.
    # Bring the voice up now, off the page's thread, so the first prompt of a
    # ride does not pay for binding the engine - and so a missing voice shows
    # up on this page before it is needed, not at the first turn.
    if prefs.voice():
        speech.start()

    if prefs.mic() and not audio.listening():
        page.run_task(audio.start)

    if prefs.auto_theme():
        page.run_task(_apply_daylight)

    paint()

    # And keep painting. Without this the gauges only ever moved when a GPS
    # fix arrived, so a parked bike showed frozen numbers while every sensor
    # behind them was reading correctly. See `_dash_clock`.
    _M["clock"] = _M.get("clock", 0) + 1
    page.run_task(_dash_clock, page, _M["clock"])

    recording = live.recording()

    # THE WHOLE SURFACE IS THE MAP.
    #
    # There used to be a card under it carrying speed, the limit, a lean bar,
    # distance to go, the fix, the bike switch and five buttons. It was about a
    # third of the screen - "its like literally 1/3 of the scree nis just
    # information and not overlaying the map in any way its giant" - and it was
    # left in by mistake when the overlays went in, so the app was showing both.
    #
    # It is gone. The numbers live in the overlay, and every control that used to
    # sit in that card now lives behind the round button, which is her own
    # instruction: "MOST THINGS CAN BE A BUTTON THAT OPENS A MENU UNDEER THE
    # CURRENT 'HAMBURGER MENU IN A CIRCLE'".
    layout = prefs.hud()
    _M["layout"] = layout
    _M["dash_holder"] = None
    _M["overlay_holder"] = None

    def _round(icon, on_click, tooltip="", lit=False, primary=False):
        """A round button that floats on the map. The only chrome on this page."""
        pal = PAL()
        return ft.Container(
            width=52, height=52, border_radius=RAD(26), ink=True,
            on_click=on_click, tooltip=tooltip or None,
            alignment=ft.Alignment.CENTER,
            bgcolor=(pal["accent"] if primary
                     else ft.Colors.with_opacity(0.78, pal["bg"])),
            border=None if primary else BRD(pal["border"], 1),
            content=ft.Icon(icon, size=24,
                            color=(pal["bg"] if primary
                                   else (pal["accent"] if lit
                                         else pal["text"]))))

    #: Where the gauges sit, in words. Only this orientation's pair is offered:
    #: she is holding the phone the way she rides with it.
    _WHERE = {"left": "down the left", "right": "down the right",
              "top": "along the top", "bottom": "along the bottom"}

    def _toggle_flag(name):
        """A plain on/off preference from the sheet: flip, save, repaint."""
        def handler(_e=None):
            prefs.set_value(name, not getattr(prefs, name)())
            page.run_task(_save_prefs)
            _rerender(page)
        return handler

    def _pick_theme(key):
        """A theme by hand. Turns Follow the sun off, as Settings does -
        otherwise the next phase change would quietly undo her choice."""
        def handler(_e=None):
            prefs.set_value("theme", key)
            if prefs.auto_theme():
                prefs.set_value("auto_theme", False)
            theme.apply(page)
            page.run_task(_save_prefs)
            _rerender(page)
        return handler

    def _toggle_sun(_e=None):
        """Follow the sun on or off. Off puts back the theme she last picked
        by hand; on works the phase out again now, not at the next one."""
        on = not prefs.auto_theme()
        prefs.set_value("auto_theme", on)
        if on:
            _M["phase"] = None
            page.run_task(_apply_daylight)
        else:
            prefs.set_value("theme", prefs.theme_key())
        theme.apply(page)
        page.run_task(_save_prefs)
        _rerender(page)

    def _pick_layout(key):
        """A gauge layout. Picking one while they are hidden shows them: a
        layout chosen is gauges wanted."""
        def handler(_e=None):
            prefs.set_value("hud", key)
            if not prefs.dash():
                prefs.toggle_dash()
            page.run_task(_save_prefs)
            _rerender(page)
        return handler

    def _pick_anchor(name, value):
        def handler(_e=None):
            prefs.set_value(name, value)
            page.run_task(_save_prefs)
            _rerender(page)
        return handler

    def _open_actions(_e=None):
        """What she changes on the bike, or just before a ride, in one sheet.

        A dialog rather than a bar: all of it is done at a stop, and none of it
        is worth permanent screen on a moving motorcycle. Settings keeps what
        is set once; this keeps what she changes on the way - the theme at
        dusk, the gauges, the voice, the mic - because "a control belongs
        where she is when she wants it".

        Sideways it is a grid, three across: a list showed three and a half
        rows before it scrolled, which is not a sheet to use at a light. Every
        target is 56 dp, Close and Back included - Material's text buttons are
        40, which a glove misses.

        A plain row acts and closes, so she sees the change on the map at
        once. A row that opens a choice (the look, the gauge layout) swaps the
        sheet's contents in place, with Back to return.
        """
        pal = PAL()
        wide = is_wide(page)
        cols = 3 if wide else 1
        row_w = 200 if wide else 320
        width = cols * row_w + (cols - 1) * 8

        def item(icon, label, detail, on_click, lit=False, close=True):
            def handler(e):
                if close:
                    _dismiss()
                on_click(e)
            row = ui.row_tap(ft.Row(spacing=12, controls=[
                ft.Icon(icon, size=20,
                        color=pal["accent"] if lit else pal["text2"]),
                ft.Column(spacing=0, expand=True, tight=True, controls=[
                    ft.Text(label, size=ui.MID, weight=ft.FontWeight.W_700,
                            color=pal["text"], max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(detail, size=ui.SMALL, color=pal["text2"],
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)])]),
                handler, height=ui.TOUCH)
            row.width = row_w
            return row

        def info(icon, label, detail):
            """A row that says something and is not a button.

            For a choice that cannot be made right now: offering the tap and
            then doing nothing is exactly the lie she caught - "tiles streets
            clicking it does nothing".
            """
            return ft.Container(width=row_w, height=ui.TOUCH, content=ft.Row(
                spacing=12, controls=[
                    ft.Icon(icon, size=20, color=pal["text3"]),
                    ft.Column(spacing=0, expand=True, tight=True, controls=[
                        ft.Text(label, size=ui.MID, weight=ft.FontWeight.W_700,
                                color=pal["text"], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(detail, size=ui.SMALL, color=pal["text2"],
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS)])]))

        def pick(label, chosen, on_click, w):
            """One choice among several: a tile, lit when it is the one."""
            def handler(e):
                _dismiss()
                on_click(e)
            return ft.Container(
                width=w, height=ui.TOUCH, on_click=handler, ink=True,
                border_radius=RAD(12), alignment=ft.Alignment.CENTER,
                bgcolor=pal["accent"] if chosen else pal["surface"],
                border=None if chosen else BRD(pal["border"], 1),
                content=ft.Text(label, size=ui.SMALL,
                                weight=ft.FontWeight.W_700,
                                color=pal["bg"] if chosen else pal["text"],
                                text_align=ft.TextAlign.CENTER))

        def grid(controls):
            # Fixed widths, never expand: a child that expands inside a
            # wrapping Row paints grey in a release build.
            return ft.Row(wrap=True, spacing=8, run_spacing=8, width=width,
                          controls=controls)

        def _dismiss(_e=None):
            try:
                page.pop_dialog()
            except Exception as exc:                 # noqa: BLE001
                print(f"[navi] pop_dialog failed: {type(exc).__name__}: "
                      f"{exc}", flush=True)

        def main_rows():
            choice, style_rows = tiles.choices(prefs.tiles())
            can_draw = [row for row in style_rows if row["usable"]]
            drawn_name = (tiles.label(choice["style"]) if choice["style"]
                          else "Unknown")
            if len(can_draw) > 1:
                nxt = tiles.label(tiles.cycle(prefs.tiles(),
                                              only=[r["key"] for r in can_draw]))
                tile_row = item(ft.Icons.LAYERS, f"Map: {drawn_name}",
                                f"{choice['note']} · tap for {nxt}",
                                _cycle_style)
            else:
                others = ", ".join(r["label"] for r in style_rows
                                   if not r["usable"])
                tile_row = info(ft.Icons.LAYERS,
                                f"Map: {drawn_name} - offline",
                                "the one style with no signal. "
                                + (f"{others} are online only - see Settings."
                                   if others else ""))
            # The same switch as Settings > Gauges, and it says so. It used to
            # read "Gauges" over the layout's description, so a tap that hid
            # them looked like a tap on the layout.
            layout_name = prefs.layout_name()
            where = _WHERE.get(prefs.hud_side() if wide else prefs.hud_edge(),
                               "")
            look = theme.THEME_NAMES.get(
                theme.S.get("theme") if prefs.auto_theme()
                else prefs.theme_key(), "")
            return [
                item(ft.Icons.SPEED,
                     "Gauges: on" if prefs.dash() else "Gauges: off",
                     (f"{layout_name} - tap to hide. Same switch as Settings > "
                      "Gauges" if prefs.dash() else
                      f"tap to show the {layout_name}. Same switch as "
                      "Settings > Gauges"),
                     _toggle_dash, lit=prefs.dash()),
                item(ft.Icons.DASHBOARD_CUSTOMIZE, f"Layout: {layout_name}",
                     where, lambda e: show("gauges"), close=False),
                item(ft.Icons.PALETTE, f"Look: {look}",
                     "following the sun" if prefs.auto_theme()
                     else "picked by hand",
                     lambda e: show("look"), close=False,
                     lit=prefs.auto_theme()),
                item(ft.Icons.RECORD_VOICE_OVER if prefs.voice()
                     else ft.Icons.VOICE_OVER_OFF,
                     "Turn voice: on" if prefs.voice() else "Turn voice: off",
                     "turns spoken aloud" if prefs.voice()
                     else "turns shown, not spoken",
                     _toggle_flag("voice"), lit=prefs.voice()),
                item(ft.Icons.THEATER_COMEDY,
                     "Character lines: on" if prefs.flourishes()
                     else "Character lines: off",
                     "his extra lines" if prefs.flourishes()
                     else "the voice, no theatre",
                     _toggle_flag("flourishes"), lit=prefs.flourishes()),
                item(ft.Icons.MIC if audio.listening() else ft.Icons.MIC_OFF,
                     "Engine sound",
                     "on - loudness and RPM" if audio.listening() else "off",
                     lambda e: page.run_task(_toggle_mic),
                     lit=audio.listening()),
                item(ft.Icons.GPS_FIXED if prefs.gps() else ft.Icons.GPS_OFF,
                     "Follow the bike",
                     "on - the map follows you" if prefs.gps()
                     else "off - no fixes, no repaints",
                     _toggle_gps, lit=prefs.gps()),
                item(ft.Icons.MY_LOCATION, "Centre on the bike",
                     fix_text.value or "", _recentre),
                item(ft.Icons.TWO_WHEELER, prefs.machine_name(),
                     f"{bikes.profile(prefs.machine())['cylinders']} cyl · "
                     f"RPM = Hz x {60 / bikes.firing_ratio(prefs.machine()):.0f}",
                     _next_bike),
                tile_row,
            ]

        def look_rows():
            per = 4 if wide else 2
            w = (width - (per - 1) * 8) // per
            current = theme.S.get("theme") if prefs.auto_theme() \
                else prefs.theme_key()
            sun = item(ft.Icons.WB_TWILIGHT,
                       "Follow the sun: on" if prefs.auto_theme()
                       else "Follow the sun: off",
                       "picks the theme and dims to match" if prefs.auto_theme()
                       else "tap to let the time of day pick",
                       _toggle_sun, lit=prefs.auto_theme())
            sun.width = width
            return [sun] + [
                pick(theme.THEME_NAMES[key],
                     key == current and not prefs.auto_theme(),
                     _pick_theme(key), w)
                for key in theme.THEMES]

        def gauge_rows():
            per = 3 if wide else 2
            w = (width - (per - 1) * 8) // per
            name, now = (("hud_side", prefs.hud_side()) if wide
                         else ("hud_edge", prefs.hud_edge()))
            sides = [(key, _WHERE[key].capitalize())
                     for key in (("left", "right") if wide
                                 else ("top", "bottom"))]
            half = (width - 8) // 2
            return [
                pick(label, key == prefs.hud() and prefs.dash(),
                     _pick_layout(key), w)
                for key, label, _detail in gauges.LAYOUTS
            ] + [pick(label, key == now, _pick_anchor(name, key), half)
                 for key, label in sides]

        def button(text, on_click, primary=False):
            return ui.button(text, on_click, primary=primary)

        titles = {"main": "Ride", "look": "Look", "gauges": "Gauge layout"}

        def show(view, paint=True):
            rows = {"main": main_rows, "look": look_rows,
                    "gauges": gauge_rows}[view]()
            closers = ([button("Back", lambda e: show("main"))]
                       if view != "main" else []) + [button("Close", _dismiss)]
            if wide:
                # Sideways the height is the whole problem, and the last line
                # of the grid has room: Back and Close go there instead of a
                # row of their own under it.
                for closer in closers:
                    closer.width = row_w
                rows = rows + closers
            sheet.content = ft.Container(width=width, content=ft.Column(
                spacing=8, tight=True, scroll=ft.ScrollMode.AUTO,
                controls=[grid(rows)]))
            # Sideways there is no height to spare for a title on the main
            # sheet; the rows say what they are.
            sheet.title = (None if wide and view == "main" else
                           ft.Text(titles[view], size=16, color=pal["text"]))
            sheet.actions = [] if wide else closers
            _M["sheet_view"] = view
            if paint:
                page.update()

        sheet = ft.AlertDialog(bgcolor=pal["bg"])
        show("main", paint=False)
        try:
            page.show_dialog(sheet)
        except Exception as exc:                     # noqa: BLE001 - no dialog
            print(f"[navi] show_dialog failed: {type(exc).__name__}: {exc}",
                  flush=True)

    # Positioned children rather than expanded ones: a Stack of controls that all
    # want to fill it is how a Flutter layout ends up measuring forever, and this
    # page froze on both her phones.
    map_layers = [ft.Container(expand=True, content=the_map)]
    band = []
    if prefs.dash():
        if layout == "strip":
            _M["dash_holder"] = ft.Container(
                padding=PAD(8, 12), bgcolor=p["bg"], content=_dash_row())
            band.append(_M["dash_holder"])
        else:
            # ignore_interactions so panning and pinching work THROUGH the
            # gauges; without it the bottom of the map cannot be dragged.
            wide = is_wide(page)
            _M["wide"] = wide
            _M["anchor"] = prefs.hud_anchor(wide)
            _M["overlay_holder"] = ft.Container(
                left=0, right=0, bottom=0, top=0, ignore_interactions=True,
                content=gauges.overlay(_hud_state(), layout, wide,
                                       _M["anchor"]))
            map_layers.append(_M["overlay_holder"])

    # Two buttons, bottom right, floating. Record is the only thing worth a tap
    # while moving; everything else is behind the second one.
    # All the floating chrome in ONE column, top-left, under the menu button.
    #
    # Her "y not have it like underneath the hamburger menu and options buttons
    # like above?" - and she is right that it is the consistent answer. They
    # were pinned bottom-right, which put Record on top of the ribbon and meant
    # the map had controls in two opposite corners for no reason. One column
    # leaves the entire bottom edge to the gauges and the entire right side to
    # the map.
    map_layers.append(ft.Container(
        left=12, top=74,   # 12 + 52 + a 10 gap: one column with the menu button
        content=ft.Column(spacing=10, tight=True, controls=[
            _round(ft.Icons.STOP if recording else ft.Icons.FIBER_MANUAL_RECORD,
                   _toggle_record,
                   "Stop recording" if recording else "Record this ride",
                   primary=not recording),
            _round(ft.Icons.TUNE, _open_actions, "Ride options"),
        ])))

    return ft.Column(spacing=0, expand=True, controls=[
        prompt_band, turn_band, pin_band, voice_band, map_band,
        ft.Container(expand=True, content=ft.Stack(expand=True,
                                                   controls=map_layers)),
        *band])


#: The map control and the layer contents that change, held across rebuilds.
_MAP = {"key": None, "map": None, "marker": None, "dest": None,
        "track": None, "route": None}


def _map_parts(start, choice, p):
    """The map, built once and reused. THIS IS WHY THE APP WAS UNUSABLE.

    `main.render()` rebuilds the whole surface on every tap that changes
    anything - the bike switch, the gauge toggle, the tile cycle, the
    microphone, the record button, every setting. It clears `page.controls`
    and calls `build()` again.

    `build()` used to construct a brand new `fmap.Map` each time. A new Map is
    a new Flutter widget with a new TileLayer, so flutter_map threw away its
    tile cache and re-fetched every tile on screen - forty of them - for a tap
    that changed a colour. That is what "lags after trying anything" was, and
    why it never recovered: each tap queued another forty fetches behind the
    last.

    Agedashii Moto never rebuilds its map, which is most of why Moto is fast.

    So the Map is built once and kept. Only a genuinely different map earns a
    new one: a different tile source or a different zoom range. Everything
    else - where the bike is, which way it points, the track behind it, the
    route ahead, the destination pin, and the theme's colours - is a field
    assignment on a control that already exists.

    Colours are deliberately NOT part of the key. Switching to Red night at
    dusk is exactly when she is riding, and it must not cost a full tile
    refetch; the caller recolours the existing controls instead.
    """
    key = (choice["url"], choice.get("zooms"))
    if _MAP["key"] == key and _MAP["map"] is not None:
        return _MAP

    marker = fmap.Marker(
        coordinates=fmap.MapLatitudeLongitude(*start), width=38, height=38,
        content=ui.bike_marker(prefs.bike(), live.heading()))
    dest_layer = fmap.MarkerLayer(markers=[])
    track_line = fmap.PolylineMarker(
        # The tail, not the whole ride. `on_position` has always sliced to
        # TRACK_POINTS, but the first build did not - so opening the map part
        # way through a four-hour ride handed Flutter every coordinate at once
        # and drew them, which is the one moment it can least afford it.
        coordinates=[fmap.MapLatitudeLongitude(*q)
                     for q in live.LIVE.get("track", [])[-TRACK_POINTS:]],
        color=p["good"], stroke_width=4)
    route_line = fmap.PolylineMarker(coordinates=[], color=p["road"],
                                     stroke_width=6, border_color=p["bg"],
                                     border_stroke_width=1)
    the_map = fmap.Map(
        expand=True,
        initial_center=fmap.MapLatitudeLongitude(*start),
        initial_zoom=15, min_zoom=3, max_zoom=18, bgcolor=p["bg"],
        layers=[fmap.TileLayer(url_template=choice["url"],
                               user_agent_package_name=tiles.USER_AGENT,
                               # Without these the layer assumes the source
                               # goes to z19. The offline archive stops at
                               # z15 to stay under 40 MB, so every tile from
                               # z16 up came back empty and the map went
                               # blank at exactly the zoom she rides at.
                               # Given the real range it scales z15 instead.
                               min_native_zoom=(choice.get("zooms") or (0, 19))[0],
                               max_native_zoom=(choice.get("zooms") or (0, 19))[1]),
                fmap.PolylineLayer(polylines=[route_line]),
                fmap.PolylineLayer(polylines=[track_line]),
                dest_layer,
                fmap.MarkerLayer(markers=[marker])])
    _MAP.update(key=key, map=the_map, marker=marker, dest=dest_layer,
                track=track_line, route=route_line)
    return _MAP


def forget_map():
    """Drop the cached map. For tests, and for a hard theme reset."""
    _MAP.update(key=None, map=None, marker=None, dest=None,
                track=None, route=None)


def _rerender(page):
    """Ask the shell to rebuild this surface.

    Restored after being deleted by accident: the layout rewrite replaced
    everything from the end of `build()` to the end of the file, and these three
    functions lived below it. Six call sites inside `build()` reference this one,
    so every control that changes a setting - the bike switch, the gauge toggle,
    the tile cycle, the microphone - raised `NameError: name '_rerender' is not
    defined` the moment it was tapped.
    """
    rerender = getattr(page, "_navi_rerender", None)
    if callable(rerender):
        rerender()


def repaint():
    """Repaint the live readouts from outside this module, if it is on screen."""
    paint = _M.get("paint")
    if callable(paint):
        paint()
        return True
    return False


async def _dash_clock(page, generation):
    """Redraw the gauges on a clock, not only when the GPS moves.

    THE GAUGES HAD NO CLOCK. `paint()` ran from `on_position` and from
    `build()`, and nowhere else - `repaint()` existed and nothing ever called
    it. So parked, or stopped at a light, or indoors with no fix, every sensor
    was reading correctly and updating its channel and NOTHING ON SCREEN EVER
    CHANGED. Her words: "most of the sensors are / should be indicating things
    and i don't really see them doing anything."

    The sensors run at 400 ms and the GPS at 1 Hz, so tying the drawing to the
    GPS meant lean, g-force, tilt, pressure and RPM could only ever appear
    while she was already moving - which is exactly when she cannot look.

    One task, at DASH_EVERY_S, cancelled by generation: every rebuild bumps
    the counter and the previous loop notices and returns. It draws only when
    the map is the surface on screen and the dash is switched on, so a parked
    phone on the Settings page costs nothing.
    """
    import asyncio
    while _M.get("clock") == generation:
        await asyncio.sleep(DASH_EVERY_S)
        if _M.get("clock") != generation:
            return
        if not _showing_ride(page) or not prefs.dash():
            continue
        paint = _M.get("paint")
        if callable(paint):
            paint()
            _update(page)


def channel_report():
    """Which telemetry channels this build is getting, by kind."""
    return {kind: telemetry.kinds(kind)
            for kind in ("measured", "derived", "estimated")}
