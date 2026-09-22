"""Garage: the bikes, what has been done to them, and what they cost.

Three tabs, matching the website's three views.

**Home** is the whole of the website's garage. Pick one of the six bikes the
server knows about and you get its aggregate stats and six sections -
Dashboard, Mileage, Services, Expenses, Spec and Notes - reading and writing
the same JSON files the website does.

**Mechanic** is a placeholder on the website ("Mechanic view - content TBD").
It is a working quick-log here, and says so, because the app already had one
and deleting a feature is not parity.

**Flipper** is the server's live Craigslist/Reddit scanner.

Three things this file exists to get right, all of which shipped wrong:

*Nothing here invents a bike.* The old page read `M.BIKES`, which is in
`api._MAP`, so the moment `garage_bikes()` answered None - which is what "no
server" looks like - module `__getattr__` handed back `mock.BIKES`: a CB250
at 8,240 miles and a Rebel 250 at 14,310, neither of which exists. This
reads `api.garage_tree()`, which returns None and gets said out loud.

*Nothing here renders a null as a zero.* Every bike on the server currently
answers `miles_since_service: null`, `mpg_avg: null`, `cost_per_mile: null`
- nothing has been logged yet. The old page printed "0 since svc" under a
progress bar measured against a 1,000-mile service interval nobody
configured. Absent renders as an em dash.

*Records are written with the field names something reads.* The old log form
sent `{label, odo, amount}` whatever the type. The website's mileage table
reads date/odo/gallons/price_per_gal/cost/notes, services reads
date/odo/type/parts_cost/labor_cost/notes, expenses reads
date/category/vendor/amount/notes. `label` is read by nothing at either end,
and `amount` exists only on expenses, so a service logged from the app
appeared on the website as a date, an odometer and four blanks - and counted
nothing towards "total spent". The schema now lives in
`api.GARAGE_RECORD_FIELDS`, once, for both ends.
"""
import asyncio
import time

import flet as ft

from .. import api as M
from .. import panels
from ..ui import BRD, PAD, PAL, RAD, S, card, chip, sub, title
from ._forms import Writer, rebuilder
from .lab import _tabs


# Which bike and which of its sections you were last looking at. Module level
# so a write - or any repaint - does not throw you back to the picker.
_state = {"bike": None, "section": "dashboard", "note": None}

# The Flipper search, likewise. A scan is slow enough that losing the query
# on a repaint would be its own small disaster.
_flip = {"query": "motorcycle", "min": "", "max": "",
         "data": None, "error": "", "note": "", "ran": False}

# label, icon
BIKE_SECTIONS = (
    ("dashboard", "Dashboard", ft.Icons.SPEED),
    ("mileage", "Mileage", ft.Icons.ROUTE),
    ("services", "Services", ft.Icons.BUILD),
    ("expenses", "Expenses", ft.Icons.ATTACH_MONEY),
    ("spec", "Spec", ft.Icons.INFO_OUTLINE),
    ("notes", "Notes", ft.Icons.EDIT_NOTE),
)

# Human labels and input kinds for the record forms, matching the website's
# `garageOpenRecord` exactly - same order, same wording, same field types.
RECORD_LABELS = {
    "date": "Date", "odo": "Odometer (mi)", "gallons": "Gallons",
    "price_per_gal": "$/gal", "cost": "Total cost $", "notes": "Notes",
    "type": "Type (oil/valves/tires/chain/etc)", "parts_cost": "Parts $",
    "labor_cost": "Labor $", "category": "Category (reg/insurance/gear/parts/etc)",
    "vendor": "Vendor", "amount": "Amount $",
}
RECORD_TITLES = {"mileage": "Add Mileage", "services": "Add Service",
                 "expenses": "Add Expense"}

SPEC_LABELS = {
    "year": "Year", "make": "Make", "model": "Model", "nickname": "Nickname",
    "cc": "CC", "condition": "Condition", "reg_expires": "Reg expires",
    "vin": "VIN", "plate": "Plate", "color": "Color",
    "purchased": "Purchased", "purchase_price": "Purchase price",
    "odo_at_purchase": "Odo at purchase", "odo_current": "Current odo",
    "insurance": "Insurance", "notes": "Notes",
}


# ----------------------------------------------------------------- plumbing

def _repaint(page):
    """Re-render the page without marking every cached endpoint stale.

    `_forms.rebuilder` calls `api.refresh()`, which is right after a write
    and wrong for navigation: changing from Mileage to Services would
    re-fetch the machines, the weather and everything else the chrome reads,
    over the network, before drawing a list that is already in hand.
    """
    def go():
        rerender = getattr(S, "rerender", None)
        if callable(rerender):
            rerender()
            return
        page.controls.clear()       # standalone/tests: no chrome to preserve
        page.add(build(page))
        page.update()
    return go


def _show(page, dlg):
    """Open a dialog. ft.Page has show_dialog/pop_dialog in 0.86, not open/close."""
    (getattr(page, "show_dialog", None) or getattr(page, "open"))(dlg)


def _dismiss(page, dlg):
    fn = getattr(page, "pop_dialog", None)
    if fn is not None:
        fn()
    else:
        getattr(page, "close")(dlg)


