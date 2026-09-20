"""Lab: the machines, their network, their services, and the torrent box.

Every tab here reads the live dashbored server. Where a source is down, the
error it actually gave is printed verbatim instead of being replaced by a
plausible-looking row: a dashboard that invents a torrent, or shows a green
light for a service it never reached, is worse than one that says nothing.

Two server quirks shape the code below and are worth knowing before editing:

* ``/api/qbit`` and ``/api/qbit/action`` answer HTTP 500 with the real cause
  in ``{"error": ...}``. ``api._post`` returns None on any HTTPError, which
  throws that message away, so writes here go through ``_post_json``.
* ``/api/service/control`` answers ``{"ok": true, ...}`` whenever the SSH
  call returned *anything*, including "Unit caddy.service could not be
  found". Its ``ok`` therefore means "the request completed", not "the
  service started", so the output is shown verbatim and never summarised
  into a success.
"""
import json
import urllib.error
import urllib.request

import flet as ft

from ..ui import PAL, PAD, BRD, S, card, chip, title, sub, dot
from .. import api as M
from .. import config
from ._forms import Writer, offline_banner, rebuilder

# Which tab each page was last looking at, so a write that repaints the page
# does not silently throw you back to the first tab.
_TAB_INDEX = {}


def _tabs(page, tabs, key=None):
    p = PAL()
    selected = max(0, min(_TAB_INDEX.get(key, 0), len(tabs) - 1)) if key else 0

    def remember(e):
        _TAB_INDEX[key] = e.control.selected_index

    return ft.Tabs(
        length=len(tabs), selected_index=selected, animation_duration=150, expand=True,
        on_change=remember if key else None,
        content=ft.Column(spacing=0, expand=True, controls=[
            ft.TabBar(
                tabs=[ft.Tab(label=t) for t, _ in tabs],
                indicator_color=p["accent"], divider_color=p["border"],
                label_color=p["accent"], unselected_label_color=p["text2"],
                overlay_color=ft.Colors.with_opacity(0.08, p["accent"]),
            ),
            ft.TabBarView(expand=True, controls=[
                ft.Container(padding=PAD(10, 0), content=c) for _, c in tabs]),
        ]),
    )


def build(page):
    return _tabs(page, [("Machines", _machines(page)), ("Network", _network()),
                        ("Services", _services(page)), ("Qbit", _qbit(page))],
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


# ------------------------------------------------------------------ machines

def _machines(page):
    p = PAL()
    def mc(m):
        rows = [
            ("OS", m["os"]), ("Host", m["host"]),
        ] + ([("Uptime", m["uptime"]), ("CPU", f"{m['cpu']}%"),
              ("RAM", str(m["ram"]) + ("%" if isinstance(m["ram"], int) else "")),
              ("Temp", f"{m['temp']}°C"),
              ("Docker", str(m["docker"]))] if m["online"] else [])
        return card(ft.Column(spacing=6, controls=[
            ft.Row([
                dot(m["color"], dim=not m["online"]),
                title(m["display"]),
                ft.Container(expand=True),
                chip("online" if m["online"] else "offline",
                     p["green"] if m["online"] else p["red"], small=True),
            ]),
            ft.Divider(height=1, color=p["border"]),
            ft.Row(spacing=14, wrap=True, controls=[
                ft.Column(spacing=2, controls=[sub(k, 10), ft.Text(v, size=12, color=p["text"])])
                for k, v in rows
            ]),
        ]))
    # M.MACHINES falls back to mock when the server is unreachable, so say so
    # rather than let sample hostnames read as the real fleet. Read it before
    # asking STATUS_LIVE: the lookup is what performs the request that sets it.
    machines = M.MACHINES
    banner = [] if M.STATUS_LIVE() else [offline_banner(p, "these machines")]
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO,
                     controls=banner + [mc(m) for m in machines])


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
        cards.append(_unreachable_card("service reachability"))
    elif not isinstance(probes, dict) or not probes:
        cards.append(_error_card("The server returned no reachability results",
                                 "GET /api/system/services answered with nothing usable."))
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
        cards.append(card(ft.Column(spacing=12, controls=[
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
        ])))

    # --- per-machine control ----------------------------------------------
    if dash is None:
        cards.append(_unreachable_card("per-machine services"))
        return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=cards)

    ids = [k for k in dash if isinstance(dash.get(k), dict)]
    if not ids:
        cards.append(card(sub("The server listed no machines.", 11), pad=12))
        return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=cards)

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

    host_dd.on_change = fill
    fill()

    def control(action):
        async def handler(e):
            name = (unit.value or "").strip()
            if not name:
                w.report(False, "type a unit or container name first")
                page.update()
                return
            w.busy()
            output.value = ""
            data, err = _post_json(
                "/api/service/control",
                {"host": host_dd.value, "service": name, "action": action}, timeout=45)
            if err:
                w.report(False, err)
            else:
                # See the module docstring: this endpoint's ok is always true,
                # so the output is the only honest signal about what happened.
                w.report(True, f"{action} {name} on {host_dd.value} — server said:")
                output.value = (data or {}).get("output") or "(no output)"
            page.update()
        return handler

    cards.append(card(ft.Column(spacing=8, controls=[
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
    ])))

    cards.append(card(ft.Column(spacing=6, controls=[
        title("Listening on this machine", 13),
        sub("From the server's port probe. The process name is often not the "
            "unit name — tap ↑ to copy one into the box above.", 10),
        listing,
    ])))
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=cards)


# ---------------------------------------------------------------------- qbit

# qBittorrent state strings, grouped only as far as it changes what is drawn.
_QB_STOPPED = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}
_QB_BAD = {"error", "missingFiles", "unknown"}
_QB_SEEDING = {"uploading", "forcedUP", "stalledUP", "queuedUP", "checkingUP"}


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
                           border_radius=2),
            ft.Row([sub(" · ".join(bits), 11)]),
        ]
        if thash:
            stopped = state in _QB_STOPPED
            controls.append(ft.Row(spacing=6, controls=[
                ft.OutlinedButton("Resume" if stopped else "Pause", height=30,
                                  on_click=act("resume" if stopped else "pause",
                                               thash, name)),
                ft.Container(expand=True),
                delete_button(thash, name),
            ]))
        else:
            # No hash means nothing can be acted on; say why rather than show
            # buttons that would fail with "hashes required".
            controls.append(sub("no hash in the server's reply — cannot act on this one", 10))
        return card(ft.Column(spacing=6, controls=controls), pad=10)

    dl_total, up_total = _speed(stats.get("dl_info_speed")), _speed(stats.get("up_info_speed"))
    summary = [b for b in (f"{len(torrents)} torrents" if torrents else "",
                           f"↓{dl_total}" if dl_total else "",
                           f"↑{up_total}" if up_total else "",
                           str(stats.get("connection_status") or "")) if b]
    head = card(ft.Row(spacing=8, controls=[
        title("Torrents", 14),
        ft.Container(expand=True),
        sub(" · ".join(summary) or "no transfer stats", 11),
        ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                      tooltip="re-read from the server",
                      on_click=lambda e: w.rebuild()),
    ]), pad=10)

    body = [head]
    if err:
        body.append(_error_card("qBittorrent did not answer", err))
    if torrents:
        body.extend(row(t) for t in torrents)
    elif not err:
        body.append(card(sub("qBittorrent is up and has no torrents.", 11), pad=12))
    body.append(w.control)
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=body)
