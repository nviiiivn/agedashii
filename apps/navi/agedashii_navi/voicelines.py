"""Her recorded lines: which clip says what, and what fills in where one is missing.

Hers: "GO LEFT IF YOU WANT TO LIVE" and "FOLLOW THIS STREET TO THE CHOPPA".
These are WHOLE LINES in her own words, one per maneuver, not our sentence in
another voice - so a clip replaces the maneuver outright, a distance clip plays
before it when the band calls for one, and street names are left off by default
because a robot saying "Broadway" after her line would ruin the joke.

The names are fixed: they were sent to her and she may be recording against
them now (voice_import.NAMES). The clips live in the app's storage and arrive
through voice_import's picker; nothing of anyone's voice ships in the APK.

THE RULE: a missing clip is replaced by the phone's voice for THAT ITEM ALONE.
Never silence, and a half-filled set works and improves as she adds files. With
none of her clips applying, the prompt is our whole sentence, unchanged.

Pure: no audio here. `playlist` returns [(kind, value, fallback)] - kind "clip"
with a file path, or "tts" with words - and speech.py plays it.
"""
import math

from . import guidance
from .voice_import import FIXED_DISTANCES, distance_feet

#: Instruction words -> her maneuver clip, checked in order. The offline router
#: says "bear" where the clips say "slight"; server routes may say either.
MANEUVERS = (("roundabout", "roundabout"),
             ("u-turn", "uturn"), ("turn around", "uturn"),
             ("sharp left", "sharp-left"), ("sharp right", "sharp-right"),
             ("bear left", "slight-left"), ("slight left", "slight-left"),
             ("bear right", "slight-right"), ("slight right", "slight-right"),
             ("turn left", "turn-left"), ("turn right", "turn-right"),
             ("merge", "merge"), ("exit", "exit"), ("ramp", "exit"),
             ("arrive", "arrive"), ("destination", "arrive"),
             ("continue", "continue"), ("straight", "continue"))

#: How far off one of her distance clips may be and still be said: a factor of
#: 1.5 either way. Her Arnold set (200, 400, 800, 1500 ft) then covers 133 to
#: 2250 ft without a gap. Beyond it the phone says the distance: "two hundred"
#: for a turn a mile away is a wrong instruction, and at 60 mph a wrong
#: distance is far worse than a robot saying the right one. Agreed with minion 2
#: for the Kotlin app, which reads the same files and uses the same table.
DIST_FACTOR = 1.5
FT_PER_M = 3.280839895

#: What the phone says for a situation she has no clip for. `speeding` has
#: none on purpose: a robot nagging about speed is not what she asked for, so
#: it only ever plays her own line.
SITUATIONS = {"offroute": "Off route.", "rerouting": "Re-routing.",
              "start": "Starting the route.", "speeding": None}


def maneuver_name(instruction):
    """Her clip name for an instruction, or None when none fits.

    Matched on the verb alone, before any " onto ": "continue onto Ramp Road"
    is a continue, not an exit.
    """
    if not instruction:
        return None
    text = _bare(instruction).lower()
    for words, name in MANEUVERS:
        if words in text:
            return name
    return None


def distance_clip(metres, clips):
    """(name, path) of the distance clip she HAS nearest `metres`, or None.

    `clips` is {name: path}: any `dist-<N>ft`, `dist-<N>mile(s)`, dist-quarter
    or dist-half (voice_import.distance_clips). Nearest by ratio - 700 ft is
    nearer 500 than 1000 - and no further off than DIST_FACTOR. A tie goes to
    the shorter one, so a turn is never announced as further than it is.
    """
    if not metres or metres <= 0:
        return None
    feet = metres * FT_PER_M
    best = None
    for name, path in (clips or {}).items():
        at = distance_feet(name)
        if not at or not path:
            continue
        key = (round(abs(math.log(feet / at)), 9), at)
        if best is None or key < best[0]:
            best = (key, name, path)
    if best is None or best[0][0] > round(math.log(DIST_FACTOR), 9):
        return None
    return best[1], best[2]


def _bare(instruction):
    """"turn left onto Grand" -> "turn left": her line replaces the verb only."""
    return (instruction or "continue").split(" onto ")[0].strip()


