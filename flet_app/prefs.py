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
# Pages hidden from the *menu* (the drawer), and nothing else. This used to
# mean "hidden everywhere": the bar was filtered through it, so the single
# eye in Settings > Layout drove both surfaces at once and there was no way
# to keep a page in the menu but off the bar. The key name is unchanged on
# purpose - its drawer meaning is the one an existing install already has,
# so that half of the migration is free.
KEY_PAGES_HIDDEN = NS + "pages_hidden"
# Pages on the bottom bar. Now an independent set: a page is on the bar
# because it is in here, whether or not the menu also lists it.
KEY_BOTTOM_PAGES = NS + "bottom_pages"
# Which generation of the two keys above is stored. 1 is the coupled scheme
# where bottom_pages only took effect for a page that was also unhidden;
# 2 is the split. See `load()` for the one-time conversion between them.
KEY_LAYOUT_SCHEME = NS + "layout_scheme"
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
# ------------------------------------------------------------ appearance
# Three knobs that used to be constants scattered through the drawing code.
# ui.py owns what they *mean*; this module only stores them.
KEY_RADIUS = NS + "radius"
KEY_GLOW = NS + "glow"
KEY_CRT = NS + "crt"
# 0-100, meaningless while KEY_CRT is off but always coerced to something
# valid regardless - a slider next to the switch in Settings > Appearance.
KEY_CRT_INTENSITY = NS + "crt_intensity"


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

# Settings must stay reachable from at least one surface: it is the only way
# back from a layout you regret. It may leave the menu *or* the bar - just
# not both. Enforced on the way in (`set_page_hidden`, `set_in_bottom_bar`
# refuse the last one) and again on the way out (`hidden_pages()` drops it
# from the hidden set if nothing else reaches it), because a value written
# by an older build or hand-edited in storage never went through the setter.
PROTECTED_PAGES = {"settings"}

# The scheme this build writes. Bumped when the meaning of a stored layout
# key changes and `load()` has to convert.
LAYOUT_SCHEME = 2

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

# The corner-radius choices and their default. Named here rather than
# duplicated because Settings offers them and load() validates against them,
# but the *meaning* of the number - how it reaches a control - is ui.RAD()'s
# alone. Imported lazily in `_radius_choices()` so this module stays free of
# a hard dependency on the drawing layer.
def _radius_choices():
    from . import ui as _ui
    return _ui.RADIUS_CHOICES, _ui.DEFAULT_RADIUS

# Seconds of cached-response reuse. 30 is what api.TTL ships with; 0 means
# "only when I pull to refresh".
REFRESH_CHOICES = (0, 15, 30, 60, 300, 900)
DEFAULT_REFRESH = 30

# The CRT overlay's intensity range and shipped default. Kept here rather
# than read off ui.DEFAULT_CRT_INTENSITY for the same reason KEY_RADIUS's
# default is a literal 8 below rather than a lazy ui import - _defaults()
# runs at module load, before it is safe to assume ui.py is fully imported.
CRT_INTENSITY_MIN, CRT_INTENSITY_MAX = 0, 100
DEFAULT_CRT_INTENSITY = 40


def _defaults():
    return {
        KEY_PAGE_ORDER: list(PAGE_KEYS),
        KEY_PAGES_HIDDEN: [],
        KEY_BOTTOM_PAGES: list(DEFAULT_BOTTOM),
        KEY_LAYOUT_SCHEME: LAYOUT_SCHEME,
        KEY_HOME_ORDER: list(HOME_PANEL_KEYS),
        KEY_HOME_HIDDEN: [],
        KEY_UNITS: "imperial",
        KEY_SODIUM_TARGET: 2300,
        KEY_REFRESH_SECS: DEFAULT_REFRESH,
        KEY_BOTTOM_ENABLED: True,
        KEY_TRIP_TAGS: [],
        # Appearance ships as it always looked: rounded, no glow, no CRT.
        KEY_RADIUS: 8,
        KEY_GLOW: False,
        KEY_CRT: False,
        KEY_CRT_INTENSITY: DEFAULT_CRT_INTENSITY,
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


def _as_range_int(value, lo, hi, fallback):
    """Coerce a stored number to an int within [lo, hi].

    Like `_as_int`, but for a genuinely continuous range - the CRT
    intensity slider - rather than a small enumerated set of choices.
    Out-of-range or unparseable reads as the default rather than being
    clamped into range: a wildly corrupt stored value should cost the
    setting, not silently become "0" or "100".
    """
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return n if lo <= n <= hi else fallback


# Anything a storage backend might hand back in place of False. The reason
# this exists at all: SharedPreferences is not consistent about booleans and
# can round-trip one as the string "false", and `bool("false")` is True - so
# every switch in this app came back *on* after a restart. That shipped. It
# is now one function with one test rather than a paragraph of comment
# repeated beside each reader.
_FALSEY = ("false", "0", "no", "off", "")


def _as_bool(value, fallback):
    """Coerce a stored flag to a real bool. Absent means `fallback`."""
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


# ---------------------------------------------------------------- read API
def page_order():
    """All nine page keys in the owner's order, hidden ones included."""
    return sanitise_order(_V.get(KEY_PAGE_ORDER), PAGE_KEYS)


def _bar_set():
    """The raw chosen-for-the-bar set, before any rail is applied."""
    return set(sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS))


