"""Agedashii Watudo - what to do today, and where to go someday.

Wat2Do and Future Finds, as an app of their own, with the Settings and the
help an app needs and a dashboard page does not. The two pages still ship
inside the Agedashii dashboard; they are built off `agedashii_qi` (the look)
and `agedashii_washi` (the system) and nothing else of ours.

Why the two are one app
-----------------------
Her words: "Agedashi-Watudo - What 2 DO". Wat2Do plans the day; Future Finds
is the list of places worth a ride someday. Future lives here rather than
with Trips because Wat2Do is what *imports* it - "what shall I do" and
"where might I go" are one question asked twice, and the mission generator
on the Present tab draws on the same spots.

Four surfaces, three of them on the bar
---------------------------------------
Wat2Do and Future Finds are two tabs of one page: `wat2do.build()` is
already a two-tab page and this shell does not build a second copy of
either. Moving between them is a write to `surface._TAB_INDEX["wat2do"]`
and a repaint, so there is one Present, one Future, one `future._S`, and
the navigation and the page's own tab bar are handles on the same control
rather than two states that can disagree. Which of the two is lit is *read
back* out of `_TAB_INDEX` rather than stored here.

Settings and How this works are surfaces of the shell's own. They have no
tab bar to read back, so `_HERE` remembers which is showing - and forgets
again the moment either tab page is.

Two ways around, and the bar is the one you reach for
-----------------------------------------------------
Her words: "Each of these smaller apps (not tofu) work better with the
bottom navigation bar and less with the hamburger menu so... i want the
ability to show / hide the hamburger menu trigger as wel please." So the
bar carries Wat2Do, Future Finds and Settings, and How this works hangs
under Settings. The hamburger stays: the ask was for a *switch*, and one
that starts off is indistinguishable from the feature being missing -
"how come its not on by default". Switched off, its slot goes away rather
than sitting empty. One rail, in `menu_visible()`: with the bar
switched off the trigger comes back whether or not it is switched on,
because something has to reach every surface.

Offline is the product
----------------------
The theme, the corner radius, the glow, the CRT overlay, the card layout and
every preference are local: they are read from SharedPreferences at launch -
`agedashii_washi.prefs` for what every app shares, `agedashii_watudo.prefs`
for the theme, the menu button and what only Wat2Do has an opinion about -
and never ask the network anything, so the app looks and lays out exactly
the same with the radio off. Settings and the help surface are entirely
local for the same reason. Only the day's record and the Future lists
come from the dashbored server, and those go through `agedashii_washi.store`,
which answers reads from this device's own copy and queues writes in an
outbox that survives a process death. A tick made in a tunnel is sent later,
not lost - the chip in the topbar is what says so, and the watcher at the
bottom of `main()` is what drains it when signal comes back.
"""
import asyncio

import flet as ft

from agedashii_qi import (GLOW, PAL, RAD, S, THEME_NAMES, THEMES, apply_theme,
                          crt_overlay)
from agedashii_washi import api as M
from agedashii_washi import config, panels, prefs
from agedashii_washi.surface import _TAB_INDEX, _dismiss, _error_card, _show
from agedashii_washi.widgets import BRD, PAD

from agedashii_watudo import prefs as wprefs
from agedashii_watudo import ui as U
from agedashii_watudo.pages import help as help_page
from agedashii_watudo.pages import settings as settings_page
from agedashii_watudo.pages import wat2do
from agedashii_watudo.pages import weather as weather_page

# The Agedashii pill's three colours, shared with the dashboard and with
# alola.lol. Written here rather than imported from the dashboard because
# this app must not depend on it; they are brand constants, not code.
ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

# The key `wat2do.build()` passes to `_tabs`, and therefore the key its tab
# index is stored under. Navigation here is a write to that index.
TAB_KEY = "wat2do"

