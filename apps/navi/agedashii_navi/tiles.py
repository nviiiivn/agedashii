"""Which tiles the map draws, and where they come from.

Four renderings, because "MAP WITH VARIOUS VIEWS/THEMES", and one rule about
all of them: the offline archive wins whenever it is installed.

That rule is the whole design. `agedashii_washi.tileserver` serves an MBTiles
file off the device over loopback, so a map can be drawn in a canyon with the
radio off - and an app that quietly preferred a prettier online style over the
archive would be an app that shows a grey grid exactly when it matters. So
`pick()` returns the archive for every style when one is installed, and says
which style it actually got.

WHAT THE ARCHIVE CAN DRAW

An MBTiles archive holds ONE rendering. This file used to say that rendering
was streets, and it is not: the archive every build ships is named "Offline
OSM dark" in its own metadata table and its tiles are dark. So with the
archive installed, Dark is the only style that is ever drawn, and Streets,
Satellite and Topo are never honoured - not in a canyon and not in town
either, because the rule above does not look at signal.

Hers, from the road: "tiles streets clicking it does nothing, changing the
theme in settings or putting streets or topo changes nothing either it just
the black tiles". The rule was right and the picker was lying about it. So
the archive's rendering is READ from its metadata rather than assumed,
`pick()` reports the style actually drawn, and `choices()` says of every
style whether it can be drawn right now and, if not, why - so no screen has
to offer a choice that cannot be honoured.

The online styles are public endpoints, listed with their attribution because
that is the licence condition, not a nicety.
"""
import os
import sqlite3

from agedashii_washi import api as M
from agedashii_washi import tileserver

