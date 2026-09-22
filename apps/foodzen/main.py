"""Agedashii Foodzen - groceries, what you ate, and what your blood pressure did.

This is the whole app, not a page of one. It is modelled on the dashboard's
own ``main.py`` - same topbar, same drawer, same SafeArea, same CRT stack -
and deliberately imports none of it: Foodzen stands on Qi (the look) and
Washi (the system) and on nothing that belongs to another app. The three
apps in this repo share those two packages and share no code with each other,
which is the point of the split; importing ``agedashii_tofu`` here would quietly
undo it.

Three surfaces:

    FoodZen   the website's page, unchanged - Grocery, Meals, Scrape,
              Expenses, and EatZen and BPM as its last two tabs.
    EatZen    food logging, promoted to a page of its own because in this
              app it is a headline feature and not a tab buried in Tools.
    BPM       blood pressure readings, same reasoning.

Offline is the normal case, not the error case. Themes, layout, corner
radius, the CRT and every preference are read from local storage and work
with the radio off; the food table lives on the server but the *arithmetic*
is the server's too, so the one thing that genuinely needs a connection says
so instead of guessing. Writes go through ``agedashii_washi.store``, which
applies them to the local cache and queues them until they land - the chip in
the topbar is what says how many are still waiting.
"""
import asyncio

import flet as ft

from agedashii_qi import (GLOW, PAL, RAD, S, THEME_NAMES, THEMES, apply_theme,
                          crt_overlay)
from agedashii_washi import api as M
from agedashii_washi import config, panels, prefs
from agedashii_washi.surface import _TAB_INDEX, _dismiss, _show
from agedashii_washi.widgets import BRD, PAD
from agedashii_foodzen.pages import eatzen, foodzen

# The gradient in the brand pill. Same three as the dashboard and the website:
# the pill is the identity across all of them, so it does not get restyled per
# app - only the word inside it changes.
ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"

FOODZEN, EATZEN, BPM = "foodzen", "eatzen", "bpm"
PAGES = [FOODZEN, EATZEN, BPM]
LABELS = {FOODZEN: "FoodZen", EATZEN: "EatZen", BPM: "BPM"}
ICONS = {FOODZEN: ft.Icons.SHOPPING_CART, EATZEN: ft.Icons.RESTAURANT,
         BPM: ft.Icons.MONITOR_HEART}

# The tabs inside FoodZen, so the drawer can offer one directly instead of
# making you land on the page and hunt for it. (label, tab index) against the
# `_tabs(..., key="foodzen")` call in pages/foodzen.py - the test walks both
# and fails if they drift.
#
# EatZen and BPM are tabs 4 and 5 of that page and are deliberately NOT listed
# here: they are top-level entries in this app, and one surface listed twice
# in a six-row menu is noise.
SUBPAGES = {
    FOODZEN: [("Grocery", 0), ("Meals", 1), ("Scrape", 2), ("Expenses", 3)],
}

# How often the outbox watcher looks up from its book. Matched to
# api.ONLINE_RECHECK, which is how long a "we are offline" verdict is trusted
# for reads - one cadence for the whole app rather than two that disagree.
SYNC_POLL_SECS = 20


