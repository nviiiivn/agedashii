"""Everything she can change, and an honest account of what the app is getting.

Two halves.

The top half is choices, and every one of them is a row of fixed tiles rather
than a dropdown or a slider. Four themes, two bike colours, two unit systems,
four tile styles, four sample rates - all small enough sets to show at once, all
tappable with a glove. A dropdown needs two taps and a fingernail; a slider
needs a fingernail and precision. Neither belongs in a thing operated at a stop
light.

The bottom half is diagnostics, and it is the reason this page matters. It says
which telemetry channels this build is actually reading, straight out of
`telemetry.CHANNELS` rather than from a sentence someone has to remember to
update - so the three channels with no backend are named as absent, with the
reason, instead of quietly reading zero. It also says whether the offline map
archive and the routing graph are installed, because those two are the whole
difference between an app that works in a canyon and one that does not, and "why
is the map grey" should be answerable without a cable.
"""
import flet as ft

from agedashii_washi import config

from .. import (audio, bikes, boot, daylight, gauges, live, prefs, sensors,
                speech, telemetry, theme, tiles, ui, updates, version,
                voice_import, voicelines)
from ..theme import BRD, PAD, PAL, RAD

_KIND_WORDS = {
    "measured": "read straight off a sensor",
    "derived": "computed from the measured channels",
    "estimated": "a best guess, and allowed to come back empty",
}


def _rerender(page):
    rerender = getattr(page, "_navi_rerender", None)
    if callable(rerender):
        rerender()


def _tile(text, on_click, chosen, expand=True):
    p = PAL()
    return ft.Container(
        on_click=on_click, ink=True, height=ui.TOUCH, expand=expand,
        padding=ft.Padding(left=10, top=0, right=10, bottom=0),
        border_radius=RAD(12), alignment=ft.Alignment.CENTER,
        bgcolor=p["accent"] if chosen else p["surface"],
        border=None if chosen else BRD(p["border"], 1),
        content=ft.Text(text, size=ui.SMALL, weight=ft.FontWeight.W_700,
                        color=p["bg"] if chosen else p["text"],
                        text_align=ft.TextAlign.CENTER))


#: Every picker tile is this wide, so a row of two and a row of eight line up
#: down the page instead of each section inventing its own rhythm. Her "can you
#: just make things in a column properly then all ui buttons follow the same
#: 'idea' or whatever for organization and standarizaiton sake?".
TILE_W = 104


def _section(title, *blocks):
    """A heading and its controls, spaced the same way every time.

    One helper rather than each section assembling its own Column, so adding a
    setting cannot quietly introduce a different gap or a different heading size.
    """
    p = PAL()
    return ft.Column(spacing=8, tight=True, controls=[
        ft.Container(height=6),
        ft.Text(title, size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        *[b for b in blocks if b is not None]])


def _choices(options, note=None):
    """A wrapped row of equal-width tiles. The standard control on this page."""
    row = ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
        ft.Container(width=TILE_W, content=tile) for tile in options])
    if note is None:
        return row
    return ft.Column(spacing=6, tight=True, controls=[
        row, ft.Text(note, size=ui.SMALL, color=PAL()["text2"])])


def _switch_row(label, detail, value, on_change):
    p = PAL()
    return ui.card(ft.Row(spacing=10, controls=[
        ft.Column(spacing=0, expand=True, tight=True, controls=[
            ft.Text(label, size=ui.MID, weight=ft.FontWeight.W_700,
                    color=p["text"]),
            ft.Text(detail, size=ui.SMALL, color=p["text2"])]),
        ft.Switch(value=value, on_change=on_change, active_color=p["accent"]),
    ]))


def _mic_flag(page):
    """The microphone switch, which has to actually open or close the device.

    Not a plain `_flag`: storing the preference without starting or stopping the
    recorder would leave a switch that says "listening" while nothing listens -
    and, worse, the reverse.
    """
    async def run(wanted):
        if wanted:
            ok, message = await audio.start()
            prefs.set_value("mic", bool(ok))
            if not ok:
                STATE["note"] = f"microphone: {message}"
        else:
            await audio.stop()
            prefs.set_value("mic", False)
        await prefs.save(prefs.store())
        _rerender(page)

    def handler(e):
        page.run_task(run, bool(getattr(e.control, "value", False)))
    return handler


#: The last thing a switch had to say, kept across the repaint it causes.
STATE = {"note": ""}

