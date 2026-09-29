"""Agedashii Navi - turn-by-turn and telemetry for a motorcycle, offline.

Its own app, on her instruction: "WE CAN push this into xnav after. but at the
moment i want it to be its own app FIRST okay???? i don't want to have to push
everything else alongside it everytime". So nothing here imports Moto, and this
builds and installs on its own.

FIVE SURFACES, ALL FIVE ON THE BAR

    Ride      the map and the HUD - where she spends the whole journey
    Where to  destinations: the graph, coordinates, saved routes, GPX
    Around    what is nearby, when there is signal
    Rides     what this phone recorded, and the exports
    Settings  the choices, and an honest account of the sensors

There is no hamburger. Every surface is on the bar, Help hangs under Settings
the way a manual lives in a drawer, and a drawer carrying a second copy of the
bar is the mistake the dashboard already made once. Five is the most a Material
NavigationBar holds without the labels colliding, which is exactly how many
there are.

WHAT THE SHELL OWNS AND WHAT IT DOES NOT

It owns the theme, the bar, and which surface is showing. It does NOT own the
ride: the recording, the route and the last fix live in `live.py` at module
level, so switching surfaces, changing a setting or repainting for a theme
cannot end a ride she is halfway through. `page._navi_rerender` is how a page
asks for a full rebuild; `ride.repaint()` is how anything asks for just the
readouts.

Launch always lands on Ride. Whatever she opened the app for, it was the map.
"""
import traceback

import flet as ft

from agedashii_washi import config
from agedashii_washi import prefs as wprefs

from agedashii_navi import (awake, boot, character, live, prefs, sensors,
                            theme, ui)
from agedashii_navi.pages import diagnostics as diagnostics_page
from agedashii_navi.pages import help as help_page
from agedashii_navi.pages import log as log_page
from agedashii_navi.pages import ride as ride_page
from agedashii_navi.pages import routes as routes_page
from agedashii_navi.pages import search as search_page
from agedashii_navi.pages import settings as settings_page

PAGES = ["ride", "routes", "search", "log", "settings", "help", "diagnostics"]
#: Help is not on the bar - see the module docstring.
BAR_PAGES = ["ride", "routes", "search", "log", "settings"]
#: The drawer's rows. The diagnostics page is not one: it is the last door in
#: Settings, and a drawer row for it would be one more thing to scan past.
DRAWER_PAGES = ["ride", "routes", "search", "log", "settings", "help"]
#: The surfaces that are a search, where opening one gets his line - her
#: "WANT 2" on the search button. Only on arriving from elsewhere, never on a
#: repaint of the one she is on.
TAP_PAGES = ("routes", "search")
#: Where Back goes from a surface that lives under another one. Anything not
#: here, Back leaves the app, exactly as before.
PARENT = {"diagnostics": "settings"}
#: Whether Back goes up a level (see `on_back`). Off until the Settings doors
#: land and it has been pressed on a real phone: a Back that Flutter hands to
#: Python and never hears about again is a Back button that does nothing.
UP_ON_BACK = False

LABELS = {"ride": "Ride", "routes": "Where to", "search": "Around",
          "log": "Rides", "settings": "Settings", "help": "How this works",
          "diagnostics": "What this build is getting"}
#: The bar and a drawer row have no room for four words; the page still calls
#: itself "How this works".
NAV_LABELS = dict(LABELS, help="Help", diagnostics="This build")

ICONS = {"ride": ft.Icons.NAVIGATION_OUTLINED,
         "routes": ft.Icons.SIGNPOST_OUTLINED,
         "search": ft.Icons.TRAVEL_EXPLORE_OUTLINED,
         "log": ft.Icons.HISTORY, "settings": ft.Icons.SETTINGS_OUTLINED,
         "help": ft.Icons.HELP_OUTLINE,
         "diagnostics": ft.Icons.MONITOR_HEART_OUTLINED}
