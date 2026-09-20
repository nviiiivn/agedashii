import flet as ft
from flet_app.ui import PAL, PAD, BRD, S, apply_theme, dot
from flet_app.theme_data import THEMES
from flet_app.pages import home, lab, tools, trips, weather, garage, wat2do, extras, settings
from flet_app import api as M
from flet_app import config

ORANGE, PINK, CYAN = "#ffb020", "#ff5e8a", "#39d0ff"
MAIN = ["home", "lab", "tools", "garage", "wat2do"]
DRAWER = ["trips", "weather", "extras", "settings"]
LABELS = {"home": "Home", "lab": "Lab", "tools": "Tools", "garage": "Garage", "wat2do": "Wat2Do",
          "trips": "Trips", "weather": "Weather", "extras": "Extras", "settings": "Settings"}
ICONS = {"home": ft.Icons.HOME, "lab": ft.Icons.DNS, "tools": ft.Icons.CONSTRUCTION,
         "garage": ft.Icons.TWO_WHEELER, "wat2do": ft.Icons.CHECK_CIRCLE, "trips": ft.Icons.MAP,
         "weather": ft.Icons.CLOUD, "extras": ft.Icons.WIDGETS, "settings": ft.Icons.SETTINGS}

async def main(page):
    apply_theme(page)

    # Persistent storage is a Flet *service*: constructing it inside the page
    # context registers it, and it is unregistered again the moment nothing
    # holds a strong reference - hence parking it on S rather than a local.
    S.prefs = ft.SharedPreferences()
    # Restore the saved server address and confirm it still answers before the
    # first screen paints, so pages do not flash sample data then swap.
    await config.load(S.prefs)

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

    async def drawer_pick(e):
        idx = e.control.selected_index
        if idx is not None:
            render(DRAWER[idx])
            await page.close_drawer()

    def nav_drawer():
        p = PAL()
        return ft.NavigationDrawer(
            bgcolor=p["bg2"],
            controls=[
                ft.Container(height=12),
                *[ft.NavigationDrawerDestination(icon=ICONS[k], label=LABELS[k]) for k in DRAWER],
            ],
            on_change=drawer_pick,
        )

    async def open_menu(e):
        await page.show_drawer()

    page.drawer = nav_drawer()

    def topbar():
        p = PAL()
        return ft.Row(spacing=10, controls=[
            ft.IconButton(ft.Icons.MENU, icon_color=p["text2"], on_click=open_menu),
            brand_chip(),
            ft.Container(expand=True),
            machine_dots(),
            ft.Icon(ft.Icons.SEARCH, color=p["text2"], size=18),
            ft.IconButton(ft.Icons.SETTINGS, icon_color=p["text2"],
                          on_click=lambda e: render("settings")),
        ])

    def bottom_bar():
        return ft.NavigationBar(
            selected_index=0, destinations=[
                ft.NavigationBarDestination(icon=ICONS[k], label=LABELS[k]) for k in MAIN],
            on_change=lambda e: render(MAIN[e.control.selected_index]),
        )

    def render(key):
        p = PAL()
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
            c = settings.build(page, on_theme=lambda: render("settings"))
        body.content = c
        body.bgcolor = p["bg"]
        page.controls.clear()
        page.add(ft.Column(spacing=0, expand=True, controls=[
            ft.Container(padding=PAD(8, 12), bgcolor=p["bg"], content=topbar()),
            body,
            bottom_bar(),
        ]))
        page.update()

    render("home")

# ft.app() is deprecated since 0.80 and removed in 0.90; ft.run() is the same
# call. view=None keeps this headless (serves over HTTP) rather than trying to
# open a desktop window - this host is a headless Pi. For a desktop window use
# view=ft.AppView.FLET_APP, which is what `flet build linux/macos` produces.
ft.run(main, view=None, port=8550)
