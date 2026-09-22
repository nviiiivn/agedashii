"""Settings - a surface of this app, not a dialog it can open.

It was a dialog: the server address, three switches and a row of radius
chips, opened off a gear in the topbar. Her answer to that was "where is the
settings? i think you have not taken the format/template literally --- each
of these independent apps needs their own icons/labels/readme wikis INSIDE on
how to use/settings menu etc". A dialog is something an app shows you; a
surface is something an app *has*. This is the second thing.

So it is a destination on the bottom bar alongside Wat2Do and Future Finds,
it scrolls, it is built from the same cards every other surface is built
from, and it carries everything that was in the dialog plus the settings
that had nowhere to live: the CRT intensity (stored and honoured, with no
control in this app until now), the hamburger trigger, and the five that
only mean anything here - how a day is labelled, whether finished tasks stay
on screen, whether pain and spoons are tracked and whether they steer the
suggestion, what the template editor writes, how many subjects a mission
rolls, and where distances are measured from.

Two stores, on purpose. Appearance and the bottom bar are
`agedashii_washi.prefs`, shared with the dashboard and the sibling apps so
they mean the same thing everywhere. The rest is `agedashii_watudo.prefs`,
because washi has no key for them and inventing one there would make every
other app carry a setting about a checklist it does not have. `_Saver`
writes both, so nothing on this page has to know which store its value went
to.

Shaped after `agedashii_tofu/pages/settings.py` - the card-per-question
layout, the one shared status line, the sentence under every control - and
importing nothing from it. Watudo does not depend on the dashboard.
"""
import asyncio

import flet as ft

from agedashii_qi import (CRT_INTENSITY_MAX, CRT_INTENSITY_MIN, PAL,
                          RADIUS_CHOICES, RADIUS_LABELS, S, THEME_NAMES,
                          THEMES, apply_theme)
from agedashii_washi import api as M
from agedashii_washi import config, prefs
from agedashii_washi.widgets import BRD, PAD, card, sub, title

from .. import prefs as wprefs
from .. import ui as U


# The last thing the status line said, kept out here rather than on the
# _Saver instance. Appearance and navigation settings repaint the whole
# shell, which builds a brand new Settings page with a brand new status
# line - so a message written to the old one would be thrown away in the
# same breath it was written. Seeding each new line from here is what makes
# "saved" survive the repaint that the setting itself caused.
_STATUS = {"msg": "", "bad": False}


class _Saver:
    """One shared status line for every edit on this page.

    Same contract the dashboard's Settings has: the screen may change
    immediately - these are local preferences, not server records - but a
    write that did not land says so rather than pretending. Every handler
    below ends in `commit()`, so there is exactly one place that knows
    whether storage is working.
    """

    def __init__(self, page):
        p = PAL()
        self.page = page
        self.text = ft.Text(_STATUS["msg"], size=11, selectable=True,
                            color=p["pink"] if _STATUS["bad"] else p["text2"])

    @property
    def control(self):
        return ft.Container(padding=PAD(2, 0), content=self.text)

    def note(self, msg, bad=False):
        p = PAL()
        _STATUS.update({"msg": msg, "bad": bad})
        self.text.value = msg
        self.text.color = p["pink"] if bad else p["text2"]

    def refresh(self):
        try:
            self.page.update()
        except Exception:      # noqa: BLE001 - not mounted yet
            pass

    async def commit(self, msg="saved"):
        """Persist both stores and say what happened."""
        if S.prefs is None:
            self.note("changed for now - no storage on this device, so it "
                      "resets on restart", bad=True)
        elif await prefs.save(S.prefs) and await wprefs.save(S.prefs):
            self.note(msg)
        else:
            self.note("not saved - storage refused the write", bad=True)
        self.refresh()


def _repainter(on_change):
    """Call the shell's repaint, and never let it lose the edit."""
    def repaint():
        if not on_change:
            return
        try:
            on_change()
        except Exception:      # noqa: BLE001 - a repaint must not cost the setting
            pass
    return repaint


