"""How this works: the wiki, inside the app, for the app.

"each of these independent apps needs their own icons/labels/readme wikis
INSIDE on how to use/settings menu etc..."

The README says all of this and lives in a git checkout, which is no use on
a phone at the side of a road. This is the same facts on screen.

Written as plain sentences and measured numbers. No marketing, no "seamless",
no "blazing fast" - the interesting thing about this app is which parts keep
working with the radio off, and a reader who has been told everything is
amazing cannot tell which those are. Every figure here is one that is
checked somewhere else in the repo:

    38.8 MB / 11,716 map tiles, zoom 10-15   assets/maps/bayarea.mbtiles
    86 MB / 725,626 nodes routing graph      agedashii_washi/routing.py
    ~180 ms to route 40 km on a Pi           the same
    3 s server probe, 20 s outbox watcher    config.probe, main.SYNC_POLL_SECS
    5 attempts before a write parks          agedashii_washi/store.py

Nothing here is a claim about the server. Whether the server is reachable
right now is a question with a live answer, and it is asked and answered in
Settings > Diagnostics, one tap away.
"""
import flet as ft

from agedashii_qi import PAL, RAD, S
from agedashii_washi.widgets import BRD, PAD, card, sub, title

#: (icon, heading, [paragraph, ...]) - the whole document, as data, so the
#: drawing code below is four lines and a test can read the text without
#: walking a control tree.
SECTIONS = [
    (ft.Icons.ROUTE, "Trips", [
        "Four tabs. Selekt is the trip library and a two-point planner: tap "
        "the map or type coordinates and it routes between them. Produce is "
        "the route editor - waypoints, forks, stacking, and the trip report. "
        "Rides holds recorded GPX: upload one, list them, replay one with a "
        "scrubber. xNav is the same live map XNAV carries, kept here because "
        "planning a route and then following it is one job.",
        "A distance that has not been routed shows an em dash, never 0. If "
        "you see a number, something measured it.",
    ]),
    (ft.Icons.TWO_WHEELER, "Garage", [
        "Home is one bike at a time: its aggregate stats and its Dashboard, "
        "Mileage, Services, Expenses, Spec and Notes sections, reading and "
        "writing the same records the website does. Mechanic is a quick log "
        "for a service or a fill-up. Flipper runs the server's Craigslist "
        "and Reddit scanner and scores what it finds.",
        "The bikes come from the server. With none reachable and nothing "
        "cached, this says so - it does not fill the page with example "
        "motorcycles, which it used to.",
    ]),
    (ft.Icons.NAVIGATION, "XNAV", [
        "The live map with the whole screen: GPS position, speed, track "
        "recording, and turn-by-turn guidance along a route with speed "
        "limits, curve warnings, an elevation profile and POI overlays.",
        "Record starts a track. The points come from the GPS receiver, not "
        "from the network, so recording works anywhere the phone can see "
        "sky. Stop says how many points and how far.",
    ]),
    (ft.Icons.SIGNAL_CELLULAR_OFF, "With no signal at all", [
        "The map draws from an MBTiles archive on the device - 11,716 tiles, "
        "zoom 10 to 15, about 39 MB - served to the map widget over loopback "
        "by a small HTTP server inside the app. Nothing leaves the phone. "
        "With no archive installed the map falls back to "
        "tile.openstreetmap.org and the chip in the corner says 'online "
        "maps' instead of 'offline maps'.",
        "Routing is done on the device by an A* search over a packed OSM "
        "graph - 86 MB, 725,626 nodes for the Bay Area, about 180 ms for a "
        "40 km route on a Raspberry Pi. Pure Python, no C extension. With no "
        "graph installed, routing falls back to the server's Valhalla and "
        "says on screen that it did.",
        "Themes, the corner radius, the glow, the CRT overlay, the card "
        "layouts, the units and every other preference are stored on the "
        "device and never asked of anybody.",
        "Track recording, the trip library, the garage records and the rides "
        "you already have are all readable, because the app answers reads "
        "from its own copy first, online or not.",
    ]),
    (ft.Icons.CLOUD_UPLOAD, "What queues, and what happens to it", [
        "Everything that belongs to the dashbored server - trips, routes, "
        "rides, and every garage mileage, service, expense, spec sheet and "
        "note - goes through a SQLite cache and an outbox. A write is "
        "applied to the cache and queued in the same transaction, so the "
        "screen updates with no signal and the queued change survives the "
        "app being killed.",
        "The chip in the topbar appears only when something is waiting, and "
        "says what. It drains on a tap, when the app comes back to the "
        "foreground, and whenever the 20-second watcher notices the server "
        "answering again. Nothing waiting means no chip: an 'all synced' "
        "badge would be a claim about the server that a queue cannot make.",
        "A write the server refuses five times parks instead of retrying "
        "forever. Tapping the chip un-parks those and tries again, which is "
        "the way back for something that burned its attempts while the "
        "server was busy returning errors. Being offline never costs a "
        "queued write an attempt.",
    ]),
    (ft.Icons.BLOCK, "What refuses instead of queueing, and why", [
        "Valhalla routing, Overpass speed limits and POIs, and the elevation "
        "lookup are live third-party services. There is nothing to replay "
        "against them later: a route request answered next Tuesday is not "
        "the answer to the question you asked today, and queueing one would "
        "be promising something the app cannot deliver.",
        "So they refuse, and say which one failed and why, rather than "
        "sitting in a queue. The offline router and the offline tiles are "
        "the answer to the same need, and they are on the device.",
    ]),
    (ft.Icons.FIBER_MANUAL_RECORD, "Recording a ride", [
        "XNAV, or Trips > xNav. Record starts the track; the map draws it as "
        "you go and the three counters are distance, elapsed time and point "
        "count. Stop ends it and reports the total.",
        "The Android build asks for background location and a foreground "
        "service on purpose: without them a ride stops recording the moment "
        "the screen locks, which is most of every ride. GPS hardware is "
        "declared not required, so a device without it still installs and "
        "still opens the library, the garage and the router.",
        "A finished track is saved to the server through the same outbox as "
        "everything else, so stopping a ride in a canyon is fine.",
    ]),
    (ft.Icons.TUNE, "Settings", [
        "Server address with a Check button that reports exactly what came "
        "back - 'blocked by auth', 'blocked by Cloudflare at the edge' and "
        "'unreachable' need completely different fixes, so they are not all "
        "called 'offline'.",
        "Theme across all 24 palettes, and it is kept for next launch. "
        "Corner rounding, accent glow, the CRT overlay and its intensity.",
        "Bottom bar on or off, and the menu button shown or hidden. One of "
        "the two always stays on, because an app with neither has no way "
        "back to this page.",
        "Units: miles or kilometres, which reaches every distance, speed and "
        "climb figure on every surface. Records are stored as the server "
        "sent them; this converts on the way to the screen.",
    ]),
]


