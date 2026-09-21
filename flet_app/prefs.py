"""Owner-editable app preferences: layout, panels, units, targets.

The website has a GridStack editor; the app had a theme picker. This module
is the app's answer - the stored side of it. Settings writes here, and
everything else *reads* here through the small sync API at the top
(`visible_pages`, `bottom_bar_pages`, `home_panels`, `unit_system`, ...).

Two rules shape the design:

**Reads are synchronous and never fail.** Flet builds a page with ordinary
sync calls, so a read that had to await storage could not be used where it is
needed. Values therefore live in a module-level cache that starts at the
shipped defaults; `load()` fills it in once, and every read is served from
memory afterwards. Before `load()` runs - and on any device where storage is
unavailable - reads return the defaults instead of raising. This mirrors
`config.py`, which guards its load/save the same way and for the same reason.

**Stored values are never trusted.** A saved order can name a page that no
longer exists, miss one that was added since, or arrive as the wrong type
entirely. Every list is run through `sanitise_order()` on the way in, so a
stale or corrupt preference degrades to "the default order" rather than to a
navigation bar with a missing tab or a KeyError at startup.

SharedPreferences only round-trips str/int/float/bool/list[str], so hidden
sets and orders are stored as lists of strings, not as dicts or sets.
"""
import re

from . import config

# ---------------------------------------------------------------- keys
# All namespaced "agedashii.*", alongside config.KEY_URL / KEY_CANDIDATES.
#
# The app shipped as NVII-DASH and stored everything under "nvii.*". Renaming
# the product renamed the namespace with it, which on its own would have read
# to an existing install as a factory reset: nothing under the new names, so
# every value falls back to the shipped default and the saved layout, units
# and sodium target are gone. `load()` therefore falls back to the pre-rename
# name whenever the new one is absent - see `legacy_key()`.
NS = "agedashii."
LEGACY_NS = "nvii."

KEY_PAGE_ORDER = NS + "page_order"
KEY_PAGES_HIDDEN = NS + "pages_hidden"
KEY_BOTTOM_PAGES = NS + "bottom_pages"
KEY_HOME_ORDER = NS + "home_order"
KEY_HOME_HIDDEN = NS + "home_hidden"
KEY_UNITS = NS + "units"
KEY_SODIUM_TARGET = NS + "sodium_target"
KEY_REFRESH_SECS = NS + "refresh_secs"
# The bottom bar duplicates the drawer, which lists every page. Switching it
# off gives the height back to the content; the drawer still reaches
# everything, so nothing becomes unreachable.
KEY_BOTTOM_ENABLED = NS + "bottom_enabled"
# Trip-report tags she invented herself, on top of the 27 the app ships.
# The website keeps its equivalents in localStorage, so the two lists are
# per-device by design; what crosses over is the *label* written into a
# trip's report, which both ends already understand.
KEY_TRIP_TAGS = NS + "trip_tags"


def legacy_key(key):
    """The pre-rename name of `key`, or None if it never had one.

    Every stored key is "agedashii.<rest>" and was "nvii.<rest>" before the
    product was renamed, panel keys included. Mapping by prefix rather than
    by a hand-written table means a key added later migrates for free.
    """
    return LEGACY_NS + key[len(NS):] if key.startswith(NS) else None

# ---------------------------------------------------------------- catalogue
# The nine pages, in shipped order. Deliberately a copy of main.PAGES rather
# than an import: main.py calls ft.run() at import time, so importing it from
# a library module would start the app.
PAGE_KEYS = ["home", "lab", "tools", "garage", "wat2do",
             "trips", "weather", "extras", "settings"]

PAGE_LABELS = {"home": "Home", "lab": "Lab", "tools": "Tools", "garage": "Garage",
               "wat2do": "Wat2Do", "trips": "Trips", "weather": "Weather",
               "extras": "Extras", "settings": "Settings"}

# Settings cannot be hidden: it is the only way back from a layout you regret.
PROTECTED_PAGES = {"settings"}

DEFAULT_BOTTOM = ["home", "lab", "tools", "garage", "wat2do"]

# A NavigationBar also carries a "More" destination, so five is the most that
# fits on a phone before the labels start eliding.
MAX_BOTTOM = 5

# The panels pages/home.py stacks, in the order it stacks them.
HOME_PANEL_KEYS = ["greeting", "weather", "nowplaying", "machines", "actions", "wat2do"]

