"""Agedashii Moto - Trips + Garage, in an app of its own.

Three surfaces, their own Settings, and a guide:

  Trips    the trip library, the two-point offline planner, the route
           editor, recorded rides, and xNav as its fourth tab
  Garage   the bikes, their mileage/services/expenses, spec sheets, notes,
           and the server's Craigslist/Reddit scanner
  XNAV     the live map on its own, full height - GPS, track recording and
           turn-by-turn guidance
  Settings this app's own: the server address, the theme, the corner
           radius, the glow, the CRT overlay, the navigation itself, and
           the units - none of which could be changed from inside Moto
           before, only by opening the dashboard app instead
  Help     "How this works", off Settings. Not on the bar: it is read
           once, and the bar is for the places you go back to.

This file is the shell they live in: the topbar, the drawer, the bottom
bar, the theme, the CRT overlay and the outbox chip. It builds none of the
surfaces itself - `agedashii_moto.pages` does that, and it was already
doing it inside the big dashboard app before the split.

THE BAR IS THE NAVIGATION; THE MENU IS AN OPTION
    "Each of these smaller apps (not tofu) work better with the bottom
    navigation bar and less with the hamburger menu... i want the ability
    to show / hide the hamburger menu trigger as well."

    So the bar carries all four surfaces and is on by default, and the
    hamburger is a preference. Hidden, its slot in the topbar is not
    drawn at all rather than left as a dead gap. Nothing becomes
    unreachable either way: the bar reaches every surface, Help hangs off
    Settings, and the tabs inside Trips and Garage have always been on
    the surfaces themselves as well as in the drawer.

    One rail: the last of the two cannot be switched off. See
    `motoprefs.hamburger_shown`.

It is modelled on the dashboard's own `main.py` and deliberately does not
import it. Home carries nine pages, a layout editor, a Settings page and a
set of preferences naming all nine; Moto carries four surfaces that fit on
a bottom bar with room to spare. Importing Home's shell would bring the
other six pages, their network reads and their preferences along with it,
which is the whole thing the split was for.

WHAT NEEDS A NETWORK, AND WHAT DOES NOT
    The trips, the rides, the bikes and everything logged against them live
    on the dashbored server, so those sync - through `agedashii_washi.store`,
    which answers reads from the device's own SQLite copy and queues writes
    in an outbox when there is nobody to talk to. Nothing is dropped and
    nothing is lost to being killed mid-queue.

    Everything else is local and stays local: the 24 themes, the corner
    radius, the glow, the CRT overlay, the per-surface card layouts, the
    units, the navigation preferences, the routing graph and the map
    tiles. A ride planned in a canyon with no bars plans the same as one
    planned on wifi, and the map draws from the on-device MBTiles archive
    rather than going quiet.

    The chip in the topbar is the one place the difference is admitted to:
    it appears only when the server has not got something yet, says what,
    and drains on a tap, on resume, and whenever signal comes back.
"""
import asyncio

import flet as ft

from agedashii_qi import GLOW, PAL, RAD, S, apply_theme, crt_overlay
from agedashii_qi.themes import THEMES, THEME_NAMES
from agedashii_washi import api as M
from agedashii_washi import config, panels, prefs
from agedashii_washi.surface import _TAB_INDEX, _dismiss, _show
from agedashii_washi.widgets import BRD, PAD

from agedashii_moto import motoprefs
from agedashii_moto.pages import (garage, help as helppage, home, settings,
                                  trips, vnav, weather, xnav)

# The gradient the brand pill is painted in. The same three stops the
# dashboard uses, because it is the same brand - Moto is a door into it,
# not a different product.
ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

#: Every surface on the navigation, in order. Four of them, so unlike Home
#: there is still no "More" sentinel and no split between a bar set and a
#: menu set: the bar and the drawer list exactly the same four things.
PAGES = ["trips", "garage", "xnav", "settings"]

#: Reachable, but not on the bar. Help is read once and then not again;
#: giving it a permanent slot would cost a fifth of the bar for it.
EXTRA_PAGES = ["home", "weather", "help"]

