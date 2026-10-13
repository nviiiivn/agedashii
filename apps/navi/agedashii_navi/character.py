"""His character, played: the wild lines, the flourishes, the cough, the taps,
and how one prompt is put together from them.

Hers, across the evening: the wild lines "peppered in randomly and
accordingly"; "id rather him scream things that make kind of sense than the
normal waze arnold talking"; the cough in the middle of an instruction; the
sorry group; "STOP IT" on arrival; "WANT 2" on the search button.

The choices are pure and live in voicelines (Flourish, Wild). This module
holds the one piece of state they must SHARE - which line played last - so a
ride prompt and a tap on another page never repeat each other, and it is the
one door the pages go through. "Character lines" off (prefs.flourishes) turns
off the wild lines, the flourishes and the cough together, leaving the plain
voice; the taps have their own switch (prefs.flourish_taps).
"""
import time

from . import prefs, speech, voice_import, voicelines

#: Shared across the ride and every page.
FLOURISH = voicelines.Flourish()
WILD = voicelines.Wild()
ARRANGE = voicelines.Arranger()


def clip(name, band=None):
    """The file that says `name`: her recording, else one of his wild lines,
    else the built-in (Waze) one, else None - the phone's voice. `band` is the
    prompt's, because arrive's wild lines wait for the "now" one."""
    return WILD.resolve(name, source=voice_import.clip_source,
                        clip_path=voice_import.clip_path,
                        wilds=voice_import.wilds,
                        character=prefs.flourishes(), band=band)


def mood(names, parts, fired=()):
    """The flourish mood for this moment, with the angry line's minute bar."""
    return voicelines.flourish_mood(names, parts,
                                    angry_ok=FLOURISH.angry_ok(time.time()),
                                    fired=fired)


def _maneuver(parts):
    parts = parts or {}
    return parts.get("index", (parts.get("kind"), parts.get("instruction"),
                               parts.get("street")) if parts else None)


def flourish(mood, parts=None):
    """[("flourish", path, None)] or [] for this moment - see voicelines.Flourish."""
    if mood is None or not prefs.flourishes():
        return []
    pick = FLOURISH.pick(mood, voice_import.flourishes(mood),
                         maneuver=_maneuver(parts), now=time.time())
    return [("flourish", pick[1], None)] if pick else []


def farewell():
    """His goodbye - "hasta la vista, baby" - or [].

    Her rule, 2026-10-12: "HASTA LA VISTA BABY should only play when i quit or
    when i stop gps/recording". It used to be an ARRIVAL line, which said
    goodbye to someone who had just turned up. The clips are now a mood of
    their own, which voicelines.flourish_moods can never return, so the only
    way to hear one is to call this: stopping the GPS, stopping a recording,
    or quitting the app.
    """
    return flourish("farewell")


def flourishes(names, parts, fired=()):
    """His lines after a prompt: none, one, or two ("do it now" + "damn it").

    The first as it always was (voicelines.flourish_mood, its rate, one per
    maneuver); the second only after a first, from voicelines.second_moods
    (voicelines.Flourish.second). Never two on the "now" band.
    """
    if (parts or {}).get("then"):
        return []                   # "and then" is the whole of that prompt
    first = flourish(mood(names, parts, fired), parts)
    if not first:
        return []
    now = time.time()
    pools = {m: voice_import.flourishes(m) for m in voicelines.second_moods(
        names, parts, angry_ok=FLOURISH.angry_ok(now), fired=fired)}
    pick = FLOURISH.second(pools, FLOURISH.last, now=now)
    return first + ([("flourish", pick[1], None)] if pick else [])


def halves(slot, path):
    """The two halves of `slot`'s plain line, or None - voicelines.halves."""
    return voicelines.halves(slot, path, voice_import.clip_source,
                             voice_import.clip_path)


def arrange(piece, band, lines=(), text=None):
    """One prompt composed - voicelines.Arranger - with his cough when
    Character lines is on."""
    coughs = (voice_import.interjections("interject-cough")
              if prefs.flourishes() else {})
    return ARRANGE.arrange(piece, band, lines, coughs, text=text)


#: Whether this process has said its launch line.
_OPENED = {}


def opening():
    """Her launch line - "That's amazing, where are we?" - once per cold launch.

    A SLOT, not theatre (agreed with minion 2 and minion 5): her own open clip,
    else one of his wild-open-* lines when Character lines are on, else the
    built-in open.mp3; with none of those, silence - never the phone's voice
    greeting her. Announce turns off means silent, and it never talks over a
    prompt. Returns the items handed to speech, for tests.
    """
    if _OPENED.get("done") or not prefs.voice():
        return []
    _OPENED["done"] = True
    path = clip("open")
    if not path or speech.voice_active():
        return []
    items = [("clip", path, None)]
    speech.play(items)
    return items


def tap():
    """A character line for a tap - opening search, picking a destination.

    Never on the ride screen's own controls (record, options) and never over a
    prompt: the taps are theatre, and theatre waits for the road. Returns the
    items handed to speech, for tests.
    """
    if not prefs.voice() or not prefs.flourish_taps() or speech.voice_active():
        return []
    pick = FLOURISH.pick("ui", voice_import.flourishes("ui"))
    if not pick:
        return []
    items = [("flourish", pick[1], None)]
    speech.play(items)
    return items
