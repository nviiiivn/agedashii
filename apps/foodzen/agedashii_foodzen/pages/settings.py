"""Settings - the screen this app was missing.

"where is the settings? i think you have not taken the format/template
literally --- each of these independent apps needs their own icons/labels/
readme wikis INSIDE on how to use/settings menu etc."

So: a real surface on the navigation, not a dialog hung off the topbar. It
offers what the dashboard's Settings offers minus the parts that are not this
app's business - there are no nine pages to reorder here, no machine editor,
no offline map - plus the two things only Foodzen has: the sodium target, and
the switch that hides the hamburger.

Three things are done differently from the dashboard's version on purpose:

**The theme persists.** The dashboard picks a theme, applies it, and forgets
it, so it resets at every launch. This one writes it through
``agedashii_foodzen.prefs_local``, which is read back in ``main()`` before the
first frame. See that module for why the key is this app's own rather than a
new one in the shared store.

**The server check runs off the event loop.** ``config.set_url`` opens a
socket and waits up to three seconds. On Flet's loop that means three seconds
of a frozen frame with every tap in the meantime dropped - which is precisely
what "the button does nothing" has meant in this codebase, twice. It goes
through ``asyncio.to_thread``, and the button says "checking…" while it runs.

**Nothing here writes to the server.** The only endpoint this screen touches
is ``/api/config``, a side-effect-free GET, and only when you press Check.
Her FoodZen data has been destroyed once already by something that poked a
save endpoint to see what it did.

Wording follows the dashboard's: a label, then one grey line saying what the
control actually changes, in plain words, including what it does not do.
"""
import asyncio

import flet as ft

from agedashii_qi import (PAL, RAD, RADIUS_CHOICES, RADIUS_LABELS, S,
                          THEME_NAMES, THEMES, FOCUS_WIDTH, apply_theme)
from agedashii_washi import api as M
from agedashii_washi import config, panels, prefs
from agedashii_washi.widgets import BRD, PAD, card, chip, sub, title

from .. import glyphs, prefs_local

# The card stacks this app owns. Derived from the shared table rather than
# listed here, so a panel added to one of these tabs turns up in the editor
# without anyone remembering to come back.
SURFACES = [k for k in panels.SURFACES if k.startswith("foodzen.")]


# ---------------------------------------------------------------- plumbing
class Saver:
    """One status line for every edit on this page.

    Same contract the write forms use: the screen may change immediately -
    these are local preferences, not server records - but a write that did
    not land says so rather than pretending. Both stores are committed
    together because from where she is standing there is only one Settings,
    and a theme that saved while the sodium target did not would be a
    baffling thing to debug from the outside.
    """

    def __init__(self, page):
        self.page = page
        self.text = ft.Text("", size=11, color=PAL()["text2"], selectable=True)

    @property
    def control(self):
        return ft.Container(padding=PAD(2, 0), content=self.text)

    def note(self, msg, bad=False):
        p = PAL()
        self.text.value = msg
        self.text.color = p["pink"] if bad else p["text2"]

    async def commit(self, msg="saved"):
        if S.prefs is None:
            self.note("changed for now - no storage on this device, "
                      "so it resets on restart", bad=True)
        else:
            shared = await prefs.save(S.prefs)
            mine = await prefs_local.save(S.prefs)
            if shared and mine:
                self.note(msg)
            else:
                self.note("not saved - storage refused the write", bad=True)
        try:
            self.page.update()
        except Exception:      # noqa: BLE001 - torn down mid-save
            pass


def _picked(e, valid, fallback):
    """The option she chose, however this Flet build reports it.

    The selection arrives either as a property patch on the control or as
    the event payload depending on the control and the platform, so both
    are checked and anything unrecognised leaves the setting alone.
    Guessing here would silently reset a preference.
    """
    for candidate in (getattr(getattr(e, "control", None), "value", None),
                      getattr(e, "data", None)):
        if isinstance(candidate, str) and candidate in valid:
            return candidate
    return fallback


def _dropdown(p, value, options, on_select, width=260, label=None):
    """A Dropdown wired to 0.86's `on_select`. There is no `on_change` here -
    it exists as a name, never fires, and the setting silently does nothing."""
    return ft.Dropdown(
        value=value, options=[ft.dropdown.Option(k, t) for k, t in options],
        on_select=on_select, dense=True, filled=True, text_size=12, width=width,
        label=label, border_color=p["border"], color=p["text"],
        label_style=ft.TextStyle(color=p["text2"], size=11))