def _dash(value):
    """A value the server may not have. None and "" render as absent."""
    if value is None or value == "":
        return "—"
    return str(value)


def _int(value):
    """An integer with thousands separators, or an em dash. 0 is shown."""
    if value is None or value == "":
        return "—"
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def _money(value, *, digits=2):
    if value is None or value == "":
        return "—"
    try:
        return f"${float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _field(p, hint, *, value="", width=None, multiline=False, keyboard=None):
    return ft.TextField(
        hint_text=hint, value=value, dense=True, filled=True, text_size=12,
        width=width, expand=width is None, multiline=multiline,
        min_lines=3 if multiline else 1, max_lines=8 if multiline else 1,
        keyboard_type=keyboard, border_color=p["border"],
        focused_border_color=p["accent"], color=p["text"])


def _today():
    return time.strftime("%Y-%m-%d")


def _labelled(p, label, control):
    return ft.Column(spacing=3, controls=[
        ft.Text(label.upper(), size=9, color=p["text3"],
                weight=ft.FontWeight.W_600), control])


def _no_server(what, detail=None):
    p = PAL()
    return card(ft.Column(spacing=6, controls=[
        ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.CLOUD_OFF, size=18, color=p["pink"]),
            title(f"No {what}", 14)]),
        sub(detail or "The garage lives on the server - there is nothing on "
                      "this device to show in its place, and inventing a "
                      "bike would be worse than an empty page.", 11),
    ]), pad=14)


# --------------------------------------------------------------------- home

def _bike_of(bikes, slug):
    for b in bikes:
        if b.get("name") == slug:
            return b
    return None


def _picker(page, bikes):
    """The six bikes, with the record counts the server reports."""
    p = PAL()
    repaint = _repaint(page)

    def pick(slug):
        def handler(e):
            _state["bike"] = slug
            _state["note"] = None
            repaint()
        return handler

    def row(b):
        slug = b.get("name")
        on = slug == _state["bike"]
        counts = (f"{b.get('mileage_count', 0)} mi · "
                  f"{b.get('service_count', 0)} svc · "
                  f"{b.get('expense_count', 0)} exp")
        return ft.Container(
            padding=PAD(8, 10), border_radius=RAD(9), ink=True, on_click=pick(slug),
            bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else p["surface"],
            border=BRD(p["accent"] if on else p["border"], 1),
            content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                ft.Icon(ft.Icons.TWO_WHEELER, size=18,
                        color=p["accent"] if on else p["text2"]),
                ft.Column(spacing=1, expand=True, controls=[
                    ft.Text(b.get("label") or slug or "?", size=12.5,
                            weight=ft.FontWeight.W_600, color=p["text"],
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(counts, size=9.5, color=p["text3"]),
                ]),
                ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, color=p["text3"]),
            ]))

    return card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            title("Garage", 14), ft.Container(expand=True),
            ft.FilledButton(content=ft.Text("New note", size=12), height=32,
                            icon=ft.Icons.ADD,
                            on_click=lambda e: _note_dialog(page, bikes)),
        ]),
        ft.Column(spacing=6, controls=[row(b) for b in bikes]),
    ]))


def _stats_strip(slug, stats):
    """The six aggregates the website shows, with null rendered as absent.

    `total_miles` and `total_spent` follow the website in reading 0 as "none
    logged" rather than as a measurement; the other four are genuinely null
    on the wire until there is enough to compute them.
    """
    p = PAL()
    if stats is None:
        return card(sub("Could not read this bike's stats from the server.", 11),
                    pad=12)
    tiles = [
        ("total miles", _int(stats.get("total_miles")) if stats.get("total_miles") else "—"),
        ("total spent", _money(stats.get("total_spent"), digits=0)
         if stats.get("total_spent") else "—"),
        ("mpg avg", _dash(stats.get("mpg_avg"))),
        ("cost / mile", _money(stats.get("cost_per_mile"))
         if stats.get("cost_per_mile") is not None else "—"),
        ("since service", f"{_int(stats.get('miles_since_service'))} mi"
         if stats.get("miles_since_service") is not None else "—"),
        ("days since svc", f"{stats.get('days_since_service')}d"
         if stats.get("days_since_service") is not None else "—"),
    ]

    def tile(label, value):
        return ft.Container(
            col={"xs": 4, "md": 2}, padding=PAD(8, 8), border_radius=RAD(9),
            bgcolor=p["bg2"], border=BRD(p["border"], 1),
            content=ft.Column(spacing=2, controls=[
                ft.Text(label, size=9, color=p["text3"]),
                ft.Text(value, size=14, weight=ft.FontWeight.W_700, color=p["text"],
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ]))

    unlogged = not (stats.get("mileage_count") or stats.get("service_count")
                    or stats.get("expense_count"))
    body = [ft.ResponsiveRow(columns=12, spacing=6, run_spacing=6,
                             controls=[tile(l, v) for l, v in tiles])]
    if unlogged:
        body.append(sub("Nothing logged for this bike yet, so none of these "
                        "have been measured.", 10))
    return card(ft.Column(spacing=8, controls=body), pad=10)


def _section_bar(page, current):
    p = PAL()
    repaint = _repaint(page)

    def go(key):
        def handler(e):
            _state["section"] = key
            repaint()
        return handler

    def tab(key, label, icon):
        on = key == current
        return ft.Container(
            padding=PAD(5, 10), border_radius=RAD(18), ink=True, on_click=go(key),
            bgcolor=ft.Colors.with_opacity(0.18, p["accent"]) if on else p["bg2"],
            border=BRD(p["accent"] if on else p["border"], 1),
            content=ft.Row(spacing=5, tight=True, controls=[
                ft.Icon(icon, size=13, color=p["accent"] if on else p["text2"]),
                ft.Text(label, size=11.5, color=p["text"] if on else p["text2"],
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400),
            ]))

    return ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO,
                  controls=[tab(*s) for s in BIKE_SECTIONS])


