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
#: it only ever plays her own line. Nor has `open`, the launch line
#: (character.opening): the phone's voice greeting her is not the line.
SITUATIONS = {"offroute": "Off route.", "rerouting": "Re-routing.",
              "start": "Starting the route.", "speeding": None, "open": None}


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


class Core:
    """The instruction's pieces for one prompt - what every arrangement keeps
    whole: the distance (said first, or after the maneuver), the maneuver
    line, its two halves when it can be cut, and its tail (the side or exit
    number, the street), which is glued to the maneuver whichever comes first.
    """

    def __init__(self, d_first, d_after, m, halves, tail):
        self.d_first = d_first      # "In 400 feet," before the maneuver
        self.d_after = d_after      # "in 400 feet" after it
        self.m = m
        self.halves = halves        # [a, b] or None
        self.tail = tail

    def classic(self):
        """Distance, maneuver, tail - the order before composition."""
        return ([self.d_first] if self.d_first else []) + [self.m] + self.tail


def core(parts, clip_path, streets=False, imperial=True, distances=None,
         halves=None):
    """The Core for one fired prompt, or None when none of her clips applies
    and the prompt is our own sentence, unchanged.

    `parts` is what guidance fired ({instruction, street, metres, band,
    kind}); `clip_path(name)` gives a file or None; `streets` is her switch
    for saying the street name after her line (off by default); `distances`
    is {name: path} of every distance clip she has - by default the fixed
    names, looked up through `clip_path`; `halves(slot, path)` gives the two
    halves of that slot's line, or None (see `halves`).
    """
    if not parts:
        return None
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
    man_clip = None
    if man == "exit":
        # The exit that says its own side, when there is one for this side
        # (her "exit left human"); else the plain exit and the phone's side.
        side = _SIDE.search((parts.get("instruction") or "").lower())
        if side:
            man_clip = clip_path(f"exit-{side.group(1)}")
            if man_clip:
                man = f"exit-{side.group(1)}"
    if man_clip is None and man:
        man_clip = clip_path(man)
    if distances is None:
        distances = {n: clip_path(n) for n in FIXED_DISTANCES}
    found = distance_clip(metres, distances) if metres else None
    dist_clip = found[1] if found else None
    if man_clip is None and dist_clip is None:
        return None                         # none of hers applies: our sentence
    d_first = d_after = None
    if metres:
        words = guidance.distance_text(metres, imperial)
        first, after = f"In {words},", f"in {words}"
        d_first = ("clip", dist_clip, first) if dist_clip else ("tts", first, None)
        d_after = ("clip", dist_clip, after) if dist_clip else ("tts", after, None)
    bare = _bare(parts.get("instruction"))
    m = ("clip", man_clip, bare) if man_clip else ("tts", bare, None)
    cut = halves(man, man_clip) if (man_clip and halves) else None
    # Either half that will not play is replaced by the WHOLE maneuver in the
    # phone's voice - "turn *cough* Turn left" still says left (minion 2).
    cut = [("clip", cut[0], bare), ("clip", cut[1], bare)] if cut else None
    tail = []
    # Her line replaces the verb, so what only the words carry follows it in
    # the phone's voice: which exit off a roundabout, which side a ramp is on.
    if man_clip:
        said = _after_her_line(man, parts.get("instruction"))
        if said:
            tail.append(("tts", said, None))
    street = parts.get("street")
    if streets and street and man != "arrive":
        tail.append(("tts", f"onto {street}", None))
    return Core(d_first, d_after, m, cut, tail)


def playlist(text, parts, clip_path, streets=False, imperial=True, distances=None):
    """The items for one fired prompt in the classic order - distance,
    maneuver, tail - or our own sentence when none of her clips applies.
    A ride composes the same Core instead (Arranger)."""
    got = core(parts, clip_path, streets, imperial, distances)
    return got.classic() if got else [("tts", text, None)]