# ------------------------------------------------------------------ server
def server_card(page, saver, on_change=None):
    """Where the day's record comes from, and why it is not answering.

    Watudo has its own SharedPreferences store, so it cannot inherit an
    address set in the dashboard: without this card it could only ever talk
    to the two addresses compiled into `config.DEFAULT_CANDIDATES`.

    The probe is deliberately off the event loop. `config.set_url` opens a
    socket with a 3 second timeout, and three seconds inside a synchronous
    Flet handler is three seconds of dropped taps - the exact failure that
    once made the dashboard's status dots look dead.
    """
    p = PAL()
    repaint = _repainter(on_change)
    status = ft.Text(_server_line(), size=11, color=p["text2"], selectable=True)
    address = U.field(config.SERVER_URL, "http://host:6900", expand=True)

    async def check(e=None):
        url = (address.value or "").strip()
        if not url:
            return
        status.value = "checking..."
        status.color = p["text2"]
        saver.refresh()
        ok, detail = await asyncio.to_thread(config.set_url, url)
        await config.save(S.prefs, url)
        # Pointing somewhere else invalidates every cached answer; they came
        # from a different server.
        M.refresh()
        status.value = f"{config.SERVER_URL} - {detail}"
        status.color = p["text2"] if ok else p["pink"]
        saver.note("server address saved" if ok else "saved, but it did not answer",
                   bad=not ok)
        saver.refresh()
        repaint()

    address.on_submit = check

    return card(ft.Column(spacing=8, controls=[
        _head("Server", ft.Icons.DNS),
        sub("Where the day's record and the Future lists come from. Everything "
            "else is on this device and works with the radio off.", 11),
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
            address,
            ft.IconButton(ft.Icons.REFRESH, icon_color=p["accent"],
                          tooltip="Check this address", on_click=check)]),
        status,
        sub("Off the LAN, connect the VPN first - the public hostname is behind "
            "a login and will not answer the app.", 10),
    ]))


def _server_line():
    st = config.STATUS
    return f"{st.get('url') or config.SERVER_URL} - {st.get('detail') or ''}"


# -------------------------------------------------------------- appearance
def theme_card(page, saver, on_change=None):
    """All 24 themes, each previewing itself, and the one you are on lit.

    Named and tappable rather than a next/previous button: 24 is far too
    many to walk one tap at a time, and a tooltip naming the current one
    needs a mouse, which a phone does not have.

    The choice is written down. In the dashboard the picker is per-session
    and the theme is back to the default every launch - a small annoyance
    there and a larger one in an app you open to tick three boxes.
    """
    repaint = _repainter(on_change)

    def swatch(key):
        t = THEMES[key]
        on = key == S.theme

        async def pick(e):
            apply_theme(page, key)
            wprefs.set_theme(key)
            await saver.commit(f"theme - {THEME_NAMES.get(key, key)}")
            repaint()

        # Fixed width, and nothing inside it expands: these are laid out in
        # a Row(wrap=True), which hands its children unbounded constraints.
        return U.tap(ft.Container(
            width=78, padding=PAD(6, 6), border_radius=8, on_click=pick,
            bgcolor=t["surface"], border=BRD(t["accent"] if on else t["border"],
                                             2 if on else 1),
            content=ft.Column(spacing=4,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                ft.Row(spacing=3, alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t[c])
                    for c in ("accent", "green", "pink")]),
                ft.Text(THEME_NAMES[key], size=9, color=t["text"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.CENTER),
            ])), tint=t["accent"])

    return card(ft.Column(spacing=10, controls=[
        _head("Theme", ft.Icons.PALETTE),
        sub(f"On now: {THEME_NAMES.get(S.theme, S.theme)}. Remembered on this "
            "device, so it is still here at the next launch.", 11),
        ft.Row(spacing=8, wrap=True, run_spacing=8,
               controls=[swatch(k) for k in THEME_NAMES]),
    ]))


