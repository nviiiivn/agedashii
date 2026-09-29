"""What Navi remembers about how she wants to ride with it.

A store of its own, separate from `agedashii_washi.prefs`, for the same
reason Watudo has one: every other Agedashii app would otherwise carry a
setting about a lean-angle warning it does not have. Washi's store still
holds what every app shares - the server address, the card layout - and this
holds what only a navigation app has an opinion about.

Everything here is read at launch and never asked for again, so the app looks
and behaves identically with the radio off. That is not a nicety: "i need
everything to work with no signal. no exception".

STORED SHAPE

All values are stored as strings under `navi.*`, because SharedPreferences on
Android round-trips strings reliably. The parse back is deliberately total -
an unreadable value becomes the default rather than an exception, since a
preference that can crash the launch is worse than one that can be wrong.

    navi.theme        "night" | "day" | "sunlight" | "cluster"
    navi.bike         "yellow" | "black"
    navi.units        "imperial" | "metric"
    navi.tiles        "dark" | "streets" | "satellite" | "topo"
    navi.keep_awake   "1" | "0"
    navi.voice        "1" | "0"
    navi.north_up     "1" | "0"
    navi.autorecord   "1" | "0"
    navi.hz           "1" | "2" | "5" | "10"        GPS samples per second
    navi.offroute_m   "30".."200"                   metres before "off route"
    navi.last_dest    "37.804400,-122.271200,Name"  or ""

No dates are stored here. The ride log has the timestamps and owns its own
files; see telemetry.py.
"""
from . import bikes, theme

KEY = "navi."

