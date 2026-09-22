"""EatZen and BPM — the same two tools the website has, on the phone.

Sodium is the headline, added sugar comes along for the ride. Two rules the
code sticks to, matching the web client exactly:

1. A number you typed always beats a looked-up one. You are reading a label;
   the table holds a generic average.
2. Nothing is guessed. A row marked IDK contributes nothing and says so,
   instead of quietly counting as zero and making the day look better than
   it was.

All the arithmetic and the food table live on the server, so a figure can
never differ between the phone and the browser.
"""
import time

import flet as ft

from .. import api as M
from .. import prefs
from ..ui import PAL, RAD, card, chip, sub, title
from ._forms import Writer, offline_banner, rebuilder


def _tools_build():
    from . import tools
    return tools.build


def _now_local():
    return time.strftime("%Y-%m-%dT%H:%M")


def _num(value):
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _show(page, dlg):
    """Open a dialog across Flet versions.

    ft.Page has no .open()/.close() in 0.86 - they are show_dialog() and
    pop_dialog(). Calling the wrong one raises AttributeError the moment the
    dialog opens, which is how the "From label" form was broken.
    """
    (getattr(page, "show_dialog", None) or getattr(page, "open"))(dlg)


def _dismiss(page, dlg):
    fn = getattr(page, "pop_dialog", None)
    if fn is not None:
        fn()
    else:
        getattr(page, "close")(dlg)


def _blank():
    return {"food": "", "qty": "", "unit": "", "na": "", "addsug": "", "idk": False,
            "res": None}


# Rows survive a repaint, so editing one does not wipe the others.
_rows = [_blank()]


def _resolve(row):
    """Ask the server what this row weighs and contains. None when unknown."""
    if row["idk"] or not str(row["food"]).strip():
        return None
    key = f'{row["food"]}|{row["qty"]}|{row["unit"]}'
    if row.get("res") and row["res"].get("key") == key:
        return row["res"]
    text = " ".join(str(x).strip() for x in (row["qty"], row["unit"], row["food"])
                    if str(x).strip())
    res = M.eatzen_parse(text)
    items = (res or {}).get("items") or []
    if not items or not items[0].get("matched"):
        row["res"] = None
        return None
    it = items[0]
    row["res"] = {"key": key, "food": it.get("food"), "grams": it.get("grams"),
                  "na": it.get("na"), "sug": it.get("sug"),
                  "note": it.get("portion_note") or it.get("note") or ""}
    return row["res"]


def _row_sodium(row):
    if row["idk"]:
        return None
    typed = _num(row["na"])
    if typed is not None:
        return typed
    return row["res"]["na"] if row.get("res") else None