def hidden_pages():
    """Page keys the owner has taken out of the *menu*, in canonical order.

    Taking a page out of the menu says nothing about the bar any more; the
    two are separate sets. A protected page is only allowed out of the menu
    while the bar still carries it, so this is where the read-side rail
    lives: if Settings is hidden and not on the bar, it is not hidden.
    """
    hidden = sanitise_set(_V.get(KEY_PAGES_HIDDEN), PAGE_KEYS)
    bar = _bar_set()
    return [k for k in hidden if k not in PROTECTED_PAGES or k in bar]


def visible_pages():
    """Pages the menu lists, in order. Not the same set as the bar's."""
    hidden = set(hidden_pages())
    return [k for k in page_order() if k not in hidden]


def bottom_bar_pages():
    """Pages for the bottom navigation bar, in order, at most MAX_BOTTOM.

    Independent of the menu: a page rides the bar because it is in the
    chosen set, whether or not the menu also lists it. That is the whole
    point - "i disabled the bottom bar and 'show' everything. seemingly
    theres no way to customize the bottom bar without the menu becoming
    affected". It used to intersect with `visible_pages()`, so it was.

    Never returns an empty list: a bar of nothing but "More" is a dead end,
    so the first page the menu offers stands in.
    """
    chosen = _bar_set()
    bar = [k for k in page_order() if k in chosen][:MAX_BOTTOM]
    if bar:
        return bar
    fallback = visible_pages() or reachable_pages()
    return fallback[:1]


def reachable_pages():
    """Every page you can get to, in order - the menu plus the bar.

    What `render()` must check a page against. Under the old coupled scheme
    "on the bar" implied "in the menu", so `visible_pages()` was the whole
    truth; now a page can be bar-only, and bouncing her off it because the
    menu does not list it would make the bar's own buttons dead.
    """
    reach = set(visible_pages()) | _bar_set() | PROTECTED_PAGES
    return [k for k in page_order() if k in reach]


def bottom_bar_enabled():
    """Whether to draw the bottom bar at all.

    It repeats what the drawer already lists, so it is worth being able to
    turn off outright rather than only choosing what rides on it.
    """
    return _as_bool(_V.get(KEY_BOTTOM_ENABLED), True)   # never set: ship it on


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


def move_panel_onto(surface, key, target):
    """Drop `key` where `target` currently sits. True if anything moved.

    What drag-and-drop means, as opposed to the arrows' one-step `move()`.
    Pop-then-insert-at-the-old-index is deliberate: dragging downwards
    lands the card *after* its target and upwards lands it *before*, which
    is what the finger just did, and it needs no direction test.

    Works on the full order, hidden panels included, so a hidden card keeps
    its place in the list relative to its neighbours and comes back where
    she left it rather than at the end.
    """
    order = panel_order(surface)
    if key == target or key not in order or target not in order:
        return False
    i, j = order.index(key), order.index(target)
    order.pop(i)
    order.insert(j, key)
    _V[_pkey(surface, "order")] = order
    return True


