"""Lab: the machines, their network, their containers, and the torrent box.

Every tab here reads the live dashbored server. Where a source is down, the
error it actually gave is printed verbatim instead of being replaced by a
plausible-looking row: a dashboard that invents a torrent, or shows a green
light for a service it never reached, is worse than one that says nothing.

Server quirks that shape the code below and are worth knowing before editing:

* ``/api/qbit`` and ``/api/qbit/action`` answer HTTP 500 with the real cause
  in ``{"error": ...}``. ``api._post`` returns None on any HTTPError, which
  throws that message away, so writes here go through ``_post_json``.
* ``/api/service/control`` answers ``{"ok": true, ...}`` whenever the SSH
  call returned *anything*, including "Unit caddy.service could not be
  found". Its ``ok`` therefore means "the request completed", not "the
  service started", so the output is shown verbatim and never summarised
  into a success. Same for ``/api/power/*``.
* Every widget of an unreachable machine is ``{"error": "unreachable"}``.
  That is a non-empty dict, so ``bool(widget)`` calls it alive - the trap
  ``api.widget_alive`` exists to close. SP3 is down as this is written and
  answers exactly that for system, dotfiles, network and processes.
* ``/api/qbit``'s stats are qBittorrent's own ``transfer/info``, so the keys
  are ``dl_info_speed`` / ``up_info_speed`` / ``free_space_on_disk`` and a
  torrent's upload rate is ``upspeed``. The website reads ``dl_rate``,
  ``ul_rate``, ``free_space`` and ``ulspeed``, which do not exist; its
  Download / Upload / Free Space tiles have therefore always read 0 B/s.
  This page does not copy that.

There is no Things tab, because there is no IoT source on the server: no
hub, no sensors, nothing in /api/config but machines. The website's Things
view is an empty "coming soon" card. The Network tab stands in its place and
shows something real - see ``_network``.
"""
import json
import urllib.error
import urllib.request

import flet as ft

from ..ui import PAL, PAD, BRD, RAD, S, card, chip, title, sub, dot
from .. import api as M
from .. import config
from .. import panels
from ._forms import Writer, rebuilder

# Which tab each page was last looking at, so a write that repaints the page
# does not silently throw you back to the first tab.
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


def build(page):
    return _tabs(page, [("Machines", lambda: _machines(page)),
                        ("Network", lambda: _network()),
                        ("Services", lambda: _services(page)),
                        ("Docker", lambda: _docker(page)),
                        ("Qbit", lambda: _qbit(page))],
                 key="lab")


# ------------------------------------------------------------------ plumbing

def _post_json(path, payload, timeout=20):
    """POST JSON and return (data, error_text); error_text is None on success.

    Deliberately not ``api._post``: urllib raises HTTPError on 4xx/5xx and
    ``_post`` answers None, so the server's own explanation - which is the
    only thing that tells you *why* qBittorrent or systemctl said no - never
    reaches the screen. A 4xx/5xx also does not mean the dashbored server is
    down, so it must not mark the connection offline.
    """
    req = urllib.request.Request(
        config.SERVER_URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
        return data, (str(data["error"]) if isinstance(data, dict) and data.get("error")
                      else None)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001 - an HTML error page is still a failure
            body = {}
        detail = body.get("error") if isinstance(body, dict) else None
        return body, str(detail or config._describe(e))
    except Exception as e:  # noqa: BLE001
        config._remember(config.SERVER_URL, False, config._describe(e))
        return None, config._describe(e)


def _rebuild(page):
    """Re-read from the server and repaint the whole app.

    Prefers the repaint main.py parks on S, because ``_forms.rebuilder``
    replaces page.controls outright and would drop the topbar and the nav bar
    along with the tab it was refreshing.
    """
    fallback = rebuilder(page, build)

    def run():
        fn = getattr(S, "rerender", None)
        if callable(fn):
            M.refresh()
            fn()
        else:
            fallback()
    return run


def _dashboard():
    """Raw /api/dashboard, or None. Shares api's cache with M.MACHINES.

    The timeout is generous because the server SSHes to every machine to
    build this; api's 2.5s default suits endpoints that answer from disk.
    """
    return M._get("/api/dashboard", timeout=25)


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


def _gauge(value, label, size=58):
    """A ring with its own number in it, the website's machine-card gauge.

    Colour is the website's: green under 60, amber to 80, red above. A value
    the server did not send draws an empty ring and an em dash rather than a
    confident 0%, which would read as an idle machine.
    """
    p = PAL()
    try:
        v = max(0.0, min(100.0, float(value)))
        known = True
    except (TypeError, ValueError):
        v, known = 0.0, False
    colour = p["green"] if v < 60 else (p["yellow"] if v < 80 else p["red"])
    return ft.Column(spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Stack(width=size, height=size, alignment=ft.Alignment(0, 0), controls=[
            ft.ProgressRing(value=(v / 100.0) if known else 0.0, width=size, height=size,
                            stroke_width=5, color=colour if known else p["border"],
                            bgcolor=ft.Colors.with_opacity(0.10, p["text2"])),
            ft.Text(f"{v:.0f}%" if known else "—", size=11,
                    weight=ft.FontWeight.W_600, color=p["text"]),
        ]),
        sub(label, 10),
    ])


def _fact(label, value):
    p = PAL()
    return ft.Column(spacing=1, controls=[
        sub(label, 10),
        ft.Text(str(value or "—"), size=12, color=p["text"], selectable=True)])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ machines

def _temp(raw):
    """"45C" -> "45 °C". The old card printed f"{temp}°C" and got "45C°C"."""
    s = str(raw or "").strip()
    if not s:
        return ""
    s = s.rstrip("Cc°").strip()
    return f"{s} °C" if s else ""


def _os_line(greeting):
    """The OS line out of the machine's fetch-style greeting blob.

    The greeting is several newline-separated "Key: value" lines. The old
    code split it on "·", which is not in it, so Tower's OS read
    "CachyOS x86_64\\nKernel: Linux" - two lines welded together.
    """
    for line in str(greeting or "").replace("\\n", "\n").split("\n"):
        line = line.strip()
        if line.lower().startswith("os:"):
            return line.split(":", 1)[1].strip()
    first = str(greeting or "").replace("\\n", "\n").split("\n")[0].strip()
    return first[:40]


def _gpu_name(raw):
    """"NVIDIA GeForce RTX 3080, 14381 MiB, 20480 MiB, 22 %" -> the card's name."""
    return str(raw or "").split(",")[0].strip()


# Which machine the topbar dots asked for. main.py sets it on the way in so
# a tap lands on that machine's own detail rather than dropping you at the
# top of the Machines tab to go hunting.
OPEN_MACHINE = {"id": None}


def _rate(v):
    """Bytes per second, where a measured 0 is a reading and None is not.

    "0 B/s" means the interface is quiet. An em dash means the server has
    only taken one sample of the counters so far and cannot yet say.
    """
    return "—" if v is None else _bytes(v) + "/s"


