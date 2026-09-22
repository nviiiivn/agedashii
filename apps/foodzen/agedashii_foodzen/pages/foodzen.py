"""FoodZen, with the six sub-tabs the website has.

The app used to show one flat strip - Feeds | Vault | Journal | FoodZen |
EatZen | BPM - so EatZen and BPM were siblings of Journal rather than parts
of FoodZen, and three whole website tabs (meals, scrape, expenses) did not
exist at all.

The shape is now the one she asked for:

    FOLDER (Tools) > PAGE (FoodZen) > TAB (EatZen)

Every tab here reads the collections `api.foodzen()` returns and writes
through the outbox, so it all works offline and syncs later.
"""
import flet as ft

from agedashii_washi import api as M
from agedashii_washi import panels
from agedashii_washi.ui_compat import PAD, PAL, RAD, S, card, chip, sub, title
from . import eatzen
from agedashii_washi.surface import _tabs

from .. import glyphs

# Which folded-away sections are open. Module level so a tick, a delete or an
# added item - every one of which repaints the tab - does not shut the
# catalogue you were halfway down.
_OPEN = {"browse": False}


def _repaint(page):
    """Repaint from what is already held; deliberately not a re-read.

    Opening a section is a layout change. `_writer`'s rebuild calls
    `M.refresh()`, which marks every cached answer in the app stale - the
    right thing after a write, and a network round trip behind a disclosure
    triangle otherwise.
    """
    fn = getattr(S, "rerender", None)
    if callable(fn):
        fn()
        return
    try:
        page.update()
    except Exception:              # noqa: BLE001 - not mounted, or torn down
        pass


def build(page):
    """The FoodZen page: six tabs, matching the website.

    Each tab is a callable, so only the one on screen is built. All six used
    to be built whenever Tools painted, whether or not FoodZen was even the
    tab you were looking at - 503 controls, 269 of them the Browse wall
    inside Grocery. See ``lab._tabs``.
    """
    return _tabs(page, [
        ("Grocery", lambda: _grocery(page)),
        ("Meals", lambda: _meals(page)),
        ("Scrape", lambda: _scrape(page)),
        ("Expenses", lambda: _expenses(page)),
        ("EatZen", lambda: eatzen.build_eatzen(page)),
        ("BPM", lambda: eatzen.build_bpm(page)),
    ], key="foodzen")


def _state():
    fz = M.FOODZEN
    return fz if isinstance(fz, dict) else {}


def _writer(page):
    """Shared save/report/rebuild plumbing for the editable tabs.

    Writes are confirmed before the row changes on screen. Optimistic UI
    would be worse here: the same JSON files are edited from the website, and
    a checkbox that ticks locally but never reached the server is exactly the
    quiet lie that makes the app untrustworthy.
    """
    p = PAL()
    status = ft.Text("", size=11, color=p["text2"])

    def rebuild():
        M.refresh()
        if getattr(page, "rerender", None):
            page.rerender()
        else:
            page.update()

    def report(ok, msg):
        status.value = msg if ok else f"not saved - {msg}"
        status.color = p["text2"] if ok else p["pink"]

    def run(fn, *a):
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

    return status, run, add_row


# ------------------------------------------------------------------ grocery