def build_eatzen(page):
    p = PAL()
    w = Writer(page, rebuilder(page, _tools_build()))
    if not M.STATUS_LIVE():
        return ft.Column(spacing=8, controls=[offline_banner(p, "EatZen")])

    # Default comes from Settings rather than being hard-coded, so the
    # strict-DASH choice is made once and respected everywhere.
    target = ft.Dropdown(value=str(prefs.sodium_target()), dense=True, filled=True,
                         text_size=12, width=190,
                         border_color=p["border"], color=p["text"],
                         options=[ft.dropdown.Option("1500", "1500 mg (strict DASH)"),
                                  ft.dropdown.Option("2300", "2300 mg (DASH)")])
    total_text = ft.Text("", size=22, weight=ft.FontWeight.W_700, color=p["text"])
    detail_text = ft.Text("", size=11, color=p["text2"])
    meter = ft.ProgressBar(value=0, color=p["accent"], bgcolor=p["bg2"],
                           bar_height=6, border_radius=RAD(3))
    rows_col = ft.Column(spacing=10)

    def recalc():
        na = 0.0
        addsug = 0.0
        unknown = counted = 0
        for r in _rows:
            if not str(r["food"]).strip():
                continue
            _resolve(r)
            value = _row_sodium(r)
            if value is None:
                unknown += 1
            else:
                na += value
                counted += 1
            extra = _num(r["addsug"])
            if extra is not None:
                addsug += extra
        goal = float(target.value or 2300)
        total_text.value = f"{round(na)} mg sodium"
        meter.value = min(1.0, na / goal) if goal else 0
        meter.color = (p["green"] if na < goal * 0.6 else
                       p["yellow"] if na < goal else p["red"])
        bits = [f"{round(na / goal * 100)}% of {int(goal)} mg",
                f"{counted} item{'' if counted == 1 else 's'} counted"]
        if addsug:
            bits.append(f"{round(addsug, 1)} g added sugar")
        if unknown:
            bits.append(f"{unknown} unknown, NOT included")
        detail_text.value = "  ·  ".join(bits)
        detail_text.color = p["pink"] if unknown else p["text2"]

    def field(row, key, hint, width=None, numeric=False):
        def on_change(e):
            row[key] = e.control.value
            if key not in ("na", "addsug"):
                row["res"] = None
            recalc()
            paint()
            page.update()
        return ft.TextField(
            value=str(row[key]), hint_text=hint, dense=True, filled=True, text_size=12,
            width=width, expand=width is None, on_change=on_change,
            keyboard_type=ft.KeyboardType.NUMBER if numeric else None,
            border_color=p["border"], focused_border_color=p["accent"], color=p["text"])

    def row_controls(i, row):
        res = row.get("res")
        if row["idk"]:
            hint = sub("not counted", 10)
        elif res:
            hint = sub(f'{res["food"]} · {res["grams"]} g', 10)
        elif str(row["food"]).strip():
            hint = ft.Text("no match — type the sodium, or add it from the label",
                           size=10, color=p["pink"])
        else:
            hint = ft.Container(height=0)

        def toggle_idk(e):
            row["idk"] = e.control.value
            recalc(); paint(); page.update()

        # One wrapped line, not four stacked ones. This was a full-width
        # food field, then a hint, then a row of four inputs, then a row of
        # controls - all inside a padded card. Empty, a single row stood
        # about four lines tall, and "What you ate" opened as a grey slab
        # taller than the screen. wrap=True lets it reflow to two lines on a
        # phone instead of forcing the page sideways.
        line = ft.Row(spacing=6, wrap=True,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Container(expand=True, content=field(row, "food", "food"),
                         width=None),
            field(row, "qty", "1", width=52),
            field(row, "unit", "unit", width=72),
            field(row, "na", "sodium mg", width=96, numeric=True),
            field(row, "addsug", "sugar g", width=84, numeric=True),
            ft.Checkbox(label="IDK", value=row["idk"], on_change=toggle_idk,
                        label_style=ft.TextStyle(size=11, color=p["text2"])),
            ft.IconButton(ft.Icons.LABEL_OUTLINE, icon_size=17,
                          icon_color=p["text2"], tooltip="Add it from the label",
                          on_click=lambda e, i=i: open_label(i)),
            ft.IconButton(ft.Icons.CLOSE, icon_size=16, icon_color=p["text2"],
                          tooltip="Remove this row",
                          on_click=lambda e, i=i: remove_row(i)),
        ])
        # The hint only takes a line when it has something to say; empty it
        # is a zero-height container, so a blank row really is one line.
        body = [line] if isinstance(hint, ft.Container) else [line, hint]
        return card(ft.Column(spacing=4, controls=body), pad=8)

    def paint():
        rows_col.controls[:] = [row_controls(i, r) for i, r in enumerate(_rows)]

    def remove_row(i):
        if 0 <= i < len(_rows):
            _rows.pop(i)
        if not _rows:
            _rows.append(_blank())
        recalc(); paint(); page.update()

    def add_row(e=None):
        _rows.append(_blank())
        paint(); page.update()

    def open_label(i):
        """Teach it a food from its packaging - the branded-snack case."""
        row = _rows[i]
        f_name = ft.TextField(value=row["food"], label="Name", dense=True, filled=True,
                              text_size=12, border_color=p["border"], color=p["text"])
        f_g = ft.TextField(label="Serving size (g)", hint_text="28", dense=True,
                           filled=True, text_size=12, width=150,
                           keyboard_type=ft.KeyboardType.NUMBER,
                           border_color=p["border"], color=p["text"])
        f_n = ft.TextField(label="Pieces per serving", hint_text="11", dense=True,
                           filled=True, text_size=12, width=160,
                           keyboard_type=ft.KeyboardType.NUMBER,
                           border_color=p["border"], color=p["text"])
        f_u = ft.TextField(value=row["unit"] or "", label="One piece is called",
                           hint_text="chip", dense=True, filled=True, text_size=12,
                           width=160, border_color=p["border"], color=p["text"])
        f_na = ft.TextField(label="Sodium per serving (mg)", hint_text="180", dense=True,
                            filled=True, text_size=12, width=190,
                            keyboard_type=ft.KeyboardType.NUMBER,
                            border_color=p["border"], color=p["text"])
        f_as = ft.TextField(label="Added sugar per serving (g)", hint_text="0",
                            dense=True, filled=True, text_size=12, width=210,
                            keyboard_type=ft.KeyboardType.NUMBER,
                            border_color=p["border"], color=p["text"])
        preview = ft.Text("", size=11, color=p["text2"])

        def show_maths(e=None):
            g, n, na = _num(f_g.value), _num(f_n.value), _num(f_na.value)
            bits = []
            if g and na is not None:
                bits.append(f"per 100 g: {round(na / g * 100)} mg")
            if g and n:
                bits.append(f"one {f_u.value or 'piece'} ≈ {round(g / n, 1)} g")
            if n and na is not None:
                bits.append(f"4 ≈ {round(na / n * 4)} mg")
            preview.value = "  ·  ".join(bits) or "Fill in serving size and sodium."
            page.update()

        for f in (f_g, f_n, f_na, f_u):
            f.on_change = show_maths

        async def save(e):
            g, na = _num(f_g.value), _num(f_na.value)
            if not str(f_name.value).strip():
                w.report(False, "name required"); page.update(); return
            if not g or na is None:
                w.report(False, "serving size and sodium are needed")
                page.update(); return
            n = _num(f_n.value)
            unit = (f_u.value or "serving").strip()
            portions = [["serving", g]]
            if n:
                portions.insert(0, [unit, round(g / n, 2)])
            rec = {"n": f_name.value.strip(), "na": round(na / g * 100, 1),
                   "sug": 0, "k": 0, "kcal": 0, "p": portions}
            extra = _num(f_as.value)
            if extra is not None:
                rec["addsug"] = round(extra / g * 100, 1)
            w.busy()
            ok, msg = M.custom_food_save(rec)
            w.report(ok, msg)
            if ok:
                row["food"] = rec["n"]
                if n and not row["unit"]:
                    row["unit"] = unit
                row["idk"] = False
                row["res"] = None
                _dismiss(page, dlg)
                recalc(); paint()
            page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Add a food from the label", size=15),
            content=ft.Column(tight=True, scroll=ft.ScrollMode.AUTO, width=420,
                              spacing=8, controls=[
                                  sub("Copy the numbers off the bag.", 11),
                                  f_name,
                                  ft.Row(spacing=6, wrap=True, controls=[f_g, f_n]),
                                  ft.Row(spacing=6, wrap=True, controls=[f_u, f_na]),
                                  f_as, preview]),
            actions=[ft.TextButton("Cancel", on_click=lambda e: _dismiss(page, dlg)),
                     ft.FilledButton("Save", on_click=save)])
        _show(page, dlg)
        show_maths()

    sentence = ft.TextField(hint_text="1 hardboiled egg, 4oz portobello, 1 tbsp butter",
                            dense=True, filled=True, text_size=12, expand=True,
                            border_color=p["border"], focused_border_color=p["accent"],
                            color=p["text"])
    use_llm = ft.Checkbox(label="AI match", value=True,
                          label_style=ft.TextStyle(size=11, color=p["text2"]))

    async def do_parse(e):
        text = (sentence.value or "").strip()
        if not text:
            return
        w.busy()
        res = M.eatzen_parse(text, llm=use_llm.value)
        if not res:
            w.report(False, "could not reach the server"); page.update(); return
        _rows[:] = []
        for it in (res.get("items") or []):
            stated = it.get("stated") or []
            qty = "" if it.get("qty") is None else it.get("qty")
            _rows.append({
                "food": it.get("food") or it.get("name") or it.get("input"),
                "qty": qty, "unit": it.get("unit") or "",
                "na": it.get("na") if "na" in stated else "",
                "addsug": it.get("addsug") if "addsug" in stated else "",
                "idk": not it.get("matched"),
                "res": ({"key": f'{it.get("food")}|{qty}|{it.get("unit") or ""}',
                         "food": it.get("food"), "grams": it.get("grams"),
                         "na": it.get("na"), "sug": it.get("sug"),
                         "note": it.get("portion_note") or ""}
                        if it.get("matched") else None)})
        if not _rows:
            _rows.append(_blank())
        w.report(True, "matched with the local model — check the rows"
                 if res.get("llm_used") else "broken down — check the rows")
        recalc(); paint(); page.update()

    when = ft.TextField(value=_now_local(), label="when", dense=True, filled=True,
                        text_size=12, width=190, border_color=p["border"],
                        color=p["text"])

    async def do_log(e):
        items = []
        for r in _rows:
            if not str(r["food"]).strip():
                continue
            items.append({"food": r["food"], "qty": r["qty"], "unit": r["unit"],
                          "idk": bool(r["idk"]), "na": _row_sodium(r),
                          "addsug": _num(r["addsug"]),
                          "resolved": (r["res"] or {}).get("food"),
                          "grams": (r["res"] or {}).get("grams")})
        if not items:
            w.report(False, "nothing to log"); page.update(); return
        na = sum(i["na"] or 0 for i in items)
        addsug = sum(i["addsug"] or 0 for i in items)
        entry = {"id": f"ez-{int(time.time() * 1000)}", "ts": when.value or _now_local(),
                 "items": items, "totals": {"na": round(na), "addsug": round(addsug, 1)},
                 "unknown": sum(1 for i in items if i["na"] is None)}
        w.busy()
        ok, msg = M.eatzen_log(entry)
        w.report(ok, f"logged {round(na)} mg sodium" if ok else msg)
        if ok:
            _rows[:] = [_blank()]
            w.rebuild()
        else:
            page.update()

    entries = M.eatzen_entries()
    by_day = {}
    for entry in entries:
        by_day.setdefault(str(entry.get("ts") or "")[:10], []).append(entry)
    log_controls = []
    for day in sorted(by_day, reverse=True)[:7]:
        log_controls.append(ft.Text(f"{day} — {M.eatzen_day_total(entries, day)} mg",
                                    size=12, weight=ft.FontWeight.W_600,
                                    color=p["text"]))
        for entry in by_day[day]:
            names = ", ".join(i.get("food", "") for i in (entry.get("items") or []))[:60]
            log_controls.append(ft.Row(spacing=6, controls=[
                sub(str(entry.get("ts") or "")[11:16], 10),
                ft.Text(names, size=11, color=p["text2"], expand=True),
                ft.Text(f'{(entry.get("totals") or {}).get("na", 0)} mg', size=11,
                        color=p["text"]),
                ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color=p["text2"],
                              on_click=w.run(M.eatzen_delete, entry.get("id")))]))
    if not log_controls:
        log_controls = [sub("Nothing logged yet.", 11)]

    recalc()
    paint()
    # Dropdown uses on_select in Flet 0.86; on_change never fires, so the
    # total silently ignored the 1500/2300 mg switch.
    target.on_select = lambda e: (recalc(), page.update())

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            title("Describe it", 14),
            sentence,
            ft.Row(spacing=6, controls=[use_llm, ft.Container(expand=True),
                                        ft.FilledButton("Break it down", height=34,
                                                        on_click=do_parse)]),
            sub("Optional — fills the rows below. Nothing saves until you press Log.",
                10),
        ])),
        ft.Row([title("What you ate", 14), ft.Container(expand=True),
                ft.TextButton("Add row", on_click=add_row)]),
        rows_col,
        card(ft.Column(spacing=8, controls=[
            total_text, meter, detail_text,
            ft.Row(spacing=8, controls=[target, ft.Container(expand=True)]),
            ft.Row(spacing=8, controls=[when,
                                        ft.FilledButton("Log it", height=34,
                                                        on_click=do_log)]),
        ])),
        card(ft.Column(spacing=6, controls=[title("Logged", 13), *log_controls])),
        w.control,
    ])


