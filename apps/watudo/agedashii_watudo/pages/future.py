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
empty list, rather than a plausible one. That is also why the offline notice
is `not_connected` here rather than `_forms.offline_banner` - see its
docstring; the shared one announces sample data that does not exist and
writes that do not save, and neither is true on this page.
"""
import asyncio
import time
import urllib.parse

import flet as ft

from agedashii_washi import api as M
from agedashii_washi import config
from agedashii_washi.ui_compat import PAL, PAD, BRD, RAD, card, chip, title, sub
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


# app.js's curated meta: the key the server uses, a label, and a colour.
# These are hand-kept lists the server ships, not scan results - backroads,
# redwoods, EBT-accepting museums, film locations, oddities, and scraped
# Funcheap events. Counts as of 2026-09-21: 12, 17, 178, 1362, 10, 46.
CURATED = (
    ("backroads", "Backroads", "#f2c94c", ft.Icons.ROUTE),
    ("redwoods", "Redwoods", "#66bb6a", ft.Icons.FOREST),
    ("weirdseed", "Weird", "#ff8a65", ft.Icons.REMOVE_RED_EYE),
    ("ebt", "EBT museums", "#ba68c8", ft.Icons.MUSEUM),
    ("movies", "Film locations", "#ff8fab", ft.Icons.MOVIE),
    ("events", "Events", "#4dd0e1", ft.Icons.CALENDAR_MONTH),
)
CURATED_BY_ID = {c[0]: {"id": c[0], "label": c[1], "color": c[2], "icon": c[3]}
                 for c in CURATED}

# app.js caps a curated list at 250 after sorting by distance from home.
MAX_CURATED = 250


def curated_spots(curated, key):
    """One curated list as spots -> (placeable spots, names with no coordinates).

    A third of the EBT list has ``"lat": null`` - those are real places the
    list knows only by name and city, and dropping them silently would shrink
    "178 museums" to 117 with nothing said. They come back separately so the
    page can name them instead of pretending they were never there.
    """
    rows = (curated or {}).get(key)
    if not isinstance(rows, list):
        return [], []
    spots, nameless = [], []
    for r in rows:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError, KeyError):
            city = str(r.get("city") or "").strip()
            nameless.append(f"{r['name']} ({city})" if city else str(r["name"]))
            continue
        spots.append({"oid": f"curated:{key}:{r['name']}", "name": str(r["name"]),
                      "cat": "weird" if key == "weirdseed" else "pretty",
                      "lat": round(lat, 6), "lon": round(lon, 6),
                      "note": str(r.get("note") or ""), "imgs": [],
                      "url": str(r.get("url") or ""),
                      "dist_m": haversine_m(HOME, (lat, lon))})
    spots.sort(key=lambda s: s["dist_m"])
    return spots[:MAX_CURATED], nameless


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


def curated():
    """The server's curated lists, or (None, why). Cached like every read."""
    return M.future_curated()


def _events_age(cur_data):
    """How stale the scraped events are, in the server's own number.

    ``events_stale_h`` sits alongside the lists in the same payload. Showing
    events without it invites reading a week-old scrape as tonight's diary.
    """
    hours = (cur_data or {}).get("events_stale_h")
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return ""
    if hours < 1:
        return "events scraped under an hour ago"
    if hours < 48:
        return f"events scraped {hours:.0f}h ago"
    return f"events scraped {hours / 24:.0f} days ago"


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
    ok, msg = M._queue_write("/api/future/gems/delete", json_body={"id": gem_id},
                             timeout=15, local={GEMS_PATH: local},
                             invalidate=(GEMS_PATH,)).pair
    # api._ok's word for every accepted write is "saved", which read as
    # nonsense on the one write here that takes something away.
    return ok, ("removed" if ok and msg == "saved" else msg)


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
def not_connected(p, somedays):
    """The offline notice for this tab, in the shape of `_forms.offline_banner`.

    Not `offline_banner` itself, deliberately: its fixed wording is "Showing
    sample data ... changes will not save", and on this page both halves are
    false. There is no mock Future data anywhere in the app - offline you are
    looking at this device's own copy of real answers, or at nothing - and a
    gem saved here is queued in the outbox and does go, later. Saying
    otherwise would be the exact lie the banner exists to prevent.
    """
    return card(ft.Row(spacing=8, controls=[
        ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=p["yellow"]),
        ft.Text("Not connected to the server. " + (
            "Showing this device's copy; a gem you save is queued and sent "
            "when signal is back." if somedays is not None else
            "Nothing of Future is on this device yet, so there is nothing "
            "to show - connect once and it will be here offline."),
            size=11, color=p["text2"], expand=True)]), pad=10)


