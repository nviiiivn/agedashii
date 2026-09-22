"""Agedashii Watudo - what to do today, and where to go someday.

Wat2Do and Future Finds, as an app of their own. They ship inside the
Agedashii dashboard too; this is the same two surfaces with a shell around
them instead of eight other pages. Nothing in `agedashii_watudo` was changed
to make this build - the pages are imported exactly as the dashboard imports
them, off `agedashii_qi` (the look) and `agedashii_washi` (the system).

Why the two are one app
-----------------------
Her words: "Agedashi-Watudo - What 2 DO". Wat2Do plans the day; Future Finds
is the list of places worth a ride someday. Future lives here rather than
with Trips because Wat2Do is what *imports* it - "what shall I do" and
"where might I go" are one question asked twice, and the mission generator
on the Present tab draws on the same spots.

One page, two destinations
--------------------------
`wat2do.build()` is already a two-tab page (Present | Future), and this
shell does not build a second copy of either. The drawer and the bottom bar
set `surface._TAB_INDEX["wat2do"]` and re-render, which is exactly what the
dashboard's drawer does to reach Tools > FoodZen > EatZen in one tap. So
there is one Present, one Future, one `future._S`, and the navigation and
the tab bar are two handles on the same control rather than two states that
can disagree. Which destination is lit is *read back* out of `_TAB_INDEX`
rather than stored here, so they cannot drift.

Offline is the product
----------------------
The theme, the corner radius, the glow, the CRT overlay, the card layout and
every preference are local: they are read from SharedPreferences at launch
and never ask the network anything, so the app looks and lays out exactly
the same with the radio off. Only the day's record and the Future lists
come from the dashbored server, and those go through `agedashii_washi.store`,
which answers reads from this device's own copy and queues writes in an
outbox that survives a process death. A tick made in a tunnel is sent later,
not lost - the chip in the topbar is what says so, and the watcher at the
bottom of `main()` is what drains it when signal comes back.
"""
import asyncio

import flet as ft

from agedashii_qi import (GLOW, PAL, RAD, RADIUS_CHOICES, S, THEME_NAMES,
                          THEMES, apply_theme, crt_overlay)
from agedashii_washi import api as M
from agedashii_washi import config, panels, prefs
from agedashii_washi.surface import _TAB_INDEX, _dismiss, _error_card, _show
from agedashii_washi.widgets import BRD, PAD, sub

from agedashii_watudo.pages import wat2do

# The Agedashii pill's three colours, shared with the dashboard and with
# alola.lol. Written here rather than imported from the dashboard because
# this app must not depend on it; they are brand constants, not code.
ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

# The key `wat2do.build()` passes to `_tabs`, and therefore the key its tab
# index is stored under. Navigation here is a write to that index.
TAB_KEY = "wat2do"

# The app's two destinations, in menu order, and which tab of the page each
# one is. Both halves of the drawer and the whole bottom bar are derived
# from these three tables, so adding a surface means adding a line.
PAGES = ["wat2do", "future"]
TAB_OF = {"wat2do": 0, "future": 1}
LABELS = {"wat2do": "Wat2Do", "future": "Future Finds"}
ICONS = {"wat2do": ft.Icons.CHECK_CIRCLE, "future": ft.Icons.EXPLORE}

# Where this app remembers its theme. `agedashii_washi.prefs` does not own a
# theme key - in the dashboard the picker is per-session and the theme is
# back to the default every launch, which is a small annoyance there and a
# larger one in an app you open to tick three boxes. The name is the one
# prefs would use if it ever adopted it, so adopting it later reads the
# same value rather than starting again.
KEY_THEME = "agedashii.theme"

# How often the outbox watcher looks up from its book, matched to
# api.ONLINE_RECHECK the same way the dashboard's is: one cadence for the
# whole app rather than two that disagree about how long "offline" lasts.
SYNC_POLL_SECS = 20


def surface_of(index=None):
    """Which destination the page's tab index means.

    Derived rather than stored. The tab bar inside the page writes
    `_TAB_INDEX` when she swipes, and a second copy of "where am I" kept up
    here would be wrong from that moment until the next render.
    """
    if index is None:
        index = _TAB_INDEX.get(TAB_KEY, 0)
    return "future" if index == 1 else "wat2do"


