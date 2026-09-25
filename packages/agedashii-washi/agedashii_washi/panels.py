"""Per-surface panel layout: order, visibility, width and height.

The app could already reorder and hide the six panels on Home, and set a
width on machine widgets. Nothing else could be touched - "there's no way to
resize any components on any page".

This generalises that. A *surface* is one scrollable stack of cards, named
by a dotted key so a page's tabs are separate surfaces:

    home             the home page
    weather          the Weather tab
    wat2do           the Wat2Do page
    foodzen.grocery  the Grocery tab inside FoodZen
    ...

Each surface declares its panels here, in shipped order. Settings edits the
preferences; `layout()` applies them at build time. A page hands over a dict
of builders keyed the same way and gets a laid-out control back, without
knowing anything about preferences.

Widths are responsive by design. A narrow card at 400px is unreadable, so
a width only takes effect from tablet size up (`md`); on a phone
everything is full width whatever is set. That is why these are Flet
column spans rather than pixels or a percentage.

Heights are not responsive that way - they apply at every width, phone
included, because a fixed height with its own scroll costs nothing a
narrow screen cannot afford, unlike two cards trying to share one row.
See `_sized()` for the full reasoning.

There are two ways to edit either: Settings > Card layout, a list with
arrows and a width/height slider each, and - since she asked to "take any
page and resize the component boxes/frames and organize them and then
save that view" - an edit mode on the page itself, below. Both write the
same preferences.

Width is a genuinely custom choice, not a menu of three presets: "are you
saying i can't just set a custom length?" - no. See the COLUMNS block
below for why 60 columns and not the ResponsiveRow default of 12, or a
flat 100 (percent).
"""
import math

# Column spans, the Flet ResponsiveRow convention - 60 of them, not the
# usual 12. A width is meant to be an actually custom choice, which a grid
# of three presets never was; 60 rather than a literal percentage (100)
# because 60 divides evenly by 2, 3, 4, 5, 6, 10, 12, 15, 20 and 30, so
# every common fraction lands on a whole number of columns - half is 30, a
# third is 20, three of them fill the row exactly. 100 does not have that
# property: a third of 100 is 33, and three of them leave a column spare.
# That class of off-by-one is exactly what makes a "custom" layout feel
# broken instead of deliberate, and it is why 60 - not 12, not 100 - is
# the grid, while still giving just under 1.7% resolution per column,
# finer than anyone drags a slider by hand.
COLUMNS = 60
MIN_WIDTH, MAX_WIDTH = 1, COLUMNS
DEFAULT_WIDTH = COLUMNS

