import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs
from ._forms import Writer, offline_banner, rebuilder
from . import foodzen as fzpage

def build(page):
    """Tools is a FOLDER; FoodZen is a PAGE inside it with its own TABS.

    This was one flat strip - Feeds | Vault | Journal | FoodZen | EatZen |
    BPM - which put EatZen and BPM next to Journal instead of inside
    FoodZen, and had no room for the website's meals, scrape and expenses
    tabs at all.
    """
    return _tabs(page, [("Feeds", _feeds()), ("Vault", _vault(page)),
                        ("Journal", _journal(page)),
                        ("FoodZen", fzpage.build(page))], key="tools")

def _feeds():
    p = PAL()
    def row(it):
        c = {"riders-share": p["orange"], "cyclefish": p["cyan"], "lightning": p["red"],
             "sf-mc": p["purple"]}.get(it["src"], p["accent"])
        return card(ft.Column(spacing=5, controls=[
            ft.Row([chip(it["tag"], c, small=True), ft.Container(expand=True), sub(it["meta"], 10)]),
            ft.Text(it["title"], size=13, weight=ft.FontWeight.W_500, color=p["text"]),
        ]), pad=10)
    return ft.Column(spacing=8, controls=[row(i) for i in M.FEEDS])

def _vault(page):
    """Write a note into a machine's vault.

    Previously three hardcoded filenames that did nothing. The server's
    /api/vault/save needs a machine id and a path and takes the note body
    raw, so this is a real composer rather than a browser: listing a remote
    vault needs an SSH round trip the app has no endpoint for yet, and
    pretending to list files would be the same lie the old placeholder told.
    """
    p = PAL()
    w = Writer(page, rebuilder(page, build))
    live = M.STATUS_LIVE()
    machines = [m for m in (M.MACHINES or []) if m.get("id")]

    if not live or not machines:
        return ft.Column(spacing=8, controls=[
            offline_banner(p, "the vault") if not live else
            card(sub("No machines configured on the server.", 11), pad=12)])

    host = ft.Dropdown(
        value=machines[0]["id"], dense=True, filled=True, text_size=12, width=170,
        border_color=p["border"], color=p["text"],
        options=[ft.dropdown.Option(key=m["id"], text=m.get("display", m["id"]))
                 for m in machines])
    path = ft.TextField(hint_text="notes/spark-plugs.md", dense=True, filled=True,
                        text_size=12, expand=True, border_color=p["border"],
                        focused_border_color=p["accent"], color=p["text"])
    body = ft.TextField(hint_text="note body...", multiline=True, min_lines=6,
                        max_lines=20, dense=True, filled=True, text_size=12,
                        border_color=p["border"], focused_border_color=p["accent"],
                        color=p["text"])

    async def save(e):
        rel = (path.value or "").strip()
        if not rel:
            w.report(False, "path required")
            page.update()
            return
        w.busy()
        ok, msg = M.vault_save(host.value, rel, body.value or "")
        w.report(ok, msg)
        page.update()

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            title("Write a note", 14),
            ft.Row(spacing=6, controls=[host, path]),
            body,
            ft.Row([ft.FilledButton("Save to vault", height=34, on_click=save)]),
            sub("Saves over SSH to the selected machine's vault path.", 10),
        ])),
        w.control,
    ])

def _journal(page):
    """Journal entries: create, open to edit, save, delete - all server-side."""
    p = PAL()
    w = Writer(page, rebuilder(page, build))
    entries = M.JOURNAL or []
    live = M.STATUS_LIVE()

    # The mock shape has no path, so only real entries can be opened or deleted.
    def row(j):
        rel = j.get("path")
        head = ft.Row([chip(j["cat"], p["purple"], small=True), ft.Container(expand=True),
                       sub(j.get("date", ""), 10)])
        body = [head, ft.Text(j["title"], size=13, weight=ft.FontWeight.W_500,
                              color=p["text"])]
        if rel and live:
            editor = ft.Column(spacing=6, visible=False)

            async def open_entry(e, rel=rel, editor=editor):
                if editor.visible:
                    editor.visible = False
                    editor.controls.clear()
                    page.update()
                    return
                content = M.journal_read(rel)
                if content is None:
                    w.report(False, "could not read entry")
                    page.update()
                    return
                field = ft.TextField(value=content, multiline=True, min_lines=6,
                                     max_lines=20, text_size=12, dense=True,
                                     filled=True, border_color=p["border"],
                                     focused_border_color=p["accent"], color=p["text"])

                async def save(e2, rel=rel, field=field):
                    w.busy()
                    ok, msg = M.journal_save(rel, field.value or "")
                    w.report(ok, msg)
                    page.update()

                editor.controls[:] = [field, ft.Row(spacing=8, controls=[
                    ft.FilledButton("Save", height=32, on_click=save),
                    ft.OutlinedButton("Delete", height=32,
                                      on_click=w.run(M.journal_delete, rel)),
                ])]
                editor.visible = True
                page.update()

            head.controls.insert(1, ft.IconButton(ft.Icons.EDIT_NOTE, icon_size=16,
                                                  icon_color=p["text2"],
                                                  on_click=open_entry))
            body.append(editor)
        return card(ft.Column(spacing=6, controls=body), pad=10)

    cat = ft.Dropdown(
        value=M.JOURNAL_CATEGORIES[0], dense=True, filled=True, text_size=12, width=150,
        border_color=p["border"], color=p["text"],
        options=[ft.dropdown.Option(c) for c in M.JOURNAL_CATEGORIES])

    def make(title_text):
        ok, res = M.journal_new(cat.value, title_text)
        return ok, (f"created {res}" if ok else res)

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        *([] if live else [offline_banner(p, "the journal")]),
        card(ft.Column(spacing=8, controls=[
            title("New entry", 14),
            ft.Row(spacing=6, controls=[cat]),
            w.entry("entry title...", make),
        ])),
        *[row(j) for j in entries],
        w.control,
    ])