def _gb(mb):
    return "—" if mb is None else f"{mb / 1024:.1f} GB"


def _pctv(v):
    return "—" if v is None else f"{v:.0f}%"


def _disk_line(d):
    """One drive: its model, whether it spins, and how big it is."""
    kind = "HDD" if d.get("rotational") else "SSD"
    model = str(d.get("model") or "").strip() or str(d.get("name") or "?")
    return f"{model} — {kind}, {_bytes(d.get('bytes'))}"


def _facts_grid(pairs):
    """Label/value pairs, two to a row on a phone and four on a desktop."""
    return ft.ResponsiveRow(columns=12, spacing=10, run_spacing=8, controls=[
        ft.Container(col={"xs": 6, "sm": 4, "lg": 3}, content=_fact(label, value))
        for label, value in pairs])


def _group(heading, pairs):
    return ft.Column(spacing=5, controls=[
        sub(heading.upper(), 9), _facts_grid(pairs)])


def _machine_detail_body(d):
    """Every field the owner asked for, for one machine, in groups.

    Anything the machine did not answer is an em dash. That is not a
    cosmetic choice: SP3 is down and answers {"error": "unreachable"} for
    every widget, and a 0 here would read as an idle, healthy machine.
    """
    p = PAL()
    rows = [
        _group("Identity", [
            ("Nickname", d["nickname"]),
            ("Official name", d["name"]),
            ("Type", d["machine_type"]),
            ("Hostname", d["hostname"]),
            ("Reached at", d["address"]),
            ("IP", d["ip"]),
        ]),
        _group("System", [
            ("OS", d["os"]),
            ("OS base", d["os_base"]),
            ("Kernel", d["kernel"]),
            ("Uptime", d["uptime"]),
        ]),
        _group("Processor", [
            ("CPU", d["cpu_name"]),
            ("Cores", d["cpu_cores"]),
            ("CPU load", _pctv(d["cpu_pct"])),
            # The server sends "45C" already. The old card printed
            # f"{temp}°C" and got "45C°C".
            ("CPU temp", _temp(d["cpu_temp"]) or None),
        ]),
        _group("Memory", [
            ("RAM", (f"{_gb(d['ram_used_mb'])} of {_gb(d['ram_total_mb'])}"
                     if d["ram_total_mb"] else None)),
            ("RAM load", _pctv(d["ram_pct"])),
            ("RAM type", d["ram_type"]),
        ]),
        _group("Graphics", [
            ("GPU", d["gpu_name"]),
            ("GPU memory", (f"{_gb(d['gpu_mem_used_mb'])} of {_gb(d['gpu_mem_total_mb'])}"
                            if d["gpu_mem_total_mb"] else None)),
            ("GPU load", _pctv(d["gpu_pct"])),
            ("GPU temp", _temp(d["gpu_temp"]) or None),
        ]),
        _group("Storage", [
            ("Root filesystem", (f"{d['disk_used']} of {d['disk_total']}"
                                 if d["disk_total"] else None)),
            ("Disk used", _pctv(d["disk_pct"])),
        ] + [(f"Drive {i + 1}", _disk_line(x)) for i, x in enumerate(d["disks"])]
          + ([] if d["disks"] else [("Drives", None)])),
        _group("Network", [
            ("Down now", _rate(d["net_rx_rate"])),
            ("Up now", _rate(d["net_tx_rate"])),
            # /proc/net/dev counts from boot, so this is exactly "since it
            # came up" - the same window as Uptime above.
            ("Down this uptime", "—" if d["net_rx_total"] is None
             else _bytes(d["net_rx_total"])),
            ("Up this uptime", "—" if d["net_tx_total"] is None
             else _bytes(d["net_tx_total"])),
            ("Connections", d["net_connections"]),
        ] + [(str(i.get("name") or "?"), f"{i.get('ip')} · {i.get('state')}")
             for i in d["net_ifaces"]]),
        _group("Running", [
            ("Listening ports", d["services_listening"]),
            ("Services up", (f"{d['units_running']} of {d['units_total']}"
                             if d["units_total"] else None)),
            ("Containers", (f"{d['containers_running']} of {d['containers_total']} running"
                            if d["containers_total"] is not None else None)),
        ]),
    ]
    out = []
    for i, r in enumerate(rows):
        if i:
            out.append(ft.Divider(height=1, color=p["border"]))
        out.append(r)
    return ft.Column(spacing=8, controls=out)