#: style key -> (label, url template, attribution)
STYLES = {
    "dark": ("Dark",
             "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
             "CARTO / OpenStreetMap"),
    "streets": ("Streets",
                "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "OpenStreetMap"),
    "satellite": ("Satellite",
                  "https://server.arcgisonline.com/ArcGIS/rest/services/"
                  "World_Imagery/MapServer/tile/{z}/{y}/{x}",
                  "Esri World Imagery"),
    "topo": ("Topo",
             "https://tile.opentopomap.org/{z}/{x}/{y}.png",
             "OpenTopoMap / OpenStreetMap"),
}

#: Words in an archive's metadata `name` that say which style it renders,
#: checked in this order. "Offline OSM dark" has "osm" in it as well, which is
#: why dark is looked for before the street words.
_NAME_WORDS = (("dark", ("dark", "night")),
               ("satellite", ("satellite", "imagery", "aerial")),
               ("topo", ("topo", "contour", "terrain")),
               ("streets", ("street", "osm", "openstreetmap")))

#: path -> (mtime, style) so the metadata is read once per archive, not once
#: per repaint.
_ARCHIVE_STYLE = {}

DEFAULT = "dark"

#: What the app identifies itself as to a public tile endpoint. Required by
#: OSM's tile usage policy, and the reason a bare default user agent collects
#: a 429 after a few hundred tiles.
USER_AGENT = "online.iamprettyfamous.agedashii.navi"


def label(style):
    return STYLES.get(style, STYLES[DEFAULT])[0]


def attribution(style):
    return STYLES.get(style, STYLES[DEFAULT])[2]


def style_from_name(name):
    """Which of STYLES an archive called `name` renders, or None if unsaid."""
    lowered = str(name or "").lower()
    for key, words in _NAME_WORDS:
        if any(word in lowered for word in words):
            return key
    return None


def archive_style(path):
    """The style the archive at `path` is a rendering of, or None if unknown.

    Read from the archive's own metadata table, the same file the tile server
    is serving. None - an unreadable file, or a name that does not say - is
    reported as unknown rather than guessed, because guessing is exactly how
    this module came to promise streets on a dark map.
    """
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        print(f"[navi] tiles: cannot stat the archive {path}: {exc}",
              flush=True)
        return None
    cached = _ARCHIVE_STYLE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    name = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM metadata WHERE name = 'name'").fetchone()
        finally:
            conn.close()
        name = row[0] if row else None
    except sqlite3.Error as exc:
        print(f"[navi] tiles: cannot read the archive's metadata: {exc}",
              flush=True)
    style = style_from_name(name)
    _ARCHIVE_STYLE[path] = (mtime, style)
    return style


def pick(style=DEFAULT, archive=None):
    """Decide what the tile layer should fetch.

    Returns a dict:

        url           what to give the tile layer
        offline       True when it is the on-device archive
        style         the style actually being drawn, or None when the
                      archive does not say what it is
        asked         the style she chose
        substituted   True when the asked-for style could not be honoured
        note          one short line for the chip, in her terms
        attribution   who to credit

    `archive` overrides where the MBTiles file is looked for, for tests. The
    default asks Washi, which checks an explicit setting, then the app bundle,
    then the download directory - and returning None is a normal outcome, not
    an error.
    """
    asked = style if style in STYLES else DEFAULT
    path = archive if archive is not None else M.mbtiles_path()
    local = tileserver.start(path) if path else None
    if local:
        # THE RULE: the archive, whatever was asked for. What changed is only
        # that it now says which rendering that is.
        drawn = archive_style(path)
        substituted = drawn != asked
        words = label(drawn).lower() if drawn else "style unknown"
        # What is drawn, and nothing about what was asked: a choice stored by
        # an older build that offered Streets would otherwise print "not
        # streets" on the chip for ever, with no way left to clear it.
        # `substituted` and `asked` still carry it for a caller that wants it.
        return {"url": local, "offline": True,
                "style": drawn, "asked": asked,
                "substituted": substituted,
                "note": f"offline map - {words}",
                "attribution": "on-device archive",
                "zooms": tileserver.status().get("zooms")}
    return {"url": STYLES[asked][1], "offline": False, "style": asked,
            "asked": asked, "substituted": False,
            "note": f"online - {STYLES[asked][0].lower()} - needs signal",
            "attribution": STYLES[asked][2],
            # The public endpoints all render to z19; satellite goes further
            # but the layer scaling past 19 is not worth a special case.
            "zooms": (0, 19)}


#: Why a style cannot be drawn when the archive is installed. In her terms,
#: and it says the reason is the rule, not a fault.
WHY_ARCHIVE_WINS = ("online only - this phone has the offline map, and the "
                    "offline map always wins so it never goes blank with no "
                    "signal")


def choices(style=DEFAULT, archive=None):
    """Every style, and whether it can be drawn right now. For the pickers.

    Returns (choice, rows): `choice` is `pick(style)`, and each row is

        key, label   the style
        drawn        True for the one on screen
        usable       True when choosing it would actually change the map
        why          when it is not usable, the reason in one line; when it
                     is, what it needs ("needs signal" for an online style)

    With the archive installed exactly one style is usable - the archive's
    own - and the other three say why not. With no archive all four are
    usable and all four need signal.
    """
    choice = pick(style, archive=archive)
    rows = []
    for key in STYLES:
        if choice["offline"]:
            usable = key == choice["style"]
            why = ("offline - drawn from the phone, no signal needed"
                   if usable else WHY_ARCHIVE_WINS)
        else:
            usable = True
            why = "online - needs signal"
        rows.append({"key": key, "label": label(key),
                     "drawn": key == choice["style"],
                     "usable": usable, "why": why})
    return choice, rows


def usable(style=DEFAULT, archive=None):
    """The style keys that choosing would actually change the map to."""
    return [row["key"] for row in choices(style, archive=archive)[1]
            if row["usable"]]


def cycle(style, only=None):
    """The next style, for a single button that walks through them.

    `only` limits the walk to the styles that can be honoured - pass
    `usable()` - so a button never lands on one the map will not draw. With
    one usable style the answer is that style, and the button has nothing to
    do; the caller should say so rather than offer the tap.
    """
    keys = [k for k in STYLES if only is None or k in only] or [DEFAULT]
    try:
        return keys[(keys.index(style) + 1) % len(keys)]
    except ValueError:
        return keys[0]