ICONS_ON = {"ride": ft.Icons.NAVIGATION, "routes": ft.Icons.SIGNPOST,
            "search": ft.Icons.TRAVEL_EXPLORE, "log": ft.Icons.HISTORY,
            "settings": ft.Icons.SETTINGS, "help": ft.Icons.HELP,
            "diagnostics": ft.Icons.MONITOR_HEART}

#: One line per surface for the drawer, so the menu says what each one is for
#: rather than making her remember. "Where to" and "Around" are the pair most
#: easily confused - one is a destination you can name, the other is a category
#: near you - so their blurbs are written to draw exactly that line.
BLURB = {"ride": "the map, the gauges, recording",
         "routes": "a destination: street, coordinates, saved route, GPX",
         "search": "what is nearby by category - needs signal",
         "log": "rides on this phone, and their exports",
         "settings": "themes, gauges, the bike, the sensors",
         "help": "what each part does, and what needs the network",
         "diagnostics": "maps, routing, sensors, this build"}

_HERE = {"key": "ride"}


async def main(page):
    # Without this the window, the taskbar and alt-tab all say "flet".
    page.title = "Agedashii Navi"
    # Flet's Page defaults to Padding.all(10), which would put a pale border
    # around a map that is supposed to reach the edges of the screen.
    page.padding = 0

    # Persistent storage is a Flet *service*: constructing it inside the page
    # context registers it, and Flet unregisters it again the moment nothing
    # holds a strong reference. `prefs.set_store` is that reference - a local
    # here would be collected and storage would quietly stop working. The other
    # apps park it on `agedashii_qi.S`; Navi does not import Qi.
    store = prefs.set_store(ft.SharedPreferences())
    # The server address, confirmed before the first paint. Only two things in
    # this app use it, but both look silly flashing empty first.
    await config.load(store)
    # Washi's shared preferences, for the outbox and the store.
    await wprefs.load(store)
    # Navi's own: the theme, the units, the bike, the sample rate. Loaded before
    # the first frame so the app never paints in the wrong theme and then
    # corrects itself.
    await prefs.load(store)
    # Pushed across rather than imported: theme.PAL reads this on every build and
    # cannot import prefs, because prefs imports theme.
    theme.S["chrome_alola"] = prefs.alola_chrome()
    theme.apply(page, prefs.theme_key())

    # A phone that sleeps at 60 mph is not a navigation device. A task, not an
    # await: a phone slow to answer must not hold up the first paint. `render`
    # keeps it following the switch after this; see `awake`.
    page.run_task(awake.follow, page, True)

    def on_lifecycle(e):
        """Back to the front: hold the screen again.

        The flag lives on the activity's window, and a window Android rebuilt
        while she was elsewhere comes back without it.
        """
        if e.state == ft.AppLifecycleState.RESUME:
            page.run_task(awake.follow, page, True)

    page.on_app_lifecycle_state_change = on_lifecycle

    async def apply_orientation():
        """Hold the phone the way the mount holds it.

        Android's own rotation lock only freezes whichever way up the phone
        happened to be when she tapped it. On a bike that is not enough: a
        lean into a corner is a rotation as far as the accelerometer is
        concerned, and a screen that flips mid-bend is worse than useless.

        `auto` leaves it to the phone, which is the old behaviour.
        """
        want = prefs.orientation()
        if want == "auto":
            allowed = list(ft.DeviceOrientation)
        elif want == "landscape":
            allowed = [ft.DeviceOrientation.LANDSCAPE_LEFT,
                       ft.DeviceOrientation.LANDSCAPE_RIGHT]
        else:
            allowed = [ft.DeviceOrientation.PORTRAIT_UP]
        try:
            await page.set_allowed_device_orientations(allowed)
        except Exception as exc:             # noqa: BLE001 - desktop has no
            print(f"[navi] orientation lock unavailable: {exc}", flush=True)

    page._navi_orientation = apply_orientation   # noqa: SLF001 - our own
    page.run_task(apply_orientation)

    # The road graph is NOT loaded here, and that is deliberate.
    #
    # It used to be, "in the background so the first route is fast". The graph is
    # 86 MB of packed arrays and unpacking it costs several hundred megabytes of
    # Python objects - so every launch paid for it whether or not she ever asked
    # for a route, and on a phone Android answered by killing the app. The
    # symptom was "the phone keeps dying... very slow. i can barely get to a
    # different page".
    #
    # So it loads on the first routing request instead: `routing.route()` blocks
    # on it, `live.route_async` already runs that on a worker thread, and until
    # then `routing.graph(timeout=0)` returns None and every reader says "no
    # graph" rather than waiting.

    # A launch always lands on the map. Module state outlives a hot reload, and
    # the honest answer to "what did I open this for" is the road.
    _HERE["key"] = "ride"
    # If she was riding somewhere when the app last died, offer it again rather
    # than making her find it with gloves on. Set as the destination but NOT
    # routed: a route computed at launch would start from wherever the phone was
    # last, which is not where it is now.
    remembered = prefs.last_dest()
    if remembered is not None and live.destination() is None:
        live.LIVE["dest"] = remembered

    body = ft.Container(expand=True)

    # ------------------------------------------------------------ the loader
    def loading_screen(label, note="", step=0, total=0):
        """Black, one line, over everything. Her "overlay black if need be".

        Deliberately not themed: it is shown before the theme has settled, and a
        loading screen that changes colour halfway through looks like a fault.
        """
        return ft.Container(
            expand=True, bgcolor="#000000", alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=18, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[
                    # The artwork itself, uncropped. Her "the 'navi' loading
                    # page should feature the logo in full as well" - and it is
                    # a square PNG shown square, so nothing is cut off the way
                    # the launcher crops it.
                    ft.Image(src=prefs.icon_asset(), width=132, height=132,
                             fit=ft.BoxFit.CONTAIN, error_content=ui.pill("Navi")),
                    ft.Text(label, size=20, weight=ft.FontWeight.W_800,
                            color="#f2f6fb"),
                    ft.Text(f"{step} of {total}" if total else "", size=12,
                            color="#9fb0c3"),
                    ft.ProgressBar(width=220, bgcolor="#1e2733",
                                   color=theme.CYAN),
                    ft.Text(note, size=12, color="#5f7183"),
                ]))

    async def boot_then_open():
        """Run the stages in order, showing each, then open the map.

        One at a time and off the UI thread. Running them together is what made
        the app unusable on a real phone; the screen is only how that stops
        looking like a hang.
        """
        import asyncio
        stages = boot.stages_for(skip_telemetry=prefs.sensors_on_demand())
        for index, (key, label) in enumerate(stages, start=1):
            page.controls.clear()
            page.add(ft.SafeArea(expand=True, content=loading_screen(
                label, "", index, len(stages))))
            page.update()
            try:
                note = await asyncio.to_thread(boot.run_stage, key, page)
            except Exception as exc:          # noqa: BLE001 - a stage that
                # throws must never strand her on a black screen forever
                note = f"failed: {exc}"
            page.controls.clear()
            page.add(ft.SafeArea(expand=True, content=loading_screen(
                label, note, index, len(stages))))
            page.update()
        boot.mark_booted()
        theme.apply(page)
        render("ride")
        # Her "That's amazing, where are we?" - once per cold launch, with the
        # map already up. character.opening is once-per-process and silent
        # without a clip, with Announce turns off, or over a prompt.
        character.opening()

    def go(key):
        """Move to a surface. Fast, because a slow handler drops taps."""
        # Leaving the map releases the sensors, unless a ride is recording and
        # wants their channels in its log.
        #
        # `prefs.sensors_on_demand` ships True and, until now, meant nothing:
        # `ride.build()` attaches whenever the dash is drawn, the dash is on by
        # default, and launch always lands on Ride - so five sensors came up on
        # the first frame of every launch and stayed up through every other
        # surface, feeding gauges that were no longer on screen. This is the
        # line that makes the preference true.
        if (key != "ride" and prefs.sensors_on_demand()
                and not live.recording()):
            sensors.detach()
        if key in TAP_PAGES and key != _HERE["key"]:
            character.tap()
        render(key)

    def bottom_pick(e):
        index = e.control.selected_index
        if 0 <= index < len(BAR_PAGES):
            go(BAR_PAGES[index])

    def menu_button():
        """The round menu button, top-left, floating over whatever is behind it.

        Her "can we have a 'hamburger menu in a circle' button on the top left
        that would open the menu items to swap to a different screen? -- that way
        ride/whereto/around/rides/settings are not ALWAYS in my face?"

        Round and translucent because on the Ride surface it sits on the map. It
        is the only permanent chrome when the bar is off, and one tap reaches
        every surface.
        """
        p = theme.PAL()
        # 52, not 46 - the same 52 as Ride's record and options buttons.
        #
        # Hers: "the menu doesn't sit above the record and options button in
        # series". It never could: this was 46 wide at left=10 while those are
        # 52 wide at left=12, so three buttons read as two sizes in two
        # columns. One size, one left edge, one column.
        return ft.Container(
            width=52, height=52, border_radius=ft.BorderRadius.all(26),
            bgcolor=ft.Colors.with_opacity(0.78, p["bg"]),
            border=ft.Border(*[ft.BorderSide(1, p["border"])] * 4),
            alignment=ft.Alignment.CENTER, ink=True,
            on_click=lambda e: page.run_task(open_menu),
            content=ft.Icon(ft.Icons.MENU, size=24, color=p["text"]))

    async def open_menu(_e=None):
        await page.show_drawer()

    def nav_drawer():
        """Every surface, one tap each. Built per render so it follows the theme.

        Only built when the drawer is actually reachable - it is four rows of
        work, and a drawer nobody can open is four rows for nothing.
        """
        p = theme.PAL()
        here = _HERE["key"]

        def row(key):
            lit = key == here
            return ft.Container(
                height=62, ink=True, border_radius=ft.BorderRadius.all(10),
                padding=ft.Padding(left=14, top=0, right=14, bottom=0),
                margin=ft.Margin(left=8, top=0, right=8, bottom=0),
                bgcolor=(ft.Colors.with_opacity(0.14, p["accent"]) if lit
                         else None),
                on_click=lambda e, k=key: page.run_task(drawer_go, k),
                content=ft.Row(spacing=12, controls=[
                    ft.Icon(ICONS_ON[key] if lit else ICONS[key], size=22,
                            color=p["accent"] if lit else p["text2"]),
                    ft.Column(spacing=0, tight=True, expand=True, controls=[
                        ft.Text(LABELS[key], size=15,
                                weight=ft.FontWeight.W_700, color=p["text"]),
                        ft.Text(BLURB[key], size=11, color=p["text2"])])]))

        # Her "can you seperate each of those 'rows' with a lil more padding
        # vertically?" - a spacer between every row rather than a bigger row,
        # so the tap targets stay the size they are and only the gaps grow.
        rows = []
        for key in DRAWER_PAGES:
            rows.append(row(key))
            rows.append(ft.Container(height=8))
        return ft.NavigationDrawer(bgcolor=p["bg"], controls=[
            ft.Container(height=18), *rows])

    async def drawer_go(key):
        # Change the page FIRST, then close the drawer.
        #
        # It used to await `close_drawer()` before calling `go()`, and that
        # await is an invoke_method with no timeout: the new surface could not
        # begin building until Flutter had answered. A page change then needed
        # three inbound messages handled in order - the menu tap, the row tap,
        # and the drawer's reply - where Moto's always-on bottom bar needs one.
        # Whatever else is loading the event loop, this is the first thing to
        # stall completely, and stalling here reads as "the app will not open
        # that page".
        #
        # Nothing about the new surface depends on the drawer being shut, so
        # the close goes behind it.
        go(key)
        await page.close_drawer()

    def bottom_bar():
        """Five stops, always the same five.

        `selected_index` must be a real index - Flet asserts on it - and Help is
        deliberately not on the bar, so standing on it lights Settings. That is
        both true about where it lives and the way back out of it.
        """
        here = _HERE["key"]
        lit = BAR_PAGES.index(here if here in BAR_PAGES else "settings")
        return ft.NavigationBar(
            selected_index=lit,
            destinations=[ft.NavigationBarDestination(
                icon=ICONS[k], selected_icon=ICONS_ON[k], label=NAV_LABELS[k])
                for k in BAR_PAGES],
            on_change=bottom_pick)

    def build_surface(key):
        if key == "routes":
            return routes_page.build(page, go=go)
        if key == "search":
            return search_page.build(page, go=go)
        if key == "log":
            return log_page.build(page, go=go)
        if key == "settings":
            return settings_page.build(page, go=go)
        if key == "help":
            return help_page.build(page, go=go)
        if key == "diagnostics":
            return diagnostics_page.build(page, go=go)
        return ride_page.build(page, go=go)

    def render(key=None):
        """Paint the whole shell around one surface."""
        p = theme.PAL()
        if key not in PAGES:
            key = _HERE["key"]
        _HERE["key"] = key
        # Which surface is showing, where anything can read it. Ride's GPS
        # handler uses it to stop painting a map that is not on screen; see
        # `ride._showing_ride`.
        page._navi_surface = key             # noqa: SLF001 - our own attribute
        # Which palette this surface gets. Ride wears whichever of the four
        # high-contrast themes she chose; everything else wears the dashboard's
        # own alola palette, because those surfaces are read at a stop with the
        # phone in a hand rather than at 60 mph in the sun. Set BEFORE the body
        # is built, since every widget reads `PAL()` as it is constructed.
        theme.set_surface("map" if key == "ride" else "chrome")
        p = theme.PAL()
        try:
            content = build_surface(key)
        except Exception as exc:             # noqa: BLE001 - a broken page is
            # not a dead app. Naming the surface that failed, and why, beats a
            # blank window with nothing to read on the side of a road.
            content = ui.error_card(f"{LABELS[key]} could not be built.", exc)
            # And say it where a log can be read, not only on the screen she
            # may never reach. A surface that cannot build is the single most
            # likely reason a page "will not open", and until now it left no
            # trace anywhere off the device.
            traceback.print_exc()
        # The map fills its surface edge to edge; every other surface wants a
        # gutter. Done here rather than inside each page, so the map does not
        # have to opt out of a padding it never wanted.
        # The menu button floats at top-left over whatever is behind it, so a
        # surface that starts at the top hides its first heading underneath it -
        # "Look" was sitting behind the hamburger. The map wants no padding at
        # all; everything else starts below the button.
        body.padding = None if key == "ride" else ft.Padding(
            left=12, top=68, right=12, bottom=0)
        body.content = content
        body.bgcolor = p["bg"]
        page.controls.clear()
        page.drawer = nav_drawer()
        # The menu button floats over the surface rather than sitting above it,
        # so turning the bar off really does give the whole screen to the map
        # instead of trading one bar for another.
        # The button is POSITIONED, not aligned.
        #
        # A Stack whose children all want to fill it has two children competing
        # for the same unbounded constraints, and Flutter resolves that by
        # measuring and re-measuring - which is what froze the app on both of her
        # phones. `left`/`top` make this child absolute: it takes its own size,
        # the map keeps the whole stack, and nothing has to be negotiated.
        stacked = ft.Stack(expand=True, controls=[
            body,
            # left=12 to match Ride's button column exactly. Ride starts its
            # own column at top=74, which is this button's 12 + 52 + a 10 gap,
            # so all three sit in one line down the left edge.
            ft.Container(left=12, top=12, content=menu_button())])
        chrome = [stacked]
        if prefs.bar():
            chrome.append(bottom_bar())
        shell = ft.Column(spacing=0, expand=True, controls=chrome)
        # Keep the chrome out from under the notch and the gesture bar. A no-op
        # on the desktop, where Flutter reports no intrusions.
        page.add(ft.SafeArea(content=shell, expand=True))
        page.update()
        # Every setting change comes through here, so this is how the Keep the
        # screen on switch takes effect the moment she flips it. Free when
        # nothing changed.
        page.run_task(awake.follow, page)

    # How a page asks for a full rebuild - a theme change, a new destination, the
    # record button. Parked on the page so any module can find it without
    # importing this one.
    page._navi_rerender = lambda: render()   # noqa: SLF001 - our own attribute

    async def on_back(e):
        """Android's Back: up one level where there is one, else out as before.

        A surface can take a Back for itself by exposing `back(page)` that
        returns True - a Settings door closing to the door list. A surface in
        PARENT goes up to it. Anything else leaves the app: `confirm_pop(True)`
        on the root view is Flet's SystemNavigator.pop, the old behaviour.

        confirm_pop is ALWAYS answered: Flutter waits up to five minutes for
        it, and an unanswered Back is a Back button that does nothing.
        """
        stay = False
        try:
            key = _HERE["key"]
            handler = getattr({"settings": settings_page}.get(key), "back",
                              None)
            if callable(handler) and handler(page):
                stay = True
            elif key in PARENT:
                go(PARENT[key])
                stay = True
        except Exception:                    # noqa: BLE001 - Back must answer
            traceback.print_exc()
        finally:
            await e.control.confirm_pop(not stay)

    # Every Back comes to `on_back` rather than closing the app outright. A
    # dialog or the drawer still takes its own Back first: they are routes on
    # top of this view, and this view's pop scope is only asked when it is on
    # top.
    views = getattr(page, "views", None)
    if UP_ON_BACK and views:
        views[0].can_pop = False
        views[0].on_confirm_pop = on_back

    def on_resized(_e=None):
        """Rotation. Rebuild, but only when the orientation actually flipped.

        `on_resized` fires for the keyboard, for a window drag, and several times
        during a rotation. Rebuilding on every one of them would rebuild the map
        mid-gesture; rebuilding only when portrait/landscape changes is one
        rebuild per rotation, which is exactly what the layout needs.
        """
        wide = (getattr(page, "width", 0) or 0) > (getattr(page, "height", 0) or 0)
        if _HERE.get("wide") == wide:
            return
        _HERE["wide"] = wide
        render()

    page.on_resized = on_resized

    # The loader runs once per PROCESS, not once per open. Coming back from the
    # recent-apps list leaves the process alive, so `boot.booted()` is already
    # true and she gets the map immediately; Android clearing the process is
    # exactly when the work does need doing again.
    if boot.booted():
        render("ride")
    else:
        page.run_task(boot_then_open)


# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call.
#
# Open a real window when there is a display, serve over HTTP when there is not:
#
#   AGEDASHII_HEADLESS=1   force the HTTP server (the Pi does this)
#   AGEDASHII_PORT=8554    pin the port; 0 lets the OS pick a free one
#
# 8554 because the others are taken: the dashboard 8550, Moto and FoodZen 8552,
# Watudo 8553. On the Pi every Agedashii app is headless and they are expected to
# be up at the same time, so a shared default would mean the second one to start
# dying with "address already in use".
if __name__ == "__main__":
    import os as _os

    _headless = _os.environ.get("AGEDASHII_HEADLESS") == "1" or not (
        _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY"))
    _port = int(_os.environ.get("AGEDASHII_PORT") or (8554 if _headless else 0))
    ft.run(main, view=None if _headless else ft.AppView.FLET_APP, port=_port)