def _machines(page):
    p = PAL()
    w = Writer(page, _rebuild(page))
    dash = M.dashboard()
    if dash is None:
        return ft.Column(spacing=10, controls=[_unreachable_card("the machine list")])

    cfg = M.machine_config() or {}
    # NOT M.docker_list(). That SSHes to five hosts and took 6.17 s against
    # the live server, which is most of the 9.84 s a topbar dot spent frozen
    # before this page appeared. Home already learned this lesson; Lab had
    # not. The store's copy paints now and `catch_up` fetches the real one
    # behind the page.
    docker = M._cached("/api/docker_list")
    ids = sorted(k for k in dash if isinstance(dash.get(k), dict))
    # Sticky for as long as Lab is the page on screen; main.py clears it on
    # the way to anywhere else. Clearing it here instead would work until
    # the first repaint - a write, a theme change, docker arriving behind
    # the page - and then quietly fold the card the owner had just opened.
    wanted = OPEN_MACHINE.get("id") if OPEN_MACHINE.get("id") in dash else None

    def repaint():
        """Repaint from what we already hold. Deliberately not _rebuild.

        Opening a card is a layout change, not new information: re-reading
        the server for it would put a network round trip behind a disclosure
        triangle, which is the mistake this whole change is undoing.
        """
        fn = getattr(S, "rerender", None)
        if callable(fn):
            fn()
        else:
            rebuilder(page, build)()

    async def catch_up():
        import asyncio
        fresh, _err = await asyncio.to_thread(M.docker_list)
        if isinstance(fresh, dict) and fresh != docker:
            fn = getattr(S, "rerender", None)
            if callable(fn):
                fn()

    if docker is None:
        runner = getattr(page, "run_task", None)
        if callable(runner):
            try:
                runner(catch_up)
            except Exception:  # noqa: BLE001 - no loop; the count stays unknown
                pass

    def containers_for(mid):
        host = (docker or {}).get(mid) or {}
        if host.get("error"):
            return None
        rows = [c for c in (host.get("containers") or []) if isinstance(c, dict)]
        if not rows:
            return None
        running = sum(1 for c in rows if c.get("state") == "running")
        return f"{running}/{len(rows)} running"

    def power_row(mid, display):
        """Wake, shut down, reboot. Two of the three ask first."""
        def send(action):
            async def handler(e):
                w.busy()
                ok, msg = M.power(mid, action)
                w.report(ok, f"{action} {display} — server said: {msg}" if ok else msg)
                page.update()
            return handler

        def guarded(action, verb):
            async def handler(e):
                _confirm(page, f"{verb} {display}?",
                         f"This sends {action} to {mid} over SSH straight away. "
                         f"Nothing here can bring it back up except Wake, and that "
                         f"needs a MAC address configured on the server.",
                         verb, send(action))
            return handler

        return ft.Row(spacing=6, wrap=True, controls=[
            ft.OutlinedButton(content="Wake", height=30, icon=ft.Icons.POWER_SETTINGS_NEW,
                              on_click=send("wake")),
            ft.OutlinedButton(content="Shutdown", height=30, icon=ft.Icons.STOP_CIRCLE_OUTLINED,
                              on_click=guarded("shutdown", "Shut down")),
            ft.OutlinedButton(content="Reboot", height=30, icon=ft.Icons.RESTART_ALT,
                              style=ft.ButtonStyle(color=p["red"]),
                              on_click=guarded("reboot", "Reboot")),
        ])

    def dotfiles_section(mid, wd):
        if wd.get("error"):
            return sub(f"dotfiles: {wd['error']}", 10)
        rows = [d for d in (wd.get("dotfiles") or []) if isinstance(d, dict)]
        present = [d for d in rows if d.get("exists")]
        if not rows:
            return None
        return ft.ExpansionTile(
            title=ft.Text(f"Dotfiles ({len(present)} of {len(rows)} present)",
                          size=12, color=p["text"]),
            tile_padding=PAD(0, 0), controls_padding=PAD(0, 0), dense=True,
            collapsed_icon_color=p["text2"], icon_color=p["accent"],
            controls=[ft.Column(spacing=2, controls=[
                _dotfile_row(page, w, mid, d) for d in rows])])

    def terminal_section(mid, ready):
        if not ready:
            return sub("the server does not offer a shell on this machine", 10)
        out = ft.Text("", size=11, color=p["text2"], selectable=True,
                      font_family="monospace")
        field = ft.TextField(hint_text="uptime", dense=True, filled=True, text_size=12,
                             expand=True, border_color=p["border"],
                             focused_border_color=p["accent"], color=p["text"])

        async def run(e):
            cmd = (field.value or "").strip()
            if not cmd:
                return
            out.value = "running…"
            page.update()
            text, err = M.run_command(mid, cmd)
            out.value = err if err else (text or "(no output)")
            out.color = p["red"] if err else p["text2"]
            page.update()

        field.on_submit = run
        return ft.ExpansionTile(
            title=ft.Text("Terminal", size=12, color=p["text"]),
            tile_padding=PAD(0, 0), controls_padding=PAD(0, 0), dense=True,
            collapsed_icon_color=p["text2"], icon_color=p["accent"],
            controls=[ft.Column(spacing=6, controls=[
                ft.Row(spacing=6, controls=[
                    field, ft.IconButton(ft.Icons.PLAY_ARROW, icon_color=p["accent"],
                                         tooltip="run over the server's SSH",
                                         on_click=run)]),
                sub("Runs as the configured user on that machine, through the "
                    "dashbored server. Output is shown exactly as it came back.", 10),
                out,
            ])])

    def headline(detail, online, error):
        """The collapsed card's one line. Cheap on purpose - see machine_card."""
        if not online:
            return ft.Text(str(error or "the server sent nothing for this machine"),
                           size=11, color=p["red"], max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS)
        bits = [f"CPU {_pctv(detail['cpu_pct'])}", f"RAM {_pctv(detail['ram_pct'])}"]
        if _temp(detail["cpu_temp"]):
            bits.append(_temp(detail["cpu_temp"]))
        if detail["uptime"]:
            bits.append("up " + detail["uptime"])
        return ft.Text(" · ".join(bits), size=11, color=p["text2"],
                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    def machine_card(mid):
        """Collapsed by default; everything on demand, for one machine only.

        This used to build every section of every card whether or not
        anyone opened it: 1756 controls in this tab, about 350 a machine,
        including 84 dotfile editors and four terminals nobody had asked
        for. Python built it in 0.05 s and Flutter then had to lay the
        whole thing out, which is why a topbar dot felt slow even after the
        server reads came off the paint path - it navigates to the heaviest
        screen in the app.

        So the card is now a name, a state and one line of headline
        numbers, and the gauges, the full field list, the processes, the
        models, the dotfile editors, the terminal and the power buttons are
        built only for the machine that is open. Which machine that is
        lives in OPEN_MACHINE, so it survives a repaint - a write, a theme
        change, docker arriving behind the page.
        """
        m = dash[mid]
        sys_w = M.machine_widget(dash, mid, "system")
        online = M.widget_alive(sys_w)
        colour = m.get("color") or p["accent"]
        display = m.get("display") or mid
        conf = cfg.get(mid) or {}
        is_open = mid == wanted

        def toggle(e):
            OPEN_MACHINE["id"] = None if is_open else mid
            repaint()

        head = ft.Container(
            ink=True, border_radius=RAD(8), on_click=toggle,
            tooltip=("hide this machine's detail" if is_open
                     else f"show everything about {display}"),
            content=ft.Row([
                dot(colour, dim=not online),
                ft.Column(spacing=0, expand=True, controls=[
                    title(display, 14),
                    sub(str(sys_w.get("hostname") or mid), 10)]),
                chip("online" if online else "offline",
                     p["green"] if online else p["red"], small=True),
                ft.Icon(ft.Icons.EXPAND_LESS if is_open else ft.Icons.EXPAND_MORE,
                        size=20, color=p["text2"]),
            ]))

        if not is_open:
            return card(ft.Column(spacing=6, controls=[
                head, headline(M.machine_detail(dash, mid, cfg, docker),
                               online, sys_w.get("error"))]))

        detail = M.machine_detail(dash, mid, cfg, docker)
        body = [head, ft.Divider(height=1, color=p["border"])]

        if not online:
            body.append(ft.Text(
                str(sys_w.get("error") or "the server sent nothing for this machine"),
                size=12, color=p["red"], selectable=True))
            body.append(sub("Everything below is unknown while it is down - not zero.", 10))
            body.append(_machine_detail_body(detail))
            body.append(power_row(mid, display))
            # No w.control here: it wraps one shared ft.Text, and a second
            # machine going down would put the same control in two trees.
            # One status line sits under the whole stack instead.
            return card(ft.Column(spacing=8, controls=body))

        gauges = [_gauge(sys_w.get("cpu_percent"), "CPU"),
                  _gauge(sys_w.get("memory_percent"), "RAM"),
                  _gauge(sys_w.get("disk_percent"), "Disk")]
        if str(sys_w.get("gpu") or "").strip():
            gauges.append(_gauge(sys_w.get("gpu_percent"), "GPU"))
        body.append(ft.Row(spacing=14, wrap=True, run_spacing=8, controls=gauges))

        mem_used, mem_total = _num(sys_w.get("mem_used")), _num(sys_w.get("mem_total"))
        facts = [_fact("Uptime", sys_w.get("uptime")),
                 _fact("OS", _os_line(sys_w.get("greeting")))]
        if mem_used is not None and mem_total:
            facts.append(_fact("Memory", f"{mem_used / 1024:.1f} / {mem_total / 1024:.1f} GB"))
        if sys_w.get("disk"):
            facts.append(_fact("Disk", sys_w.get("disk")))
        if _temp(sys_w.get("temp")):
            facts.append(_fact("Temp", _temp(sys_w.get("temp"))))
        if conf.get("host"):
            facts.append(_fact("Address", f"{conf.get('user') or '?'}@{conf['host']}"))
        cts = containers_for(mid)
        if cts:
            facts.append(_fact("Containers", cts))
        units, _unit_err = _units_for(dash, mid)
        if units:
            facts.append(_fact("Listening", f"{len(units)} processes"))
        if _gpu_name(sys_w.get("gpu")):
            facts.append(_fact("GPU", _gpu_name(sys_w.get("gpu"))))
        body.append(ft.Row(spacing=16, wrap=True, run_spacing=8, controls=facts))
        body.append(ft.Divider(height=1, color=p["border"]))
        body.append(_machine_detail_body(detail))

        procs = M.machine_widget(dash, mid, "processes")
        top = [x for x in (procs.get("cpu") or []) if isinstance(x, dict)][:6]
        if top:
            body.append(sub("Top processes", 10))
            body.append(ft.Column(spacing=2, controls=[
                ft.Row(spacing=6, controls=[
                    ft.Text(str(x.get("cmd") or x.get("user") or "?"), size=11,
                            color=p["text"], expand=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    # ps sends these as strings, so no .1f without a cast.
                    ft.Text(f"{_num(x.get('cpu')) or 0:.1f}%", size=11,
                            color=p["text2"], width=48,
                            text_align=ft.TextAlign.RIGHT),
                    ft.Text(f"{_num(x.get('mem')) or 0:.1f}%", size=11,
                            color=p["text2"], width=48,
                            text_align=ft.TextAlign.RIGHT),
                ]) for x in top]))
        elif procs.get("error"):
            body.append(sub(f"processes: {procs['error']}", 10))

        models = M.ollama_models(dash, mid)
        if models:
            running = set(M.machine_widget(dash, mid, "ollama").get("running") or [])
            body.append(sub(f"Ollama — {len(models)} models"
                            + (f", {len(running)} loaded" if running else ""), 10))
            body.append(ft.Row(spacing=5, wrap=True, run_spacing=5, controls=[
                chip(name, p["green"] if name in running else p["accent"], small=True)
                for name in models[:14]]))
            if len(models) > 14:
                body.append(sub(f"+{len(models) - 14} more", 10))

        dots = dotfiles_section(mid, M.machine_widget(dash, mid, "dotfiles"))
        if dots is not None:
            body.append(dots)
        body.append(terminal_section(
            mid, bool(M.machine_widget(dash, mid, "terminal").get("ready"))))
        body.append(power_row(mid, display))
        return card(ft.Column(spacing=8, controls=body))

    def portal_panel():
        rows = []
        for mid in ids:
            conf = cfg.get(mid) or {}
            display = (dash[mid].get("display") or mid)
            host, user = conf.get("host"), conf.get("user")
            line = (f"ssh {user}@{host}" if host and user
                    else ("this machine is the server itself" if mid in cfg
                          else "no address in /api/config"))
            rows.append(ft.Row(spacing=8, controls=[
                dot(dash[mid].get("color") or p["accent"],
                    dim=not M.widget_alive(M.machine_widget(dash, mid, "system")), size=8),
                ft.Text(display, size=12, color=p["text"], width=88),
                ft.Text(line, size=11, color=p["text2"], expand=True,
                        selectable=True, font_family="monospace"),
            ]))
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Portal", 14), ft.Container(expand=True),
                    ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                                  tooltip="re-read from the server",
                                  on_click=lambda e: w.rebuild())]),
            sub("Addresses from /api/config. Long-press to copy an ssh line; "
                "power controls are on each machine's card below.", 10),
            *rows,
            # The website's Portal also lists Portainer, Proxmox, Grafana,
            # Sentry, BreachWatch and CommonShield per host. Every one of
            # those was probed from here while writing this and none
            # answered, so they are not reproduced: a grid of dead links
            # that look live is the same lie as invented data.
        ]))

    def machine_stack():
        if not ids:
            return card(sub("The server listed no machines.", 11), pad=12)
        # The machine a dot asked for goes first as well as opening, because
        # Flet has no scroll-to-control and an expanded card four rows down
        # on a phone is still a card you have to go looking for.
        order = ([wanted] + [i for i in ids if i != wanted]) if wanted else ids
        return ft.Column(spacing=10, controls=[
            ft.ResponsiveRow(columns=12, spacing=10, run_spacing=10, controls=[
                ft.Container(col={"xs": 12, "lg": 6}, content=machine_card(mid))
                for mid in order]),
            w.control])

    # Not `offline_banner`, which says "showing sample data". Nothing on this
    # tab is sample data any more - it is the store's copy of what the server
    # last said, which is a different and much more useful thing to be told.
    banner = []
    if not M.STATUS_LIVE():
        banner = [card(ft.Row(spacing=8, controls=[
            ft.Icon(ft.Icons.HISTORY, size=16, color=p["yellow"]),
            ft.Text("Not connected. These readings are this device's copy of "
                    "what the server last said, not live — and nothing here "
                    "can be started, stopped or rebooted until it is back.",
                    size=11, color=p["text2"], expand=True)]), pad=10)]
    return ft.Column(spacing=10, expand=True, controls=banner + [
        ft.Container(expand=True, content=panels.layout("lab.machines", {
            "portal": portal_panel, "machines": machine_stack}))])


