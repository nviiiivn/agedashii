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


def layout(surface, builders, empty_hint=None):
    """Build `surface` from `builders`, honouring the owner's preferences.

    `builders` maps a panel key to a zero-argument callable. A key with no
    builder is skipped rather than raising: a preference saved by an older
    build naming a panel since removed must not take the page down.

    Returns a scrollable ResponsiveRow, so a half-width panel sits beside
    its neighbour on a wide screen and stacks on a phone.
    """
    import flet as ft

    from . import prefs

    cells = []
    for key in prefs.panels(surface):
        make = builders.get(key)
        if make is None:
            continue
        span = WIDTHS.get(prefs.panel_width(surface, key), 12)
        cells.append(ft.Container(col={"xs": 12, "md": span}, content=make()))

    if not cells:
        from .ui import sub
        label = SURFACE_LABELS.get(surface, surface)
        return ft.Column(spacing=10, expand=True, controls=[
            sub(empty_hint or f"Every panel is hidden. Settings > Layout > {label}.",
                11)])

    return ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        ft.ResponsiveRow(columns=12, spacing=10, run_spacing=10, controls=cells)])
