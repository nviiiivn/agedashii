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
import random
import re

from . import guidance
from .voice_import import FIXED_DISTANCES, FLOURISH_MOODS, distance_feet

#: Instruction words -> her maneuver clip, checked in order. The offline router
#: says "bear" where the clips say "slight"; server routes may say either.
MANEUVERS = (("roundabout", "roundabout"),
             ("u-turn", "uturn"), ("turn around", "uturn"),
             ("sharp left", "sharp-left"), ("sharp right", "sharp-right"),
             ("bear left", "slight-left"), ("slight left", "slight-left"),
             ("keep left", "slight-left"),
             ("bear right", "slight-right"), ("slight right", "slight-right"),
             ("keep right", "slight-right"),
             ("turn left", "turn-left"), ("turn right", "turn-right"),
             ("merge", "merge"), ("exit", "exit"), ("ramp", "exit"),
             ("arrive", "arrive"), ("destination", "arrive"),
             ("continue", "continue"), ("straight", "continue"))

#: How far off one of her distance clips may be and still be said: a quarter
#: either way (AGEDASHII, from minion 4's audit - "fifteen hundred feet" for a
#: turn 2,200 ft away is a wrong instruction). Her Arnold set (200, 400, 800,
#: 1500 ft) covers 160-250, 320-500, 640-1000 and 1200-1875 ft; between and
#: beyond, the phone says the distance. At 60 mph a wrong distance is far worse
#: than a robot saying the right one. Minion 2's Kotlin app reads the same files
#: and must use the same table.
DIST_FACTOR = 1.25
FT_PER_M = 3.280839895

#: What the phone says for a situation she has no clip for. `speeding` has
#: none on purpose: a robot nagging about speed is not what she asked for, so
#: it only ever plays her own line.
SITUATIONS = {"offroute": "Off route.", "rerouting": "Re-routing.",
              "start": "Starting the route.", "speeding": None}


#: Her maneuver clip names - what a router's `kind` may be taken as directly.
#: routing.KINDS also has "start" (the depart, "head north ..."), which is NOT
#: her "start" line: that is the situation, said once as the route begins.
CLIP_KINDS = frozenset(name for _words, name in MANEUVERS)


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
    # KIND BEATS WORDS: words are for her to read, the router's kind is for the
    # machine to switch on (decided with the words offline, from Valhalla's
    # maneuver TYPE on server routes). Matching the sentence broke the first
    # time anything was reworded. The words only when there is no kind.
    #
    # A kind of None IS an answer: leaving a roundabout, a ferry - no clip says
    # it honestly, so the words are spoken. Matching the words there found
    # "roundabout" and played her ENTERING line on the way out. Only a caller
    # that fired no kind at all (none does in the app) falls back to words.
    kind = parts.get("kind")
    if "kind" not in parts:
        man = maneuver_name(parts.get("instruction"))
    elif kind in CLIP_KINDS:
        man = kind
    else:
        man = None                          # None, or "start" (the depart)
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
    # Her line replaces the verb, so what only the words carry follows it in
    # the phone's voice: which exit off a roundabout, which side a ramp is on.
    if man_clip:
        tail = _after_her_line(man, parts.get("instruction"))
        if tail:
            items.append(("tts", tail, None))
    street = parts.get("street")
    if streets and street and man != "arrive":
        items.append(("tts", f"onto {street}", None))
    return items


_EXIT_NUMBER = re.compile(r"\btake the (\w+) exit\b")
_SIDE = re.compile(r"\bon the (left|right)\b")


def _after_her_line(man, instruction):
    """What must still be said after her clip for `man`, or ""."""
    text = (instruction or "").lower()
    if man == "roundabout":
        m = _EXIT_NUMBER.search(text)
        return f"take the {m.group(1)} exit" if m else ""
    if man == "exit":
        m = _SIDE.search(text)
        return f"on the {m.group(1)}" if m else ""
    return ""


def situation(name, clip_path):
    """Items for a situation - her clip, else the phone's words, else nothing."""
    clip = clip_path(name)
    if clip:
        return [("clip", clip, SITUATIONS.get(name))]
    words = SITUATIONS.get(name)
    return [("tts", words, None)] if words else []