def _dotfile_row(page, w, mid, d):
    """One dotfile line, with a viewer/editor behind it when it exists."""
    p = PAL()
    path = str(d.get("path") or "?")
    exists = bool(d.get("exists"))
    when = str(d.get("modified") or "")

    async def open_it(e):
        text, err = M.dotfile_read(mid, path)
        if err:
            w.report(False, f"{path}: {err}")
            page.update()
            return
        field = ft.TextField(value=text, multiline=True, min_lines=12, max_lines=20,
                             text_size=11, dense=True, filled=True,
                             border_color=p["border"], focused_border_color=p["accent"],
                             color=p["text"])
        note = ft.Text("", size=11, color=p["text2"], selectable=True)

        async def save(ev):
            ok, msg = M.dotfile_save(mid, path, field.value or "")
            note.value = msg if ok else f"not saved - {msg}"
            note.color = p["text2"] if ok else p["pink"]
            page.update()

        dlg = ft.AlertDialog(
            modal=True, bgcolor=p["surface"], scrollable=True,
            title=ft.Text(f"{mid}:~/{path}", size=13, weight=ft.FontWeight.W_600,
                          color=p["text"]),
            content=ft.Column(spacing=8, tight=True, width=560, controls=[field, note]),
            actions=[ft.TextButton(content="Close",
                                   on_click=lambda ev: _dismiss(page, dlg)),
                     ft.FilledButton(content="Save", on_click=save)])
        _show(page, dlg)

    return ft.Row(spacing=6, controls=[
        ft.Icon(ft.Icons.DESCRIPTION_OUTLINED if exists else ft.Icons.REMOVE,
                size=13, color=p["accent"] if exists else p["text2"]),
        ft.Text(path, size=11, color=p["text"] if exists else p["text2"], expand=True,
                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        sub(when[:16] if exists else "not present", 10),
        ft.IconButton(ft.Icons.EDIT_OUTLINED, icon_size=14, icon_color=p["text2"],
                      tooltip="read and edit this file", on_click=open_it,
                      disabled=not exists),
    ])


# ------------------------------------------------------------------- network

def _iface_is_real(i):
    """An interface worth listing: it has an IPv4 that is not a loopback.

    Docker leaves a veth per container, each with only a link-local IPv6, and
    twenty of those bury the one address you actually wanted to read.
    """
    ip = str((i or {}).get("ip") or "")
    return "." in ip and not ip.startswith("127.")


def _network():
    """Per-machine interfaces, connection count and interface totals.

    This replaces the old "Things (soon)" tab. There is no IoT source on the
    server - no hub, no sensors, nothing in /api/config but machines - so
    there was nothing real to put behind that name. /api/dashboard does carry
    a real network widget per machine, which is what this shows instead.
    """
    p = PAL()
    dash = _dashboard()
    if dash is None:
        return ft.Column(spacing=10, controls=[_unreachable_card("the network")])

    cards = []
    for mid, m in (dash or {}).items():
        widgets = (m.get("widgets") or {}) if isinstance(m, dict) else {}
        net = widgets.get("network") or {}
        color = (m.get("color") if isinstance(m, dict) else None) or p["accent"]
        head = ft.Row([dot(color, dim=bool(net.get("error"))),
                       title(m.get("display", mid), 14),
                       ft.Container(expand=True),
                       sub(mid, 10)])

        if net.get("error"):
            cards.append(card(ft.Column(spacing=6, controls=[
                head, ft.Text(str(net["error"]), size=11, color=p["red"], selectable=True)])))
            continue

        ifaces = [i for i in (net.get("interfaces") or []) if isinstance(i, dict)]
        shown = [i for i in ifaces if _iface_is_real(i)]
        hidden = len(ifaces) - len(shown)

        body = [head, ft.Divider(height=1, color=p["border"])]
        if shown:
            for i in shown:
                up = str(i.get("state", "")).upper() == "UP"
                body.append(ft.Row(spacing=8, controls=[
                    dot(p["green"] if up else p["red"], dim=not up, size=7),
                    ft.Text(str(i.get("name", "?")), size=12, color=p["text"], width=110),
                    ft.Text(str(i.get("ip", "—")), size=12, color=p["text2"],
                            expand=True, selectable=True),
                    chip(str(i.get("state", "?")).lower(),
                         p["green"] if up else p["red"], small=True),
                ]))
        elif ifaces:
            body.append(sub(f"{len(ifaces)} interfaces, none with an IPv4 address", 11))
        else:
            # lil-faegola reports [] here while still reporting bandwidth: the
            # server's `ip addr` parse came back empty. Say that, don't hide it.
            body.append(sub("the server returned no interface list for this machine", 11))
        if hidden:
            body.append(sub(f"+{hidden} container / link-local interfaces hidden", 10))

        bw = net.get("bandwidth") if isinstance(net.get("bandwidth"), dict) else {}
        busiest = sorted(
            ((n, v) for n, v in bw.items()
             if n != "lo" and isinstance(v, dict)),
            key=lambda kv: (kv[1].get("rx") or 0) + (kv[1].get("tx") or 0), reverse=True)[:3]
        stats = [_stat("conns", net.get("connections", "—"))]
        for name, v in busiest:
            stats.append(_stat(f"{name} rx/tx",
                               f"{_bytes(v.get('rx'))} / {_bytes(v.get('tx'))}"))
        if len(stats) > 1 or net.get("connections") is not None:
            body.append(ft.Row(spacing=16, wrap=True, run_spacing=6, controls=stats))
        cards.append(card(ft.Column(spacing=6, controls=body)))

    if not cards:
        cards = [card(sub("The server listed no machines.", 11), pad=12)]
    # Said out loud, not only in the module docstring: the website has a
    # Things tab here and someone looking for it deserves to know where it
    # went rather than assuming the app dropped a feature.
    cards.append(card(ft.Column(spacing=4, controls=[
        title("Where Things went", 13),
        sub("The website keeps an IoT \"Things\" tab in this slot and it is "
            "empty there too - there is no hub, no sensors and nothing but "
            "machines in the server's config, so there is nothing to show. "
            "This tab shows the network widget the server does send instead. "
            "If an IoT source ever appears, Things comes back here.", 11)]),
        pad=12))
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=cards)


