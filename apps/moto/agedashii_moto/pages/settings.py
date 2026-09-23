"""Settings: Moto's own, in Moto, on the bar.

"where is the settings? i think you have not taken the format/template
literally --- each of these independent apps needs their own icons/labels/
readme wikis INSIDE on how to use/settings menu etc..."

It had none. The app carried a palette button in the topbar and nothing
else, and everything behind it - the server address, the corner radius, the
glow, the CRT overlay, the units - could only be changed by opening the
dashboard app instead. That is not an independent app; that is a view onto
one.

So this is a fourth surface rather than a dialog hung off the topbar. It
rides the bottom bar beside Trips, Garage and XNAV, which is the whole
point: the bar is the navigation now, and a setting you can only reach by
opening a drawer that may be switched off is not reachable.

WHAT IS HERE, AND WHERE EACH VALUE LIVES
    Server address          `agedashii_washi.config`
    Theme                   `agedashii_moto.motoprefs` - see below
    Corner radius, glow,    `agedashii_washi.prefs`, shared with the
    CRT + intensity,        dashboard on purpose: one look across all
    units, bottom bar       three Agedashii apps
    Hamburger trigger       `agedashii_moto.motoprefs` - Moto's own

    The theme is stored *here* because nothing stored it anywhere. The
    dashboard's picker has always applied a theme and never written it
    down, so every launch opened on the shipped default again. Moto writes
    `agedashii.theme` under the shared namespace, so the day the other two
    apps read it they inherit the choice rather than inventing a key each.

IT IS MODELLED ON THE DASHBOARD'S SETTINGS AND DOES NOT IMPORT IT
    Same shape - a scrolling column of cards, a single status line every
    edit reports through, the same wording for the things that are the
    same. Importing the dashboard's own Settings page would bring its nine
    page keys, its panel editor for seventeen surfaces, its sodium target
    and its refresh interval into an app with four surfaces and no
    EatZen, which is the entire thing the split was for.

FLET 0.86.5, TWICE
    `Dropdown` fires `on_select` and never `on_change`; `FilledButton`
    takes `content=` and never `text=`. Both fail silently.

    And the probe: `config.set_url` opens a socket and waits up to three
    seconds. Flet runs a synchronous handler on its own event loop, so
    doing that inline leaves the old frame on screen and drops every tap
    that lands during it - which is exactly what "the button is dead"
    looks like. It goes to `asyncio.to_thread`.
"""
import asyncio

import flet as ft

from agedashii_qi import (PAL, RADIUS_CHOICES, RADIUS_LABELS, S, apply_theme,
                          FOCUS_WIDTH, GLOW, RAD)
from agedashii_qi.themes import THEME_NAMES, THEMES
from agedashii_washi import api as M
from agedashii_washi import config, prefs, routing, tileserver
from agedashii_washi.widgets import BRD, PAD, card, chip, sub, title

from .. import motoprefs
from .. import units as U

#: How long the Check button waits for the server before calling it dead.
#: The same three seconds `config.probe` defaults to - named here so the
#: sentence on screen and the timeout cannot drift apart.
PROBE_SECONDS = 3


# --------------------------------------------------------------- plumbing
def _repaint():
    """Repaint the whole shell, if there is one.

    Every control on this page reaches the chrome - the brand pill, the
    nav bar, the CRT overlay above everything - so repainting only this
    surface would leave the app half changed until you navigated away and
    came back. `S.rerender` is the shell's own whole-screen rebuild; it is
    absent when this page is built standalone (the tests), and then there
    is simply nothing to repaint.
    """
    fn = getattr(S, "rerender", None)
    if not callable(fn):
        return False
    try:
        fn()
        return True
    except Exception:  # noqa: BLE001 - a repaint must never lose the edit
        return False


def _update(page):
    try:
        page.update()
    except Exception:  # noqa: BLE001 - torn down mid-edit
        pass


#: The status line itself, made once and reused by every build of this
#: page - the same trick `main.py` uses for the body container and the
#: outbox chip, and for the same reason.
#:
#: Every edit here repaints the whole shell, which rebuilds this page,
#: which would otherwise build a *new* status Text - and the message the
#: edit then writes would land in the old one, detached from the screen.
#: "changed for now - no storage on this device" is exactly the message
#: that would be lost that way, and it is the one that matters most.
#: One control that outlives the rebuild is the smallest fix there is.
_STATUS = ft.Text("", size=11, selectable=True)