def _switch_row(p, label, why, value, on_change, *, disabled=False):
    """One labelled switch with its reason underneath."""
    return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Column(spacing=1, expand=True, controls=[
            ft.Text(label, size=13, color=p["text3"] if disabled else p["text"]),
            ft.Text(why, size=10, color=p["text3"]),
        ]),
        ft.Switch(value=value, on_change=on_change, active_color=p["accent"],
                  disabled=disabled),
    ])


def _repaint(fn):
    """Run a whole-shell repaint, if one was handed to us. Never fatal."""
    if fn:
        try:
            fn()
        except Exception:      # noqa: BLE001 - a repaint must not lose the edit
            pass


# ---------------------------------------------------------------- server
def server_card(page):
    """Where the app looks for the server, and whether it is actually there.

    Shows the real reason a connection failed rather than a generic
    "offline": "blocked by auth" and "unreachable" need completely different
    fixes, and an app silently falling back to its own copy is the failure
    this card exists to make visible.
    """
    p = PAL()
    field = ft.TextField(
        value=config.SERVER_URL, dense=True, filled=True, text_size=13,
        label="Server address", hint_text="192.0.2.21:6900",
        border_color=p["border"], focused_border_color=p["accent"],
        focused_border_width=FOCUS_WIDTH(),
        color=p["text"], label_style=ft.TextStyle(color=p["text2"], size=11))
    light = ft.Container(width=9, height=9, border_radius=5)
    status = ft.Text(size=11, color=p["text2"], selectable=True)

    def paint():
        st = config.STATUS
        light.bgcolor = p["green"] if st.get("ok") else p["pink"]
        status.value = ("connected - " if st.get("ok") else "not connected - ") \
            + str(st.get("detail") or "not checked yet")

    paint()

    async def probe(then=None):
        """Check the address on a worker thread, then repaint.

        `config.set_url` waits on a socket for up to three seconds. Run on
        the event loop that is three seconds of frozen frame and every tap
        in the meantime dropped, which is how a working button comes to
        look dead.
        """
        status.value = "checking…"
        light.bgcolor = p["text2"]
        page.update()
        await asyncio.to_thread(config.set_url, field.value)
        M.refresh()            # the cache was filled from the old address
        paint()
        if then:
            await then()
        page.update()

    async def do_check(e):
        await probe()

    async def do_save(e):
        async def persist():
            if S.prefs is None or not await config.save(S.prefs):
                status.value += "  (could not save it - it resets on restart)"

        await probe(persist)

    return card(ft.Column(spacing=10, controls=[
        glyphs.head(glyphs.ICON_SERVER, "Server", 15),
        field,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[light, ft.Container(expand=True, content=status)]),
        ft.Row(spacing=8, controls=[
            ft.FilledButton(content="Save", on_click=do_save, height=34),
            ft.OutlinedButton(content="Check", on_click=do_check, height=34),
        ]),
        sub("Off the LAN, connect the VPN first - the public hostname is "
            "behind a login and will not answer the app. Check only reads "
            "/api/config; it never writes anything.", 10),
    ]))