def build(page):
    """The Help surface. Reads nothing, asks nobody, cannot fail."""
    p = PAL()

    def section(icon, heading, paragraphs):
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[ft.Icon(icon, size=17, color=p["accent"]),
                             title(heading, 15)]),
            *[ft.Text(text, size=12, color=p["text2"], selectable=True)
              for text in paragraphs],
        ]))

    def back(e):
        goto = getattr(S, "goto", None)
        if callable(goto):
            goto("settings")

    header = ft.Container(
        padding=PAD(12, 14), border_radius=RAD(12),
        bgcolor=p["surface"], border=BRD(p["border"], 1),
        content=ft.Column(spacing=6, controls=[
            title("How Agedashii Moto works", 17),
            sub("Trips, Garage and XNAV, and which parts of them need a "
                "server. Everything below is either measured or checked by "
                "a test - nothing here is a promise.", 11),
        ]))

    return ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        header,
        *[section(*s) for s in SECTIONS],
        ft.Container(
            padding=PAD(10, 12), border_radius=RAD(9), ink=True, on_click=back,
            ink_color=ft.Colors.with_opacity(0.18, p["accent"]),
            animate=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
            border=BRD(p["border"], 1), tooltip="Back to Settings",
            content=ft.Row(spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(ft.Icons.ARROW_BACK, size=16,
                                       color=p["text2"]),
                               ft.Text("Back to Settings", size=12,
                                       color=p["text2"]),
                           ])),
    ])