class _Saver:
    """One shared status line for every preference edit on this page.

    The screen may change optimistically - these are local preferences,
    not server records - but a write that did not land says so rather than
    pretending. Both stores are written: the shared one and Moto's own,
    because the theme and the hamburger live in the second and a commit
    that only wrote the first would lose them silently.
    """

    def __init__(self, page):
        self.page = page
        self.text = _STATUS
        # Re-themed on every build rather than only when it was made, so
        # the line does not keep the old palette after a theme change.
        self.text.color = PAL()["text2"]

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
        elif all(await asyncio.gather(prefs.save(S.prefs),
                                      motoprefs.save(S.prefs))):
            self.note(msg)
        else:
            self.note("not saved - storage refused the write", bad=True)
        _update(self.page)


def _picked(e, valid, fallback):
    """The option she chose, however this Flet build reports it.

    The selection arrives either as a property patch on the control or as
    the event payload depending on the control and the platform, so both
    are checked and anything unrecognised leaves the setting alone.
    Guessing wrong here would silently reset a preference.
    """
    for candidate in (getattr(getattr(e, "control", None), "value", None),
                      getattr(e, "data", None)):
        if isinstance(candidate, str) and candidate in valid:
            return candidate
    return fallback


def _dropdown(p, value, options, on_select, width=260, label=None):
    """A Dropdown wired to 0.86's `on_select` - there is no `on_change`."""
    return ft.Dropdown(
        value=value, options=[ft.dropdown.Option(k, t) for k, t in options],
        on_select=on_select, dense=True, filled=True, text_size=12, width=width,
        label=label, border_color=p["border"], color=p["text"],
        label_style=ft.TextStyle(color=p["text2"], size=11))


def _switch_row(p, label, why, value, on_change, disabled=False):
    """One labelled switch. The label is a real sentence, not a word.

    Never `expand=True` on anything that could end up inside a wrapping
    Row - this one is always in a Column, and the expand is on the text
    column, which is what gives the switch its right-hand berth.
    """
    return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Column(spacing=1, expand=True, controls=[
            ft.Text(label, size=13, color=p["text3"] if disabled else p["text"]),
            ft.Text(why, size=10, color=p["text3"]),
        ]),
        ft.Switch(value=value, on_change=on_change, disabled=disabled,
                  active_color=p["accent"]),
    ])


def _head(p, icon, text, size=15):
    """A card heading with its own themed mark.

    Every glyph on this page is a Material icon tinted from `PAL()`, never
    an emoji: Settings is chrome, and chrome follows all 24 themes. An
    emoji is painted by the font and stays the same colour on every one of
    them, which reads as a sticker on top of the design rather than part
    of it.
    """
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[ft.Icon(icon, size=16, color=p["accent"]),
                            title(text, size)])


def _tap_target(p, content, on_click, *, tooltip=None, on=False, padding=None):
    """A row you can press, that says so when you press it.

    `ink=True` is Flet's own Material ripple and is the whole of the press
    feedback story for a Container - without it a tap on a card row is
    completely silent, which is what "flet's showcase feels smooth and
    this does not" is about. `animate` makes the selected/unselected
    colour change a 120 ms transition rather than a jump between frames.
    """
    return ft.Container(
        padding=padding or PAD(10, 12), border_radius=RAD(9), ink=True,
        on_click=on_click, tooltip=tooltip, content=content,
        ink_color=ft.Colors.with_opacity(0.18, p["accent"]),
        animate=ft.Animation(120, ft.AnimationCurve.EASE_OUT),
        bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else p["surface"],
        border=BRD(p["accent"] if on else p["border"], 1),
        shadow=GLOW(blur=14, opacity=0.35) if on else None)