# The common fractions, in columns - every one of them a whole number,
# which is the point of 60. These are snap points for the settings slider
# and the fast taps on the resize grip, never the only reachable widths:
# the slider's own range and the grip's drag both reach every column
# between them.
COMMON_WIDTHS = (COLUMNS, COLUMNS * 3 // 4, COLUMNS * 2 // 3, COLUMNS // 2,
                 COLUMNS // 3, COLUMNS // 4, COLUMNS // 5, COLUMNS // 6)
# = (60, 45, 40, 30, 20, 15, 12, 10) = full, 3/4, 2/3, 1/2, 1/3, 1/4, 1/5, 1/6

# How close a slider release has to land to a common fraction to snap
# there - assistance, not a constraint. She has to be able to stop
# *between* the stops, so this is a small fraction of the 60-wide track,
# never `divisions` set to the stop count, which would pull *every*
# position on the track to its nearest stop and remove the custom part.
WIDTH_SNAP_TOLERANCE = 2

# What `panel_width` used to store, before it held a plain span: a layout
# saved by an older build named one of three widths instead of a number.
# Read once on the way in, in `prefs.panel_width`, and mapped to the span
# it meant, so nothing already saved is lost.
LEGACY_WIDTH_NAMES = {"full": COLUMNS, "half": COLUMNS // 2, "third": COLUMNS // 3}


def width_label(span):
    """'Full - 100%', or 'N% - a/b' with the fraction reduced to its lowest terms."""
    span = max(MIN_WIDTH, min(MAX_WIDTH, int(span)))
    pct = round(span / COLUMNS * 100)
    if span == COLUMNS:
        return "Full - 100%"
    g = math.gcd(span, COLUMNS)
    return f"{pct}% - {span // g}/{COLUMNS // g}"


def snap_width(span):
    """`span` pulled to the nearest common fraction, if it is close enough.

    NOT called on any user gesture any more. Both callers - the settings
    slider's release and the edit-mode grip's release - used to run a drag
    through this, which quietly overrode a deliberate custom width with a
    tidy one. Kept because it is the right helper if snapping is ever made
    opt-in, and it is covered by tests.

    Call this once, at the end of a gesture - a slider's `on_change_end`,
    a drag's `on_grip_end` - never while a drag or slider is still moving,
    so the motion itself feels continuous and only the landing spot gets
    pulled the last couple of columns onto a tidy fraction. Anything
    further than `WIDTH_SNAP_TOLERANCE` from every stop is left exactly
    where she put it.
    """
    span = max(MIN_WIDTH, min(MAX_WIDTH, int(span)))
    nearest = min(COMMON_WIDTHS, key=lambda s: abs(s - span))
    return nearest if abs(nearest - span) <= WIDTH_SNAP_TOLERANCE else span


# A card's height, alongside its width - "stack things and make them
# longer or shorter... so I could stack smaller components closer
# together and larger components on certain sides". Also a real range she
# sets, not a handful of named steps, for the same reason width is.
# `auto` is 0, a real value at the bottom of the range rather than a
# separate hidden default bolted on beside it, so a card she has not
# touched behaves exactly as it always did.
#
# The unit is a 100px cell because that is what her website's GridStack
# layout already calls `h` - the mental model she already has. Not a
# percentage the way width is, because a page has no fixed "100% of
# height" for a fraction to be relative to - height is measured in cells,
# width in columns, because those are the two units each one actually has.
HEIGHT_CELL_PX = 100
MIN_HEIGHT, MAX_HEIGHT = 0, 8
DEFAULT_HEIGHT = 0


def height_label(cells):
    """'Auto' at the floor, else 'NNNpx - N cells'."""
    cells = max(MIN_HEIGHT, min(MAX_HEIGHT, int(cells)))
    if cells <= 0:
        return "Auto"
    return f"{cells * HEIGHT_CELL_PX}px - {cells} cell{'' if cells == 1 else 's'}"


# surface key -> [(panel key, label), ...] in shipped order.
SURFACES = {
    "home": [
        ("greeting", "Greeting & date"), ("stats", "Stat strip"),
        ("weather", "Weather"), ("nowplaying", "Now playing"),
        ("machines", "Machine status"), ("links", "Quick links"),
        ("actions", "Quick actions"),
    ],
    "weather": [
        ("header", "Location picker & rain"), ("map", "Bay Area map"),
        ("hero", "Current conditions"), ("forecast", "3-day forecast"),
        ("regional", "All spots"),
    ],
    "weather.explore": [
        ("search", "Place search"), ("cats", "POI categories"),
        ("map", "Map"), ("point", "Tapped point"), ("results", "Nearby POIs"),
    ],
    "wat2do": [
        ("date", "Date navigation"), ("checklist", "Checklist"),
        ("vitals", "Pain & spoons"), ("weather", "Weather call"),
        ("out", "Get out"), ("today", "Today"),
    ],
    "foodzen.grocery": [
        ("templates", "List templates"), ("list", "Grocery list"),
        ("browse", "Browse"), ("need", "Need"),
    ],
    "foodzen.meals": [
        ("pantry", "Pantry"), ("recipes", "Recipe vault"),
        ("mealplan", "Meal plan"),
    ],
    "foodzen.expenses": [
        ("receipt", "Import receipt"), ("spending", "Spending"),
        ("radar", "Freq radar"),
    ],
    "trips.selekt": [
        ("planner", "Plan a route"), ("plannermap", "Planner map"),
        ("turns", "Turn by turn"), ("builder", "Trip builder"),
        ("cards", "Trip cards"),
    ],
    "trips.produce": [
        ("picker", "Route picker"), ("map", "Route map"),
        ("waypoints", "Waypoints"), ("meta", "Forks & stacking"),
        ("report", "Trip report"),
    ],
    "trips.rides": [
        ("guidance", "Follow a route"), ("upload", "Upload GPX"),
        ("list", "Recorded rides"), ("viewer", "Ride replay"),
    ],
    "trips.xnav": [
        ("live", "Live GPS & recording"), ("route", "Route picker"),
        ("next", "Next turn"), ("pois", "POI overlays"),
        ("map", "Map"), ("elevation", "Elevation profile"),
        ("stats", "Route stats"), ("maneuvers", "Turn-by-turn"),
    ],
    "lab.machines": [
        ("portal", "Portal & addresses"), ("machines", "Machine cards"),
    ],
    "lab.services": [
        ("reachability", "LAN reachability"), ("control", "Control a service"),
        ("listening", "Listening ports"),
    ],
    "lab.docker": [
        ("summary", "Containers summary"), ("containers", "Containers by host"),
    ],
    "extras.projects": [
        ("search", "Search & count"), ("repos", "Repositories"),
    ],
    "garage.home": [
        ("bikes", "Bike picker"), ("stats", "Stats"),
        ("detail", "Selected bike"),
    ],
    "garage.mechanic": [
        ("log", "Quick log"), ("fleet", "The fleet"), ("note", "Website note"),
    ],
    "garage.flipper": [
        ("search", "Search & scan"), ("listings", "Scored listings"),
    ],
}

SURFACE_LABELS = {
    "home": "Home", "weather": "Weather - Bay Area",
    "weather.explore": "Weather - Explore", "wat2do": "Wat2Do",
    "foodzen.grocery": "FoodZen - Grocery", "foodzen.meals": "FoodZen - Meals",
    "foodzen.expenses": "FoodZen - Expenses",
    "trips.selekt": "Trips - Selekt", "trips.produce": "Trips - Produce",
    "trips.rides": "Trips - Rides", "trips.xnav": "Trips - xNav",
    "lab.machines": "Lab - Machines", "lab.services": "Lab - Services",
    "lab.docker": "Lab - Docker", "extras.projects": "Extras - Projects",
    "garage.home": "Garage - Home", "garage.mechanic": "Garage - Mechanic",
    "garage.flipper": "Garage - Flipper",
}


def panel_keys(surface):
    return [k for k, _ in SURFACES.get(surface, [])]


def panel_labels(surface):
    return dict(SURFACES.get(surface, []))


# ------------------------------------------------------------- edit mode
# The website's answer to "let me arrange this page" is a GridStack with
# `staticGrid: true` until you press Customize. This is the same idea for
# the app: one app-wide mode, off by default, that turns every card stack
# into something you can drag and resize *on the page* instead of from a
# list in Settings.
#
# Deliberately NOT GridStack's free x/y grid. The app is used on a phone;
# absolute positions that read well at 1400px are unusable at 400px, and
# the order + width model already collapses to one column on its own.
#
# App-wide rather than per-surface because the entry point is in the
# topbar, which is rebuilt once per page render while a tab switch rebuilds
# only the body: a per-surface flag would go stale the moment she changed
# tab, and she would be editing a stack she is no longer looking at.
#
# No "dragging" slot any more. That used to track which card a hand-rolled
# Draggable had picked up, for a DragTarget elsewhere to read back on drop.
# ReorderableListView keeps that state itself - Flutter's, not ours - and
# only ever hands `layout()` a finished (old_index, new_index) pair.
EDIT = {"on": False, "surface": None}


def editing():
    """Whether the whole app is in arrange-the-cards mode."""
    return bool(EDIT["on"])


def set_editing(on):
    EDIT["on"] = bool(on)
    return EDIT["on"]


def begin_render():
    """Called by the shell before it builds a page body.

    Clears the "this page has a card stack" flag that `layout()` sets, so
    the topbar can leave Customize out on a page that has nothing to
    arrange - a button that does nothing is what the dead search icon was.
    """
    EDIT["surface"] = None


def has_surface():
    return EDIT["surface"] is not None


def layout(surface, builders, empty_hint=None):
    """Build `surface` from `builders`, honouring the owner's preferences.

    `builders` maps a panel key to a zero-argument callable. A key with no
    builder is skipped rather than raising: a preference saved by an older
    build naming a panel since removed must not take the page down.

    Out of edit mode this returns a scrollable ResponsiveRow, so a
    half-width panel sits beside its neighbour on a wide screen and stacks
    on a phone - unchanged from every build before this one: not one
    control is added, the tree is exactly what it always was, counted and
    compared against the previous build. That matters because these pages
    were only just cut from ~1900 controls to ~240, and a page that takes a
    second to build is a page whose buttons "do nothing".

    In edit mode it returns a `ReorderableListView` instead - Flutter's own
    reorderable list, so a dragged card visibly lifts, the others slide to
    make room, and it settles, rather than the old hand-rolled
    Draggable/DragTarget pair that simply jumped a card to its new index
    with no motion in between.

    That is a real trade, not a free upgrade: `ReorderableListView` is a
    single-column `ListView` under the hood - "in the cross axis, the
    children are required to fill" it, per Flet's own docs - so it cannot
    seat a half- or third-width card beside a neighbour the way
    ResponsiveRow does. Widths are not dropped, though: `prefs.panel_width`
    still drives the resize grip and is still saved and still applies the
    moment she leaves edit mode, which is when ResponsiveRow comes back.
    While *arranging*, every card stacks one to a row - which is already
    exactly what she sees at phone width in the ordinary layout, width
    setting or not, since a column span only takes effect from tablet size
    up. Rearranging one card at a time down a single column is also, if
    anything, an easier drag target than reaching across a two-up grid.
    """
    import flet as ft

    from . import prefs

    EDIT["surface"] = surface
    edit = editing()

    order = prefs.panels(surface)
    labels = panel_labels(surface)
    cells = []
    for key in order:
        make = builders.get(key)
        if make is None:
            continue
        width = prefs.panel_width(surface, key)
        height = prefs.panel_height(surface, key)
        content = _sized(height, make())
        if edit:
            cells.append(_editable(surface, key, labels.get(key, key),
                                   width, height, content))
        else:
            # "xs" (below the `md` breakpoint - a phone) is always the full
            # 60 columns, whatever `width` is set to: a width only takes
            # effect from tablet size up, the same rule it always was, just
            # expressed in the new 60-column grid instead of the old 12.
            cells.append(_animated(ft.Container(
                col={"xs": COLUMNS, "md": max(MIN_WIDTH, min(MAX_WIDTH, width))},
                content=content)))

    if not cells:
        from .widgets import sub
        label = SURFACE_LABELS.get(surface, surface)
        return ft.Column(spacing=10, expand=True, controls=[
            sub(empty_hint or f"Every panel is hidden. Settings > Layout > {label}.",
                11)])

    if edit:
        return ft.Column(spacing=10, expand=True, controls=[
            ft.ReorderableListView(
                spacing=10, expand=True, show_default_drag_handles=False,
                on_reorder=_on_reorder(surface), controls=cells)])

    return ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        ft.ResponsiveRow(columns=COLUMNS, spacing=10, run_spacing=10, controls=cells)])