HOME_PANEL_LABELS = {"greeting": "Greeting & date", "weather": "Weather",
                     "nowplaying": "Now playing", "machines": "Machines",
                     "actions": "Quick actions", "wat2do": "Wat2Do progress"}

UNIT_SYSTEMS = ("imperial", "metric")
SODIUM_TARGETS = (1500, 2300)

# Seconds of cached-response reuse. 30 is what api.TTL ships with; 0 means
# "only when I pull to refresh".
REFRESH_CHOICES = (0, 15, 30, 60, 300, 900)
DEFAULT_REFRESH = 30


def _defaults():
    return {
        KEY_PAGE_ORDER: list(PAGE_KEYS),
        KEY_PAGES_HIDDEN: [],
        KEY_BOTTOM_PAGES: list(DEFAULT_BOTTOM),
        KEY_HOME_ORDER: list(HOME_PANEL_KEYS),
        KEY_HOME_HIDDEN: [],
        KEY_UNITS: "imperial",
        KEY_SODIUM_TARGET: 2300,
        KEY_REFRESH_SECS: DEFAULT_REFRESH,
        KEY_BOTTOM_ENABLED: True,
        KEY_TRIP_TAGS: [],
    }


_V = _defaults()

# False until load() has been given a chance. Settings uses it to decide
# whether to hydrate itself; nothing else should need it.
LOADED = False


# ---------------------------------------------------------------- pure logic
def sanitise_order(saved, canonical):
    """Return `canonical`'s members ordered by `saved`, unknowns dropped.

    The two failure modes this exists to absorb: a preference saved by an
    older build that names a page since removed, and one saved before a page
    was added. Unknown names are dropped, missing ones are appended in their
    canonical position, and a saved value that is not a list of strings is
    ignored entirely.
    """
    order = []
    if isinstance(saved, (list, tuple)):
        for k in saved:
            if isinstance(k, str) and k in canonical and k not in order:
                order.append(k)
    order += [k for k in canonical if k not in order]
    return order


def sanitise_set(saved, canonical):
    """Return the members of `saved` that are real, in canonical order."""
    chosen = set()
    if isinstance(saved, (list, tuple)):
        chosen = {k for k in saved if isinstance(k, str) and k in canonical}
    return [k for k in canonical if k in chosen]


def move(seq, key, delta):
    """`seq` with `key` shifted `delta` places. Out-of-range moves are no-ops."""
    out = list(seq)
    if key not in out:
        return out
    i = out.index(key)
    j = i + delta
    if not (0 <= j < len(out)):
        return out
    out.insert(j, out.pop(i))
    return out


def _as_int(value, choices, fallback):
    """Coerce a stored number to one of `choices`.

    Storage backends are not consistent about whether an int comes back as an
    int or as a string, so both are accepted; anything else falls back.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if n in choices else fallback


# ---------------------------------------------------------------- read API
def page_order():
    """All nine page keys in the owner's order, hidden ones included."""
    return sanitise_order(_V.get(KEY_PAGE_ORDER), PAGE_KEYS)


def hidden_pages():
    """Page keys the owner has switched off. Never includes Settings."""
    return [k for k in sanitise_set(_V.get(KEY_PAGES_HIDDEN), PAGE_KEYS)
            if k not in PROTECTED_PAGES]


def visible_pages():
    """Pages to offer at all, in order. This is what the drawer should list."""
    hidden = set(hidden_pages())
    return [k for k in page_order() if k not in hidden]


def bottom_bar_pages():
    """Pages for the bottom navigation bar, in order, at most MAX_BOTTOM.

    Hidden pages are filtered out, so switching a page off removes it from the
    bar without a second edit. Never returns an empty list: a bar of nothing
    but "More" is a dead end, so the first visible page stands in.
    """
    visible = visible_pages()
    chosen = set(sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS))
    bar = [k for k in visible if k in chosen][:MAX_BOTTOM]
    if not bar and visible:
        bar = visible[:1]
    return bar


def bottom_bar_enabled():
    """Whether to draw the bottom bar at all.

    It repeats what the drawer already lists, so it is worth being able to
    turn off outright rather than only choosing what rides on it.
    """
    v = _V.get(KEY_BOTTOM_ENABLED)
    if v is None:
        return True                       # never set: ship it on
    if isinstance(v, str):
        # SharedPreferences is not consistent about booleans and can hand
        # back "true"/"false". bool("false") is True, so a round trip
        # through storage would have made "off" read as "on".
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(v)


def set_bottom_bar_enabled(on):
    _V[KEY_BOTTOM_ENABLED] = bool(on)
    return True


