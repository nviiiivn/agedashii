"""Future Finds - the website's Wat2Do "Future" sub-tab, on the phone.

The website (dashbored-web/app.js, the ``fut*`` functions) scans a local
table of 31k California gems around a point, keeps a someday list of the
ones worth a ride, and can show each one's free Wikimedia photos and the
street view frame the server cached for it. The app had none of it.

Four endpoints, all verified against the live server before a line of this
was written (2026-09-20):

  ``GET  /api/future/scan?lat&lon&r&cats``  -> ``{"elements": [...], "count": n}``
        elements are ``{"type":"g","id":<int>,"lat","lon","tags":{...},"dist":m}``.
        Server-side, not Overpass: no network beyond our own server, and it
        answers in milliseconds, which is what makes it worth caching.
  ``GET  /api/future/gems``                 -> ``{"gems": [...]}``
  ``POST /api/future/gems``                 -> ``{"ok": true, "gem": {...}}``
        or ``{"ok": true, "dup": true}`` when the ``oid`` is already saved.
  ``POST /api/future/gems/delete``          -> ``{"ok": true, "count": n}``
  ``GET  /api/future/photos?lat&lon&name``  -> ``{"photos": [{thumb, page,
        artist, license, title}]}`` - Wikimedia Commons, credited.
  ``GET  /api/future/sv?gid=<id>``          -> ``{"ok":1,"img":"...","date"}``
        or ``{"ok": 0}``. ``img`` is a path on our own server, not a URL.

Three rules this page keeps, all of them the app's rules rather than the
website's:

**Writes go through the outbox.** Saving a gem with no signal queues it and
shows it in the someday list straight away, marked as not yet sent. Both
endpoints are safe to retry - the server dedupes a save by ``oid`` and a
delete of an unknown id is a no-op - which is what makes queueing them
honest rather than merely optimistic.

**A queued gem cannot be deleted.** The server assigns the id, so a gem that
has not been sent yet has no id to delete by; offering the button would send
a delete for an id that never existed and leave the gem saved. It says
"sending" instead.

**Nothing here is ever invented.** There is no mock Future data anywhere in
the app, so with no server and no cached scan the page says so and shows an
empty list, rather than a plausible one.
"""
import asyncio
import time
import urllib.parse

import flet as ft

from .. import api as M
from .. import config
from ..ui import PAL, PAD, BRD, card, chip, title, sub
from ._forms import offline_banner
from ._wat2do_parts import HOME, haversine_m, miles

GEMS_PATH = "/api/future/gems"

# app.js FUT_DISTS, to the metre. The server clamps r at 200 km.
DISTANCES = ((25, 40234), (50, 80467), (100, 160934))

# app.js W2D_GEM_CATS: id, label, colour. The Overpass fragments that live
# alongside them there are not needed here - the scan is served from the
# server's own gems-ca.json table, which is already categorised by these ids.
GEM_CATS = (
    ("pretty", "Viewpoints", "#f2c94c", ft.Icons.WB_SUNNY),
    ("falls", "Waterfalls", "#4fc3f7", ft.Icons.WATER_DROP),
    ("caves", "Caves", "#8d6e63", ft.Icons.TERRAIN),
    ("ruins", "Ruins", "#9e9e9e", ft.Icons.HEART_BROKEN),
    ("ancient", "Ancient", "#ffb300", ft.Icons.CASTLE),
    ("dams", "Dams & Weirs", "#90a4ae", ft.Icons.LAYERS),
    ("tunnels", "Tunnels", "#ce93d8", ft.Icons.CIRCLE),
    ("bridges", "Old Bridges", "#a1887f", ft.Icons.LINEAR_SCALE),
    ("lakes", "Lakes", "#4dd0e1", ft.Icons.WAVES),
    ("wtemples", "Water Temples", "#64b5f6", ft.Icons.WATER),
    ("forests", "Redwoods/Woods", "#66bb6a", ft.Icons.FOREST),
    ("beaches", "Beaches", "#ffe082", ft.Icons.BEACH_ACCESS),
    ("gardens", "Gardens", "#f48fb1", ft.Icons.LOCAL_FLORIST),
    ("temples", "Temples", "#b39ddb", ft.Icons.CHURCH),
    ("thrift", "Thrift", "#ef5350", ft.Icons.CHECKROOM),
    ("flea", "Flea Markets", "#26a69a", ft.Icons.STOREFRONT),
    ("weird", "Weird", "#ff8a65", ft.Icons.REMOVE_RED_EYE),
)
CAT_BY_ID = {c[0]: {"id": c[0], "label": c[1], "color": c[2], "icon": c[3]}
             for c in GEM_CATS}