def _animated(container):
    """A panel that changes size or position should grow, not jump.

    Dragging the width grip, changing a height, or reordering the stack all
    used to teleport the card to its new geometry in a single frame - which
    is the difference between "I resized that" and "the screen flickered".
    Both properties are set on the CONTAINER whose geometry actually
    changes; animating a child instead does nothing, because the child is
    not what Flutter is laying out.

    Routed through motion so it obeys reduce-motion with everything else,
    and so the duration is the same one cards use everywhere in the app
    rather than a number chosen here.
    """
    from . import motion
    return motion.resizable(container)


def _sized(height, content):
    """`content` at its stored height, or untouched if that height is 'auto'.

    'auto' - the default, and every card before this - returns `content`
    exactly as given: no wrapper, no control added, same as width's own
    "nothing costs anything until she asks" rule.

    Unlike width, a fixed height applies at *every* screen size, phone
    included. Width only takes effect from tablet size up because seating
    two half-width cards side by side on a 400px screen is a genuine
    impossibility - there is no room. A fixed height has no equivalent
    wall: a short card with its own scroll costs nothing structurally on
    a phone, and "stack smaller components closer together" is a request
    that matters most on the screen with the least vertical room to begin
    with.

    The wrapper is a fixed-height Container around a scrolling Column,
    never a clip. A height that hid whatever overflowed it - a machine
    list that grew, a checklist that filled in - would lose content with
    nothing on screen to say a row was missing; a height that scrolls
    loses nothing, only ever one swipe further down.
    """
    import flet as ft

    cells = max(MIN_HEIGHT, min(MAX_HEIGHT, int(height or 0)))
    if not cells:
        return content
    return _animated(ft.Container(
        height=cells * HEIGHT_CELL_PX,
        content=ft.Column(expand=True, scroll=ft.ScrollMode.AUTO,
                          controls=[content])))