#: name -> (default, allowed values)
_CHOICES = {
    "theme": ("night", tuple(theme.THEMES)),
    # `bike` is the MARKER COLOUR and `machine` is which motorcycle. Two keys
    # because they are two decisions - the marker colour is about finding herself
    # on a map, the machine is about the firing ratio the RPM maths uses - and
    # `bike` shipped first, so renaming it would orphan the stored value.
    "bike": ("yellow", ("yellow", "black")),
    "machine": (bikes.DEFAULT, tuple(bikes.PROFILES)),
    "units": ("imperial", ("imperial", "metric")),
    "tiles": ("dark", ("dark", "streets", "satellite", "topo")),
    # How the telemetry is drawn. Her "i'm looking for OTHER views/versions/
    # themes of those types of sensors as overlays so it doesn't block the view"
    # and "too much of the screen is covered by ui right now", then: "i want
    # this default, but all the views (+ MORE HOPEFULY) should be available in
    # the settings menu."
    #
    # Every one of these floats ON the map rather than stealing height from it,
    # except `strip`, which is the old band and is kept because it is the only
    # one that shows everything at once.
    #
    #   ribbon    one thin translucent row along the bottom edge. The default.
    #   corners   anchored in the four corners, middle of the map clear.
    #   gunsight  a thick horizon line across the centre, numbers in the
    #             bottom corners - closest to an aircraft attitude indicator.
    #   column    a narrow strip down the left edge, map clear to the right.
    #   strip     the old band under the map. Everything, nothing hidden.
    #   minimal   speed, limit and the next turn. Nothing else.
    #   off       the map, and nothing on it.
    "hud": ("ribbon", ("ribbon", "corners", "gunsight", "column", "strip",
                       "minimal", "off")),
    # Which of her two icon artworks the app wears inside itself - the splash,
    # the menu header, the about card.
    #
    # NOT the launcher icon. Android only allows that to change through
    # activity-alias entries in the manifest and a native call to
    # PackageManager, neither of which Flet exposes, so the icon on the home
    # screen is whatever was built in. This setting is honest about being the
    # in-app one.
    "icon": ("cb", ("cb", "ex")),
    # Where the gauges sit, per orientation. Her spec exactly: "horizontal/wide
    # makes the sensor shit mostly on the right side (or left adjustable in
    # settings) and vertically it's at the bottom (or top, adjustABLE IN
    # SETTINGS)".
    #
    # Two settings rather than one, because a phone in a bar mount is used both
    # ways and a single "edge" would move the gauges under her hand in one
    # orientation to keep them clear in the other.
    # Which way up the phone is allowed to be.
    #
    # Hers: "if i turn on DO NOT AUTO ROTATE it stays vertical but ... is there
    # an option to MAKE IT / FORCE IT horizontal? so that the gforces don't
    # ever confuse the phone into thinking its turn".
    #
    # Android's own rotation lock only holds whichever way up the phone
    # happened to be, and a bike leaning into a corner IS a rotation as far as
    # the accelerometer is concerned. Locking it here means a landscape mount
    # stays landscape through a hard left.
    "orientation": ("auto", ("auto", "landscape", "portrait")),
    "hud_side": ("right", ("right", "left")),    # landscape
    "hud_edge": ("bottom", ("bottom", "top")),   # portrait
}
_FLAGS = {
    # The screen must stay on. A phone that sleeps at 60 mph is not a
    # navigation device, so this is one of the two that default to on.
    "keep_awake": True,
    # Spoken turns, over whatever the helmet is paired to. Default on for the
    # same reason: the instruction you can hear is the one that does not cost
    # a glance.
    "voice": True,
    # Rotate the map to the direction of travel. Off by default - north-up is
    # what a paper map does and it is easier to keep a mental picture from.
    "north_up": False,
    # Start recording the moment the bike moves rather than waiting to be
    # told. Off by default: recording a ride she did not mean to record is a
    # privacy decision, not a convenience.
    "autorecord": False,
    # The sensor dash - horizon, g dot, climb, loudness, RPM - over the map.
    # On by default: it is the reason the sensors are read at all.
    "dash": True,
    # The microphone, for loudness and the RPM estimate. OFF by default and it
    # stays that way until she asks: it is a microphone, and defaulting it on
    # would be opening one without being told to.
    "mic": False,
    # Building footprints on the vector map. They ship inside the tiles either
    # way, so this only shows or hides a layer - no second download and no
    # second map.
    #
    # Hers, and she is the one who asked for a switch rather than picking a
    # side: buildings are landmarks in town and clutter on a twisty road, and
    # which of those is true depends on the ride. OFF by default, because the
    # road she is on has to win over the scenery.
    "buildings": False,
    # Say the street name after her own recorded line ("...onto Broadway" in
    # the phone's voice). OFF by default: her lines are whole lines in her own
    # words, and a robot tacking "Broadway" onto "GO LEFT IF YOU WANT TO LIVE"
    # ruins the joke, which is the point of the feature.
    "clip_streets": False,
    # His wild lines after some prompts (voicelines.Flourish). ON by default:
    # hers - "i want those peppered in randomly and accordingly" - and it is
    # a switch so she can turn the theatre off for a long ride and keep the
    # voice.
    "flourishes": True,
    # Whether the surfaces that are NOT the map follow the dashboard's own
    # alola.lol palette. On by default, because they are read at a stop with the
    # phone in a hand, where looking like the rest of the family is worth more
    # than maximum contrast. The map keeps the theme she picked either way.
    "alola_chrome": True,
    # Let the time of day pick the theme and the screen brightness: Sunlight at
    # midday, Night at dusk, Red night after dark. On by default, because the one
    # thing a rider should never have to do at 60 mph is change a setting.
    # Picking a theme by hand turns this off, so a deliberate choice is never
    # overridden a minute later.
    "auto_theme": True,
    # The bottom bar. Off by default now: five labelled destinations across the
    # bottom of a navigation screen is a lot of permanent furniture - "ride/
    # whereto/around/rides/settings are not ALWAYS in my face?" - and the round
    # menu button top-left reaches all of them in one tap.
    "bar": False,
    # Attach the sensors at launch, or wait to be asked. Waiting by default:
    # "app loads. then if/when i want i can click 'load/launch sensors/telemtry'
    # ... hopefully making the front end lighter and easier". It also makes the
    # first launch after an install noticeably quicker.
    "sensors_on_demand": True,
    # Follow the bike. Off means the GPS stream is not subscribed at all: no
    # fixes, no repaints, no radio. Her own idea - "maybe gps should be a button
    # i activate so it's another thing to off-load from the front?".
    #
    # ON by default, because an app you open to navigate that does not know
    # where you are is a worse first impression than a warm phone. The button is
    # in ride options, and Record turns it on regardless.
    "gps": True,
}
_INTS = {
    # One fix a second is enough to navigate from and cheap on battery. Five
    # and ten are there for a ride she wants the detail of.
    "hz": (1, (1, 2, 5, 10)),
    # How far off the line before guidance says so and offers a re-route.
    # 45 m is wide enough to survive a parallel frontage road and tight
    # enough to catch a missed turn within a block.
    "offroute_m": (45, None),
}

#: Bounds for the free integers, so a hand-edited value cannot produce a
#: re-route on every fix or one that never fires.
_INT_RANGE = {"offroute_m": (30, 200)}

#: The live values. A dict for the same reason theme.S is one.
S = {}

#: The Flet SharedPreferences service, parked here on purpose.
#:
#: It is a *service*: constructing it inside the page context registers it, and
#: Flet unregisters it again the moment nothing holds a strong reference. The
#: other Agedashii apps park it on `agedashii_qi.S`, but Navi does not import Qi
#: - it has its own look - so it needs a holder of its own. A local in `main()`
#: would be collected and storage would silently stop working.
STORE = {"prefs": None}

LOADED = False


def store():
    """The storage service, or None before `set_store` has been called."""
    return STORE["prefs"]


def set_store(service):
    STORE["prefs"] = service
    return service