def and_then(then, clip_path):
    """The items after a "now" prompt for the maneuver straight after it -
    guidance's parts["then"]: "and then" (her clip, else the phone's words),
    then that maneuver's line and its tail, at the "now" band: no distance,
    no street. An exit keeps its side, a roundabout its exit number."""
    joint = clip_path("and-then")
    items = [("clip", joint, JOINS["and-then"]) if joint
             else ("tts", JOINS["and-then"], None)]
    nxt = core(dict(then, band="now"), clip_path)
    return items + (nxt.classic() if nxt else
                    [("tts", _bare(then.get("instruction")), None)])


def halves(slot, path, source, clip_path):
    """[a, b] - the two halves of `slot`'s line, part-<slot>-a and -b - or None.

    Only for the plain line: `path` must be the slot's own clip, not one of
    his wild takes, and both halves must come from the same place as it -
    never her half and Arnold's half in one line. `source` and `clip_path`
    are voice_import's.
    """
    if not path or path != clip_path(slot):
        return None
    whose = source(slot)
    a, b = f"part-{slot}-a", f"part-{slot}-b"
    if source(a) != whose or source(b) != whose:
        return None
    got = clip_path(a), clip_path(b)
    return list(got) if all(got) else None


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
#: The exits that say their own side. Never matched from words: an "exit"
#: whose instruction says "on the left" or "on the right" plays one when
#: there is one (core), else "exit" and the phone adds the side.
SIDED = {"exit-left": "take the exit on the left",
         "exit-right": "take the exit on the right"}
#: What joins one maneuver to the next (guidance's "then"): her AndThen.
JOINS = {"and-then": "and then"}


def stand_in(name, imperial=True):
    """The phone's words for `name` when she has no clip, or None (speeding, open)."""
    if name in STAND_IN:
        return STAND_IN[name]
    if name in SIDED:
        return SIDED[name]
    if name in JOINS:
        return JOINS[name]
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


#: While she stays off route, at most one situation line (off route /
#: re-routing) per this many seconds; the FIRST of a spell always speaks at
#: once. Riding away from each fresh route played one every 5-7 s on the
#: emulator - 20 in 80 s - and a voice that talks constantly is one she stops
#: hearing (AGEDASHII's ruling; minion 2's Kotlin Cues use the same numbers).
SITUATION_EVERY_S = 30.0
#: A spell ends only after she has been ON route this long without a break.
#: A re-route hands her a fresh route and reads "on" at once; if that ended
#: the spell, every re-route would reset the cap and the spam would stay.
REJOIN_S = 30.0