# ---------------------------------------------------------------- appearance
def theme_card(page, saver, on_theme=None):
    """All 24 themes, and this app remembers which one you picked.

    The dashboard's picker applies a theme and stores nothing, so it is back
    to the default at every launch. This writes through prefs_local and
    main() reads it before the first frame.
    """
    p = PAL()

    def swatch(key):
        t = THEMES[key]
        active = S.theme == key

        async def pick(e):
            apply_theme(page, key)
            prefs_local.set_theme(key)
            await saver.commit(f"theme saved - {THEME_NAMES.get(key, key)}")
            _repaint(on_theme)

        return ft.Container(
            width=74, border_radius=RAD(10), padding=PAD(6, 4), ink=True,
            bgcolor=t["surface"], border=BRD(t["accent"], 2 if active else 1),
            on_click=pick, tooltip=THEME_NAMES.get(key, key),
            content=ft.Column(
                spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(spacing=3, alignment=ft.MainAxisAlignment.CENTER, controls=[
                        ft.Container(width=12, height=12, border_radius=6,
                                     bgcolor=t["accent"]),
                        ft.Container(width=12, height=12, border_radius=6,
                                     bgcolor=t["green"]),
                        ft.Container(width=12, height=12, border_radius=6,
                                     bgcolor=t["pink"]),
                    ]),
                    ft.Text(THEME_NAMES[key], size=9, color=t["text"], max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            text_align=ft.TextAlign.CENTER),
                ]),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=1,
                                color=ft.Colors.with_opacity(0.5, t["accent"]))
            if active else None)

    return card(ft.Column(spacing=10, controls=[
        glyphs.head(glyphs.ICON_APPEARANCE, "Theme", 15,
                    trailing=chip(THEME_NAMES.get(S.theme, S.theme),
                                  p["accent"], small=True)),
        sub("Every colour in the app, including the icons in it. Saved on "
            "this device and back on the next launch.", 10),
        # Fixed-width swatches in a wrap. Nothing in here expands: a Wrap
        # hands its children unbounded width, and an Expanded child of one
        # paints over the whole screen. See tests/test_no_unbounded_in_wrap.
        ft.Row(spacing=8, wrap=True, run_spacing=8,
               controls=[swatch(k) for k in THEME_NAMES]),
    ]))


def appearance_card(page, saver, on_theme=None):
    """Corner rounding, the accent glow, and the CRT overlay with its slider.

    All three reach the chrome - the brand pill, the nav bar, the overlay
    above everything - so each one repaints the whole shell rather than
    just this page, or the app sits half-changed until you navigate away.
    """
    p = PAL()

    async def pick_radius(e):
        valid = {str(n) for n in RADIUS_CHOICES}
        prefs.set_corner_radius(_picked(e, valid, str(prefs.corner_radius())))
        await saver.commit(f"corners set to {prefs.corner_radius()}px")
        _repaint(on_theme)

    async def toggle_glow(e):
        prefs.set_accent_glow(e.control.value)
        await saver.commit("accent glow " + ("on" if e.control.value else "off"))
        _repaint(on_theme)

    intensity_text = ft.Text(f"{prefs.crt_intensity()}%", size=11, color=p["text2"])
    intensity_slider = ft.Slider(
        min=0, max=100, divisions=20, value=prefs.crt_intensity(),
        active_color=p["accent"], disabled=not prefs.crt_overlay(),
        label="{value}%")

    def preview_intensity(e):
        # Live while dragging, written once on release: a drag through 0-100
        # would otherwise write to storage on every intermediate tick.
        intensity_text.value = f"{round(e.control.value)}%"
        page.update()

    async def commit_intensity(e):
        prefs.set_crt_intensity(round(e.control.value))
        await saver.commit(f"CRT intensity set to {prefs.crt_intensity()}%")
        _repaint(on_theme)

    intensity_slider.on_change = preview_intensity
    intensity_slider.on_change_end = commit_intensity

    async def toggle_crt(e):
        prefs.set_crt_overlay(e.control.value)
        intensity_slider.disabled = not e.control.value
        await saver.commit("CRT overlay " + ("on" if e.control.value else "off"))
        _repaint(on_theme)

    return card(ft.Column(spacing=10, controls=[
        glyphs.head(ft.Icons.AUTO_AWESOME, "Appearance", 15),
        _dropdown(p, str(prefs.corner_radius()),
                  [(str(n), RADIUS_LABELS.get(n, f"{n}px")) for n in RADIUS_CHOICES],
                  pick_radius, label="Corner rounding"),
        sub("Applies to every rounded surface - cards, chips, buttons, fields, "
            "the brand pill and the nav bar. Round dots stay round; they are "
            "circles, not corners.", 10),
        _switch_row(p, "Accent glow",
                    "An accent-coloured halo on headings, the brand pill, the "
                    "screen you are on and focused fields. Follows the theme.",
                    prefs.accent_glow(), toggle_glow),
        _switch_row(p, "CRT overlay",
                    "Scanlines, a halftone dot mask and a soft vignette over "
                    "the whole app, the way the website does it. It does not "
                    "take taps.",
                    prefs.crt_overlay(), toggle_crt),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text("Intensity", size=13, color=p["text"], width=64),
            ft.Container(expand=True, content=intensity_slider),
            intensity_text,
        ]),
        sub("How strong the scanlines, dot mask and vignette draw when the "
            "overlay is on.", 10),
    ]))