# ------------------------------------------------------------------ services

def _units_for(dash, mid):
    """(process -> {ports, pids, labels}, error) for one machine.

    The server's probe lists *listening ports*, so several rows share one
    process; they are folded together to make a list you can read.
    """
    widgets = ((dash.get(mid) or {}).get("widgets") or {})
    svc = widgets.get("services") or {}
    if svc.get("error"):
        return None, str(svc["error"])
    rows = svc.get("services")
    if not isinstance(rows, list):
        return None, "the server sent no service list for this machine"
    out = {}
    unnamed = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        proc = str(r.get("process") or "").strip()
        # "LISTEN" is the port scraper failing to find a process name, not a
        # service. Listing it as one would be an invented row.
        if not proc or proc == "LISTEN":
            unnamed += 1
            continue
        entry = out.setdefault(proc, {"ports": [], "pids": set(), "labels": set()})
        port = str(r.get("port") or "").strip()
        if port and port != "*" and port not in entry["ports"]:
            entry["ports"].append(port)
        if r.get("pid"):
            entry["pids"].add(str(r["pid"]))
        if r.get("name"):
            entry["labels"].add(str(r["name"]))
    if not out and unnamed:
        # void answers with rows whose process column is "LISTEN": something
        # is listening, the server's port parser just could not name it.
        # "nothing listening" would be the wrong thing to say.
        return out, (f"{unnamed} ports are listening but the server could not "
                     f"name the processes holding them")
    return out, None


