"""The two preferences Moto owns, and the shared store has no key for.

`agedashii_washi.prefs` already holds everything Moto has in common with
the dashboard - units, the corner radius, the glow, the CRT overlay and
its intensity, the bottom bar, the per-surface card layouts - and Moto
reads and writes all of those straight through it. Nothing here duplicates
any of that.

Two settings are not in there, and cannot be added from here: that module's
``save()`` writes exactly the keys ``_defaults()`` names, so a key this app
invented would be loaded from nowhere and written nowhere. Both live here
instead, alongside it in the same SharedPreferences and under the same
``agedashii.`` namespace.

**The theme.** The dashboard's picker changes the theme and never stores
it, so every launch opens on the shipped default again however many times
she has picked another one. Moto stores it under ``agedashii.theme`` - a
shared name, not a Moto-private one, because the theme is meant to be the
same in all three apps and the other two can adopt exactly this key when
their turn comes.

**The hamburger.** Moto-only by nature: "each of these smaller apps work
better with the bottom navigation bar and less with the hamburger menu...
i want the ability to show / hide the hamburger menu trigger as well".
The dashboard carries nine pages and needs its drawer; Moto carries four
surfaces that fit on a bar, so its drawer is an option rather than the way
in.

Two rules, both inherited from `prefs` because they are the same two
hazards:

*Reads are synchronous and never fail.* Flet builds a page with ordinary
sync calls. Values live in a module-level cache that starts at the shipped
defaults; `load()` fills it in once and every read is served from memory.

*Stored values are never trusted.* SharedPreferences only round-trips
str/int/float/bool/list[str], and it is not consistent about booleans - one
written as ``False`` can come back as the string ``"false"``, and
``bool("false")`` is ``True``, which is how every switch in this app's
ancestor came back on after a restart. So flags are written as the literal
strings ``"true"``/``"false"`` and read back by explicit comparison, never
by truthiness. A theme key that no longer exists reads as "no choice
stored" rather than as a KeyError on the first paint.
"""
from agedashii_qi.themes import DEFAULT, THEMES
from agedashii_washi import prefs

#: The same namespace `prefs` uses, so everything this app stores sits
#: together and a future `nvii.*` migration finds it by the same prefix rule.
NS = prefs.NS

#: Which of the 24 themes to open on. Deliberately NOT namespaced to Moto:
#: the theme is a whole-brand preference and the other two apps should read
#: this exact key rather than inventing one each.
KEY_THEME = NS + "theme"

#: Whether the topbar draws the drawer trigger at all. Moto's own, because
#: the drawer is Moto's option and the dashboard's necessity.
KEY_HAMBURGER = NS + "moto.hamburger"


def _defaults():
    return {
        # "" rather than DEFAULT: an empty string is "she has never picked
        # one", which is a different fact from "she picked the one that
        # happens to be shipped", and only the first should follow a change
        # of shipped default.
        KEY_THEME: "",
        # On. The bottom bar is the primary navigation now and reaches every
        # surface on its own, so this could ship off - but hiding a control
        # she has been using for months without being asked is a surprise,
        # and what she asked for was the *ability* to hide it. Off is one tap
        # away in Settings, which rides the bar and so is reachable either way.
        KEY_HAMBURGER: True,
    }


_V = _defaults()

#: False until `load()` has been given a chance. Settings shows it.
LOADED = False


# ---------------------------------------------------------------- coercion
#: Everything a storage backend might hand back meaning False. Read by
#: explicit membership, never by `bool()`: `bool("false")` is True.
_TRUE = ("true", "1", "yes", "on")
_FALSE = ("false", "0", "no", "off", "")


def _as_bool(value, fallback):
    """Coerce a stored flag to a real bool. Absent or junk means `fallback`."""
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return fallback


def _flag(value):
    """The string form a flag is stored as - unambiguous on the way back."""
    return "true" if value else "false"