def appearance_card(page, saver, on_change=None):
    """Corner radius, the accent glow, and the CRT overlay with its slider.

    The intensity slider is new here. The value was already stored and
    already honoured by `crt_overlay()` - this app simply had no control for
    it, so the overlay was on or off at whatever intensity was last set from
    the dashboard, which on a phone that never ran the dashboard meant the
    shipped 40 forever.
    """
    p = PAL()
    repaint = _repainter(on_change)

    def pick_radius(value):
        """An async on_click per chip, rather than one handler reading the
        control it was fired from: a chip carries its value in the closure,
        so there is nothing to read back and nothing to get wrong."""
        async def handler(e):
            prefs.set_corner_radius(value)
            await saver.commit(f"corners - {RADIUS_LABELS.get(value, value)}")
            repaint()
        return handler

    async def toggle_glow(e):
        prefs.set_accent_glow(bool(e.control.value))
        await saver.commit("accent glow " + ("on" if e.control.value else "off"))
        repaint()

    intensity_text = ft.Text(f"{prefs.crt_intensity()}%", size=11, color=p["text2"])
    intensity = ft.Slider(
        min=CRT_INTENSITY_MIN, max=CRT_INTENSITY_MAX, divisions=20,
        value=prefs.crt_intensity(), active_color=p["accent"],
        disabled=not prefs.crt_overlay(), label="{value}%")

    def preview(e):
        # Live while dragging, saved once the thumb is released: a whole-app
        # repaint on every intermediate tick would drop frames on a phone.
        intensity_text.value = f"{round(e.control.value)}%"
        saver.refresh()

    async def commit_intensity(e):
        prefs.set_crt_intensity(round(e.control.value))
        intensity_text.value = f"{prefs.crt_intensity()}%"
        await saver.commit(f"CRT intensity {prefs.crt_intensity()}%")
        repaint()

    intensity.on_change = preview
    intensity.on_change_end = commit_intensity

    async def toggle_crt(e):
        prefs.set_crt_overlay(bool(e.control.value))
        intensity.disabled = not e.control.value
        await saver.commit("CRT overlay " + ("on" if e.control.value else "off"))
        repaint()

    return card(ft.Column(spacing=12, controls=[
        _head("Appearance", ft.Icons.TUNE),
        ft.Column(spacing=6, controls=[
            ft.Text("Corner rounding", size=13, color=p["text"]),
            ft.Row(spacing=6, wrap=True, run_spacing=6, controls=[
                U.pill(f"{v}px" if v else "Square", on=prefs.corner_radius() == v,
                       tooltip=RADIUS_LABELS.get(v), on_click=pick_radius(v))
                for v in RADIUS_CHOICES]),
            sub("Every rounded surface in the app - cards, chips, fields, the "
                "brand pill, the nav bar. Round dots stay round; they are "
                "circles, not corners.", 10),
        ]),
        U.switch_row("Accent glow",
                     "An accent halo on headings, the brand pill and whichever "
                     "surface you are on. Follows the theme.",
                     prefs.accent_glow(), toggle_glow, icon=ft.Icons.AUTO_AWESOME),
        U.switch_row("CRT overlay",
                     "Scanlines, a halftone dot mask and a soft vignette over "
                     "the whole app. It does not take taps.",
                     prefs.crt_overlay(), toggle_crt, icon=ft.Icons.TV),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text("Intensity", size=12, color=p["text2"], width=62),
            ft.Container(expand=True, content=intensity),
            intensity_text,
        ]),
    ]))