def defaults():
    out = {k: v[0] for k, v in _CHOICES.items()}
    out.update(_FLAGS)
    out.update({k: v[0] for k, v in _INTS.items()})
    out["last_dest"] = ""
    return out


def reset():
    """Back to shipped defaults, in memory. Used by tests and by Settings."""
    global LOADED
    S.clear()
    S.update(defaults())
    LOADED = False
    theme.set_theme(S["theme"])


reset()


# --------------------------------------------------------------- accessors

def get(name):
    if name not in S:
        return defaults().get(name)
    return S[name]


def theme_key():
    return S.get("theme", "night")


def bike():
    """The marker colour, "yellow" or "black". NOT which motorcycle."""
    return S.get("bike", "yellow")


def machine():
    """Which motorcycle - a key into `bikes.PROFILES`."""
    return S.get("machine", bikes.DEFAULT)


def machine_name():
    return bikes.profile(machine())["name"]


def units():
    return S.get("units", "imperial")


def imperial():
    return units() == "imperial"


def tiles():
    return S.get("tiles", "dark")


def keep_awake():
    return bool(S.get("keep_awake", True))


def voice():
    return bool(S.get("voice", True))


def north_up():
    return bool(S.get("north_up", False))


def autorecord():
    return bool(S.get("autorecord", False))


def dash():
    """Whether any telemetry is drawn at all. `hud()` says how.

    ONE SWITCH. It used to be three: this flag ("Show the gauges at all" at
    the bottom of Settings), an "Off" tile among the layouts in Settings, and
    the Gauges row in Ride options - which flipped this flag, never saved it,
    and did nothing at all whenever the layout was "off", because this
    function ANDed the two. Hers: "in settings there's 'show the gauges at
    all' and the in options there's gauges there and it only shows or hides
    it... sooooooo idk".

    Now `dash` is the only on/off, and `hud` only ever says which layout.
    A stored "off" layout from an older build is turned into dash=False by
    `_normalise` at load, so the `hud() != "off"` below is belt and braces.
    """
    return bool(S.get("dash", True)) and hud() != "off"


def toggle_dash():
    """Show or hide the gauges. The one door both surfaces use. Returns dash()."""
    if S.get("hud") == "off":
        S["hud"] = _CHOICES["hud"][0]
        S["dash"] = True
    else:
        S["dash"] = not bool(S.get("dash", True))
    return dash()


def hud():
    return S.get("hud", "ribbon")


def layout_name():
    """The chosen layout's name, as the tiles in Settings show it."""
    from . import gauges
    return next((name for key, name, _detail in gauges.LAYOUTS
                 if key == hud()), hud().title())


def bar():
    return bool(S.get("bar", False))


def sensors_on_demand():
    return bool(S.get("sensors_on_demand", True))


def gps():
    return bool(S.get("gps", True))


def orientation():
    """Which way up the phone is allowed to be: auto, landscape or portrait."""
    return S.get("orientation", "auto")


def hud_side():
    """Which side the gauges sit on in landscape."""
    return S.get("hud_side", "right")


def hud_edge():
    """Which edge the gauges sit on in portrait."""
    return S.get("hud_edge", "bottom")


def hud_anchor(wide):
    """Where the gauges go for this orientation: left/right/top/bottom."""
    return hud_side() if wide else hud_edge()


def icon():
    """Which artwork the app wears inside itself: "cb" or "ex"."""
    return S.get("icon", "cb")


def icon_asset():
    """The bundled path for the chosen in-app icon.

    `icons/icon-192.png` is the default, shipped by the build. The alternate is
    shipped beside it under its own name, so both are present in every build and
    switching needs no download.
    """
    return ("icons/icon-192.png" if icon() == "cb"
            else "icons/icon-ex-192.png")


def mic():
    return bool(S.get("mic", False))


def buildings():
    """Whether the vector map draws building footprints."""
    return bool(S.get("buildings", False))


def clip_streets():
    """Whether the street name follows her recorded lines."""
    return bool(S.get("clip_streets", False))


def flourishes():
    """Whether character lines are added after some prompts."""
    return bool(S.get("flourishes", True))


def alola_chrome():
    return bool(S.get("alola_chrome", True))


def auto_theme():
    return bool(S.get("auto_theme", True))


def hz():
    return int(S.get("hz", 1) or 1)


def offroute_m():
    return int(S.get("offroute_m", 45) or 45)


def last_dest():
    """(lat, lon, name) she last rode to, or None.

    Kept so that opening the app after a stop offers the same destination
    rather than making her find it again with gloves on.
    """
    return _parse_dest(S.get("last_dest", ""))


def set_last_dest(lat, lon, name=""):
    S["last_dest"] = f"{float(lat):.6f},{float(lon):.6f},{name or ''}"
    return S["last_dest"]