class Cues:
    """Which situation lines a fix calls for - each on its EDGE, never per fix.

    start      a route to a NEW destination is up; a re-route of the same one
               is not a start
    offroute   the follower has just confirmed she is off the route
    rerouting  a re-route has just been asked for (re-arms whenever a fresh
               route briefly puts her back on one)
               Both are held to SITUATION_EVERY_S while a spell lasts, except
               the spell's FIRST line, which always speaks at once; a spell
               ends after REJOIN_S on route. The new route's instructions are
               never held - this caps situation lines, not guidance.
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
        self.spell = False            # an off-route spell is running
        self.situ_at = None           # when its last situation line spoke
        self.on_since = None          # on route since, within a spell

    def update(self, status, dest, reroute, over, now):
        names = []
        if status and status.get("ok") and dest is not None and dest is not self.dest:
            # By identity: live.set_destination makes a new tuple each time,
            # so the same place chosen again is a new start.
            self.dest = dest
            names.append("start")
        off = bool(status and status.get("off_route"))
        # The spell ends after REJOIN_S on route without a break.
        if not off:
            if self.on_since is None:
                self.on_since = now
            if self.spell and now - self.on_since >= REJOIN_S:
                self.spell = False
                self.refusal_said = False
        else:
            self.on_since = None
        situ = []
        if off and not self.off:
            situ.append("offroute")
        if not off:
            self.rerouted = False             # re-arms on a momentary new route
        elif reroute and not self.rerouted:
            self.rerouted = True
            # One situation line per edge: on the fix that goes off route,
            # "off route" says it; "re-routing" on top was the three-line
            # stack before the new instruction (minion 1's Flet rides;
            # AGEDASHII's ruling, minion 2's Kotlin is the same).
            if not situ:
                situ.append("rerouting")
        self.off = off
        if situ:
            # The first line of a spell always speaks; after that one situation
            # playlist per SITUATION_EVERY_S - both edges of one fix together.
            if not self.spell:
                self.spell = True
                self.situ_at = now
                names += situ
            elif now - self.situ_at >= SITUATION_EVERY_S:
                self.situ_at = now
                names += situ
        if over and not self.fast and (self.fast_at is None
                                       or now - self.fast_at >= SPEEDING_EVERY_S):
            self.fast_at = now
            names.append("speeding")
        self.fast = bool(over)
        return names

    def refused(self):
        """True the first time a re-route is refused in this off-route spell
        (a spell as the cap defines it: it ends only after REJOIN_S on route).

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
                 "sorry": 1.0,
                 # A tap in the app - opening search, picking a destination.
                 # Rarer: theatre on a button pressed ten times a day wears
                 # thin fast. Minion 2's Kotlin number, kept identical.
                 "ui": 0.15,
                 # Kind lines on a correct turn, far or near. Kotlin's number.
                 "banter": 0.25,
                 # Leaving: she quit, or stopped the GPS, or stopped
                 # recording. Always, because it is the last thing he says -
                 # and flourish_moods can never return this mood, so nothing
                 # on the road can reach it. Her rule: "HASTA LA VISTA BABY
                 # should only play when i quit or when i stop gps/recording".
                 "farewell": 1.0}
#: Her cough, "now and then": inside the instruction - in the middle of the
#: maneuver line when it comes in halves ("continue *cough* straight"), else
#: in the join between the distance and the turn. Rarer than any flourish,
#: far/near only, never twice running.
COUGH_RATE = 0.08
#: How often a far/near prompt says the maneuver FIRST: "turn left, in 400
#: feet" (hers: "turn left" + "in 200 meters"). Agreed with minion 2.
P_MANEUVER_FIRST = 0.3
#: How often a flourish gets a second one after it: "do it now" + "damn it".
#: Never on the "now" band. Agreed with minion 2.
P_SECOND = 0.35


#: At most one angry line a minute. A long wrong-turn burst rolled angry on
#: every off-route spell - three in 25 s on the emulator (minion 2). While it is
#: barred, the next mood in line may still fire (AGEDASHII's ruling).
ANGRY_EVERY_S = 60.0


def flourish_mood(names, parts, angry_ok=True, fired=()):
    """Which pool this moment draws from, or None for no flourish.

    Always LAST, after the whole instruction, so it never delays one. By band,
    per AGEDASHII's rulings (minion 2's Kotlin is identical):
    - "now": urgent for a turn ("DO IT NOW" - she asked for it by name), or
      arrival for the arrive maneuver, where nothing follows. Nothing else.
    - "far" and "near": angry (off route, re-routing) > scold (speeding) >
      start > banter - what she DID, never urgent: "In half a mile... NOW!"
      could send her in early, and "RIGHT NOW" at 900 ft was false. Banter
      only on a correct turn, never with a mistake cued.
    The arrive maneuver's far and near prompts get no line at all.
    `angry_ok` False (inside ANGRY_EVERY_S) passes over angry to the next.
    """
    moods = flourish_moods(names, parts, angry_ok, fired)
    return moods[0] if moods else None


def _arriving(parts):
    return parts.get("kind") == "arrive" or (
        "kind" not in parts and maneuver_name(parts.get("instruction")) == "arrive")


