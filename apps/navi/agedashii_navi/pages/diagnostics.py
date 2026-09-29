"""What this build is actually getting - read-outs, on a page of their own.

They were the bottom half of Settings, in one card with the choices above
them, and that is how she read the road graph's "not started" as the sensors'
status: two different things sharing a card look like one thing. Here every
subject is its own card with its own heading - the map and the router, the
sensors, what a ride log records, the build - and nothing on this page is a
preference except the two that act on what it shows (load the sensors now,
wait to be asked).

The channel list comes straight out of `telemetry.CHANNELS` rather than a
sentence someone has to remember to update, so a channel with no backend is
named as absent, with the reason, instead of quietly reading zero.

Reached from the last door in Settings, and it goes back there.

A button here repaints THIS page's column in place rather than the whole
shell: the same scrolling Column stays mounted, so she stays where she was
instead of being thrown back to the top - which is what every switch in the
old single Settings page did.
"""
import asyncio

import flet as ft

from agedashii_washi import config

from .. import audio, boot, live, prefs, sensors, telemetry, tiles, ui, \
    updates, version
from ..theme import BRD, PAD, PAL, RAD

TITLE = "What this build is getting"

KIND_WORDS = {
    "measured": "read straight off a sensor",
    "derived": "computed from the measured channels",
    "estimated": "a best guess, and allowed to come back empty",
}

#: The last thing a button had to say, kept across the repaint it causes -
#: one per card, so "checking…" for an update never lands under the sensors.
STATE = {"sensors": "", "build": ""}


def router_chip(graph, p):
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


def router_line(graph):
    detail = graph.get("detail") or ""
    if graph.get("ready"):
        return detail or "loaded"
    if graph.get("phase") in ("idle", "", None) and graph.get("installed"):
        return "installed - it loads on the first route, offline, in about a second"
    if graph.get("phase") == "loading":
        return "loading"
    return detail or "unknown"


def _heading(text):
    return ft.Text(text, size=ui.MID, weight=ft.FontWeight.W_800,
                   color=PAL()["text"])


def _note(text, colour=None):
    return ft.Text(text, size=ui.SMALL, color=colour or PAL()["text2"])


def _maps_card():
    p = PAL()
    choice, _rows = tiles.choices(prefs.tiles())
    graph = live.graph_status()
    return ui.card(ft.Column(spacing=8, tight=True, controls=[
        _heading("Map and routing"),
        ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
            ui.chip("offline maps" if choice["offline"] else "online maps only",
                    p["good"] if choice["offline"] else p["warn"],
                    ft.Icons.MAP),
            ui.chip(*router_chip(graph, p), ft.Icons.ALT_ROUTE)]),
        _note(f"Map: {choice['note']}"),
        # "Road graph:" so this line cannot be read as anything else's status.
        _note("Road graph: " + router_line(graph)),
    ]))


def _readings(p, zero, unzero, refresh):
    """Live readings, and a way to call the mount's angle "level".

    Hers: "IT WOULD BE NICE TO ACTUALLY SEE THE SENSORS LIKE LIVE. OR TEST
    SENSORS NOW OR SOMETHING?" - and "sometimes i move the phone mount of
    something and itd be good to recalibrate what is 'home' or 'straight'".
    """
    zeroed = sensors.zeroed()
    return ft.Container(
        padding=PAD(10, 10), bgcolor=p["bg"], border_radius=RAD(8),
        border=BRD(p["border"], 1),
        content=ft.Column(spacing=8, tight=True, controls=[
            ft.Row(wrap=True, spacing=14, run_spacing=6, controls=[
                ft.Text(f"{k.replace('_', ' ')} {v}", size=ui.SMALL,
                        color=p["text"], weight=ft.FontWeight.W_700)
                for k, v in sorted(sensors.latest().items())] or [
                _note("nothing yet")]),
            _note(f"level set at roll {zeroed['roll']}deg, "
                  f"pitch {zeroed['pitch']}deg" if zeroed["set"] else
                  "reading the phone's own angles - set level once the mount "
                  "is where you want it"),
            ft.Row(spacing=8, controls=[
                ui.button("Set level here", zero,
                          icon=ft.Icons.CENTER_FOCUS_STRONG, expand=True),
                ui.button("Clear", unzero, icon=ft.Icons.RESTART_ALT,
                          expand=True)]),
            ui.button("Refresh", refresh, icon=ft.Icons.REFRESH, expand=True),
            _note("Hold the bike upright and level before setting it. Tilt and "
                  "pitch are the PHONE's angles, so they only mean anything "
                  "relative to the mount."),
        ]))