def _grocery(page):
    """List templates, the list itself, browse, and need."""
    p = PAL()
    fz = _state()
    status, run, add_row = _writer(page)

    items = fz.get("items") or []
    need_items = fz.get("need_items") or []
    templates = fz.get("templates") or []

    def list_row(it):
        name = it.get("name", "")
        done = bool(it.get("done"))
        qty = it.get("qty") or 1
        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.IconButton(
                ft.Icons.CHECK_BOX if done else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                icon_size=17, icon_color=p["green"] if done else p["text2"],
                on_click=run(M.foodzen_toggle, name, not done)),
            # The item's own glyph - content, so an emoji. A neutral plate
            # for anything the table does not know; never a near-miss. See
            # glyphs.py for the rule and why it matters on a shopping list.
            ft.Text(glyphs.food_emoji(name), size=14),
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
            ft.Text(glyphs.food_emoji(name), size=14),
            ft.Text(name, size=12, color=p["text"], expand=True),
            chip(cat, p["purple"], small=True) if cat else ft.Container(),
            ft.IconButton(ft.Icons.CLOSE, icon_size=15, icon_color=p["text2"],
                          on_click=run(remove_fn, name)),
        ])

    def template_row(t):
        names = [str(i.get("name", i) if isinstance(i, dict) else i)
                 for i in (t.get("items") or [])]
        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(t.get("name", "untitled"), size=12, color=p["text"]),
                sub(f"{len(names)} items" + (" · " + ", ".join(names[:4]) if names else ""), 10),
            ]),
            ft.TextButton("add all", on_click=run(M.foodzen_template_apply, t.get("id"))),
        ])

    def templates_card():
        return card(ft.Column(spacing=6, controls=[
            glyphs.head(glyphs.ICON_TEMPLATES, "List templates", 13,
                        trailing=ft.TextButton(content="save current list",
                                               on_click=run(M.foodzen_template_save))
                        if items else None),
            *([template_row(t) for t in templates] if templates
              else [sub("no templates yet - build a list, then save it as one", 11)]),
        ]))

    def list_card():
        return card(ft.Column(spacing=6, controls=[
            glyphs.head(glyphs.ICON_GROCERY, "Grocery list", 16,
                        trailing=chip(fz.get("list_name") or "new list",
                                      p["orange"], small=True)),
            *([list_row(i) for i in items] if items
              else [sub("empty - add something below", 11)]),
            add_row("add an item...", M.foodzen_add),
        ]))

    def need_card():
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Need", 13), ft.Container(expand=True),
                    ft.TextButton("move all to list", on_click=run(M.foodzen_need_to_list))
                    if need_items else ft.Container()]),
            *([simple_row(n.get("name", ""), M.foodzen_need_remove, n.get("cat"))
               for n in need_items] if need_items else [sub("nothing needed", 11)]),
            add_row("need to restock...", M.foodzen_need_add),
        ]))

    return ft.Column(spacing=8, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("foodzen.grocery", {
            "templates": templates_card, "list": list_card,
            "browse": lambda: _browse(page, run), "need": need_card,
        })),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


def _browse(page, run):
    """The catalogue, by section, tap to add - the website's browse wall.

    Chips rather than the website's 231 tiles: a tile grid that size is
    unusable on a phone, and the point of it is adding a common item without
    typing. Height-capped and internally scrolled so it cannot push the rest
    of the tab off the screen.

    Folded away until asked for. 231 buttons is 269 controls - nearly the
    whole of the Grocery tab - built every time anything on the page
    repainted, for a wall you have to scroll a fixed 260px window to use.
    The list you came to read is above it. Open state lives in `_OPEN` so a
    tick, a delete or an added item does not shut it again mid-shop.
    """
    p = PAL()
    from agedashii_washi.foodzen_catalog import CATALOG

    def toggle(e):
        _OPEN["browse"] = not _OPEN["browse"]
        _repaint(page)

    head = ft.Container(
        ink=True, border_radius=RAD(8), on_click=toggle,
        tooltip=("hide the catalogue" if _OPEN["browse"]
                 else "the whole catalogue, tap an item to add it"),
        content=ft.Row([
            title("Browse", 13), ft.Container(expand=True),
            chip(f"{len(CATALOG)} items", p["accent"], small=True),
            ft.Icon(ft.Icons.EXPAND_LESS if _OPEN["browse"]
                    else ft.Icons.EXPAND_MORE, size=20, color=p["text2"]),
        ]))

    if not _OPEN["browse"]:
        return card(head)

    by_section = {}
    for name, section in sorted(CATALOG.items()):
        by_section.setdefault(section, []).append(name)

    sections = []
    for section in sorted(by_section):
        sections.append(ft.Column(spacing=4, controls=[
            sub(section.upper(), 10),
            ft.Row(wrap=True, spacing=4, run_spacing=4, controls=[
                # The label carries the glyph because a TextButton is a
                # string and there is nowhere else to put one. `n` itself,
                # not the label, is what gets added to the list.
                ft.TextButton(glyphs.food_label(n), on_click=run(M.foodzen_add, n))
                for n in by_section[section]]),
        ]))

    return card(ft.Column(spacing=8, controls=[
        head,
        ft.Container(height=260, content=ft.Column(
            spacing=10, scroll=ft.ScrollMode.AUTO, controls=sections)),
    ]))


# -------------------------------------------------------------------- meals