# The app's four destinations, in menu order. The drawer, the bottom bar and
# Settings' own description of the navigation are all derived from these
# tables, so adding a surface means adding a line rather than editing three
# places that can disagree.
PAGES = ["wat2do", "future", "weather", "settings", "help"]

# The two that are tabs of one page, and which tab each one is. These are
# the surfaces whose "am I here" is read back out of the page's own tab
# index rather than stored - see `surface_of`.
TAB_PAGES = ("wat2do", "future")
TAB_OF = {"wat2do": 0, "future": 1}

LABELS = {"wat2do": "Wat2Do", "future": "Future Finds",
          "weather": "Weather", "settings": "Settings",
          "help": "How this works"}

# The nav label, where a drawer row and a bar slot have no room for
# four words. LABELS[help] is what the page calls itself.
NAV_LABELS = dict(LABELS, help="Help")

BLURB = {"wat2do": "today's checklist, pain, spoons and a mission",
         "future": "places worth a ride someday",
         "weather": "the bay, and anywhere you tap",
         "settings": "server, theme, the day, the origin",
         "help": "what each part does, and what needs the server"}

# Two marks per surface: the outline is the resting state and the filled one
# is where you are. Material draws both in a NavigationBar, and the pair is
# what makes a destination recognisable at a glance instead of four grey
# glyphs that differ only in outline.
ICONS = {"wat2do": ft.Icons.CHECK_CIRCLE_OUTLINE,
         "future": ft.Icons.EXPLORE_OUTLINED,
         "weather": ft.Icons.CLOUD_OUTLINED,
         "settings": ft.Icons.SETTINGS_OUTLINED,
         "help": ft.Icons.HELP_OUTLINE}
ICONS_ON = {"wat2do": ft.Icons.CHECK_CIRCLE, "future": ft.Icons.EXPLORE,
            "weather": ft.Icons.CLOUD, "settings": ft.Icons.SETTINGS,
            "help": ft.Icons.HELP}

# What rides the bottom bar. Help is not on it: it is one screen you read
# once, it lives under Settings the way a manual lives in a drawer, and a
# fourth stop would push the three that are used daily closer together.
# Every other surface is here, which is what makes the bar - and not the
# hamburger - the way around this app.
BAR_PAGES = ["wat2do", "future", "weather", "settings"]

# Where this app remembers its theme, re-exported from its own preferences
# module. `agedashii_washi.prefs` does not own a theme key - in the dashboard
# the picker is per-session and the theme is back to the default every
# launch, which is a small annoyance there and a larger one in an app you
# open to tick three boxes.
KEY_THEME = wprefs.KEY_THEME

# How often the outbox watcher looks up from its book, matched to
# api.ONLINE_RECHECK the same way the dashboard's is: one cadence for the
# whole app rather than two that disagree about how long "offline" lasts.
SYNC_POLL_SECS = 20


# Which surface the shell is parked on, for the ones that have no tab bar of
# their own to read it back out of. Wat2Do and Future Finds are NOT tracked
# here - see `surface_of` - so there is still nothing that can disagree with
# the page's own tab bar about which of those two is showing.
_HERE = {"key": "wat2do"}


def surface_of(index=None):
    """Which destination is on screen.

    Two halves, because there are two kinds of surface. Wat2Do and Future
    Finds are tabs of one page, and the page's own tab bar writes
    `_TAB_INDEX` when she swipes - so which of those two is showing is
    *derived* from that index, never stored, because a second copy would be
    wrong from the moment of a swipe until the next render. Settings and
    How this works have no tab bar, so the shell remembers them; they are
    forgotten again the instant either tab page is shown.

    `index` forces the first half, for a caller that has a tab index in
    hand and wants to know what it means.
    """
    if index is None and _HERE["key"] not in TAB_PAGES:
        return _HERE["key"]
    if index is None:
        index = _TAB_INDEX.get(TAB_KEY, 0)
    return "future" if index == 1 else "wat2do"