# ---------------------------------------------------------------- navigation
def navigation_card(page, saver, on_layout=None):
    """The bottom bar, and the hamburger she asked to be able to hide.

    "Each of these smaller apps (not tofu) work better with the bottom
    navigation bar and less with the hamburger menu... i want the ability to
    show / hide the hamburger menu trigger as wel please."

    So the bar is the navigation and the menu is optional. The one rail: with
    the bar off, the menu trigger comes back whatever this switch says,
    because an app with neither has no way to reach any other screen -
    including this one, which is where you would undo it. The switch goes
    dead and says why rather than silently lying about what it does.
    """
    p = PAL()
    bar_on = prefs.bottom_bar_enabled()

    async def toggle_bar(e):
        prefs.set_bottom_bar_enabled(e.control.value)
        await saver.commit("bottom bar " + ("on" if e.control.value else "off"))
        _repaint(on_layout)

    async def toggle_menu(e):
        prefs_local.set_hamburger_enabled(e.control.value)
        await saver.commit("menu button " + ("shown" if e.control.value else "hidden"))
        _repaint(on_layout)

    return card(ft.Column(spacing=10, controls=[
        glyphs.head(glyphs.ICON_NAV, "Navigation", 15),
        _switch_row(p, "Bottom bar",
                    "The four screens along the bottom. This is how this app "
                    "is meant to be driven; off gives the height back to the "
                    "page.",
                    bar_on, toggle_bar),
        _switch_row(p, "Menu button",
                    ("The hamburger in the top left. It reaches the same four "
                     "screens plus FoodZen's tabs directly."
                     if bar_on else
                     "Held on because the bottom bar is off - with neither, "
                     "nothing would be reachable."),
                    prefs_local.hamburger_shown(), toggle_menu, disabled=not bar_on),
        sub("Hidden, the menu button leaves no gap behind it - the brand pill "
            "moves to the edge.", 10),
    ]))


# ---------------------------------------------------------------- the domain
def eatzen_card(page, saver):
    p = PAL()
    valid = {str(n) for n in prefs.SODIUM_TARGETS}

    async def pick(e):
        prefs.set_sodium_target(_picked(e, valid, str(prefs.sodium_target())))
        await saver.commit("sodium target saved")

    return card(ft.Column(spacing=8, controls=[
        glyphs.head(glyphs.ICON_SODIUM, "EatZen", 15),
        _dropdown(p, str(prefs.sodium_target()),
                  [("1500", "1500 mg - strict DASH"), ("2300", "2300 mg - DASH")],
                  pick, label="Daily sodium target"),
        sub("The ceiling EatZen starts each day from, and what the meter on "
            "that screen fills against. Changing it does not rewrite days "
            "already logged.", 10),
    ]))


def units_card(page, saver):
    p = PAL()

    async def pick(e):
        prefs.set_unit_system(_picked(e, set(prefs.UNIT_SYSTEMS), prefs.unit_system()))
        await saver.commit("units saved")

    return card(ft.Column(spacing=8, controls=[
        glyphs.head(glyphs.ICON_UNITS, "Units", 15),
        _dropdown(p, prefs.unit_system(),
                  [("imperial", "Imperial - miles, °F"), ("metric", "Metric - km, °C")],
                  pick, label="Measure things in"),
        sub("Grams and milligrams are the same either way - nutrition is "
            "metric everywhere. This is for the parts of the app that carry "
            "a distance or a temperature.", 10),
    ]))


def data_card(page, saver):
    p = PAL()
    labels = {0: "Only when I ask", 15: "Every 15 seconds", 30: "Every 30 seconds",
              60: "Every minute", 300: "Every 5 minutes", 900: "Every 15 minutes"}
    valid = {str(n) for n in prefs.REFRESH_CHOICES}

    async def pick(e):
        prefs.set_refresh_seconds(_picked(e, valid, str(prefs.refresh_seconds())))
        M.refresh()            # the cache was filled under the old interval
        await saver.commit("refresh interval saved")

    return card(ft.Column(spacing=8, controls=[
        glyphs.head(glyphs.ICON_DATA, "Data", 15),
        _dropdown(p, str(prefs.refresh_seconds()),
                  [(str(n), labels[n]) for n in prefs.REFRESH_CHOICES],
                  pick, label="Reread from the server"),
        sub("How long an answer from the server is reused before the app asks "
            "again. Longer saves battery. Your own edits are never delayed by "
            "this - they queue and go as soon as there is signal.", 10),
    ]))