#: What the phone says in place of each maneuver clip, for the Settings
#: preview: the offline router's own words for each kind (routing.TURNS,
#: "arrive at your destination"). Since minion 4's maneuver work the offline
#: router says sharp, merge, exit and roundabout too, so every clip can play
#: with no signal.
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
        self.refusal_said = False

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
            self.refusal_said = False
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

    def refused(self):
        """True the first time a re-route is refused in this off-route spell.

        After "off route" and "re-routing", a refusal was SILENCE while the
        banner said re-routing and the search retried every 12 s - and with an
        offline-only re-route and a Bay-only map, she meets it at the edge.
        Once per spell, like "re-routing": the retries need no voice.
        """
        if self.refusal_said:
            return False
        self.refusal_said = True
        return True


# ------------------------------------------------------ character lines

#: Hers: "i asked for almost everything to be prefaced or postfaced with some
#: other clips and like DO IT NOW and yeaaararrghhhhh and get to the choppaa
#: ... peppered in randomly and accordingly." A nav voice is not a character;
#: these make it one. Files are flourish-<mood>-<slug>.<ext> (agreed with
#: minion 5, who levels the pool, and minion 2, whose Kotlin app mirrors this).
#: The moods are voice_import.FLOURISH_MOODS - one list, where names are read.
#: How often each kind of moment gets one - ONE edit to change. "urgent" is
#: the ordinary turn: one in four, so it stays a surprise.
FLOURISH_RATE = {"urgent": 0.25, "arrival": 1.0, "start": 1.0,
                 "angry": 0.5, "scold": 0.5,
                 # After the app has SAID what failed (a refused re-route) -
                 # never instead of the words, and not at all with no voice.
                 "sorry": 1.0}
#: Her cough, "now and then": dropped into the join between a prompt's
#: distance and its turn ("in a quarter mile *cough* turn left"). Rarer than
#: any flourish, far/near only, never twice running.
COUGH_RATE = 0.08


def flourish_mood(names, parts):
    """Which pool this moment draws from, or None for no flourish.

    THE INSTRUCTION IS NEVER DELAYED: on the "now" band - six seconds from the
    corner - nothing is added, except to arriving, where nothing follows.
    Arrival is ONLY that "now" prompt; the far and near ones get none.
    Otherwise by priority: angry (off route, re-routing) > scold (speeding) >
    start > urgent (an ordinary turn prompt).
    """
    parts = parts or {}
    arriving = parts.get("kind") == "arrive" or (
        "kind" not in parts and maneuver_name(parts.get("instruction")) == "arrive")
    band = parts.get("band")
    if arriving and band == "now":
        return "arrival"
    if band == "now":
        return None
    if "offroute" in names or "rerouting" in names:
        return "angry"
    if "speeding" in names:
        return "scold"
    if "start" in names:
        return "start"
    if parts and not arriving:
        return "urgent"
    return None


class Flourish:
    """Picks the character line, remembering enough to not repeat itself.

    Never the same one twice running; at most one per maneuver across its
    far/near/now prompts. `rng` is for tests.
    """

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.last = None
        self.maneuver = None
        self.coughed = False

    def pick(self, mood, pool, maneuver=None):
        """(name, path) from `pool` ({name: path}) for `mood`, or None."""
        if not pool or mood not in FLOURISH_RATE:
            return None
        if maneuver is not None and maneuver == self.maneuver:
            return None
        if self.rng.random() >= FLOURISH_RATE[mood]:
            return None
        names = sorted(n for n, path in pool.items() if path and n != self.last)
        if not names:
            return None
        name = self.rng.choice(names)
        self.last = name
        if maneuver is not None:
            self.maneuver = maneuver
        return name, pool[name]

    def cough(self, items, pool, band=None):
        """`items` with a cough in the join after the distance, now and then.

        Only between the FIRST two pieces - the distance and the turn - of a
        far/near prompt: never inside a clip, never on the "now" band, never
        on two prompts running. A cough is a ("flourish", ...) item, so any
        newer prompt cuts it like one.
        """
        if band == "now" or len(items) < 2 or not pool:
            self.coughed = False
            return items
        if self.coughed or self.rng.random() >= COUGH_RATE:
            self.coughed = False
            return items
        path = pool[self.rng.choice(sorted(pool))]
        self.coughed = True
        return [items[0], ("flourish", path, None)] + list(items[1:])


def refusal(reason):
    """A refused route, made for the ear: routing's screen sentence is too
    long to hear at speed, so the ones she can act on get their own words."""
    from agedashii_washi import routing
    if reason == routing.START_OUTSIDE:
        words = ("No route. You are outside the offline map. "
                 "Ride back into it, or find signal.")
    elif reason == routing.DEST_OUTSIDE:
        words = "No route. That place is outside the offline map."
    else:
        words = "No new route yet. It will keep trying."
    return [("tts", words, None)]