def _dashboard(page, stats):
    """The website's six action tiles, with the counts the server reports."""
    p = PAL()
    repaint = _repaint(page)
    s = stats or {}

    def go(key):
        def handler(e):
            _state["section"] = key
            repaint()
        return handler

    def tile(key, icon, heading, detail, adder=None):
        controls = [
            ft.Icon(icon, size=20, color=p["accent"]),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(heading, size=12.5, weight=ft.FontWeight.W_600, color=p["text"]),
                ft.Text(detail, size=9.5, color=p["text3"]),
            ]),
        ]
        if adder:
            controls.append(ft.IconButton(
                ft.Icons.ADD_CIRCLE, icon_size=20, icon_color=p["accent"],
                tooltip=RECORD_TITLES[adder],
                on_click=lambda e, t=adder: _record_dialog(page, t)))
        return ft.Container(
            col={"xs": 12, "md": 4}, padding=PAD(10, 12), border_radius=RAD(10),
            ink=True, on_click=go(key), bgcolor=p["bg2"],
            border=BRD(p["border"], 1),
            content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=controls))

    return ft.Column(spacing=10, controls=[
        ft.ResponsiveRow(columns=12, spacing=8, run_spacing=8, controls=[
            tile("mileage", ft.Icons.ROUTE, "Mileage",
                 f"{s.get('mileage_count', 0)} entries", "mileage"),
            tile("services", ft.Icons.BUILD, "Services",
                 f"{s.get('service_count', 0)} entries", "services"),
            tile("expenses", ft.Icons.ATTACH_MONEY, "Expenses",
                 f"{s.get('expense_count', 0)} entries", "expenses"),
        ]),
        ft.ResponsiveRow(columns=12, spacing=8, run_spacing=8, controls=[
            tile("spec", ft.Icons.INFO_OUTLINE, "Spec sheet", "VIN, plate, reg, odo"),
            tile("notes", ft.Icons.EDIT_NOTE, "Notes / Mods / Refs", "free-form"),
        ]),
    ])


# ------------------------------------------------------------ record tables

def _record_line(p, rtype, r):
    """One record, with every field the website's table column shows.

    A six-column table is unreadable at 400px, so the columns become a
    headline and a detail line - the same fields, not fewer.
    """
    date = _dash(r.get("date"))
    if rtype == "mileage":
        head = f"{date} · {_int(r.get('odo'))} mi"
        bits = []
        if r.get("gallons") not in (None, ""):
            bits.append(f"{r['gallons']} gal")
        if r.get("price_per_gal") not in (None, ""):
            bits.append(f"{_money(r['price_per_gal'])}/gal")
        right = _money(r.get("cost")) if r.get("cost") not in (None, "") else ""
    elif rtype == "services":
        head = f"{date} · {r.get('type') or 'service'}"
        bits = []
        if r.get("odo") not in (None, ""):
            bits.append(f"{_int(r['odo'])} mi")
        if r.get("parts_cost") not in (None, ""):
            bits.append(f"parts {_money(r['parts_cost'])}")
        if r.get("labor_cost") not in (None, ""):
            bits.append(f"labor {_money(r['labor_cost'])}")
        total = sum(float(r.get(k) or 0) for k in ("parts_cost", "labor_cost"))
        right = _money(total) if total else ""
    else:
        head = f"{date} · {r.get('category') or 'expense'}"
        bits = [r["vendor"]] if r.get("vendor") else []
        right = _money(r.get("amount")) if r.get("amount") not in (None, "") else ""

    if r.get("notes"):
        bits.append(str(r["notes"]))
    return ft.Container(
        padding=PAD(7, 10), border_radius=RAD(8), bgcolor=p["bg2"],
        border=BRD(p["border"], 1),
        content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                       controls=[
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(head, size=12, color=p["text"], weight=ft.FontWeight.W_500),
                ft.Text(" · ".join(bits), size=9.5, color=p["text3"]),
            ]),
            ft.Text(right, size=13, weight=ft.FontWeight.W_600, color=p["text"]),
        ]))


