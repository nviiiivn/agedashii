"""How this works - the wiki, inside the app that needs it.

Her words: "each of these independent apps needs their own icons/labels/readme
wikis INSIDE on how to use". A README in the repo is for whoever builds the
app; this is for whoever is holding it, offline, wondering why the checklist
will not tick.

Rules for what goes in here, because a help page that drifts is worse than
none:

* **Plain sentences.** No marketing, no exclamation marks, and no claim the
  app does not make good on. If something needs the server, it says so.
* **Only what is true of this build.** Every line below describes behaviour
  that exists in this checkout; the offline paragraph in particular quotes
  the numbers the tests assert, so a change that breaks it breaks a test
  first.
* **Nothing that needs the network.** It is static text and themed icons, so
  it reads exactly the same in a tunnel - which is the most likely place to
  be reading it.
"""
import flet as ft

from agedashii_qi import PAL
from agedashii_washi.widgets import PAD, card, sub, title

from .. import ui as U

# One place for the whole page's content, so a section is a data change
# rather than a layout change: (icon, heading, [paragraph, ...]).
TOPICS = [
    (ft.Icons.CHECK_CIRCLE, "Wat2Do - the day", [
        "One day at a time. The arrows move a day either way and the calendar "
        "button comes back to today.",
        "The checklist comes from a template you edit once, not from a list "
        "you rebuild every morning. Tap the notepad on the date row to edit "
        "it: one task per line, and a line starting with \"> \" is a sub-task "
        "of the line above it. A parent ticks when all of its sub-tasks do.",
        "It is the same checklist the website shows. A box ticked here is "
        "ticked there, because both write the same record under the same "
        "keys.",
    ]),
    (ft.Icons.MONITOR_HEART, "Pain and spoons", [
        "Two sliders: pain out of ten, spoons out of five. They are saved "
        "when you let go, not while you drag - the number on screen is what "
        "your thumb is doing until it has been written down.",
        "They are not just a diary. Low spoons keeps the suggestion close to "
        "home, high pain makes it an indoor day, and the mission generator "
        "rolls a smaller radius for both. Settings can turn that steering off "
        "and keep the recording, or turn the whole card off.",
        "A day you have not answered stays unanswered. Nothing is assumed "
        "from yesterday.",
    ]),
    (ft.Icons.CASINO, "The mission generator", [
        "\"Roll a mission\" picks somewhere real to go and gives you a reason "
        "to go there: a venue from the map, three to six photo subjects off "
        "your own deck, and one constraint - only red things, ten shots max.",
        "The deck is yours and is edited from the notepad next to the button. "
        "How many subjects a roll deals is a setting.",
        "It needs the server: the venues come from an Overpass search and the "
        "deck is stored with the day's records.",
    ]),
    (ft.Icons.EXPLORE, "Future Finds - someday", [
        "The other half of the same question. Wat2Do plans today; Future "
        "Finds is the list of places worth a ride someday, and the mission "
        "generator draws on the same spots.",
        "Pick categories and a radius - 25, 50 or 100 miles - and scan. What "
        "comes back is a server-side table of gems, not a live search, so a "
        "scan you have run once is still there with no signal.",
        "Saving one puts it on the someday list. Saving with no signal queues "
        "it, exactly like a tick.",
    ]),
    (ft.Icons.CLOUD_UPLOAD, "What works with no signal", [
        "The theme, the corner radius, the glow, the CRT overlay, the card "
        "layout and every setting live on this device. They never ask the "
        "network anything, so the app looks and lays out the same with the "
        "radio off.",
        "Anything you change is kept. A tick made in a tunnel is applied to "
        "this device's copy and put in a queue in the same breath - the next "
        "read sees it immediately - and sent when there is signal again.",
        "The queue is coalesced per day. An afternoon of ticking boxes with "
        "no signal becomes one write carrying the final state of that day, "
        "not forty: 40 ticks, 1 queued item.",
        "The cloud in the topbar is what says something is waiting. Tap it to "
        "try again now. It is not there when the queue is empty, because a "
        "badge that is always on screen stops being read.",
    ]),
    (ft.Icons.DNS, "What needs the server", [
        "The day's record, the checklist template, the mission deck, the "
        "weather strip, the map's POI scan, the route to a place, and all of "
        "Future Finds.",
        "With no server and nothing cached, each of those says so rather than "
        "showing a plausible empty list. Nothing here invents a number.",
        "Settings > Server is where the address lives, with a Check button "
        "that says which kind of failure it was - unreachable, behind a login, "
        "or blocked at the edge. Those need different fixes.",
    ]),
    (ft.Icons.PLACE, "Where distances come from", [
        "This app does not ask the device where it is, and it asks for no "
        "location permission. Everything is measured from one point you set "
        "in Settings, which starts as home.",
        "Change it and the map's marker, the POI scan, the mission radius and "
        "every distance in a list move with it.",
    ]),
    (ft.Icons.DASHBOARD_CUSTOMIZE, "Rearranging the cards", [
        "The button in the topbar with the four squares turns Wat2Do into "
        "something you can arrange: drag a card by its handle to reorder, drag "
        "its grip to make it narrower, tap to cycle its height, and hide the "
        "ones you do not use.",
        "Every drag saves as it happens. Done just leaves the mode.",
        "It is stored on this device, so it is not undone by the website.",
    ]),
    (ft.Icons.EXPLORE, "Getting around", [
        "The bar along the bottom is the navigation: Wat2Do, Future Finds, "
        "Settings. Present and Future also have their own tabs at the top of "
        "the page, which are the same two destinations reached a second way.",
        "The hamburger menu is off by default and can be switched on in "
        "Settings. With the bottom bar switched off it comes back on its own, "
        "because something has to reach every surface.",
    ]),
]


def build(page, go=None):
    """The whole help surface. Static: it reads the same with no signal."""
    p = PAL()

    def topic(icon, heading, paragraphs):
        return card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[U.glyph(icon, size=17, color=p["accent"]),
                             title(heading, 14)]),
            *[ft.Text(text, size=12, color=p["text2"], selectable=True)
              for text in paragraphs],
        ]))

    head = [ft.Container(padding=PAD(2, 2), content=ft.Column(spacing=4, controls=[
        title("How this works", 18),
        sub("What each part of Watudo does, what keeps working with no signal, "
            "and what does not. Two minutes, and it is all true of this build.",
            11),
    ]))]
    if go:
        head.append(U.link_row("Back to Settings",
                               "Everything described here has a control there.",
                               ft.Icons.SETTINGS, lambda: go("settings")))
    return ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO,
                     controls=head + [topic(*t) for t in TOPICS])