def menu_visible():
    """Whether the hamburger trigger is drawn at all.

    Her setting, with one rail: something has to reach every surface. With
    the bottom bar switched off the drawer is the only thing left that does,
    so the trigger comes back whether or not it is switched on - and
    Settings says so on the switch rather than letting the app quietly
    disagree with it. A setting that can strand you on one page is not a
    setting, it is a trap.
    """
    return wprefs.menu_button() or not prefs.bottom_bar_enabled()


def bar_names():
    """The bar's destinations as prose: "A, B and C"."""
    names = [LABELS[k] for k in BAR_PAGES]
    return (" and ".join(names) if len(names) < 3
            else ", ".join(names[:-1]) + " and " + names[-1])


def reach_lines():
    """How each surface can be reached, in the app's own words.

    Passed to Settings so its navigation card describes the navigation this
    shell actually builds, rather than a second opinion about it that can
    drift.
    """
    bar = (bar_names() if prefs.bottom_bar_enabled() else "off")
    menu = ("every surface, " + LABELS["help"].lower() + " included"
            if menu_visible() else "hidden")
    return [f"Bottom bar: {', '.join(LABELS[k] for k in BAR_PAGES)}"
            if prefs.bottom_bar_enabled() else "Bottom bar: off",
            f"Menu button: {menu}",
            f"{LABELS['help']} is always reachable from Settings.",
            "Present and Future are also the page's own two tabs."]