def _meals(page):
    """Pantry, recipe vault and meal plan."""
    p = PAL()
    fz = _state()
    status, run, add_row = _writer(page)
    pantry_items = fz.get("pantry_items") or []
    recipes = fz.get("recipes") or []

    def pantry_row(x):
        name = x.get("name", "") if isinstance(x, dict) else str(x)
        cat = x.get("cat") if isinstance(x, dict) else None
        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Text(glyphs.food_emoji(name), size=14),
            ft.Text(name, size=12, color=p["text"], expand=True),
            chip(cat, p["purple"], small=True) if cat else ft.Container(),
            ft.IconButton(ft.Icons.CLOSE, icon_size=15, icon_color=p["text2"],
                          on_click=run(M.foodzen_pantry_remove, name)),
        ])

    def recipe_row(r):
        ings = r.get("ingredients") or []
        meta = " · ".join(str(x) for x in (
            (f"{r.get('servings')} servings" if r.get("servings") else None),
            (f"{r.get('time_min')} min" if r.get("time_min") else None),
            (f"{len(ings)} ingredients" if ings else None)) if x)
        return card(ft.Column(spacing=4, controls=[
            ft.Row([title(str(r.get("title") or "untitled")[:56], 12),
                    ft.Container(expand=True),
                    ft.TextButton("to list",
                                  on_click=run(M.foodzen_recipe_to_list, r.get("id")))]),
            sub(meta or "no details", 10),
        ]), pad=10)

    def pantry_card():
        return card(ft.Column(spacing=6, controls=[
            title("Pantry", 13),
            *([pantry_row(x) for x in pantry_items] if pantry_items
              else [sub("pantry empty", 11)]),
            add_row("already have...", M.foodzen_pantry_add),
        ]))

    def recipes_card():
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Recipe vault", 13), ft.Container(expand=True),
                    chip(str(len(recipes)), p["accent"], small=True)]),
            *([recipe_row(r) for r in recipes[:40]] if recipes
              else [sub("no recipes saved - use Scrape to pull one in from a URL", 11)]),
        ]))

    return ft.Column(spacing=8, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("foodzen.meals", {
            "pantry": pantry_card, "recipes": recipes_card,
            "mealplan": lambda: _mealplan(recipes),
        })),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


def _mealplan(recipes):
    """Seven days, showing what is planned.

    The plan lives on the recipe as a `day` field rather than in its own
    collection, because /api/foodzen/save has no mealplan kind and inventing
    one here would write something the website cannot read back.
    """
    p = PAL()
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    planned = {}
    for r in recipes:
        d = r.get("day")
        if d in days:
            planned.setdefault(d, []).append(r)

    rows = [ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Container(width=34, content=sub(d, 11)),
        ft.Text(", ".join(str(r.get("title") or "?")[:28] for r in (planned.get(d) or []))
                or "—", size=12,
                color=p["text"] if planned.get(d) else p["text2"], expand=True),
    ]) for d in days]

    return card(ft.Column(spacing=6, controls=[
        ft.Row([title("Meal plan", 13), ft.Container(expand=True),
                chip(f"{sum(len(v) for v in planned.values())} planned",
                     p["green"], small=True)]),
        *rows,
        sub("Mirrors the day set on each recipe.", 10),
    ]))


# ------------------------------------------------------------------- scrape

def _scrape(page):
    """Pull a recipe off a URL and save it to the vault."""
    p = PAL()
    status = ft.Text("", size=11, color=p["text2"])
    url = ft.TextField(hint_text="https://... recipe URL", dense=True, filled=True,
                       text_size=12, expand=True, border_color=p["border"],
                       focused_border_color=p["accent"], color=p["text"])
    result = ft.Column(spacing=6, controls=[])
    got = {}

    def say(msg, bad=False):
        status.value = msg
        status.color = p["pink"] if bad else p["text2"]

    async def save(e):
        if not got:
            return
        ok, msg = M.foodzen_recipe_save(dict(got))
        say(msg if ok else f"not saved - {msg}", not ok)
        if ok:
            M.refresh()
        page.update()

    async def to_list(e):
        ings = (got.get("ingredients") or []) if got else []
        if not ings:
            return
        added = sum(bool(M.foodzen_add(str(i)[:60])[0]) for i in ings)
        say(f"added {added} of {len(ings)} to the grocery list")
        M.refresh()
        page.update()

    async def fetch(e):
        u = (url.value or "").strip()
        if not u:
            say("paste a recipe URL first", True)
            page.update()
            return
        say("fetching...")
        result.controls = []
        page.update()
        data, err = M.foodzen_scrape(u)
        if err:
            say(f"scrape failed - {err}", True)
            page.update()
            return
        got.clear()
        got.update(data or {})
        ings = got.get("ingredients") or []
        result.controls = [
            ft.Text(str(got.get("title") or "untitled"), size=13,
                    weight=ft.FontWeight.W_600, color=p["text"]),
            sub(" · ".join(str(x) for x in (
                (f"{got.get('servings')} servings" if got.get("servings") else None),
                (f"{got.get('time_min') or got.get('time')} min"
                 if (got.get("time_min") or got.get("time")) else None),
                f"{len(ings)} ingredients") if x), 10),
            *[sub("· " + str(i)[:70], 11) for i in ings[:25]],
            ft.Row(spacing=6, controls=[
                ft.FilledButton("Save to vault", height=34, on_click=save),
                ft.OutlinedButton("Ingredients to list", height=34, on_click=to_list),
            ]),
        ]
        say("check it over, then save")
        page.update()

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            title("Recipe scanner", 13),
            sub("Paste a recipe URL. The server fetches and parses it; nothing "
                "is saved until you say so.", 11),
            ft.Row(spacing=6, controls=[
                url, ft.FilledButton("Fetch", height=38, on_click=fetch)]),
        ])),
        card(ft.Column(spacing=6, controls=[title("Result", 13), result])),
        ft.Container(padding=PAD(0, 4), content=status),
    ])


