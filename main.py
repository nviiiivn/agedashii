import asyncio

import flet as ft
from flet_app.ui import PAL, PAD, BRD, S, apply_theme, dot
from flet_app.pages.lab import _TAB_INDEX
from flet_app.pages.eatzen import _show, _dismiss
from flet_app.theme_data import THEMES, THEME_NAMES
from flet_app.pages import home, lab, tools, trips, weather, garage, wat2do, extras, settings
from flet_app import api as M
from flet_app import config
from flet_app import prefs

ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

# Every page, in menu order. The drawer lists all of them; the bottom bar
# carries the five reached most often plus a "More" button that opens the
# drawer. Splitting them into two disjoint sets, as this did before, left
# four pages reachable only from a hamburger and no single place that
# listed everything.
PAGES = ["home", "lab", "tools", "garage", "wat2do",
         "trips", "weather", "extras", "settings"]
MAIN = ["home", "lab", "tools", "garage", "wat2do"]
MORE = "\x00more"  # sentinel: not a page, it opens the drawer
LABELS = {"home": "Home", "lab": "Lab", "tools": "Tools", "garage": "Garage", "wat2do": "Wat2Do",
          "trips": "Trips", "weather": "Weather", "extras": "Extras", "settings": "Settings",
          MORE: "More"}
ICONS = {"home": ft.Icons.HOME, "lab": ft.Icons.DNS, "tools": ft.Icons.CONSTRUCTION,
         "garage": ft.Icons.TWO_WHEELER, "wat2do": ft.Icons.CHECK_CIRCLE, "trips": ft.Icons.MAP,
         "weather": ft.Icons.CLOUD, "extras": ft.Icons.WIDGETS, "settings": ft.Icons.SETTINGS,
         MORE: ft.Icons.MORE_HORIZ}
BOTTOM = MAIN + [MORE]

# What sits inside each page, so the drawer can offer it directly instead of
# making you land on a page and hunt for the tab. Each entry is
# (label, [(tab-group key, index), ...], indent depth) - the list sets every
# level on the way down, which is how Tools > FoodZen > EatZen is reachable
# in one tap. The keys and indices must match the _tabs(..., key=) calls in
# the page modules; the test walks both and fails if they drift.
SUBPAGES = {
    "lab": [("Machines", [("lab", 0)], 1), ("Network", [("lab", 1)], 1),
            ("Services", [("lab", 2)], 1), ("Docker", [("lab", 3)], 1),
            ("Qbit", [("lab", 4)], 1)],
    "tools": [
        ("Feeds", [("tools", 0)], 1),
        ("Vault", [("tools", 1)], 1),
        ("Journal", [("tools", 2)], 1),
        ("FoodZen", [("tools", 3)], 1),
        ("Grocery", [("tools", 3), ("foodzen", 0)], 2),
        ("Meals", [("tools", 3), ("foodzen", 1)], 2),
        ("Scrape", [("tools", 3), ("foodzen", 2)], 2),
        ("Expenses", [("tools", 3), ("foodzen", 3)], 2),
        ("EatZen", [("tools", 3), ("foodzen", 4)], 2),
        ("BPM", [("tools", 3), ("foodzen", 5)], 2),
    ],
    "garage": [("Home", [("garage", 0)], 1),
               ("Mechanic", [("garage", 1)], 1),
               ("Flipper", [("garage", 2)], 1)],
    "weather": [("Bay Area", [("weather", 0)], 1),
                ("Explore", [("weather", 1)], 1)],
    "extras": [("Projects", [("extras", 0)], 1), ("AI", [("extras", 1)], 1),
               ("Future", [("extras", 2)], 1),
               ("Obligations", [("extras", 3)], 1)],
}

# How often the outbox watcher looks up from its book. Matched to
# api.ONLINE_RECHECK, which is how long a "we are offline" verdict is trusted
# for reads - one cadence for the whole app rather than two that disagree.
SYNC_POLL_SECS = 20