# -------------------------------------------------------------- navigation
def navigation_card(page, saver, on_change=None, reach=None):
    """The bottom bar, the hamburger, and what is left reaching each surface.

    Her words: "Each of these smaller apps (not tofu) work better with the
    bottom navigation bar and less with the hamburger menu so... that's also
    a reason i want that settings included. i want the ability to show / hide
    the hamburger menu trigger as wel please."

    So the bar is the primary navigation and the menu is the optional one -
    the reverse of the dashboard, which has nine pages and needs a drawer.
    The one rail: something has to reach every surface. With the bar
    switched off the menu comes back whether or not it is switched on, and
    its switch says so instead of silently disagreeing with the topbar.

    `reach` is the shell's own answer to "where can each surface be reached
    from", passed in rather than recomputed here so this card cannot end up
    describing a navigation the app does not actually have.
    """
    p = PAL()
    repaint = _repainter(on_change)

    async def toggle_bar(e):
        prefs.set_bottom_bar_enabled(bool(e.control.value))
        await saver.commit("bottom bar " + ("on" if e.control.value else "off"))
        repaint()

    async def toggle_menu(e):
        wprefs.set_menu_button(bool(e.control.value))
        await saver.commit("menu button "
                           + ("shown" if e.control.value else "hidden"))
        repaint()

    bar_on = prefs.bottom_bar_enabled()
    forced = not bar_on
    rows = [
        _head("Navigation", ft.Icons.EXPLORE),
        U.switch_row("Bottom bar",
                     "Wat2Do, Future Finds and Settings along the bottom. This "
                     "is how the app is meant to be got around.",
                     bar_on, toggle_bar, icon=ft.Icons.VIEW_SIDEBAR),
        U.switch_row("Menu button",
                     "The hamburger in the topbar. Hidden, its slot goes away "
                     "rather than leaving a gap - the bar reaches everything."
                     if not forced else
                     "On, and locked on: with the bottom bar off the menu is "
                     "the only thing left that reaches every surface.",
                     wprefs.menu_button() or forced, toggle_menu,
                     icon=ft.Icons.MENU, disabled=forced),
    ]
    if reach:
        rows.append(ft.Container(
            padding=PAD(6, 0),
            content=ft.Column(spacing=2, controls=[
                ft.Text(line, size=10, color=p["text3"]) for line in reach])))
    return card(ft.Column(spacing=12, controls=rows))


# ------------------------------------------------------------------- day
def day_card(page, saver):
    """How Wat2Do shows a day.

    Neither switch repaints the shell: both change the Wat2Do surface, which
    is not the one on screen while you are setting them, and it will be
    built with the new answer the moment you go back to it.
    """

    async def toggle_friendly(e):
        wprefs.set_friendly_dates(bool(e.control.value))
        await saver.commit("dates " + ("friendly" if e.control.value else "plain"))

    async def toggle_hide(e):
        wprefs.set_hide_done(bool(e.control.value))
        await saver.commit("finished tasks "
                           + ("hidden" if e.control.value else "kept on screen"))

    return card(ft.Column(spacing=12, controls=[
        _head("Your day", ft.Icons.TODAY),
        U.switch_row("Friendly day names",
                     'Today reads "Today" rather than its date. Off, every day '
                     "is shown as the date it is.",
                     wprefs.friendly_dates(), toggle_friendly,
                     icon=ft.Icons.CALENDAR_MONTH),
        U.switch_row("Hide finished tasks",
                     "A ticked task leaves the checklist instead of staying "
                     "struck through. The count still counts it.",
                     wprefs.hide_done(), toggle_hide, icon=ft.Icons.CHECKLIST),
    ]))


