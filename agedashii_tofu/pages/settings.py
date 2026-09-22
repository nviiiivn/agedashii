"""Settings: the app's answer to the website's GridStack layout editor.

The website lets you drag panels around; the app could only pick a theme.
What is here now is the phone-shaped equivalent - reorder the pages and
choose, separately, which appear in the menu and which ride the bottom bar,
reorder/hide/resize the cards on every surface - plus the settings that were
only ever hard-coded constants (units, the DASH sodium ceiling, how long
cached data stays good), and a Diagnostics card that answers "why is it
showing sample data" without a log file.

The menu and the bar are two controls per row, not one. They were one, and
one eye drove both surfaces: "seemingly theres no way to customize the
bottom bar without the menu becoming affected.. it's not different /
optional like i asked for user preferences."

Reordering *here* is done with arrows rather than drag-and-drop on purpose:
these lists live inside a scrolling column, and a long-press drag inside a
scroll view is ambiguous on a touch screen - the gesture that reorders a row
is the same one that scrolls past it. The cards on a page can be dragged,
because there the handle is a distinct icon rather than the whole row; see
panels._editable for why that distinction is what makes it safe.

Everything is stored by agedashii_tofu/prefs.py, which also owns the read API the
rest of the app consumes. This page only edits; it never decides what a
preference means.
"""
import flet as ft
from ..ui import (PAL, PAD, BRD, RAD, card, chip, title, sub, S,
                  apply_theme, FOCUS_WIDTH, GLOW,
                  RADIUS_CHOICES, RADIUS_LABELS)
from ..theme_data import THEMES, THEME_NAMES
from .. import api, config, panels, prefs


def server_card(page):
    """Where the app looks for the dashbored server, and whether it is there.

    Deliberately shows the real reason a connection failed rather than a
    generic "offline": "blocked by auth" and "unreachable" need completely
    different fixes, and the app silently falling back to sample data is the
    failure mode this card exists to make visible.
    """
    p = PAL()
    field = ft.TextField(
        value=config.SERVER_URL, dense=True, filled=True, text_size=13,
        label="Server address", hint_text="192.0.2.21:6900",
        border_color=p["border"], focused_border_color=p["accent"],
        focused_border_width=FOCUS_WIDTH(),
        color=p["text"], label_style=ft.TextStyle(color=p["text2"], size=11),
    )
    light = ft.Container(width=9, height=9, border_radius=5)
    status = ft.Text(size=11, color=p["text2"], selectable=True)

    def paint():
        st = config.STATUS
        light.bgcolor = p["green"] if st["ok"] else p["pink"]
        status.value = ("live data - " if st["ok"] else "sample data - ") + st["detail"]

    paint()

    async def do_test(e):
        status.value = "checking..."
        light.bgcolor = p["text2"]
        page.update()
        config.set_url(field.value)
        api.refresh()          # drop cached responses from the old address
        paint()
        page.update()

    async def do_save(e):
        config.set_url(field.value)
        api.refresh()
        saved = await config.save(S.prefs) if S.prefs else False
        paint()
        if not saved:
            status.value += "  (could not persist - will reset on restart)"
        page.update()

    return card(ft.Column(spacing=10, controls=[
        title("Server", 15),
        field,
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[light, ft.Container(expand=True, content=status)]),
        ft.Row(spacing=8, controls=[
            ft.FilledButton("Save", on_click=do_save, height=34),
            ft.OutlinedButton("Test", on_click=do_test, height=34),
        ]),
        sub("Off the LAN, connect the VPN first - the public hostname is behind "
            "a login and will not answer the app.", 10),
    ]))