def _records(page, rtype):
    p = PAL()
    slug = _state["bike"]
    rows = M.garage_records(slug, rtype)
    add = ft.FilledButton(content=ft.Text(RECORD_TITLES[rtype], size=12), height=34,
                          icon=ft.Icons.ADD,
                          on_click=lambda e: _record_dialog(page, rtype))
    if rows is None:
        return ft.Column(spacing=10, controls=[
            add, sub(f"Could not read this bike's {rtype} from the server.", 11)])
    if not rows:
        return ft.Column(spacing=10, controls=[
            add, sub(f"No {rtype} logged for this bike yet.", 11)])
    # Newest first, as the website sorts it.
    ordered = sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True)
    noun = {"mileage": "mileage entr", "services": "service record",
            "expenses": "expense"}[rtype]
    plural = "y" if rtype == "mileage" else ""
    if len(ordered) != 1:
        plural = "ies" if rtype == "mileage" else "s"
    return ft.Column(spacing=10, controls=[
        add,
        ft.Column(spacing=5, controls=[_record_line(p, rtype, r) for r in ordered]),
        sub(f"{len(ordered)} {noun}{plural}", 10),
    ])


def coerce_record(rtype, typed):
    """(record, error) from what was typed into the Add form.

    Split out of the dialog so it can be tested without a page. A number
    field left blank is sent as null, not as 0 - the server's stats treat
    `gallons: 0` as a fill-up of nothing and would compute an mpg from it.
    A whole number is sent as an int, so an odometer reads 10212 on the
    website rather than 10212.0.
    """
    record = {}
    for key in M.GARAGE_RECORD_FIELDS[rtype]:
        raw = (typed.get(key) or "").strip()
        if key in M.GARAGE_NUMERIC_FIELDS:
            if raw == "":
                record[key] = None
                continue
            try:
                value = float(raw)
            except ValueError:
                return None, f"{RECORD_LABELS[key]} must be a number"
            record[key] = int(value) if value.is_integer() else value
        else:
            record[key] = raw
    if not any(v not in (None, "") for k, v in record.items() if k != "date"):
        return None, "nothing to record - fill in at least one field"
    return record, None


def coerce_spec(typed):
    """The whole spec sheet as the server should store it.

    Every key in GARAGE_SPEC_FIELDS is present in the result even when it
    was left blank, because `/api/garage/spec/save` rewrites _BIKE.json
    wholesale - a key omitted here is a key deleted there.
    """
    data = {}
    for key in M.GARAGE_SPEC_FIELDS:
        raw = (typed.get(key) or "").strip()
        if key in M.GARAGE_SPEC_NUMERIC and raw != "":
            try:
                number = float(raw)
            except ValueError:
                data[key] = raw          # the website keeps it as typed
            else:
                data[key] = int(number) if number.is_integer() else number
            continue
        data[key] = raw
    return data


def _record_dialog(page, rtype):
    """The website's Add form for one record type, field for field."""
    p = PAL()
    slug = _state["bike"]
    fields = M.GARAGE_RECORD_FIELDS[rtype]
    inputs = {}
    rows = []
    for key in fields:
        multiline = (key == "notes" and rtype == "services")
        value = _today() if key == "date" else ""
        box = _field(p, RECORD_LABELS[key], value=value, multiline=multiline,
                     keyboard=(ft.KeyboardType.NUMBER
                               if key in M.GARAGE_NUMERIC_FIELDS else None))
        inputs[key] = box
        rows.append(_labelled(p, RECORD_LABELS[key], box))

    note = ft.Text("", size=11, color=p["pink"], selectable=True)
    dlg = ft.AlertDialog(
        modal=True, bgcolor=p["surface"],
        title=ft.Text(RECORD_TITLES[rtype], size=15, color=p["text"]),
        content=ft.Container(width=420, content=ft.Column(
            spacing=10, tight=True, scroll=ft.ScrollMode.AUTO,
            controls=rows + [note])))

    async def save(e):
        record, problem = coerce_record(
            rtype, {k: box.value for k, box in inputs.items()})
        if problem:
            note.value = problem
            note.color = p["pink"]
            page.update()
            return
        note.value = "saving…"
        note.color = p["text2"]
        page.update()
        ok, msg = await asyncio.to_thread(M.garage_record_add, slug, rtype, record)
        if not ok:
            note.value = f"not saved - {msg}"
            note.color = p["pink"]
            page.update()
            return
        _dismiss(page, dlg)
        _state["section"] = rtype
        rebuilder(page, build)()

    dlg.actions = [ft.TextButton("Cancel", on_click=lambda e: _dismiss(page, dlg)),
                   ft.FilledButton(content=ft.Text("Save"), on_click=save)]
    _show(page, dlg)


# ----------------------------------------------------------------- spec tab

def _spec(page, slug):
    p = PAL()
    spec = M.garage_spec(slug)
    if spec is None:
        return sub("Could not read this bike's spec sheet from the server.", 11)

    w = Writer(page, _repaint(page))
    inputs = {}
    cells = []
    for key in M.GARAGE_SPEC_FIELDS:
        if key == "notes":
            continue
        box = _field(p, SPEC_LABELS[key], value=_blank_str(spec.get(key)))
        inputs[key] = box
        cells.append(ft.Container(col={"xs": 12, "md": 6},
                                  content=_labelled(p, SPEC_LABELS[key], box)))
    notes = _field(p, "Notes", value=_blank_str(spec.get("notes")), multiline=True)
    inputs["notes"] = notes

    async def save(e):
        data = coerce_spec({k: box.value for k, box in inputs.items()})
        w.busy()
        ok, msg = await asyncio.to_thread(M.garage_spec_save, slug, data)
        w.report(ok, msg)
        page.update()

    return ft.Column(spacing=10, controls=[
        ft.ResponsiveRow(columns=12, spacing=10, run_spacing=10, controls=cells),
        _labelled(p, "Notes", notes),
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.FilledButton(content=ft.Text("Save Spec", size=12), height=34,
                            icon=ft.Icons.SAVE, on_click=save),
            w.text,
        ]),
        sub("The server rewrites the whole spec file on every save, so every "
            "box above is sent each time - clearing one clears it.", 10),
    ])