def flourish_moods(names, parts, angry_ok=True, fired=()):
    """Every mood this moment allows, best first (see flourish_mood, which
    is the first of these). `fired` is the cues as they FIRED on this fix,
    when some are held back from `names` (ride._speeding_alone): a mistake
    among them still rules out banter."""
    parts = parts or {}
    arriving = _arriving(parts)
    band = parts.get("band")
    if band == "now":
        if arriving:
            return ["arrival"]
        return ["urgent"] if parts else []
    if arriving and parts:
        return []                   # arriving's line is for being there
    mistake = any(n in names or n in fired
                  for n in ("offroute", "rerouting", "speeding"))
    moods = []
    if ("offroute" in names or "rerouting" in names) and angry_ok:
        moods.append("angry")
    if "speeding" in names:
        moods.append("scold")
    if "start" in names:
        moods.append("start")
    # No urgent short of "now": "RIGHT NOW" at 900 ft on a near prompt at 34
    # mph was false. A correct turn's far and near get BANTER - true and kind,
    # the app enjoying itself with her - and a fix that carries a mistake
    # never does: "big mistake" + "haha" is at her expense (AGEDASHII's
    # rulings; minion 2's Kotlin is the same).
    if parts and band in ("far", "near") and not mistake:
        moods.append("banter")
    return moods


def second_moods(names, parts, angry_ok=True, fired=()):
    """Where a SECOND flourish may come from: the moods this moment allows,
    and nothing more - a mood matches what she DID, so angry and scold only
    ever follow the situation that earned them, never a plain turn
    (AGEDASHII's ruling after minion 1's Flet rides; minion 2's Kotlin is the
    same). None on the "now" band, which stays short, and none on the route
    start, which gets one line (the same report: "aagh" + "your clothes
    now")."""
    if (parts or {}).get("band") == "now" or "start" in names:
        return []
    return flourish_moods(names, parts, angry_ok, fired)


#: How many of a pool's recent lines are held back: the last k of a pool of
#: n, k = min(RECENT_MAX, n // 2) - two lines alternate, four hold back two,
#: nine or more hold back four. She heard the same few lines over and over
#: (AGEDASHII, relayed); one line of memory is no memory in a pool of nine.
#: A POOL is a wild slot's takes, a flourish mood, or the cough. Agreed with
#: minion 2.
RECENT_MAX = 4

_TAKE = re.compile(r"-[0-9]+$")


def phrase(name):
    """What a clip SAYS: its name less a trailing take number - "what the
    hell" 4 and 6 are one phrase, and hearing one then the other is hearing
    the same line twice (AGEDASHII's ruling, agreed with minion 2)."""
    return _TAKE.sub("", name) if name else name


def _phrase_order(p):
    return (p is None, p or "")