# ------------------------------------------------------------------ server
def server_card(page, saver):
    """Where the app looks for the dashbored server, and whether it is there.

    Shows the real reason a connection failed rather than a generic
    "offline": "blocked by auth" and "unreachable" need completely
    different fixes, and the app quietly falling back to cached data is
    the failure this card exists to make visible.
    """
    p = PAL()
    field = ft.TextField(
        value=config.SERVER_URL, dense=True, filled=True, text_size=13,
        label="Server address", hint_text="192.0.2.21:6900",
        border_color=p["border"], focused_border_color=p["accent"],
        focused_border_width=FOCUS_WIDTH(),
        color=p["text"], label_style=ft.TextStyle(color=p["text2"], size=11))
    light = ft.Container(width=9, height=9, border_radius=5,
                         animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT))
    status = ft.Text(size=11, color=p["text2"], selectable=True)
    check = ft.FilledButton(content="Check", height=34, icon=ft.Icons.WIFI_TETHERING)
    save = ft.OutlinedButton("Save", height=34, icon=ft.Icons.SAVE_OUTLINED)

    def paint():
        st = config.STATUS
        light.bgcolor = p["green"] if st["ok"] else p["pink"]
        status.value = (("connected - " if st["ok"] else "not connected - ")
                        + str(st.get("detail") or "not checked yet"))

    paint()

    async def probe(url):
        """The blocking probe, off Flet's event loop.

        `config.set_url` opens a socket and waits. Run inline it would
        hold the loop for up to PROBE_SECONDS, leaving the old frame up
        and dropping every tap that landed meanwhile - a Check button
        that looks broken because the whole app is.
        """
        light.bgcolor = p["text2"]
        status.value = f"checking - up to {PROBE_SECONDS}s…"
        check.disabled = save.disabled = True
        _update(page)
        try:
            await asyncio.to_thread(config.set_url, url)
            M.refresh()          # the cache was filled from the old address
        finally:
            check.disabled = save.disabled = False
        paint()
        _update(page)

    async def do_check(e):
        await probe(field.value)

    async def do_save(e):
        await probe(field.value)
        if S.prefs is None:
            saver.note("changed for now - no storage on this device, "
                       "so it resets on restart", bad=True)
        elif await config.save(S.prefs):
            saver.note(f"server address saved - {config.SERVER_URL}")
        else:
            saver.note("not saved - storage refused the write", bad=True)
        _update(page)

    check.on_click = do_check
    save.on_click = do_save

    return card(ft.Column(spacing=10, controls=[
        _head(p, ft.Icons.DNS, "Server"),
        field,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[light, ft.Container(expand=True, content=status)]),
        ft.Row(spacing=8, controls=[check, save]),
        sub("Check asks the address for /api/config and reports exactly what "
            "came back. Save keeps it for next launch. Off the LAN, connect "
            "the VPN first - the public hostname is behind a login and will "
            "not answer the app.", 10),
    ]))


# -------------------------------------------------------------- appearance
def theme_card(page, saver):
    """All 24 themes, named, previewed - and written down.

    Written down is the new part. The dashboard's picker changes `S.theme`
    and stores nothing, so it "famously resets every launch"; this one
    puts the key in storage and `main.py` reads it back before the first
    paint.
    """
    p = PAL()

    def pick(key):
        async def handler(e):
            apply_theme(page, key)
            motoprefs.set_theme(key)
            # The repaint rebuilds this page in the new palette, which is
            # what puts the tick on the swatch she just tapped. It happens
            # before the commit so the screen answers the tap immediately
            # and the storage write - which can be slow on a phone - is
            # not in the way of it.
            _repaint()
            await saver.commit(f"theme saved - {THEME_NAMES.get(key, key)}")
        return handler

    def swatch(key):
        t = THEMES[key]
        on = key == S.theme
        # Fixed width, deliberately. These sit in a wrapping Row, and a
        # Wrap hands its children unbounded constraints - anything with
        # expand=True in there paints over the whole screen. That exact
        # bug has made a page unusable twice in this codebase.
        return ft.Container(
            width=76, border_radius=RAD(10), padding=PAD(6, 4), ink=True,
            ink_color=ft.Colors.with_opacity(0.2, t["accent"]),
            animate=ft.Animation(140, ft.AnimationCurve.EASE_OUT),
            bgcolor=t["surface"], border=BRD(t["accent"], 2 if on else 1),
            on_click=pick(key), tooltip=THEME_NAMES.get(key, key),
            content=ft.Column(
                spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(spacing=3, alignment=ft.MainAxisAlignment.CENTER,
                           controls=[ft.Container(width=12, height=12,
                                                  border_radius=6, bgcolor=t[c])
                                     for c in ("accent", "green", "pink")]),
                    ft.Text(THEME_NAMES.get(key, key), size=9, color=t["text"],
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                            text_align=ft.TextAlign.CENTER),
                ]),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=1,
                                color=ft.Colors.with_opacity(0.5, t["accent"]))
            if on else None)

    return card(ft.Column(spacing=10, controls=[
        ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Container(expand=True, content=_head(p, ft.Icons.PALETTE, "Theme")),
            chip(THEME_NAMES.get(S.theme, S.theme), p["accent"], small=True),
        ]),
        sub("Kept for next launch, and shared with the other Agedashii apps "
            "on this device.", 10),
        ft.Row(spacing=8, wrap=True, run_spacing=8,
               controls=[swatch(k) for k in THEME_NAMES]),
    ]))


