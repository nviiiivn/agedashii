"""Every ride this phone recorded, and getting the data back out of it.

The list is read off the device, not the server. A ride is complete on the phone
the moment it stops - that is the whole design of the log - so this page works
in the same canyon the ride happened in, and the upload to the server is a
separate thing that may or may not have happened yet.

EXPORT

Three formats, written beside the log, because they answer three different
questions:

    .csv    every channel, one row per sample - opens in anything
    .gpx    the track, with the derived channels in an extensions block
    .json   the samples plus the channel dictionary that explains them

The channel dictionary travelling inside the JSON is the part worth defending: a
column called `g_lat` means nothing in six months, and an export that has to be
read next to this source tree is not an export.

Rides are listed newest first, and each one can be pushed to the server - her
"EASY LINK/PUSH TO AGEDASHI MOTO" - through Washi's outbox, so a push made with
no signal is queued rather than lost.

WHERE THEY GO, ON THE PAGE

Hers: "what happens when i upload/uploaded rides? where do they get saved to?
moto?" The page now says it at the top (`uploads.where()`), and every ride
says whether it has gone: not sent, waiting for signal, sent, or on the
dashboard - the last one only once the server's own list has shown it.
Opening the page pushes anything still queued and asks the server for that
list, off the UI thread and at most once a minute (`uploads.refresh`).
"""
import os
import time

import flet as ft

from .. import guidance, live, prefs, telemetry, ui, uploads
from ..theme import PAL

#: Which ride is expanded, and the last thing an export said.
_S = {"open": "", "note": ""}


def _update(page):
    """Push the change, and SAY SO when that fails - see ride._update."""
    try:
        page.update()
    except Exception as exc:                           # noqa: BLE001
        print(f"[navi] rides: page.update failed: {type(exc).__name__}: "
              f"{exc}", flush=True)


def _rerender(page):
    rerender = getattr(page, "_navi_rerender", None)
    if callable(rerender):
        rerender()


def list_rides(directory=None):
    """Every recorded ride, newest first, as {path, file, size, when}.

    Reads only the directory entry, not the file: a list of forty rides must not
    mean parsing forty logs.
    """
    folder = directory if directory is not None else live.ride_dir()
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".jsonl")]
    except OSError:
        return []
    rows = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        rows.append({"path": path, "file": name, "size": stat.st_size,
                     "when": stat.st_mtime})
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


def read_ride(path):
    """A `telemetry.Ride` reconstructed from a log on disk. See `load_ride`."""
    return telemetry.load_ride(path)