class _Saver:
    """One shared status line for every preference edit on this page.

    Same contract as _forms.Writer: the screen may change optimistically
    (these are local preferences, not server records), but a write that did
    not land says so rather than pretending.
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
        elif await prefs.save(S.prefs):
            self.note(msg)
        else:
            self.note("not saved - storage refused the write", bad=True)
        self.page.update()


def _icon_toggle(icon, on, tooltip, on_click, *, disabled=False):
    p = PAL()
    return ft.IconButton(
        icon, icon_size=18, tooltip=tooltip, on_click=on_click, disabled=disabled,
        icon_color=(p["text3"] if disabled else (p["accent"] if on else p["text3"])),
    )


def _arrows(first, last, up, down):
    p = PAL()
    return [
        ft.IconButton(ft.Icons.KEYBOARD_ARROW_UP, icon_size=18, on_click=up,
                      disabled=first, tooltip="Move up",
                      icon_color=p["text3"] if first else p["text2"]),
        ft.IconButton(ft.Icons.KEYBOARD_ARROW_DOWN, icon_size=18, on_click=down,
                      disabled=last, tooltip="Move down",
                      icon_color=p["text3"] if last else p["text2"]),
    ]


def _surface_toggle(p, text, on, tooltip, on_click, disabled=False):
    """One labelled on/off pill - "Menu" or "Bar" - for one page.

    A word, not an icon. The eye this replaces was one control for two
    surfaces, so it could not say which it meant and in fact drove both:
    "seemingly theres no way to customize the bottom bar without the menu
    becoming affected". Two pills that name themselves cannot have that
    problem, and they read on a phone where a tooltip never appears.
    """
    colour = p["text3"] if (disabled or not on) else p["accent"]
    return ft.Container(
        padding=PAD(8, 4), border_radius=RAD(6), ink=not disabled,
        bgcolor=ft.Colors.with_opacity(0.16, p["accent"]) if on and not disabled else None,
        border=BRD(p["border"], 1) if not on else None,
        tooltip=tooltip, on_click=None if disabled else on_click,
        content=ft.Text(text, size=11, color=colour,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400))


def _where(in_menu, in_bar):
    """The one-line answer to "where does this page appear?"."""
    if in_menu and in_bar:
        return "menu + bar"
    if in_menu:
        return "menu only"
    if in_bar:
        return "bar only"
    return "nowhere - off both"


def layout_card(page, saver, on_layout=None):
    """Reorder the nine pages and choose, per page, menu and/or bottom bar.

    The menu and the bar are two independent sets, which is the whole
    point of this card. Before, `bottom_bar_pages()` filtered through
    `visible_pages()`, so one eye per row drove both surfaces: hiding
    "garage" took it out of the drawer *and* off the bar, and there was no
    way to have a page in one but not the other.
    """
    p = PAL()
    rows = ft.Column(spacing=0)

    def paint():
        order = prefs.page_order()
        in_menu = set(prefs.visible_pages())
        in_bar = set(prefs.bottom_bar_pages())
        rows.controls = [row(k, i, order) for i, k in enumerate(order)]
        count = ft.Text(
            f"{len(in_bar)} of {prefs.MAX_BOTTOM} bottom-bar slots used - "
            f"{len(in_menu)} pages in the menu",
            size=10, color=p["text3"])
        rows.controls.append(ft.Container(padding=PAD(6, 8), content=count))

        # The bar duplicates the drawer, which now lists every page *and* its
        # tabs, so it is worth switching off entirely rather than only
        # choosing what rides on it. Nothing becomes unreachable either way.
        async def toggle_bar(e):
            prefs.set_bottom_bar_enabled(e.control.value)
            await saver.commit("bottom bar " + ("on" if e.control.value else "off"))
            paint()
            if on_layout:
                on_layout()

        rows.controls.append(ft.Container(
            padding=PAD(6, 6),
            border=ft.Border(top=ft.BorderSide(1, p["border"])),
            content=ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Column(spacing=1, expand=True, controls=[
                    ft.Text("Bottom bar", size=13, color=p["text"]),
                    ft.Text("Off gives the height back to the page. The menu "
                            "still reaches everything.", size=10, color=p["text3"]),
                ]),
                ft.Switch(value=prefs.bottom_bar_enabled(), on_change=toggle_bar,
                          active_color=p["accent"]),
            ])))

    def row(key, i, order):
        menu = key in set(prefs.visible_pages())
        bar = key in set(prefs.bottom_bar_pages())
        protected = key in prefs.PROTECTED_PAGES
        # A protected page may leave one surface but not the last one, so
        # the pill that is holding it up is the one that goes dead.
        last_hold = protected and menu != bar

        async def up(e):
            prefs.move_page(key, -1)
            paint()
            await saver.commit("order saved")
            _bump(on_layout)

        async def down(e):
            prefs.move_page(key, +1)
            paint()
            await saver.commit("order saved")
            _bump(on_layout)

        async def toggle_bar(e):
            if not prefs.set_in_bottom_bar(key, not bar):
                if bar:
                    saver.note("Settings has to stay reachable - put it in "
                               "the menu first", bad=True)
                else:
                    saver.note(f"the bottom bar holds {prefs.MAX_BOTTOM} - "
                               f"take one out first", bad=True)
                page.update()
                return
            paint()
            await saver.commit("bottom bar saved")
            _bump(on_layout)

        async def toggle_menu(e):
            if not prefs.set_page_in_menu(key, not menu):
                saver.note("Settings has to stay reachable - put it on the "
                           "bottom bar first", bad=True)
                page.update()
                return
            paint()
            await saver.commit("menu saved")
            _bump(on_layout)

        return ft.Container(
            padding=PAD(2, 0),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])) if i < len(order) - 1 else None,
            content=ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                *_arrows(i == 0, i == len(order) - 1, up, down),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(prefs.PAGE_LABELS.get(key, key), size=13,
                            color=p["text"] if (menu or bar) else p["text3"]),
                    ft.Text(_where(menu, bar), size=10, color=p["text3"]),
                ]),
                _surface_toggle(
                    p, "Menu", menu,
                    "Settings has to stay reachable from somewhere" if (last_hold and menu)
                    else ("Take this page out of the menu" if menu
                          else "List this page in the menu"),
                    toggle_menu, disabled=last_hold and menu),
                _surface_toggle(
                    p, "Bar", bar,
                    "Settings has to stay reachable from somewhere" if (last_hold and bar)
                    else ("Take this page off the bottom bar" if bar
                          else "Put this page on the bottom bar"),
                    toggle_bar, disabled=last_hold and bar),
            ]))

    async def reset(e):
        prefs.reset_layout()
        paint()
        await saver.commit("layout reset")
        _bump(on_layout)

    paint()
    return card(ft.Column(spacing=8, controls=[
        ft.Row(controls=[title("Pages & navigation", 15), ft.Container(expand=True),
                         ft.TextButton("Reset", on_click=reset)]),
        sub("Arrows reorder. Menu and Bar are separate: a page can be in the "
            "menu, on the bottom bar, in both, or in neither. Turning one off "
            "leaves the other exactly as it is.", 10),
        rows,
    ]))


def panels_card(page, saver, on_layout=None):
    """Reorder, hide and resize the cards on any surface.

    Replaces the Home-only editor. "Surface" is one scrollable stack of
    cards - the Home page, the Weather tab, each of FoodZen's tabs - so a
    page's tabs are edited separately, which is the only way tabs and
    panels can both be arranged.

    Width and height: nothing in the app could be resized before either,
    and both are now genuinely custom - a slider over every column or
    cell, not a menu of a few presets ("are you saying i can't just set a
    custom length?" - she could not, and now she can). Width applies from
    tablet width up; on a phone every card is full width whatever the
    slider is set to, because a narrow card at 400px cannot be read.
    Height has no such floor - a short card scrolls its own content on
    any screen, phone included, so it applies everywhere.
    """
    p = PAL()
    surfaces = list(panels.SURFACES)
    chosen = [surfaces[0]]
    rows = ft.Column(spacing=0)

    def paint():
        surface = chosen[0]
        order = prefs.panel_order(surface)
        labels = panels.panel_labels(surface)
        hidden = set(prefs.hidden_panels(surface))
        rows.controls = [row(surface, k, i, order, labels, hidden)
                         for i, k in enumerate(order)]

    def row(surface, key, i, order, labels, hidden_set):
        hidden = key in hidden_set
        width = prefs.panel_width(surface, key)
        height = prefs.panel_height(surface, key)

        async def up(e):
            prefs.move_panel(surface, key, -1)
            paint()
            await saver.commit("order saved")
            if on_layout:
                on_layout()

        async def down(e):
            prefs.move_panel(surface, key, +1)
            paint()
            await saver.commit("order saved")
            if on_layout:
                on_layout()

        async def toggle(e):
            prefs.set_panel_hidden(surface, key, not hidden)
            paint()
            await saver.commit("saved")
            if on_layout:
                on_layout()

        # Live labels, updated straight from the slider's own on_change so
        # dragging feels continuous - `paint()` rebuilds every row on this
        # surface, which is the wrong amount of work to repeat on every
        # frame of a drag; the actual save only happens once, on release.
        width_text = ft.Text(panels.width_label(width), size=10, color=p["text3"])
        height_text = ft.Text(panels.height_label(height), size=10, color=p["text3"])

        def on_width_drag(e):
            width_text.value = panels.width_label(round(e.control.value))
            width_text.update()

        async def on_width_settle(e):
            # NOT snapped. This used to call panels.snap_width(), which
            # pulled the value to the nearest common fraction within two
            # columns - so dragging to 22 and letting go silently became
            # 20. With stops at 60/45/40/30/20/15/12/10 that captured a
            # large part of the range, and her verdict was exact: "i still
            # can't customize the sizing its like 'your way' or nothing".
            # The tap-to-preset shortcut on the edit-mode grip is the fast
            # path to a tidy fraction; the slider now means what it says.
            span = round(e.control.value)
            prefs.set_panel_width(surface, key, span)
            e.control.value = span
            width_text.value = panels.width_label(span)
            e.control.update()
            width_text.update()
            await saver.commit(f"{labels.get(key, key)} width set to {panels.width_label(span)}")
            if on_layout:
                on_layout()

        def on_height_drag(e):
            height_text.value = panels.height_label(round(e.control.value))
            height_text.update()

        async def on_height_settle(e):
            cells = round(e.control.value)
            prefs.set_panel_height(surface, key, cells)
            height_text.value = panels.height_label(cells)
            height_text.update()
            await saver.commit(f"{labels.get(key, key)} height set to {panels.height_label(cells)}")
            if on_layout:
                on_layout()

        width_slider = ft.Slider(
            min=panels.MIN_WIDTH, max=panels.MAX_WIDTH, value=width,
            divisions=panels.MAX_WIDTH - panels.MIN_WIDTH,
            active_color=p["accent"], expand=True,
            on_change=on_width_drag, on_change_end=on_width_settle)

        height_slider = ft.Slider(
            min=panels.MIN_HEIGHT, max=panels.MAX_HEIGHT, value=height,
            divisions=panels.MAX_HEIGHT - panels.MIN_HEIGHT,
            active_color=p["accent"], expand=True,
            on_change=on_height_drag, on_change_end=on_height_settle)

        return ft.Container(
            padding=PAD(2, 8),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])) if i < len(order) - 1 else None,
            content=ft.Column(spacing=2, controls=[
                ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                    *_arrows(i == 0, i == len(order) - 1, up, down),
                    ft.Container(expand=True, content=ft.Text(
                        labels.get(key, key), size=13,
                        color=p["text3"] if hidden else p["text"])),
                    _icon_toggle(ft.Icons.VISIBILITY_OFF if hidden else ft.Icons.VISIBILITY,
                                 not hidden, "Show this card" if hidden else "Hide this card",
                                 toggle),
                ]),
                ft.Row(spacing=6, controls=[
                    ft.Text("Width", size=9, color=p["text3"]),
                    ft.Container(expand=True), width_text]),
                width_slider,
                # Repeated here, not only in the card's own sub() note above
                # the list: she asked directly whether a custom width was
                # even possible, so the one screen-size caveat it has gets
                # said again right next to the control that sets it.
                sub("Tablet and desktop only - always full width on a phone.", 9),
                ft.Row(spacing=6, controls=[
                    ft.Text("Height", size=9, color=p["text3"]),
                    ft.Container(expand=True), height_text]),
                height_slider,
                sub("Applies on every screen size, phone included.", 9),
            ]))

    def pick_surface(e):
        value = getattr(e.control, "value", None) or getattr(e, "data", None)
        if value in panels.SURFACES:
            chosen[0] = value
            paint()
            page.update()

    async def reset(e):
        prefs.reset_panels(chosen[0])
        paint()
        await saver.commit("layout reset")
        if on_layout:
            on_layout()

    paint()
    return card(ft.Column(spacing=8, controls=[
        ft.Row([title("Card layout", 15), ft.Container(expand=True),
                ft.TextButton("Reset this page", on_click=reset)]),
        sub("Reorder, hide and resize the cards on each screen. Widths apply "
            "on a tablet or desktop; a phone always stacks full width.", 10),
        _dropdown(p, surfaces[0],
                  [(k, panels.SURFACE_LABELS.get(k, k)) for k in surfaces],
                  pick_surface, width=260),
        rows,
    ]))


def _dropdown(p, value, options, on_select, width=230, label=None):
    """A Dropdown wired to 0.86's `on_select` - there is no `on_change` here."""
    return ft.Dropdown(
        value=value, options=[ft.dropdown.Option(k, t) for k, t in options],
        on_select=on_select, dense=True, filled=True, text_size=12, width=width,
        label=label, border_color=p["border"], color=p["text"],
        label_style=ft.TextStyle(color=p["text2"], size=11))


def _picked(e, valid, fallback):
    """The option the owner chose, however this Flet build reports it.

    The selection arrives either as a property patch on the control or as the
    event payload depending on the control and the platform, so both are
    checked and anything unrecognised leaves the setting alone. Guessing
    wrong here would silently reset a preference.
    """
    for candidate in (getattr(getattr(e, "control", None), "value", None),
                      getattr(e, "data", None)):
        if isinstance(candidate, str) and candidate in valid:
            return candidate
    return fallback


def _switch_row(p, label, why, value, on_change):
    """One labelled switch, shaped like the bottom-bar row in layout_card."""
    return ft.Row(vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Column(spacing=1, expand=True, controls=[
            ft.Text(label, size=13, color=p["text"]),
            ft.Text(why, size=10, color=p["text3"]),
        ]),
        ft.Switch(value=value, on_change=on_change, active_color=p["accent"]),
    ])