async def main(page):
    # Without this the window, the taskbar and alt-tab all say "flet".
    page.title = "Agedashii Watudo"
    apply_theme(page)

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
    await _restore_theme(page)

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

        All this does is write a tab index and repaint. Flet runs a
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
        """Everything this app can show, plus the two things it can change.

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

        def page_row(k):
            on = k == here
            return ft.Container(
                padding=PAD(14, 9), border_radius=RAD(8), ink=True,
                on_click=lambda e, key=k: _drawer_go(key),
                # The row you are standing on is the app's active nav item;
                # glow on, it gets alola's accent shadow. Only the active
                # one, so the drawer does not become a wall of light.
                shadow=GLOW(blur=14, opacity=0.35) if on else None,
                bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else None,
                content=ft.Row(spacing=12, controls=[
                    ft.Icon(ICONS[k], size=19,
                            color=p["accent"] if on else p["text2"]),
                    ft.Text(LABELS[k], size=14,
                            weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                            color=p["text"] if on else p["text2"]),
                ]))

        def action_row(label, icon, run):
            return ft.Container(
                padding=PAD(14, 9), border_radius=RAD(8), ink=True,
                on_click=lambda e: run(),
                content=ft.Row(spacing=12, controls=[
                    ft.Icon(icon, size=19, color=p["text2"]),
                    ft.Text(label, size=14, color=p["text2"]),
                ]))

        rows += [page_row(k) for k in PAGES]
        rows += [
            ft.Container(height=1, bgcolor=p["border"],
                         margin=ft.Margin(left=14, top=10, right=14, bottom=10)),
            # The bottom bar carries the two surfaces and nothing else, so
            # these two live here. That is what stops the drawer from being
            # a second copy of the bar.
            action_row(f"Theme - {THEME_NAMES.get(S.theme, S.theme)}",
                       ft.Icons.PALETTE, _drawer_dialog(open_theme_picker)),
            action_row("Settings", ft.Icons.SETTINGS,
                       _drawer_dialog(open_settings)),
        ]
        return ft.NavigationDrawer(bgcolor=p["bg2"], controls=rows)

    def bottom_pick(e):
        idx = e.control.selected_index
        if 0 <= idx < len(PAGES):
            go(PAGES[idx])

    def bottom_bar():
        # selected_index must be a real index (Flet asserts on it), and
        # `surface_of` can only ever answer with a member of PAGES, so there
        # is no "not on the bar" case to fall back from.
        here = PAGES.index(surface_of())
        return ft.NavigationBar(
            selected_index=here,
            destinations=[ft.NavigationBarDestination(icon=ICONS[k],
                                                      label=LABELS[k])
                          for k in PAGES],
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
            return ft.Container(
                padding=PAD(10, 8), border_radius=RAD(8), ink=True,
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
                ]))

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

    def open_settings(e):
        """The server, and the three knobs that are not the theme.

        Deliberately small. The dashboard has a full Settings page and this
        app does not need one - but it does need to be pointable at a
        server, because it has its own preferences store and so cannot
        inherit an address set over there.
        """
        p = PAL()
        status = ft.Text(_server_line(), size=11, color=p["text2"],
                         selectable=True)
        field = ft.TextField(
            value=config.SERVER_URL, hint_text="http://host:6900", dense=True,
            filled=True, text_size=12, border_color=p["border"],
            focused_border_color=p["accent"], color=p["text"], expand=True)

        async def check(e=None):
            url = (field.value or "").strip()
            if not url:
                return
            status.value = "checking..."
            status.color = p["text2"]
            page.update()
            # Blocking: probe() opens a socket with a 3 s timeout, and a
            # three-second synchronous handler is three seconds of dropped
            # taps. Off the loop it goes.
            ok, detail = await asyncio.to_thread(config.set_url, url)
            await config.save(S.prefs, url)
            # Pointing somewhere else invalidates every cached answer; they
            # came from a different server.
            M.refresh()
            status.value = f"{config.SERVER_URL} - {detail}"
            status.color = p["text2"] if ok else p["pink"]
            page.update()
            render()

        field.on_submit = check

        def knob(label, detail, value, on_change):
            return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[
                ft.Column(spacing=1, expand=True, controls=[
                    ft.Text(label, size=13, color=p["text"]),
                    sub(detail, 11)]),
                ft.Switch(value=value, on_change=on_change,
                          active_color=p["accent"]),
            ])

        def set_glow(e):
            prefs.set_accent_glow(bool(e.control.value))
            _persist_and_render()

        def set_crt(e):
            prefs.set_crt_overlay(bool(e.control.value))
            _persist_and_render()

        def set_bar(e):
            prefs.set_bottom_bar_enabled(bool(e.control.value))
            _persist_and_render()

        def radius_chip(value, label):
            on = prefs.corner_radius() == value

            def pick(e):
                prefs.set_corner_radius(value)
                _persist_and_render()
                _dismiss(page, dlg)
                open_settings(None)

            return ft.Container(
                padding=PAD(4, 10), border_radius=RAD(20), ink=True,
                on_click=pick,
                bgcolor=ft.Colors.with_opacity(0.14, p["accent"]) if on else None,
                border=BRD(p["accent"] if on else p["border"], 1),
                content=ft.Text(label, size=11,
                                color=p["accent"] if on else p["text2"]))

        dlg = ft.AlertDialog(
            bgcolor=p["bg2"],
            title=ft.Text("Settings", size=15, color=p["text"]),
            content=ft.Container(width=320, content=ft.Column(
                spacing=14, tight=True, scroll=ft.ScrollMode.AUTO, controls=[
                    ft.Column(spacing=6, controls=[
                        ft.Text("Server", size=13, color=p["text"]),
                        sub("Where the day's record and the Future lists "
                            "come from. Everything else is on the device.", 11),
                        ft.Row(spacing=6, controls=[
                            field,
                            ft.IconButton(ft.Icons.REFRESH,
                                          icon_color=p["accent"],
                                          tooltip="Check this address",
                                          on_click=check)]),
                        status,
                    ]),
                    ft.Container(height=1, bgcolor=p["border"]),
                    # Worth having here rather than only in the dashboard's
                    # Settings page: the page already carries a Present |
                    # Future tab bar of its own, so on a small screen the
                    # bottom bar is a second row of the same two choices.
                    # Some people want both; this is how to have one.
                    knob("Bottom bar", "The two surfaces along the bottom. "
                         "The page's own tab bar reaches them either way.",
                         prefs.bottom_bar_enabled(), set_bar),
                    knob("Accent glow", "alola's accent shadow on headings "
                         "and the active nav item.", prefs.accent_glow(),
                         set_glow),
                    knob("CRT overlay", "Scanlines, halftone and a vignette "
                         "over the whole app.", prefs.crt_overlay(), set_crt),
                    ft.Column(spacing=6, controls=[
                        ft.Text("Corner radius", size=13, color=p["text"]),
                        ft.Row(spacing=6, wrap=True, controls=[
                            radius_chip(v, f"{v}px" if v else "Square")
                            for v in RADIUS_CHOICES]),
                    ]),
                ])),
            actions=[ft.TextButton(content="Close",
                                   on_click=lambda e: _dismiss(page, dlg))],
        )
        _show(page, dlg)

    def _server_line():
        st = config.STATUS
        return f"{st.get('url') or config.SERVER_URL} - {st.get('detail') or ''}"

    def _persist_and_render():
        """Push a changed knob into the drawing layer, repaint, and save.

        `apply_runtime` is what makes a preference *apply* - the appearance
        values live on `agedashii_qi`'s S, not in prefs, so that RAD() and
        GLOW() cost one attribute lookup per control.
        """
        prefs.apply_runtime()
        render()
        run_task = getattr(page, "run_task", None)
        if callable(run_task) and S.prefs is not None:
            run_task(prefs.save, S.prefs)

    def _remember_theme(key):
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
        p = PAL()
        editing = panels.editing()
        # Only on a surface that has a card stack to arrange. Future Finds
        # does not; a button that does nothing is worse than no button.
        arrange = [ft.IconButton(
            ft.Icons.CHECK if editing else ft.Icons.DASHBOARD_CUSTOMIZE,
            icon_color=p["accent"] if editing else p["text2"],
            tooltip="Done arranging" if editing else "Arrange the cards on this page",
            on_click=toggle_customize)] if (panels.has_surface() or editing) else []
        return ft.Row(spacing=10, controls=[
            ft.IconButton(ft.Icons.MENU, icon_color=p["text2"],
                          on_click=open_menu),
            brand_chip(),
            ft.Container(expand=True),
            sync_chip,
            *arrange,
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)}",
                          on_click=open_theme_picker),
            ft.IconButton(ft.Icons.SETTINGS, icon_color=p["text2"],
                          tooltip="Server and appearance",
                          on_click=open_settings),
        ])

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
        _TAB_INDEX[TAB_KEY] = TAB_OF[key]
        # Cleared before the body builds and set again by panels.layout(),
        # so the topbar - built after the body, below - knows whether this
        # surface has a card stack worth offering Customize for.
        panels.begin_render()
        paint_sync()
        try:
            content = wat2do.build(page)
        except Exception as e:         # noqa: BLE001 - a broken page is not a dead app
            # `_tabs` guards each tab body it *calls*, but wat2do passes its
            # two bodies already built, so a failure inside them lands here.
            # Saying so beats a blank window with nothing to read.
            content = _error_card("Watudo could not be built.", e)
        body.content = content
        body.bgcolor = p["bg"]
        page.drawer = nav_drawer()
        page.controls.clear()
        chrome = [ft.Container(padding=PAD(8, 12), bgcolor=p["bg"],
                               content=topbar()),
                  body]
        # The bar repeats what the drawer lists; off, the content gets the
        # height back and the drawer still reaches both surfaces.
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
    """
    try:
        key = await S.prefs.get(KEY_THEME)
    except Exception:                  # noqa: BLE001 - no storage, ship default
        return
    if isinstance(key, str) and key in THEMES:
        apply_theme(page, key)


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