# ---------------------------------------------------------------- card layout
def cards_card(page, saver, on_layout=None):
    """Reorder and hide the cards on each of FoodZen's stacks.

    Arrows rather than drag, because this list lives inside a scrolling
    column and on a touch screen the gesture that reorders a row is the same
    one that scrolls past it. Dragging and resizing is on the page itself -
    the Arrange button in the topbar - where the handle is a distinct icon.
    """
    p = PAL()
    chosen = [SURFACES[0]] if SURFACES else [None]
    rows = ft.Column(spacing=0)

    def paint():
        surface = chosen[0]
        if surface is None:
            rows.controls = [sub("nothing to arrange here", 11)]
            return
        order = prefs.panel_order(surface)
        labels = panels.panel_labels(surface)
        hidden = set(prefs.hidden_panels(surface))
        rows.controls = [row(surface, k, i, order, labels, hidden)
                         for i, k in enumerate(order)]

    def row(surface, key, i, order, labels, hidden_set):
        hidden = key in hidden_set

        async def up(e):
            prefs.move_panel(surface, key, -1)
            paint()
            await saver.commit("order saved")
            _repaint(on_layout)

        async def down(e):
            prefs.move_panel(surface, key, +1)
            paint()
            await saver.commit("order saved")
            _repaint(on_layout)

        async def toggle(e):
            prefs.set_panel_hidden(surface, key, not hidden)
            paint()
            await saver.commit("saved")
            _repaint(on_layout)

        first, last = i == 0, i == len(order) - 1
        return ft.Container(
            padding=PAD(2, 0),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])) if not last else None,
            content=ft.Row(spacing=4,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.IconButton(ft.Icons.KEYBOARD_ARROW_UP, icon_size=18, on_click=up,
                              disabled=first, tooltip="Move up",
                              icon_color=p["text3"] if first else p["text2"]),
                ft.IconButton(ft.Icons.KEYBOARD_ARROW_DOWN, icon_size=18, on_click=down,
                              disabled=last, tooltip="Move down",
                              icon_color=p["text3"] if last else p["text2"]),
                ft.Container(expand=True, content=ft.Text(
                    labels.get(key, key), size=13,
                    color=p["text3"] if hidden else p["text"])),
                ft.IconButton(
                    ft.Icons.VISIBILITY_OFF if hidden else ft.Icons.VISIBILITY,
                    icon_size=18, on_click=toggle,
                    tooltip="Show this card" if hidden else "Hide this card",
                    icon_color=p["text3"] if hidden else p["accent"]),
            ]))

    def pick_surface(e):
        value = _picked(e, set(SURFACES), chosen[0])
        if value != chosen[0]:
            chosen[0] = value
            paint()
            page.update()

    async def reset(e):
        if chosen[0]:
            prefs.reset_panels(chosen[0])
        paint()
        await saver.commit("layout reset")
        _repaint(on_layout)

    paint()
    return card(ft.Column(spacing=8, controls=[
        glyphs.head(ft.Icons.VIEW_AGENDA, "Card layout", 15,
                    trailing=ft.TextButton(content="Reset this tab", on_click=reset)),
        sub("Reorder and hide the cards on each FoodZen tab. Widths and "
            "heights are dragged on the page itself, from the Arrange button "
            "in the top bar.", 10),
        _dropdown(p, chosen[0],
                  [(k, panels.SURFACE_LABELS.get(k, k)) for k in SURFACES],
                  pick_surface, width=260),
        rows,
    ]))


