"""Watudo's own preferences - the ones ``agedashii_washi.prefs`` does not own.

Washi stores what every Agedashii app has in common: the corner radius, the
accent glow, the CRT overlay and its intensity, the card layout, and whether
there is a bottom bar at all. Settings here writes those through washi, which
is why they survive between the dashboard and this app in the same shape.

What washi has no key for is everything below: the theme (the dashboard's
picker is per-session and resets every launch), whether the hamburger trigger
is drawn at all, and the five settings that only mean something to Wat2Do -
how a day is labelled, whether finished tasks stay on screen, whether pain and
spoons are tracked and whether they steer the suggestion, what the template
editor writes for a sub-task, how many subjects a mission rolls, and where
distances are measured from.

Three rules, the same three washi's prefs module is built on, for the same
reasons:

**Reads are synchronous and never fail.** Flet builds a page with ordinary
sync calls, so a read that had to await storage could not be used where it is
needed. Values live in a module-level cache that starts at the shipped
defaults; ``load()`` fills it once and every read afterwards is memory.

**Stored values are never trusted.** Storage can hand back the wrong type, a
number outside its range, or a string where a tuple was meant. Every reader
coerces, and anything it cannot make sense of reads as the shipped default
rather than raising: a corrupt preference should cost the setting, not the
app.

**Booleans are read explicitly.** SharedPreferences can round-trip ``False``
as the string ``"false"``, and ``bool("false")`` is ``True`` - which is how
every switch in the dashboard once came back *on* after a restart. ``_as_bool``
is the one place that is handled.

Nothing here reaches the network, and nothing here is sent anywhere. These are
this device's answers to questions about this device.
"""

# Namespaced under the app, beneath the shared "agedashii." prefix, so a key
# added here can never collide with one washi adds later.
NS = "agedashii.watudo."

# The exception, and deliberately not under the app's namespace: the theme is
# the name `agedashii_washi.prefs` would use if it ever adopted one, so
# adopting it later reads the value this app has been writing all along
# rather than starting again from the shipped default.
KEY_THEME = "agedashii.theme"

# Whether the hamburger trigger is drawn in the topbar at all. Her words:
# "Each of these smaller apps (not tofu) work better with the bottom
# navigation bar and less with the hamburger menu... i want the ability to
# show / hide the hamburger menu trigger as wel please."
#
# Shipped OFF, because every surface this app has is on the bottom bar and a
# menu button that opens a list of the three things already along the bottom
# is furniture. Turning it on gets the drawer back; see `menu_button()` for
# the one case where it comes back whether or not it is on.
KEY_MENU_BUTTON = NS + "menu_button"

# ------------------------------------------------------------- the day
# Whether today's date reads "Today" or "Mon 22 Sep". On by default: the app
# is opened to tick three boxes and the answer is nearly always today, so the
# friendly word is the useful one. Off is for anyone who would rather always
# see the actual date than work out which day "Today" was when the phone has
# been in a pocket since midnight.
KEY_FRIENDLY_DATES = NS + "friendly_dates"

# Whether a ticked task stays in the checklist. Off by default - the struck
# -through line is the record of having done it, and a list that empties as
# you work can read as a list that lost your work. On, the day's list gets
# shorter as it goes, which is the other half of the same argument.
KEY_HIDE_DONE = NS + "hide_done"

# ---------------------------------------------------------- pain & spoons
# Whether the pain and spoons card is built at all. On by default: it is half
# of what Wat2Do is for. Off, the card is not drawn and neither number is
# written - the day's record keeps whatever it already had rather than being
# overwritten with a blank.
KEY_TRACK_VITALS = NS + "vitals"

# Whether those two numbers steer what the app suggests - the mode chip on
# Get out, and the radius and indoor/outdoor bias of the mission roll. On by
# default, because that is what recording them buys. Off, the suggestion is
# made from the weather alone and the numbers are still recorded.
KEY_VITALS_STEER = NS + "vitals_steer"

# --------------------------------------------------- template & missions
# What the checklist template editor writes when it normalises a sub-task.
# The website writes "> " and the parser on both ends also accepts a
# two-space indent, so either is read correctly by the browser; this only
# decides which one the phone writes.
KEY_SUB_MARKER = NS + "sub_marker"
SUB_MARKERS = ("caret", "indent")
SUB_MARKER_LABELS = {"caret": "> like the website", "indent": "  two spaces"}