async def main(page):
    apply_theme(page)

    # Persistent storage is a Flet *service*: constructing it inside the page
    # context registers it, and it is unregistered again the moment nothing
    # holds a strong reference - hence parking it on S rather than a local.
    S.prefs = ft.SharedPreferences()
    # Restore the saved server address and confirm it still answers before the
    # first screen paints, so pages do not flash sample data then swap.
    await config.load(S.prefs)
    # Layout choices (page order, hidden pages, bottom bar, units,
    # sodium target, refresh interval) come from the same storage.
    await prefs.load(S.prefs)
    prefs.apply_runtime()

    body = ft.Container(expand=True, padding=PAD(12, 12))

    def brand_chip():
        p = PAL()
        return ft.Container(
            padding=PAD(4, 12), border_radius=10,
            gradient=ft.LinearGradient(colors=[ORANGE, PINK, CYAN],
                                       begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0)),
            content=ft.Text("NVII", size=13, weight=ft.FontWeight.W_800, color="#0d1117"),
        )

    def machine_dots():
        """One dot per machine: coloured when up, hollow when down, and each
        one opens that machine in Lab.

        They used to be 8px circles with no label and no handler, and they
        lied: api.machines() called a host online whenever its system widget
        was non-empty, and an unreachable host answers {"error":
        "unreachable"} - which is non-empty. SP3 was down and lit.
        """
        p = PAL()

        def go(mid):
            def handler(e):
                # Lab's first tab is Machines; _tabs reads its starting
                # index out of _TAB_INDEX.
                _TAB_INDEX["lab"] = 0
                render("lab")
            return handler

        def one(m):
            up = m["online"]
            why = m.get("error") or ""
            tip = f"{m['display']} - " + (
                f"up, {m.get('uptime', '')}".strip().rstrip(",") if up
                else (f"down ({why})" if why else "down"))
            return ft.Container(
                width=16, height=16, border_radius=8, ink=True,
                tooltip=tip + " - tap to open in Lab",
                alignment=ft.Alignment(0, 0), on_click=go(m["id"]),
                content=ft.Container(
                    width=8, height=8, border_radius=4,
                    bgcolor=m["color"] if up else None,
                    # Hollow, not grey: a grey dot beside coloured ones still
                    # reads as "a machine", where an empty ring reads as off.
                    border=None if up else BRD(p["text3"], 1)))

        return ft.Row(spacing=2, controls=[one(m) for m in M.MACHINES])

    # ------------------------------------------------------- the outbox chip
    # Reads heal themselves: api._wake re-probes behind the UI and a stale
    # panel quietly refills. Writes had nothing that did that. An edit made
    # with no signal went into the outbox and stayed there until the user
    # happened to make another write that succeeded - which on a phone that
    # spends its offline time in a tunnel and its online time locked in a
    # pocket could be days, with no way to tell it had happened.
    #
    # So: one chip in the chrome that says what is waiting, and two things
    # that drain it - coming back to the foreground, and the watcher below
    # noticing signal returned. Both live here rather than on a page because
    # the outbox is the whole app's, not any one page's.
    #
    # Created once and reused across renders, the same way `body` is: a
    # background drain needs a control it can still find after a repaint.
    sync_icon = ft.Icon(ft.Icons.CLOUD_UPLOAD, size=13)
    sync_text = ft.Text("", size=10, weight=ft.FontWeight.W_600)
    sync_chip = ft.Container(
        padding=PAD(3, 8), border_radius=9, visible=False, ink=True,
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
        sync_icon.name = ft.Icons.CLOUD_OFF if stuck else ft.Icons.CLOUD_UPLOAD
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

    # Which page is on screen. Held in a list so the nested handlers below can
    # write to it, and so a theme change can repaint the page you are on
    # instead of bouncing you back to Home.
    current = ["home"]

    async def drawer_pick(e):
        idx = e.control.selected_index
        if idx is not None and 0 <= idx < len(PAGES):
            await page.close_drawer()
            pages = _drawer_pages()
            if 0 <= idx < len(pages):
                render(pages[idx])


    def _drawer_pages():
        return prefs.visible_pages()

    def _bottom_items():
        """Bottom bar = the owner's chosen pages, plus More for the rest."""
        return prefs.bottom_bar_pages() + [MORE]

    def go_sub(page_key, tab_path):
        """Jump straight to a tab inside a page, e.g. Tools > FoodZen > EatZen.

        _tabs() reads its starting index out of _TAB_INDEX, keyed by the same
        name it saves under, so setting those keys before rendering lands on
        the right tab without any new plumbing.
        """
        async def handler(e):
            for tab_key, index in tab_path:
                _TAB_INDEX[tab_key] = index
            await page.close_drawer()
            render(page_key)
        return handler

    def nav_drawer():
        p = PAL()
        # Rebuilt on every render: the drawer is themed, and one built once at
        # startup would keep the old palette after a theme change.
        #
        # Hand-built rows rather than NavigationDrawerDestination, because
        # that control is flat and the whole point here is depth: every page
        # with tabs shows them, so Tools > FoodZen > EatZen is one tap from
        # the menu instead of a page you have to land on and then hunt
        # through.
        pages = _drawer_pages()
        rows = [ft.Container(height=12)]

        def page_row(k):
            on = k == current[0]
            return ft.Container(
                padding=PAD(14, 9), border_radius=8, ink=True,
                bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else None,
                on_click=lambda e, key=k: _drawer_go(key),
                content=ft.Row(spacing=12, controls=[
                    ft.Icon(ICONS[k], size=19,
                            color=p["accent"] if on else p["text2"]),
                    ft.Text(LABELS[k], size=14,
                            weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                            color=p["text"] if on else p["text2"]),
                ]))

        def sub_row(label, page_key, tab_path, depth=1):
            return ft.Container(
                padding=ft.Padding(14 + 20 * depth, 6, 14, 6), border_radius=8,
                ink=True, on_click=go_sub(page_key, tab_path),
                content=ft.Text(label, size=12.5, color=p["text2"]))

        for k in pages:
            rows.append(page_row(k))
            for label, tab_path, depth in SUBPAGES.get(k, []):
                rows.append(sub_row(label, k, tab_path, depth))

        return ft.NavigationDrawer(bgcolor=p["bg2"], controls=rows)

    def _drawer_go(key):
        page.run_task(_drawer_go_async, key)

    async def _drawer_go_async(key):
        await page.close_drawer()
        render(key)

    async def open_menu(e):
        await page.show_drawer()

    def open_theme_picker(e):
        """A named, tappable list of every theme.

        This button used to step blindly to the next theme, naming the
        current one only in a tooltip - and a tooltip needs a mouse, which a
        phone does not have. So you could change the theme but never see
        what you were on or get back to one you liked. 21 themes is too many
        to walk one tap at a time.
        """
        p = PAL()

        def pick(key):
            def handler(e):
                _dismiss(page, dlg)
                apply_theme(page, key)
                render(current[0])
            return handler

        def swatch(key):
            t = THEMES[key]
            on = key == S.theme
            return ft.Container(
                padding=PAD(10, 8), border_radius=8, ink=True, on_click=pick(key),
                bgcolor=ft.Colors.with_opacity(0.14, p["accent"]) if on else None,
                content=ft.Row(spacing=10, controls=[
                    # The theme's own colours, so the row previews itself.
                    ft.Container(width=34, height=18, border_radius=5,
                                 bgcolor=t["bg2"], border=BRD(t["border"], 1),
                                 content=ft.Row(spacing=2, controls=[
                                     ft.Container(width=6, height=6, border_radius=3,
                                                  bgcolor=t[c])
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

    def topbar():
        p = PAL()
        return ft.Row(spacing=10, controls=[
            ft.IconButton(ft.Icons.MENU, icon_color=p["text2"], on_click=open_menu),
            brand_chip(),
            # Machine status. It lives here, next to the brand, rather than
            # beside the palette: a row of coloured circles touching a
            # palette icon reads as theme swatches you can tap, and these
            # are neither tappable nor themes.
            machine_dots(),
            ft.Container(expand=True),
            sync_chip,
            # There was an ft.Icon(SEARCH) here: a picture of a button that
            # could not be clicked and searched nothing. Removed rather than
            # faked - the drawer now lists every page, which is the jump-to-a-
            # page affordance the icon pretended to offer.
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)}",
                          on_click=open_theme_picker),
            ft.IconButton(ft.Icons.SETTINGS, icon_color=p["text2"],
                          on_click=lambda e: render("settings")),
        ])

    async def bottom_pick(e):
        items = _bottom_items()
        idx = e.control.selected_index
        if not (0 <= idx < len(items)):
            return
        key = items[idx]
        if key == MORE:
            # Re-render first so the bar stops highlighting "More" if the
            # drawer is dismissed without a choice.
            render(current[0])
            await page.show_drawer()
        else:
            render(key)

    def bottom_bar():
        # selected_index must be a real index (Flet asserts on it), so pages
        # that only live in the drawer highlight "More" - which is where they
        # are - instead of falsely lighting up Home.
        items = _bottom_items()
        here = items.index(current[0]) if current[0] in items else items.index(MORE)
        return ft.NavigationBar(
            selected_index=here, destinations=[
                ft.NavigationBarDestination(icon=ICONS[k], label=LABELS[k]) for k in items],
            on_change=bottom_pick,
        )

    def render(key):
        p = PAL()
        visible = prefs.visible_pages()
        if key not in visible:
            key = visible[0] if visible else "home"
        current[0] = key
        paint_sync()
        if key == "home":
            c = home.build(page)
        elif key == "lab":
            c = lab.build(page)
        elif key == "tools":
            c = tools.build(page)
        elif key == "trips":
            c = trips.build(page)
        elif key == "weather":
            c = weather.build(page)
        elif key == "garage":
            c = garage.build(page)
        elif key == "wat2do":
            c = wat2do.build(page)
        elif key == "extras":
            c = extras.build(page)
        else:
            # on_layout repaints so a reordered bottom bar takes effect at
            # once. Settings itself decides when to call it - repainting on
            # every arrow tap would scroll the list back to the top.
            c = settings.build(page, on_theme=lambda: render("settings"),
                               on_layout=lambda: render("settings"))
        body.content = c
        body.bgcolor = p["bg"]
        page.drawer = nav_drawer()
        page.controls.clear()
        chrome = [ft.Container(padding=PAD(8, 12), bgcolor=p["bg"], content=topbar()),
                  body]
        # The bar repeats what the drawer lists; off, the content gets the
        # height back and the drawer still reaches every page.
        if prefs.bottom_bar_enabled():
            chrome.append(bottom_bar())
        page.add(ft.Column(spacing=0, expand=True, controls=chrome))
        page.update()

    # A page that writes to the server needs to re-read and repaint after the
    # write lands. _forms.rebuilder does that by replacing page.controls,
    # which drops the topbar and the nav bar with it; parking the real
    # repaint here lets a page refresh itself without losing the chrome.
    # Parked on S for the same reason S.prefs is: it must stay referenced.
    S.rerender = lambda: render(current[0])

    # Home's quick actions need to *go* somewhere - Docker, Qbit, Wat2Do,
    # Journal - and only the shell knows how. Same trick go_sub uses: set
    # the tab indices the page will read on the way down, then render.
    #
    # Parked on S rather than passed in, because a page with no shell
    # (standalone, the tests) must be able to see that there is nowhere to
    # go and leave the tile out. A navigation tile that quietly does nothing
    # is exactly what this replaced.
    def goto(key, tab_path=()):
        for tab_key, index in (tab_path or ()):
            _TAB_INDEX[tab_key] = index
        render(key)

    S.goto = goto

    render("home")

    # Launch is itself a resume: whatever the last session queued has been
    # waiting across a process death, and the store kept it.
    sync_now()
    # Parked on S alongside the rest, so nothing drops the only reference to
    # the watcher and lets it be garbage collected mid-sleep.
    S.sync_watcher = page.run_task(watch_outbox)

# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call. view=None keeps this headless (serves over HTTP) rather than trying to
# open a desktop window - this host is a headless Pi. For a desktop window use
# view=ft.AppView.FLET_APP, which is what `flet build linux/macos` produces.
ft.run(main, view=None, port=8550)