def vitals_card(page, saver):
    """Pain and spoons: whether they are recorded, and whether they decide.

    Neither switch repaints the shell. They change the Wat2Do surface, which
    is not the surface you are looking at while you set them, and a repaint
    would scroll Settings back to the top to update a page nobody can see.
    The one thing that *does* have to react immediately is the second switch
    greying out when the first goes off, so this card repaints itself - the
    same `rows`/`paint` shape the dashboard's layout card uses.
    """
    rows = ft.Column(spacing=12)

    async def toggle_track(e):
        wprefs.set_track_vitals(bool(e.control.value))
        paint()
        await saver.commit("pain & spoons "
                           + ("tracked" if e.control.value else "not tracked"))

    async def toggle_steer(e):
        wprefs.set_vitals_steer(bool(e.control.value))
        await saver.commit("steering " + ("on" if e.control.value else "off"))

    def paint():
        tracking = wprefs.track_vitals()
        rows.controls = [
            _head("Pain & spoons", ft.Icons.MONITOR_HEART),
            U.switch_row("Track them",
                         "The two sliders on Wat2Do, saved to the day's record "
                         "when you let go. Off, the card is not drawn and "
                         "neither number is written.",
                         tracking, toggle_track, icon=ft.Icons.HEALING),
            U.switch_row("Let them steer the day",
                         "Low spoons keeps the suggestion close, high pain "
                         "makes it indoor, and the mission rolls a smaller "
                         "radius. Off, only the weather decides." if tracking
                         else "Nothing to steer with while they are not being "
                         "tracked.",
                         wprefs.vitals_steer(), toggle_steer,
                         icon=ft.Icons.LIGHTBULB_OUTLINE, disabled=not tracking),
        ]

    paint()
    return card(rows)


def template_card(page, saver):
    """What the checklist template editor writes, and how big a mission is."""

    async def pick_marker(e):
        wprefs.set_sub_marker(U.picked(e, set(wprefs.SUB_MARKERS),
                                       wprefs.sub_marker()))
        await saver.commit("sub-task marker saved")

    async def pick_subjects(e):
        valid = {str(n) for n in wprefs.MISSION_SUBJECT_CHOICES}
        wprefs.set_mission_subjects(
            U.picked(e, valid, str(wprefs.mission_subjects())))
        await saver.commit("mission size saved")

    counts = [(str(n), "3 to 6 - let the dice decide" if n == 0
               else f"{n} subject{'' if n == 1 else 's'}")
              for n in wprefs.MISSION_SUBJECT_CHOICES]

    return card(ft.Column(spacing=10, controls=[
        _head("Template & missions", ft.Icons.EDIT_NOTE),
        U.dropdown(wprefs.sub_marker(),
                   [(k, wprefs.SUB_MARKER_LABELS[k]) for k in wprefs.SUB_MARKERS],
                   pick_marker, label="Sub-task lines are written as"),
        sub("Only what this end writes. Both markers are read back correctly "
            "here and in the browser, so a template written either way opens "
            "the same in both.", 10),
        U.dropdown(str(wprefs.mission_subjects()), counts, pick_subjects,
                   label="Subjects per mission"),
        sub("How many photo subjects Roll a mission deals off the deck. A "
            "number larger than the deck deals the whole deck.", 10),
    ]))


def origin_card(page, saver):
    """Where every distance in this app is measured from.

    The honest half of the location story. This app declares no location
    permission and asks the device nothing - so rather than a fixed origin
    nobody can change, the origin is something you type. It is what
    `future.scan()`'s `centre` parameter has been waiting for, and it moves
    the map's home marker, the POI scan, the mission radius, the thrift list
    and every distance column together.
    """
    p = PAL()
    from . import _wat2do_parts as W

    current = wprefs.origin()
    where = ft.Text("", size=11, color=p["text2"], selectable=True)

    def say_where():
        lat, lon = W.origin()
        where.value = (f"{lat:.5f}, {lon:.5f}"
                       + ("  (home)" if not wprefs.origin() else ""))

    say_where()
    entry = U.field(wprefs.format_origin(current), "37.769972, -122.216445",
                    expand=True)

    async def use(e=None):
        if not wprefs.set_origin(entry.value or ""):
            saver.note("that is not a latitude and longitude - try "
                       "37.769972, -122.216445", bad=True)
            saver.refresh()
            return
        # Every cached answer was measured from somewhere else.
        M.refresh()
        say_where()
        await saver.commit("measuring from " + W.origin_label())

    async def reset(e=None):
        wprefs.set_origin("")
        entry.value = ""
        M.refresh()
        say_where()
        await saver.commit("measuring from home again")

    entry.on_submit = use

    return card(ft.Column(spacing=8, controls=[
        _head("Measure distances from", ft.Icons.PLACE),
        sub("Nothing in Watudo asks the device where it is - it has no "
            "location permission at all. This is the point everything is "
            "measured from instead: the map, the scan, the mission radius and "
            "every distance in a list.", 11),
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
            entry,
            ft.IconButton(ft.Icons.CHECK, icon_color=p["accent"],
                          tooltip="Measure from this point", on_click=use),
            ft.IconButton(ft.Icons.HOME, icon_color=p["text2"],
                          tooltip="Back to home", on_click=reset)]),
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[U.glyph(ft.Icons.MY_LOCATION, size=14), where]),
    ]))