# The website's opening four. Scanning all seventeen at 100 miles returns
# thousands of road bridges and buries everything worth looking at.
DEFAULT_CATS = ("pretty", "falls", "ruins", "weird")

# app.js caps the rendered list at 60 after sorting by distance from home.
MAX_SPOTS = 60


# --------------------------------------------------------------- pure logic
def gem_classify(tags, name=""):
    """OSM tags to one of our gem ids. Port of app.js ``futClassify``.

    The scan endpoint does not say which category it matched a row on - it
    only filters by it - so the row has to be re-classified from its tags to
    colour it. Order matters and is the website's, verbatim: a tunnel that is
    also a bridge is a tunnel, because that is what the browser would say.
    """
    t = tags or {}
    n = name or t.get("name") or ""
    if t.get("tourism") == "viewpoint" or t.get("natural") == "cape":
        return "pretty"
    if t.get("waterway") == "waterfall":
        return "falls"
    if t.get("natural") == "cave_entrance":
        return "caves"
    if (t.get("abandoned") == "yes" or t.get("ruins") == "yes"
            or t.get("historic") == "ruins"):
        return "ruins"
    if t.get("historic") in ("archaeological_site", "fort", "castle",
                             "city_gate") or t.get("man_made") == "aqueduct" \
            or t.get("historic") == "citywalls":
        return "ancient"
    if (t.get("waterway") == "dam" or t.get("barrier") == "dam"
            or t.get("man_made") == "weir"):
        return "dams"
    if t.get("tunnel") == "yes" and t.get("highway"):
        return "tunnels"
    if (t.get("bridge") == "yes" and t.get("highway")) or t.get("man_made") == "bridge":
        return "bridges"
    if t.get("natural") == "water" and t.get("water") == "lake":
        return "lakes"
    if "water temple" in n.lower():
        return "wtemples"
    if t.get("natural") == "wood" or t.get("landuse") == "forest" \
            or t.get("leisure") == "nature_reserve":
        return "forests"
    if t.get("natural") == "beach":
        return "beaches"
    if t.get("leisure") == "garden" or t.get("tourism") == "garden":
        return "gardens"
    if t.get("building") == "temple" or t.get("historic") == "wayside_shrine":
        return "temples"
    if t.get("shop") in ("charity", "second_hand"):
        return "thrift"
    if t.get("shop") == "flea_market":
        return "flea"
    if t.get("amenity") == "marketplace" and ("flea" in n.lower() or "swap" in n.lower()):
        return "flea"
    return "pretty"


def tag_images(tags):
    """Commons file titles named by the row's own tags. app.js ``futTagImgs``.

    A spot that names its own photo gets that photo, rather than whatever the
    Commons search decides is nearby and similarly spelled.
    """
    out = []
    for key in ("image", "wikimedia_commons", "image:0", "image:1", "image:2"):
        value = (tags or {}).get(key)
        if not value or str(value).startswith("http"):
            continue
        value = str(value)
        title = value if value.startswith("File:") else "File:" + value
        if title not in out:
            out.append(title)
    return out