# ---------------------------------------------------------------- help
def help_card(page, on_help=None):
    """The way in to the manual. It is the reason Settings is worth opening
    on a phone you have not touched in a month."""
    return card(ft.Column(spacing=8, controls=[
        glyphs.head(glyphs.ICON_HELP, "How this works", 15),
        sub("What each screen does, what works with the radio off, where the "
            "sodium numbers come from, and what the * on a row means.", 10),
        ft.Row(spacing=8, controls=[
            ft.FilledButton(content="Open the guide", height=34,
                            icon=ft.Icons.MENU_BOOK,
                            on_click=(lambda e: on_help()) if on_help else None,
                            disabled=on_help is None)]),
    ]))


# ---------------------------------------------------------------- diagnostics
def _diag_row(p, label, value, ok=None):
    """One fact, with its own colour. `ok=None` means neither good nor bad."""
    color = p["text2"] if ok is None else (p["green"] if ok else p["yellow"])
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
        ft.Container(width=104, content=ft.Text(label, size=11, color=p["text3"])),
        ft.Text(str(value), size=11, color=color, selectable=True, expand=True),
    ])


def diagnostics_card(page):
    """The facts you would otherwise need a log file to find.

    Every lookup is guarded: this card exists to explain a broken app, so it
    is the last place that may itself break.
    """
    p = PAL()

    def safe(fn, default="unavailable"):
        try:
            return fn()
        except Exception as exc:   # noqa: BLE001 - show the fault, do not hide it
            return f"{default} ({type(exc).__name__})"

    st = config.STATUS
    live = safe(lambda: M.STATUS_LIVE(), False)
    live_ok = live is True
    waiting = safe(lambda: M.pending_count(), "?")

    rows = [
        _diag_row(p, "Server", st.get("url") or config.SERVER_URL),
        _diag_row(p, "Connection", ("connected - " if live_ok else "not connected - ")
                  + str(st.get("detail", "not checked yet")), live_ok),
        _diag_row(p, "Waiting to send", f"{waiting} change(s) in the outbox",
                  waiting == 0 if isinstance(waiting, int) else None),
        _diag_row(p, "App version", safe(prefs.app_version)),
        _diag_row(p, "Storage", "SharedPreferences ready" if S.prefs is not None
                  else "unavailable - settings reset on restart",
                  S.prefs is not None),
        _diag_row(p, "Theme", f"{THEME_NAMES.get(S.theme, S.theme)}"
                  + (" (saved)" if prefs_local.LOADED else " (not loaded yet)"),
                  prefs_local.LOADED),
        _diag_row(p, "Navigation", ("bottom bar" if prefs.bottom_bar_enabled()
                                    else "no bottom bar")
                  + (" + menu button" if prefs_local.hamburger_shown()
                     else ", no menu button")),
    ]
    return card(ft.Column(spacing=8, controls=[
        glyphs.head(glyphs.ICON_DIAGNOSTICS, "Diagnostics", 15,
                    trailing=chip("live" if live_ok else "local only",
                                  p["green"] if live_ok else p["yellow"], small=True)),
        *rows,
    ]))


# ---------------------------------------------------------------- the page
def build(page, on_theme=None, on_layout=None, on_help=None):
    """The Settings surface.

    `on_theme` and `on_layout` are the shell's whole-app repaint - anything
    that reaches the chrome has to use them or the app sits half-changed.
    `on_help` navigates to the guide; without it the button is drawn
    disabled rather than dead, so a harness with no shell shows the truth.
    """
    # Startup loads both stores before the first frame; this covers the case
    # where it has not, and repaints once the values arrive.
    prefs.hydrate(page, S.prefs, on_done=getattr(S, "rerender", None))

    saver = Saver(page)

    return ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        server_card(page),
        navigation_card(page, saver, on_layout=on_layout),
        theme_card(page, saver, on_theme=on_theme),
        appearance_card(page, saver, on_theme=on_theme),
        saver.control,
        eatzen_card(page, saver),
        units_card(page, saver),
        data_card(page, saver),
        cards_card(page, saver, on_layout=on_layout),
        help_card(page, on_help=on_help),
        diagnostics_card(page),
        card(ft.Column(spacing=6, controls=[
            title("On this device", 14),
            sub("Theme, appearance, navigation, units, the sodium target and "
                "the card layout are stored on this phone only - they need no "
                "connection and are never sent anywhere. Lists, meals and "
                "readings live on the server and are shared with the website.",
                11),
        ])),
    ])
