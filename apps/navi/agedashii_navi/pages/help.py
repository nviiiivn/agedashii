"""How this works - what each part does, and what it needs to do it.

One screen, read once, written to answer the questions that actually come up on
a ride: why the map is grey, why there is no route, where the lean angle comes
from, and whether the speed limit is a real sign.

Titled "How this works" and reached from a nav row labelled "Help", because both
were wanted: "help/how this works? why not both?"

The honest-limits section is not an apology. It is the part that stops her
trusting a number she should not: the lean angle is real physics but only above
walking pace, the speed limit is typical-for-the-road-class and not a reading of
a sign, and three channels have no sensor behind them in this build at all.
"""
import flet as ft

from .. import guidance, prefs, telemetry, tiles, ui
from ..theme import PAL

TITLE = "How this works"


def _section(heading, lines):
    p = PAL()
    return ui.card(ft.Column(spacing=6, controls=[
        ft.Text(heading, size=ui.MID, weight=ft.FontWeight.W_800,
                color=p["text"]),
        *[ft.Text(line, size=ui.SMALL, color=p["text2"]) for line in lines],
    ]))


def build(page, go=None):
    p = PAL()
    estimated = ", ".join(telemetry.kinds("estimated"))
    styles = ", ".join(tiles.label(k) for k in tiles.STYLES)
    # Read, not written down: this sentence used to say the offline map was a
    # street rendering, and it is a dark one.
    drawn = tiles.pick(prefs.tiles())

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Row(spacing=10, controls=[
            ui.pill("Navi"),
            ft.Text(TITLE, size=ui.BIG, weight=ft.FontWeight.W_800,
                    color=p["text"])]),

        _section("It is built to work with no signal", [
            "The map draws from an MBTiles archive on the phone, served to the "
            "map by a tiny web server inside the app. No archive installed "
            "means online tiles, and the chip under the map says which you are "
            "looking at rather than leaving you to guess.",
            "The router is a road graph on the phone too - a real search, not a "
            "cached answer - so turn-by-turn works in a canyon. Every route "
            "says whether it came from the phone or the server, and it is never "
            "guessed at.",
            "GPS itself never needed a network. Recording a ride never does.",
        ]),

        _section("Ride", [
            "The turn band is only there when there is a route. Speed is the "
            "biggest thing on screen because it is what gets read most.",
            "Record starts a ride log. It writes to the phone as you go, so a "
            "flat battery costs a few seconds rather than the whole ride.",
            (f"There are four map styles: {styles}. The offline map on this "
             f"phone is one rendering - {tiles.label(drawn['style'])} - and it "
             "always wins, so the map never goes blank with no signal. The "
             "others are online styles and are not offered while it is "
             "installed. Settings says which one is drawn."
             if drawn["offline"] and drawn["style"] else
             f"There are four map styles: {styles}. With no offline map on "
             "this phone every one of them needs signal. Settings says which "
             "one is drawn."),
        ]),

        _section("Turns, and when they are announced", [
            "Prompts are timed, not fixed: roughly a minute, twenty seconds and "
            "six seconds before a turn, so the same instruction arrives at the "
            "right moment at 25 mph and at 70.",
            "Each one fires once. Nothing repeats every second.",
            "Off route needs three fixes in a row beyond the threshold, and "
            "coming back needs you well inside it - otherwise one bad fix under "
            "a bridge announces a mistake you did not make.",
            "Each prompt is shown large at the top and spoken by the phone's "
            "own voice, offline, through the helmet if it is paired - your "
            "music dips for it and comes back. Announce turns in Settings "
            "switches both. If the phone has no offline voice, the Ride page "
            "says so and tells you where to install one.",
        ]),

        _section("Where to", [
            "Typing a street name searches the graph on the phone. Pasting "
            "coordinates, a geo: pin or a maps link routes straight away.",
            "Places asks an online geocoder, and says so on the button - a "
            "separate button rather than an automatic fallback, so the box does "
            "not silently stop working when the radio dies.",
            "Saved trips and rides come from the server and are served from "
            "this phone's cached copy when there is nothing to ask.",
            "A GPX import takes the end of the track as the destination and "
            "draws the imported shape beside the line you are given.",
        ]),

        _section("The telemetry, and what is actually behind it", [
            "Measured channels come off the GPS receiver. Speed and heading are "
            "the receiver's own Doppler solution, not positions subtracted - "
            "which is why they are usable while barely moving.",
            "Derived channels are computed from those, and the arithmetic is "
            "written down in the source. Lean angle is atan(v * yaw rate / g): "
            "that is what lean IS for any bike in a steady corner, with no "
            "calibration - but it is meaningless below about 11 mph, so it "
            "reports nothing there rather than a number.",
            "The accelerometer, gyroscope, magnetometer and barometer are all "
            "real and all read directly - Flet ships them. A phone without one "
            "of them simply contributes no column for it, never a zero, so a "
            "reading and a gap can never be confused. Settings lists which ones "
            "have actually reported on this device.",
            f"Estimated channels are a best guess and are allowed to come back "
            f"empty: {estimated}. See the engine note, below.",
        ]),

        _section("The engine note, and the RPM", [
            "With the microphone on, the exhaust fundamental is found and turned "
            "into engine speed. A four-stroke fires cylinders/2 times per "
            "revolution, so the CB750 - an inline four - gives RPM = Hz x 30, "
            "and the EX250 twin gives RPM = Hz x 60. That is why the bike has to "
            "be set correctly: the wrong one is out by a fixed factor.",
            "The signal is high-passed to drop road rumble, band-limited to the "
            "range that bike's engine can actually produce, and the fundamental "
            "is found by harmonic product rather than by taking the loudest bin "
            "- which would report double the true speed whenever the second "
            "harmonic is the louder one, as it usually is.",
            "It refuses to answer rather than guessing. Too quiet, no clear "
            "fundamental above the background, a figure outside the engine's rev "
            "range, or a jump too big to be mechanical - each of those shows a "
            "dash instead of a number.",
            "The loudness meter is the engine's band only, so it follows the "
            "throttle rather than the wind, and the three-bar meter beside it "
            "shows where in the band the energy sits.",
        ]),

        _section("Following the sun", [
            "Sunlight by day, Night at dusk, Red night after dark, and the "
            "screen dimmed to match. Worked out from where you are and what time "
            "it is - no light sensor, no network, no permission.",
            "That is deliberately better than a light sensor here: a sensor "
            "reads the shadow of an overpass as night and a headlight behind you "
            "as day, so the screen hunts between levels on a road with trees. "
            "The sun's altitude changes over minutes.",
            "Red on black is not decoration. Rod cells carry night vision and "
            "are nearly blind to deep red, so a red screen can be read without "
            "spending the dark adaptation that took twenty minutes to build.",
            "Picking a theme by hand turns the automation off, so a deliberate "
            "choice is never silently reverted.",
        ]),

        _section("The speed limit", [
            f"It is {guidance.LIMIT_SOURCE}.",
            "The graph knows what kind of road you are on - motorway, "
            "residential, service - and this shows what that kind of road is "
            "normally posted at in California. It is not a camera and it has "
            "not read the sign.",
            "The graph's own speed table is a different thing again: those are "
            "fitted door-to-door travel speeds used to pick a route, and they "
            "are lower than any sign. Showing them as limits would be wrong by "
            "10 mph on a residential street, so they are not used here.",
        ]),

        _section("Rides, and getting the data out", [
            "Every recording is listed from the phone, newest first, with its "
            "distance, top speed, maximum lean, hardest brake and climb.",
            "CSV is every channel, one row per sample. GPX is the track, with "
            "the derived channels in an extensions block. JSON carries the "
            "samples and the dictionary that explains the columns, so the "
            "export still makes sense in six months.",
            "Send to the server queues through the same outbox the rest of "
            "Agedashii uses, so a ride pushed with no signal goes up when "
            "signal comes back instead of being lost.",
        ]),

        _section("What needs the network", [
            "Places (the geocoder), What is around here (Overpass), the saved "
            "trips and rides list on its first load, and pushing a ride.",
            "Everything else - the map, the router, the guidance, the "
            "recording, every export and every setting - is on the phone.",
        ]),

        ui.button("Back to the map",
                  lambda e: go("ride") if callable(go) else None,
                  icon=ft.Icons.MAP, primary=True, expand=True),
        ft.Container(height=12),
    ])