#: The surfaces built out of `panels.layout` - the ones the topbar's
#: Arrange button applies to. Settings and Help are ordinary columns with
#: nothing to rearrange, so a test that asks "does every surface register a
#: card stack" asks it of these three and not of all five.
# xnav is a stub with nothing to arrange yet; vNav's cards are
# inside Trips, which is already here.
CARD_PAGES = ["trips", "garage", "home"]

#: Where the bar's highlight goes for a surface that is not on the bar.
#: Help lives under Settings, so standing in Help lights Settings rather
#: than nothing - a bar with no selection reads as "you are nowhere".
NAV_PARENT = {"help": "settings"}

LABELS = {"home": "Home", "trips": "Trips", "garage": "Garage",
          "xnav": "XNAV", "weather": "Weather", "settings": "Settings",
          "help": "Help"}

# Themed Material icons, never emoji: this is chrome, and chrome has to
# follow all 24 themes. An emoji is painted by the font at a fixed colour
# and would sit on the design rather than in it.
#
# Four marks that cannot be mistaken for each other at 24px, which the old
# set could: MAP for Trips beside NAVIGATION for XNAV is two arrows on two
# maps. ROUTE is a line with stops on it - a planned trip; NAVIGATION is
# the compass arrow - where you are now; TWO_WHEELER is a motorcycle; TUNE
# is a row of sliders, which is what Settings actually is, rather than the
# gear every app in the world uses for everything.
ICONS = {"home": ft.Icons.HOME_OUTLINED, "trips": ft.Icons.ROUTE_OUTLINED, "garage": ft.Icons.TWO_WHEELER,
         "xnav": ft.Icons.NAVIGATION_OUTLINED,
         "weather": ft.Icons.CLOUD_OUTLINED, "settings": ft.Icons.TUNE,
         "help": ft.Icons.MENU_BOOK}

# The filled twin of each mark, for the destination you are standing on.
# Outline-to-filled is Material's own selected-state language and it reads
# at a glance on a phone, where the indicator pill alone is easy to miss in
# bright sun.
ICONS_SELECTED = {"home": ft.Icons.HOME, "trips": ft.Icons.ROUTE, "garage": ft.Icons.TWO_WHEELER,
                  "xnav": ft.Icons.NAVIGATION, "weather": ft.Icons.CLOUD,
                  "settings": ft.Icons.TUNE, "help": ft.Icons.MENU_BOOK}

# What sits inside each surface, so the drawer can offer a tab directly
# instead of landing you on the surface to hunt for it. Each entry is
# (label, [(tab-group key, index), ...], indent depth); the keys and indices
# must match the `_tabs(..., key=)` calls in the page modules, and a test
# walks both and fails if they drift.
#
# XNAV has no entry because it has no tabs - it is one screen. Settings
# has one that is not a tab at all: Help is a surface of its own, and the
# drawer offering it is the second way in beside the row on the Settings
# page itself.
SUBPAGES = {
    "trips": [("Selekt", [("trips", 0)], 1), ("Produce", [("trips", 1)], 1),
              ("Rides", [("trips", 2)], 1), ("vNav", [("trips", 3)], 1)],
    "garage": [("Home", [("garage", 0)], 1),
               ("Mechanic", [("garage", 1)], 1),
               ("Flipper", [("garage", 2)], 1)],
    "weather": [("Bay Area", [("weather", 0)], 1),
                ("Explore", [("weather", 1)], 1)],
}

#: Sub-rows that navigate to another surface rather than to a tab. Kept
#: apart from SUBPAGES because the test that walks SUBPAGES against the
#: page modules' own `_tabs()` calls would have nothing to match this to.
SUBSURFACES = {"settings": [("Help", "help", 1)]}


def _sub_rows(key):
    """Everything the drawer would list *under* `key` - tabs and surfaces.

    One question with one answer, because three places used to ask it and
    two of them only knew about SUBPAGES: whether to draw a chevron,
    whether to draw the rows, and which rows. A surface with only a
    sub-surface (Settings) would otherwise get its rows suppressed by the
    chevron test and never appear.
    """
    return list(SUBPAGES.get(key, [])) + list(SUBSURFACES.get(key, []))