def playlist(text, parts, clip_path, streets=False, imperial=True, distances=None):
    """The items to play for one fired prompt.

    `parts` is what guidance fired ({instruction, street, metres, band}) or
    None; `clip_path(name)` gives a file or None; `streets` is her switch for
    saying the street name after her line (off by default); `distances` is
    {name: path} of every distance clip she has - by default the fixed names,
    looked up through `clip_path`.
    """
    if not parts:
        return [("tts", text, None)]
    band = parts.get("band")
    metres = parts.get("metres") if band != "now" else None
    man = maneuver_name(parts.get("instruction"))
    man_clip = clip_path(man) if man else None
    if distances is None:
        distances = {n: clip_path(n) for n in FIXED_DISTANCES}
    found = distance_clip(metres, distances) if metres else None
    dist_clip = found[1] if found else None
    if man_clip is None and dist_clip is None:
        return [("tts", text, None)]          # none of hers applies: our sentence
    items = []
    if metres:
        spoken = f"In {guidance.distance_text(metres, imperial)},"
        items.append(("clip", dist_clip, spoken) if dist_clip else ("tts", spoken, None))
    bare = _bare(parts.get("instruction"))
    items.append(("clip", man_clip, bare) if man_clip else ("tts", bare, None))
    street = parts.get("street")
    if streets and street and man != "arrive":
        items.append(("tts", f"onto {street}", None))
    return items


def situation(name, clip_path):
    """Items for a situation - her clip, else the phone's words, else nothing."""
    clip = clip_path(name)
    if clip:
        return [("clip", clip, SITUATIONS.get(name))]
    words = SITUATIONS.get(name)
    return [("tts", words, None)] if words else []


#: What the phone says in place of each maneuver clip, for the Settings
#: preview. The offline router's own words (agedashii_washi.routing.TURNS and
#: "arrive at your destination"); it never says sharp, merge or exit, so those
#: four clips only play on a route from the server.
STAND_IN = {"turn-left": "turn left", "turn-right": "turn right",
            "slight-left": "bear left", "slight-right": "bear right",
            "sharp-left": "sharp left", "sharp-right": "sharp right",
            "uturn": "make a U-turn", "continue": "continue straight",
            "merge": "merge", "exit": "take the exit",
            "arrive": "arrive at your destination",
            "roundabout": "take the roundabout"}


def stand_in(name, imperial=True):
    """The phone's words for `name` when she has no clip, or None (speeding)."""
    if name in STAND_IN:
        return STAND_IN[name]
    feet = distance_feet(name)
    if feet:
        return f"In {guidance.distance_text(feet / FT_PER_M, imperial)},"
    return SITUATIONS.get(name)


def preview(name, clip_path, imperial=True):
    """What the Settings play button plays: exactly what a ride would."""
    words = stand_in(name, imperial)
    clip = clip_path(name)
    if clip:
        return [("clip", clip, words)]
    return [("tts", words, None)] if words else []


#: Seconds between two "speeding" lines. One per climb over the limit, and not
#: again inside this, so riding along the limit is not a nag.
SPEEDING_EVERY_S = 60.0


class Cues:
    """Which situation lines a fix calls for - each on its EDGE, never per fix.

    start      a route to a NEW destination is up; a re-route of the same one
               is not a start
    offroute   the follower has just confirmed she is off the route
    rerouting  the first re-route asked for in that off-route spell. Once per
               spell: a search that keeps failing asks again every 12 s
               (live.REROUTE_EVERY), and she does not need telling each time
    speeding   the speed has just gone over (guidance.over_limit), at most
               once per SPEEDING_EVERY_S

    Fed only fixes that were taken (live.set_position's "moved"): a rejected
    fix carries no status, and reading that as "back on route" would say
    "off route" again on the next good one.
    """

    def __init__(self):
        self.dest = None
        self.off = False
        self.rerouted = False
        self.fast = False
        self.fast_at = None

    def update(self, status, dest, reroute, over, now):
        names = []
        if status and status.get("ok") and dest is not None and dest is not self.dest:
            # By identity: live.set_destination makes a new tuple each time,
            # so the same place chosen again is a new start.
            self.dest = dest
            names.append("start")
        off = bool(status and status.get("off_route"))
        if off and not self.off:
            names.append("offroute")
        if not off:
            self.rerouted = False
        elif reroute and not self.rerouted:
            self.rerouted = True
            names.append("rerouting")
        self.off = off
        if over and not self.fast and (self.fast_at is None
                                       or now - self.fast_at >= SPEEDING_EVERY_S):
            self.fast_at = now
            names.append("speeding")
        self.fast = bool(over)
        return names
