import flet as ft
from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .lab import _tabs
from ._forms import Writer, offline_banner, rebuilder

def build(page):
    return _tabs(page, [("Home", _bikes()), ("Mechanic", _mechanic(page)), ("Flipper", _flipper())])

def _bikes():
    p = PAL()
    def bike(b):
        return card(ft.Column(spacing=8, controls=[
            ft.Row([
                ft.Container(width=10, height=38, border_radius=4, bgcolor=b["color"]),
                ft.Column(spacing=1, controls=[
                    title(b["name"], 15),
                    sub(f"{b['year']} {b['make']} · {b['status']}", 11),
                ]),
                ft.Container(expand=True),
                chip(b["status"],
                     p["green"] if b["status"]=="running" else p["yellow"], small=True),
            ]),
            ft.Divider(height=1, color=p["border"]),
            ft.Row(spacing=16, controls=[
                ft.Column(spacing=1, controls=[sub("total mi", 10),
                    ft.Text(f"{b['total_mi']:,}", size=15, weight=ft.FontWeight.W_700, color=p["text"])]),
                ft.Column(spacing=1, controls=[sub("since svc", 10),
                    ft.Text(f"{b['mi_since_svc']}", size=15, weight=ft.FontWeight.W_700,
                            color=b["color"] if b["mi_since_svc"] < 800 else p["red"])]),
            ]),
            ft.ProgressBar(value=min(1.0, b["mi_since_svc"] / 1000), color=b["color"],
                           bgcolor=p["bg2"], bar_height=4, border_radius=2),
        ]))
    return ft.Column(spacing=10, controls=[bike(b) for b in M.BIKES])

def _mechanic(page):
    """Log mileage, services and expenses against a bike.

    The server appends the record and assigns its id and created_at under a
    lock, so this sends only the fields and never invents an id - two phones
    logging at once would otherwise collide.
    """
    p = PAL()
    w = Writer(page, rebuilder(page, build))
    live = M.STATUS_LIVE()
    bikes = M.BIKES or []
    slugs = [(b.get("slug") or b.get("id") or b.get("name"), b.get("name", "?"))
             for b in bikes if (b.get("slug") or b.get("id") or b.get("name"))]

    if not live or not slugs:
        return ft.Column(spacing=8, controls=[
            offline_banner(p, "garage records") if not live else
            card(sub("No bikes configured on the server.", 11), pad=12)])

    bike = ft.Dropdown(value=slugs[0][0], dense=True, filled=True, text_size=12,
                       width=190, border_color=p["border"], color=p["text"],
                       options=[ft.dropdown.Option(key=s, text=n) for s, n in slugs])
    rtype = ft.Dropdown(value="services", dense=True, filled=True, text_size=12,
                        width=130, border_color=p["border"], color=p["text"],
                        options=[ft.dropdown.Option(t) for t in M.GARAGE_RECORD_TYPES])

    def field(hint, width=None):
        return ft.TextField(hint_text=hint, dense=True, filled=True, text_size=12,
                            width=width, expand=width is None,
                            border_color=p["border"],
                            focused_border_color=p["accent"], color=p["text"])

    label = field("what happened (e.g. oil change)")
    odo = field("odometer", 110)
    amount = field("cost", 90)

    records = ft.Column(spacing=4)

    def load_records(e=None):
        rows = M.garage_records(bike.value, rtype.value)
        records.controls.clear()
        if rows is None:
            records.controls.append(sub("could not read records", 11))
        elif not rows:
            records.controls.append(sub("no records yet", 11))
        else:
            for r in rows[-12:][::-1]:
                bits = [str(r.get(k)) for k in ("date", "odo", "label", "amount")
                        if r.get(k) not in (None, "")]
                records.controls.append(ft.Row(spacing=8, controls=[
                    ft.Text(" · ".join(bits) or r.get("id", "?"), size=12,
                            color=p["text"], expand=True),
                    sub((r.get("created_at") or "")[:10], 10)]))
        page.update()

    # Flet 0.86 Dropdown fires on_select, not on_change. Assigning on_change
    # is silently accepted and never called, so switching bike or record type
    # left the list below showing the previous selection's records.
    bike.on_select = load_records
    rtype.on_select = load_records
    load_records()

    async def add(e):
        rec = {}
        if (label.value or "").strip():
            rec["label"] = label.value.strip()
        if (odo.value or "").strip():
            try:
                rec["odo"] = int(float(odo.value))
            except ValueError:
                w.report(False, "odometer must be a number")
                page.update()
                return
        if (amount.value or "").strip():
            try:
                rec["amount"] = round(float(amount.value), 2)
            except ValueError:
                w.report(False, "cost must be a number")
                page.update()
                return
        if not rec:
            w.report(False, "nothing to record")
            page.update()
            return
        rec.setdefault("date", __import__("time").strftime("%Y-%m-%d"))
        w.busy()
        ok, msg = M.garage_record_add(bike.value, rtype.value, rec)
        w.report(ok, msg)
        if ok:
            label.value = odo.value = amount.value = ""
            load_records()
        page.update()

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            title("Log a record", 14),
            ft.Row(spacing=6, controls=[bike, rtype]),
            label,
            ft.Row(spacing=6, controls=[odo, amount,
                                        ft.FilledButton("Add", height=34, on_click=add)]),
        ])),
        card(ft.Column(spacing=6, controls=[title("Recent", 13), records])),
        w.control,
    ])

def _flipper():
    """Real listings from the server's scanner.

    This used to render four invented bikes from mock.py behind a "sample
    deals" chip. The server has a working scanner, and a made-up Honda
    Nighthawk at $950 is not worth showing whatever it is labelled.
    """
    p = PAL()
    deals = M.flipper()
    if deals is None:
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Can't reach the scanner", 14),
            sub("Flipper scans Craigslist and Reddit live, so there is "
                "nothing to show without the server.", 11)]), pad=14)])
    if not deals:
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Nothing worth flagging", 14),
            sub("The last scan found no listing it would score - what it did "
                "find looked like wanted ads or placeholder prices.",
                11)]), pad=14)])

    def deal(d):
        score = d.get("score") or 0
        c = p["green"] if score >= 80 else (p["yellow"] if score >= 60 else p["red"])
        net = d.get("net_profit")
        bits = [ft.Column(spacing=1, controls=[
            sub("ask", 10),
            ft.Text(f"${(d.get('price') or 0):,}", size=13, color=p["text"])])]
        if net is not None:
            bits.append(ft.Column(spacing=1, controls=[
                sub("net", 10),
                ft.Text(f"${net:,}", size=13,
                        color=p["green"] if net >= 0 else p["red"])]))
        bits.append(ft.Column(spacing=1, controls=[
            sub("score", 10), ft.Text(str(score), size=13, color=c)]))
        head = [title(str(d.get("title") or "untitled")[:70], 13),
                ft.Container(expand=True)]
        if d.get("pearl"):
            head.append(chip("PEARL", p["green"], small=True))
        return card(ft.Column(spacing=5, controls=[
            ft.Row(head),
            ft.Row(spacing=14, controls=bits),
            sub(" · ".join(str(x) for x in (d.get("source"), d.get("region"),
                                            d.get("severity")) if x), 10),
        ]), pad=10)

    return ft.Column(spacing=8, controls=[
        ft.Row([chip(f"{len(deals)} scored listings", p["orange"],
                     ft.Icons.SEARCH, small=True)]),
        *[deal(d) for d in deals[:25]],
    ])