def _on_reorder(surface):
    """An `on_reorder` handler bound to `surface`.

    `OnReorderEvent` carries `old_index`/`new_index` into the list
    `ReorderableListView` actually drew - the *visible* panels, in
    render order - matching Flet's own documented handler, which pops the
    moved item from `old_index` and re-inserts it at `new_index` with no
    further adjustment. Doing the same over a plain list of panel keys and
    handing the result to `prefs.reorder_visible_panels` keeps that exact
    contract while translating it back into the full, hidden-included
    order the store keeps.

    Guarded the way every other handler in this module is guarded: Flet
    0.86's own example trusts `e.old_index`/`e.new_index` are always both
    present ints, but a raise inside a gesture handler is invisible - the
    list simply stops responding - so a build that hands back something
    this code does not understand is a no-op instead of a dead drag.
    """
    from . import prefs

    def handler(e):
        visible = prefs.panels(surface)
        old_index = getattr(e, "old_index", None)
        new_index = getattr(e, "new_index", None)
        if not isinstance(old_index, int) or not (0 <= old_index < len(visible)):
            return
        if not isinstance(new_index, int):
            return
        moved = visible.pop(old_index)
        visible.insert(max(0, min(len(visible), new_index)), moved)
        if prefs.reorder_visible_panels(surface, visible):
            _apply()

    return handler