def appearance_card(page, saver):
    """Corner radius, accent glow, and the CRT overlay with its intensity.

    Three drawing settings the app previously had no opinion you could
    change: 87 hard-coded corner radii, no glow at all, and no overlay.
    They live together because they are one question - what does this
    thing look like - and apart from the theme because that is a palette
    and these are a shape and two effects.
    """
    p = PAL()

    async def pick_radius(e):
        valid = {str(n) for n in RADIUS_CHOICES}
        prefs.set_corner_radius(_picked(e, valid, str(prefs.corner_radius())))
        _repaint()
        await saver.commit(f"corners set to {prefs.corner_radius()}px")

    async def toggle_glow(e):
        prefs.set_accent_glow(e.control.value)
        _repaint()
        await saver.commit("accent glow " + ("on" if e.control.value else "off"))

    intensity_text = ft.Text(f"{prefs.crt_intensity()}%", size=11, color=p["text2"])
    intensity_slider = ft.Slider(
        min=0, max=100, divisions=20, value=prefs.crt_intensity(),
        active_color=p["accent"], disabled=not prefs.crt_overlay(),
        label="{value}%")

    def preview_intensity(e):
        # Live while dragging, saved once on release: dragging through
        # 0-100 must not write to storage on every intermediate tick.
        intensity_text.value = f"{round(e.control.value)}%"
        _update(page)

    async def commit_intensity(e):
        prefs.set_crt_intensity(round(e.control.value))
        _repaint()
        await saver.commit(f"CRT intensity set to {prefs.crt_intensity()}%")

    intensity_slider.on_change = preview_intensity
    intensity_slider.on_change_end = commit_intensity

    async def toggle_crt(e):
        prefs.set_crt_overlay(e.control.value)
        intensity_slider.disabled = not e.control.value
        _repaint()
        await saver.commit("CRT overlay " + ("on" if e.control.value else "off"))

    return card(ft.Column(spacing=10, controls=[
        _head(p, ft.Icons.AUTO_AWESOME, "Appearance"),
        _dropdown(p, str(prefs.corner_radius()),
                  [(str(n), RADIUS_LABELS.get(n, f"{n}px")) for n in RADIUS_CHOICES],
                  pick_radius, label="Corner rounding"),
        sub("Every rounded surface - cards, chips, buttons, fields, dialogs, "
            "the brand pill and the nav bar. Round dots stay round; they are "
            "circles, not corners.", 10),
        _switch_row(p, "Accent glow",
                    "An accent-coloured halo on headings, the brand pill and "
                    "the surface you are on. Follows the theme.",
                    prefs.accent_glow(), toggle_glow),
        _switch_row(p, "CRT overlay",
                    "Scanlines, a halftone dot mask and a soft vignette over "
                    "the whole app. It does not take taps.",
                    prefs.crt_overlay(), toggle_crt),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[ft.Text("Intensity", size=13, color=p["text"], width=64),
                         ft.Container(expand=True, content=intensity_slider),
                         intensity_text]),
        sub("How strong the overlay draws when it is on.", 10),
    ]))