def _blank_str(value):
    return "" if value is None else str(value)


# ---------------------------------------------------------------- notes tab

def _note_path(slug, rel):
    """The path `/api/garage/read` will actually accept.

    The server's tree reports a *folder* as `"<slug>/Mods"` but a *file*
    inside it as `"Mods/2026-09-21_x.md"` - `os.path.relpath(fp, bike_dir)`,
    relative to the bike, where the folder above it is relative to the
    garage. `garage_read` joins whatever it is given onto GARAGE_DIR, so the
    file path is short by one segment and reading it answers HTTP 404. The
    website hands that path straight to /api/garage/read, so Garage > Notes
    there cannot open a note at all; verified by curl against the live
    server. Put the slug back if it is missing rather than depending on
    which of the two the server happens to send.
    """
    rel = (rel or "").lstrip("/")
    if not rel:
        return ""
    if rel == slug or rel.startswith(slug + "/"):
        return rel
    return f"{slug}/{rel}"


def _note_files(node, out):
    """Flatten the server's tree into (folder, filename, path) triples."""
    for child in (node.get("children") or []):
        if child.get("type") == "file":
            out.append((node.get("name") or "", child.get("name") or "",
                        child.get("path") or ""))
        else:
            _note_files(child, out)
    return out


def _notes(page, bike):
    p = PAL()
    repaint = _repaint(page)
    folders = bike.get("children") or []

    def open_note(path):
        def handler(e):
            _state["note"] = path
            repaint()
        return handler

    slug = bike.get("name") or ""
    blocks = []
    for folder in folders:
        files = [(name, _note_path(slug, path))
                 for _folder, name, path in _note_files(folder, [])]
        rows = [ft.Container(
            padding=PAD(5, 9), border_radius=RAD(7), ink=True,
            on_click=open_note(path),
            bgcolor=(ft.Colors.with_opacity(0.12, p["accent"])
                     if path == _state["note"] else p["bg2"]),
            border=BRD(p["accent"] if path == _state["note"] else p["border"], 1),
            content=ft.Row(spacing=7, controls=[
                ft.Icon(ft.Icons.DESCRIPTION, size=13, color=p["text3"]),
                ft.Text(name.rsplit(".", 1)[0], size=11.5, color=p["text"],
                        expand=True, no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS)]))
            for name, path in files]
        blocks.append(ft.Column(spacing=5, controls=[
            ft.Row(spacing=6, controls=[
                ft.Icon(ft.Icons.FOLDER, size=14, color=p["text3"]),
                ft.Text(folder.get("name") or "?", size=11.5,
                        weight=ft.FontWeight.W_600, color=p["text2"])]),
            ft.Column(spacing=4, controls=rows) if rows
            else sub("empty", 10),
        ]))

    body = [
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            sub("Notes / Mods / Refs", 11.5), ft.Container(expand=True),
            ft.FilledButton(content=ft.Text("New", size=12), height=32,
                            icon=ft.Icons.ADD,
                            on_click=lambda e: _note_dialog(
                                page, [bike], bike.get("name"))),
        ]),
        ft.Column(spacing=10, controls=blocks) if blocks
        else sub("This bike has no note folders on the server.", 11),
    ]

    if _state["note"]:
        text = M.garage_read(_state["note"])
        if text is None:
            body.append(card(sub(f"Could not read {_state['note']}.", 11), pad=12))
        else:
            body.append(card(ft.Column(spacing=6, controls=[
                ft.Row(spacing=8, controls=[
                    ft.Text(_state["note"].split("/")[-1], size=11,
                            color=p["text2"], expand=True, selectable=True),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=16,
                                  icon_color=p["text3"],
                                  on_click=lambda e: (_state.update(note=None),
                                                      repaint())),
                ]),
                ft.Markdown(text or "_empty_", selectable=True,
                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB),
            ]), pad=12))

    return ft.Column(spacing=10, controls=body)


