"""What a surface is made of above the widget level.

These were all defined inside the Lab page and imported out of it by seven
others - ``from .lab import _tabs`` in Garage, FoodZen, Wat2Do, Tools,
Trips, Weather and Extras. That made Lab a dependency of pages that have
nothing to do with Lab, and it is why Lab could not be moved or split
without touching all of them. They are framework, so they live in the
framework.

Nothing here reaches the network. A tab bar, a dialog, an error card and a
byte formatter are drawn from what the caller already has; see
``agedashii_washi.store`` for the one part of the system that does talk to
a server, and note that it queues rather than blocks when there is nobody
to talk to.
"""
import flet as ft

from agedashii_qi import PAL, RAD, S
from .widgets import BRD, PAD, card, chip, dot, sub, title
from . import config

_TAB_INDEX = {}


def _tabs(page, tabs, key=None):
    """A tab bar. A tab's body may be a control, or a callable built on demand.

    Lab passes callables, and the reason is the whole of the topbar-dot bug:
    ``build()`` used to construct all five tabs before anything painted, and
    four of them read the network. Timed against the live server on the Pi
    that was 15.4 s cold and 12.4 s warm - Machines 6.1 s, Docker 4.1 s,
    Qbit 3.1 s, Services 2.0 s - during which Flet's event loop is inside a
    synchronous handler, the old frame stays on screen and further taps are
    dropped. On the laptop, tapping a status dot ran for 9.84 s and swallowed
    the next nine taps, which is exactly what "clicking does nothing" is.

    So only the tab you are looking at is built now; the rest are built the
    first time they are selected, and a background pass fills them in shortly
    after the first paint so a swipe never lands on an empty page. Callers
    that pass plain controls (every other page) are unchanged.
    """
    p = PAL()
    selected = max(0, min(_TAB_INDEX.get(key, 0), len(tabs) - 1)) if key else 0
    slots = [ft.Container(padding=PAD(10, 0)) for _ in tabs]
    built = [False] * len(tabs)

    def fill(i):
        if not (0 <= i < len(tabs)) or built[i]:
            return False
        built[i] = True
        body = tabs[i][1]
        try:
            slots[i].content = body() if callable(body) else body
        except Exception as e:  # noqa: BLE001 - one bad tab is not the page
            slots[i].content = _error_card(f"{tabs[i][0]} could not be built.", e)
        return True

    fill(selected)
    for i, (_label, body) in enumerate(tabs):
        if not callable(body):
            fill(i)

    def on_change(e):
        i = e.control.selected_index
        if key is not None:
            _TAB_INDEX[key] = i
        if fill(i):
            try:
                page.update()
            except Exception:  # noqa: BLE001 - torn down mid-switch
                pass

    async def fill_the_rest():
        # Off the UI thread: building a tab means reading the server.
        import asyncio
        await asyncio.sleep(0)
        changed = await asyncio.to_thread(
            lambda: any([fill(i) for i in range(len(tabs))]))
        if changed:
            try:
                page.update()
            except Exception:  # noqa: BLE001
                pass

    if any(callable(body) for _label, body in tabs):
        runner = getattr(page, "run_task", None)
        if callable(runner):
            try:
                runner(fill_the_rest)
            except Exception:  # noqa: BLE001 - no loop, on_change still fills
                pass

    return ft.Tabs(
        length=len(tabs), selected_index=selected, animation_duration=150, expand=True,
        on_change=on_change,
        content=ft.Column(spacing=0, expand=True, controls=[
            ft.TabBar(
                tabs=[ft.Tab(label=t) for t, _ in tabs],
                indicator_color=p["accent"], divider_color=p["border"],
                label_color=p["accent"], unselected_label_color=p["text2"],
                overlay_color=ft.Colors.with_opacity(0.08, p["accent"]),
            ),
            ft.TabBarView(expand=True, controls=slots),
        ]),
    )


def _error_card(head, detail, icon=ft.Icons.ERROR_OUTLINE):
    p = PAL()
    return card(ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
                       controls=[
        ft.Icon(icon, size=16, color=p["red"]),
        ft.Column(spacing=2, expand=True, controls=[
            ft.Text(head, size=12, weight=ft.FontWeight.W_500, color=p["text"]),
            ft.Text(str(detail), size=11, color=p["text2"], selectable=True),
        ]),
    ]), pad=10)


def _unreachable_card(what):
    return _error_card(
        f"Cannot reach the dashbored server, so {what} is unknown.",
        config.STATUS.get("detail") or "no detail", ft.Icons.CLOUD_OFF)


def _bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _speed(n):
    try:
        if float(n) <= 0:
            return ""
    except (TypeError, ValueError):
        return ""
    return _bytes(n) + "/s"


def _stat(label, value, color=None):
    p = PAL()
    return ft.Column(spacing=1, controls=[
        sub(label, 10),
        ft.Text(str(value), size=13, weight=ft.FontWeight.W_600, color=color or p["text"])])


def _show(page, dlg):
    opener = getattr(page, "show_dialog", None) or getattr(page, "open", None)
    if callable(opener):
        opener(dlg)


def _dismiss(page, dlg=None):
    closer = getattr(page, "pop_dialog", None)
    if callable(closer):
        closer()
        return
    closer = getattr(page, "close", None)
    if callable(closer):
        closer(dlg)


def _confirm(page, headline, detail, verb, run, danger=True):
    """Ask before doing something that cannot be taken back.

    Every destructive control on this page goes through here: rebooting a
    machine, stopping a container, deleting a torrent. `run` is the async
    handler to call once the user says yes; it is only ever called from the
    second tap.
    """
    p = PAL()

    async def yes(e):
        _dismiss(page)
        await run(e)

    dlg = ft.AlertDialog(
        modal=True, bgcolor=p["surface"],
        title=ft.Text(headline, size=14, weight=ft.FontWeight.W_600, color=p["text"]),
        content=ft.Text(detail, size=12, color=p["text2"], selectable=True),
        actions=[
            ft.TextButton(content="Cancel", on_click=lambda e: _dismiss(page, dlg)),
            ft.FilledButton(content=verb, on_click=yes,
                            style=ft.ButtonStyle(bgcolor=p["red"]) if danger else None),
        ])
    _show(page, dlg)