# ---------------------------------------------------------------- read API
def stored_theme():
    """The theme key she has picked, or "" if she never has.

    A key naming a theme that no longer exists reads as "" too: a palette
    that was renamed out of `THEMES` should cost the preference, not the
    first paint.
    """
    key = _V.get(KEY_THEME)
    return key if isinstance(key, str) and key in THEMES else ""


def theme():
    """The theme to open on - hers if she has one, the shipped one if not."""
    return stored_theme() or DEFAULT


def hamburger_visible():
    """Her stored choice about the drawer trigger, on its own.

    Not what the topbar draws - see `hamburger_shown()` for that.
    """
    return _as_bool(_V.get(KEY_HAMBURGER), True)


def hamburger_shown():
    """Whether the topbar actually draws the drawer trigger.

    The read-side rail, and the same one `prefs.hidden_pages()` puts around
    a protected page: hiding the hamburger while the bottom bar is also off
    would leave an app with no navigation at all and no way back to the
    Settings that did it. A value written by an older build, or hand-edited
    in storage, never went through the setter, so the guarantee is made here
    as well as there.
    """
    return hamburger_visible() or not prefs.bottom_bar_enabled()


def can_hide_hamburger():
    """Whether hiding it is allowed right now. False while the bar is off."""
    return prefs.bottom_bar_enabled()


def can_hide_bottom_bar():
    """Whether the bar may be switched off. False while the hamburger is."""
    return hamburger_visible()


# ---------------------------------------------------------------- write API
def set_theme(key):
    """Remember `key` as the theme to open on. False if it is not a theme."""
    if not (isinstance(key, str) and key in THEMES):
        return False
    _V[KEY_THEME] = key
    return True


def set_hamburger_visible(on):
    """Show or hide the drawer trigger. False if that would strand the app.

    Refused rather than silently ignored, so Settings can say why instead
    of drawing a switch that flips back on its own.
    """
    on = bool(on)
    if not on and not can_hide_hamburger():
        return False
    _V[KEY_HAMBURGER] = on
    return True


def reset():
    """Back to the shipped values. Used by the tests, and by nothing else."""
    _V.clear()
    _V.update(_defaults())


# -------------------------------------------------------------- persistence
async def _read(store, key):
    """One guarded read. None for absent, unreadable, or no such key."""
    if not key:
        return None
    try:
        return await store.get(key)
    except Exception:  # noqa: BLE001 - storage is optional, not required
        return None


async def load(store):
    """Fill the cache from storage. True if anything was actually read.

    `store` is a flet SharedPreferences, or None in a test and before the
    service is registered. Every read is guarded on its own: one key a
    platform refuses must not cost the other.

    A key absent under its current name is looked up again under the
    pre-rename `nvii.*` name, the same way `prefs.load` does it, so an
    install that has been running since before the rename keeps its choice.
    """
    global LOADED
    LOADED = True
    if store is None:
        return False
    read_any = False
    for key in _defaults():
        value = await _read(store, key)
        if value is None:
            value = await _read(store, prefs.legacy_key(key))
        if value is None:
            continue
        read_any = True
        _V[key] = value
    # Normalise once, here, so no later read ever sees a stored shape - the
    # string "false", a theme that was deleted - and every read afterwards
    # is a plain lookup.
    _V[KEY_THEME] = stored_theme()
    _V[KEY_HAMBURGER] = hamburger_visible()
    return read_any


def save_payload():
    """Every key that goes to storage, with the value to write for it.

    Derived from `_defaults()` rather than listed by hand: a hand-written
    list is exactly how the shared store's bottom-bar switch came to be
    loaded but never saved, working all session and back on at every
    restart with nothing failing loudly enough to notice.
    """
    payload = {KEY_HAMBURGER: _flag(hamburger_visible())}
    for key in _defaults():
        payload.setdefault(key, _V.get(key, _defaults()[key]))
    payload[KEY_THEME] = stored_theme()
    return payload


async def save(store):
    """Persist both preferences. Returns False rather than raising."""
    if store is None:
        return False
    try:
        for key, value in save_payload().items():
            await store.set(key, value)
        return True
    except Exception:  # noqa: BLE001 - report it in the UI, do not crash
        return False