def _note_dialog(page, bikes, preselect=None):
    """Create a markdown note, matching the website's bike/section/title form."""
    p = PAL()
    options = [(b.get("name"), b.get("label") or b.get("name")) for b in bikes
               if b.get("name")]
    if not options:
        return
    chosen = preselect or _state["bike"] or options[0][0]
    if chosen not in [slug for slug, _ in options]:
        chosen = options[0][0]

    # Flet 0.86 Dropdown fires on_select, never on_change; there is no
    # handler here at all, so `.value` is read at save time instead.
    bike = ft.Dropdown(value=chosen, dense=True, filled=True, text_size=12,
                       border_color=p["border"], color=p["text"],
                       options=[ft.DropdownOption(key=s, text=n)
                                for s, n in options])
    section = ft.Dropdown(value=M.GARAGE_NOTE_SECTIONS[0], dense=True, filled=True,
                          text_size=12, border_color=p["border"], color=p["text"],
                          options=[ft.DropdownOption(key=s, text=s)
                                   for s in M.GARAGE_NOTE_SECTIONS])
    name = _field(p, "e.g. Valve clearance, Carb rebuild, Paint touch-up")
    note = ft.Text("", size=11, color=p["pink"], selectable=True)

    dlg = ft.AlertDialog(
        modal=True, bgcolor=p["surface"],
        title=ft.Text("New note", size=15, color=p["text"]),
        content=ft.Container(width=400, content=ft.Column(
            spacing=10, tight=True, controls=[
                _labelled(p, "Bike", bike),
                _labelled(p, "Section", section),
                _labelled(p, "Title", name),
                note])))

    async def create(e):
        note.value = "creating…"
        note.color = p["text2"]
        page.update()
        ok, msg = await asyncio.to_thread(
            M.garage_note_new, bike.value, section.value, name.value or "")
        if not ok:
            note.value = f"not created - {msg}"
            note.color = p["pink"]
            page.update()
            return
        _dismiss(page, dlg)
        _state["bike"] = bike.value
        _state["section"] = "notes"
        _state["note"] = msg if msg.endswith(".md") else None
        rebuilder(page, build)()

    dlg.actions = [ft.TextButton("Cancel", on_click=lambda e: _dismiss(page, dlg)),
                   ft.FilledButton(content=ft.Text("Create"), on_click=create)]
    _show(page, dlg)


# ------------------------------------------------------------------- tabs

def _home(page):
    p = PAL()
    bikes = M.garage_tree()
    if bikes is None:
        return ft.Column(spacing=10, controls=[_no_server("garage")])
    if not bikes:
        return ft.Column(spacing=10, controls=[
            card(sub("The server has no bikes configured.", 11), pad=12)])

    if _state["bike"] not in [b.get("name") for b in bikes]:
        _state["bike"] = bikes[0].get("name")
    if _state["section"] not in [k for k, _, _ in BIKE_SECTIONS]:
        _state["section"] = "dashboard"

    bike = _bike_of(bikes, _state["bike"])
    stats = M.garage_stats(_state["bike"])
    section = _state["section"]

    def detail():
        if section == "dashboard":
            body = _dashboard(page, stats)
        elif section in M.GARAGE_RECORD_TYPES:
            body = _records(page, section)
        elif section == "spec":
            body = _spec(page, _state["bike"])
        else:
            body = _notes(page, bike)
        counts = (f"{bike.get('mileage_count', 0)} mileage · "
                  f"{bike.get('service_count', 0)} services · "
                  f"{bike.get('expense_count', 0)} expenses")
        return card(ft.Column(spacing=10, controls=[
            ft.Column(spacing=1, controls=[
                title(bike.get("label") or _state["bike"], 15),
                sub(counts, 10)]),
            _section_bar(page, section),
            ft.Divider(height=1, color=p["border"]),
            body,
        ]))

    return panels.layout("garage.home", {
        "bikes": lambda: _picker(page, bikes),
        "stats": lambda: _stats_strip(_state["bike"], stats),
        "detail": detail,
    })


def _mechanic(page):
    """A quick log against any bike, with the fields the records really have.

    The website's Mechanic view is still a placeholder - "Mechanic view -
    content TBD" - so this is app-side only and says so. What it is not any
    more is wrong: it used to send `{label, odo, amount}` for every record
    type, and `label` is a field nothing reads.
    """
    p = PAL()
    bikes = M.garage_tree()
    if bikes is None:
        return ft.Column(spacing=10, controls=[_no_server("garage")])
    options = [(b.get("name"), b.get("label") or b.get("name")) for b in bikes
               if b.get("name")]
    if not options:
        return ft.Column(spacing=10, controls=[
            card(sub("The server has no bikes configured.", 11), pad=12)])

    repaint = _repaint(page)

    def start(rtype):
        def handler(e):
            _state["section"] = rtype
            _record_dialog(page, rtype)
        return handler

    def bike_card(b):
        slug = b.get("name")
        stats = M.garage_stats(slug) or {}
        since = stats.get("miles_since_service")
        days = stats.get("days_since_service")
        if since is None and days is None:
            service = "no service logged"
        else:
            bits = []
            if since is not None:
                bits.append(f"{_int(since)} mi")
            if days is not None:
                bits.append(f"{days}d")
            service = "since service: " + " · ".join(bits)

        def choose(e, s=slug):
            _state["bike"] = s
            repaint()

        on = slug == _state["bike"]
        return ft.Container(
            padding=PAD(8, 10), border_radius=RAD(9), ink=True, on_click=choose,
            bgcolor=ft.Colors.with_opacity(0.12, p["accent"]) if on else p["bg2"],
            border=BRD(p["accent"] if on else p["border"], 1),
            content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                ft.Icon(ft.Icons.TWO_WHEELER, size=17,
                        color=p["accent"] if on else p["text2"]),
                ft.Column(spacing=1, expand=True, controls=[
                    ft.Text(b.get("label") or slug, size=12,
                            weight=ft.FontWeight.W_600, color=p["text"],
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(service, size=9.5, color=p["text3"]),
                ]),
                ft.Text(_money(stats.get("total_spent"), digits=0)
                        if stats.get("total_spent") else "—",
                        size=12, color=p["text2"]),
            ]))

    if _state["bike"] not in [s for s, _ in options]:
        _state["bike"] = options[0][0]
    chosen_label = dict(options).get(_state["bike"], _state["bike"])

    return panels.layout("garage.mechanic", {
        "log": lambda: card(ft.Column(spacing=8, controls=[
            title("Log against a bike", 14),
            sub(f"logging to {chosen_label}", 10),
            ft.Row(spacing=6, wrap=True, controls=[
                ft.FilledButton(content=ft.Text(RECORD_TITLES[t], size=12),
                                height=34, icon=ft.Icons.ADD, on_click=start(t))
                for t in M.GARAGE_RECORD_TYPES]),
        ])),
        "fleet": lambda: card(ft.Column(spacing=8, controls=[
            title("The fleet", 13),
            ft.Column(spacing=6, controls=[bike_card(b) for b in bikes]),
        ])),
        "note": lambda: card(
            sub("The website's Mechanic tab is still a placeholder - "
                "\"content TBD\" - so this view is app-side only. Everything "
                "it writes lands in the same records the website reads.",
                10.5), pad=10),
    })


