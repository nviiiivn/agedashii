import flet as ft
from flet_app.ui import PAL, PAD, BRD, S, apply_theme, dot
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
        p = PAL()
        return ft.Row(spacing=6, controls=[
            ft.Container(width=8, height=8, border_radius=4, bgcolor=m["color"] if m["online"] else p["border"])
            for m in M.MACHINES])

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

    def nav_drawer():
        p = PAL()
        # Rebuilt on every render: the drawer is themed, and one built once at
        # startup would keep the old palette after a theme change.
        pages = _drawer_pages()
        here = pages.index(current[0]) if current[0] in pages else 0
        return ft.NavigationDrawer(
            bgcolor=p["bg2"], selected_index=here,
            controls=[
                ft.Container(height=12),
                *[ft.NavigationDrawerDestination(icon=ICONS[k], label=LABELS[k])
                  for k in pages],
            ],
            on_change=drawer_pick,
        )

    async def open_menu(e):
        await page.show_drawer()

    def cycle_theme(e):
        """Step to the next theme and repaint where you are.

        THEME_NAMES is the ordered list the Settings grid uses, so the button
        and the grid walk the same themes in the same order.
        """
        keys = list(THEME_NAMES)
        i = keys.index(S.theme) if S.theme in keys else -1
        apply_theme(page, keys[(i + 1) % len(keys)])
        render(current[0])

    def topbar():
        p = PAL()
        return ft.Row(spacing=10, controls=[
            ft.IconButton(ft.Icons.MENU, icon_color=p["text2"], on_click=open_menu),
            brand_chip(),
            ft.Container(expand=True),
            machine_dots(),
            # There was an ft.Icon(SEARCH) here: a picture of a button that
            # could not be clicked and searched nothing. Removed rather than
            # faked - the drawer now lists every page, which is the jump-to-a-
            # page affordance the icon pretended to offer.
            ft.IconButton(ft.Icons.PALETTE, icon_color=p["text2"],
                          tooltip=f"Theme: {THEME_NAMES.get(S.theme, S.theme)} — tap for the next",
                          on_click=cycle_theme),
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
        page.add(ft.Column(spacing=0, expand=True, controls=[
            ft.Container(padding=PAD(8, 12), bgcolor=p["bg"], content=topbar()),
            body,
            bottom_bar(),
        ]))
        page.update()

    # A page that writes to the server needs to re-read and repaint after the
    # write lands. _forms.rebuilder does that by replacing page.controls,
    # which drops the topbar and the nav bar with it; parking the real
    # repaint here lets a page refresh itself without losing the chrome.
    # Parked on S for the same reason S.prefs is: it must stay referenced.
    S.rerender = lambda: render(current[0])

    render("home")

# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call. view=None keeps this headless (serves over HTTP) rather than trying to
# open a desktop window - this host is a headless Pi. For a desktop window use
# view=ft.AppView.FLET_APP, which is what `flet build linux/macos` produces.
ft.run(main, view=None, port=8550)