async def main(page):
    # Without this the window, the taskbar and alt-tab all say "flet".
    page.title = "Agedashii Watudo"
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
    # The server address, confirmed before the first screen paints so the
    # page does not flash an empty day and then fill it in. This is a
    # separate app with its own SharedPreferences store, so it does not
    # inherit an address set in the dashboard; Settings below is how it is
    # pointed somewhere else.
    await config.load(S.prefs)
    # Layout and appearance. Local, and the whole reason the app draws
    # correctly with no signal.
    await prefs.load(S.prefs)
    prefs.apply_runtime()
    # This app's own preferences - the theme, the menu button and the five
    # that only Wat2Do has an opinion about. A second store rather than more
    # keys in washi's, because every other Agedashii app would then carry a
    # setting about a checklist it does not have.
    await wprefs.load(S.prefs)
    await _restore_theme(page)
    # A launch always starts on Wat2Do, whatever surface the last one was
    # left on. Module state outlives a hot reload in development and the
    # honest answer to "what did I open this for" is the day, not the
    # Settings page she was last reading.
    _HERE["key"] = "wat2do"

    body = ft.Container(expand=True, padding=PAD(12, 12))

    def brand_chip():
        """The product name in the gradient pill, and nothing else.

        The dashboard's pill says "Agedashii" because that is what that app
        is called. This one is Watudo, so it says Watudo: the pill is the
        identity, and an app whose identity bar named a different app would
        be the same mistake as the "NVII" badge this replaced.
        """
        return ft.Container(
            padding=PAD(4, 12), border_radius=RAD(10),
            gradient=ft.LinearGradient(colors=[ORANGE, PINK, CYAN],
                                       begin=ft.Alignment(-1, 0),
                                       end=ft.Alignment(1, 0)),
            # alola's hero title carries `0 0 30px color-mix(accent 40%)`.
            # The pill is this app's equivalent of that title, so it gets
            # the wide version of the glow rather than the 10px heading one.
            shadow=GLOW(blur=24, opacity=0.40),
            # Eased, so turning the glow on or the corners square from
            # Settings is something the pill *does* rather than something
            # that has already happened by the time the page repaints.
            animate=U.motion(),
            content=ft.Text("Watudo", size=13, weight=ft.FontWeight.W_800,
                            color="#0d1117"),
        )

    # ------------------------------------------------------- the outbox chip
    # Reads heal themselves: the store re-probes behind the UI and a stale
    # panel quietly refills. Writes had nothing that did that. A tick made
    # with no signal goes into the outbox and stays there until some later
    # write happens to succeed - which on a phone that spends its offline
    # time in a tunnel and its online time locked in a pocket could be days,
    # with nothing on screen saying so.
    #
    # So: one chip in the chrome that says what is waiting, and two things
    # that drain it - coming back to the foreground, and the watcher below
    # noticing signal returned. Created once and reused across renders, the
    # same way `body` is: a background drain needs a control it can still
    # find after a repaint.
    sync_icon = ft.Icon(ft.Icons.CLOUD_UPLOAD, size=13)
    sync_text = ft.Text("", size=10, weight=ft.FontWeight.W_600)
    # U.tap rather than a bare `ink=True`: the chip's whole job is to be
    # tapped when something is stuck, and the drain behind it takes as long
    # as a socket timeout. A themed ripple is what says the tap landed
    # before the queue has anything new to report.
    sync_chip = U.tap(ft.Container(
        padding=PAD(3, 8), border_radius=RAD(9), visible=False,
        content=ft.Row(spacing=5, tight=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[sync_icon, sync_text])))

    def paint_sync():
        """Say what the server has not got yet, in the server's own terms."""
        p = PAL()
        st = M.sync_status()
        label = st.get("label") or ""
        stuck = (st.get("parked") or 0) + (st.get("conflict") or 0)
        sync_text.value = label
        sync_text.color = p["pink"] if stuck else p["text2"]
        sync_icon.color = p["pink"] if stuck else p["text2"]
        # `.icon`, not `.name`. ft.Icon exposes the glyph as `icon` in Flet
        # 0.86 and the class is not slotted, so `sync_icon.name = ...` binds
        # a stray attribute, raises nothing, and leaves the cloud pointing
        # up while the queue is stuck. Silent, and only visible in the one
        # state you reach by losing data.
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

        `force` is the tap: it also un-parks writes the server refused,
        which is the only way back for an item that burned its attempts
        while the server was busy answering 502s.
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
        # the last time this app was looked at, which is exactly when a
        # phone is most likely to have found wifi in between.
        if getattr(e, "state", None) in (ft.AppLifecycleState.RESUME,
                                         ft.AppLifecycleState.SHOW):
            sync_now()

    page.on_app_lifecycle_state_change = on_lifecycle

    # ----------------------------------------------------------- navigation
    def go(key):
        """Move to a destination. Fast, because a slow handler drops taps.

        All this does is write where we are and repaint. Flet runs a
        synchronous handler on its event loop, so anything expensive here
        would hold the old frame on screen and swallow the next taps - the
        exact failure that once made the dashboard's status dots look dead.
        The page's own slow reads are already deferred behind `page.run_task`
        inside `wat2do`/`future`; nothing is added to them here.
        """
        render(key)

    def _drawer_go(key):
        page.run_task(_drawer_go_async, key)

    async def _drawer_go_async(key):
        await page.close_drawer()
        go(key)

    def _drawer_dialog(open_dialog):
        """Close the drawer, then open a dialog. Both are page-level, and a
        dialog shown under an open drawer is a dialog nobody can reach."""
        async def handler():
            await page.close_drawer()
            open_dialog(None)
        return lambda: page.run_task(handler)

    async def open_menu(e):
        await page.show_drawer()

    def nav_drawer():
        """Every surface, plus the one thing the bar cannot carry.

        Only built and attached when the trigger is visible - there is no
        point keeping a drawer nobody can open, and building one costs a
        walk of four rows on every render.

        Rebuilt on every render because it is themed, and one built once at
        startup would keep the old palette after a theme change.

        Flat, with no chevrons: the dashboard's drawer nests because its
        pages have tabs inside them worth reaching directly, and here the
        tabs *are* the destinations. A chevron that expands to the row
        above it would be furniture.
        """
        p = PAL()
        here = surface_of()
        rows = [ft.Container(height=12)]
        rows += [U.nav_row(k, LABELS[k], ICONS_ON[k] if k == here else ICONS[k],
                           lit=k == here, on_click=_drawer_go, detail=BLURB[k])
                 for k in PAGES]
        rows += [
            ft.Container(height=1, bgcolor=p["border"],
                         margin=ft.Margin(left=14, top=10, right=14, bottom=10)),
            # The bar carries three of the four surfaces, so the drawer has
            # to carry something it does not or it is a second copy of the
            # bar: How this works, above, and the quick theme switch here.
            U.action_row(f"Theme - {THEME_NAMES.get(S.theme, S.theme)}",
                         ft.Icons.PALETTE, _drawer_dialog(open_theme_picker),
                         detail="all 24, without leaving this page"),
        ]
        return ft.NavigationDrawer(bgcolor=p["bg2"], controls=rows)

    def bottom_pick(e):
        idx = e.control.selected_index
        if 0 <= idx < len(BAR_PAGES):
            go(BAR_PAGES[idx])

    def bottom_bar():
        """The primary navigation: three stops, always the same three.

        `selected_index` must be a real index - Flet asserts on it - and one
        surface is deliberately not on the bar. How this works lives under
        Settings, so standing on it lights Settings, which is both a true
        statement about where it is and the way back.
        """
        here = surface_of()
        lit = BAR_PAGES.index(here if here in BAR_PAGES else "settings")
        return ft.NavigationBar(
            selected_index=lit,
            destinations=[ft.NavigationBarDestination(
                icon=ICONS[k], selected_icon=ICONS_ON[k], label=LABELS[k])
                for k in BAR_PAGES],
            animation_duration=U.MOTION_MS,
            on_change=bottom_pick,
        )

    # -------------------------------------------------------------- dialogs
    def open_theme_picker(e):
        """A named, tappable list of every theme.

        Twenty-four is far too many to walk one tap at a time, and a tooltip
        naming the current one needs a mouse, which a phone does not have.
        Each row previews itself in its own colours.
        """
        p = PAL()

        def pick(key):
            def handler(e):
                _dismiss(page, dlg)
                apply_theme(page, key)
                render()
                _remember_theme(key)
            return handler

        def swatch(key):
            t = THEMES[key]
            on = key == S.theme
            # U.tap rather than a bare `ink=True`: the ripple is tinted with
            # the theme the row is offering, so the feedback previews the
            # choice as well as acknowledging the tap.
            return U.tap(ft.Container(
                padding=PAD(10, 8), border_radius=RAD(8),
                on_click=pick(key),
                bgcolor=ft.Colors.with_opacity(0.14, p["accent"]) if on else None,
                content=ft.Row(spacing=10, controls=[
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
                ])), tint=t["accent"])

        dlg = ft.AlertDialog(
            bgcolor=p["bg2"],
            title=ft.Text(f"Theme - {THEME_NAMES.get(S.theme, S.theme)}",
                          size=15, color=p["text"]),
            content=ft.Container(width=300, height=380, content=ft.Column(
                spacing=2, scroll=ft.ScrollMode.AUTO,
                controls=[swatch(k) for k in THEME_NAMES])),
            actions=[ft.TextButton(content="Close",
                                   on_click=lambda e: _dismiss(page, dlg))],
        )
        _show(page, dlg)

    def _remember_theme(key):
        # Into the cache first, so Settings' own picker and Diagnostics show
        # the new theme immediately, and to storage second.
        wprefs.set_theme(key)
        run_task = getattr(page, "run_task", None)
        if callable(run_task) and S.prefs is not None:
            run_task(_write_theme, key)

    async def _write_theme(key):
        try:
            await S.prefs.set(KEY_THEME, key)
        except Exception:              # noqa: BLE001 - storage can refuse
            pass

    # -------------------------------------------------------- arrange mode
    async def toggle_customize(e):
        """Enter or leave arrange-the-cards mode on the page you are on.

        Wat2Do's Present tab is a card stack - date, checklist, vitals,
        weather, out, today - so the whole panels editor applies here
        unchanged. Every drag and resize already saves as it happens
        (panels._apply); Done is the belt to that braces, and both go
        through prefs.save because there is one store for layout.
        """
        on = panels.set_editing(not panels.editing())
        if not on and S.prefs is not None:
            await prefs.save(S.prefs)
        render()

    def topbar():
        """The pill, what is queued, and the two things that are not a place.

        No gear any more. Settings is a surface on the bottom bar, and a gear
        up here would be a third way to the same screen - the thing that made
        the old drawer a copy of the old bar. The palette stays because it is
        not navigation: it changes the app you are looking at without leaving
        the page you are on.

        When the hamburger is hidden its slot is *gone*, not blank: an
        IconButton with nothing in it still takes its 48px, and a gap where a
        control used to be reads as a control that failed to draw.
        """
        p = PAL()
        editing = panels.editing()
        # Only on a surface that has a card stack to arrange. Future Finds,
        # Settings and Help do not; a button that does nothing is worse than
        # no button.
        arrange = [ft.IconButton(
            ft.Icons.CHECK if editing else ft.Icons.DASHBOARD_CUSTOMIZE,
            icon_color=p["accent"] if editing else p["text2"],
            tooltip="Done arranging" if editing else "Arrange the cards on this page",
            on_click=toggle_customize)] if (panels.has_surface() or editing) else []
        menu = [ft.IconButton(ft.Icons.MENU, icon_color=p["text2"],
                              tooltip="Everything this app can show",
                              on_click=open_menu)] if menu_visible() else []
        return ft.Row(spacing=10, controls=[
            *menu,
            brand_chip(),
            ft.Container(expand=True),
            sync_chip,
            *arrange,
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)}",
                          on_click=open_theme_picker),
        ])

    # -------------------------------------------------------------- surfaces
    def build_surface(key):
        """The body for one destination.

        Wat2Do and Future Finds are one call, not two: `wat2do.build()` is
        already a two-tab page and this shell does not build a second copy of
        either. Which tab it opens on is `_TAB_INDEX`, which `render` has
        just written - so the drawer, the bottom bar and the page's own tab
        bar are three handles on one control rather than three states that
        can disagree.
        """
        if key == "settings":
            return settings_page.build(page, on_change=render, go=go,
                                       reach=reach_lines(),
                                       bar_names=bar_names())
        if key == "help":
            return help_page.build(page, go=go)
        if key == "weather":
            return weather_page.build(page)
        # No tab bar while the bottom bar is carrying these two: on a phone
        # that was the same two choices in two rows, one above the other.
        # With the bar off, the page's own tabs are how you get between them
        # and they come back.
        return wat2do.build(page, tabs=not prefs.bottom_bar_enabled())

    # --------------------------------------------------------------- render
    def render(key=None):
        """Paint the whole shell around the surface `key`.

        `key` is optional: with none, the surface the page's own tab bar is
        already showing is repainted, which is what a theme change or a
        finished write wants. There is no `current` variable to keep in
        step - see `surface_of`.
        """
        p = PAL()
        if key not in PAGES:
            key = surface_of()
        _HERE["key"] = key
        if key in TAB_PAGES:
            _TAB_INDEX[TAB_KEY] = TAB_OF[key]
        # Cleared before the body builds and set again by panels.layout(),
        # so the topbar - built after the body, below - knows whether this
        # surface has a card stack worth offering Customize for.
        panels.begin_render()
        paint_sync()
        try:
            content = build_surface(key)
        except Exception as e:         # noqa: BLE001 - a broken page is not a dead app
            # `_tabs` guards each tab body it *calls*, but wat2do passes its
            # two bodies already built, so a failure inside them lands here.
            # Saying so beats a blank window with nothing to read.
            content = _error_card(f"{LABELS[key]} could not be built.", e)
        body.content = content
        body.bgcolor = p["bg"]
        # Built only when there is a trigger for it. A drawer nobody can open
        # is four rows of work per render for nothing.
        page.drawer = nav_drawer() if menu_visible() else None
        page.controls.clear()
        chrome = [ft.Container(padding=PAD(8, 12), bgcolor=p["bg"],
                               content=topbar()),
                  body]
        # The bar is the primary navigation, so it is on unless she has said
        # otherwise; off, the content gets the height back and `menu_visible`
        # has already put the drawer trigger back to reach everything.
        if prefs.bottom_bar_enabled():
            chrome.append(bottom_bar())
        shell = ft.Column(spacing=0, expand=True, controls=chrome)
        # Keep the chrome out from under the hardware. On a phone the topbar
        # otherwise draws beneath the clock and the gesture bar sits over the
        # bottom row. Flutter reports zero intrusions on desktop, so this is
        # a no-op on the laptop rather than a second layout to keep working.
        # It wraps the chrome and NOT the CRT overlay below: a screen effect
        # that stopped short of the cutout would draw a bright band exactly
        # where the cutout is.
        shell = ft.SafeArea(content=shell, expand=True)
        # The CRT vignette goes on here, once, over the whole shell - not per
        # surface and not per card. Two controls for the entire app when it
        # is on, none at all when it is off, which is the default. The
        # overlay is ignore_interactions throughout, so everything underneath
        # is still hit-tested exactly as it was.
        if prefs.crt_overlay():
            shell = ft.Stack(expand=True, controls=[shell, crt_overlay()])
        page.add(shell)
        page.update()

    # A page that writes to the server needs to re-read and repaint after the
    # write lands, and the panels editor needs the same after a drag.
    # `forms.rebuilder` and `panels._apply` both look for this on S; without
    # it they replace page.controls directly, which would throw away the
    # topbar and the nav bar along with the body. Parked on S for the same
    # reason S.prefs is: it must stay referenced.
    S.rerender = lambda: render()

    render("wat2do")

    # Launch is itself a resume: whatever the last session queued has been
    # waiting across a process death, and the store kept it.
    sync_now()
    # Parked on S alongside the rest, so nothing drops the only reference to
    # the watcher and lets it be garbage collected mid-sleep.
    S.sync_watcher = page.run_task(watch_outbox)