# ---------------------------------------------------------------- flipper

def _flipper_meta(p, data):
    """Count, market median, what each source returned, and why some did not."""
    median = data.get("market_median")
    bits = [f"{data.get('count', 0)} listings",
            f"market median {_money(median, digits=0)}" if median
            else "market median — (needs 5 priced listings)"]
    sources = data.get("sources_summary") or {}
    if sources:
        bits.append(" · ".join(f"{k} {v}" for k, v in sorted(sources.items())))

    rows = [sub(" · ".join(bits[:2]), 10.5)]
    if sources:
        rows.append(ft.Text(bits[2], size=9.5, color=p["text3"], selectable=True))

    errors = data.get("errors") or {}
    if errors:
        # Grouped by cause, as the website does, because "9 sources failed"
        # with no detail is how it took a browser console to find out that
        # every Reddit source is answering HTTP 429.
        why = {}
        for src, msg in errors.items():
            text = str(msg)
            cause = text
            marker = "HTTP Error "
            if marker in text:
                after = text.split(marker, 1)[1]
                cause = "HTTP " + after.split(":")[0].strip()
            else:
                cause = text.split(":")[0][:40]
            why.setdefault(cause, []).append(src)
        commonest = max(why.items(), key=lambda kv: len(kv[1]))
        rows.append(ft.Text(
            f"{len(errors)} source{'' if len(errors) == 1 else 's'} failed "
            f"({commonest[0]}): " + ", ".join(sorted(errors)),
            size=9.5, color=p["pink"], selectable=True))
    return ft.Column(spacing=4, controls=rows)