# -------------------------------------------------------------- navigation
def navigation_card(page, saver):
    """The bottom bar, and the hamburger she asked to be able to hide.

    "Each of these smaller apps (not tofu) work better with the bottom
    navigation bar and less with the hamburger menu... i want the ability
    to show / hide the hamburger menu trigger as well."

    One rail, enforced here and again in `motoprefs.hamburger_shown()`:
    the last one standing cannot be switched off. An app with neither a
    bar nor a menu has no navigation and no way back to this page, and a
    setting that can strand you is worse than a setting you cannot have.
    """
    p = PAL()
    bar_on = prefs.bottom_bar_enabled()
    burger_on = motoprefs.hamburger_shown()

    async def toggle_bar(e):
        wanted = bool(e.control.value)
        if not wanted and not motoprefs.can_hide_bottom_bar():
            e.control.value = True
            saver.note("the hamburger is hidden, so the bar is the only way "
                       "around - show the hamburger first", bad=True)
            _update(page)
            return
        prefs.set_bottom_bar_enabled(wanted)
        _repaint()
        await saver.commit("bottom bar " + ("on" if wanted else "off"))

    async def toggle_burger(e):
        wanted = bool(e.control.value)
        if not motoprefs.set_hamburger_visible(wanted):
            e.control.value = True
            saver.note("the bottom bar is off, so the menu is the only way "
                       "around - switch the bar on first", bad=True)
            _update(page)
            return
        _repaint()
        await saver.commit("menu button " + ("shown" if wanted else "hidden"))

    return card(ft.Column(spacing=10, controls=[
        _head(p, ft.Icons.VIEW_AGENDA, "Navigation"),
        sub("Four surfaces fit on a bar with room to spare, so the bar is the "
            "way around this app and the menu is optional. One of the two "
            "always stays on.", 10),
        _switch_row(p, "Bottom bar",
                    "Trips, Garage, XNAV and Settings along the bottom. Off "
                    "gives the height back to the page.",
                    bar_on, toggle_bar,
                    disabled=not motoprefs.can_hide_bottom_bar() and bar_on),
        _switch_row(p, "Menu button",
                    "The hamburger in the top left. It opens the drawer, "
                    "which also lists the tabs inside Trips and Garage. "
                    "Hidden, its slot in the topbar goes away entirely.",
                    burger_on, toggle_burger,
                    disabled=not motoprefs.can_hide_hamburger() and burger_on),
        sub("Every tab is still reachable with the menu hidden - Trips and "
            "Garage each carry their own tab bar at the top of the surface.",
            10),
    ]))


# ------------------------------------------------------------------- units
def units_card(page, saver):
    """Miles or kilometres. Moto's own domain setting, and the one that was
    a lie: the preference existed and not one distance in this app read it."""
    p = PAL()

    async def pick(e):
        prefs.set_unit_system(_picked(e, set(prefs.UNIT_SYSTEMS),
                                      prefs.unit_system()))
        _repaint()
        await saver.commit(f"units saved - {U.distance_unit()}")

    return card(ft.Column(spacing=8, controls=[
        _head(p, ft.Icons.STRAIGHTEN, "Units"),
        _dropdown(p, prefs.unit_system(),
                  [("imperial", "Imperial - miles, mph, feet"),
                   ("metric", "Metric - km, km/h, metres")],
                  pick, label="Measure things in"),
        sub(f"Right now: routes, recorded rides and odometers in "
            f"{U.distance_unit()}, speeds in {U.speed_unit()}, climb in "
            f"{U.elevation_unit()}. Records are stored exactly as the server "
            f"sent them - this converts on the way to the screen, so nothing "
            f"already logged changes meaning.", 10),
    ]))


# -------------------------------------------------------------------- help
def help_card(page):
    """The way in to "How this works". A row, not a buried link."""
    p = PAL()

    def open_help(e):
        goto = getattr(S, "goto", None)
        if callable(goto):
            goto("help")

    row = ft.Row(spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                 controls=[
                     ft.Icon(ft.Icons.MENU_BOOK, size=20, color=p["accent"]),
                     ft.Column(spacing=1, expand=True, controls=[
                         ft.Text("How this works", size=13, color=p["text"],
                                 weight=ft.FontWeight.W_600),
                         ft.Text("What each surface does, what works with no "
                                 "signal, and what refuses outright.",
                                 size=10, color=p["text3"]),
                     ]),
                     ft.Icon(ft.Icons.CHEVRON_RIGHT, size=18, color=p["text2"]),
                 ])
    return card(_tap_target(p, row, open_help, tooltip="Open the guide"), pad=4)