class Recent:
    """Each pool's recent PHRASES, newest last."""

    def __init__(self):
        self.picks = {}

    def choose(self, rng, pool, names, exclude=()):
        """A take from `names` - the pool's whole list - or None.

        The phrase first, evenly among the pool's phrases less the last
        min(RECENT_MAX, p // 2) it played and any in `exclude`; then a take of
        it, evenly - so a phrase with three takes is not three times likelier.
        Only when that leaves nothing is the memory dropped (`exclude` still
        holds); a pool of one phrase holds nothing back. `names` may hold None,
        which is a phrase of its own (Wild's Waze line).
        """
        takes = {}
        for name in names:
            takes.setdefault(phrase(name), []).append(name)
        phrases = sorted(takes, key=_phrase_order)
        k = min(RECENT_MAX, len(phrases) // 2)
        held = set(self.picks.get(pool, [])[-k:]) if k else set()
        left = ([p for p in phrases if p not in held and p not in exclude]
                or [p for p in phrases if p not in exclude])
        if not left:
            return None, False
        said = rng.choice(left)
        self.heard(pool, said)
        return rng.choice(takes[said]), True

    def heard(self, pool, said):
        picks = self.picks.setdefault(pool, [])
        picks.append(said)
        del picks[:-RECENT_MAX]


class Flourish:
    """Picks the character line, remembering enough to not repeat itself.

    Each mood holds back its last few lines (Recent); at most one per
    maneuver across its far/near/now prompts. `rng` is for tests.
    """

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.recent = Recent()
        self.last = None               # the last line played, any mood
        self.maneuver = None
        self.angry_at = None

    def angry_ok(self, now):
        """Whether an angry line may play: none within ANGRY_EVERY_S."""
        return self.angry_at is None or now - self.angry_at >= ANGRY_EVERY_S

    def pick(self, mood, pool, maneuver=None, now=None):
        """(name, path) from `pool` ({name: path}) for `mood`, or None."""
        if not pool or mood not in FLOURISH_RATE:
            return None
        if maneuver is not None and maneuver == self.maneuver:
            return None
        if self.rng.random() >= FLOURISH_RATE[mood]:
            return None
        name, found = self.recent.choose(
            self.rng, mood, sorted(n for n, path in pool.items() if path))
        if not found:
            return None
        self._heard(name, mood, now)
        if maneuver is not None:
            self.maneuver = maneuver
        return name, pool[name]

    def second(self, pools, first, now=None):
        """(name, path) of a second line after `first`, or None.

        `pools` is {mood: {name: path}} for second_moods. A mood is picked
        evenly among those with a phrase other than `first`'s, then a line in
        it (Recent.choose, never `first`'s phrase). It may share first's mood
        ("DO IT NOW" + "RIGHT NOW"); an angry one still waits out
        ANGRY_EVERY_S. Rules agreed with minion 2.
        """
        if first is None or self.rng.random() >= P_SECOND:
            return None
        not_first = {phrase(first)}
        choices = {}
        for mood, pool in (pools or {}).items():
            if mood == "angry" and now is not None and not self.angry_ok(now):
                continue
            full = sorted(n for n, path in (pool or {}).items() if path)
            if any(phrase(n) not in not_first for n in full):
                choices[mood] = full
        if not choices:
            return None
        mood = self.rng.choice(sorted(choices))
        name, _found = self.recent.choose(self.rng, mood, choices[mood], exclude=not_first)
        self._heard(name, mood, now)
        return name, pools[mood][name]

    def _heard(self, name, mood, now):
        self.last = name
        if mood == "angry" and now is not None:
            self.angry_at = now


class Arranger:
    """Puts one prompt together from its pieces - COMPOSED, not a template.

    Hers: "everything needs to be significantly CREATIVE. with random things
    thrown in" - "turn left" + "in 200 meters", "continue -" + cough +
    "- straight", "exit left human" + "do it now" + "damn it". Rules agreed
    with minion 2, so both apps compose alike:

    - ORDER, far/near: the distance first, or the maneuver first
      (P_MANEUVER_FIRST). Its tail and street always stay glued to it. The
      "now" band is the maneuver alone: no distance, no reordering.
    - COUGH (COUGH_RATE, far/near, never two prompts running): inside the
      maneuver when it comes in halves, else in the join between the
      distance and the maneuver - never before the first piece of the
      instruction or after the last, where it would sound like its end.
    - FLOURISHES: after the whole instruction, always (character.flourishes
      picks them: up to two, one on "now").
    - SHAPE (order, cough, how many flourishes): never the same as the last
      far/near prompt's; a repeat flips the order, and the cough's join goes
      with it. "now" neither counts nor resets it.

    Every flourish and the cough are ("flourish", ...) items, so any newer
    prompt cuts them. The instruction itself is never cut or delayed past
    the distance: nothing but a cough goes before its end. `rng` is for tests.
    """

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.shape = None
        self.coughed = False
        self.recent = Recent()

    def arrange(self, piece, band, flourishes=(), coughs=None, text=None):
        """The items for one prompt. `piece` is a Core, or None for our own
        sentence `text`; `flourishes` the items character.flourishes chose;
        `coughs` {name: path} of the cough takes (empty: no cough)."""
        flourishes = list(flourishes or [])
        if piece is None:
            self.coughed = False
            return ([("tts", text, None)] if text else []) + flourishes
        has_d = band != "now" and piece.d_first is not None
        order = ("first" if has_d and self.rng.random() < P_MANEUVER_FIRST
                 else "classic")
        cough = None
        if (band != "now" and coughs and not self.coughed
                and (piece.halves or has_d)
                and self.rng.random() < COUGH_RATE):
            cough = "inside" if piece.halves else "join"
        self.coughed = cough is not None
        if has_d:
            shape = (order, cough, len(flourishes))
            if shape == self.shape:
                order = "classic" if order == "first" else "first"
                shape = (order, cough, len(flourishes))
            self.shape = shape
        said = None
        if cough:
            take, _found = self.recent.choose(self.rng, "cough", sorted(coughs))
            said = ("flourish", coughs[take], None)
        if cough == "inside":
            group = [piece.halves[0], said, piece.halves[1]]
        else:
            group = [piece.m]
        group += piece.tail
        join = [said] if cough == "join" else []
        if not has_d:
            items = group
        elif order == "classic":
            items = [piece.d_first] + join + group
        else:
            items = group + join + [piece.d_after]
        return items + flourishes


#: Slots whose clip carries a DIRECTION. Their wild lines come ONLY from files
#: SHE imported and named for the slot - never the built-in pool, because that
#: would be us guessing left from right (AGEDASHII's ruling; minion 2's Kotlin
#: is identical, and minion 5 ships no wild direction takes).
DIRECTION_SLOTS = frozenset({"turn-left", "turn-right", "slight-left", "slight-right",
                             "sharp-left", "sharp-right", "uturn",
                             "exit-left", "exit-right"})


class Wild:
    """Which recording says a slot - hers, his wild line, or the Waze one.

    Hers: "id rather him scream things that make kind of sense than the normal
    waze arnold talking. only use that shit as fall back". So per slot: HER
    imported clip; else one of the wild-<slot>-* lines at random, never the
    same one twice running - a slot holds back its last few (Recent); else
    the built-in (Waze) clip; else None, the phone's voice. With one wild line
    for a slot it alternates with the Waze one rather than repeating.
    "Character lines" off skips the wild step.

    ONLY A FILE NAMED FOR THIS SLOT is ever used for it (wild-<slot>-...),
    and for a DIRECTION slot only one SHE imported (AGEDASHII's ruling): we
    never pick a direction line from the built-in pool - that would be us
    guessing whether a clip is unmistakable about left or right - but a file
    she names wild-turn-left-go-left-if-you-want-to-live is her deliberate
    act, and "GO LEFT IF YOU WANT TO LIVE" were her first words on this
    feature.
    """

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.recent = Recent()

    def resolve(self, name, *, source, clip_path, wilds, character=True, band=None):
        """The file to play for `name`, or None. `source`, `clip_path` and
        `wilds` are voice_import's clip_source, clip_path and wilds.

        The arrive slot's wild lines wait for ARRIVING - its "now" prompt; its
        far and near prompts say the plain arrive line (AGEDASHII's ruling).
        """
        if (source(name) == "yours" or not character
                or (name == "arrive" and band != "now")):
            return clip_path(name)
        mine = f"wild-{name}-"
        pool = {n: path for n, path in (wilds(name) or {}).items()
                if path and n.startswith(mine)
                and (name not in DIRECTION_SLOTS or source(n) == "yours")}
        if not pool:
            return clip_path(name)
        # A lone phrase and the Waze line are a pool of two (None is the Waze
        # line), so they take turns, however many takes the phrase has.
        names = sorted(pool)
        if len({phrase(n) for n in names}) == 1 and clip_path(name):
            names.append(None)
        pick, _found = self.recent.choose(self.rng, name, names)
        return clip_path(name) if pick is None else pool[pick]


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
