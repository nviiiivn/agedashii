"""Home - the overview the website opens on, on the phone.

The browser's Overview (dashbored-web/app.js: `loadOverview`, `loadWeather`,
`renderNowPlaying`, `renderMachinePowerButtons`, `refreshAll`) shows four
stat tiles, the weather with a three-day forecast, what is playing, a row
per machine with its CPU / RAM / disk, eight quick links and a quick-actions
card that can wake, shut down and reboot each machine.

What was here instead: a temperature, five status dots, and four labelled
tiles - Wake, Ride, Wat2Do, List - with no handlers on any of them. They
looked like buttons for as long as the page has existed and did nothing when
tapped, which is worse than not being there: it reads as the app being
broken rather than as a feature not being built.

Two claims have also been removed rather than reproduced:

* **"Ride weather good"** was a hardcoded string. It said the same thing in
  a downpour. The weather callout is now computed from the actual weather
  code and wind speed, the way Wat2Do's already is.
* **"0% rain"** came from `weather()['now']['rain']`, which reads
  `hourly.precipitation_probability[0]`. This server sends no `hourly` block
  at all, so that list is always empty and the chip always said 0% - a
  measurement nobody took. Humidity, which the server does send, is shown
  instead.

Order, visibility and width all come from Settings through `panels`.
"""
import asyncio
import datetime

import flet as ft

from .. import api as M
from .. import panels
from ..ui import PAL, PAD, BRD, S, card, chip, dot, sub, title

# The eight links the website's Quick Links card carries, verbatim. They are
# hardcoded there too - there is no endpoint that lists them - but they are
# real addresses that really open, which is the difference between this and
# a decorative tile.
QUICK_LINKS = (
    ("AI chat", "Open WebUI", "https://ai.alola.lol/", ft.Icons.SMART_TOY),
    ("Image / video", "ComfyUI", "https://comfy.alola.lol/", ft.Icons.IMAGE),
    ("IDE", "VS Code", "https://code.alola.lol/?folder=/home/nvii",
     ft.Icons.CODE),
    ("Music", "Navidrome", "https://navi.alola.lol/app/#/login",
     ft.Icons.LIBRARY_MUSIC),
    ("Movies / shows", "Jellyfin", "https://jelly.alola.lol/web/#/home",
     ft.Icons.MOVIE),
    ("totemDocs", "Docs", "https://totem.alola.lol/", ft.Icons.MENU_BOOK),
    ("Site builder", "Drag and drop", "https://build.alola.lol",
     ft.Icons.WEB),
    ("Gallery", "ComfyUI output", "https://stuff.alola.lol/",
     ft.Icons.PHOTO_LIBRARY),
)

# Open-Meteo WMO codes -> the icon and words the website's WX_DESC uses.
WX_DESC = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Heavy thunderstorm",
}
WX_ICON = {
    0: ft.Icons.WB_SUNNY, 1: ft.Icons.WB_SUNNY, 2: ft.Icons.WB_CLOUDY,
    3: ft.Icons.CLOUD, 45: ft.Icons.FOGGY, 48: ft.Icons.FOGGY,
    51: ft.Icons.GRAIN, 53: ft.Icons.GRAIN, 55: ft.Icons.GRAIN,
    61: ft.Icons.WATER_DROP, 63: ft.Icons.WATER_DROP, 65: ft.Icons.WATER_DROP,
    71: ft.Icons.AC_UNIT, 73: ft.Icons.AC_UNIT, 75: ft.Icons.AC_UNIT,
    80: ft.Icons.GRAIN, 81: ft.Icons.WATER_DROP, 82: ft.Icons.THUNDERSTORM,
    95: ft.Icons.THUNDERSTORM, 96: ft.Icons.THUNDERSTORM,
    99: ft.Icons.THUNDERSTORM,
}


def wx_words(code):
    return WX_DESC.get(code, "—")


def wx_icon(code):
    return WX_ICON.get(code, ft.Icons.THERMOSTAT)