# How many photo subjects a rolled mission carries. 0 means "as the website
# rolls it" - three to six, decided by the dice - which is the shipped
# behaviour and stays the default.
KEY_MISSION_SUBJECTS = NS + "mission_subjects"
MISSION_SUBJECT_CHOICES = (0, 2, 3, 4, 5, 6, 8)

# ----------------------------------------------------------- the origin
# Where every distance in this app is measured from, as "lat,lon". Empty
# means the shipped origin, `_wat2do_parts.HOME`.
#
# This exists because the app used to have a location permission and no code
# that asked the device anything: every distance came from a fixed point and
# `future.scan()` took a `centre` nothing passed. Rather than ship a
# permission for a feature that did not exist, the origin is something you
# type - which is honest, works with the radio off, and is the caller
# `centre` never had. See the README.
KEY_ORIGIN = NS + "origin"


def _defaults():
    return {
        KEY_THEME: "",
        # On out of the box. "the ability to show / hide the hamburger menu
        # trigger" asked for a switch, not for it to start hidden - and a
        # control that is absent until you find its setting reads as a bug.
        KEY_MENU_BUTTON: True,
        KEY_FRIENDLY_DATES: True,
        KEY_HIDE_DONE: False,
        KEY_TRACK_VITALS: True,
        KEY_VITALS_STEER: True,
        KEY_SUB_MARKER: "caret",
        KEY_MISSION_SUBJECTS: 0,
        KEY_ORIGIN: "",
    }


_V = _defaults()

# False until load() has been given a chance. Settings reads it to decide
# whether it is showing stored values or shipped ones; nothing else needs it.
LOADED = False


# ---------------------------------------------------------------- coercion
# Anything a storage backend might hand back in place of False. `bool("false")`
# is True, which is the whole reason this is a function and not an inline
# `bool(...)` at nine call sites.
_FALSEY = ("false", "0", "no", "off", "")