# How often the outbox watcher looks up from its book. Matched to
# api.ONLINE_RECHECK, which is how long a "we are offline" verdict is
# trusted for reads - one cadence for the whole app rather than two that
# disagree.
SYNC_POLL_SECS = 20

#: Where Moto opens. Trips rather than XNAV: XNAV turns the GPS on, and an
#: app that starts by taking a fix is an app that costs battery to glance at.
FIRST_PAGE = "trips"


def build_surface(page, key):
    """The control tree for one surface. No chrome, no side effects.

    Split out of `render` so the tests can build every surface without
    standing up a shell, and so the shell has exactly one place that maps a
    key to a page module.

    Every builder takes only `page`, Settings included. The two things
    Settings needs from the shell - repaint everything, go somewhere else -
    are read off `S`, where the shell parks them, so this signature stays
    one line and a surface built with no shell around it can see that
    there is nowhere to go rather than being handed a callback into a dead
    page.
    """
    if key == "home":
        return home.build(page)
    if key == "garage":
        return garage.build(page)
    if key == "xnav":
        return xnav.build(page)
    if key == "weather":
        return weather.build(page)
    if key == "settings":
        return settings.build(page)
    if key == "help":
        return helppage.build(page)
    return trips.build(page)


async def main(page):
    # Without this the window, the taskbar and alt-tab all say "flet". The
    # suffix matters here in a way it does not for the dashboard: Moto is
    # one of three Agedashii apps that can be installed side by side, and
    # three windows all called "Agedashii" is three windows you cannot tell
    # apart in a switcher.
    page.title = "Agedashii Moto"
    apply_theme(page)

    # Flet's Page defaults to Padding.all(10). That inset sits OUTSIDE the
    # CRT overlay and outside SafeArea, so the effect stopped 10px short of
    # every edge and the scaffold showed through as a pale border all the
    # way round - "there's a white border around everything and it breaks
    # immersion". The app draws its own padding on the body container, so
    # the page-level inset was only ever doing harm.
    page.padding = 0

    # Persistent storage is a Flet *service*: constructing it inside the page
    # context registers it, and it is unregistered again the moment nothing
    # holds a strong reference - hence parking it on S rather than a local.
    S.prefs = ft.SharedPreferences()
    # Restore the saved server address and confirm it still answers before
    # the first screen paints, so Trips does not flash an empty library and
    # then fill it in.
    await config.load(S.prefs)
    # Appearance and layout come from the same storage, and are shared with
    # the other Agedashii apps on purpose: the theme she picked in one is the
    # theme she gets in all of them.
    await prefs.load(S.prefs)
    prefs.apply_runtime()
    # Moto's own two keys, and the reason they are not in `prefs`: that
    # module writes exactly the keys its own `_defaults()` names, so a key
    # added from here would be read from nowhere and saved nowhere. See
    # agedashii_moto/motoprefs.py.
    await motoprefs.load(S.prefs)
    # The theme, applied before anything paints. The dashboard's picker has
    # always changed the theme and never written it down, so every launch
    # opened on the shipped default again however many times she had picked
    # another - "the dashboard's picker famously resets every launch". This
    # is the read half of the fix; Settings > Theme is the write half.
    apply_theme(page, motoprefs.theme())

    body = ft.Container(expand=True, padding=PAD(12, 12))

    def brand_chip():
        """The product name in the gradient pill, and nothing else.

        "Moto", not "Agedashii Moto": the pill *is* the Agedashii mark, so
        spelling it out inside the mark would say it twice. The window title
        and the installed app name carry the full name; this is the bar.
        """
        return ft.Container(
            padding=PAD(4, 12), border_radius=RAD(10),
            gradient=ft.LinearGradient(colors=[ORANGE, PINK, CYAN],
                                       begin=ft.Alignment(-1, 0),
                                       end=ft.Alignment(1, 0)),
            # alola's hero title carries `0 0 30px color-mix(accent 40%)`.
            # The pill is this app's equivalent of that title, so it gets the
            # wide version of the glow rather than the 10px heading one.
            shadow=GLOW(blur=24, opacity=0.40),
            content=ft.Text("Moto", size=13, weight=ft.FontWeight.W_800,
                            color="#0d1117"),
        )

    # ------------------------------------------------------- the outbox chip
    # Reads heal themselves: the store answers from its own copy and
    # re-probes behind the UI. Writes had nothing that did that. A service
    # logged with no signal goes into the outbox and stays there until
    # another write happens to succeed - which on a bike that spends its
    # offline time in a canyon and its online time in a pocket could be days,
    # with no way to tell.
    #
    # So: one chip in the chrome that says what is waiting, and three things
    # that drain it - a tap, coming back to the foreground, and the watcher
    # below noticing signal returned. It lives here rather than on a page
    # because the outbox is the whole app's, not any one surface's.
    #
    # Created once and reused across renders, the same way `body` is: a
    # background drain needs a control it can still find after a repaint.
    sync_icon = ft.Icon(ft.Icons.CLOUD_UPLOAD, size=13)
    sync_text = ft.Text("", size=10, weight=ft.FontWeight.W_600)
    sync_chip = ft.Container(
        padding=PAD(3, 8), border_radius=RAD(9), visible=False, ink=True,
        content=ft.Row(spacing=5, tight=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[sync_icon, sync_text]))

    def paint_sync():
        """Say what the server has not got yet, in the server's own terms."""
        p = PAL()
        st = M.sync_status()
        label = st.get("label") or ""
        stuck = (st.get("parked") or 0) + (st.get("conflict") or 0)
        sync_text.value = label
        sync_text.color = p["pink"] if stuck else p["text2"]
        sync_icon.color = p["pink"] if stuck else p["text2"]
        # `.icon`, not `.name` - ft.Icon has no `name` in Flet 0.86 and is
        # not slotted, so assigning to it binds a stray attribute and the
        # glyph never changes. Inherited from the dashboard, fixed there too.
        sync_icon.icon = ft.Icons.CLOUD_OFF if stuck else ft.Icons.CLOUD_UPLOAD
        sync_chip.bgcolor = p["bg2"]
        sync_chip.border = BRD(p["pink"] if stuck else p["border"], 1)
        sync_chip.tooltip = (f"{label} - tap to try again" if label else "")
        # Nothing waiting is the normal state; an always-present "all synced"
        # badge would be noise, and worse, would look like a claim about the
        # server when it is only a claim about the queue.
        sync_chip.visible = bool(label)

    def drain(*, force=False):
        """Flush the outbox. Blocking - callers put it on a worker thread.

        `force` is the tap: it also un-parks writes the server refused, which
        is the only way back for an item that burned its five attempts while
        the server was busy answering 502s.
        """
        try:
            if force and (M.sync_status().get("parked") or 0):
                M.retry_parked()
            if not M.pending_count():
                return None
            # Offline, the cheap /api/config probe answers in three seconds
            # where a queued write's own timeout is ten or fifteen. Ask the
            # cheap question first, exactly as the read path does.
            if not force and not M.sync_status().get("online"):
                try:
                    if not M.store().check_online():
                        return None
                except Exception:      # noqa: BLE001 - no store, nothing to drain
                    return None
            return M.sync()
        except Exception:              # noqa: BLE001 - syncing never kills the app
            return None

    def drain_and_paint(*, force=False):
        drain(force=force)
        paint_sync()
        try:
            page.update()
        except Exception:              # noqa: BLE001 - torn down mid-drain
            pass

    def sync_now(e=None):
        runner = getattr(page, "run_thread", None)
        if callable(runner):
            runner(drain_and_paint, force=True)

    sync_chip.on_click = sync_now

    async def watch_outbox():
        """Notice signal coming back, and send what has been waiting for it.

        Polls rather than listens because there is nothing to listen to:
        Flet surfaces no connectivity event, and the only honest test for
        "is the server there" is asking it. Costs one 3-second probe every
        20 seconds *while there is something queued*, and nothing at all the
        rest of the time - which is almost always.
        """
        while True:
            try:
                await asyncio.sleep(SYNC_POLL_SECS)
                if not M.pending_count():
                    continue
                await asyncio.to_thread(drain)
                paint_sync()
                page.update()
            except asyncio.CancelledError:
                raise
            except Exception:          # noqa: BLE001 - a bad tick is not fatal
                pass

    def on_lifecycle(e):
        # Back in the foreground. Whatever was queued has been queued since
        # the last time this app was looked at, which is exactly when a phone
        # is most likely to have found wifi in between.
        if getattr(e, "state", None) in (ft.AppLifecycleState.RESUME,
                                         ft.AppLifecycleState.SHOW):
            sync_now()

    page.on_app_lifecycle_state_change = on_lifecycle

    # Which surface is on screen. Held in a list so the nested handlers below
    # can write to it, and so a theme change can repaint the surface you are
    # on instead of bouncing you back to Trips.
    current = [FIRST_PAGE]

    # ------------------------------------------------------------ navigation
    def go_sub(page_key, tab_path):
        """Jump straight to a tab inside a surface, e.g. Trips > Rides.

        `_tabs()` reads its starting index out of `_TAB_INDEX`, keyed by the
        same name it saves under, so setting those keys before rendering
        lands on the right tab without any new plumbing.
        """
        async def handler(e):
            for tab_key, index in tab_path:
                _TAB_INDEX[tab_key] = index
            await page.close_drawer()
            render(page_key)
        return handler

    def go_surface(key):
        """A drawer sub-row that is another surface - Settings > Help."""
        async def handler(e):
            await page.close_drawer()
            render(key)
        return handler

    def _drawer_go(key):
        page.run_task(_drawer_go_async, key)

    async def _drawer_go_async(key):
        await page.close_drawer()
        render(key)

    def _toggle_drawer_section(key):
        """Flip one surface's chevron - the drawer's own affordance, not a
        navigation. Never goes through render(): that rebuilds the topbar,
        the body and the bottom bar along with the drawer, and Trips' body
        reads the trip list. Flet runs a synchronous handler on its event
        loop, so a slow rebuild leaves the old frame up and drops the taps
        that land during it. Swapping just `page.drawer` and updating is the
        smallest repaint that can possibly show the new chevron state.
        """
        prefs.set_drawer_open(key, not prefs.drawer_section_open(key, current[0]))
        page.drawer = nav_drawer()
        page.update()
        run_task = getattr(page, "run_task", None)
        if callable(run_task) and S.prefs is not None:
            run_task(prefs.save, S.prefs)

    def nav_drawer():
        p = PAL()
        # Rebuilt on every render: the drawer is themed, and one built once
        # at startup would keep the old palette after a theme change. Also
        # rebuilt on its own by _toggle_drawer_section - see there for why
        # that path does not go through render().
        #
        # Hand-built rows rather than NavigationDrawerDestination, because
        # that control is flat and the point here is depth: a surface with
        # tabs shows them, so Garage > Flipper is one tap from the menu
        # instead of a page you have to land on and then hunt through.
        rows = [ft.Container(height=12)]

        def page_row(k):
            on = k == current[0]
            highlight = dict(
                # The drawer row you are standing on is the app's "active nav
                # item"; glow on, it gets alola's accent shadow. Only the
                # active one, so the drawer does not become a wall of light.
                shadow=GLOW(blur=14, opacity=0.35) if on else None,
                bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else None)
            label_row = ft.Row(spacing=12, controls=[
                ft.Icon(ICONS[k], size=19, color=p["accent"] if on else p["text2"]),
                ft.Text(LABELS[k], size=14,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["text"] if on else p["text2"]),
            ])
            # Tapping the label or the icon always navigates - that is
            # unconditional, chevron or not. A surface with no sub-items
            # (XNAV) gets exactly the row it would have had anyway: no
            # chevron, no indent, no extra wrapper.
            if not _sub_rows(k):
                return ft.Container(
                    padding=PAD(14, 9), border_radius=RAD(8), ink=True,
                    on_click=lambda e, key=k: _drawer_go(key),
                    content=label_row, **highlight)

            # A surface with sub-items additionally gets a chevron - a
            # separate tap target from the label/icon. 40x40 is a real
            # Material touch target.
            open_ = prefs.drawer_section_open(k, current[0])
            nav = ft.Container(
                padding=PAD(14, 9), border_radius=RAD(8), ink=True, expand=True,
                on_click=lambda e, key=k: _drawer_go(key), content=label_row)
            chevron = ft.Container(
                width=40, height=40, alignment=ft.Alignment.CENTER,
                border_radius=RAD(8), ink=True,
                on_click=lambda e, key=k: _toggle_drawer_section(key),
                tooltip="Collapse" if open_ else "Expand",
                content=ft.Icon(
                    ft.Icons.EXPAND_MORE if open_ else ft.Icons.CHEVRON_RIGHT,
                    size=20, color=p["accent"] if on else p["text2"]))
            return ft.Container(
                border_radius=RAD(8),
                content=ft.Row(spacing=0,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[nav, chevron]),
                **highlight)

        def sub_row(label, on_click, depth=1):
            return ft.Container(
                padding=ft.Padding(14 + 20 * depth, 6, 14, 6),
                border_radius=RAD(8), ink=True,
                # The drawer's rows had no press feedback at all beyond the
                # ripple; 120 ms on the hover/selected tint is what makes a
                # tap feel answered rather than merely obeyed.
                ink_color=ft.Colors.with_opacity(0.18, p["accent"]),
                animate=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
                on_click=on_click,
                content=ft.Text(label, size=12.5, color=p["text2"]))

        for k in PAGES:
            rows.append(page_row(k))
            # Sub-items only draw while their section is open - a surface
            # with none of its own never reaches drawer_section_open at all,
            # matching "no chevron, no rows".
            if _sub_rows(k) and prefs.drawer_section_open(k, current[0]):
                for label, tab_path, depth in SUBPAGES.get(k, []):
                    rows.append(sub_row(label, go_sub(k, tab_path), depth))
                # A sub-row that is another surface rather than a tab -
                # Settings > How this works. Same shape, different handler.
                for label, target, depth in SUBSURFACES.get(k, []):
                    rows.append(sub_row(label, go_surface(target), depth))

        return ft.NavigationDrawer(bgcolor=p["bg2"], controls=rows)

    async def open_menu(e):
        await page.show_drawer()

    # ----------------------------------------------------------- the topbar
    def open_theme_picker(e):
        """A named, tappable list of every theme.

        24 themes is far too many to walk one tap at a time, and a tooltip
        naming the current one needs a mouse, which a phone does not have.
        The theme is shared storage, so picking one here is picking it for
        every Agedashii app on the device.

        Kept beside the full picker in Settings rather than replaced by it:
        this is one tap from anywhere and Settings is two, and switching
        theme is the one appearance change she makes often. Both write the
        same key - a theme picked here survives a restart exactly as one
        picked there does.
        """
        p = PAL()

        def pick(key):
            def handler(e):
                _dismiss(page, dlg)
                apply_theme(page, key)
                motoprefs.set_theme(key)
                render(current[0])
                # Storage is async and the repaint above is not, so the
                # write goes behind the frame rather than in front of it.
                run_task = getattr(page, "run_task", None)
                if callable(run_task) and S.prefs is not None:
                    run_task(motoprefs.save, S.prefs)
            return handler

        def swatch(key):
            t = THEMES[key]
            on = key == S.theme
            return ft.Container(
                padding=PAD(10, 8), border_radius=RAD(8), ink=True,
                on_click=pick(key),
                ink_color=ft.Colors.with_opacity(0.18, p["accent"]),
                animate=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
                bgcolor=ft.Colors.with_opacity(0.14, p["accent"]) if on else None,
                content=ft.Row(spacing=10, controls=[
                    # The theme's own colours, so the row previews itself.
                    ft.Container(width=34, height=18, border_radius=RAD(5),
                                 bgcolor=t["bg2"], border=BRD(t["border"], 1),
                                 content=ft.Row(spacing=2, controls=[
                                     ft.Container(width=6, height=6,
                                                  border_radius=3, bgcolor=t[c])
                                     for c in ("accent", "green", "pink")])),
                    ft.Text(THEME_NAMES.get(key, key), size=13,
                            weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                            color=p["text"], expand=True),
                    ft.Icon(ft.Icons.CHECK, size=16, color=p["accent"])
                    if on else ft.Container(width=16),
                ]))

        dlg = ft.AlertDialog(
            bgcolor=p["bg2"],
            title=ft.Text(f"Theme - {THEME_NAMES.get(S.theme, S.theme)}",
                          size=15, color=p["text"]),
            content=ft.Container(width=300, height=380, content=ft.Column(
                spacing=2, scroll=ft.ScrollMode.AUTO,
                controls=[swatch(k) for k in THEME_NAMES])),
            actions=[ft.TextButton("Close", on_click=lambda e: _dismiss(page, dlg))],
        )
        _show(page, dlg)

    async def toggle_customize(e):
        """Enter or leave arrange-the-cards mode on the surface you are on.

        Every surface here is built out of `panels.layout`, so every one of
        them can be rearranged. Done is also where the arrangement is written
        to storage: each drag and resize already saves as it happens (see
        panels._apply), so this is the belt to that braces.
        """
        on = panels.set_editing(not panels.editing())
        if not on and S.prefs is not None:
            await prefs.save(S.prefs)
        render(current[0])

    def topbar():
        p = PAL()
        editing = panels.editing()
        # Only on a surface that has a card stack to arrange. A button that
        # does nothing is worse than no button, which is also why Settings
        # and Help - ordinary columns, nothing to rearrange - get no
        # Arrange button rather than a disabled one.
        arrange = [ft.IconButton(
            ft.Icons.CHECK if editing else ft.Icons.DASHBOARD_CUSTOMIZE,
            icon_color=p["accent"] if editing else p["text2"],
            tooltip="Done arranging" if editing
            else "Arrange the cards on this page",
            on_click=toggle_customize)] if (panels.has_surface() or editing) else []
        # Hidden means *gone*, not disabled and not an empty box of the same
        # width: "when hidden, its topbar slot goes away entirely rather
        # than leaving a dead gap". The brand pill slides left into the
        # space, which is the same place it sits on the website.
        menu = [ft.IconButton(ft.Icons.MENU, icon_color=p["text2"],
                              tooltip="Menu", on_click=open_menu)] \
            if motoprefs.hamburger_shown() else []
        return ft.Row(spacing=10, controls=[
            *menu,
            brand_chip(),
            ft.Text(LABELS[current[0]], size=12, color=p["text2"]),
            ft.Container(expand=True),
            sync_chip,
            *arrange,
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)}",
                          on_click=open_theme_picker),
        ])

    # -------------------------------------------------------- the bottom bar
    async def bottom_pick(e):
        idx = e.control.selected_index
        if 0 <= idx < len(PAGES):
            render(PAGES[idx])

    def bottom_bar():
        p = PAL()
        # selected_index must be a real index (Flet asserts on it). Every
        # surface on the bar is in PAGES, and a surface that is not - Help -
        # lights its parent instead of nothing, so the bar never reads as
        # "you are nowhere". The final fallback stays rather than trusting
        # either of those forever.
        here = NAV_PARENT.get(current[0], current[0])
        index = PAGES.index(here) if here in PAGES else 0
        return ft.NavigationBar(
            selected_index=index,
            # Icons as controls rather than bare names, so each one is
            # tinted from PAL() and follows all 24 themes instead of taking
            # whatever Material's seeded scheme decided. The selected one is
            # the filled twin in the accent colour - Material's own
            # selected-state language, and legible in sunlight where the
            # indicator pill alone is not.
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.Icon(ICONS[k], color=p["text2"]),
                    selected_icon=ft.Icon(ICONS_SELECTED[k], color=p["accent"]),
                    label=LABELS[k], tooltip=LABELS[k])
                for k in PAGES],
            on_change=bottom_pick,
            animation_duration=180,
        )

    # -------------------------------------------------------------- render
    def render(key):
        p = PAL()
        if key not in PAGES and key not in EXTRA_PAGES:
            key = FIRST_PAGE
        current[0] = key
        # Cleared before the body builds and set again by panels.layout(), so
        # the topbar - built after the body, below - knows whether this
        # surface has a card stack worth offering Customize for.
        panels.begin_render()
        paint_sync()
        body.content = build_surface(page, key)
        body.bgcolor = p["bg"]
        # Not built at all when there is no trigger for it. The drawer is
        # eight-odd containers per render and nothing can open it with the
        # hamburger hidden, so building it anyway would be paying for a
        # control she has switched off.
        page.drawer = nav_drawer() if motoprefs.hamburger_shown() else None
        page.controls.clear()
        chrome = [ft.Container(padding=PAD(8, 12), bgcolor=p["bg"],
                               content=topbar()),
                  body]
        # The bar is the primary navigation and is on by default; off, the
        # content gets the height back and the drawer - which cannot also
        # be hidden, see motoprefs.hamburger_shown - still reaches
        # everything. Off is worth having on XNAV in particular, where
        # every row of pixels is map.
        if prefs.bottom_bar_enabled():
            chrome.append(bottom_bar())
        shell = ft.Column(spacing=0, expand=True, controls=chrome)
        # Keep the chrome out from under the hardware. On a Pixel 8 Pro an
        # app with no SafeArea draws its topbar beneath the clock and its
        # bottom row under the gesture bar. Flutter reports zero intrusions
        # on desktop, so this is a no-op on the laptop rather than a second
        # layout to keep working. It wraps the chrome and NOT the CRT overlay
        # below: a screen effect that stopped short of the cutout would draw
        # a bright band exactly where the cutout is.
        shell = ft.SafeArea(content=shell, expand=True)
        # The CRT vignette goes on here, once, over the whole shell - not per
        # surface and not per card. Two controls for the entire app when it
        # is on, none at all when it is off, which is the default. The
        # overlay is ignore_interactions, so everything underneath is still
        # hit-tested exactly as it was; see qi.crt_overlay().
        if prefs.crt_overlay():
            shell = ft.Stack(expand=True, controls=[shell, crt_overlay()])
        page.add(shell)
        page.update()

    # A surface that writes to the server needs to re-read and repaint after
    # the write lands. Garage's `_repaint` and `forms.rebuilder` both look
    # for this first and only replace `page.controls` wholesale if it is
    # absent - which would drop the topbar and the bottom bar with it, and
    # strand her on a page with no way off. Parked on S for the same reason
    # S.prefs is: it must stay referenced.
    S.rerender = lambda: render(current[0])

    # Trips' cards link into each other (a trip card's Edit opens Produce),
    # and only the shell knows how to change surface. Same trick go_sub
    # uses: set the tab indices the page will read on the way down, then
    # render. Parked on S rather than passed in, because a page with no
    # shell - standalone, the tests - must be able to see that there is
    # nowhere to go and leave the control out rather than offer a tile that
    # quietly does nothing.
    def goto(key, tab_path=()):
        for tab_key, index in (tab_path or ()):
            _TAB_INDEX[tab_key] = index
        render(key)

    S.goto = goto

    render(FIRST_PAGE)

    # Launch is itself a resume: whatever the last session queued has been
    # waiting across a process death, and the store kept it.
    sync_now()
    # Parked on S alongside the rest, so nothing drops the only reference to
    # the watcher and lets it be garbage collected mid-sleep.
    S.sync_watcher = page.run_task(watch_outbox)


# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call.
#
# Open a real window when there is a display, serve headless when there is
# not, and let either be overridden. Hardcoding `view=None, port=8550` is
# right on the headless Pi and wrong everywhere else: it produces no window
# at all on a laptop, and a second copy dies with "address already in use".
#
#   AGEDASHII_HEADLESS=1   force the HTTP server (the Pi does this)
#   AGEDASHII_PORT=8552    pin the port; 0 or unset lets the OS pick a free
#                          one, so Moto and the dashboard cannot collide
if __name__ == "__main__":
    import os as _os

    _headless = _os.environ.get("AGEDASHII_HEADLESS") == "1" or not (
        _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY"))
    _port = int(_os.environ.get("AGEDASHII_PORT") or (8552 if _headless else 0))
    ft.run(main, view=None if _headless else ft.AppView.FLET_APP, port=_port)
