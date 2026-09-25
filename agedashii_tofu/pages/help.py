"""How this works: the wiki, inside the app, for the app.

"each of these independent apps needs their own icons/labels/readme wikis
INSIDE on how to use/settings menu etc..."

Moto, Watudo and Foodzen each got one of these; Tofu was the only app
without, which is backwards - it is the one with the machines and the power
buttons on it.

The README says most of this and lives in a git checkout, which is no use
on a phone. Written as plain sentences: the interesting thing about this
app is which parts keep working with the radio off, and a reader who has
been told everything is amazing cannot tell which those are.

Called **Help** wherever it is navigated to - a drawer row has no room for
four words - and **How this works** once you are on it.
"""
import flet as ft

from .. import config
from ..ui import PAD, PAL, RAD, card, sub, title

SECTIONS = [
    ("What Tofu is", [
        "The whole dashboard: the machines, the services and containers on "
        "them, the feeds, the vault, the journal, the repos, the AI chats "
        "and the obligations.",
        "Trips, Garage, Wat2Do and FoodZen used to be here too. They are "
        "their own apps now - Agedashii Moto, Watudo and Foodzen - and each "
        "one installs beside this one.",
    ]),
    ("What works with no signal", [
        "The theme, the corner radius, the accent glow, the CRT overlay, "
        "the card layout of every surface and every preference. All of it "
        "is read from this device at launch and drawn with the radio off.",
        "Server data goes through a cache and an outbox on the device. A "
        "read answers from that cache; a write made offline is queued, is "
        "visible to the very next read, and goes out when the server comes "
        "back.",
        "A write never loses a retry to being offline - being unreachable "
        "is not a failed attempt.",
    ]),
    ("What needs the server", [
        "The machine stats, the container and service lists, and the power "
        "buttons. There is nothing to queue and replay for those - a "
        "reboot you asked for an hour ago is not one you still want.",
        "The AI chats, for the same reason.",
        "Each of these says why it cannot answer rather than showing a "
        "zero, which would read as a machine that is up and idle.",
    ]),
    ("Where the surfaces went", [
        "Tools holds Feeds, Vault, Journal, Projects, AI and Obligations. "
        "The last three were their own page, Extras, until they moved here.",
        "Weather moved to Moto and Watudo - the two apps that need to know "
        "whether to go outside.",
        "Trip Reports and Mechanicry left the Journal for Moto's Garage, "
        "under Mechanic.",
    ]),
    ("Pointing it at a server", [
        "Settings > Server. The address is saved on this device and reused "
        "next launch, with no rebuild.",
        "On startup it tries each known address and takes the first that "
        "answers, so a stale saved address falls back rather than hanging.",
        "In a browser it cannot reach a LAN address at all: an https page "
        "is not allowed to fetch http. The installed app has no such limit.",
    ]),
]


def build(page):
    p = PAL()
    rows = [title("How this works")]
    for heading, lines in SECTIONS:
        rows.append(card(ft.Column(spacing=8, controls=[
            sub(heading.upper(), 10),
            *[ft.Text(line, size=12.5, color=p["text2"]) for line in lines],
        ])))
    rows.append(ft.Container(
        padding=PAD(8, 10), border_radius=RAD(8),
        content=ft.Text(f"Server: {config.SERVER_URL}", size=11,
                        color=p["text3"])))
    return ft.Column(spacing=12, expand=True, scroll=ft.ScrollMode.AUTO,
                     controls=rows)
