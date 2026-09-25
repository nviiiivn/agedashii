"""Home - the overview this app opens on.

Built from the shared cards in ``agedashii_washi.homecards``: the greeting,
the quick links, a row of quick actions and - where there is something
playing - the music line. A weather shortcut taps through to this app's
Weather page.

Her brief for these: "the quick links and quick actions and
'welcome'/'good morning' shit, a weather shortcut, and maybe a music
component". Nothing more for now; what this app *should* eventually show
is a later pass.

Every card goes through ``panels.layout``, so each one can be reordered
and hidden per app without any new code.
"""
import flet as ft

from agedashii_washi import homecards, panels
from agedashii_washi.ui_compat import S


def _goto(key):
    """Navigate, if the shell is there to do it.

    Returns None with no shell - and a tile with no handler is not drawn,
    rather than drawn dead.
    """
    jump = getattr(S, "goto", None)
    if not callable(jump):
        return None
    return lambda e: jump(key)


def build(page):
    def go(key):
        return _goto(key)

    def open_url(url):
        page.launch_url(url)

    cards = {
        "greeting": lambda: homecards.greeting(),
        "links": lambda: homecards.links(on_open=open_url),
        "actions": lambda: homecards.actions([
        ("Today", ft.Icons.CHECK_CIRCLE, go("wat2do")),
        ("Future Finds", ft.Icons.EXPLORE, go("future")),
        ("Weather", ft.Icons.CLOUD, go("weather")),
        ]),
        "nowplaying": homecards.nowplaying,
        "weather": lambda: homecards.weather_shortcut(
            on_open=(lambda: S.goto("weather"))
            if callable(getattr(S, "goto", None)) else None),
    }
    return panels.layout("home", cards)