def drawer_pages():
    """Everything reachable from the drawer - the same set as visible_pages."""
    return visible_pages()


# ------------------------------------------------------- custom trip tags
# Stored as "label|severity|category" strings, because SharedPreferences
# round-trips list[str] and nothing richer. A row that does not parse is
# dropped rather than raising: a corrupt preference should cost one tag,
# not the Produce editor.

TAG_SEVERITIES = ("info", "good", "warn", "danger")
TAG_CATS = ("attribute", "warning", "bolo")


def trip_tags():
    """[(label, severity, category), ...] - her own tags, never the presets."""
    out = []
    seen = set()
    for row in (_V.get(KEY_TRIP_TAGS) or []):
        if not isinstance(row, str) or "|" not in row:
            continue
        label, _, rest = row.partition("|")
        severity, _, cat = rest.partition("|")
        label = label.strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append((label,
                    severity if severity in TAG_SEVERITIES else "info",
                    cat if cat in TAG_CATS else "attribute"))
    return out


def add_trip_tag(label, severity="info", cat="attribute"):
    label = (label or "").strip().lower()
    if not label:
        return False
    if any(t[0] == label for t in trip_tags()):
        return False
    severity = severity if severity in TAG_SEVERITIES else "info"
    cat = cat if cat in TAG_CATS else "attribute"
    _V[KEY_TRIP_TAGS] = [f"{a}|{b}|{c}" for a, b, c in trip_tags()] + \
                        [f"{label}|{severity}|{cat}"]
    return True


def remove_trip_tag(label):
    label = (label or "").strip().lower()
    kept = [f"{a}|{b}|{c}" for a, b, c in trip_tags() if a != label]
    changed = len(kept) != len(trip_tags())
    _V[KEY_TRIP_TAGS] = kept
    return changed


def home_panel_order():
    """Every home panel key in the owner's order, hidden ones included."""
    return sanitise_order(_V.get(KEY_HOME_ORDER), HOME_PANEL_KEYS)


def hidden_home_panels():
    return sanitise_set(_V.get(KEY_HOME_HIDDEN), HOME_PANEL_KEYS)


def home_panels():
    """Home panels to render, in order. Hidden ones are already removed."""
    hidden = set(hidden_home_panels())
    return [k for k in home_panel_order() if k not in hidden]


# ---------------------------------------------------- per-surface panels
# Home's six panels were the only thing in the app that could be reordered
# or hidden, and nothing anywhere had a width. These generalise both to
# every card stack. Keys are built per surface rather than kept in one dict
# because SharedPreferences only round-trips list[str]: a nested mapping
# would have to be encoded, and three flat lists are easier to reason about
# when one comes back corrupt.

def _pkey(surface, part):
    return f"{NS}panels.{surface}.{part}"


def _panel_keys_for_storage():
    """Every per-surface panel key, for load() and save() to walk.

    Derived from panels.SURFACES rather than listed by hand, so a surface
    added by a page is persisted without anyone remembering to come here.
    """
    from . import panels as _panels
    return [_pkey(s, part) for s in _panels.SURFACES
            for part in ("order", "hidden", "width")]


def _surface_keys(surface):
    from . import panels as _panels
    return _panels.panel_keys(surface)


def panel_order(surface):
    """Every panel key for `surface` in the owner's order, hidden included."""
    return sanitise_order(_V.get(_pkey(surface, "order")), _surface_keys(surface))


def hidden_panels(surface):
    return sanitise_set(_V.get(_pkey(surface, "hidden")), _surface_keys(surface))


def panels(surface):
    """Panels to render for `surface`, in order, hidden ones removed."""
    hidden = set(hidden_panels(surface))
    return [k for k in panel_order(surface) if k not in hidden]


def panel_width(surface, key):
    """'full', 'half' or 'third'. Stored as "<panel>:<width>" strings.

    An unrecognised width - from an older build, or a corrupt value - reads
    as full rather than raising: the cost of being wrong here is a card
    that is too wide, and the cost of raising is no page at all.
    """
    from . import panels as _panels
    raw = _V.get(_pkey(surface, "width"))
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, str) or ":" not in entry:
                continue
            k, _, w = entry.partition(":")
            if k == key and w in _panels.WIDTHS:
                return w
    return _panels.DEFAULT_WIDTH