def cycle_panel_width(surface, key, step=1, wrap=True):
    """Step a card's width through full -> half -> third. The new one, or None.

    The keyboard-free, pointer-free way to resize: a tap on the width pill
    wraps round, so three taps come back where they started and nothing is
    ever out of reach.

    `wrap=False` clamps instead, for the resize grip: dragging hard to the
    right means "as wide as it goes" and should stop at full, not come out
    the other side at a third. None when nothing changed, so the caller can
    skip a repaint it does not need.
    """
    from . import panels as _panels
    if key not in _surface_keys(surface):
        return None
    order = list(_panels.WIDTH_ORDER)
    i = order.index(panel_width(surface, key))
    j = (i + step) % len(order) if wrap else max(0, min(len(order) - 1, i + step))
    if j == i:
        return None
    want = order[j]
    return want if set_panel_width(surface, key, want) else None


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


# ---------------------------------------------------------- appearance
def corner_radius():
    """How round every corner in the app is, in px. 0 is square.

    An unrecognised stored value falls back to the shipped 8 rather than
    being used: a corrupt preference should cost the setting, not draw the
    whole app at a radius nothing in Settings can undo.
    """
    choices, default = _radius_choices()
    return _as_int(_V.get(KEY_RADIUS), choices, default)


def accent_glow():
    """Whether headings, the brand pill and active nav items carry alola's
    accent shadow. Off by default - it is an effect, not a default look."""
    return _as_bool(_V.get(KEY_GLOW), False)


def crt_overlay():
    """Whether the CRT vignette is drawn over the app. Off by default."""
    return _as_bool(_V.get(KEY_CRT), False)


def crt_intensity():
    """How strong the CRT overlay draws, 0-100. Read regardless of whether
    the overlay itself is on - it is meaningless while off, but a value
    Settings can show and edit either way."""
    return _as_range_int(_V.get(KEY_CRT_INTENSITY), CRT_INTENSITY_MIN,
                         CRT_INTENSITY_MAX, DEFAULT_CRT_INTENSITY)


def set_corner_radius(value):
    choices, _ = _radius_choices()
    _V[KEY_RADIUS] = _as_int(value, choices, corner_radius())
    apply_runtime()
    return True


def set_accent_glow(on):
    _V[KEY_GLOW] = bool(on)
    apply_runtime()
    return True


def set_crt_overlay(on):
    _V[KEY_CRT] = bool(on)
    apply_runtime()
    return True


def set_crt_intensity(value):
    _V[KEY_CRT_INTENSITY] = _as_range_int(value, CRT_INTENSITY_MIN,
                                          CRT_INTENSITY_MAX, crt_intensity())
    apply_runtime()
    return True


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


def set_page_in_menu(key, wanted):
    """Put a page in the menu, or take it out. Returns True if it changed.

    Refused for a protected page that the bar does not carry either - that
    would strand Settings, and Settings is the only way back from a layout
    you regret. Refusing rather than silently ignoring lets the caller say
    why instead of drawing a switch that does nothing.
    """
    if key not in PAGE_KEYS:
        return False
    if not wanted and key in PROTECTED_PAGES and key not in _bar_set():
        return False
    current = set(hidden_pages())
    if wanted:
        if key not in current:
            return False
        current.discard(key)
    else:
        if key in current:
            return False
        current.add(key)
    _V[KEY_PAGES_HIDDEN] = [k for k in PAGE_KEYS if k in current]
    return True


def set_page_hidden(key, hidden):
    """Hide or show a page *in the menu*. The bar is a separate setting.

    Kept as the inverse of `set_page_in_menu` because "hidden" is the word
    the stored key and the older call sites use.
    """
    return set_page_in_menu(key, not hidden)