def _foodzen(page):
    """Editable FoodZen: grocery list, needs and pantry all write to the server.

    Every write is confirmed before the row changes on screen. Optimistic UI
    would be worse here than a brief pause: the same JSON files are edited from
    the website, and a checkbox that ticks locally but never reached the server
    is exactly the kind of quiet lie that makes the app untrustworthy.
    """
    p = PAL()
    fz = M.FOODZEN
    live = bool(fz.get("items") or fz.get("need_items") or fz.get("pantry_items")) \
        or fz.get("list_id") is not None

    status = ft.Text("", size=11, color=p["text2"])

    def rebuild():
        M.refresh()
        page.controls.clear()
        page.add(build(page))
        page.update()

    def report(ok, msg):
        status.value = msg if ok else f"not saved - {msg}"
        status.color = p["text2"] if ok else p["pink"]

    def run(fn, *a):
        """Call a write, report the result, and re-read from the server."""
        async def handler(e):
            status.value = "saving..."
            page.update()
            ok, msg = fn(*a)
            report(ok, msg)
            if ok:
                rebuild()
            else:
                page.update()
        return handler

    def add_row(hint, fn):
        field = ft.TextField(hint_text=hint, dense=True, filled=True, text_size=12,
                             expand=True, border_color=p["border"],
                             focused_border_color=p["accent"], color=p["text"])

        async def submit(e):
            name = (field.value or "").strip()
            if not name:
                return
            status.value = "saving..."
            page.update()
            ok, msg = fn(name)
            report(ok, msg)
            if ok:
                field.value = ""
                rebuild()
            else:
                page.update()

        field.on_submit = submit
        return ft.Row(spacing=6, controls=[
            field, ft.IconButton(ft.Icons.ADD, icon_color=p["accent"], on_click=submit)])

    def list_row(it):
        name = it.get("name", "")
        done = bool(it.get("done"))
        qty = it.get("qty") or 1
        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.IconButton(
                ft.Icons.CHECK_BOX if done else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                icon_size=17, icon_color=p["green"] if done else p["text2"],
                on_click=run(M.foodzen_toggle, name, not done)),
            ft.Text(f"{name}{'' if qty in (1, None) else f'  x{qty}'}", size=12,
                    color=p["text2"] if done else p["text"], expand=True,
                    style=ft.TextStyle(
                        decoration=ft.TextDecoration.LINE_THROUGH) if done else None),
            chip(it.get("cat", ""), p["accent"], small=True) if it.get("cat") else ft.Container(),
            ft.IconButton(ft.Icons.CLOSE, icon_size=15, icon_color=p["text2"],
                          on_click=run(M.foodzen_remove, name)),
        ])

    def simple_row(name, remove_fn, cat=None):
        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text(name, size=12, color=p["text"], expand=True),
            chip(cat, p["purple"], small=True) if cat else ft.Container(),
            ft.IconButton(ft.Icons.CLOSE, icon_size=15, icon_color=p["text2"],
                          on_click=run(remove_fn, name)),
        ])

    items = fz.get("items") or []
    need_items = fz.get("need_items") or []
    pantry_items = fz.get("pantry_items") or []

    banner = []
    if not live:
        banner = [card(ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=p["yellow"]),
            ft.Text("Showing sample data - not connected to the server. "
                    "Anything added here will not save.", size=11,
                    color=p["text2"], expand=True)]), pad=10)]

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        *banner,
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("Grocery list"), ft.Container(expand=True),
                    chip(fz.get("list_name") or "new list", p["orange"], small=True)]),
            *( [list_row(i) for i in items] if items
               else [sub("empty - add something below", 11)] ),
            add_row("add an item...", M.foodzen_add),
        ])),
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("Needs", 13), ft.Container(expand=True),
                    ft.TextButton("move all to list", on_click=run(M.foodzen_need_to_list))
                    if need_items else ft.Container()]),
            *( [simple_row(n.get("name", ""), M.foodzen_need_remove, n.get("cat"))
                for n in need_items] if need_items else [sub("nothing needed", 11)] ),
            add_row("need to restock...", M.foodzen_need_add),
        ])),
        card(ft.Column(spacing=6, controls=[
            title("Pantry", 13),
            *( [simple_row(x.get("name", ""), M.foodzen_pantry_remove, x.get("cat"))
                for x in pantry_items] if pantry_items else [sub("pantry empty", 11)] ),
            add_row("already have...", M.foodzen_pantry_add),
        ])),
        ft.Container(padding=PAD(0, 4), content=status),
    ])