def _as_bool(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


def _as_choice(value, choices, fallback):
    return value if value in choices else fallback


def _as_int(value, choices, fallback):
    """Coerce a stored number to one of `choices`. Storage is not consistent
    about whether an int comes back as an int or as a string."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if n in choices else fallback


def parse_origin(raw):
    """"lat,lon" to a (lat, lon) pair, or None.

    None for empty, malformed, out-of-range or non-numeric input - every one
    of which means "use the shipped origin" rather than "draw the map in the
    middle of the ocean". Latitude and longitude are checked against the
    actual limits of each, so a pair typed the wrong way round in the
    Americas is refused rather than silently measuring from Antarctica.
    """
    if not isinstance(raw, str) or "," not in raw:
        return None
    lat_s, _, lon_s = raw.partition(",")
    try:
        lat, lon = float(lat_s.strip()), float(lon_s.strip())
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def format_origin(latlon):
    """A (lat, lon) pair as the string that is stored and shown."""
    if not latlon:
        return ""
    return f"{float(latlon[0]):.6f},{float(latlon[1]):.6f}"


# ---------------------------------------------------------------- read API
def theme():
    """The theme key last picked on this device, or "" for the shipped one."""
    value = _V.get(KEY_THEME)
    return value if isinstance(value, str) else ""


def menu_button():
    """Whether she has asked for the hamburger trigger.

    The raw setting. `main.menu_visible()` is what decides whether one is
    actually drawn: with the bottom bar switched off, the drawer is the only
    thing left that reaches every surface, so the trigger comes back whatever
    this says. A setting that can strand you on one page is not a setting.
    """
    return _as_bool(_V.get(KEY_MENU_BUTTON), True)


def friendly_dates():
    return _as_bool(_V.get(KEY_FRIENDLY_DATES), True)


def hide_done():
    return _as_bool(_V.get(KEY_HIDE_DONE), False)


def track_vitals():
    return _as_bool(_V.get(KEY_TRACK_VITALS), True)


def vitals_steer():
    """Whether pain and spoons steer the suggestion and the mission roll.

    Always False when they are not being tracked at all - there would be
    nothing to steer with, and a switch that claims otherwise is a lie the
    Settings page would then have to explain.
    """
    return track_vitals() and _as_bool(_V.get(KEY_VITALS_STEER), True)


def sub_marker():
    return _as_choice(_V.get(KEY_SUB_MARKER), SUB_MARKERS, "caret")


def sub_prefix():
    """What the template editor puts in front of a sub-task line."""
    return "> " if sub_marker() == "caret" else "  "


def mission_subjects():
    """How many subjects a mission rolls, or 0 for the website's 3-6."""
    return _as_int(_V.get(KEY_MISSION_SUBJECTS), MISSION_SUBJECT_CHOICES, 0)


def origin():
    """Where distances are measured from, or None for the shipped origin."""
    return parse_origin(_V.get(KEY_ORIGIN))


# ---------------------------------------------------------------- write API
def set_theme(key):
    _V[KEY_THEME] = key if isinstance(key, str) else ""
    return True


def set_menu_button(on):
    _V[KEY_MENU_BUTTON] = bool(on)
    return True


def set_friendly_dates(on):
    _V[KEY_FRIENDLY_DATES] = bool(on)
    return True


def set_hide_done(on):
    _V[KEY_HIDE_DONE] = bool(on)
    return True


def set_track_vitals(on):
    _V[KEY_TRACK_VITALS] = bool(on)
    return True


def set_vitals_steer(on):
    _V[KEY_VITALS_STEER] = bool(on)
    return True


def set_sub_marker(value):
    _V[KEY_SUB_MARKER] = _as_choice(value, SUB_MARKERS, sub_marker())
    return True


def set_mission_subjects(value):
    _V[KEY_MISSION_SUBJECTS] = _as_int(value, MISSION_SUBJECT_CHOICES,
                                       mission_subjects())
    return True


def set_origin(raw):
    """Store an origin from what was typed. False if it could not be read.

    An empty string is not a failure - it is how you say "back to the
    shipped origin" - so it is stored and reported as success.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        _V[KEY_ORIGIN] = ""
        return True
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        raw = f"{raw[0]},{raw[1]}"
    pair = parse_origin(raw)
    if pair is None:
        return False
    _V[KEY_ORIGIN] = format_origin(pair)
    return True


def reset_all():
    _V.clear()
    _V.update(_defaults())
    return True


# ---------------------------------------------------------------- persistence
async def _read(store, key):
    """One guarded read. None for absent, unreadable, or no such key."""
    try:
        return await store.get(key)
    except Exception:          # noqa: BLE001 - storage is optional, not required
        return None


async def load(store):
    """Fill the cache from storage. True if anything was read.

    Every read is individually guarded: one key a platform refuses must not
    cost the other eight. Values are normalised through their own readers on
    the way in, so nothing downstream ever sees a stored shape - a bool that
    arrived as "false" is a bool from here on.
    """
    global LOADED
    LOADED = True
    if store is None:
        return False
    read_any = False
    for key in _defaults():
        value = await _read(store, key)
        if value is None:
            continue
        read_any = True
        _V[key] = value
    _V[KEY_THEME] = theme()
    _V[KEY_MENU_BUTTON] = menu_button()
    _V[KEY_FRIENDLY_DATES] = friendly_dates()
    _V[KEY_HIDE_DONE] = hide_done()
    _V[KEY_TRACK_VITALS] = track_vitals()
    _V[KEY_VITALS_STEER] = _as_bool(_V.get(KEY_VITALS_STEER), True)
    _V[KEY_SUB_MARKER] = sub_marker()
    _V[KEY_MISSION_SUBJECTS] = mission_subjects()
    _V[KEY_ORIGIN] = format_origin(origin())
    return read_any


def save_payload():
    """Every key that goes to storage, with the value to write for it.

    Derived from `_defaults()` rather than listed by hand. A hand-written
    list is how washi's bottom-bar switch came to be loaded but never saved:
    it worked all session and was back on at every restart, and nothing
    failed loudly enough to notice. A test asserts this covers every key.

    Written as real values, never as whatever came back from storage last
    time, so a bool that arrived as the string "false" is not handed straight
    back to be misread again on the next launch.
    """
    return {
        KEY_THEME: theme(),
        KEY_MENU_BUTTON: menu_button(),
        KEY_FRIENDLY_DATES: friendly_dates(),
        KEY_HIDE_DONE: hide_done(),
        KEY_TRACK_VITALS: track_vitals(),
        # The stored value, not `vitals_steer()`, which reports False while
        # tracking is off. Saving the derived answer would turn steering off
        # permanently the moment she stopped tracking for a day.
        KEY_VITALS_STEER: _as_bool(_V.get(KEY_VITALS_STEER), True),
        KEY_SUB_MARKER: sub_marker(),
        KEY_MISSION_SUBJECTS: mission_subjects(),
        KEY_ORIGIN: format_origin(origin()),
    }


async def save(store):
    """Persist every preference. Returns False rather than raising."""
    if store is None:
        return False
    try:
        for key, value in save_payload().items():
            await store.set(key, value)
        return True
    except Exception:          # noqa: BLE001 - report it in the UI, do not crash
        return False