def ride_call(cur):
    """(sentence, tone) about going out, from the actual weather.

    The same rule Wat2Do uses, and the same rule the website's w2d callout
    uses: wet is code 51 and up, windy is over 20 mph. The old chip on this
    card said "Ride weather good" unconditionally, in every condition, since
    the day it was written.
    """
    if not cur:
        return "No weather reading", "unknown"
    try:
        code = int(cur.get("weather_code") or 0)
    except (TypeError, ValueError):
        code = 0
    try:
        wind = float(cur.get("wind_speed_10m") or 0)
    except (TypeError, ValueError):
        wind = 0.0
    if code >= 51:
        return "Wet - indoor day", "bad"
    if wind > 20:
        return "Windy - bundled ride only", "meh"
    return "Clear-ish - good day to ride", "good"


def _goto(key, tab_path=()):
    """Navigate to another page, if the app shell is there to do it.

    main.py parks a `goto` on the shared `S` for exactly this. Standalone
    builds and the tests have no shell, so the handler is None and the tile
    is not drawn at all rather than drawn dead - which is the whole bug this
    card is being rewritten for.
    """
    jump = getattr(S, "goto", None)
    if not callable(jump):
        return None
    return lambda e: jump(key, tab_path)


def build(page):
    p = PAL()
    live = M.STATUS_LIVE()
    ov = M.overview()
    cur = M.weather_current()
    days = M.weather_days()

    status = ft.Text("", size=11, color=p["text2"], selectable=True)

    def say(ok, msg):
        status.value = msg if ok else f"failed - {msg}"
        status.color = p["text2"] if ok else p["pink"]

    def repaint():
        try:
            page.update()
        except Exception:      # noqa: BLE001 - not mounted yet (first paint)
            pass

    def _run(coro_fn, *args):
        runner = getattr(page, "run_task", None)
        if not callable(runner):
            return
        try:
            runner(coro_fn, *args)
        except Exception:      # noqa: BLE001 - no running loop
            pass

    # ------------------------------------------------------------ greeting
    def greeting():
        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return ft.Column(spacing=0, controls=[
            ft.Text(f"Good {part}, nvii", size=22, weight=ft.FontWeight.W_700,
                    color=p["text"]),
            sub(datetime.date.today().strftime("%a, %b %-d") + " · Vulcan Lofts"),
        ])

    # ----------------------------------------------------------- stat strip
    docker_tile = ft.Column(spacing=1)

    async def count_docker():
        """Fetch the container count behind the page and drop it into place."""
        n = await asyncio.to_thread(M.docker_running, True)
        docker_tile.controls[1].value = "—" if n is None else str(n)
        docker_tile.controls[2].value = ("across all hosts" if n is not None
                                         else "docker did not answer")
        repaint()

    def stat_tile(value, label, note, colour=None):
        return ft.Container(
            col={"xs": 6, "md": 3}, padding=PAD(10, 10), border_radius=12,
            bgcolor=p["surface"], border=BRD(p["border"], 1),
            content=ft.Column(spacing=1, controls=[
                ft.Text(label, size=9, color=p["text2"]),
                ft.Text(value, size=22, weight=ft.FontWeight.W_700,
                        color=colour or p["text"]),
                ft.Text(note, size=9, color=p["text3"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ]))

    def stats_card():
        # An em dash everywhere the server has not been heard from. A zero
        # here would be a reading, and "0 machines online" is a very
        # different sentence from "we have not asked yet".
        if ov is None:
            return ft.ResponsiveRow(columns=12, spacing=8, run_spacing=8, controls=[
                stat_tile("—", "Machines online", "no reply from the server"),
                stat_tile("—", "Containers running", "no reply from the server"),
                stat_tile("—", "Services up", "no reply from the server"),
                stat_tile(f"{round(cur['temperature_2m'])}°F" if cur else "—",
                          "Oakland temp",
                          wx_words(cur.get("weather_code")) if cur else "no reading"),
            ])
        containers = ov["containers"]
        # /api/docker_list SSHes to five machines and takes six seconds. It
        # is not allowed to hold up the whole page for one integer, so the
        # tile says it is counting and `count_docker` fills it in.
        docker_tile.controls = [
            ft.Text("Containers running", size=9, color=p["text2"]),
            ft.Text("—" if containers is None else str(containers), size=22,
                    weight=ft.FontWeight.W_700, color=p["text"]),
            ft.Text("across all hosts" if containers is not None
                    else "counting...", size=9, color=p["text3"], max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS)]
        return ft.ResponsiveRow(columns=12, spacing=8, run_spacing=8, controls=[
            stat_tile(str(ov["online"]), "Machines online",
                      f"of {ov['total']} total",
                      p["green"] if ov["online"] == ov["total"] else p["yellow"]),
            ft.Container(col={"xs": 6, "md": 3}, padding=PAD(10, 10),
                         border_radius=12, bgcolor=p["surface"],
                         border=BRD(p["border"], 1), content=docker_tile),
            stat_tile(str(ov["services"]), "Services up", "active units"),
            stat_tile(f"{round(cur['temperature_2m'])}°F" if cur else "—",
                      "Oakland temp",
                      wx_words(cur.get("weather_code")) if cur else "no reading"),
        ])

    # ------------------------------------------------------------- weather
    def weather_card():
        if not cur:
            return card(ft.Column(spacing=6, controls=[
                sub("WEATHER · OAKLAND", 10),
                sub("Weather unavailable - the server has not answered "
                    "/api/weather on this device.", 11)]))
        code = cur.get("weather_code")
        call, tone = ride_call(cur)
        tone_colour = {"good": p["green"], "meh": p["yellow"], "bad": p["red"],
                       "unknown": p["text2"]}[tone]
        tiles = []
        for d in (days or []):
            tiles.append(ft.Container(
                expand=True, padding=PAD(6, 4), border_radius=8, bgcolor=p["bg2"],
                content=ft.Column(
                    spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        sub(d["day"], 10),
                        ft.Icon(wx_icon(d["code"]), size=16, color=p["text2"]),
                        ft.Text(f"{d['hi']}° / {d['lo']}°"
                                if d["hi"] is not None and d["lo"] is not None
                                else "—", size=11, color=p["text"])])))
        return card(ft.Column(spacing=8, controls=[
            sub("WEATHER · OAKLAND", 10),
            ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                       ft.Icon(wx_icon(code), size=32, color=p["yellow"]),
                       ft.Column(spacing=0, expand=True, controls=[
                           ft.Text(f"{round(cur.get('temperature_2m') or 0)}°F",
                                   size=30, weight=ft.FontWeight.W_700,
                                   color=p["text"]),
                           sub(f"{wx_words(code)} · feels "
                               f"{round(cur.get('apparent_temperature') or 0)}°F",
                               11)])]),
            ft.Row(spacing=6, wrap=True, controls=[
                # Humidity and wind are both in the server's `current` block.
                # A rain chance is not, on any request this server answers.
                chip(f"Humidity {cur.get('relative_humidity_2m', '—')}%",
                     p["cyan"], ft.Icons.WATER_DROP, small=True),
                chip(f"Wind {round(cur.get('wind_speed_10m') or 0)} mph",
                     p["accent"], ft.Icons.AIR, small=True),
            ]),
            ft.Row(spacing=6, controls=tiles) if tiles else sub(
                "No forecast in the last reply.", 10),
            ft.Container(padding=PAD(6, 10), border_radius=8,
                         bgcolor=ft.Colors.with_opacity(0.12, tone_colour),
                         border=BRD(ft.Colors.with_opacity(0.35, tone_colour), 1),
                         content=ft.Row(spacing=6, controls=[
                             ft.Icon(ft.Icons.MOTORCYCLE, size=14,
                                     color=tone_colour),
                             ft.Text(call, size=12, color=tone_colour)])),
        ]))

    # --------------------------------------------------------- now playing
    def nowplaying_card():
        n = M.NOWPLAYING
        # Only look like a playing track when something is. This card used to
        # show the same hardcoded song permanently, play icon and all.
        playing = bool(n.get("playing"))
        return card(ft.Row(spacing=12, controls=[
            ft.Container(width=44, height=44, border_radius=8, bgcolor=p["bg2"],
                         border=BRD(p["border"], 1), alignment=ft.Alignment(0, 0),
                         content=ft.Icon(ft.Icons.MUSIC_NOTE if playing
                                         else ft.Icons.MUSIC_OFF,
                                         color=p["pink"] if playing else p["text2"],
                                         size=22)),
            ft.Column(spacing=2, expand=True, controls=[
                ft.Text(n["title"], size=14,
                        weight=ft.FontWeight.W_600 if playing else ft.FontWeight.W_400,
                        color=p["text"] if playing else p["text2"]),
                sub(n["artist"]),
            ]),
            ft.Icon(ft.Icons.PLAY_CIRCLE if playing else ft.Icons.PAUSE_CIRCLE_OUTLINE,
                    color=p["accent"] if playing else p["text3"], size=30),
        ]))

    # ------------------------------------------------------- machine status
    def machines_card():
        if ov is None:
            return card(ft.Column(spacing=8, controls=[
                sub("MACHINE STATUS", 10),
                sub("No reply from the server - nothing is known about any "
                    "machine right now.", 11)]))
        rows = []
        for m in ov["machines"]:
            letter = (m["display"] or m["id"])[:1].upper()
            if m["online"]:
                metrics = ft.Row(spacing=10, controls=[
                    _metric("CPU", m["cpu"], p), _metric("RAM", m["ram"], p),
                    _metric("Disk", m["disk"], p)])
            else:
                # Not three gauges at zero. The machine is not idle, it is
                # unreachable, and those look identical on a bar chart.
                metrics = ft.Text(m["error"] or "Offline", size=10, color=p["red"])
            rows.append(ft.Container(
                padding=PAD(7, 8), border_radius=10, bgcolor=p["bg2"],
                border=BRD(p["border"], 1), ink=bool(_goto("lab", [("lab", 0)])),
                on_click=_goto("lab", [("lab", 0)]),
                content=ft.Row(spacing=10, controls=[
                    ft.Container(width=28, height=28, border_radius=8,
                                 alignment=ft.Alignment(0, 0),
                                 bgcolor=ft.Colors.with_opacity(0.18, m["color"]),
                                 content=ft.Text(letter, size=13,
                                                 weight=ft.FontWeight.W_700,
                                                 color=m["color"])),
                    ft.Column(spacing=1, expand=True, controls=[
                        ft.Row(spacing=6, controls=[
                            dot(m["color"], dim=not m["online"], size=7),
                            ft.Text(m["display"], size=12.5,
                                    weight=ft.FontWeight.W_500,
                                    color=p["text"] if m["online"] else p["text2"])]),
                        metrics]),
                ])))
        return card(ft.Column(spacing=8, controls=[
            ft.Row([sub("MACHINE STATUS", 10), ft.Container(expand=True),
                    sub(f"{ov['online']}/{ov['total']} up", 10)]),
            *rows]))

    # --------------------------------------------------------- quick links
    def links_card():
        rows = []
        for label, note, url, icon in QUICK_LINKS:
            rows.append(ft.Container(
                col={"xs": 12, "md": 6}, padding=PAD(7, 9), border_radius=10,
                bgcolor=p["bg2"], border=BRD(p["border"], 1), ink=True, url=url,
                content=ft.Row(spacing=8, controls=[
                    ft.Icon(icon, size=15, color=p["accent"]),
                    ft.Column(spacing=0, expand=True, controls=[
                        ft.Text(label, size=11.5, color=p["text"], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(note, size=9, color=p["text3"], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS)]),
                    ft.Icon(ft.Icons.OPEN_IN_NEW, size=11, color=p["text3"]),
                ])))
        return card(ft.Column(spacing=8, controls=[
            sub("QUICK LINKS", 10),
            ft.ResponsiveRow(columns=12, spacing=6, run_spacing=6, controls=rows)]))

    # ------------------------------------------------------- quick actions
    async def do_power(mid, action, label):
        status.value = f"{action} -> {label}..."
        status.color = p["text2"]
        repaint()
        ok, msg = await asyncio.to_thread(M.power, mid, action)
        # The server answers {"ok": true} whenever the SSH call returned
        # anything at all, including "WoL tools not installed", so its own
        # words go on screen rather than a tick.
        say(ok, f"{label}: {msg}" if ok else f"{label} {action}: {msg}")
        repaint()

    async def refresh_all():
        status.value = "refreshing..."
        status.color = p["text2"]
        repaint()
        await asyncio.to_thread(M.refresh)
        rerender = getattr(S, "rerender", None)
        if callable(rerender):
            rerender()
            return
        repaint()

    def actions_row():
        """Real controls only.

        This was four tiles - Wake, Ride, Wat2Do, List - with no handler on
        any of them. Every control below either sends something to the
        server or moves the app somewhere; a jump whose destination the shell
        cannot provide is left out rather than drawn dead.
        """
        machine_rows = []
        for m in (ov or {}).get("machines", []):
            machine_rows.append(ft.Row(
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(m["display"], size=11, color=p["text2"], width=64,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.IconButton(ft.Icons.POWER_SETTINGS_NEW, icon_size=15,
                                  icon_color=p["green"], tooltip="Wake on LAN",
                                  on_click=(lambda e, m=m: _run(
                                      do_power, m["id"], "wake", m["display"]))),
                    ft.IconButton(ft.Icons.STOP_CIRCLE, icon_size=15,
                                  icon_color=p["text2"], tooltip="Shut down",
                                  on_click=_confirm(page, p,
                                                    f"Shut down {m['display']}?",
                                                    lambda m=m: _run(
                                                        do_power, m["id"],
                                                        "shutdown", m["display"]))),
                    ft.IconButton(ft.Icons.RESTART_ALT, icon_size=15,
                                  icon_color=p["red"], tooltip="Reboot",
                                  on_click=_confirm(page, p,
                                                    f"Reboot {m['display']}?",
                                                    lambda m=m: _run(
                                                        do_power, m["id"],
                                                        "reboot", m["display"]))),
                ]))

        jumps = [("Docker", ft.Icons.INVENTORY_2, "lab", [("lab", 4)]),
                 ("Qbit", ft.Icons.DOWNLOAD, "lab", [("lab", 3)]),
                 ("Wat2Do", ft.Icons.CHECK_CIRCLE, "wat2do", []),
                 ("Journal", ft.Icons.EDIT_NOTE, "tools", [("tools", 2)])]
        tiles = []
        for label, icon, key, path in jumps:
            handler = _goto(key, path)
            if handler is None:
                continue
            tiles.append(ft.Container(
                col={"xs": 6, "md": 3}, padding=PAD(9, 0), border_radius=10,
                bgcolor=p["bg2"], border=BRD(p["border"], 1), ink=True,
                alignment=ft.Alignment(0, 0), on_click=handler,
                content=ft.Column(
                    spacing=3, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[ft.Icon(icon, size=18, color=p["accent"]),
                              ft.Text(label, size=10.5, color=p["text2"])])))
        tiles.append(ft.Container(
            col={"xs": 6, "md": 3}, padding=PAD(9, 0), border_radius=10,
            bgcolor=p["bg2"], border=BRD(p["border"], 1), ink=True,
            alignment=ft.Alignment(0, 0),
            on_click=lambda e: _run(refresh_all),
            content=ft.Column(
                spacing=3, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[ft.Icon(ft.Icons.REFRESH, size=18, color=p["accent"]),
                          ft.Text("Refresh all", size=10.5, color=p["text2"])])))

        body = [sub("QUICK ACTIONS", 10)]
        if machine_rows:
            body += [ft.Column(spacing=2, controls=machine_rows),
                     ft.Divider(height=1, color=p["border"])]
        elif ov is None:
            body.append(sub("Power controls need the machine list, and the "
                            "server has not answered for it.", 10))
        body.append(ft.ResponsiveRow(columns=12, spacing=6, run_spacing=6,
                                     controls=tiles))
        if not live:
            body.append(sub("Offline - power commands are not queued and will "
                            "say so if you try.", 10))
        return card(ft.Column(spacing=8, controls=body))

    # ------------------------------------------------------ wat2do progress
    done = sum(1 for it in M.WAT2DO["items"] if it.get("done"))
    total = len(M.WAT2DO["items"])

    def wat2do_mini():
        def row(it):
            return ft.Row(spacing=8, controls=[
                ft.Icon(ft.Icons.CHECK_BOX if it.get("done")
                        else ft.Icons.CHECK_BOX_OUTLINE_BLANK, size=16,
                        color=p["green"] if it.get("done") else p["text2"]),
                ft.Text(it["t"], size=12 if not it.get("sub") else 11,
                        color=p["text2"] if it.get("done")
                        else (p["text3"] if it.get("sub") else p["text"]),
                        style=None if not it.get("done") else ft.TextStyle(
                            decoration=ft.TextDecoration.LINE_THROUGH),
                        offset=ft.Offset(0.6, 0) if it.get("sub") else None),
            ])
        head = [title("Wat2Do"), ft.Container(expand=True),
                ft.Text(f"{done}/{total}", size=12, color=p["accent"],
                        weight=ft.FontWeight.W_600)]
        jump = _goto("wat2do")
        inner = ft.Column(spacing=6, controls=[
            ft.Row(head),
            ft.ProgressBar(value=(done / total) if total else 0, color=p["orange"],
                           bgcolor=p["bg2"], bar_height=4, border_radius=2),
            *[row(it) for it in M.WAT2DO["items"][:4]],
        ])
        if jump is None:
            return card(inner)
        return ft.Container(border_radius=12, bgcolor=p["surface"],
                            border=BRD(p["border"], 1), padding=14, ink=True,
                            on_click=jump, content=inner)

    body = panels.layout("home", {
        "greeting": greeting, "stats": stats_card, "weather": weather_card,
        "nowplaying": nowplaying_card, "machines": machines_card,
        "links": links_card, "actions": actions_row, "wat2do": wat2do_mini,
    })
    # Only chase the slow number if the strip that shows it is on screen.
    if ov is not None and ov["containers"] is None and docker_tile.controls:
        _run(count_docker)
    # The status line is chrome, not a panel: a power command that failed
    # must have somewhere to say so even with every card hidden.
    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=body),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