def build(page, go=None):
    p = PAL()
    note = ft.Text(_S["note"], size=ui.SMALL, color=p["text2"])
    # Not the ride still being recorded. Its log is only what has been flushed
    # so far, so a Send from here would put a cut-short copy on the server
    # under the name the finished ride will use. It has its own card above
    # the list, and it is sent whole when she stops it.
    live_ride = live.LIVE.get("ride") if live.recording() else None
    live_path = (os.path.abspath(live_ride.path)
                 if live_ride is not None and live_ride.path else None)
    rows = [row for row in list_rides()
            if os.path.abspath(row["path"]) != live_path]
    sent = uploads.records()

    async def _check():
        """Push anything queued, then ask the server which rides it has.

        Off the UI thread and throttled inside `uploads.refresh`, so the
        rebuild this causes does not ask again. Only repaints when an answer
        changed something, and only while this page is the one on screen.
        """
        import asyncio
        changed, why = await asyncio.to_thread(
            uploads.refresh, [row["path"] for row in rows])
        if why:
            _S["note"] = why
        elif _S["note"] == uploads.UNREACHABLE:
            # Reached it this time: the old "could not reach" is no longer
            # true, so it must not stay on the page looking current.
            _S["note"] = ""
            changed = changed or 1
        if (changed or why) and getattr(page, "_navi_surface", "log") == "log":
            _rerender(page)

    if rows:
        page.run_task(_check)

    def _export(path, kind):
        def handler(_e=None):
            ride = read_ride(path)
            body = {"csv": ride.to_csv, "gpx": ride.to_gpx,
                    "json": ride.to_json}[kind]()
            out = os.path.splitext(path)[0] + "." + kind
            try:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(body)
            except OSError as exc:
                note.value = _S["note"] = f"could not write it: {exc}"
                note.color = PAL()["bad"]
                _update(page)
                return
            note.value = _S["note"] = f"wrote {os.path.basename(out)}"
            note.color = PAL()["good"]
            _update(page)
        return handler

    def _push(path):
        async def run():
            # The one door every upload goes through: the whole log, every
            # channel, and a record of what happened. See uploads.py.
            import asyncio
            note.value = _S["note"] = "sending…"
            _update(page)
            ok, message = await asyncio.to_thread(uploads.send, path)
            _S["note"] = message if ok else f"not sent: {message}"
            _rerender(page)
        return lambda e=None: page.run_task(run)

    def _toggle(path):
        def handler(_e=None):
            _S["open"] = "" if _S["open"] == path else path
            _rerender(page)
        return handler

    def _card(row):
        pal = PAL()
        open_ = _S["open"] == row["path"]
        words, colour = uploads.describe(
            uploads.status(row["path"], data=sent))
        head = ui.row_tap(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.TWO_WHEELER, size=20, color=pal["accent"]),
            ft.Column(spacing=0, expand=True, tight=True, controls=[
                ft.Text(time.strftime("%a %d %b, %H:%M",
                                      time.localtime(row["when"])),
                        size=ui.MID, weight=ft.FontWeight.W_700,
                        color=pal["text"]),
                # Whether it has gone, on the collapsed row, so the answer to
                # "has this one been uploaded" does not need a tap.
                ft.Text(words, size=ui.SMALL, color=pal[colour], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS)]),
            ft.Icon(ft.Icons.EXPAND_LESS if open_ else ft.Icons.EXPAND_MORE,
                    size=20, color=pal["text3"]),
        ]), _toggle(row["path"]), height=ui.TOUCH)
        if not open_:
            return head

        summary = read_ride(row["path"]).summary()
        unit = guidance.speed_unit(prefs.imperial())
        facts = [
            ("distance", guidance.distance_text(summary["distance_m"],
                                                prefs.imperial())),
            ("time", f"{int(summary['duration_s'] // 60)} min"),
            ("top speed", f"{guidance.speed_text(summary['max_speed_ms'], prefs.imperial())} {unit}"),
            ("max lean", f"{summary['max_lean_deg']:.0f}°"),
            ("max cornering", f"{summary['max_g_lat']:.2f} g"),
            ("hardest brake", f"{abs(summary['min_g_long']):.2f} g"),
            ("climb", guidance.distance_text(summary["climb_m"],
                                             prefs.imperial())),
            ("samples", str(summary["samples"])),
        ]
        return ft.Column(spacing=8, controls=[
            head,
            ui.card(ft.Column(spacing=10, controls=[
                ft.Row(wrap=True, spacing=18, run_spacing=8, controls=[
                    ui.stat(name, text) for name, text in facts]),
                ft.Row(spacing=8, controls=[
                    ui.button("CSV", _export(row["path"], "csv"),
                              icon=ft.Icons.TABLE_VIEW, expand=True),
                    ui.button("GPX", _export(row["path"], "gpx"),
                              icon=ft.Icons.ROUTE, expand=True),
                    ui.button("JSON", _export(row["path"], "json"),
                              icon=ft.Icons.DATA_OBJECT, expand=True)]),
                ui.button("Send to the dashboard" if words.startswith("not")
                          else "Send to the dashboard again",
                          _push(row["path"]),
                          icon=ft.Icons.CLOUD_UPLOAD, expand=True),
                ft.Text(words, size=ui.SMALL, color=pal[colour]),
                ft.Text(f"{row['size'] / 1024:.0f} KB · {row['file']} - "
                        "exports are written next to the log, in "
                        + os.path.dirname(row["path"]),
                        size=ui.SMALL, color=pal["text2"]),
            ])),
        ])

    body = ([_card(row) for row in rows] if rows
            else [ui.empty("no rides recorded yet", ft.Icons.TWO_WHEELER)])

    now_recording = []
    if live.recording():
        now_recording.append(ui.card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.FIBER_MANUAL_RECORD, size=18, color=p["bad"]),
            ft.Column(spacing=0, expand=True, tight=True, controls=[
                ft.Text("recording now", size=ui.MID,
                        weight=ft.FontWeight.W_700, color=p["text"]),
                ft.Text(live.summary_line(), size=ui.SMALL,
                        color=p["text2"])]),
        ])))

    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
        *now_recording,
        ft.Row(spacing=8, controls=[
            ft.Text("Rides on this phone", size=ui.MID,
                    weight=ft.FontWeight.W_800, color=p["text"], expand=True),
            ui.chip(str(len(rows)), p["text2"], ft.Icons.FOLDER)]),
        ui.card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.CLOUD_UPLOAD, size=18, color=p["text2"]),
            ft.Text(uploads.where(), size=ui.SMALL, color=p["text2"],
                    expand=True)])),
        note,
        *body,
        ft.Container(height=12),
    ])