def gid(oid):
    """The street-view index key for a spot. Port of app.js ``futGid``.

    The index is keyed by the bare gem id; a scan row's oid is ``g/<id>``.
    """
    text = str(oid or "")
    if text.startswith("g/"):
        text = text[2:]
    keep = [c if (c.isalnum() or c in "_.-") else "_" for c in text]
    return "".join(keep)[:120]


def spot_from(element):
    """One scan element as a spot, or None when it is not placeable."""
    tags = (element or {}).get("tags") or {}
    name = tags.get("name")
    lat, lon = element.get("lat"), element.get("lon")
    if not name or lat is None or lon is None:
        return None
    return {"oid": f"{element.get('type', 'g')}/{element.get('id', '')}",
            "name": name, "cat": gem_classify(tags, name),
            "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "imgs": tag_images(tags),
            "dist_m": haversine_m(HOME, (float(lat), float(lon)))}


def spots_from(elements):
    """Scan elements to named, deduplicated, distance-sorted spots."""
    seen, out = set(), []
    for element in elements or []:
        spot = spot_from(element)
        if spot is None or spot["oid"] in seen:
            continue
        seen.add(spot["oid"])
        out.append(spot)
    out.sort(key=lambda s: s["dist_m"])
    return out[:MAX_SPOTS]


def gem_to_spot(gem):
    """A saved gem in the same shape a scanned spot has, so rows share code."""
    try:
        lat, lon = float(gem.get("lat")), float(gem.get("lon"))
    except (TypeError, ValueError):
        return None
    return {"id": gem.get("id") or "", "oid": gem.get("oid") or "",
            "name": gem.get("name") or "(unnamed)",
            "cat": gem.get("cat") or "pretty", "lat": lat, "lon": lon,
            "note": gem.get("note") or "", "imgs": [],
            "pending": bool(gem.get("pending")),
            "dist_m": haversine_m(HOME, (lat, lon))}


# -------------------------------------------------------------- server calls
def scan(cats, radius_m, centre=None):
    """Gems of `cats` within `radius_m` of `centre`, or None if we have none.

    Served from the store, so the last scan you ran is still there in a
    canyon. Each (cats, radius, centre) is its own cache entry, which is the
    point: they are different questions with different answers.
    """
    lat, lon = centre or HOME
    if not cats:
        return []
    joined = ",".join(sorted(cats))
    path = (f"/api/future/scan?lat={lat}&lon={lon}&r={int(radius_m)}"
            f"&cats={urllib.parse.quote(joined)}")
    d = M._get(path, timeout=30)
    return None if d is None else spots_from(d.get("elements"))


def saved():
    """The someday list, or None if this device has never seen it."""
    d = M._get(GEMS_PATH, timeout=10)
    if d is None:
        return None
    out = [gem_to_spot(g) for g in (d.get("gems") or [])]
    out = [g for g in out if g is not None]
    out.sort(key=lambda g: g["dist_m"])
    return out


def _gems_doc():
    doc = M._cached(GEMS_PATH)
    return dict(doc) if isinstance(doc, dict) else {}


def save_gem(spot):
    """Queue a gem onto the someday list. Returns (ok, message).

    Retry-safe: the server answers ``{"ok": true, "dup": true}`` for an oid
    it already holds, so a queued save that gets sent twice adds one gem.
    """
    oid = str(spot.get("oid") or "")
    doc = _gems_doc()
    gems = list(doc.get("gems") or [])
    if oid and any(g.get("oid") == oid for g in gems):
        return True, "already on the list"
    payload = {"oid": oid, "name": spot.get("name") or "",
               "lat": spot.get("lat"), "lon": spot.get("lon"),
               "cat": spot.get("cat") or "", "note": spot.get("note") or ""}
    if not payload["name"] or payload["lat"] is None or payload["lon"] is None:
        return False, "that spot has no name or no position"
    # `pending` is ours, not the server's: it is how the row says "this is on
    # the phone, not on the server yet" until a re-read replaces it with the
    # server's own record, id and all.
    local = dict(doc)
    local["gems"] = gems + [dict(payload, id="", ts=int(time.time()), pending=True)]
    return M._queue_write(GEMS_PATH, json_body=payload, timeout=15,
                          local={GEMS_PATH: local}, invalidate=(GEMS_PATH,)).pair