def build_bpm(page):
    p = PAL()
    w = Writer(page, rebuilder(page, _tools_build()))
    if not M.STATUS_LIVE():
        return ft.Column(spacing=8, controls=[offline_banner(p, "BPM")])

    def num_field(label, width):
        return ft.TextField(label=label, dense=True, filled=True, text_size=12,
                            width=width, keyboard_type=ft.KeyboardType.NUMBER,
                            border_color=p["border"], focused_border_color=p["accent"],
                            color=p["text"])

    f_sys = num_field("top (systolic)", 150)
    f_dia = num_field("bottom (diastolic)", 165)
    f_hr = num_field("heart rate", 130)
    f_when = ft.TextField(value=_now_local(), label="when", dense=True, filled=True,
                          text_size=12, width=190, border_color=p["border"],
                          color=p["text"])
    f_note = ft.TextField(label="note", dense=True, filled=True, text_size=12,
                          expand=True, border_color=p["border"], color=p["text"])

    async def add(e):
        sys_, dia = _num(f_sys.value), _num(f_dia.value)
        if sys_ is None or dia is None:
            w.report(False, "top and bottom are required"); page.update(); return
        hr = _num(f_hr.value)
        reading = {"id": f"bp-{int(time.time() * 1000)}",
                   "ts": f_when.value or _now_local(),
                   "sys": int(sys_), "dia": int(dia),
                   "hr": int(hr) if hr is not None else None,
                   "note": f_note.value or ""}
        w.busy()
        ok, msg = M.bpm_add(reading)
        w.report(ok, "reading saved" if ok else msg)
        if ok:
            w.rebuild()
        else:
            page.update()

    readings = M.bpm_readings()
    entries = M.eatzen_entries()
    colour = {"normal": p["green"], "elev": p["yellow"], "high1": p["orange"],
              "high2": p["red"], "crisis": p["red"]}

    rows = []
    for b in readings[:30]:
        key, label = M.bp_category(b.get("sys"), b.get("dia"))
        na = M.eatzen_day_total(entries, str(b.get("ts") or "")[:10])
        rows.append(card(ft.Column(spacing=4, controls=[
            ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(f'{b.get("sys")}/{b.get("dia")}', size=16,
                        weight=ft.FontWeight.W_700, color=p["text"]),
                chip(label, colour.get(key, p["text2"]), small=True),
                ft.Container(expand=True),
                sub(str(b.get("ts") or "").replace("T", " "), 10),
                ft.IconButton(ft.Icons.CLOSE, icon_size=14, icon_color=p["text2"],
                              on_click=w.run(M.bpm_delete, b.get("id")))]),
            ft.Row(spacing=14, controls=[
                sub(f'hr {b.get("hr")}' if b.get("hr") else "hr —", 11),
                sub(f"{na} mg sodium that day" if na else "no intake logged", 11),
            ]),
            *([sub(b["note"], 10)] if b.get("note") else []),
        ]), pad=10))
    if not rows:
        rows = [card(sub("No readings yet.", 11), pad=12)]

    summary = []
    if readings:
        n = min(len(readings), 7)
        avg_s = round(sum(float(b.get("sys") or 0) for b in readings[:n]) / n)
        avg_d = round(sum(float(b.get("dia") or 0) for b in readings[:n]) / n)
        key, label = M.bp_category(avg_s, avg_d)
        summary = [card(ft.Row(
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(f"{avg_s}/{avg_d}", size=24, weight=ft.FontWeight.W_700,
                        color=p["text"]),
                ft.Column(spacing=2, controls=[
                    sub(f"average of last {n}", 10),
                    chip(label, colour.get(key, p["text2"]), small=True)]),
            ]))]

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            title("New reading", 14),
            ft.Row(spacing=6, wrap=True, controls=[f_sys, f_dia, f_hr]),
            ft.Row(spacing=6, controls=[f_when, f_note]),
            ft.Row([ft.FilledButton("Add", height=34, on_click=add)]),
        ])),
        *summary,
        *rows,
        w.control,
    ])