def _services(page):
    p = PAL()
    w = Writer(page, _rebuild(page))
    probes = M._get("/api/system/services", timeout=15)
    dash = _dashboard()
    cards = []

    # --- LAN reachability -------------------------------------------------
    if probes is None:
        reach = _unreachable_card("service reachability")
    elif not isinstance(probes, dict) or not probes:
        reach = _error_card("The server returned no reachability results",
                            "GET /api/system/services answered with nothing usable.")
    else:
        rows = []
        for name, up in probes.items():
            up = bool(up)
            rows.append(ft.Row([
                dot(p["green"] if up else p["red"], dim=not up),
                ft.Text(str(name), size=13, color=p["text"]),
                ft.Container(expand=True),
                chip("up" if up else "down", p["green"] if up else p["red"], small=True),
            ]))
        reach = card(ft.Column(spacing=12, controls=[
            ft.Row([title("LAN reachability"), ft.Container(expand=True),
                    ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                                  tooltip="re-read from the server",
                                  on_click=lambda e: w.rebuild())]),
            *rows,
            # No start/stop here on purpose: these are TCP port probes against
            # fixed host:port pairs, not units on a known machine, so there is
            # nothing /api/service/control could be told to act on.
            sub("TCP port probes run by the server. Reachability only — these "
                "are not units, so they cannot be started or stopped here.", 10),
        ]))

    # --- per-machine control ----------------------------------------------
    if dash is None:
        cards.append(_unreachable_card("per-machine services"))
        return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO,
                         controls=[reach] + cards)

    ids = [k for k in dash if isinstance(dash.get(k), dict)]
    if not ids:
        cards.append(card(sub("The server listed no machines.", 11), pad=12))
        return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO,
                         controls=[reach] + cards)

    host_dd = ft.Dropdown(
        value=ids[0], dense=True, filled=True, text_size=12, width=170,
        border_color=p["border"], color=p["text"],
        options=[ft.dropdown.Option(key=k, text=(dash[k].get("display") or k))
                 for k in ids])
    unit = ft.TextField(hint_text="unit or container, e.g. caddy", dense=True,
                        filled=True, text_size=12, expand=True,
                        border_color=p["border"], focused_border_color=p["accent"],
                        color=p["text"])
    listing = ft.Column(spacing=4)
    output = ft.Text("", size=11, color=p["text2"], selectable=True)

    def fill(e=None):
        units, err = _units_for(dash, host_dd.value)
        listing.controls.clear()
        if err:
            listing.controls.append(ft.Text(err, size=11, color=p["red"], selectable=True))
        elif not units:
            listing.controls.append(sub("nothing listening on this machine", 11))
        else:
            for name in sorted(units):
                u = units[name]
                ports = ", ".join(u["ports"][:6]) + ("…" if len(u["ports"]) > 6 else "")
                label = " · ".join(sorted(u["labels"])[:3])

                def use(ev, name=name):
                    unit.value = name
                    page.update()

                listing.controls.append(ft.Row(spacing=6, controls=[
                    ft.Text(name, size=12, color=p["text"], width=130,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(ports or "—", size=11, color=p["text2"], expand=True),
                    chip(label, p["cyan"], small=True) if label else ft.Container(),
                    ft.IconButton(ft.Icons.ARROW_UPWARD, icon_size=14,
                                  icon_color=p["text2"],
                                  tooltip="put this name in the control box",
                                  on_click=use),
                ]))
        page.update()

    # on_select, not on_change: Flet 0.86's Dropdown has no on_change, and
    # assigning one is silently accepted and never fires - so picking a
    # different machine here left the listing showing the first one's ports.
    host_dd.on_select = fill
    fill()

    def send(action, name):
        async def handler(e):
            w.busy()
            output.value = ""
            ok, said = M.service_control(host_dd.value, name, action)
            if not ok:
                w.report(False, said)
            else:
                # See the module docstring: this endpoint's ok is always true,
                # so the output is the only honest signal about what happened.
                w.report(True, f"{action} {name} on {host_dd.value} — server said:")
                output.value = said
            page.update()
        return handler

    def control(action):
        """Status goes straight through; anything that changes state asks."""
        async def handler(e):
            name = (unit.value or "").strip()
            if not name:
                w.report(False, "type a unit or container name first")
                page.update()
                return
            if action == "status":
                await send(action, name)(e)
                return
            _confirm(page, f"{action.title()} {name}?",
                     f"On {dash[host_dd.value].get('display') or host_dd.value}, "
                     f"the server will try `docker {action} {name}` and then "
                     f"`systemctl {action} {name}`. Whatever that does happens "
                     f"immediately and cannot be undone from here.",
                     action.title(), send(action, name),
                     danger=action in ("stop", "restart"))
        return handler

    def control_card():
        return card(ft.Column(spacing=8, controls=[
            title("Control a service", 14),
            ft.Row(spacing=6, controls=[host_dd, unit]),
            ft.Row(spacing=6, controls=[
                ft.FilledButton("Start", height=32, on_click=control("start")),
                ft.OutlinedButton("Stop", height=32, on_click=control("stop")),
                ft.OutlinedButton("Restart", height=32, on_click=control("restart")),
                ft.TextButton("Status", height=32, on_click=control("status")),
            ]),
            sub("The server tries docker first, then systemctl. Its reply is shown "
                "as-is: it reports success for reaching the machine, not for the "
                "service actually changing state.", 10),
            w.control,
            output,
        ]))

    def listening_card():
        return card(ft.Column(spacing=6, controls=[
            title("Listening on this machine", 13),
            sub("From the server's port probe. The process name is often not the "
                "unit name — tap ↑ to copy one into the box above.", 10),
            listing,
        ]))

    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("lab.services", {
            "reachability": lambda: reach,
            "control": control_card, "listening": listening_card}))])


# -------------------------------------------------------------------- docker

# docker's own state words, in the order a human wants to read them.
_CT_ORDER = {"running": 0, "restarting": 1, "paused": 2, "created": 3, "exited": 4}


def _ct_colour(state):
    p = PAL()
    return {"running": p["green"], "paused": p["yellow"], "restarting": p["yellow"],
            "exited": p["text2"]}.get(str(state or ""), p["text2"])


def _ct_sort(c):
    state = str((c or {}).get("state") or "")
    return (_CT_ORDER.get(state, 9), str((c or {}).get("name") or "").lower())