def delete_gem(gem_id):
    """Queue a removal from the someday list. Returns (ok, message).

    Retry-safe: deleting an id the server does not hold rewrites the list
    unchanged and answers ok.
    """
    gem_id = str(gem_id or "")
    if not gem_id:
        return False, "that one has not reached the server yet"
    doc = _gems_doc()
    local = dict(doc)
    local["gems"] = [g for g in (doc.get("gems") or []) if g.get("id") != gem_id]
    return M._queue_write("/api/future/gems/delete", json_body={"id": gem_id},
                          timeout=15, local={GEMS_PATH: local},
                          invalidate=(GEMS_PATH,)).pair


def photos(spot):
    """Free Wikimedia photos for a spot, or None. Credited, never re-hosted."""
    query = (f"/api/future/photos?lat={spot.get('lat')}&lon={spot.get('lon')}"
             f"&name={urllib.parse.quote(str(spot.get('name') or ''))}")
    imgs = spot.get("imgs") or []
    if imgs:
        query += "&titles=" + urllib.parse.quote("|".join(imgs[:10]))
    d = M._get(query, timeout=40)
    return None if d is None else (d.get("photos") or [])


def street_view(spot):
    """(image_url, caption) for a spot, or (None, why) when there is none."""
    key = gid(spot.get("oid"))
    if not key:
        return None, "no street view for this spot"
    d = M._get(f"/api/future/sv?gid={urllib.parse.quote(key)}", timeout=20)
    if d is None:
        return None, config.STATUS.get("detail") or "server unreachable"
    if not d.get("ok") or not d.get("img"):
        return None, "no street view for this spot"
    # The server serves the frame itself, so the URL has to carry the server
    # the rest of the app is pointed at - not a path the Image control would
    # resolve against nothing.
    return config.SERVER_URL.rstrip("/") + d["img"], (
        f"Imagery {d['date']}" if d.get("date") else "")


# ------------------------------------------------------------------- the page
# Survives a repaint so a scan is not thrown away every time the page
# rebuilds - the same reason wat2do.py keeps its `_S`.
_S = {"cats": list(DEFAULT_CATS), "dist": 50, "spots": None, "note": ""}


def _run(page, coro_fn, *args):
    """page.run_task where there is a loop, otherwise nothing. As wat2do."""
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return
    try:
        runner(coro_fn, *args)
    except Exception:          # noqa: BLE001 - no running loop (tests, import check)
        pass


