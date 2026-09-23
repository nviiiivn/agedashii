"""How this works - the manual, inside the app.

Her ask: "each of these independent apps needs their own icons/labels/readme
wikis INSIDE on how to use". A README in the repo is for whoever builds the
thing; this is for whoever is standing in a shop holding the phone.

Rules it is written under, because a help screen that oversells is worse than
none at all:

* Plain sentences. No marketing, no exclamation marks, no "simply".
* It says what does *not* work as readily as what does - which screens need
  the server, what happens with the radio off, and which number is a guess.
* Every claim here is checkable against the code beside it. If a line stops
  being true, it is a bug in this file, not a nice-to-have.

Reached from Settings rather than from the navigation: it is the thing you
read once and then rarely, and a four-item bottom bar has better uses for its
fourth slot. `build()` takes a `back` callback so the surface that opened it
decides where "back" goes.
"""
import flet as ft

from agedashii_qi import PAL
from agedashii_washi.widgets import PAD, card, sub

from .. import glyphs


def _para(text):
    return ft.Text(text, size=12, color=PAL()["text2"], selectable=True)


def _point(label, text):
    """One labelled line - the shape the whole document is written in."""
    p = PAL()
    return ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                  controls=[
                      ft.Container(width=8, height=8, border_radius=4,
                                   margin=ft.Margin(0, 5, 0, 0), bgcolor=p["accent"]),
                      ft.Column(spacing=1, expand=True, controls=[
                          ft.Text(label, size=12, weight=ft.FontWeight.W_600,
                                  color=p["text"]),
                          ft.Text(text, size=11.5, color=p["text2"], selectable=True),
                      ])])


def _section(icon, heading, *rows):
    return card(ft.Column(spacing=9, controls=[glyphs.head(icon, heading, 14), *rows]))


def build(page, back=None):
    """The help surface. `back` is called by the Back button, if given."""
    p = PAL()

    controls = [
        card(ft.Column(spacing=6, controls=[
            glyphs.head(glyphs.ICON_HELP, "How this works", 15),
            _para("Foodzen is the grocery list, what you ate, and what your "
                  "blood pressure did - the same three things the website "
                  "keeps, on the phone. It is a client for the dashbored "
                  "server at home; it holds its own copy of everything so it "
                  "keeps working when it cannot reach it."),
        ])),

        _section(glyphs.ICON_GROCERY, "The screens",
                 _point("FoodZen",
                        "Six tabs. Grocery is the list, the catalogue to tap "
                        "from, and the restock pile. Meals is the pantry, the "
                        "recipe vault and the week's plan. Scrape pulls a "
                        "recipe off a URL. Expenses turns a receipt into rows "
                        "and shows what you spend and buy most."),
                 _point("EatZen",
                        "One meal at a time: photograph it, describe it, or "
                        "type the rows yourself. It adds up sodium against "
                        "your daily target and logs the day."),
                 _point("BPM",
                        "Blood pressure readings, with the day's sodium shown "
                        "beside each one, and the average of the last seven."),
                 _point("Settings",
                        "The server address, every theme, the shape of the "
                        "app, the sodium target, and the switch for this "
                        "menu bar and the hamburger.")),

        _section(ft.Icons.WIFI_OFF, "Offline and online",
                 _point("Works with the radio off",
                        "Themes, corner rounding, the glow, the CRT overlay, "
                        "units, the sodium target, the layout of the cards "
                        "and which navigation you use. None of those is a "
                        "request; they are stored on this device."),
                 _point("Edits are queued, not lost",
                        "Ticking an item off the grocery list, logging a "
                        "meal or adding a reading goes into an outbox and is "
                        "sent when there is signal. The chip in the top bar "
                        "says how many are waiting - tap it to try now."),
                 _point("Needs the server",
                        "Looking a food up, the photo and label readers, "
                        "scraping a recipe and parsing a receipt all happen "
                        "on the server. Off the LAN, connect the VPN first: "
                        "the public hostname is behind a login and will not "
                        "answer the app.")),

        _section(glyphs.ICON_CAMERA, "From a photo to a number",
                 _point("1. The photo is shrunk here",
                        "Long side down to 1280px on the phone, before "
                        "anything is sent."),
                 _point("2. A vision model on Tower names the food",
                        "It says what is on the plate and estimates how much "
                        "of it there is. That is all it does."),
                 _point("3. The numbers come from the food table",
                        "Sodium and sugar are looked up in the server's own "
                        "table - USDA rows plus any food you have added from "
                        "a label - exactly as they are when you type the meal "
                        "in by hand. The model is never asked for a nutrient "
                        "figure and never states one."),
                 _point("4. You check it",
                        "The rows land editable. A number you type always "
                        "wins over a looked-up one, because you are reading a "
                        "label and the table holds an average."),
                 sub("The one exception is the label reader in \"Add a food "
                     "from the label\": there the model is transcribing print "
                     "rather than recalling an average, so it may report the "
                     "figures - and you are asked to check them against the "
                     "bag.", 10.5)),

        _section(glyphs.ICON_SODIUM, "What the marks mean",
                 _point("* after a row or a total",
                        "The portion was estimated from the photo, not "
                        "counted. Edit the amount and the star goes - it was "
                        "only ever standing in for \"nobody has looked at "
                        "this yet\". A total carries the star if any row it "
                        "is built from does."),
                 _point("IDK",
                        "A row marked IDK contributes nothing and is counted "
                        "as unknown in the detail line, rather than quietly "
                        "counting as zero and making the day look better "
                        "than it was."),
                 _point("\"no match\"",
                        "The table has no row for that name. Type the sodium "
                        "yourself, or use the label button on the row to "
                        "teach it the food once and for all."),
                 _point("The food emoji",
                        "Decoration, and nothing more. It is picked from the "
                        "name by a small table; a food it does not know gets "
                        "a plain plate. It is never part of a calculation.")),

        _section(glyphs.ICON_SETTINGS, "Where things are kept",
                 _point("On this device",
                        "Theme, appearance, navigation, units, the sodium "
                        "target and the card layout. They never leave the "
                        "phone and never need a connection."),
                 _point("On the server",
                        "The grocery list, the pantry, recipes, purchases, "
                        "every logged meal and every blood pressure reading. "
                        "The website edits the same records, so a change made "
                        "in either place shows up in the other."),
                 _point("If storage is unavailable",
                        "Settings says so, in Diagnostics. Preferences still "
                        "work for the session and reset when the app "
                        "restarts.")),
    ]

    if back:
        controls.append(ft.Container(
            padding=PAD(4, 0),
            content=ft.Row(spacing=8, controls=[
                ft.FilledButton(content="Back to Settings", height=36,
                                icon=ft.Icons.ARROW_BACK,
                                on_click=lambda e: back())])))

    controls.append(ft.Container(
        padding=PAD(2, 0),
        content=ft.Text("Agedashii Foodzen - the grocery list, what you ate, "
                        "and what your blood pressure did.",
                        size=10, color=p["text3"])))

    return ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO,
                     controls=controls)