def _channels(kind, p):
    colour = {"measured": p["good"], "derived": p["accent"],
              "estimated": p["warn"]}[kind]
    lines = []
    for name in telemetry.kinds(kind):
        _k, unit, about = telemetry.CHANNELS[name]
        lines.append(ft.Row(spacing=8, controls=[
            ft.Container(width=8, height=8, border_radius=4, bgcolor=colour),
            ft.Text(name, size=ui.SMALL, weight=ft.FontWeight.W_700,
                    color=p["text"], width=84),
            ft.Text(f"{about} ({unit})", size=ui.SMALL, color=p["text2"],
                    expand=True, max_lines=2)]))
    return ft.Column(spacing=6, tight=True, controls=[
        ft.Text(f"{kind} - {KIND_WORDS[kind]}", size=ui.SMALL,
                weight=ft.FontWeight.W_800, color=colour),
        *lines])


def build(page, go=None):
    """The page: a heading, a way back, and one card per subject."""
    body = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)

    def refresh(_e=None, paint=True):
        body.controls = _controls()
        if paint:
            page.update()

    async def _save():
        await prefs.save(prefs.store())

    async def load_sensors():
        """Attach the sensors on request. Off the UI thread:
        `boot.stage_telemetry` waits up to a second and a bit for the first
        reading, and a second of frozen app is what this exists to avoid."""
        STATE["sensors"] = "loading telemetry…"
        refresh()
        # force=True: pressing the button IS the demand, so it subscribes even
        # when `sensors_on_demand` would otherwise leave them off.
        STATE["sensors"] = await asyncio.to_thread(boot.stage_telemetry, page,
                                                   True)
        refresh()

    def zero(_e=None):
        got = sensors.zero()
        STATE["sensors"] = ("no accelerometer has reported yet" if got is None
                            else f"level set: roll {got['roll']}deg, "
                                 f"pitch {got['pitch']}deg")
        refresh()

    def unzero(_e=None):
        sensors.unzero()
        STATE["sensors"] = "back to the phone's own angles"
        refresh()

    def on_demand(e):
        prefs.set_value("sensors_on_demand",
                        bool(getattr(e.control, "value", False)))
        page.run_task(_save)
        refresh()

    async def check_updates():
        """Ask the server what the newest build is. Off the UI thread: with no
        signal it sits for the full timeout."""
        STATE["build"] = "checking…"
        refresh()
        await asyncio.to_thread(updates.check)
        STATE["build"] = ""
        refresh()

    def _sensors_card():
        p = PAL()
        hardware = sensors.available()
        listening = audio.listening()
        return ui.card(ft.Column(spacing=8, tight=True, controls=[
            _heading("Sensors"),
            ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[
                ui.chip(name.replace("_", " "), p["good"], ft.Icons.SENSORS)
                for name in hardware["seen"]] or [
                _note("no sensor has reported yet")]),
            *[ui.chip(f"{name}: {why}", p["warn"], ft.Icons.SENSORS_OFF)
              for name, why in hardware["errors"].items()],
            ui.chip("microphone listening" if listening else "microphone off",
                    p["good"] if listening else p["text2"],
                    ft.Icons.MIC if listening else ft.Icons.MIC_OFF),
            # Why the tacho is blank, or why its figure is older than a second.
            *([ui.chip(("no RPM: " if audio.STATE.get("rpm_est") is None
                        else "RPM held: ") + audio.STATE["rpm_rejected"],
                       p["text2"], ft.Icons.INFO_OUTLINE)]
              if listening and audio.STATE.get("rpm_rejected") else []),
            # Her "app loads. then if/when i want i can click 'load/launch
            # sensors/telemtry' ... hopefully making the front end lighter".
            ui.button("Sensors running" if sensors.attached()
                      else "Load the sensors now",
                      lambda e: page.run_task(load_sensors),
                      icon=ft.Icons.SENSORS, expand=True,
                      primary=not sensors.attached()),
            ft.Row(spacing=10, controls=[
                ft.Column(spacing=0, expand=True, tight=True, controls=[
                    ft.Text("Wait to be asked", size=ui.MID,
                            weight=ft.FontWeight.W_700, color=p["text"]),
                    _note("the sensors attach when you tap the button above, "
                          "rather than at every launch")]),
                ft.Switch(value=prefs.sensors_on_demand(), on_change=on_demand,
                          active_color=p["accent"])]),
            *([_readings(p, zero, unzero, refresh)]
              if sensors.attached() else []),
            *([_note(STATE["sensors"], p["warn"])] if STATE["sensors"] else []),
        ]))

    def _log_card():
        p = PAL()
        return ui.card(ft.Column(spacing=10, tight=True, controls=[
            _heading("What a ride log records"),
            _channels("measured", p),
            _channels("derived", p),
            _channels("estimated", p),
            _note("A channel with no sensor behind it on this device is simply "
                  "absent from the log - never a zero, which would be "
                  "indistinguishable from a real reading."),
        ]))

    def _build_card():
        p = PAL()
        return ui.card(ft.Column(spacing=8, tight=True, controls=[
            ft.Row(spacing=8, controls=[
                _heading("This build"),
                ft.Container(expand=True),
                ui.chip(version.label(), p["text2"], ft.Icons.TAG)]),
            _note(updates.summary(),
                  p["good"] if updates.available() else p["text2"]),
            ft.Row(spacing=8, controls=[
                ui.button("Check for updates",
                          lambda e: page.run_task(check_updates),
                          icon=ft.Icons.SYSTEM_UPDATE, expand=True),
                *([ui.button("Get it", lambda e: updates.install(page),
                             icon=ft.Icons.DOWNLOAD, primary=True,
                             expand=True)] if updates.available() else [])]),
            *([_note(STATE["build"], p["warn"])] if STATE["build"] else []),
            _note("The download and the install are the browser's job - the "
                  "app only knows there is something to install. Same signing "
                  "key, so it lands as an update and your rides stay.",
                  p["text3"]),
            ft.Divider(height=1, color=p["border"]),
            ft.Text("Server", size=ui.SMALL, weight=ft.FontWeight.W_800,
                    color=p["text"]),
            # SERVER_URL, not BASE: Washi's config has no BASE.
            _note(getattr(config, "SERVER_URL", "") or "not set"),
            _note("Only the saved-route list and pushing a finished ride use "
                  "it. A finished ride is saved there on the Pi and shows in "
                  "Moto under Trips > Rides - the Rides page says which have "
                  "gone.", p["text3"]),
        ]))

    def _controls():
        p = PAL()
        return [
            ui.row_tap(ft.Row(spacing=10, controls=[
                ft.Icon(ft.Icons.ARROW_BACK, size=20, color=p["text"]),
                ft.Text("Settings", size=ui.MID, weight=ft.FontWeight.W_700,
                        color=p["text"])]),
                lambda e: go("settings") if callable(go) else None),
            ft.Text(TITLE, size=ui.BIG, weight=ft.FontWeight.W_800,
                    color=p["text"]),
            _note("What the phone is actually giving this app, right now."),
            _maps_card(),
            _sensors_card(),
            _log_card(),
            _build_card(),
            ui.button("How this works",
                      lambda e: go("help") if callable(go) else None,
                      icon=ft.Icons.HELP_OUTLINE, expand=True),
            ft.Container(height=12),
        ]

    refresh(paint=False)
    return body