def build(page):
    p = PAL()
    live = M.STATUS_LIVE()
    somedays = saved()

    status = ft.Text(_S["note"], size=11, color=p["text2"], selectable=True)
    results = ft.Column(spacing=2)
    saved_box = ft.Column(spacing=2)
    count = ft.Text("", size=11, color=p["text2"])

    def say(ok, msg):
        _S["note"] = msg if ok else f"not saved - {msg}"
        status.value = _S["note"]
        status.color = p["text2"] if ok else p["pink"]

    def repaint():
        try:
            page.update()
        except Exception:      # noqa: BLE001 - not mounted yet (first paint)
            pass

    def show(dlg):
        opener = getattr(page, "show_dialog", None) or getattr(page, "open", None)
        if callable(opener):
            opener(dlg)

    def dismiss(dlg):
        closer = getattr(page, "pop_dialog", None)
        if callable(closer):
            closer()
            return
        closer = getattr(page, "close", None)
        if callable(closer):
            closer(dlg)

    # ------------------------------------------------------------- controls
    def cat_chip(meta):
        on = meta["id"] in _S["cats"]

        def toggle(e, key=meta["id"]):
            if key in _S["cats"]:
                _S["cats"].remove(key)
            else:
                _S["cats"].append(key)
            _S["spots"] = None
            rerender()

        return ft.Container(
            padding=PAD(3, 8), border_radius=9, ink=True, on_click=toggle,
            bgcolor=ft.Colors.with_opacity(0.18, meta["color"]) if on else p["bg2"],
            border=BRD(meta["color"] if on else p["border"], 1),
            content=ft.Row(spacing=5, tight=True, controls=[
                ft.Icon(meta["icon"], size=12,
                        color=meta["color"] if on else p["text2"]),
                ft.Text(meta["label"], size=10,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["text"] if on else p["text2"])]))

    def dist_chip(mi, metres):
        on = _S["dist"] == mi

        def pick(e, mi=mi):
            _S["dist"] = mi
            _S["spots"] = None
            rerender()

        return ft.Container(
            padding=PAD(3, 10), border_radius=9, ink=True, on_click=pick,
            bgcolor=p["accent"] if on else p["bg2"],
            border=BRD(p["accent"] if on else p["border"], 1),
            content=ft.Text(f"{mi} mi", size=10,
                            weight=ft.FontWeight.W_700 if on else ft.FontWeight.W_400,
                            color="#0d1117" if on else p["text2"]))

    def rerender():
        """Repaint this tab in place, keeping the chrome. As _forms.rebuilder."""
        from ..ui import S
        rerun = getattr(S, "rerender", None)
        if callable(rerun):
            rerun()
            return
        repaint()

    # ---------------------------------------------------------------- scan
    async def do_scan(e=None):
        metres = dict(DISTANCES)[_S["dist"]]
        say(True, f"scanning {_S['dist']} mi...")
        repaint()
        got = await asyncio.to_thread(scan, list(_S["cats"]), metres)
        if got is None:
            _S["spots"] = None
            say(False, config.STATUS.get("detail") or "server unreachable")
        else:
            _S["spots"] = got
            say(True, f"{len(got)} within {_S['dist']} mi of home")
        rerender()

    # --------------------------------------------------------------- writes
    async def do_save(spot):
        say(True, "saving...")
        repaint()
        ok, msg = await asyncio.to_thread(save_gem, spot)
        say(ok, msg)
        rerender()

    async def do_delete(gem_id):
        say(True, "removing...")
        repaint()
        ok, msg = await asyncio.to_thread(delete_gem, gem_id)
        say(ok, msg)
        rerender()

    # ------------------------------------------------------------ overlays
    def overlay(heading, body):
        dlg = ft.AlertDialog(
            title=ft.Text(heading, size=14, weight=ft.FontWeight.W_600),
            content=ft.Column(spacing=8, width=420, tight=True,
                              scroll=ft.ScrollMode.AUTO, controls=[body]),
            actions=[])
        dlg.actions = [ft.TextButton("Close", on_click=lambda e: dismiss(dlg))]
        return dlg

    async def do_photos(spot):
        note = ft.Text("Loading photos...", size=11, color=p["text2"])
        strip = ft.Column(spacing=8, controls=[note])
        show(overlay(spot["name"], strip))
        repaint()
        got = await asyncio.to_thread(photos, spot)
        if got is None:
            note.value = ("No photos on this device for that spot, and the "
                          "server is not answering.")
        elif not got:
            note.value = "No free photos found for this spot."
        else:
            strip.controls = [
                ft.Column(spacing=2, controls=[
                    ft.Image(src=ph.get("thumb") or "", width=380, fit=ft.ImageFit.CONTAIN,
                             error_content=sub("image did not load", 10)),
                    sub(" · ".join([x for x in (ph.get("artist"), ph.get("license")) if x])
                        or "Wikimedia Commons", 9)])
                for ph in got[:12]]
        repaint()

    async def do_street_view(spot):
        note = ft.Text("Loading street view...", size=11, color=p["text2"])
        box = ft.Column(spacing=6, controls=[note])
        show(overlay(spot["name"], box))
        repaint()
        url, caption = await asyncio.to_thread(street_view, spot)
        if url is None:
            note.value = caption
        else:
            box.controls = [
                ft.Image(src=url, width=400, fit=ft.ImageFit.CONTAIN,
                         error_content=sub("street view did not load", 10)),
                sub(caption or "", 10)]
        repaint()

    # ------------------------------------------------------------- the rows
    def action(icon, tooltip, handler):
        return ft.IconButton(icon, icon_size=15, icon_color=p["text2"],
                             tooltip=tooltip, on_click=handler)

    def row(spot, *, saved_row):
        meta = CAT_BY_ID.get(spot["cat"], CAT_BY_ID["pretty"])
        pending = bool(spot.get("pending"))
        buttons = [action(ft.Icons.IMAGE, "Photos",
                          lambda e, s=spot: _run(page, do_photos, s)),
                   action(ft.Icons.STREETVIEW, "Street view",
                          lambda e, s=spot: _run(page, do_street_view, s))]
        if saved_row:
            if pending:
                # No server id yet, so nothing to delete by. Saying "sending"
                # is the truth; a delete button here would quietly fail.
                buttons.append(chip("sending", p["yellow"], small=True))
            else:
                buttons.append(action(ft.Icons.DELETE_OUTLINE, "Remove",
                                      lambda e, i=spot["id"]: _run(page, do_delete, i)))
        else:
            already = any(g["oid"] and g["oid"] == spot["oid"]
                          for g in (somedays or []))
            buttons.append(
                chip("saved", p["green"], small=True) if already else
                action(ft.Icons.BOOKMARK_ADD_OUTLINED, "Save to the someday list",
                       lambda e, s=spot: _run(page, do_save, s)))
        return ft.Container(
            padding=PAD(3, 2), border_radius=6,
            content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(meta["icon"], size=15, color=meta["color"]),
                               ft.Column(spacing=0, expand=True, controls=[
                                   ft.Text(spot["name"], size=12, color=p["text"]),
                                   sub(f"{meta['label']} · "
                                       f"{miles(spot['dist_m']):.1f} mi", 10)]),
                               *buttons]))

    spots = _S["spots"]
    if spots is None:
        results.controls = [sub("Pick categories and a distance, then Scan.", 11)]
        count.value = ""
    elif not spots:
        results.controls = [sub("Nothing in those categories that far out.", 11)]
        count.value = "0 found"
    else:
        results.controls = [row(s, saved_row=False) for s in spots]
        count.value = f"{len(spots)} found"

    if somedays is None:
        saved_box.controls = [sub("No someday list on this device yet - "
                                  "connect once and it will be here offline.", 11)]
    elif not somedays:
        saved_box.controls = [sub("Nothing saved yet. Scan, then tap the "
                                  "bookmark on a gem.", 11)]
    else:
        saved_box.controls = [row(g, saved_row=True) for g in somedays]

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        *([] if live else [offline_banner(p, "the someday list")]),
        card(ft.Column(spacing=8, controls=[
            ft.Row([title("Future Finds", 14), ft.Container(expand=True), count]),
            sub("Gems from the server's own California table, measured from "
                "home. No Overpass, so it answers instantly.", 10),
            ft.Row(spacing=6, controls=[
                *[dist_chip(mi, m) for mi, m in DISTANCES],
                ft.Container(expand=True),
                ft.FilledButton("Scan", height=32, icon=ft.Icons.TRAVEL_EXPLORE,
                                on_click=lambda e: _run(page, do_scan))]),
            ft.Row(spacing=6, wrap=True,
                   controls=[cat_chip(CAT_BY_ID[c[0]]) for c in GEM_CATS]),
        ])),
        card(ft.Column(spacing=4, controls=[title("Found", 13), results])),
        card(ft.Column(spacing=4, controls=[
            ft.Row([title("Someday list", 13), ft.Container(expand=True),
                    sub(f"{len(somedays)} saved" if somedays else "", 10)]),
            saved_box])),
        ft.Container(padding=PAD(0, 4), content=status),
    ])