def set_panel_width(surface, key, width):
    from . import panels as _panels
    if key not in _surface_keys(surface) or width not in _panels.WIDTHS:
        return False
    kept = [e for e in (_V.get(_pkey(surface, "width")) or [])
            if isinstance(e, str) and e.partition(":")[0] != key]
    if width != _panels.DEFAULT_WIDTH:
        kept.append(f"{key}:{width}")
    _V[_pkey(surface, "width")] = kept
    return True


def move_panel(surface, key, delta):
    _V[_pkey(surface, "order")] = move(panel_order(surface), key, delta)
    return True


def set_panel_hidden(surface, key, hidden):
    keys = _surface_keys(surface)
    if key not in keys:
        return False
    current = set(hidden_panels(surface))
    if hidden:
        current.add(key)
    else:
        current.discard(key)
    _V[_pkey(surface, "hidden")] = [k for k in keys if k in current]
    return True


def reset_panels(surface):
    """Back to the shipped order, everything visible, everything full width."""
    for part in ("order", "hidden", "width"):
        _V.pop(_pkey(surface, part), None)
    return True


def unit_system():
    """'imperial' (mi, °F) or 'metric' (km, °C)."""
    v = _V.get(KEY_UNITS)
    return v if v in UNIT_SYSTEMS else "imperial"


def distance_unit():
    return "mi" if unit_system() == "imperial" else "km"


def temperature_unit():
    return "F" if unit_system() == "imperial" else "C"


def sodium_target():
    """The DASH sodium ceiling EatZen should start from, in mg (1500 or 2300)."""
    return _as_int(_V.get(KEY_SODIUM_TARGET), SODIUM_TARGETS, 2300)


def refresh_seconds():
    """How long a cached server response stays good. 0 means manual only."""
    return _as_int(_V.get(KEY_REFRESH_SECS), REFRESH_CHOICES, DEFAULT_REFRESH)


def app_version():
    """This client's version, read off the User-Agent it sends.

    Taken from config.USER_AGENT rather than declared again here, so
    Diagnostics can only ever show the version the server actually sees.
    """
    m = re.match(r"agedashii/(\S+)", getattr(config, "USER_AGENT", "") or "")
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------- write API
def set_page_order(order):
    _V[KEY_PAGE_ORDER] = sanitise_order(order, PAGE_KEYS)


def move_page(key, delta):
    set_page_order(move(page_order(), key, delta))


def set_page_hidden(key, hidden):
    """Show or hide a page. Hiding a protected page is silently refused."""
    if key not in PAGE_KEYS or (hidden and key in PROTECTED_PAGES):
        return
    current = set(hidden_pages())
    if hidden:
        current.add(key)
    else:
        current.discard(key)
    _V[KEY_PAGES_HIDDEN] = [k for k in PAGE_KEYS if k in current]


def set_in_bottom_bar(key, wanted):
    """Add or remove a page from the bottom bar.

    Adding past MAX_BOTTOM is refused rather than silently truncating a page
    the owner already put there; the caller reports it.  Returns True if the
    set changed.
    """
    if key not in PAGE_KEYS:
        return False
    current = sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS)
    if wanted and key not in current:
        if len(current) >= MAX_BOTTOM:
            return False
        current.append(key)
    elif not wanted and key in current:
        current.remove(key)
    else:
        return False
    _V[KEY_BOTTOM_PAGES] = [k for k in PAGE_KEYS if k in set(current)]
    return True


def set_home_order(order):
    _V[KEY_HOME_ORDER] = sanitise_order(order, HOME_PANEL_KEYS)


def move_home_panel(key, delta):
    set_home_order(move(home_panel_order(), key, delta))


def set_home_panel_hidden(key, hidden):
    if key not in HOME_PANEL_KEYS:
        return
    current = set(hidden_home_panels())
    if hidden:
        current.add(key)
    else:
        current.discard(key)
    _V[KEY_HOME_HIDDEN] = [k for k in HOME_PANEL_KEYS if k in current]


def set_unit_system(value):
    if value in UNIT_SYSTEMS:
        _V[KEY_UNITS] = value


def set_sodium_target(value):
    _V[KEY_SODIUM_TARGET] = _as_int(value, SODIUM_TARGETS, sodium_target())


def set_refresh_seconds(value):
    _V[KEY_REFRESH_SECS] = _as_int(value, REFRESH_CHOICES, refresh_seconds())
    apply_runtime()


def reset_layout():
    """Put pages and home panels back the way the app ships. Units stay."""
    d = _defaults()
    for k in (KEY_PAGE_ORDER, KEY_PAGES_HIDDEN, KEY_BOTTOM_PAGES,
              KEY_HOME_ORDER, KEY_HOME_HIDDEN):
        _V[k] = d[k]