# How far the width grip has to travel before a card steps one column (of
# 60). Fixed pixels rather than a fraction of the card, because the card
# does not know how wide it was drawn and a fraction would need a layout
# pass to compute. 8 means the full 1-60 range - the same range the
# settings slider covers - is one deliberate sweep across a card, not a
# gesture that bottoms out after the first few columns.
WIDTH_STEP_PX = 8


def _apply():
    """Repaint the app and persist, after an edit-mode change.

    Repainting goes through `ui.S.rerender`, the shell's own whole-page
    rebuild, because a width change moves a card into a different column
    span and nothing short of rebuilding the ResponsiveRow shows that.

    Saving goes through the same `prefs.save()` Settings uses - there is
    one store, not a second one for this editor. It is fired per change as
    well as on Done, so an app killed mid-arrange has still kept the work.
    """
    from . import prefs
    from agedashii_qi import S

    rerender = getattr(S, "rerender", None)
    if callable(rerender):
        try:
            rerender()
        except Exception:  # noqa: BLE001 - a repaint must not lose the edit
            pass
    store = getattr(S, "prefs", None)
    page = getattr(S, "page", None)
    run_task = getattr(page, "run_task", None)
    if store is None or not callable(run_task):
        return False
    try:
        run_task(prefs.save, store)
        return True
    except Exception:  # noqa: BLE001 - no loop, or a stub page in a harness
        return False


