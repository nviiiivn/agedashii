import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs
from . import future

def build(page):
    # XNAV moved to Trips, where the rest of the ride pipeline lives; the
    # copy here was a mock that could never record anything.
    #
    # Future Finds lives here rather than as a fourth tab on Wat2Do, where
    # the website keeps it, for one reason: a new top-level page has to be
    # declared in prefs.PAGE_KEYS or the layout editor cannot show it, and a
    # tab inside Wat2Do would sit under that page's own date/checklist state.
    # Extras is where the app already puts the things that are their own
    # thing. `key` makes the tab bar remember which one you were on across
    # the repaint every write triggers - without it, saving a gem bounced
    # you back to Projects.
    return _tabs(page, [("Projects", _projects()), ("AI", _ai(page)),
                        ("Future", future.build(page)),
                        ("Obligations", _obl())], key="extras")

def _projects():
    p = PAL()
    def row(pr):
        return card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.FOLDER_COPY, color=p["orange"], size=18),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(pr["name"], size=13, weight=ft.FontWeight.W_500, color=p["text"]),
                sub(f"{pr['lang']} · {pr['desc']}", 10)]),
            ft.Icon(ft.Icons.OPEN_IN_NEW, size=14, color=p["text2"]),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(x) for x in M.PROJECTS])

def _ai(page):
    p = PAL()
    msgs = list(M.AI_CHAT)
    list_ = ft.Column(spacing=8, controls=[])
    entry = ft.TextField(hint_text="Message…", border_radius=12, bgcolor=p["bg2"],
                         border_color=p["border"], color=p["text"], dense=True, expand=True)

    def bubble(m):
        mine = m["role"] == "user"
        return ft.Row(alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START, controls=[
            ft.Container(width=280, padding=PAD(10, 12), border_radius=14,
                         bgcolor=p["accent"] if mine else p["surface"],
                         border=BRD(p["border"], 0 if mine else 1),
                         content=ft.Text(m["text"], size=13, color="#ffffff" if mine else p["text"])),
        ])

    def render(extra=None):
        list_.controls = [bubble(m) for m in msgs] + ([extra] if extra else [])
        try:
            if list_.page:
                list_.update()
        except RuntimeError:
            pass

    def send(e):
        txt = (entry.value or "").strip()
        if not txt:
            return
        entry.value = ""
        entry.update()
        msgs.append({"role": "user", "text": txt})
        render(ft.Row(controls=[ft.Text("…", size=13, color=p["text2"])], alignment=ft.MainAxisAlignment.START))
        model = M.ai_model()
        reply = M.ai_chat("openrouter", model, [{"role": "user", "text": m["text"]} for m in msgs[-8:]])
        msgs.append({"role": "ai", "text": reply or "(offline — is the Pi's dashbored up?)"})
        render()

    entry.on_submit = send
    render()
    return ft.Column(spacing=8, controls=[
        ft.Row(spacing=6, controls=[chip("provider: openrouter", p["purple"], small=True),
                                    chip("model: auto", p["cyan"], small=True)]),
        ft.Container(expand=True, content=ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True,
                                                    controls=[list_])),
        ft.Row([entry, ft.IconButton(ft.Icons.SEND, icon_color=p["accent"], on_click=send)]),
    ])

def _xnav():
    p = PAL()
    return ft.Column(spacing=8, controls=[
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("XNAV", 14), ft.Container(expand=True),
                    chip("2 rides loaded", p["green"], small=True)]),
            ft.Container(height=130, border_radius=10, bgcolor=p["bg2"], border=BRD(p["border"], 1),
                         alignment=ft.Alignment(0, 0),
                         content=ft.Column(spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                             ft.Icon(ft.Icons.EXPLORE, size=38, color=p["accent"]),
                             sub("GPX explorer — map + elevation + speed", 11)])),
        ])),
    ])

def _obl():
    p = PAL()
    def row(o):
        return card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.ERROR_OUTLINE if o["urg"]=='high' else ft.Icons.CHECK_CIRCLE_OUTLINE,
                    color=p["red"] if o["urg"]=='high' else p["text2"], size=18),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(o["t"], size=13, color=p["text"]),
                sub(o["due"], 10)]),
        ]), pad=10)
    items = M.OBLIGATIONS if isinstance(M.OBLIGATIONS, list) else []
    if not items:
        # There is no obligations store on the server - the website's own
        # Obligations view says "coming soon". This used to show invented
        # ones instead: "Renew motorcycle insurance, Sep 12" and a DMV
        # appointment. Those are deadlines you might act on, and they were
        # never real.
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Nothing tracked yet", 14),
            sub("There is no obligations store on the server yet, so this "
                "shows nothing rather than inventing deadlines.", 11)]),
            pad=14)])
    return ft.Column(spacing=8, controls=[row(o) for o in items])