class Shell:
    """Everything that is not a page: topbar, drawer, bottom bar, CRT.

    A class rather than the dashboard's nest of closures for one reason: the
    tests have to drive `render()` for every surface without running `main()`,
    and `main()` is where the network lives - `config.load` probes the server
    before the first screen paints. A shell you can construct around a page
    and ask to render is testable; a shell that only exists inside a coroutine
    that has already tried to reach a LAN address is not.
    """

    def __init__(self, page):
        self.page = page
        # Which surface is on screen. Rebound by render(), read by the drawer,
        # the bottom bar and by a theme change - which repaints the page you
        # are on rather than bouncing you back to the first one.
        self.current = FOODZEN
        # The body is created once and reused across renders, the same way the
        # sync chip is: swapping its `content` is a smaller repaint than
        # rebuilding the column, and a background drain needs a control it can
        # still find after a repaint.
        self.body = ft.Container(expand=True, padding=PAD(12, 12))
        # Which drawer sections are unfolded. Held here rather than in prefs
        # because prefs.set_drawer_open() only accepts the dashboard's own
        # page keys and silently refuses "foodzen"; a three-row menu does not
        # need its chevron remembered across launches badly enough to justify
        # a Foodzen-only storage key that nothing else reads.
        self._open = {}
        self._build_sync_chip()

    # ----------------------------------------------------------- the brand
    def brand_chip(self):
        """The product name in the gradient pill, and nothing else.

        The dashboard's pill says Agedashii. This one says Foodzen: on a
        phone's home screen these are two icons side by side, and a pill that
        said the same word in both would make the one you opened a guess.
        """
        return ft.Container(
            padding=PAD(4, 12), border_radius=RAD(10),
            gradient=ft.LinearGradient(colors=[ORANGE, PINK, CYAN],
                                       begin=ft.Alignment(-1, 0),
                                       end=ft.Alignment(1, 0)),
            # alola's hero title carries `0 0 30px color-mix(accent 40%)`. The
            # pill is this app's equivalent of that title, so it gets the wide
            # version of the glow rather than the 10px heading one.
            shadow=GLOW(blur=24, opacity=0.40),
            content=ft.Text("Foodzen", size=13, weight=ft.FontWeight.W_800,
                            color="#0d1117"))

    # ------------------------------------------------------- the outbox chip
    # Reads heal themselves: api._wake re-probes behind the UI and a stale
    # panel quietly refills. Writes had nothing that did that. A grocery item
    # ticked off with no signal goes into the outbox and stays there until the
    # next write happens to succeed - which on a phone that spends its offline
    # time in a shop and its online time locked in a pocket could be days,
    # with nothing on screen saying so.
    #
    # So: one chip in the chrome that says what is waiting, and two things
    # that drain it - coming back to the foreground, and the watcher below
    # noticing signal returned.
    def _build_sync_chip(self):
        self.sync_icon = ft.Icon(ft.Icons.CLOUD_UPLOAD, size=13)
        self.sync_text = ft.Text("", size=10, weight=ft.FontWeight.W_600)
        self.sync_chip = ft.Container(
            padding=PAD(3, 8), border_radius=RAD(9), visible=False, ink=True,
            on_click=self.sync_now,
            content=ft.Row(spacing=5, tight=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[self.sync_icon, self.sync_text]))

    def paint_sync(self):
        """Say what the server has not got yet, in the server's own terms."""
        p = PAL()
        st = M.sync_status()
        label = st.get("label") or ""
        stuck = (st.get("parked") or 0) + (st.get("conflict") or 0)
        self.sync_text.value = label
        self.sync_text.color = p["pink"] if stuck else p["text2"]
        self.sync_icon.color = p["pink"] if stuck else p["text2"]
        # `.icon`, not `.name`. ft.Icon's property is `icon` in Flet 0.86 and
        # the control is not slotted, so `sync_icon.name = ...` binds a stray
        # attribute, raises nothing, and simply never changes the glyph - the
        # dashboard's copy of this chip has been doing exactly that.
        self.sync_icon.icon = ft.Icons.CLOUD_OFF if stuck else ft.Icons.CLOUD_UPLOAD
        self.sync_chip.bgcolor = p["bg2"]
        self.sync_chip.border = BRD(p["pink"] if stuck else p["border"], 1)
        self.sync_chip.tooltip = f"{label} - tap to try again" if label else ""
        # Nothing waiting is the normal state; an always-present "all synced"
        # badge would be noise, and worse, would look like a claim about the
        # server when it is only a claim about the queue.
        self.sync_chip.visible = bool(label)

    def drain(self, *, force=False):
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

    def drain_and_paint(self, *, force=False):
        self.drain(force=force)
        self.paint_sync()
        try:
            self.page.update()
        except Exception:              # noqa: BLE001 - torn down mid-drain
            pass

    def sync_now(self, e=None):
        runner = getattr(self.page, "run_thread", None)
        if callable(runner):
            runner(self.drain_and_paint, force=True)

    async def watch_outbox(self):
        """Notice signal coming back, and send what has been waiting for it.

        Polls rather than listens because there is nothing to listen to: Flet
        surfaces no connectivity event, and the only honest test for "is the
        server there" is asking it. Costs one 3-second probe every 20 seconds
        *while there is something queued*, and nothing at all the rest of the
        time - which is almost always.
        """
        while True:
            try:
                await asyncio.sleep(SYNC_POLL_SECS)
                if not M.pending_count():
                    continue
                await asyncio.to_thread(self.drain)
                self.paint_sync()
                self.page.update()
            except asyncio.CancelledError:
                raise
            except Exception:          # noqa: BLE001 - a bad tick is not fatal
                pass

    def on_lifecycle(self, e):
        # Back in the foreground. Whatever was queued has been queued since
        # the last time this app was looked at, which is exactly when a phone
        # is most likely to have found wifi in between.
        if getattr(e, "state", None) in (ft.AppLifecycleState.RESUME,
                                         ft.AppLifecycleState.SHOW):
            self.sync_now()

    # ---------------------------------------------------------- navigation
    async def _close_drawer(self):
        fn = getattr(self.page, "close_drawer", None)
        if callable(fn):
            try:
                await fn()
            except Exception:          # noqa: BLE001 - no drawer open, or no page
                pass

    def _go(self, key, tab=None):
        """An on_click that closes the drawer and lands on `key`.

        Async because `close_drawer` is; the render after it is synchronous
        and has to stay fast. A slow handler on Flet's event loop leaves the
        old frame up and drops every tap that arrives while it runs, which is
        what "the menu does nothing" has meant in this codebase twice.
        """
        async def handler(e=None):
            if tab is not None:
                # _tabs() reads its starting index out of _TAB_INDEX under the
                # same key it saves to, so setting it before the render lands
                # on the right tab with no new plumbing.
                _TAB_INDEX["foodzen"] = tab
            await self._close_drawer()
            self.render(key)
        return handler

    def _toggle_section(self, key):
        """Flip one page's chevron - a drawer affordance, not a navigation.

        Never goes through render(): that rebuilds the topbar, the body and
        the bottom bar along with the drawer, and the body is the expensive
        part. Swapping just `page.drawer` is the smallest repaint that can
        show the new chevron state.
        """
        def handler(e=None):
            self._open[key] = not self._section_open(key)
            self.page.drawer = self.nav_drawer()
            self.page.update()
        return handler

    def _section_open(self, key):
        """Open by default only on the page you are standing on.

        "Everything open" would make a three-row menu a seven-row one;
        "everything closed" would hide the tabs you opened the menu to reach
        behind a chevron nobody has told you about yet.
        """
        return self._open.get(key, key == self.current)

    def nav_drawer(self):
        """Rebuilt on every render: the drawer is themed, and one built once
        at startup would keep the old palette after a theme change.

        Hand-built rows rather than NavigationDrawerDestination, because that
        control is flat and the whole point here is depth - FoodZen's four
        tabs are one tap from the menu instead of a page you land on and then
        hunt through.
        """
        p = PAL()
        rows = [ft.Container(height=12)]

        def page_row(k):
            on = k == self.current
            highlight = dict(
                # The row you are standing on is the app's active nav item;
                # glow on, it gets alola's accent shadow. Only the active one,
                # so the drawer does not become a wall of light.
                shadow=GLOW(blur=14, opacity=0.35) if on else None,
                bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else None)
            label_row = ft.Row(spacing=12, controls=[
                ft.Icon(ICONS[k], size=19, color=p["accent"] if on else p["text2"]),
                ft.Text(LABELS[k], size=14,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["text"] if on else p["text2"])])
            if not SUBPAGES.get(k):
                return ft.Container(padding=PAD(14, 9), border_radius=RAD(8),
                                    ink=True, on_click=self._go(k),
                                    content=label_row, **highlight)
            # A page with sub-items additionally gets a chevron, as a separate
            # tap target from the label: tapping the label always navigates,
            # only the chevron folds. 40x40 is a real Material touch target.
            open_ = self._section_open(k)
            nav = ft.Container(padding=PAD(14, 9), border_radius=RAD(8), ink=True,
                               expand=True, on_click=self._go(k), content=label_row)
            chevron = ft.Container(
                width=40, height=40, alignment=ft.Alignment.CENTER,
                border_radius=RAD(8), ink=True, on_click=self._toggle_section(k),
                tooltip="Collapse" if open_ else "Expand",
                content=ft.Icon(
                    ft.Icons.EXPAND_MORE if open_ else ft.Icons.CHEVRON_RIGHT,
                    size=20, color=p["accent"] if on else p["text2"]))
            return ft.Container(
                border_radius=RAD(8), content=ft.Row(
                    spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[nav, chevron]), **highlight)

        def sub_row(label, key, tab):
            return ft.Container(
                padding=ft.Padding(34, 6, 14, 6), border_radius=RAD(8), ink=True,
                on_click=self._go(key, tab),
                content=ft.Text(label, size=12.5, color=p["text2"]))

        for k in PAGES:
            rows.append(page_row(k))
            if SUBPAGES.get(k) and self._section_open(k):
                for label, tab in SUBPAGES[k]:
                    rows.append(sub_row(label, k, tab))

        return ft.NavigationDrawer(bgcolor=p["bg2"], controls=rows)

    async def open_menu(self, e=None):
        opener = getattr(self.page, "show_drawer", None)
        if callable(opener):
            await opener()

    # ------------------------------------------------------------- topbar
    def open_theme_picker(self, e=None):
        """A named, tappable list of every theme.

        Not a button that steps blindly to the next one naming it in a
        tooltip: a tooltip needs a mouse, which a phone does not have, and 24
        themes is too many to walk one tap at a time.
        """
        p = PAL()

        def pick(key):
            def handler(e):
                _dismiss(self.page, dlg)
                apply_theme(self.page, key)
                self.render(self.current)
            return handler

        def swatch(key):
            t = THEMES[key]
            on = key == S.theme
            return ft.Container(
                padding=PAD(10, 8), border_radius=RAD(8), ink=True,
                on_click=pick(key),
                bgcolor=ft.Colors.with_opacity(0.14, p["accent"]) if on else None,
                content=ft.Row(spacing=10, controls=[
                    # The theme's own colours, so the row previews itself.
                    ft.Container(width=34, height=18, border_radius=RAD(5),
                                 bgcolor=t["bg2"], border=BRD(t["border"], 1),
                                 content=ft.Row(spacing=2, controls=[
                                     ft.Container(width=6, height=6, border_radius=3,
                                                  bgcolor=t[c])
                                     for c in ("accent", "green", "pink")])),
                    ft.Text(THEME_NAMES.get(key, key), size=13,
                            weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                            color=p["text"], expand=True),
                    ft.Icon(ft.Icons.CHECK, size=16, color=p["accent"])
                    if on else ft.Container(width=16)]))

        dlg = ft.AlertDialog(
            bgcolor=p["bg2"],
            title=ft.Text(f"Theme - {THEME_NAMES.get(S.theme, S.theme)}",
                          size=15, color=p["text"]),
            content=ft.Container(width=300, height=380, content=ft.Column(
                spacing=2, scroll=ft.ScrollMode.AUTO,
                controls=[swatch(k) for k in THEME_NAMES])),
            actions=[ft.TextButton(content="Close",
                                   on_click=lambda e: _dismiss(self.page, dlg))])
        _show(self.page, dlg)

    async def toggle_customize(self, e=None):
        """Enter or leave arrange-the-cards mode on the surface you are on.

        Grocery, Meals and Expenses are panel stacks; EatZen and BPM are not,
        so the button only appears where there is something to arrange. Done
        is also where the arrangement is written to storage - every drag
        already saves as it happens (panels._apply), so this is the belt to
        that braces, and both go through prefs.save because there is one store
        for layout and this is not a second one.
        """
        on = panels.set_editing(not panels.editing())
        if not on and S.prefs is not None:
            await prefs.save(S.prefs)
        self.render(self.current)

    def topbar(self):
        p = PAL()
        editing = panels.editing()
        arrange = [ft.IconButton(
            ft.Icons.CHECK if editing else ft.Icons.DASHBOARD_CUSTOMIZE,
            icon_color=p["accent"] if editing else p["text2"],
            tooltip="Done arranging" if editing else "Arrange the cards on this page",
            on_click=self.toggle_customize)] if (panels.has_surface() or editing) else []
        return ft.Row(spacing=10, controls=[
            ft.IconButton(ft.Icons.MENU, icon_color=p["text2"], on_click=self.open_menu),
            self.brand_chip(),
            ft.Container(expand=True),
            self.sync_chip,
            *arrange,
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)}",
                          on_click=self.open_theme_picker)])

    # --------------------------------------------------------- bottom bar
    async def bottom_pick(self, e):
        idx = getattr(e.control, "selected_index", None)
        if idx is not None and 0 <= idx < len(PAGES):
            self.render(PAGES[idx])

    def bottom_bar(self):
        # selected_index must be a real index (Flet asserts on it). Every page
        # is on the bar here, so there is no "More" and nothing to fall back
        # to - but the guard stays, because current is set by render() and a
        # future page that is drawer-only must not crash the bar.
        here = PAGES.index(self.current) if self.current in PAGES else 0
        return ft.NavigationBar(
            selected_index=here,
            destinations=[ft.NavigationBarDestination(icon=ICONS[k], label=LABELS[k])
                          for k in PAGES],
            on_change=self.bottom_pick)

    # ------------------------------------------------------------- render
    def body_for(self, key):
        """The page itself. One call, and nothing in it knows about the shell."""
        if key == EATZEN:
            return eatzen.build_eatzen(self.page)
        if key == BPM:
            return eatzen.build_bpm(self.page)
        return foodzen.build(self.page)

    def render(self, key=None):
        p = PAL()
        key = key if key in PAGES else FOODZEN
        self.current = key
        # Cleared before the body builds and set again by panels.layout(), so
        # the topbar - built after the body, below - knows whether this page
        # has a card stack worth offering Customize for.
        panels.begin_render()
        self.paint_sync()
        self.body.content = self.body_for(key)
        self.body.bgcolor = p["bg"]
        self.page.drawer = self.nav_drawer()
        self.page.controls.clear()
        chrome = [ft.Container(padding=PAD(8, 12), bgcolor=p["bg"],
                               content=self.topbar()),
                  self.body]
        # The bar repeats what the drawer lists; off, the content gets the
        # height back and the drawer still reaches everything.
        if prefs.bottom_bar_enabled():
            chrome.append(self.bottom_bar())
        shell = ft.Column(spacing=0, expand=True, controls=chrome)
        # Keep the chrome out from under the hardware. On a Pixel 8 Pro an app
        # with no SafeArea draws its topbar beneath the clock and puts the
        # gesture bar over the bottom row, so half the menu is unreachable.
        # Flutter reports zero intrusions on desktop, so this is a no-op on
        # the laptop rather than a second layout to keep working. It wraps the
        # chrome and NOT the CRT overlay below: a screen effect that stopped
        # short of the cutout would draw a bright band exactly where the
        # cutout is.
        shell = ft.SafeArea(content=shell, expand=True)
        # The CRT vignette goes on here, once, over the whole shell - not per
        # page and not per card. Two controls for the entire app when it is
        # on, none at all when it is off, which is the default. The overlay is
        # ignore_interactions, so everything underneath is hit-tested exactly
        # as it was.
        if prefs.crt_overlay():
            shell = ft.Stack(expand=True, controls=[shell, crt_overlay()])
        self.page.add(shell)
        self.page.update()

    def attach(self):
        """Wire the shell to the page and paint the first screen.

        A page that writes to the server re-reads and repaints afterwards, and
        the helper that does it (forms.rebuilder) replaces page.controls -
        which would drop the topbar and the nav bar with it. Parking the real
        repaint on S lets a page refresh itself without losing the chrome.
        Parked on S rather than passed down for the same reason S.prefs is: it
        has to stay strongly referenced, and a page built with no shell at all
        (the tests) must be able to see there is nothing there and cope.
        """
        S.rerender = lambda: self.render(self.current)
        self.page.on_app_lifecycle_state_change = self.on_lifecycle
        self.render(FOODZEN)
        return self