def _metric(label, value, p):
    """One CPU / RAM / disk reading, or an em dash when there is none."""
    colour = p["text2"]
    if value is not None:
        colour = p["red"] if value >= 80 else p["yellow"] if value >= 60 else p["green"]
    return ft.Row(spacing=3, tight=True, controls=[
        ft.Text(label, size=9, color=p["text3"]),
        ft.Text("—" if value is None else f"{value}%", size=10, color=colour,
                weight=ft.FontWeight.W_600)])


def _confirm(page, p, question, on_yes):
    """A two-tap guard, standing in for the browser's confirm().

    Flet 0.86 opens a dialog with page.show_dialog and closes it with
    page.pop_dialog - `open`/`close` are the old names and do nothing here.
    Where the page has neither (tests, standalone), the second tap on the
    button itself is the confirmation, so the control still behaves.
    """
    def handler(e):
        opener = getattr(page, "show_dialog", None)
        closer = getattr(page, "pop_dialog", None)
        if not (callable(opener) and callable(closer)):
            btn = e.control
            if getattr(btn, "data", None) != "armed":
                btn.data = "armed"
                btn.tooltip = f"{question} Tap again."
                try:
                    page.update()
                except Exception:      # noqa: BLE001
                    pass
                return
            on_yes()
            return

        dlg = ft.AlertDialog(
            title=ft.Text(question, size=14, weight=ft.FontWeight.W_600))

        def yes(ev):
            closer()
            on_yes()

        dlg.actions = [ft.TextButton("Cancel", on_click=lambda ev: closer()),
                       ft.FilledButton("Do it", on_click=yes)]
        opener(dlg)
    return handler
