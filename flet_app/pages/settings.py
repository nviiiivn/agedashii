"""Settings: the app's answer to the website's GridStack layout editor.

The website lets you drag panels around; the app could only pick a theme.
What is here now is the phone-shaped equivalent - reorder and hide the pages,
choose which five ride in the bottom bar, reorder and hide the panels on
Home - plus the settings that were only ever hard-coded constants (units, the
DASH sodium ceiling, how long cached data stays good), and a Diagnostics card
that answers "why is it showing sample data" without a log file.

Reordering is done with arrows rather than drag-and-drop on purpose: these
lists live inside a scrolling column, and a long-press drag inside a scroll
view is ambiguous on a touch screen - the gesture that reorders a row is the
same one that scrolls past it.

Everything is stored by flet_app/prefs.py, which also owns the read API the
rest of the app consumes. This page only edits; it never decides what a
preference means.
"""
import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, S, apply_theme
from ..theme_data import THEMES, THEME_NAMES
from .. import api, config, prefs


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


def layout_card(page, saver, on_layout=None):
    """Reorder the nine pages, hide the unused ones, pick the bottom five."""
    p = PAL()
    rows = ft.Column(spacing=0)

    def paint():
        order = prefs.page_order()
        hidden = set(prefs.hidden_pages())
        in_bar = set(prefs.bottom_bar_pages())
        rows.controls = [row(k, i, order) for i, k in enumerate(order)]
        count = ft.Text(
            f"{len(in_bar)} of {prefs.MAX_BOTTOM} bottom-bar slots used - "
            f"{len(order) - len(hidden)} pages visible",
            size=10, color=p["text3"])
        rows.controls.append(ft.Container(padding=PAD(6, 8), content=count))

    def row(key, i, order):
        hidden = key in set(prefs.hidden_pages())
        bar = key in set(prefs.bottom_bar_pages())
        protected = key in prefs.PROTECTED_PAGES

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
                saver.note(f"the bottom bar holds {prefs.MAX_BOTTOM} - "
                           f"take one out first", bad=True)
                page.update()
                return
            paint()
            await saver.commit("bottom bar saved")
            _bump(on_layout)

        async def toggle_hide(e):
            prefs.set_page_hidden(key, not hidden)
            paint()
            await saver.commit("saved")
            _bump(on_layout)

        where = "bottom bar" if bar else "drawer only"
        return ft.Container(
            padding=PAD(2, 0),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])) if i < len(order) - 1 else None,
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                *_arrows(i == 0, i == len(order) - 1, up, down),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(prefs.PAGE_LABELS.get(key, key), size=13,
                            color=p["text3"] if hidden else p["text"]),
                    ft.Text("hidden" if hidden else where, size=10, color=p["text3"]),
                ]),
                _icon_toggle(ft.Icons.VERTICAL_ALIGN_BOTTOM, bar,
                             "Remove from the bottom bar" if bar else "Put in the bottom bar",
                             toggle_bar, disabled=hidden),
                _icon_toggle(ft.Icons.VISIBILITY_OFF if hidden else ft.Icons.VISIBILITY,
                             not hidden,
                             "Settings cannot be hidden" if protected else
                             ("Show this page" if hidden else "Hide this page"),
                             toggle_hide, disabled=protected),
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
        sub("Arrows reorder. The tray icon puts a page in the bottom bar; the eye "
            "hides it everywhere. Hidden pages leave the bar automatically.", 10),
        rows,
    ]))


def home_card(page, saver):
    """Show, hide and reorder the panels on the Home screen."""
    p = PAL()
    rows = ft.Column(spacing=0)

    def paint():
        order = prefs.home_panel_order()
        rows.controls = [row(k, i, order) for i, k in enumerate(order)]

    def row(key, i, order):
        hidden = key in set(prefs.hidden_home_panels())

        async def up(e):
            prefs.move_home_panel(key, -1)
            paint()
            await saver.commit("order saved")

        async def down(e):
            prefs.move_home_panel(key, +1)
            paint()
            await saver.commit("order saved")

        async def toggle(e):
            prefs.set_home_panel_hidden(key, not hidden)
            paint()
            await saver.commit("saved")

        return ft.Container(
            padding=PAD(2, 0),
            border=ft.Border(bottom=ft.BorderSide(1, p["border"])) if i < len(order) - 1 else None,
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                *_arrows(i == 0, i == len(order) - 1, up, down),
                ft.Container(expand=True, content=ft.Text(
                    prefs.HOME_PANEL_LABELS.get(key, key), size=13,
                    color=p["text3"] if hidden else p["text"])),
                _icon_toggle(ft.Icons.VISIBILITY_OFF if hidden else ft.Icons.VISIBILITY,
                             not hidden, "Show this panel" if hidden else "Hide this panel",
                             toggle),
            ]))

    paint()
    return card(ft.Column(spacing=8, controls=[
        title("Home panels", 15),
        sub("What the Home screen stacks, top to bottom.", 10),
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
    if callback:
        try:
            callback()
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
            width=72, border_radius=10, padding=PAD(6, 4),
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
        card(ft.Column(spacing=4, controls=[
            title("Appearance", 15),
            sub(f"Theme: {THEME_NAMES[S.theme]}", 11),
        ])),
        group("Themes", keys),
        saver.control,
        layout_card(page, saver, on_layout=on_layout),
        home_card(page, saver),
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