def _deal_card(p, d):
    score = d.get("score")
    if d.get("not_a_deal"):
        # Anything the server could not treat as a bike for sale says so
        # plainly instead of wearing a score and a profit it never earned.
        return ft.Container(
            padding=PAD(8, 10), border_radius=RAD(9), bgcolor=p["bg2"],
            border=BRD(p["border"], 1),
            content=ft.Column(spacing=3, controls=[
                ft.Row(spacing=8, controls=[
                    chip("not a deal", p["text3"], small=True),
                    ft.Text(f"{d.get('source', '?')}"
                            + (f" · {d['region']}" if d.get("region") else ""),
                            size=9.5, color=p["text3"]),
                    ft.Container(expand=True),
                    ft.Text(_money(d.get("price"), digits=0), size=11,
                            color=p["text2"]),
                ]),
                ft.Text(str(d.get("title") or "untitled"), size=11.5,
                        color=p["text2"], max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(str(d["not_a_deal"]), size=9.5, color=p["text3"]),
            ]))

    colour = (p["green"] if (score or 0) >= 70
              else p["yellow"] if (score or 0) >= 50 else p["red"])
    net = d.get("net_profit")
    net_text = "—" if net is None else (("+" if net >= 0 else "-")
                                        + _money(abs(net), digits=0))
    net_colour = p["text3"] if net is None else (p["green"] if net >= 0 else p["red"])

    top = [ft.Container(width=34, height=26, border_radius=RAD(6), bgcolor=colour,
                        alignment=ft.Alignment(0, 0),
                        content=ft.Text(str(score), size=12,
                                        weight=ft.FontWeight.W_700,
                                        color=ft.Colors.BLACK)),
           ft.Text(f"{d.get('source', '?')}"
                   + (f" · {d['region']}" if d.get("region") else ""),
                   size=9.5, color=p["text3"]),
           ft.Container(expand=True),
           ft.Text(_money(d.get("price"), digits=0), size=12.5,
                   weight=ft.FontWeight.W_600, color=p["text"])]
    if d.get("pearl"):
        top.insert(2, chip("PEARL", p["green"], small=True))

    detail = []
    if d.get("location"):
        detail.append(str(d["location"]))
    if d.get("starts") is False:
        detail.append("NO START")
    elif d.get("starts") is True:
        detail.append("runs")
    if d.get("repair_cost"):
        detail.append(f"repair ~{_money(d['repair_cost'], digits=0)}")
    if d.get("est_hrs"):
        detail.append(f"~{d['est_hrs']}h work")

    damage = [str(x) for x in ((d.get("found") or []) + (d.get("suspected") or []))][:6]
    rows = [
        ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=top),
        ft.Text(str(d.get("title") or "untitled"), size=12.5, color=p["text"],
                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
        ft.Row(spacing=8, controls=[
            ft.Text(f"net {net_text}", size=11, color=net_colour),
            ft.Text(("gross " + ("+" if (d.get("profit") or 0) >= 0 else "-")
                     + _money(abs(d["profit"]), digits=0))
                    if d.get("profit") is not None else "", size=10,
                    color=p["text3"]),
            ft.Container(expand=True),
            chip(str(d.get("severity") or "low"),
                 p["green"] if d.get("severity") == "low"
                 else p["yellow"] if d.get("severity") == "medium" else p["red"],
                 small=True),
        ]),
    ]
    if detail:
        rows.append(ft.Text(" · ".join(detail), size=9.5, color=p["text3"]))
    if damage:
        rows.append(ft.Row(spacing=5, wrap=True,
                           controls=[chip(x, p["orange"], small=True) for x in damage]))
    if d.get("url"):
        rows.append(ft.Row(spacing=4, controls=[
            ft.Icon(ft.Icons.OPEN_IN_NEW, size=11, color=p["accent"]),
            ft.Text(str(d["url"]), size=9, color=p["accent"], selectable=True,
                    no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)]))

    return ft.Container(
        padding=PAD(9, 10), border_radius=RAD(9), bgcolor=p["surface"],
        border=BRD(p["green"] if d.get("pearl") else p["border"], 1),
        content=ft.Column(spacing=4, controls=rows))


def _flipper(page):
    p = PAL()
    query = _field(p, "Search (e.g. ninja 250, cb750, dirt bike)",
                   value=_flip["query"])
    lo = _field(p, "min $", value=_flip["min"], width=88,
                keyboard=ft.KeyboardType.NUMBER)
    hi = _field(p, "max $", value=_flip["max"], width=88,
                keyboard=ft.KeyboardType.NUMBER)
    status = ft.Text(_flip["note"] or "", size=10.5,
                     color=p["pink"] if _flip["error"] else p["text2"],
                     selectable=True, expand=True)
    if _flip["error"]:
        status.value = _flip["error"]
    results = ft.Column(spacing=8)
    meta = ft.Column(spacing=4)

    def draw():
        data = _flip["data"]
        meta.controls.clear()
        results.controls.clear()
        if data is None:
            results.controls.append(sub(
                "The scanner runs on the server against Craigslist and "
                "Reddit, so there is nothing to show until it has been run.",
                11))
            return
        meta.controls.append(_flipper_meta(p, data))
        listings = data.get("listings") or []
        if not listings:
            results.controls.append(sub("No results for that search.", 11))
            return
        for d in listings[:40]:
            results.controls.append(_deal_card(p, d))
        if len(listings) > 40:
            results.controls.append(
                sub(f"showing the first 40 of {len(listings)}", 10))

    async def scan(e):
        _flip["query"] = (query.value or "").strip() or "motorcycle"
        _flip["min"] = (lo.value or "").strip()
        _flip["max"] = (hi.value or "").strip()
        _flip["error"] = ""
        # The scan really does take minutes against seven Craigslist regions,
        # so the button goes dead and says why before anything is awaited.
        _flip["note"] = "scanning Craigslist and Reddit — this takes a while…"
        status.value = _flip["note"]
        status.color = p["text2"]
        button.disabled = True
        page.update()

        data = await asyncio.to_thread(M.flipper_scan, _flip["query"],
                                       _flip["min"], _flip["max"])
        button.disabled = False
        _flip["ran"] = True
        if data is None:
            _flip["data"] = None
            _flip["error"] = ("could not reach the scanner — "
                              + (M.config.STATUS.get("detail") or "no server"))
            status.value = _flip["error"]
            status.color = p["pink"]
        else:
            _flip["data"] = data
            where = M.provenance(M.flipper_scan_path(_flip["query"], _flip["min"],
                                                     _flip["max"]))
            _flip["note"] = (f"{data.get('count', 0)} listings, "
                             f"{'live off the wire' if where == M.LIVE else where}")
            status.value = _flip["note"]
            status.color = p["text2"]
        draw()
        page.update()

    button = ft.FilledButton(content=ft.Text("Scan", size=12), height=34,
                             icon=ft.Icons.SEARCH, on_click=scan)
    query.on_submit = scan
    draw()

    return panels.layout("garage.flipper", {
        "search": lambda: card(ft.Column(spacing=8, controls=[
            query,
            ft.Row(spacing=6, controls=[lo, hi, button]),
            ft.Row(spacing=6, controls=[status]),
            meta,
        ])),
        "listings": lambda: results,
    })


# --------------------------------------------------------------------- page

def build(page):
    return _tabs(page, [("Home", _home(page)), ("Mechanic", _mechanic(page)),
                        ("Flipper", _flipper(page))], key="garage")