def _editable(surface, key, label, width, height, content):
    """One card, wrapped for edit mode: handle to reorder, pills to resize.

    Reordering goes through `ReorderableDragHandle`, not a hand-rolled
    Draggable/DragTarget pair - the list itself (`layout()`'s
    ReorderableListView) does the picking-up, sliding and dropping; this
    only has to say *which* part of the card may start that gesture. That
    split still matters exactly as much as it did under the old
    mechanism: `ReorderableDragHandle` wraps only the grip icon, not the
    card, because a full-card drag region competes with the enclosing
    scrollable for the same vertical gesture and wins it - the surface
    would stop scrolling in edit mode, and on a phone with eight cards
    most of them would be out of reach.

    Resizing is unchanged by any of this - it was never part of the
    reorder mechanism, only living in the same header. Width is one
    control answering to two gestures, because only one of them is
    certain. `on_horizontal_drag_*` is a single-axis recognizer, so
    Flutter's arena hands it the horizontal drag without ever contending
    with the vertical scroll (or, now, the vertical reorder drag) this
    stack sits in; `on_pan_*` would contend, and on a diagonal the other
    gesture wins. A drag reaches every column from 1 to 60; a tap instead
    cycles through the common fractions - full, 3/4, 2/3, half, ... - so a
    usual width is reachable with no drag at all, on a phone, and on any
    build where the drag turns out not to arrive. Height is tap-only for
    the same contention reason, one axis further: there is no direction a
    height drag could use that would not collide with either the list's
    scroll or its own reorder drag, both already vertical.

    The width drag is measured from *positions*, never by summing deltas,
    and it commits once at the end. Two reasons, both structural rather
    than stylistic: Flet 0.86 documents `DragUpdateEvent.local_delta` as
    the movement "since the start of the drag" while calling the payload
    incremental, so summing it would be wrong under one of those two
    readings; and a width change rebuilds the page, which destroys this
    very control, so a gesture that tried to step live would be cancelled
    by its own first step. The landing column is passed through
    `snap_width` before it is saved, so a drag that stops close to a
    common fraction settles there instead of one column short or long.
    """
    import flet as ft

    from . import prefs
    from agedashii_qi import PAL, RAD
    from .widgets import PAD, BRD

    p = PAL()

    # Where the grip was picked up and where it is now, in whatever
    # coordinate space the event offers.
    span = {"from": None, "to": None}

    def on_grip_start(e):
        span["from"] = span["to"] = _x(e)

    def on_grip_move(e):
        x = _x(e)
        if x is None:
            return
        if span["from"] is None:
            span["from"] = x
        span["to"] = x

    def on_grip_end(e):
        start, end = span["from"], span["to"]
        span["from"] = span["to"] = None
        if start is None or end is None:
            return
        steps = int((end - start) / WIDTH_STEP_PX)
        if not steps:
            return
        # Rightwards is wider: a larger span is now a larger number of
        # columns directly (unlike the old three-name scheme, there is no
        # index to walk backwards through), so the sign of the drag is the
        # sign of the delta, unchanged. No wrapping on a drag: dragging
        # hard right should stop at 60/60 (full), not come out the other
        # side back down near 1/60.
        target = prefs.cycle_panel_width(surface, key, steps, wrap=False)
        if target is not None:
            # The landing column is kept exactly as dragged. This used to
            # call snap_width() and save the tidied value instead, which
            # meant a deliberate 22 became 20 on release. Her words: "i
            # still can't customize the sizing its like 'your way' or
            # nothing". Tapping the grip still jumps between the common
            # fractions, which is the fast path; the drag is now literal.
            _apply()

    def on_grip_tap(e):
        if prefs.cycle_panel_width_preset(surface, key, 1):
            _apply()

    def on_height_tap(e):
        if prefs.cycle_panel_height(surface, key, 1):
            _apply()

    grip = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
        on_tap=on_grip_tap,
        on_horizontal_drag_start=on_grip_start,
        on_horizontal_drag_update=on_grip_move,
        on_horizontal_drag_end=on_grip_end,
        content=ft.Container(
            padding=PAD(8, 3), border_radius=RAD(6), ink=True,
            bgcolor=ft.Colors.with_opacity(0.16, p["accent"]),
            tooltip=f"{width_label(width)} - drag sideways for any width, "
                    f"or tap to jump to a common one",
            content=ft.Text(width_label(width), size=11, color=p["accent"])))

    height_pill = ft.GestureDetector(
        on_tap=on_height_tap,
        content=ft.Container(
            padding=PAD(8, 3), border_radius=RAD(6), ink=True,
            bgcolor=ft.Colors.with_opacity(0.16, p["accent"]),
            tooltip=f"{height_label(height)} height - tap to cycle",
            content=ft.Text(height_label(height), size=11, color=p["accent"])))

    handle = ft.ReorderableDragHandle(
        mouse_cursor=ft.MouseCursor.GRAB,
        content=ft.Icon(ft.Icons.DRAG_INDICATOR, size=18, color=p["accent"],
                        tooltip=f"Drag {label} to move it"))

    header = ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        handle,
                        ft.Text(label, size=11, color=p["text3"], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        height_pill,
                        grip,
                    ])

    return ft.Container(
        padding=PAD(6, 6), border_radius=RAD(10),
        border=BRD(ft.Colors.with_opacity(0.55, p["accent"]), 1),
        content=ft.Column(spacing=6, controls=[header, content]))


def _x(e):
    """Where the pointer is, horizontally, or None if the event cannot say.

    `local_position` is the grip's own coordinate space and
    `global_position` the window's; either answers "how far did the finger
    travel" as long as both ends of one drag are read from the same one,
    which they are. None rather than 0.0 for an event carrying neither, so
    a shape this build does not understand cancels the resize instead of
    reading as "did not move" - and a raise inside a gesture handler is
    invisible, the card simply stops responding.
    """
    for name in ("local_position", "global_position"):
        x = getattr(getattr(e, name, None), "x", None)
        if isinstance(x, (int, float)):
            return float(x)
    return None