def appearance_card(page, saver, on_theme=None):
    """Corner radius, accent glow and the CRT overlay.

    All three are drawing settings the app previously had no opinion you
    could change: 87 hard-coded corner radii, no glow at all, and no
    overlay. They live together because they are one question - what does
    this thing look like - and apart from the theme swatches because those
    are a palette and these are a shape and two effects.

    `on_theme` is main.py's whole-shell repaint. Every one of these three
    reaches the chrome - the brand pill, the nav bar, the overlay above
    everything - so repainting only this page would leave the app half
    changed until you navigated away and back.
    """
    p = PAL()

    def repaint():
        if on_theme:
            try:
                on_theme()
            except Exception:  # noqa: BLE001 - a repaint must not lose the edit
                pass

    async def pick_radius(e):
        valid = {str(n) for n in RADIUS_CHOICES}
        prefs.set_corner_radius(_picked(e, valid, str(prefs.corner_radius())))
        await saver.commit(f"corners set to {prefs.corner_radius()}px")
        repaint()

    async def toggle_glow(e):
        prefs.set_accent_glow(e.control.value)
        await saver.commit("accent glow " + ("on" if e.control.value else "off"))
        repaint()

    # Live while dragging, saved once the thumb is released - the same split
    # trips.py's scrub bar and wat2do's pain/spoons sliders use, so dragging
    # through 0-100 does not write to storage on every intermediate tick.
    intensity_text = ft.Text(f"{prefs.crt_intensity()}%", size=11, color=p["text2"])
    intensity_slider = ft.Slider(
        min=0, max=100, divisions=20, value=prefs.crt_intensity(),
        active_color=p["accent"], disabled=not prefs.crt_overlay(),
        label="{value}%")

    def preview_intensity(e):
        intensity_text.value = f"{round(e.control.value)}%"
        page.update()

    async def commit_intensity(e):
        prefs.set_crt_intensity(round(e.control.value))
        await saver.commit(f"CRT intensity set to {prefs.crt_intensity()}%")
        repaint()

    intensity_slider.on_change = preview_intensity
    intensity_slider.on_change_end = commit_intensity

    async def toggle_crt(e):
        prefs.set_crt_overlay(e.control.value)
        intensity_slider.disabled = not e.control.value
        await saver.commit("CRT overlay " + ("on" if e.control.value else "off"))
        repaint()
        page.update()

    return card(ft.Column(spacing=10, controls=[
        title("Appearance", 15),
        sub(f"Theme: {THEME_NAMES[S.theme]}", 11),
        _dropdown(p, str(prefs.corner_radius()),
                  [(str(n), RADIUS_LABELS.get(n, f"{n}px")) for n in RADIUS_CHOICES],
                  pick_radius, width=260, label="Corner rounding"),
        sub("Applies to every rounded surface - cards, chips, buttons, fields, "
            "dialogs, the brand pill and the nav bar. Round dots stay round; "
            "they are circles, not corners.", 10),
        _switch_row(p, "Accent glow",
                    "An accent-coloured halo on headings, the brand pill, the "
                    "page you are on and focused fields. Follows the theme.",
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


def units_card(page, saver):
    p = PAL()

    async def pick(e):
        prefs.set_unit_system(_picked(e, set(prefs.UNIT_SYSTEMS), prefs.unit_system()))
        await saver.commit("units saved")

    return card(ft.Column(spacing=8, controls=[
        title("Units", 15),
        _dropdown(p, prefs.unit_system(),
                  [("imperial", "Imperial - miles, °F"), ("metric", "Metric - km, °C")],
                  pick, label="Measure things in"),
        sub("Applies to distances on Trips and temperatures on Weather.", 10),
    ]))


def health_card(page, saver):
    p = PAL()
    valid = {str(n) for n in prefs.SODIUM_TARGETS}

    async def pick(e):
        prefs.set_sodium_target(_picked(e, valid, str(prefs.sodium_target())))
        await saver.commit("sodium target saved")

    return card(ft.Column(spacing=8, controls=[
        title("EatZen", 15),
        _dropdown(p, str(prefs.sodium_target()),
                  [("1500", "1500 mg - strict DASH"), ("2300", "2300 mg - DASH")],
                  pick, label="Daily sodium target"),
        sub("The ceiling EatZen starts each day from. Changing it here does not "
            "rewrite days already logged.", 10),
    ]))


def data_card(page, saver):
    p = PAL()
    labels = {0: "Only when I ask", 15: "Every 15 seconds", 30: "Every 30 seconds",
              60: "Every minute", 300: "Every 5 minutes", 900: "Every 15 minutes"}
    valid = {str(n) for n in prefs.REFRESH_CHOICES}

    async def pick(e):
        prefs.set_refresh_seconds(_picked(e, valid, str(prefs.refresh_seconds())))
        api.refresh()          # the old cache was filled under the old interval
        await saver.commit("refresh interval saved")

    return card(ft.Column(spacing=8, controls=[
        title("Data", 15),
        _dropdown(p, str(prefs.refresh_seconds()),
                  [(str(n), labels[n]) for n in prefs.REFRESH_CHOICES],
                  pick, label="Refresh from the server"),
        sub("How long an answer from the server is reused before the app asks "
            "again. Longer saves battery on cellular.", 10),
    ]))


def _diag_row(p, label, value, ok=None):
    """One fact, with its own colour. `ok=None` means it is neither good nor bad."""
    color = p["text2"] if ok is None else (p["green"] if ok else p["yellow"])
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
        ft.Container(width=104, content=ft.Text(label, size=11, color=p["text3"])),
        ft.Text(value, size=11, color=color, selectable=True, expand=True),
    ])


def diagnostics_card(page):
    """The facts you would otherwise need a log file to find.

    Every lookup is guarded: Diagnostics exists to explain a broken app, so it
    is the last place that may itself break. A probe that raises reports the
    exception instead of taking the page down with it.
    """
    p = PAL()

    def safe(fn, default="unavailable"):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - show the fault, do not hide it
            return f"{default} ({type(exc).__name__})"

    st = config.STATUS
    live = safe(api.STATUS_LIVE, False)
    live_ok = live is True

    def maps():
        path = api.mbtiles_path()
        return path or "none installed - the map uses online tiles"

    def tiles():
        from .. import tileserver
        s = tileserver.status()
        if not s.get("running"):
            return "not running"
        return f"running on 127.0.0.1:{s.get('port')} ({s.get('mbtiles') or 'no archive'})"

    map_path = safe(maps)
    rows = [
        _diag_row(p, "Server", st.get("url") or config.SERVER_URL),
        _diag_row(p, "Data", ("live data - " if live_ok else "sample data - ")
                  + str(st.get("detail", "not checked yet")), live_ok),
        _diag_row(p, "App version", prefs.app_version()),
        _diag_row(p, "Offline map", map_path, map_path.startswith("/")),
        _diag_row(p, "Tile server", safe(tiles)),
        _diag_row(p, "Storage", "SharedPreferences ready" if S.prefs is not None
                  else "unavailable - settings will reset on restart",
                  S.prefs is not None),
        _diag_row(p, "Theme", THEME_NAMES.get(S.theme, S.theme)),
    ]
    return card(ft.Column(spacing=8, controls=[
        ft.Row(controls=[
            title("Diagnostics", 15), ft.Container(expand=True),
            chip("live" if live_ok else "sample", p["green"] if live_ok else p["yellow"],
                 small=True)]),
        *rows,
    ]))


def _bump(on_layout):
    """Tell the caller the navigation changed, if it asked to be told.

    Left to the caller rather than wired to S.rerender here: repainting the
    whole page on every arrow tap scrolls Settings back to the top, which is
    a worse trade than a bottom bar that catches up on the next navigation.
    Whoever teaches main.py to read prefs decides.
    """
    # The parameter is on_layout; this said `callback`, which is not a name
    # in any enclosing scope. Python only resolves it when the line runs, so
    # it imported fine, passed every test that did not toggle a bottom-bar
    # row, and crashed the whole app the first time she hid one.
    if on_layout:
        try:
            on_layout()
        except Exception:  # noqa: BLE001 - a repaint must not lose the edit
            pass


def build(page, on_theme=None, on_layout=None):
    """Build the Settings page.

    `on_theme` repaints after a theme change (main.py already passes it).
    `on_layout` is optional and called after any page/bottom-bar edit, for
    once main.py consumes prefs.visible_pages()/bottom_bar_pages().
    """
    p = PAL()

    # Startup should have loaded these already; this covers the case where it
    # has not, and repaints once the values arrive.
    prefs.hydrate(page, S.prefs, on_done=getattr(S, "rerender", None))

    saver = _Saver(page)

    def swatch(key):
        t = THEMES[key]
        active = S.theme == key
        def pick(e):
            apply_theme(page, key)
            if on_theme:
                on_theme()
        return ft.Container(
            width=72, border_radius=RAD(10), padding=PAD(6, 4),
            bgcolor=t["surface"], border=BRD(t["accent"], 2 if active else 1),
            on_click=pick,
            content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Row(spacing=3, alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["accent"]),
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["green"]),
                    ft.Container(width=12, height=12, border_radius=6, bgcolor=t["pink"]),
                ]),
                ft.Text(THEME_NAMES[key], size=9, color=t["text"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER),
            ]),
            shadow=ft.BoxShadow(blur_radius=10, spread_radius=1,
                                color=ft.Colors.with_opacity(0.5, t["accent"])) if active else None,
        )

    def group(label, keys):
        return ft.Column(spacing=8, controls=[
            sub(label.upper(), 10),
            ft.Row(spacing=8, wrap=True, run_spacing=8, controls=[swatch(k) for k in keys]),
        ])

    keys = list(THEME_NAMES)
    return ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, controls=[
        server_card(page),
        appearance_card(page, saver, on_theme=on_theme),
        group("Themes", keys),
        saver.control,
        layout_card(page, saver, on_layout=on_layout),
        panels_card(page, saver, on_layout=on_layout),
        units_card(page, saver),
        health_card(page, saver),
        data_card(page, saver),
        diagnostics_card(page),
        card(ft.Column(spacing=6, controls=[
            title("System", 14),
            sub("Layout, panels, units and targets are stored on this device only. "
                "The machine editor and syncing a layout with the website still "
                "need API wiring.", 11),
        ])),
    ])
