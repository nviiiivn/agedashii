"""His character, played: the wild lines, the flourishes, the cough, the taps.

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


def clip(name, band=None):
    """The file that says `name`: her recording, else one of his wild lines,
    else the built-in (Waze) one, else None - the phone's voice. `band` is the
    prompt's, because arrive's wild lines wait for the "now" one."""
    return WILD.resolve(name, source=voice_import.clip_source,
                        clip_path=voice_import.clip_path,
                        wilds=voice_import.wilds,
                        character=prefs.flourishes(), band=band)


def mood(names, parts):
    """The flourish mood for this moment, with the angry line's minute bar."""
    return voicelines.flourish_mood(names, parts,
                                    angry_ok=FLOURISH.angry_ok(time.time()))


def flourish(mood, parts=None):
    """[("flourish", path, None)] or [] for this moment - see voicelines.Flourish."""
    if mood is None or not prefs.flourishes():
        return []
    parts = parts or {}
    maneuver = parts.get("index", (parts.get("kind"), parts.get("instruction"),
                                   parts.get("street")) if parts else None)
    pick = FLOURISH.pick(mood, voice_import.flourishes(mood), maneuver=maneuver,
                         now=time.time())
    return [("flourish", pick[1], None)] if pick else []


def cough(items, band=None):
    """`items` with his cough in the distance/turn join, now and then."""
    if not prefs.flourishes():
        return items
    return FLOURISH.cough(items, voice_import.interjections("interject-cough"), band=band)


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