# ----------------------------------------------------------------- expenses

def _expenses(page):
    """Import a receipt, see spending, and see what you buy most often."""
    p = PAL()
    fz = _state()
    purchases = fz.get("purchases") or []
    status = ft.Text("", size=11, color=p["text2"])
    holding = {}

    store = ft.TextField(hint_text="store", dense=True, filled=True, text_size=12,
                         width=140, border_color=p["border"], color=p["text"])
    body = ft.TextField(hint_text="paste the receipt text", dense=True, filled=True,
                        text_size=12, multiline=True, min_lines=3, max_lines=8,
                        expand=True, border_color=p["border"], color=p["text"])
    parsed = ft.Column(spacing=4, controls=[])

    def say(msg, bad=False):
        status.value = msg
        status.color = p["pink"] if bad else p["text2"]

    async def do_save(e):
        if not holding:
            return
        ok, msg = M.foodzen_purchase_save(dict(holding))
        say(msg if ok else f"not saved - {msg}", not ok)
        if ok:
            body.value = ""
            parsed.controls = []
            M.refresh()
        page.update()

    async def do_parse(e):
        txt = (body.value or "").strip()
        if not txt:
            say("paste the receipt text first", True)
            page.update()
            return
        say("parsing...")
        page.update()
        data, err = M.foodzen_receipt(txt, (store.value or "").strip())
        if err:
            say(f"parse failed - {err}", True)
            page.update()
            return
        holding.clear()
        holding.update(data or {})
        rows = holding.get("items") or []
        parsed.controls = ([
            *[ft.Row([ft.Text(str(r.get("name", ""))[:34], size=12, color=p["text"],
                              expand=True),
                      sub(f"x{r.get('qty', 1)}", 10),
                      ft.Text(f"${float(r.get('price') or 0):.2f}", size=12,
                              color=p["text"])])
              for r in rows[:40]],
            ft.Row([ft.Container(expand=True),
                    ft.Text(f"total ${float(holding.get('total') or 0):.2f}", size=12,
                            weight=ft.FontWeight.W_600, color=p["text"])]),
            ft.FilledButton("Save purchase", height=34, on_click=do_save),
        ] if rows else [sub("nothing recognised in that text", 11)])
        say(f"{len(rows)} lines parsed - check them, then save")
        page.update()

    total = 0.0
    by_month, freq = {}, {}
    for pur in purchases:
        try:
            t = float(pur.get("total") or 0)
        except (TypeError, ValueError):
            t = 0.0
        total += t
        month = str(pur.get("date") or "")[:7]
        if month:
            by_month[month] = by_month.get(month, 0.0) + t
        for it in (pur.get("items") or []):
            n = str(it.get("name", "")).strip().lower()
            if n:
                freq[n] = freq.get(n, 0) + 1

    spend_rows = [ft.Row([ft.Text(m, size=12, color=p["text"], expand=True),
                          ft.Text(f"${v:,.2f}", size=12, color=p["text"])])
                  for m, v in sorted(by_month.items(), reverse=True)[:12]]
    radar = [ft.Row([ft.Text(n[:30], size=12, color=p["text"], expand=True),
                     chip(f"{c}x", p["orange"], small=True)])
             for n, c in sorted(freq.items(), key=lambda kv: -kv[1])[:15]]

    def receipt_card():
        return card(ft.Column(spacing=8, controls=[
            title("Import receipt", 13),
            ft.Row(spacing=6, controls=[store, ft.Container(expand=True)]),
            body,
            ft.Row(spacing=6, controls=[
                ft.FilledButton("Parse", height=34, on_click=do_parse)]),
            parsed,
        ]))

    def spending_card():
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Spending", 13), ft.Container(expand=True),
                    chip(f"${total:,.2f} · {len(purchases)} orders",
                         p["green"], small=True)]),
            *(spend_rows or [sub("no purchases saved yet", 11)]),
        ]))

    def radar_card():
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Freq radar", 13), ft.Container(expand=True)]),
            *(radar or [sub("nothing bought twice yet", 11)]),
        ]))

    return ft.Column(spacing=8, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("foodzen.expenses", {
            "receipt": receipt_card, "spending": spending_card,
            "radar": radar_card,
        })),
        ft.Container(padding=PAD(0, 4), content=status),
    ])