def _docker(page):
    """Every container on every machine, from /api/docker_list.

    The app had none of this: the website's busiest Lab view, 38 containers
    across five hosts, had no counterpart here at all. Start / stop / restart
    go through the same /api/service/control the Services tab uses, and the
    same caveat applies - its ``ok`` means the SSH call completed, so the
    output is shown rather than a tick.
    """
    p = PAL()
    w = Writer(page, _rebuild(page))
    hosts, err = M.docker_list()
    if hosts is None:
        return ft.Column(spacing=10, controls=[
            _error_card("Cannot list containers", err or "no detail",
                        ft.Icons.CLOUD_OFF)])

    ids = [k for k in hosts if isinstance(hosts.get(k), dict)]
    rows_by_host = {k: sorted(
        (c for c in (hosts[k].get("containers") or []) if isinstance(c, dict)),
        key=_ct_sort) for k in ids}
    total = sum(len(v) for v in rows_by_host.values())
    running = sum(1 for v in rows_by_host.values() for c in v if c.get("state") == "running")

    filt = {"text": ""}
    body = ft.Column(spacing=10)

    def act(mid, name, action):
        async def send(e):
            w.busy()
            ok, said = M.service_control(mid, name, action)
            w.report(ok, f"{action} {name} — server said: {said}" if ok else said)
            page.update()
        return send

    def guarded(mid, name, action, display):
        async def handler(e):
            _confirm(page, f"{action.title()} {name}?",
                     f"On {display}. The server runs `docker {action} {name}` "
                     f"over SSH straight away.",
                     action.title(), act(mid, name, action),
                     danger=action in ("stop", "restart"))
        return handler

    def ct_row(mid, display, c):
        name = str(c.get("name") or "?")
        state = str(c.get("state") or "?")
        ports = M.docker_ports(c.get("ports"))
        line2 = " · ".join(x for x in (str(c.get("image") or ""),
                                       str(c.get("status") or ""), ports) if x)

        async def logs(e):
            text, lerr = M.docker_logs(mid, name)
            dlg = ft.AlertDialog(
                modal=True, bgcolor=p["surface"], scrollable=True,
                title=ft.Text(f"Logs: {name}", size=13, weight=ft.FontWeight.W_600,
                              color=p["text"]),
                content=ft.Column(spacing=6, tight=True, width=560, controls=[
                    ft.Text(lerr or (text or "(no output)"), size=11,
                            color=p["red"] if lerr else p["text2"],
                            selectable=True, font_family="monospace"),
                    sub("docker logs --tail 80, run on the machine over SSH.", 10),
                ]),
                actions=[ft.TextButton(content="Close",
                                       on_click=lambda ev: _dismiss(page, dlg))])
            _show(page, dlg)

        buttons = []
        if state != "running":
            buttons.append(ft.IconButton(
                ft.Icons.PLAY_ARROW, icon_size=16, icon_color=p["green"],
                tooltip="start", on_click=guarded(mid, name, "start", display)))
        else:
            buttons.append(ft.IconButton(
                ft.Icons.STOP, icon_size=16, icon_color=p["red"],
                tooltip="stop", on_click=guarded(mid, name, "stop", display)))
        buttons.append(ft.IconButton(
            ft.Icons.RESTART_ALT, icon_size=16, icon_color=p["text2"],
            tooltip="restart", on_click=guarded(mid, name, "restart", display)))
        buttons.append(ft.IconButton(
            ft.Icons.ARTICLE_OUTLINED, icon_size=16, icon_color=p["text2"],
            tooltip="last 80 log lines", on_click=logs))

        return ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            dot(_ct_colour(state), dim=state != "running", size=7),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(name, size=12, weight=ft.FontWeight.W_500, color=p["text"],
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(line2 or "—", size=10, color=p["text2"],
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]),
            chip(state, _ct_colour(state), small=True),
            ft.Row(spacing=0, tight=True, controls=buttons),
        ])

    def host_card(mid):
        rows = rows_by_host[mid]
        q = filt["text"].lower()
        shown = [c for c in rows if not q or q in str(c.get("name") or "").lower()
                 or q in str(c.get("image") or "").lower()]
        up = sum(1 for c in rows if c.get("state") == "running")
        head = ft.Row([
            dot(hosts[mid].get("color") or p["accent"], dim=not up, size=8),
            title(hosts[mid].get("display") or mid, 13),
            ft.Container(expand=True),
            chip(f"{up}/{len(rows)} running",
                 p["green"] if up else p["text2"], small=True),
        ])
        if not rows:
            # sp3 answers with an empty list while it is unreachable. "No
            # containers" and "we could not ask" are different facts.
            inner = [sub("the server listed no containers for this machine", 11)]
        elif not shown:
            inner = [sub(f"none of its {len(rows)} containers match the filter", 11)]
        else:
            inner = [ct_row(mid, hosts[mid].get("display") or mid, c) for c in shown]
        return card(ft.Column(spacing=8, controls=[
            head, ft.Divider(height=1, color=p["border"]), *inner]))

    def paint():
        body.controls = [host_card(mid) for mid in ids] or [
            card(sub("The server listed no machines.", 11), pad=12)]

    search = ft.TextField(hint_text="filter by name or image", dense=True, filled=True,
                          text_size=12, expand=True, border_color=p["border"],
                          focused_border_color=p["accent"], color=p["text"])

    def do_filter(e=None):
        filt["text"] = (search.value or "").strip()
        paint()
        page.update()

    search.on_submit = do_filter
    search.on_change = do_filter
    paint()

    def summary_card():
        return card(ft.Column(spacing=8, controls=[
            ft.Row([title("Containers", 14), ft.Container(expand=True),
                    chip(f"{running}/{total} running",
                         p["green"] if running else p["text2"], small=True),
                    ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                                  tooltip="re-read from the server",
                                  on_click=lambda e: w.rebuild())]),
            ft.Row(spacing=6, controls=[search]),
            sub("docker ps on every machine, over the server's SSH. Start, stop "
                "and restart ask first and then report the server's own words.", 10),
            w.control,
        ]))

    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("lab.docker", {
            "summary": summary_card, "containers": lambda: body}))])


# ---------------------------------------------------------------------- qbit

# qBittorrent state strings, grouped only as far as it changes what is drawn.
_QB_STOPPED = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}
_QB_BAD = {"error", "missingFiles", "unknown"}
_QB_SEEDING = {"uploading", "forcedUP", "stalledUP", "queuedUP", "checkingUP"}

# The name filter, at module level so a repaint after an action does not
# quietly widen the list back out to everything.
_QB_FILTER = {"text": ""}


def _qb_color(state):
    p = PAL()
    if state in _QB_BAD:
        return p["red"]
    if state in _QB_STOPPED:
        return p["yellow"]
    if state in _QB_SEEDING:
        return p["cyan"]
    return p["accent"]


def _eta(secs):
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return ""
    # qBittorrent uses 8640000 (100 days) as its "unknown / infinite" marker.
    if secs <= 0 or secs >= 8640000:
        return ""
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d"


