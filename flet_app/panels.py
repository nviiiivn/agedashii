"""Per-surface panel layout: order, visibility and width.

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

Widths are responsive by design. A third-width card at 400px is unreadable,
so a width only takes effect from tablet size up (`md`); on a phone
everything is full width whatever is set. That is why these are Flet column
spans rather than pixels.

There are two ways to edit that: Settings > Card layout, a list with
arrows and width buttons, and - since she asked to "take any page and
resize the component boxes/frames and organize them and then save that
view" - an edit mode on the page itself, below. Both write the same
preferences.
"""

# Column spans out of 12, the Flet ResponsiveRow convention.
WIDTHS = {"full": 12, "half": 6, "third": 4}
WIDTH_ORDER = ("full", "half", "third")
WIDTH_LABELS = {"full": "Full", "half": "1/2", "third": "1/3"}
DEFAULT_WIDTH = "full"

# surface key -> [(panel key, label), ...] in shipped order.
SURFACES = {
    "home": [
        ("greeting", "Greeting & date"), ("stats", "Stat strip"),
        ("weather", "Weather"), ("nowplaying", "Now playing"),
        ("machines", "Machine status"), ("links", "Quick links"),
        ("actions", "Quick actions"), ("wat2do", "Wat2Do progress"),
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
EDIT = {"on": False, "surface": None, "dragging": None}


def editing():
    """Whether the whole app is in arrange-the-cards mode."""
    return bool(EDIT["on"])


def set_editing(on):
    EDIT["on"] = bool(on)
    if not EDIT["on"]:
        EDIT["dragging"] = None
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

    Returns a scrollable ResponsiveRow, so a half-width panel sits beside
    its neighbour on a wide screen and stacks on a phone.

    In edit mode each cell is wrapped in drag-and-drop and a width grip.
    Out of edit mode - the default, and every build before this one - not
    one control is added: the tree is exactly what it always was, counted
    and compared against the previous build. That matters because these
    pages were only just cut from ~1900 controls to ~240, and a page that
    takes a second to build is a page whose buttons "do nothing".
    """
    import flet as ft

    from . import prefs

    EDIT["surface"] = surface
    edit = editing()

    cells = []
    order = prefs.panels(surface)
    labels = panel_labels(surface)
    for key in order:
        make = builders.get(key)
        if make is None:
            continue
        width = prefs.panel_width(surface, key)
        content = make()
        if edit:
            content = _editable(surface, key, labels.get(key, key), width, content)
        cells.append(ft.Container(col={"xs": 12, "md": WIDTHS.get(width, 12)},
                                  content=content))

    if not cells:
        from .ui import sub
        label = SURFACE_LABELS.get(surface, surface)
        return ft.Column(spacing=10, expand=True, controls=[
            sub(empty_hint or f"Every panel is hidden. Settings > Layout > {label}.",
                11)])

    return ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        ft.ResponsiveRow(columns=12, spacing=10, run_spacing=10, controls=cells)])


# How far the width grip has to travel before a card steps to the next
# width. Fixed pixels rather than a fraction of the card, because the card
# does not know how wide it was drawn and a fraction would need a layout
# pass to compute. 48 is roughly a thumb's width of deliberate movement.
WIDTH_STEP_PX = 48


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
    from .ui import S

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


def _editable(surface, key, label, width, content):
    """One card, wrapped for edit mode: drag to reorder, grip to resize.

    Every card is a DragTarget, so any card can be dropped on any other,
    but only the little grip icon in its header is the Draggable. That
    split is not cosmetic: Flutter's Draggable competes with the enclosing
    scrollable for the same vertical drag and wins it, so a card that was
    draggable edge to edge would make the surface unscrollable in edit
    mode - and she would be unable to reach the cards she wanted to move.
    A handle the size of an icon leaves every other pixel to the scroll.

    Which card is moving is kept in `EDIT["dragging"]` rather than looked
    up from the event's `src_id`: `page.get_control()` needs a real page,
    and this has to build under the stub pages the tests use.

    Resizing is one control answering to two gestures, because only one of
    them is certain. `on_horizontal_drag_*` is a single-axis recognizer, so
    Flutter's arena hands it the horizontal drag without ever contending
    with the vertical scroll this stack sits in; `on_pan_*` would contend,
    and on a diagonal the scroll wins. A tap cycles full -> 1/2 -> 1/3 ->
    full, so the width is reachable with no drag at all - on a phone, and
    on any build where the drag turns out not to arrive.

    The drag is measured from *positions*, never by summing deltas, and it
    commits once at the end. Two reasons, both structural rather than
    stylistic: Flet 0.86 documents `DragUpdateEvent.local_delta` as the
    movement "since the start of the drag" while calling the payload
    incremental, so summing it would be wrong under one of those two
    readings; and a width change rebuilds the page, which destroys this
    very control, so a gesture that tried to step live would be cancelled
    by its own first step.
    """
    import flet as ft

    from . import prefs
    from .ui import PAL, PAD, RAD, BRD

    p = PAL()
    group = f"panels:{surface}"

    def on_drag_start(e):
        EDIT["dragging"] = key

    def on_accept(e):
        src = EDIT["dragging"]
        EDIT["dragging"] = None
        if src and prefs.move_panel_onto(surface, src, key):
            _apply()

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
        # Rightwards is wider. WIDTH_ORDER runs full -> half -> third, so
        # "wider" is backwards through it. No wrapping on a drag: dragging
        # hard right should stop at full, not come out at a third.
        if prefs.cycle_panel_width(surface, key, -steps, wrap=False):
            _apply()

    def on_grip_tap(e):
        if prefs.cycle_panel_width(surface, key, 1):
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
            tooltip=f"{WIDTH_LABELS.get(width, width)} width - "
                    f"drag sideways, or tap to cycle",
            content=ft.Text(WIDTH_LABELS.get(width, width), size=11,
                            color=p["accent"])))

    handle = ft.Draggable(
        group=group, on_drag_start=on_drag_start,
        content=ft.Icon(ft.Icons.DRAG_INDICATOR, size=18, color=p["accent"],
                        tooltip=f"Drag {label} onto another card to move it"))

    header = ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        handle,
                        ft.Text(label, size=11, color=p["text3"], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        grip,
                    ])

    return ft.DragTarget(
        group=group, on_accept=on_accept,
        content=ft.Container(
            padding=PAD(6, 6), border_radius=RAD(10),
            border=BRD(ft.Colors.with_opacity(0.55, p["accent"]), 1),
            content=ft.Column(spacing=6, controls=[header, content])))


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