def reset_all():
    _V.clear()
    _V.update(_defaults())
    apply_runtime()


def apply_runtime():
    """Push the settings that other modules read as plain module state.

    Only api.TTL today. Guarded because api imports are heavier than this
    module's and a future refactor may rename it - a missing attribute should
    cost the refresh setting, not the app.
    """
    try:
        from . import api
        api.TTL = refresh_seconds()
    except Exception:  # noqa: BLE001 - a settings knob is never worth a crash
        pass


# ---------------------------------------------------------------- persistence
async def _read(prefs, key):
    """One guarded read. None for absent, unreadable, or no such key."""
    if not key:
        return None
    try:
        return await prefs.get(key)
    except Exception:  # noqa: BLE001 - storage is optional, not required
        return None


async def load(prefs):
    """Fill the cache from storage. Returns True if anything was read.

    `prefs` is a flet SharedPreferences (or None, in tests and before the
    service is registered). Every read is individually guarded: one key that
    a platform refuses must not cost the other seven.

    A key absent under its current "agedashii.*" name is looked up again
    under the "nvii.*" name the app used before the rename, so a copy that
    has been running since then keeps its layout. Nothing is written back
    here; `save()` writes the new names, and the first save after an edit
    is what actually moves a value across. The old key is left in place
    rather than deleted - it costs a few bytes and it means a downgrade to
    an older build still finds its settings.
    """
    global LOADED
    LOADED = True
    if prefs is None:
        return False
    read_any = False
    # Every fixed key, plus the three per-surface panel keys. Without these
    # the layout editor wrote to memory only: reorder a card, resize it,
    # hide it, restart the app, and it was all back to the shipped order.
    keys = list(_defaults()) + _panel_keys_for_storage()
    for key in keys:
        value = await _read(prefs, key)
        if value is None:
            value = await _read(prefs, legacy_key(key))
        if value is None:
            continue
        read_any = True
        _V[key] = value
    # Normalise once, here, so every later read is cheap and the stored shape
    # can never surprise a caller.
    _V[KEY_PAGE_ORDER] = page_order()
    _V[KEY_PAGES_HIDDEN] = hidden_pages()
    _V[KEY_BOTTOM_PAGES] = sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS)
    _V[KEY_HOME_ORDER] = home_panel_order()
    _V[KEY_HOME_HIDDEN] = hidden_home_panels()
    _V[KEY_UNITS] = unit_system()
    _V[KEY_SODIUM_TARGET] = sodium_target()
    _V[KEY_REFRESH_SECS] = refresh_seconds()
    apply_runtime()
    return read_any


async def save(prefs):
    """Persist every preference. Returns False rather than raising."""
    if prefs is None:
        return False
    payload = {
        KEY_PAGE_ORDER: page_order(),
        KEY_PAGES_HIDDEN: hidden_pages(),
        KEY_BOTTOM_PAGES: sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS),
        KEY_HOME_ORDER: home_panel_order(),
        KEY_HOME_HIDDEN: hidden_home_panels(),
        KEY_UNITS: unit_system(),
        KEY_SODIUM_TARGET: sodium_target(),
        KEY_REFRESH_SECS: refresh_seconds(),
        KEY_TRIP_TAGS: [f"{a}|{b}|{c}" for a, b, c in trip_tags()],
    }
    # Per-surface panel layout. Written for every surface, including the
    # ones left at their defaults, so switching a card back to full width
    # or un-hiding it persists as the absence it is.
    for key in _panel_keys_for_storage():
        payload[key] = _V.get(key) or []
    try:
        for key, value in payload.items():
            await prefs.set(key, value)
        return True
    except Exception:  # noqa: BLE001 - report it in the UI, do not crash
        return False


def hydrate(page, store, on_done=None):
    """Load once, in the background, from a synchronous build().

    Needed because `load()` is async but Flet builds a page synchronously.
    Startup should call `await load(S.prefs)` before the first render and
    make this a no-op; this is the fallback for when it has not, and for a
    Settings page opened in a harness with no event loop at all. Returns True
    if a load was actually scheduled.
    """
    if LOADED or store is None:
        return False
    run_task = getattr(page, "run_task", None)
    if not callable(run_task):
        return False

    async def job():
        await load(store)
        if on_done:
            on_done()

    try:
        run_task(job)
        return True
    except Exception:  # noqa: BLE001 - no loop, stub page: fall back to defaults
        return False