async def _restore_theme(page):
    """Put back the theme this device was last left on.

    Guarded end to end: storage can be empty (first launch), unavailable
    (some web contexts), or hold the name of a theme that has since been
    removed. Any of those falls through to the shipped default rather than
    taking the launch down.

    Read straight from storage rather than from `wprefs.theme()` on purpose:
    this runs before the first paint and must work even if that load was the
    one key the platform refused.
    """
    try:
        key = await S.prefs.get(KEY_THEME)
    except Exception:                  # noqa: BLE001 - no storage, ship default
        return
    if isinstance(key, str) and key in THEMES:
        apply_theme(page, key)
        wprefs.set_theme(key)


# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call.
#
# Open a real window when there is a display, serve over HTTP when there is
# not, and let either be overridden:
#
#   AGEDASHII_HEADLESS=1   force the HTTP server (the Pi does this)
#   AGEDASHII_PORT=8553    pin the port; 0 lets the OS pick a free one
#
# One port per app, all four distinct: the dashboard 8550, Moto and FoodZen
# 8552, Watudo 8553. On the Pi every Agedashii app is headless and they are
# expected to be up at the same time, so a shared default would mean the
# second one to start dying with "address already in use".
if __name__ == "__main__":
    import os as _os

    _headless = _os.environ.get("AGEDASHII_HEADLESS") == "1" or not (
        _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY"))
    _port = int(_os.environ.get("AGEDASHII_PORT") or (8553 if _headless else 0))
    ft.run(main, view=None if _headless else ft.AppView.FLET_APP, port=_port)