def _line_groups():
    """Her line names in the three groups she recorded them in.

    Distances are whatever she HAS - Arnold's are 200, 400, 800 and 1500 ft -
    because the player rounds to those. The first-list names are shown only
    while she has none, as the suggestion; after that they would be nagging
    about files that would never play.
    """
    have = list(voice_import.distance_clips())
    return (
        ("Turns", [n for n in voice_import.NAMES if n in voicelines.STAND_IN]),
        ("Distances - said before the turn",
         have or list(voice_import.FIXED_DISTANCES)),
        ("Situations", [n for n in voice_import.NAMES if n in voicelines.SITUATIONS]),
    )


def _router_chip(graph, p):
    """(words, colour) for the offline router - by phase, not just "ready".

    It loads LAZILY, on the first route. Every phase but "ready" used to read
    "no graph", so an installed graph not loaded yet looked like offline
    routing was dead (her v1.17.0 panel; it was working).
    """
    phase = graph.get("phase")
    if graph.get("ready"):
        return "offline router", p["good"]
    if phase == "loading":
        return "offline router loading", p["text2"]
    if phase == "failed":
        return "router failed", p["bad"]
    if phase == "absent" or not graph.get("installed"):
        return "no graph", p["warn"]
    return "offline router", p["good"]                 # installed, not loaded yet


def _router_line(graph):
    detail = graph.get("detail") or ""
    if graph.get("ready"):
        return detail or "loaded"
    if graph.get("phase") in ("idle", "", None) and graph.get("installed"):
        return "installed - it loads on the first route, offline, in about a second"
    if graph.get("phase") == "loading":
        return "loading"
    return detail or "unknown"


def _voice_lines(page):
    """Every one of her line names, hers or not yet, with a tap to hear it.

    AGEDASHII's spec: "A screen that LISTS the inventory and shows which files
    are present and which are missing, with a play button to hear each one".
    One tile per name - this page's own control - and a tap plays exactly what
    a ride would: her clip, or the phone's words in its place.

    Returns (card, refresh). `refresh` re-reads the folder; the import panel
    calls it after every import and removal, or this list would go on saying
    "missing" about a clip she has just added.
    """
    p = PAL()
    groups = ft.Column(spacing=8, tight=True)
    heard = ft.Text("", size=ui.SMALL, color=p["text2"])

    def play(name):
        def handler(_e):
            items = voicelines.preview(name, voice_import.clip_path,
                                       prefs.imperial())
            if not items:
                heard.value = f"{name}: nothing plays until you add it - the phone never says this one."
            else:
                speech.play(items)
                whose = voice_import.clip_source(name) or "the phone's voice"
                problem = speech.STATE.get("problem")
                heard.value = f"Playing {name}, {whose}." + (f" {problem}" if problem else "")
            page.update()
        return handler

    def tile(name):
        # Three states, from the one lookup that also decides what plays
        # (voice_import.clip_source / clip_path): her import, the built-in
        # set that ships in the app, or the phone's voice.
        source = voice_import.clip_source(name)
        if source:
            note = source                               # "yours" / "built in"
        elif voicelines.stand_in(name) is not None:
            note = "phone voice"
        else:
            note = "silent"
        colour = {"yours": p["good"], "built in": p["accent"]}.get(source)
        return ft.Container(
            width=TILE_W, height=ui.TOUCH, on_click=play(name), ink=True,
            padding=ft.Padding(left=4, top=0, right=4, bottom=0),
            border_radius=RAD(12), alignment=ft.Alignment.CENTER,
            bgcolor=p["surface"],
            border=BRD(colour or p["border"], 2 if colour else 1),
            content=ft.Column(
                spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER, controls=[
                    ft.Text(name, size=ui.SMALL, weight=ft.FontWeight.W_700,
                            color=p["text"], text_align=ft.TextAlign.CENTER),
                    ft.Text(note, size=ui.SMALL, color=colour or p["text2"])]))

    def refresh():
        groups.controls = [
            ft.Column(spacing=6, tight=True, controls=[
                ft.Text(title, size=ui.SMALL, weight=ft.FontWeight.W_800,
                        color=p["text"]),
                ft.Row(wrap=True, spacing=8, run_spacing=8,
                       controls=[tile(n) for n in names])])
            for title, names in _line_groups()]

    refresh()
    card = ui.card(ft.Column(spacing=8, tight=True, controls=[
        ui.label("YOUR LINES"),
        ft.Text("Tap one to hear it. Green is your recording; \"built in\" "
                "ships with the app - importing one of the same name replaces "
                "it, and Remove voice brings it back. The rest are said in "
                "the phone's voice. Speeding is only ever a recording.",
                size=ui.SMALL, color=p["text2"]),
        groups,
        heard,
    ]))
    return card, refresh