# Survives a repaint so a scan is not thrown away every time the page
# rebuilds - the same reason wat2do.py keeps its `_S`.
_S = {"cats": list(DEFAULT_CATS), "dist": 50, "spots": None, "note": "",
      # Which curated list is open, if any. It replaces the scan results in
      # the Found card rather than sitting beside them, exactly as on the
      # website, because they answer the same question two ways.
      "curated": None}


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
            padding=PAD(3, 8), border_radius=RAD(9), ink=True, on_click=toggle,
            bgcolor=ft.Colors.with_opacity(0.18, meta["color"]) if on else p["bg2"],
            border=BRD(meta["color"] if on else p["border"], 1),
            content=ft.Row(spacing=5, tight=True, controls=[
                ft.Icon(meta["icon"], size=12,
                        color=meta["color"] if on else p["text2"]),
                ft.Text(meta["label"], size=10,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["text"] if on else p["text2"])]))

    def cur_chip(meta, count):
        on = _S["curated"] == meta["id"]

        def toggle(e, key=meta["id"]):
            _S["curated"] = None if _S["curated"] == key else key
            rerender()

        return ft.Container(
            padding=PAD(3, 8), border_radius=RAD(9), ink=True, on_click=toggle,
            bgcolor=ft.Colors.with_opacity(0.18, meta["color"]) if on else p["bg2"],
            border=BRD(meta["color"] if on else p["border"], 1),
            content=ft.Row(spacing=5, tight=True, controls=[
                ft.Icon(meta["icon"], size=12,
                        color=meta["color"] if on else p["text2"]),
                ft.Text(f"{meta['label']} {count}" if count else meta["label"],
                        size=10,
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400,
                        color=p["text"] if on else p["text2"])]))

    def dist_chip(mi, metres):
        on = _S["dist"] == mi

        def pick(e, mi=mi):
            _S["dist"] = mi
            _S["spots"] = None
            rerender()

        return ft.Container(
            padding=PAD(3, 10), border_radius=RAD(9), ink=True, on_click=pick,
            bgcolor=p["accent"] if on else p["bg2"],
            border=BRD(p["accent"] if on else p["border"], 1),
            content=ft.Text(f"{mi} mi", size=10,
                            weight=ft.FontWeight.W_700 if on else ft.FontWeight.W_400,
                            color="#0d1117" if on else p["text2"]))

    def rerender():
        """Repaint this tab in place, keeping the chrome. As _forms.rebuilder."""
        from agedashii_washi.ui_compat import S
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
            padding=PAD(3, 2), border_radius=RAD(6),
            content=ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               ft.Icon(meta["icon"], size=15, color=meta["color"]),
                               ft.Column(spacing=0, expand=True, controls=[
                                   ft.Text(spot["name"], size=12, color=p["text"]),
                                   sub(f"{meta['label']} · "
                                       f"{miles(spot['dist_m']):.1f} mi", 10)]),
                               *buttons]))

    # ---------------------------------------------------------- curated
    cur_data, cur_err = curated()
    cur_counts = {k: len(v) for k, v in (cur_data or {}).items()
                  if isinstance(v, list)}
    cur_spots, cur_nameless = [], []
    if _S["curated"]:
        cur_spots, cur_nameless = curated_spots(cur_data, _S["curated"])

    if _S["curated"]:
        meta = CURATED_BY_ID.get(_S["curated"], CURATED[0])
        if cur_spots:
            results.controls = [row(s, saved_row=False) for s in cur_spots]
            # Film locations is 1362 rows and the list is capped at 250, so
            # "250 in Film locations" would read as the whole list. The rows
            # with no coordinates are counted out of this and named below
            # instead, so the two omissions do not get confused for each other.
            placeable = cur_counts.get(_S["curated"], len(cur_spots)) - len(cur_nameless)
            count.value = (f"{len(cur_spots)} nearest of {placeable} in {meta['label']}"
                           if len(cur_spots) < placeable
                           else f"{len(cur_spots)} in {meta['label']}")
        else:
            results.controls = [sub(
                cur_err or f"The server's {meta['label']} list is empty.", 11)]
            count.value = ""
        if cur_nameless:
            # Not a footnote: these are real entries the list holds without
            # coordinates, and quietly dropping them would turn 178 museums
            # into 117 with nothing said about the rest.
            shown = " · ".join(cur_nameless[:8])
            results.controls.append(sub(
                f"{len(cur_nameless)} more in this list have no coordinates, so "
                f"they cannot be placed or ridden to: {shown}"
                + ("…" if len(cur_nameless) > 8 else ""), 10))
    else:
        spots = _S["spots"]
        if spots is None:
            results.controls = [sub("Pick categories and a distance, then Scan — "
                                    "or open one of the curated lists above.", 11)]
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
        *([] if live else [not_connected(p, somedays)]),
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
        card(ft.Column(spacing=6, controls=[
            ft.Row([title("Curated lists", 13), ft.Container(expand=True),
                    sub(_events_age(cur_data), 10)]),
            sub("Lists the server keeps by hand, not scan results. Tap one to "
                "read it; tap it again to go back to your scan.", 10),
            ft.Row(spacing=6, wrap=True, run_spacing=6, controls=[
                cur_chip(CURATED_BY_ID[c[0]], cur_counts.get(c[0], 0))
                for c in CURATED]),
            *([sub(cur_err, 10)] if cur_err else []),
        ])),
        card(ft.Column(spacing=4, controls=[
            title("Curated" if _S["curated"] else "Found", 13), results])),
        card(ft.Column(spacing=4, controls=[
            ft.Row([title("Someday list", 13), ft.Container(expand=True),
                    sub(f"{len(somedays)} saved" if somedays else "", 10)]),
            saved_box])),
        ft.Container(padding=PAD(0, 4), content=status),
    ])
