"""xNav - turn-by-turn while riding. Not built yet.

This is the one you FOLLOW. vNav is the one you look at: planning a route,
replaying a ride, seeing where you went, on a desktop or sitting still.
Her words: *"it's great when you're on the desktop or sitting on the bike
or whatever, but when you're on the road you want a normal overlay like any
other fucking maps navigation app."*

The brief, the two hard requirements and the prior art are in
``apps/moto/XNAV.md``. The short version of why this is a page and not a
patch on vNav:

* it works with no signal, no exception. vNav routes through Valhalla and
  colours by speed limit through Overpass, both live third parties that
  refuse rather than queue - which on the road is the moment you needed
  them. The offline graph is already on the device.
* navigation and telemetry are paired. The ride log should say what the
  route was and what you actually did, together.

What this page does today is say so. A stub that rendered nothing would
read as the app being broken, and one that looked ready would be worse. So
it shows what is already true - whether the offline graph is installed -
and what is still missing.
"""
import flet as ft

from agedashii_washi import routing
from agedashii_washi.ui_compat import PAL, card, sub, title


def build(page):
    p = PAL()
    state = routing.status()
    ready = bool(state.get("ok"))

    def line(text, colour=None):
        return ft.Text(text, size=12.5, color=colour or p["text2"])

    return ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        title("xNav"),
        card(ft.Column(spacing=8, controls=[
            sub("NOT BUILT YET", 10),
            line("Turn-by-turn for the road. vNav - on Trips - is the map "
                 "you look at; this is the one you follow."),
            line("See XNAV.md for the brief and what it has to do."),
        ])),
        card(ft.Column(spacing=8, controls=[
            sub("WHAT IS ALREADY HERE", 10),
            ft.Row(spacing=8, controls=[
                ft.Icon(ft.Icons.CHECK_CIRCLE if ready
                        else ft.Icons.RADIO_BUTTON_UNCHECKED,
                        size=16, color=p["green"] if ready else p["text3"]),
                line(state.get("detail") or ("the offline road graph is installed"
                                             if ready else "no offline graph"),
                     p["text"] if ready else p["text2"]),
            ]),
            line("Routing runs on the device against that graph - about 150 ms "
                 "for a 40 km route, with 21,806 turn restrictions honoured."),
            line("Map tiles come off the device too, over a loopback server."),
        ])),
        card(ft.Column(spacing=8, controls=[
            sub("WHAT IS MISSING", 10),
            line("The guidance layer: the instruction text, noticing you have "
                 "left the route, and re-routing - all with no network."),
            line("Telemetry paired with the route, so a ride says what was "
                 "planned and what actually happened."),
        ])),
    ])