def help_card(page, go=None):
    """The way into the wiki. It is a surface, so this is a link, not a dialog."""
    if not go:
        return None
    return card(ft.Column(spacing=8, controls=[
        _head("How this works", ft.Icons.MENU_BOOK),
        U.link_row("Read the short version",
                   "What each surface does, what queues offline, and what "
                   "needs the server.", ft.Icons.HELP_OUTLINE,
                   lambda: go("help")),
    ]))


def diagnostics_card(page):
    """The facts you would otherwise need a log file to find.

    Every lookup is guarded: this card exists to explain a broken app, so it
    is the last place that may itself break.
    """
    p = PAL()
    from . import _wat2do_parts as W

    def safe(fn, default="unavailable"):
        try:
            return fn()
        except Exception as exc:   # noqa: BLE001 - show the fault, do not hide it
            return f"{default} ({type(exc).__name__})"

    st = config.STATUS
    live = safe(M.STATUS_LIVE, False) is True
    queued = safe(lambda: M.sync_status().get("label") or "nothing waiting")

    rows = [
        ("Server", st.get("url") or config.SERVER_URL, None),
        ("Data", ("live - " if live else "not connected - ")
         + str(st.get("detail", "not checked yet")), live),
        ("Outbox", str(queued), None),
        ("Storage", "on this device" if S.prefs is not None
         else "unavailable - settings reset on restart", S.prefs is not None),
        ("Measuring from", W.origin_label(), None),
        ("App version", safe(prefs.app_version), None),
    ]
    return card(ft.Column(spacing=8, controls=[
        _head("Diagnostics", ft.Icons.INFO_OUTLINE),
        *[ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                 controls=[
                     ft.Container(width=104,
                                  content=ft.Text(label, size=11, color=p["text3"])),
                     ft.Text(str(value), size=11, expand=True, selectable=True,
                             color=(p["text2"] if ok is None
                                    else (p["green"] if ok else p["yellow"])))])
          for label, value, ok in rows],
    ]))


def _head(text, icon):
    """A card heading with its own themed mark."""
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                  controls=[U.glyph(icon, size=17, color=PAL()["accent"]),
                            title(text, 15)])


def build(page, on_change=None, go=None, reach=None):
    """The whole Settings surface.

    `on_change` is the shell's repaint - every appearance and navigation
    setting here reaches the chrome, so repainting only this page would
    leave the app half changed until you navigated away and back.
    `go(key)` moves to another surface; `reach` is the shell's description
    of how each surface can be reached, shown under the navigation switches.
    """
    saver = _Saver(page)
    cards = [
        server_card(page, saver, on_change),
        navigation_card(page, saver, on_change, reach),
        theme_card(page, saver, on_change),
        appearance_card(page, saver, on_change),
        day_card(page, saver),
        vitals_card(page, saver),
        template_card(page, saver),
        origin_card(page, saver),
        help_card(page, go),
        diagnostics_card(page),
    ]
    return ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO,
                     controls=[saver.control] + [c for c in cards if c])