# ------------------------------------------------------------- diagnostics
def _diag_row(p, label, value, ok=None):
    """One fact, with its own colour. `ok=None` means neither good nor bad."""
    color = p["text2"] if ok is None else (p["green"] if ok else p["yellow"])
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                  controls=[
                      ft.Container(width=104,
                                   content=ft.Text(label, size=11, color=p["text3"])),
                      ft.Text(str(value), size=11, color=color, selectable=True,
                              expand=True),
                  ])


def diagnostics_card(page):
    """The facts you would otherwise need a log file to find.

    Every lookup is guarded: Diagnostics exists to explain a broken app,
    so it is the last place that may itself break. A probe that raises
    reports the exception instead of taking the surface down with it.
    """
    p = PAL()

    def safe(fn, default="unavailable"):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - show the fault, do not hide it
            return f"{default} ({type(exc).__name__})"

    st = config.STATUS
    live = safe(M.STATUS_LIVE, False)
    live_ok = live is True

    def maps():
        return M.mbtiles_path() or "none installed - the map uses online tiles"

    def tiles():
        s = tileserver.status()
        if not s.get("running"):
            return "not running - starts when a map is first opened"
        return (f"127.0.0.1:{s.get('port')} "
                f"({s.get('mbtiles') or 'no archive'})")

    def graph():
        path = routing.graph_path()
        if not path:
            return "none installed - routing falls back to the server"
        state = routing.status()
        return f"{path} ({state.get('phase')})"

    def queue():
        s = M.sync_status()
        return s.get("label") or "nothing waiting"

    map_path = safe(maps)
    rows = [
        _diag_row(p, "Server", st.get("url") or config.SERVER_URL),
        _diag_row(p, "Data", ("live - " if live_ok else "cached - ")
                  + str(st.get("detail", "not checked yet")), live_ok),
        _diag_row(p, "Outbox", safe(queue)),
        _diag_row(p, "App version", safe(prefs.app_version)),
        _diag_row(p, "Offline map", map_path, str(map_path).startswith("/")),
        _diag_row(p, "Tile server", safe(tiles)),
        _diag_row(p, "Route graph", safe(graph)),
        _diag_row(p, "Storage", "SharedPreferences ready" if S.prefs is not None
                  else "unavailable - settings reset on restart",
                  S.prefs is not None),
        _diag_row(p, "Theme", THEME_NAMES.get(S.theme, S.theme)),
        _diag_row(p, "Units", f"{U.distance_unit()} / {U.speed_unit()} / "
                              f"{U.elevation_unit()}"),
    ]
    return card(ft.Column(spacing=8, controls=[
        ft.Row(controls=[
            ft.Container(expand=True,
                         content=_head(p, ft.Icons.MONITOR_HEART, "Diagnostics")),
            chip("live" if live_ok else "cached",
                 p["green"] if live_ok else p["yellow"], small=True)]),
        *rows,
    ]))


# ------------------------------------------------------------------- build
def build(page):
    """The Settings surface.

    Takes only `page`, like every other surface here, so the shell's
    `build_surface(page, key)` needs no special case for it. The two
    callbacks it needs - repaint the shell, go to another surface - are
    read off `S` where the shell parks them, and both are absent when this
    is built standalone, which is a page with nothing to repaint rather
    than a crash.
    """
    # Startup loads both stores before the first paint; this covers a
    # harness that has not, and repaints once the values arrive.
    prefs.hydrate(page, S.prefs, on_done=getattr(S, "rerender", None))

    saver = _Saver(page)
    return ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        server_card(page, saver),
        theme_card(page, saver),
        appearance_card(page, saver),
        navigation_card(page, saver),
        units_card(page, saver),
        saver.control,
        help_card(page),
        diagnostics_card(page),
    ])