def _parse_dest(raw):
    if not isinstance(raw, str) or "," not in raw:
        return None
    parts = raw.split(",", 2)
    try:
        return (float(parts[0]), float(parts[1]),
                parts[2] if len(parts) > 2 else "")
    except (TypeError, ValueError):
        return None


def set_value(name, value):
    """Set one preference, coercing and validating. Returns what was stored.

    Total on purpose: a bad value is refused and the old one kept, rather
    than stored to be discovered later by the map behaving oddly.
    """
    if name == "hud" and value == "off":
        # "Off" is not a layout, it is the gauges switch. Kept as an accepted
        # value so an older caller or a stored preference still means what it
        # meant, but it lands on the one switch and leaves the layout alone.
        S["dash"] = False
        return S.get("hud")
    if name in _CHOICES:
        if value in _CHOICES[name][1]:
            S[name] = value
            if name == "theme":
                theme.set_theme(value)
            return S[name]
        return S.get(name)
    if name == "theme_auto":
        # The automatic picker's own door into the theme. It writes the palette
        # WITHOUT touching the stored preference, so tonight's red screen does
        # not become tomorrow's remembered choice - and turning the automation
        # off puts back whatever she last chose by hand.
        if value in _CHOICES["theme"][1]:
            theme.set_theme(value)
            return value
        return theme_key()
    if name in _FLAGS:
        S[name] = bool(value)
        if name == "alola_chrome":
            # theme.PAL reads this on every build and cannot import prefs - that
            # would be a cycle - so the value is pushed across as it changes.
            theme.S["chrome_alola"] = S[name]
        return S[name]
    if name in _INTS:
        try:
            num = int(value)
        except (TypeError, ValueError):
            return S.get(name)
        allowed = _INTS[name][1]
        if allowed is not None and num not in allowed:
            return S.get(name)
        low, high = _INT_RANGE.get(name, (None, None))
        if low is not None:
            num = max(low, min(high, num))
        S[name] = num
        return S[name]
    if name == "last_dest":
        S["last_dest"] = value if isinstance(value, str) else ""
        return S["last_dest"]
    return None


# ----------------------------------------------------------------- storage

def save_payload():
    """Every preference as the strings that go to storage."""
    out = {}
    for name in _CHOICES:
        out[KEY + name] = str(S.get(name, _CHOICES[name][0]))
    for name in _FLAGS:
        out[KEY + name] = "1" if S.get(name, _FLAGS[name]) else "0"
    for name in _INTS:
        out[KEY + name] = str(S.get(name, _INTS[name][0]))
    out[KEY + "last_dest"] = str(S.get("last_dest") or "")
    return out


def apply_stored(name, raw):
    """Take one raw stored string and put it into memory.

    Split out from `load` so a test can prove the parse with no Flet service,
    and so one unreadable key is visibly one key's problem.
    """
    if raw is None:
        return False
    if name in _FLAGS:
        truthy = str(raw) not in ("0", "", "False", "false")
        return set_value(name, truthy) is not None
    return set_value(name, raw) is not None


async def load(prefs):
    """Fill the cache from storage. Returns True if anything was read.

    `prefs` is a flet SharedPreferences, or None in tests and before the
    service is registered. Every read is guarded on its own: one key a
    platform refuses must not cost the other ten, and the whole point of
    these values is that the app draws correctly before it has a network.
    """
    global LOADED
    LOADED = True
    if prefs is None:
        return False
    read_any = False
    raw_seen = {}
    for name in list(defaults()):
        try:
            raw = await prefs.get(KEY + name)
        except Exception as exc:         # noqa: BLE001 - storage can refuse
            print(f"[navi] prefs: cannot read {KEY + name}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
        if raw is None:
            continue
        raw_seen[name] = raw
        if apply_stored(name, raw):
            read_any = True
    _normalise(raw_seen)
    theme.set_theme(S.get("theme", "night"))
    return read_any


def _normalise(raw_seen):
    """Fold an older build's stored state into today's meaning.

    Keys are read in `defaults()` order, `hud` before `dash`, so a stored
    hud="off" beside a stored dash="1" would otherwise come back as gauges
    ON - the opposite of what the older build was showing her. Decided from
    the raw strings, because `set_value` has already turned "off" into the
    switch and left the layout at its default by the time this runs.
    """
    if raw_seen.get("hud") == "off":
        S["dash"] = False


async def save(prefs):
    """Persist every preference. Returns False rather than raising."""
    if prefs is None:
        return False
    try:
        for key, value in save_payload().items():
            await prefs.set(key, value)
        return True
    except Exception as exc:             # noqa: BLE001 - say so in the UI
        # Printed as well as returned: every caller today ignores the False,
        # so without this a setting that never saved looked like one that did.
        print(f"[navi] prefs: save failed: {type(exc).__name__}: {exc}",
              flush=True)
        return False