async def main(page):
    # Without this the window, the taskbar and alt-tab all say "flet" - the
    # framework's own default.
    page.title = "Agedashii Foodzen"
    apply_theme(page)

    # Persistent storage is a Flet *service*: constructing it inside the page
    # context registers it, and it is unregistered again the moment nothing
    # holds a strong reference - hence parking it on S rather than a local.
    S.prefs = ft.SharedPreferences()
    # Restore the saved server address and confirm it still answers before the
    # first screen paints, so pages do not flash sample data then swap.
    await config.load(S.prefs)
    # Layout choices, units, the sodium target, corner radius, glow and the
    # CRT come from the same storage - all of it local, none of it a request.
    await prefs.load(S.prefs)
    prefs.apply_runtime()

    shell = Shell(page).attach()

    # Launch is itself a resume: whatever the last session queued has been
    # waiting across a process death, and the store kept it.
    shell.sync_now()
    # Parked on S alongside the rest, so nothing drops the only reference to
    # the watcher and lets it be garbage collected mid-sleep.
    S.foodzen_shell = shell
    S.sync_watcher = page.run_task(shell.watch_outbox)


def _view_and_port(env):
    """(view, port) for ft.run, decided from the environment.

    Hardcoding `view=None, port=8550` serves the app over HTTP instead of
    opening a window. That is right on the headless Pi and wrong everywhere
    else: from source on a laptop it produces no window at all, and a second
    run dies with "address already in use" because the port is fixed.

    So: open a real window when there is a display, serve headless when there
    is not, and let either be overridden.

        AGEDASHII_HEADLESS=1   force the HTTP server (the Pi does this)
        AGEDASHII_PORT=8552    pin the port; 0 or unset lets the OS pick a
                               free one, so two copies cannot collide

    The headless default is 8552, not the dashboard's 8550: these are separate
    apps that are expected to be served off the same Pi at the same time, and
    two defaults that collide would make whichever started second die on
    "address already in use" for no reason anybody could see.

    A function rather than four lines under `if __name__` so the choice can be
    tested without starting a server.
    """
    headless = env.get("AGEDASHII_HEADLESS") == "1" or not (
        env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    port = int(env.get("AGEDASHII_PORT") or (8552 if headless else 0))
    return (None if headless else ft.AppView.FLET_APP), port


if __name__ == "__main__":
    import os as _os

    _view, _port = _view_and_port(_os.environ)
    ft.run(main, view=_view, port=_port)