def set_in_bottom_bar(key, wanted):
    """Add or remove a page from the bottom bar. True if the set changed.

    Two refusals, both reported rather than silently absorbed:
      - past MAX_BOTTOM, because truncating would drop a page she already
        put there in favour of the one she just asked for;
      - the last surface holding a protected page, for the same reason
        `set_page_in_menu` refuses it.
    """
    if key not in PAGE_KEYS:
        return False
    current = sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS)
    if wanted and key not in current:
        if len(current) >= MAX_BOTTOM:
            return False
        current.append(key)
    elif not wanted and key in current:
        if key in PROTECTED_PAGES and key in set(hidden_pages()):
            return False
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

    api.TTL and ui.S's three appearance knobs. Guarded one import at a time,
    because a missing attribute should cost one setting rather than all of
    them - and because pushing the appearance values is what makes them
    *apply*, so it has to survive an api that failed to import.
    """
    try:
        from . import api
        api.TTL = refresh_seconds()
    except Exception:  # noqa: BLE001 - a settings knob is never worth a crash
        pass
    try:
        from . import ui
        ui.S.radius = corner_radius()
        ui.S.glow = accent_glow()
        ui.S.crt = crt_overlay()
        ui.S.crt_intensity = crt_intensity()
    except Exception:  # noqa: BLE001 - ditto, and the defaults on S still draw
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
    saw_scheme = False
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
        if key == KEY_LAYOUT_SCHEME:
            saw_scheme = True
        _V[key] = value
    # An install that has stored something but has never stored a scheme
    # number was written by the coupled build, where `bottom_pages` only
    # took effect for a page that was also unhidden. Under the split those
    # same two values would put a hidden page back on the bar - which is a
    # layout she never chose, appearing out of nowhere on an upgrade. So
    # collapse the old pair down to what it actually *showed*, once, and
    # stamp the scheme so it never runs again. An empty store is a fresh
    # install with nothing to convert.
    if read_any and not saw_scheme:
        _V[KEY_BOTTOM_PAGES] = [
            k for k in sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS)
            if k not in set(sanitise_set(_V.get(KEY_PAGES_HIDDEN), PAGE_KEYS))]
    _V[KEY_LAYOUT_SCHEME] = LAYOUT_SCHEME
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
    # Booleans especially: storage can hand one back as the string "false",
    # and normalising here means nothing downstream ever sees that shape.
    _V[KEY_BOTTOM_ENABLED] = bottom_bar_enabled()
    _V[KEY_RADIUS] = corner_radius()
    _V[KEY_GLOW] = accent_glow()
    _V[KEY_CRT] = crt_overlay()
    _V[KEY_CRT_INTENSITY] = crt_intensity()
    apply_runtime()
    return read_any


def save_payload():
    """Every key that goes to storage, with the value to write for it.

    Split out of `save()` and, crucially, *derived* from `_defaults()`
    rather than listed by hand. The hand-written list is how
    KEY_BOTTOM_ENABLED came to be loaded but never saved: the bottom bar
    switch worked all session and was back on at every restart, and nothing
    failed loudly enough to notice. Anything added to `_defaults()` is now
    persisted whether or not whoever added it remembered this function, and
    a test asserts exactly that.

    The explicit entries below are the keys whose stored form is not simply
    what is in the cache - orders that want sanitising on the way out, tags
    that want re-encoding. Everything else falls through to `setdefault`.
    """
    payload = {
        KEY_PAGE_ORDER: page_order(),
        KEY_PAGES_HIDDEN: hidden_pages(),
        KEY_BOTTOM_PAGES: sanitise_set(_V.get(KEY_BOTTOM_PAGES), PAGE_KEYS),
        # Always this build's number, never whatever was read: writing it is
        # what tells the next start-up that the menu/bar split has already
        # been applied and must not be applied again.
        KEY_LAYOUT_SCHEME: LAYOUT_SCHEME,
        KEY_HOME_ORDER: home_panel_order(),
        KEY_HOME_HIDDEN: hidden_home_panels(),
        KEY_UNITS: unit_system(),
        KEY_SODIUM_TARGET: sodium_target(),
        KEY_REFRESH_SECS: refresh_seconds(),
        KEY_TRIP_TAGS: [f"{a}|{b}|{c}" for a, b, c in trip_tags()],
        # Written as real bools, not as whatever came back from storage
        # last time, so a value that arrived as the string "false" is not
        # handed straight back to be misread again on the next restart.
        KEY_BOTTOM_ENABLED: bottom_bar_enabled(),
        KEY_RADIUS: corner_radius(),
        KEY_GLOW: accent_glow(),
        KEY_CRT: crt_overlay(),
        KEY_CRT_INTENSITY: crt_intensity(),
    }
    defaults = _defaults()
    for key in defaults:
        payload.setdefault(key, _V.get(key, defaults[key]))
    # Per-surface panel layout. Written for every surface, including the
    # ones left at their defaults, so switching a card back to full width
    # or un-hiding it persists as the absence it is.
    for key in _panel_keys_for_storage():
        payload[key] = _V.get(key) or []
    return payload


async def save(prefs):
    """Persist every preference. Returns False rather than raising."""
    if prefs is None:
        return False
    try:
        for key, value in save_payload().items():
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