def _qbit(page):
    """Live torrents from /api/qbit, with pause / resume / delete.

    /api/qbit is a passthrough of qBittorrent's own torrents/info and
    transfer/info, so every field is read defensively - the app never decides
    a torrent exists, it only draws the ones the server sent.
    """
    p = PAL()
    w = Writer(page, _rebuild(page))
    d = M._get("/api/qbit", timeout=12)
    if d is None:
        return ft.Column(spacing=10, controls=[_unreachable_card("qBittorrent")])

    err = d.get("error") if isinstance(d, dict) else "the server sent a malformed reply"
    raw = d.get("torrents") if isinstance(d, dict) else None
    torrents = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
    stats = d.get("stats") if isinstance(d, dict) and isinstance(d.get("stats"), dict) else {}

    def act(action, thash, name, **extra):
        async def handler(e):
            w.busy()
            payload = {"action": action, "hashes": thash}
            payload.update(extra)
            _data, perr = _post_json("/api/qbit/action", payload, timeout=25)
            if perr:
                w.report(False, f"{action} “{name}”: {perr}")
                page.update()
                return
            w.report(True, f"{action} sent for “{name}”")
            w.rebuild()
        return handler

    def delete_button(thash, name):
        """Two taps to delete. The first arms it, the second sends it.

        A single tap next to a progress bar on a phone is far too easy to hit
        by accident, and the server deletes without asking.
        """
        armed = {"on": False}
        btn = ft.TextButton("Delete", height=30,
                            style=ft.ButtonStyle(color=p["red"]))
        send = act("delete", thash, name, deleteFiles=False)

        async def click(e):
            if not armed["on"]:
                armed["on"] = True
                btn.content = "Really delete?"
                w.report(True, "tap again to delete — files on disk are kept")
                page.update()
                return
            armed["on"] = False
            btn.content = "Delete"
            await send(e)

        btn.on_click = click
        return btn

    def row(t):
        thash = str(t.get("hash") or "")
        name = str(t.get("name") or thash or "unnamed")
        state = str(t.get("state") or "unknown")
        color = _qb_color(state)
        try:
            prog = max(0.0, min(1.0, float(t.get("progress") or 0)))
        except (TypeError, ValueError):
            prog = 0.0
        dl, up = _speed(t.get("dlspeed")), _speed(t.get("upspeed"))
        eta = _eta(t.get("eta"))
        bits = [b for b in (f"{prog * 100:.0f}%",
                            _bytes(t.get("size")) if t.get("size") else "",
                            f"↓{dl}" if dl else "", f"↑{up}" if up else "",
                            f"eta {eta}" if eta else "") if b]

        controls = [
            ft.Row([ft.Text(name, size=12, weight=ft.FontWeight.W_500, color=p["text"],
                            expand=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    chip(state, color, small=True)]),
            ft.ProgressBar(value=prog, color=color, bgcolor=p["bg2"], bar_height=4,
                           border_radius=RAD(2)),
            ft.Row([sub(" · ".join(bits), 11)]),
        ]
        if thash:
            stopped = state in _QB_STOPPED
            controls.append(ft.Row(spacing=6, controls=[
                ft.OutlinedButton("Resume" if stopped else "Pause", height=30,
                                  on_click=act("resume" if stopped else "pause",
                                               thash, name)),
                ft.IconButton(ft.Icons.FAST_FORWARD, icon_size=16,
                              icon_color=p["text2"], tooltip="force start",
                              on_click=act("setForceStart", thash, name, value=True)),
                ft.IconButton(ft.Icons.FIND_REPLACE, icon_size=16,
                              icon_color=p["text2"],
                              tooltip="recheck the files on disk",
                              on_click=act("recheck", thash, name)),
                ft.Container(expand=True),
                delete_button(thash, name),
            ]))
        else:
            # No hash means nothing can be acted on; say why rather than show
            # buttons that would fail with "hashes required".
            controls.append(sub("no hash in the server's reply — cannot act on this one", 10))
        return card(ft.Column(spacing=6, controls=controls), pad=10)

    # qBittorrent's own transfer/info keys. The website reads dl_rate,
    # ul_rate and free_space, none of which qBittorrent sends, which is why
    # its Download / Upload / Free Space tiles have always read 0 B/s.
    dl_total, up_total = _speed(stats.get("dl_info_speed")), _speed(stats.get("up_info_speed"))
    free = stats.get("free_space_on_disk")
    active = sum(1 for t in torrents if str(t.get("state")) == "downloading")
    seeding = sum(1 for t in torrents if str(t.get("state")) in _QB_SEEDING)
    summary = [b for b in (f"{len(torrents)} torrents" if torrents else "",
                           f"↓{dl_total}" if dl_total else "",
                           f"↑{up_total}" if up_total else "",
                           f"{active} active / {seeding} seeding" if torrents else "",
                           f"{_bytes(free)} free" if free else "",
                           str(stats.get("connection_status") or "")) if b]

    def bulk(action, verb, **extra):
        """One instruction for every torrent at once. Always asks first."""
        send = act(action, "all", "all torrents", **extra)

        async def handler(e):
            if not torrents:
                w.report(False, "there are no torrents to act on")
                page.update()
                return
            _confirm(page, f"{verb} all {len(torrents)} torrents?",
                     "This is sent to qBittorrent as one instruction for every "
                     "torrent it holds"
                     + (", and cannot be undone. Files on disk are kept."
                        if action == "delete" else "."),
                     verb, send, danger=action == "delete")
        return handler

    head = card(ft.Column(spacing=8, controls=[
        ft.Row(spacing=8, controls=[
            title("Torrents", 14),
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                          tooltip="re-read from the server",
                          on_click=lambda e: w.rebuild()),
        ]),
        sub(" · ".join(summary) or "no transfer stats", 11),
        ft.Row(spacing=6, wrap=True, run_spacing=6, controls=[
            ft.OutlinedButton("Resume all", height=30,
                              on_click=bulk("resume", "Resume")),
            ft.OutlinedButton("Pause all", height=30,
                              on_click=bulk("pause", "Pause")),
            ft.TextButton("Delete all", height=30,
                          style=ft.ButtonStyle(color=p["red"]),
                          on_click=bulk("delete", "Delete", deleteFiles=False)),
        ]),
    ]), pad=10)

    search = ft.TextField(hint_text="filter by name", dense=True, filled=True,
                          text_size=12, expand=True, border_color=p["border"],
                          focused_border_color=p["accent"], color=p["text"],
                          value=_QB_FILTER["text"])
    listing = ft.Column(spacing=10)

    def paint():
        q = _QB_FILTER["text"].lower()
        shown = [t for t in torrents if not q or q in str(t.get("name") or "").lower()]
        if torrents and not shown:
            listing.controls = [card(
                sub(f"None of the {len(torrents)} torrents match “"
                    f"{_QB_FILTER['text']}”.", 11), pad=12)]
        else:
            listing.controls = [row(t) for t in shown]

    def do_filter(e=None):
        _QB_FILTER["text"] = (search.value or "").strip()
        paint()
        page.update()

    search.on_change = do_filter
    search.on_submit = do_filter
    paint()

    body = [head]
    if err:
        body.append(_error_card("qBittorrent did not answer", err))
    if torrents:
        body.append(ft.Row(spacing=6, controls=[search]))
        body.append(listing)
    elif not err:
        body.append(card(sub("qBittorrent is up and has no torrents.", 11), pad=12))
    body.append(w.control)
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=body)