def build(page, go=None, on_change=None):
    p = PAL()

    async def _save():
        await prefs.save(prefs.store())

    async def _check_updates(_e=None):
        """Ask the server what the newest build is. Off the UI thread.

        A network call, so it cannot run in the tap handler: with no signal it
        sits for the full timeout, and a frozen Settings page is exactly what
        the rest of this app is built to avoid.
        """
        import asyncio
        STATE["note"] = "checking…"
        _rerender(page)
        await asyncio.to_thread(updates.check)
        STATE["note"] = ""
        _rerender(page)

    async def _load_sensors(_e=None):
        """Attach the sensors on request, with the small overlay she asked for.

        Off the UI thread: `boot.stage_telemetry` waits up to a second and a
        bit for the first reading, and a second of frozen app is the thing this
        whole change exists to avoid.
        """
        import asyncio
        STATE["note"] = "loading telemetry…"
        _rerender(page)
        # force=True: pressing this button IS the demand, so it subscribes
        # even when `sensors_on_demand` would otherwise leave them off.
        STATE["note"] = await asyncio.to_thread(boot.stage_telemetry, page, True)
        _rerender(page)

    def _zero_sensors(_e=None):
        got = sensors.zero()
        page.run_task(_save)                   # the level is a stored pref now
        STATE["note"] = ("no accelerometer has reported yet"
                         if got is None else
                         f"level set: roll {got['roll']}deg, "
                         f"pitch {got['pitch']}deg")
        _rerender(page)

    def _unzero_sensors(_e=None):
        sensors.unzero()
        page.run_task(_save)
        STATE["note"] = "back to the phone's own angles"
        _rerender(page)

    def _set(name, value, restyle=False):
        def handler(_e=None):
            prefs.set_value(name, value)
            # Picking a theme by hand turns the automation off. Otherwise the
            # choice would be silently reverted at the next phase change, which
            # is the most infuriating possible behaviour for a setting.
            if name == "theme" and prefs.auto_theme():
                prefs.set_value("auto_theme", False)
            prefs.set_value("machine", prefs.machine())
            if name == "machine":
                audio.set_machine(value)
            # Applied immediately rather than at the next launch: she is
            # choosing this while looking at the screen it affects.
            if name == "orientation":
                apply = getattr(page, "_navi_orientation", None)
                if callable(apply):
                    page.run_task(apply)
            page.run_task(_save)
            if restyle:
                theme.apply(page)
            _rerender(page)
        return handler

    def _flag(name):
        def handler(e):
            prefs.set_value(name, bool(getattr(e.control, "value", False)))
            page.run_task(_save)
            _rerender(page)
        return handler

    def _toggle_gauges(_e=None):
        # The same door the Ride options button uses, so the two can never
        # disagree about what "off" means.
        prefs.toggle_dash()
        page.run_task(_save)
        _rerender(page)

    lines_card, lines_refresh = _voice_lines(page)
    graph = live.graph_status()
    # What is drawn, and of every style whether choosing it would change the
    # map at all. See tiles.py: with the offline archive installed, only the
    # archive's own rendering can ever be drawn.
    choice, tile_rows = tiles.choices(prefs.tiles())
    cannot = [row for row in tile_rows if not row["usable"]]
    report = {kind: telemetry.kinds(kind)
              for kind in ("measured", "derived", "estimated")}
    hardware = sensors.available()

    def _channel_block(kind):
        pal = PAL()
        colour = {"measured": pal["good"], "derived": pal["accent"],
                  "estimated": pal["warn"]}[kind]
        lines = []
        for name in report[kind]:
            _k, unit, about = telemetry.CHANNELS[name]
            lines.append(ft.Row(spacing=8, controls=[
                ft.Container(width=8, height=8, border_radius=4,
                             bgcolor=colour),
                ft.Text(name, size=ui.SMALL, weight=ft.FontWeight.W_700,
                        color=pal["text"], width=84),
                ft.Text(f"{about} ({unit})", size=ui.SMALL,
                        color=pal["text2"], expand=True, max_lines=2)]))
        return ft.Column(spacing=6, controls=[
            ft.Text(f"{kind} - {_KIND_WORDS[kind]}", size=ui.SMALL,
                    weight=ft.FontWeight.W_800, color=colour),
            *lines])

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Container(height=6),
        ft.Text("Look", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        # Wrapped, with a real width. Eight themes in one Row divided the
        # screen eight ways and every label broke mid-word - "Nig ht", "Cy ber
        # pu", "Clu ste r". A wrap lets them take the width they need and flow
        # onto a second line.
        ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
            ft.Container(width=TILE_W, content=_tile(
                theme.THEME_NAMES[key], _set("theme", key, restyle=True),
                prefs.theme_key() == key, expand=False))
            for key in theme.THEMES]),
        ft.Text("Sunlight is deliberately ugly indoors - it is the one that "
                "stays readable with the sun behind you. Red night keeps your "
                "dark adaptation: rods are nearly blind to deep red.",
                size=ui.SMALL, color=p["text2"]),
        _switch_row("Follow the sun",
                    "Sunlight by day, Night at dusk, Red night after dark, and "
                    "the screen dimmed to match - worked out from where you "
                    "are, with no sensor and no signal. Picking a theme above "
                    "turns this off.",
                    prefs.auto_theme(), _flag("auto_theme")),

        ft.Container(height=6),
        ft.Text("The bike", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile(name, _set("machine", key), prefs.machine() == key)
            for key, name in bikes.names()]),
        ft.Text(
            f"{bikes.profile(prefs.machine())['detail']} — fires "
            f"{bikes.firing_ratio(prefs.machine()):.0f}x per revolution, so "
            f"RPM = Hz x {60 / bikes.firing_ratio(prefs.machine()):.0f}, and "
            f"the exhaust is searched between "
            f"{bikes.band_hz(prefs.machine())[0]:.0f} and "
            f"{bikes.band_hz(prefs.machine())[1]:.0f} Hz. Get this wrong and "
            "every RPM reading is out by a fixed factor.",
            size=ui.SMALL, color=p["text2"]),

        ft.Text("The bike on the map", size=ui.MID,
                weight=ft.FontWeight.W_800, color=p["text"]),
        ft.Row(spacing=8, controls=[
            ft.Container(
                on_click=_set("bike", key), ink=True, height=ui.TOUCH,
                expand=True, border_radius=RAD(12),
                alignment=ft.Alignment.CENTER, bgcolor=p["surface"],
                border=BRD(p["accent"] if prefs.bike() == key else p["border"],
                           2 if prefs.bike() == key else 1),
                content=ft.Row(spacing=10, tight=True,
                               alignment=ft.MainAxisAlignment.CENTER,
                               controls=[ui.bike_marker(key, size=28),
                                         ft.Text(key.title(), size=ui.SMALL,
                                                 weight=ft.FontWeight.W_700,
                                                 color=p["text"])]))
            for key in theme.BIKE_COLOURS]),

        ft.Container(height=6),
        ft.Text("Units", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile("mph, feet, miles", _set("units", "imperial"),
                  prefs.imperial()),
            _tile("km/h, metres", _set("units", "metric"),
                  not prefs.imperial())]),

        ft.Container(height=6),
        ft.Text("Map tiles", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        # Only the styles that choosing would actually draw. Hers: "putting
        # streets or topo changes nothing either it just the black tiles" -
        # because four tiles were offered and three could never be honoured.
        # The lit tile is the one ON SCREEN, not the one stored.
        ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
            ft.Container(width=TILE_W, content=_tile(
                row["label"], _set("tiles", row["key"]), row["drawn"],
                expand=False))
            for row in tile_rows if row["usable"]]),
        ft.Text(f"now: {choice['note']} · {choice['attribution']}",
                size=ui.SMALL, color=p["text2"]),
        # One line per reason, from the rows themselves: there are two now
        # (the archive wins; Dark has no online source).
        *[ft.Text(f"{', '.join(r['label'] for r in cannot if r['why'] == why)}: "
                  f"{why}.", size=ui.SMALL, color=p["text2"])
          for why in dict.fromkeys(r["why"] for r in cannot)],
        ft.Text(("The theme recolours the buttons, the gauges and these "
                 "pages. It cannot recolour the map: the offline map is one "
                 "rendering, drawn in advance.") if choice["offline"] else
                "No offline map on this phone, so every style needs signal - "
                "with none, the map is blank.",
                size=ui.SMALL, color=p["text2"]),

        # Only on a vector map, where buildings are a layer that can be shown
        # or hidden. On the raster archive they are painted into the tile and
        # no switch can take them out, so offering one there would be the same
        # lie as the three tile styles that could never be honoured. `vector`
        # is absent until the vector map ships, so this stays hidden until it
        # can actually do something.
        *([_switch_row("Buildings",
                       "the shapes of buildings on the map - landmarks in "
                       "town, clutter on a back road",
                       prefs.buildings(), _flag("buildings"))]
          if choice.get("vector") else []),

        ft.Text("GPS samples per second", size=ui.MID,
                weight=ft.FontWeight.W_800, color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile(f"{rate} Hz", _set("hz", rate), prefs.hz() == rate)
            for rate in (1, 2, 5, 10)]),
        ft.Text("1 Hz is enough to navigate from and easiest on the battery. "
                "Higher rates are for a ride you want the detail of.",
                size=ui.SMALL, color=p["text2"]),

        _switch_row("Keep the screen on",
                    "a phone that sleeps at 60 mph is not a navigation device",
                    prefs.keep_awake(), _flag("keep_awake")),
        # A problem REPLACES the promise: it read "spoken aloud - Turns are
        # shown, not spoken" in the browser, one sentence saying both.
        _switch_row("Announce turns",
                    speech.STATE.get("problem")
                    or "shown large at the top and spoken aloud - "
                    + speech.status_text(),
                    prefs.voice(), _flag("voice")),
        # Her own recorded lines: getting them in (minion 4's panel), what is
        # in, and a tap to hear each one.
        voice_import.panel(page, on_change=lines_refresh),
        lines_card,
        _switch_row("Say street names after your lines",
                    "the phone's voice adds \"onto Broadway\" after a turn you "
                    "recorded. Off: your line is the whole line.",
                    prefs.clip_streets(), _flag("clip_streets")),
        _switch_row("Character lines",
                    "his wild lines after some prompts - always after the "
                    "instruction, never in the last seconds before a turn. "
                    "Off keeps the voice and drops the theatre.",
                    prefs.flourishes(), _flag("flourishes")),
        _switch_row("Record automatically",
                    "start a ride log as soon as the bike is rolling",
                    prefs.autorecord(), _flag("autorecord")),
        ft.Container(height=6),
        ft.Text("Gauges", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        # ONE switch, here at the top of its own section, and the Ride options
        # button is the same switch. Hers: "in settings there's 'show the
        # gauges at all' and the in options there's gauges there and it only
        # shows or hides it... sooooooo idk". It was at the far end of the
        # page, and "Off" was also one of the layouts below.
        _switch_row("Show the gauges",
                    "the Gauges button in Ride options is this same switch. "
                    "Below is only which layout they use.",
                    prefs.dash(), _toggle_gauges),
        ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
            ft.Container(width=TILE_W, content=_tile(
                name, _set("hud", key), prefs.hud() == key, expand=False))
            for key, name, _detail in gauges.LAYOUTS]),
        ft.Text(next((d for k, _n, d in gauges.LAYOUTS
                      if k == prefs.hud()), "")
                + ("" if prefs.dash() else
                   " - hidden right now; the switch above brings them back"),
                size=ui.SMALL, color=p["text2"]),
        ft.Text("Where they sit", size=ui.SMALL,
                weight=ft.FontWeight.W_800, color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile("Sideways: right", _set("hud_side", "right"),
                  prefs.hud_side() == "right"),
            _tile("Sideways: left", _set("hud_side", "left"),
                  prefs.hud_side() == "left")]),
        ft.Row(spacing=8, controls=[
            _tile("Upright: bottom", _set("hud_edge", "bottom"),
                  prefs.hud_edge() == "bottom"),
            _tile("Upright: top", _set("hud_edge", "top"),
                  prefs.hud_edge() == "top")]),
        ft.Text("Turning the phone re-lays it out on its own - a column down "
                "the side when it is wide, a row along the edge when it is "
                "upright.", size=ui.SMALL, color=p["text2"]),

        ft.Container(height=6),
        ft.Text("Which way up", size=ui.SMALL,
                weight=ft.FontWeight.W_800, color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile("Follow the phone", _set("orientation", "auto", True),
                  prefs.orientation() == "auto"),
            _tile("Always sideways", _set("orientation", "landscape", True),
                  prefs.orientation() == "landscape"),
            _tile("Always upright", _set("orientation", "portrait", True),
                  prefs.orientation() == "portrait")]),
        ft.Text("Android's own rotation lock only holds whichever way up the "
                "phone was when you tapped it - and a lean into a corner is a "
                "rotation as far as the accelerometer is concerned. Lock it "
                "here and a sideways mount stays sideways through a hard left.",
                size=ui.SMALL, color=p["text2"]),

        ft.Container(height=6),
        ft.Text("App artwork", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile("CB750", _set("icon", "cb"), prefs.icon() == "cb"),
            _tile("Alternate", _set("icon", "ex"), prefs.icon() == "ex")]),
        ft.Text("Inside the app only. Android will not let an app change its "
                "own launcher icon without native plumbing Flet does not "
                "expose, so the one on your home screen is whatever was built "
                "in.", size=ui.SMALL, color=p["text2"]),
        _switch_row("Bottom bar",
                    "off keeps only the round menu button, top-left - the map "
                    "gets the height back",
                    prefs.bar(), _flag("bar")),
        _switch_row("Listen to the engine",
                    "the microphone, for loudness and the RPM estimate - off "
                    "unless you turn it on",
                    prefs.mic(), _mic_flag(page)),
        _switch_row("Match the dashboard's colours",
                    "these pages use the alola palette; the map keeps the "
                    "theme above",
                    prefs.alola_chrome(), _flag("alola_chrome")),

        ft.Container(height=6),
        ft.Text("Off route after", size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        ft.Row(spacing=8, controls=[
            _tile(f"{metres} m", _set("offroute_m", metres),
                  prefs.offroute_m() == metres)
            for metres in (30, 45, 75, 120)]),
        ft.Text("Three fixes in a row beyond this before it says so, and it "
                "has to come back well inside before it stops - otherwise one "
                "noisy fix re-routes a corner you took correctly.",
                size=ui.SMALL, color=p["text2"]),

        ft.Container(height=6),
        ft.Text("What this build is actually getting", size=ui.MID,
                weight=ft.FontWeight.W_800, color=p["text"]),
        ui.card(ft.Column(spacing=10, controls=[
            ft.Row(spacing=8, controls=[
                ui.chip("offline maps" if choice["offline"]
                        else "online maps only",
                        p["good"] if choice["offline"] else p["warn"],
                        ft.Icons.MAP),
                ui.chip(*_router_chip(graph, p), ft.Icons.ALT_ROUTE)]),
            # "Road graph:" so this line cannot be read as the sensors' status,
            # which is what she took "not started" for.
            ft.Text("Road graph: " + _router_line(graph),
                    size=ui.SMALL, color=p["text2"]),
            ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
                ui.chip(name.replace("_", " "), p["good"], ft.Icons.SENSORS)
                for name in hardware["seen"]] or [
                ft.Text("no sensor has reported yet", size=ui.SMALL,
                        color=p["text2"])]),
            # Her "app loads. then if/when i want i can click 'load/launch
            # sensors/telemtry' ... hopefully making the front end lighter".
            ui.button("Sensors running" if sensors.attached()
                      else "Load the sensors now",
                      lambda e: page.run_task(_load_sensors),
                      icon=ft.Icons.SENSORS, expand=True,
                      primary=not sensors.attached()),
            _switch_row("Wait to be asked",
                        "the sensors attach when you tap the button above, "
                        "rather than at every launch",
                        prefs.sensors_on_demand(),
                        _flag("sensors_on_demand")),
            # Live readings, and a way to call the mount's angle "level".
            #
            # Hers: "IT WOULD BE NICE TO ACTUALLY SEE THE SENSORS LIKE LIVE.
            # OR TEST SENSORS NOW OR SOMETHING?" - and separately, "sometimes i
            # move the phone mount of something and itd be good to recalibrate
            # what is 'home' or 'straight'".
            *([ft.Container(
                padding=PAD(10, 10), bgcolor=p["bg"],
                border_radius=RAD(8), border=BRD(p["border"], 1),
                content=ft.Column(spacing=6, controls=[
                    ft.Row(wrap=True, spacing=14, run_spacing=6,
                           controls=([
                               ft.Text(f"{k.replace('_', ' ')} {v}",
                                       size=ui.SMALL, color=p["text"],
                                       weight=ft.FontWeight.W_700)
                               for k, v in sorted(sensors.latest().items())]
                               or [ft.Text("nothing yet", size=ui.SMALL,
                                           color=p["text2"])])),
                    ft.Text(("level set at "
                             f"roll {sensors.zeroed()['roll']}deg, "
                             f"pitch {sensors.zeroed()['pitch']}deg")
                            if sensors.zeroed()["set"] else
                            "reading the phone's own angles - set level once "
                            "the mount is where you want it",
                            size=ui.SMALL, color=p["text2"]),
                    ft.Row(spacing=8, controls=[
                        ui.button("Set level here", _zero_sensors,
                                  icon=ft.Icons.CENTER_FOCUS_STRONG,
                                  expand=True),
                        ui.button("Clear", _unzero_sensors,
                                  icon=ft.Icons.RESTART_ALT, expand=True)]),
                    ui.button("Refresh", lambda e: _rerender(page),
                              icon=ft.Icons.REFRESH, expand=True),
                    ft.Text("Hold the bike upright and level before setting "
                            "it. Tilt and pitch are the PHONE's angles, so "
                            "they only mean anything relative to the mount.",
                            size=ui.SMALL, color=p["text2"])]))]
              if sensors.attached() else []),
            *([ui.chip(f"{name}: {why}", p["warn"], ft.Icons.SENSORS_OFF)
               for name, why in hardware["errors"].items()]),
            ui.chip("microphone listening" if audio.listening()
                    else "microphone off",
                    p["good"] if audio.listening() else p["text2"],
                    ft.Icons.MIC if audio.listening() else ft.Icons.MIC_OFF),
            # Why the tacho is blank, or why its figure is older than a second.
            # audio.py works this out on every window and used to keep it to
            # itself, so a silent gauge looked identical to a broken one.
            *([ui.chip(("no RPM: " if audio.STATE.get("rpm_est") is None
                        else "RPM held: ") + audio.STATE["rpm_rejected"],
                       p["text2"], ft.Icons.INFO_OUTLINE)]
              if audio.listening() and audio.STATE.get("rpm_rejected") else []),
            ft.Divider(height=1, color=p["border"]),
            _channel_block("measured"),
            _channel_block("derived"),
            _channel_block("estimated"),
            ft.Text("A channel with no sensor behind it on this device is "
                    "simply absent from the log - never a zero, which would be "
                    "indistinguishable from a real reading.",
                    size=ui.SMALL, color=p["text2"]),
            ft.Text(STATE["note"], size=ui.SMALL, color=p["warn"]),
        ])),

        ui.card(ft.Column(spacing=4, controls=[
            ft.Text("Server", size=ui.SMALL, weight=ft.FontWeight.W_800,
                    color=p["text"]),
            # SERVER_URL, not BASE. Washi's config has no BASE, so this line
            # said "not set" on every phone whatever the address was.
            ft.Text(getattr(config, "SERVER_URL", "") or "not set",
                    size=ui.SMALL, color=p["text2"]),
            ft.Text("Only the saved-route list and pushing a finished ride use "
                    "it. A finished ride is saved there on the Pi and shows in "
                    "Moto under Trips > Rides - the Rides page says which have "
                    "gone.", size=ui.SMALL, color=p["text3"]),
        ])),

        ui.card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, controls=[
                ft.Text("This build", size=ui.SMALL,
                        weight=ft.FontWeight.W_800, color=p["text"]),
                ft.Container(expand=True),
                ui.chip(version.label(), p["text2"], ft.Icons.TAG)]),
            ft.Text(updates.summary(), size=ui.SMALL,
                    color=(p["good"] if updates.available()
                           else p["text2"])),
            ft.Row(spacing=8, controls=[
                ui.button("Check for updates",
                          lambda e: page.run_task(_check_updates),
                          icon=ft.Icons.SYSTEM_UPDATE, expand=True),
                *([ui.button("Get it", lambda e: updates.install(page),
                             icon=ft.Icons.DOWNLOAD, primary=True,
                             expand=True)] if updates.available() else [])]),
            ft.Text("The download and the install are the browser's job - the "
                    "app only knows there is something to install. Same "
                    "signing key, so it lands as an update and your rides "
                    "stay.", size=ui.SMALL, color=p["text3"]),
        ])),

        ui.button("How this works",
                  lambda e: go("help") if callable(go) else None,
                  icon=ft.Icons.HELP_OUTLINE, expand=True),
        ft.Container(height=12),
    ])
