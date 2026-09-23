"""The two preferences this app owns that the shared store does not.

``agedashii_washi.prefs`` already keeps units, the sodium target, the corner
radius, the glow, the CRT and the bottom bar, and every one of those is read
straight out of it. Two things are missing from it, and both belong here
rather than there:

**The theme.** Nothing in the app has ever persisted it. The dashboard picks a
theme, applies it, and forgets it - which is why "it resets every launch" is a
known complaint and not a mystery. ``agedashii_washi.prefs`` does not hold a
theme key at all, and this app must not add one to a package two other apps
share while they are being worked on. So Foodzen keeps its own, under its own
name, written to the same Flet SharedPreferences service everything else uses.
The name is namespaced ``agedashii.foodzen.*`` for exactly that reason: if a
shared key appears later, these two cannot collide with it.

**Whether the hamburger is drawn at all.** Her words: "Each of these smaller
apps work better with the bottom navigation bar and less with the hamburger
menu... i want the ability to show / hide the hamburger menu trigger as wel".
So it ships *off* - the bottom bar is this app's navigation - and Settings
turns it back on.

One rail, in ``hamburger_shown``: with the bottom bar switched off the menu
trigger comes back whether or not it is enabled, because an app with neither
has no navigation at all and no way to reach the screen that would fix it.

The same two rules as the shared module. **Reads are synchronous and never
fail** - Flet builds a page with sync calls, so a read that had to await
storage could not be used where it is needed; values live in a cache that
starts at the shipped defaults. **Stored values are never trusted** - a theme
that no longer exists reads as the default, and a boolean is compared against
the literal string "true" rather than passed to ``bool()``, because storage
hands a False back as the string "false" and ``bool("false")`` is True. That
exact footgun has already shipped once in this codebase.
"""
from agedashii_qi import DEFAULT, THEMES

from agedashii_washi import prefs as _shared

NS = "agedashii.foodzen."

KEY_THEME = NS + "theme"
KEY_HAMBURGER = NS + "hamburger"

# Bottom-nav-first means the bar is the primary way around, not that the
# drawer is hidden: "i want the ability to show / hide the hamburger menu
# trigger" asked for a switch, and a switch that starts off reads as a
# missing feature. Both are on out of the box; hiding it is hers to do.
DEFAULT_HAMBURGER = True


def _defaults():
    return {KEY_THEME: DEFAULT, KEY_HAMBURGER: DEFAULT_HAMBURGER}


_V = _defaults()

# False until load() has had a chance. Settings shows it in Diagnostics; the
# rest of the app has no reason to care.
LOADED = False


def _as_bool(value, fallback):
    """A stored flag as a real bool.

    Written as "true"/"false" by `save()` and compared against the literal
    here. SharedPreferences round-trips a bool as a string on some
    platforms, and `bool("false")` is True - every switch in the dashboard
    came back on after a restart because of it.
    """
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
        return fallback
    return bool(value)


# ---------------------------------------------------------------- read API
def theme():
    """The saved theme key, or the shipped default if it is not a real one."""
    key = _V.get(KEY_THEME)
    return key if isinstance(key, str) and key in THEMES else DEFAULT


def hamburger_enabled():
    """Whether she has asked for the menu trigger in the topbar."""
    return _as_bool(_V.get(KEY_HAMBURGER), DEFAULT_HAMBURGER)


def hamburger_shown():
    """Whether the topbar actually draws it.

    The rail: with the bottom bar off, the drawer is the only navigation
    there is, so the trigger appears regardless of the setting. Without
    this the two switches together produce an app with no way to reach any
    other surface - including Settings, which is where you would undo it.
    """
    return hamburger_enabled() or not _shared.bottom_bar_enabled()


# ---------------------------------------------------------------- write API
def set_theme(key):
    """Remember `key` as the theme. False if it is not one of the 24."""
    if not isinstance(key, str) or key not in THEMES:
        return False
    _V[KEY_THEME] = key
    return True


def set_hamburger_enabled(on):
    _V[KEY_HAMBURGER] = bool(on)
    return True


def reset():
    _V.clear()
    _V.update(_defaults())


# ---------------------------------------------------------------- persistence
async def load(prefs):
    """Fill the cache from storage. True if anything was read.

    `prefs` is a Flet SharedPreferences, or None in tests and on a device
    with no storage. Every read is guarded on its own: one key a platform
    refuses must not cost the other.
    """
    global LOADED
    LOADED = True
    if prefs is None:
        return False
    read_any = False
    for key in _defaults():
        try:
            value = await prefs.get(key)
        except Exception:      # noqa: BLE001 - storage is optional, not required
            value = None
        if value is None:
            continue
        read_any = True
        _V[key] = value
    # Normalise once, here, so no later read ever sees a stored shape - a
    # theme that was deleted between releases, or a bool that came back as
    # the string "false".
    _V[KEY_THEME] = theme()
    _V[KEY_HAMBURGER] = hamburger_enabled()
    return read_any


async def save(prefs):
    """Persist both keys. Returns False rather than raising.

    The bool goes out as "true"/"false" rather than as a Python bool: that
    is the shape `_as_bool` is written against, so what is read back is
    what was written on every platform, not only the ones that round-trip
    a real bool.
    """
    if prefs is None:
        return False
    try:
        await prefs.set(KEY_THEME, theme())
        await prefs.set(KEY_HAMBURGER, "true" if hamburger_enabled() else "false")
        return True
    except Exception:          # noqa: BLE001 - report it in the UI, do not crash
        return False
