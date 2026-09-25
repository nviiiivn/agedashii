"""Tools - Feeds, Vault, Journal and FoodZen.

The phone's half of the website's Tools page (dashbored-web/app.js: the
``fg*``, ``vault*`` and ``jn*`` functions). Built against the same endpoints
the browser uses so the two clients show the same thing:

  ``/api/feeds``                          76 items on the live server today
  ``/api/vault/{tree,read,save}``         DeVault and vault on ai-tp
  ``/api/journal/{tree,read,new,save,delete}``

Three rules, each of them a bug that has shipped here before:

* **Nothing appears until the server has it.** Every write reports what the
  server said and only then repaints. Ticking a box or saving a note that
  never left the phone is the lie this app exists not to tell.
* **Nothing is capped without saying so.** Feeds used to show the first 20 of
  76 items with nothing on screen admitting the other 56 existed. It shows a
  page at a time again - 76 cards was 1221 of this page's 1915 controls - but
  now the count, the total and the buttons that fetch the rest are on screen.
  Silence was the bug, not the cap.
* **Writes go through the outbox**, so a note written with no signal is
  queued and sent later rather than lost - and says which of the two it is.
"""
import asyncio

import flet as ft

from .. import api as M
from .. import config
from ..ui import PAL, PAD, BRD, RAD, S, card, chip, sub, title
from ._forms import Writer, offline_banner, rebuilder
from .lab import _tabs
from . import extras as _extras

# Session state, at module level so a repaint after a write does not throw
# away which feed filter, vault file or journal search you were looking at.
#
# Deliberately *not* a data cache. api._get already answers these from the
# device's store in under a millisecond and refreshes behind the page, so
# holding a second copy here only means the tab shows yesterday's list until
# the app is restarted. What is kept is what the store cannot know: where
# you were.
#
# `_F["show"]` is how many feed cards are on screen; see `_feeds`.
FEED_PAGE = 15

_F = {"kind": "all", "show": FEED_PAGE}
_V = {"host": M.VAULT_DEFAULT_HOST, "lib": "DeVault",
      "open": set(), "file": None, "text": None, "editing": False}
_J = {"cat": "all", "q": "", "sort": "new", "mode": "list", "open": None,
      "editing": None, "title": "", "body": ""}

# Category colour and icon, matching the website's JN_CATS so an entry is the
# same colour in both clients.
JN_CATS = {
    "Mental-Health": ("Mental Health", ft.Icons.PSYCHOLOGY, "#b48ead"),
    "Trip-Reports": ("Trip Reports", ft.Icons.ROUTE, "#5eb0e5"),
    "Mechanicry": ("Mechanicry", ft.Icons.BUILD, "#e0a458"),
    "New-Projects": ("New Projects", ft.Icons.ROCKET_LAUNCH, "#a3be8c"),
    "Dr-Visits": ("Dr Visits", ft.Icons.MONITOR_HEART, "#e07a5f"),
    "Medications": ("Medications", ft.Icons.MEDICATION, "#9d8fff"),
}

JN_SORTS = (("new", "Newest first"), ("old", "Oldest first"), ("az", "A-Z"))

def build(page):
    """Feeds, Vault, Journal, Projects, AI and Obligations.

    FoodZen used to be a fourth tab here, a page-inside-a-folder with six
    tabs of its own. It is its own app now (Agedashii Foodzen) and has
    been taken out of Tofu along with the rest of the split surfaces.

    Projects, AI and Obligations arrived from Extras, which stopped being
    a page of its own. They are wrapped in lambdas here; on Extras they
    were built eagerly, so all three were rebuilt every time you touched
    any one of them.

    Every tab is a callable, so only the one on screen is built. All of them
    used to be built before anything painted - 1915 controls, of which 1221
    were the 76 feed cards and 503 were the six FoodZen tabs nobody had
    opened. See ``lab._tabs`` for the mechanism, and for what that costs:
    while a slow build runs, Flet holds the old frame and drops every tap.
    """
    return _tabs(page, [("Feeds", lambda: _feeds(page)),
                        ("Vault", lambda: _vault(page)),
                        ("Journal", lambda: _journal(page)),
                        ("Projects", lambda: _extras._projects(page)),
                        ("AI", lambda: _extras._ai(page)),
                        ("Obligations", lambda: _extras._obl())],
                 key="tools")


def _run(page, coro_fn, *args):
    """page.run_task when there is a loop, otherwise quietly skip.

    Tests and the import check build pages with a stub page where scheduling
    work would fail or hang; painting the placeholders and stopping is the
    honest outcome there.
    """
    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return
    try:
        runner(coro_fn, *args)
    except Exception:          # noqa: BLE001 - no running loop
        pass


def _repaint(page):
    """Repaint from what is already held. Deliberately not ``rebuilder``.

    How much of a list is on screen, or a feed landing behind the page, is
    not a reason to mark every cached answer in the app stale and re-read
    the lot - which is what ``rebuilder`` does, because it is for writes.
    """
    fn = getattr(S, "rerender", None)
    if callable(fn):
        fn()
        return
    try:
        page.update()
    except Exception:              # noqa: BLE001 - not mounted, or torn down
        pass


def _field(p, hint, **kw):
    kw.setdefault("dense", True)
    kw.setdefault("filled", True)
    kw.setdefault("text_size", 12)
    return ft.TextField(hint_text=hint, border_color=p["border"],
                        focused_border_color=p["accent"], color=p["text"], **kw)


def _pill(p, label, on, on_click, colour=None):
    c = colour or p["accent"]
    return ft.Container(
        padding=PAD(4, 11), border_radius=RAD(20), ink=True, on_click=on_click,
        bgcolor=ft.Colors.with_opacity(0.2 if on else 0.05, c),
        border=BRD(ft.Colors.with_opacity(0.55 if on else 0.22, c), 1),
        content=ft.Text(label, size=11, color=c if on else p["text2"],
                        weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400))


# =========================================================================
# Feeds
# =========================================================================
FEEDS_PATH = "/api/feeds"

# What the last background read of FEEDS_PATH did. `error` is only ever set
# by a read that actually ran, so a tab painted before the first fetch says
# it is reading rather than claiming the server has never answered.
_FEEDS = {"busy": False, "error": ""}


def _feed_payload(raw):
    """`api.feeds_full`'s shape, out of whatever the store holds.

    The same four keys and the same defensive copies, because this is the
    cached half of that call and the two must not drift.
    """
    if not isinstance(raw, dict):
        return None
    return {"ts": raw.get("ts"), "items": list(raw.get("items") or []),
            "errors": dict(raw.get("errors") or {}),
            "sources": dict(raw.get("sources") or {})}


def _catch_up_feeds(page):
    """Read /api/feeds off the wire behind the tab. True if that started.

    /api/feeds is the slowest read on this page - 1.23 s against the live
    server, because it rescrapes its sources - and `feeds_full` used to be
    called straight from the build. Flet runs that build synchronously on
    its event loop, so the old frame stayed up and every tap during it was
    dropped, which is what "this button does nothing" is.

    Repaints only when the answer or the error actually changed, so the
    repaint it causes cannot cause one of its own for ever.
    """
    async def run():
        if _FEEDS["busy"]:
            return
        _FEEDS["busy"] = True
        had, had_error = M._cached(FEEDS_PATH), _FEEDS["error"]
        try:
            fresh = await asyncio.to_thread(M.feeds_full)
        finally:
            _FEEDS["busy"] = False
        _FEEDS["error"] = ("" if fresh is not None else
                           (config.STATUS.get("detail") or "server unreachable"))
        if _feed_payload(had) != fresh or _FEEDS["error"] != had_error:
            _repaint(page)

    runner = getattr(page, "run_task", None)
    if not callable(runner):
        return False
    try:
        runner(run)
    except Exception:              # noqa: BLE001 - no loop; we keep what we have
        return False
    return True


def _feeds(page):
    """Every feed item, filterable, each one a link - a page at a time.

    The old card list read `M.FEEDS`, which `api.feeds()` trims to 20 rows.
    Today the server has 76. Nothing on screen said so, so a bike night in
    the 21st row simply did not exist as far as the phone was concerned.

    All 76 are still reachable, and the count of every one of them is still
    on the chips. What changed is that they are not all *built* at once: 76
    cards - a thumbnail, a title, a location, a description and up to four
    tag chips each - came to 1221 controls, which is two thirds of this
    page. `_F["show"]` many are drawn, the footer says how many that is out
    of how many there are, and two buttons fetch the next page or the lot.
    """
    p = PAL()
    # The store, not the wire - see `_catch_up_feeds`, which does the wire.
    data = _feed_payload(M._cached(FEEDS_PATH))
    fetching = _catch_up_feeds(page)

    body = ft.Column(spacing=8)
    meta = ft.Text("", size=10, color=p["text2"])
    chips = ft.Row(spacing=6, wrap=True)

    def source_meta(src):
        got = (data or {}).get("sources", {}).get(src) or {}
        return (got.get("label") or src or "?"), (got.get("color") or "#8b949e")

    def row(it):
        label, colour = source_meta(it.get("source"))
        iso = it.get("iso") or ""
        # "Soon" is anything dated today or later, compared against the LOCAL
        # date. Comparing against a UTC date marks tonight's ride as past
        # from 5pm onwards.
        soon = bool(iso) and iso >= M.local_today()
        head = ft.Row(spacing=6, controls=[
            ft.Text(label, size=10, color=colour, weight=ft.FontWeight.W_600),
            ft.Container(expand=True),
            ft.Text(it.get("date") or "", size=10,
                    color=p["green"] if soon else p["text2"],
                    weight=ft.FontWeight.W_600 if soon else ft.FontWeight.W_400)])
        lines = [head, ft.Text(it.get("title") or "(untitled)", size=13,
                               weight=ft.FontWeight.W_500, color=p["text"])]
        if it.get("location"):
            lines.append(ft.Row(spacing=4, controls=[
                ft.Icon(ft.Icons.PLACE, size=11, color=p["text3"]),
                ft.Text(it["location"], size=10, color=p["text2"], expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)]))
        if it.get("desc"):
            lines.append(ft.Text(it["desc"], size=10, color=p["text3"],
                                 max_lines=3, overflow=ft.TextOverflow.ELLIPSIS))
        if it.get("tags"):
            lines.append(ft.Row(spacing=4, wrap=True, controls=[
                chip(str(tag), p["text3"], small=True) for tag in it["tags"][:4]]))
        # 20 of the 76 items carry a real image URL; the rest get the icon the
        # website falls back to. error_content matters - these are other
        # people's servers, and a hotlink that 404s must not leave a hole.
        thumb = ft.Container(
            width=54, height=54, border_radius=RAD(8), bgcolor=p["bg2"],
            alignment=ft.Alignment(0, 0),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=(ft.Image(src=it["img"], width=54, height=54,
                              fit=ft.BoxFit.COVER,
                              error_content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED,
                                                    size=18, color=p["text3"]))
                     if it.get("img") else
                     ft.Icon(ft.Icons.ROUTE if it.get("type") == "route"
                             else ft.Icons.EVENT, size=20, color=p["text3"])))
        inner = ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.START,
                       controls=[thumb, ft.Column(spacing=5, expand=True,
                                                  controls=lines)])
        url = it.get("url")
        if not url:
            return card(inner, pad=10)
        # An item with a URL is a link, exactly as it is in the browser.
        # Container.url is the whole mechanism in Flet 0.86 - there is no
        # url_target on it, so the platform decides where it opens.
        return ft.Container(
            border_radius=RAD(12), bgcolor=p["surface"], border=BRD(p["border"], 1),
            padding=10, ink=True, url=url, content=inner)

    def more_row(shown, total):
        """The footer that admits to the cap and undoes it.

        Every count here is measured, never rounded or guessed: `shown` is
        the number of cards actually on screen.
        """
        return card(ft.Column(spacing=6, controls=[
            sub(f"showing {shown} of {total} in this filter", 11),
            ft.Row(spacing=6, wrap=True, controls=[
                ft.FilledButton(f"Show {min(FEED_PAGE, total - shown)} more",
                                height=32, on_click=show(shown + FEED_PAGE)),
                ft.OutlinedButton(f"Show all {total}", height=32,
                                  on_click=show(total)),
            ]),
        ]), pad=10)

    def paint():
        if data is None:
            # Three different nothings, and calling them all "unavailable"
            # is how a tab that is simply still reading looks broken.
            if _FEEDS["error"]:
                body.controls = [card(ft.Column(spacing=4, controls=[
                    ft.Text("No feeds", size=13, weight=ft.FontWeight.W_600,
                            color=p["text"]),
                    ft.Text(_FEEDS["error"], size=11, color=p["pink"],
                            selectable=True),
                    sub(f"asked {config.SERVER_URL}{FEEDS_PATH}", 10)]), pad=12)]
            elif fetching or _FEEDS["busy"]:
                body.controls = [card(ft.Row(spacing=10, controls=[
                    ft.ProgressRing(width=15, height=15, stroke_width=2),
                    sub(f"Reading {FEEDS_PATH} - it rescrapes every source, "
                        f"so it takes a second", 11)]), pad=12)]
            else:
                body.controls = [card(sub(
                    "Feeds unavailable - the server has never answered "
                    "/api/feeds on this device.", 11), pad=12)]
            meta.value = ""
            chips.controls = []
            return
        items = M.feed_filter(data["items"], _F["kind"])
        counts = {k: len(M.feed_filter(data["items"], k))
                  for k, _ in M.feed_types(data["items"])}
        chips.controls = [
            _pill(p, f"{label} · {counts.get(k, 0)}", _F["kind"] == k, pick(k))
            for k, label in M.feed_types(data["items"])]
        # Count what is on screen, not what was fetched. Tapping "Local · 3"
        # narrowed the list to three cards and left this line reading
        # "76 items", which says the filter did not work. When a filter is
        # on, both numbers are shown so the total is still there.
        total = len(data["items"])
        if _F["kind"] and len(items) != total:
            bits = [f"{len(items)} of {total} items"]
        else:
            bits = [f"{total} items"]
        errs = data.get("errors") or {}
        if errs:
            # The website says how many sources failed and names them. A list
            # that is quietly short is the same lie as a list that is capped.
            bits.append(f"{len(errs)} source(s) unreachable: "
                        + ", ".join(sorted(errs)))
        meta.value = " · ".join(bits)
        shown = items[:max(_F["show"], 1)]
        body.controls = ([row(i) for i in shown]
                         or [card(sub("Nothing here yet.", 11), pad=12)])
        if len(shown) < len(items):
            body.controls.append(more_row(len(shown), len(items)))

    def repaint():
        try:
            page.update()
        except Exception:          # noqa: BLE001 - not mounted yet
            pass

    def show(n):
        """Draw `n` cards. Repaints the list itself - no server read at all.

        Rebuilding the page would re-read /api/feeds for a change that is
        purely how much of what we already hold is on screen.
        """
        def handler(e):
            _F["show"] = n
            paint()
            repaint()
        return handler

    def pick(kind):
        def handler(e):
            _F["kind"] = kind
            # A different filter is a different list, so the page count
            # starts over rather than showing 60 of a 3-item filter and
            # nothing of the next.
            _F["show"] = FEED_PAGE
            paint()
            repaint()
        return handler

    async def rescan(e):
        meta.value = "rescanning sources..."
        body.controls = [card(sub("scanning...", 11), pad=12)]
        try:
            page.update()
        except Exception:          # noqa: BLE001
            pass
        # force=1 makes the server rescrape every source, which takes long
        # enough that it must not be the read path. The result lands in the
        # store; the rebuild reads it back out of there.
        await asyncio.to_thread(M.feeds_full, True)
        rebuilder(page, build)()

    paint()
    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[chips, ft.Container(expand=True),
                         ft.IconButton(ft.Icons.REFRESH, icon_size=17,
                                       icon_color=p["text2"],
                                       tooltip="Rescan every source",
                                       on_click=rescan)]),
        meta, body])


# =========================================================================
# Vault
# =========================================================================
def _vault(page):
    """Browse, read and edit the vaults - not just write blind into them.

    This was a three-field composer: pick a machine, type a path, type a
    body, hope. It could not list what was already there, so it could not
    tell you that you were about to overwrite a note, and the comment said
    listing "needs an SSH round trip the app has no endpoint for yet".

    It does have one. `/api/vault/tree` and `/api/vault/read` have been on
    the server all along - they are what the browser's vault browser runs on.
    """
    p = PAL()
    w = Writer(page, rebuilder(page, build))

    tree, tree_err = M.vault_tree(_V["host"], _V["lib"])

    tree_box = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, height=250)
    name_line = ft.Text(_V["file"] or "Select a note", size=11,
                        color=p["text"] if _V["file"] else p["text2"],
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True)
    viewer = ft.Column(spacing=4)
    # Seeded from the session, not blank: a write elsewhere on the page
    # rebuilds this whole tab, and a fresh empty editor over an open note is
    # how a half-typed edit becomes a saved empty file.
    editor = _field(p, "note body...", value=_V["text"] or "", multiline=True,
                    min_lines=10, max_lines=24, visible=False)
    save_btn = ft.FilledButton("Save", height=32, visible=False)
    cancel_btn = ft.OutlinedButton("Cancel", height=32, visible=False)
    edit_btn = ft.OutlinedButton("Edit", height=32, visible=False)

    def repaint():
        try:
            page.update()
        except Exception:          # noqa: BLE001 - not mounted yet
            pass

    def paint_tree():
        if tree_err:
            tree_box.controls = [sub(tree_err, 11)]
            return
        rows = M.vault_visible(M.vault_flatten(tree or []), _V["open"])
        out = []
        for r in rows:
            if r["dir"]:
                is_open = r["path"] in _V["open"]
                out.append(ft.Container(
                    padding=ft.Padding(4 + 14 * r["depth"], 3, 4, 3),
                    border_radius=RAD(6), ink=True,
                    on_click=lambda e, path=r["path"]: toggle_dir(path),
                    content=ft.Row(spacing=5, controls=[
                        ft.Icon(ft.Icons.KEYBOARD_ARROW_DOWN if is_open
                                else ft.Icons.CHEVRON_RIGHT,
                                size=14, color=p["text2"]),
                        ft.Icon(ft.Icons.FOLDER, size=13, color=p["yellow"]),
                        ft.Text(r["name"], size=11.5, color=p["text"], expand=True,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)])))
            else:
                on = _V["file"] == r["path"]
                out.append(ft.Container(
                    padding=ft.Padding(4 + 14 * r["depth"] + 14, 3, 4, 3),
                    border_radius=RAD(6), ink=True,
                    bgcolor=(ft.Colors.with_opacity(0.14, p["accent"]) if on
                             else None),
                    on_click=lambda e, path=r["path"]: _run(page, open_file, path),
                    content=ft.Row(spacing=5, controls=[
                        ft.Icon(ft.Icons.DESCRIPTION, size=12,
                                color=p["accent"] if on else p["text3"]),
                        ft.Text(r["name"], size=11.5, expand=True, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color=p["accent"] if on else p["text2"])])))
        tree_box.controls = out or [sub("This vault is empty.", 11)]

    def toggle_dir(path):
        _V["open"] ^= {path}
        paint_tree()
        repaint()

    def paint_viewer():
        text = _V["text"]
        if text is None:
            viewer.controls = [sub("Select a note from the tree to read it.", 11)]
            for b in (edit_btn, save_btn, cancel_btn):
                b.visible = False
            editor.visible = False
            return
        viewer.controls = _markdown(text, p)
        viewer.visible = not _V["editing"]
        editor.visible = _V["editing"]
        edit_btn.visible = not _V["editing"]
        save_btn.visible = cancel_btn.visible = _V["editing"]

    async def open_file(path):
        _V["file"] = path
        _V["editing"] = False
        name_line.value = path
        viewer.controls = [sub("loading...", 11)]
        paint_tree()
        repaint()
        text = await asyncio.to_thread(M.vault_read, _V["host"], _V["lib"], path)
        if text is None:
            _V["text"] = None
            viewer.controls = [ft.Text("Could not read that note.", size=12,
                                       color=p["pink"])]
            repaint()
            return
        _V["text"] = text
        editor.value = text
        paint_viewer()
        repaint()

    def start_edit(e):
        if _V["text"] is None:
            return
        _V["editing"] = True
        editor.value = _V["text"]
        paint_viewer()
        repaint()

    def cancel_edit(e):
        _V["editing"] = False
        paint_viewer()
        repaint()

    async def do_save(e):
        if not _V["file"]:
            return
        w.busy()
        ok, msg = await asyncio.to_thread(
            M.vault_write, _V["host"], _V["file"], editor.value or "", _V["lib"])
        w.report(ok, msg)
        if ok:
            # Only now is the saved text the truth on screen. Repainting
            # before the server answered is how an unsaved edit looks saved.
            _V["text"] = editor.value or ""
            _V["editing"] = False
            paint_viewer()
        repaint()

    edit_btn.on_click = start_edit
    cancel_btn.on_click = cancel_edit
    save_btn.on_click = do_save

    def switch_lib(lib):
        def handler(e):
            if _V["lib"] == lib:
                return
            _V["lib"] = lib
            _V.update({"file": None, "text": None, "editing": False,
                       "open": set()})
            rebuilder(page, build)()
        return handler

    # machine_config(), not M.MACHINES. The capitalised name falls through
    # api._MAP to mock.MACHINES when the read fails, so with no signal this
    # picker would offer five machines off a hardcoded list, two of which do
    # not have the addresses the mock claims. None here means offer only the
    # machine already selected, which is at least true.
    cfg = M.machine_config() or {}
    opts = [ft.DropdownOption(key=mid, text=(m or {}).get("display", mid))
            for mid, m in sorted(cfg.items())]
    host_pick = ft.Dropdown(
        value=_V["host"], dense=True, filled=True, text_size=11, width=130,
        border_color=p["border"], color=p["text"],
        options=opts or [ft.DropdownOption(key=_V["host"], text=_V["host"])])

    def pick_host(e):
        # Flet 0.86: Dropdown fires on_select, not on_change. Wiring
        # on_change here is a control that looks live and never fires.
        chosen = getattr(e.control, "value", None) or _V["host"]
        if chosen == _V["host"]:
            return
        _V["host"] = chosen
        _V.update({"file": None, "text": None, "editing": False,
                   "open": set()})
        rebuilder(page, build)()

    host_pick.on_select = pick_host

    paint_tree()
    paint_viewer()

    # The old blind composer, kept but folded away: writing a note straight
    # into a path is still the fastest way to jot one, and it is now next to
    # a tree that shows what is already there, so it can no longer clobber a
    # file you did not know about without you seeing it first.
    path_field = _field(p, "notes/spark-plugs.md", expand=True)
    new_body = _field(p, "note body...", multiline=True, min_lines=5, max_lines=14)
    new_note = card(ft.Column(spacing=8, controls=[
        title("Write a new note", 13),
        path_field, new_body,
        ft.Row([ft.FilledButton("Create", height=32,
                                on_click=lambda e: _run(page, create))]),
        sub(f"Saved over SSH into {_V['lib']} on {_V['host']}. "
            f"An existing path is overwritten.", 10)]), pad=10)
    new_note.visible = False

    def toggle_new(e):
        new_note.visible = not new_note.visible
        repaint()

    async def create():
        rel = (path_field.value or "").strip()
        if not rel:
            w.report(False, "path required")
            repaint()
            return
        w.busy()
        ok, msg = await asyncio.to_thread(
            M.vault_write, _V["host"], rel, new_body.value or "", _V["lib"])
        w.report(ok, msg)
        if ok:
            rebuilder(page, build)()
            return
        repaint()

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        card(ft.Column(spacing=8, controls=[
            ft.Row(spacing=6, wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                       *[_pill(p, lib, _V["lib"] == lib, switch_lib(lib))
                         for lib in M.VAULT_LIBS],
                       # No expand spacer here: this Row wraps, and a
                       # Wrap has no fixed line to push anything to the
                       # far end of - the child just takes unbounded
                       # width. Same shape as the bug that made EatZen's
                       # "What you ate" a full-screen grey slab.
                       host_pick,
                       ft.IconButton(ft.Icons.REFRESH, icon_size=17,
                                     icon_color=p["text2"],
                                     tooltip="Re-read the tree",
                                     on_click=lambda e: rebuilder(page, build)()),
                       ft.IconButton(ft.Icons.NOTE_ADD, icon_size=17,
                                     icon_color=p["text2"],
                                     tooltip="Write a new note",
                                     on_click=toggle_new)]),
            sub(f"{_V['lib']} on {_V['host']}", 10),
            tree_box,
        ]), pad=10),
        new_note,
        card(ft.Column(spacing=6, controls=[
            ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[name_line, edit_btn, save_btn, cancel_btn]),
            ft.Divider(height=1, color=p["border"]),
            viewer, editor,
        ]), pad=10),
        w.control,
    ])


def _markdown(text, p):
    """Markdown as Flet controls.

    The browser has `md2html`; Flet ships `ft.Markdown`, which handles the
    same headings, lists, code fences, tables and links. Wrapping it rather
    than hand-rolling a parser keeps the two clients rendering the same note
    the same way, and the raw text is shown if the control is ever missing
    from a build rather than showing nothing at all.
    """
    if not (text or "").strip():
        return [sub("(empty note)", 11)]
    md = getattr(ft, "Markdown", None)
    if md is None:                 # pragma: no cover - depends on the build
        return [ft.Text(text, size=11.5, color=p["text"], selectable=True)]
    return [md(value=text, selectable=True,
               extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
               code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
               on_tap_link=lambda e: page_launch(e))]


def page_launch(e):
    """Open a markdown link in the system browser, if the platform allows."""
    try:
        e.page.launch_url(e.data)
    except Exception:              # noqa: BLE001 - no launcher on this platform
        pass


# =========================================================================
# Journal
# =========================================================================
def _journal(page):
    """Entries: stats, category chips, search, sort, read, edit, delete.

    The old tab was a category dropdown, a title box and an inline editor. No
    search, no sort, no filter, no excerpt, no body on a new entry - so a new
    entry was created empty and the category's template, which the website
    prefills and dates for you, never appeared.
    """
    p = PAL()
    w = Writer(page, rebuilder(page, build))
    live = M.STATUS_LIVE()
    entries = M.journal_list()
    known = entries is not None
    entries = entries or []

    def repaint():
        try:
            page.update()
        except Exception:          # noqa: BLE001
            pass

    def cat_meta(key):
        return JN_CATS.get(key, (key or "—", ft.Icons.DESCRIPTION, "#8b949e"))

    # ------------------------------------------------------------- stats
    total, week, month, top = M.journal_stats(entries)
    top_label, top_icon, top_colour = cat_meta(top)

    def stat(value, label, colour=None, icon=None):
        return ft.Container(
            expand=True, padding=PAD(8, 6), border_radius=RAD(10), bgcolor=p["bg2"],
            border=BRD(p["border"], 1),
            content=ft.Column(spacing=1,
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[
                                  ft.Icon(icon, size=18, color=colour) if icon
                                  else ft.Text(str(value), size=18,
                                               weight=ft.FontWeight.W_700,
                                               color=colour or p["text"]),
                                  ft.Text(label, size=9, color=p["text2"])]))

    stats_row = ft.Row(spacing=6, controls=[
        stat(total, "Total"), stat(week, "This week"), stat(month, "This month"),
        stat("—", "Top category",
             colour=top_colour if top else p["text2"],
             icon=top_icon if top else None)])

    # ------------------------------------------------- filters and search
    counts = {}
    for e in entries:
        counts[e.get("cat", "")] = counts.get(e.get("cat", ""), 0) + 1

    def pick_cat(key):
        def handler(ev):
            _J["cat"] = key
            rebuilder(page, build)()
        return handler

    chips = [_pill(p, f"All · {len(entries)}", _J["cat"] == "all", pick_cat("all"))]
    for key, (label, _icon, colour) in JN_CATS.items():
        if key not in counts:
            continue
        chips.append(_pill(p, f"{label} · {counts[key]}", _J["cat"] == key,
                           pick_cat(key), colour))

    search = _field(p, "search titles and excerpts...", value=_J["q"], expand=True)

    def do_search(e):
        _J["q"] = (search.value or "").strip()
        rebuilder(page, build)()

    search.on_submit = do_search

    sort_pick = ft.Dropdown(
        value=_J["sort"], dense=True, filled=True, text_size=11, width=140,
        border_color=p["border"], color=p["text"],
        options=[ft.DropdownOption(key=k, text=label) for k, label in JN_SORTS])

    def pick_sort(e):
        # on_select, not on_change - Flet 0.86's Dropdown never fires the
        # latter, and a sort box that silently does nothing looks like the
        # sort is broken rather than the wiring.
        _J["sort"] = getattr(e.control, "value", None) or "new"
        rebuilder(page, build)()

    sort_pick.on_select = pick_sort

    # --------------------------------------------------------- compose
    compose = ft.Column(spacing=8, visible=_J["mode"] == "compose")
    cat_choice = {"key": _J["editing"] and _entry_cat(entries, _J["editing"])
                  or list(JN_CATS)[0]}
    # Seeded from the session and written back on every keystroke. Any write
    # anywhere on this page rebuilds the whole tab, and a compose form that
    # comes back empty has eaten what you were writing.
    title_field = _field(p, "entry title...", expand=True, value=_J["title"])
    body_field = _field(p, "write it here...", multiline=True, min_lines=10,
                        max_lines=24, value=_J["body"])
    title_field.on_change = lambda e: _J.__setitem__("title", e.control.value or "")
    body_field.on_change = lambda e: _J.__setitem__("body", e.control.value or "")
    cat_row = ft.Row(spacing=6, wrap=True)

    def paint_cat_row():
        """Pills when creating; one static chip when editing.

        The server names an entry's file after its category, so an existing
        entry cannot be moved from here. Showing the other five greyed but
        still pill-shaped is a row of controls that look tappable and are
        not, so editing shows the one it is in and nothing else.
        """
        if _J["editing"]:
            key = cat_choice["key"]
            label, icon, colour = cat_meta(key)
            cat_row.controls = [chip(label, colour, icon, small=True)]
            return

        def pick(key):
            def handler(e):
                cat_choice["key"] = key
                paint_cat_row()
                repaint()
            return handler

        cat_row.controls = [
            _pill(p, label, cat_choice["key"] == key, pick(key), colour)
            for key, (label, _i, colour) in JN_CATS.items()]

    paint_cat_row()

    async def open_compose(path=None):
        _J["mode"] = "compose"
        _J["editing"] = path
        compose.visible = True
        if path:
            title_field.value = _J["title"] = _entry_title(entries, path)
            cat_choice["key"] = _entry_cat(entries, path) or list(JN_CATS)[0]
            body_field.value = "loading..."
            paint_cat_row()
            repaint()
            text = await asyncio.to_thread(M.journal_read, path)
            body_field.value = _J["body"] = text if text is not None else ""
            if text is None:
                w.report(False, "could not read that entry")
        else:
            title_field.value = _J["title"] = ""
            body_field.value = "loading template..."
            paint_cat_row()
            repaint()
            body_field.value = _J["body"] = await asyncio.to_thread(
                M.journal_template, cat_choice["key"])
        repaint()

    def close_compose(e):
        _J.update({"mode": "list", "editing": None, "title": "", "body": ""})
        rebuilder(page, build)()

    async def save_entry(e):
        text = (title_field.value or "").strip()
        if len(text) < 3:
            # The website enforces the same floor. A one-character title makes
            # a file called `2026-09-21_a.md` that nobody can find again.
            w.report(False, "title needs at least 3 characters")
            repaint()
            return
        w.busy()
        path = _J["editing"]
        if not path:
            ok, res = await asyncio.to_thread(M.journal_new, cat_choice["key"], text)
            if not ok:
                w.report(False, res)
                repaint()
                return
            if res == M.QUEUED_MESSAGE:
                # The server names the file, so there is nothing to write the
                # body into yet. Saying so beats writing it to a guessed path.
                w.report(True, "entry queued - reopen it to add the body once "
                               "it has been created")
                _J.update({"mode": "list", "editing": None, "title": "",
                           "body": ""})
                rebuilder(page, build)()
                return
            path = res
        ok, msg = await asyncio.to_thread(M.journal_save, path, body_field.value or "")
        w.report(ok, msg)
        if ok:
            _J.update({"mode": "list", "editing": None, "title": "", "body": ""})
            rebuilder(page, build)()
            return
        repaint()

    compose.controls = [
        ft.Row([title("Edit entry" if _J["editing"] else "New entry", 14),
                ft.Container(expand=True),
                ft.TextButton("Cancel", on_click=close_compose)]),
        title_field,
        sub("Category" + (" - fixed; the server names the file after it"
                          if _J["editing"] else ""), 10),
        cat_row, body_field,
        ft.Row([ft.FilledButton("Save", height=34, on_click=save_entry)]),
    ]

    # ------------------------------------------------------------ reader
    reader = ft.Column(spacing=8, visible=_J["mode"] == "read")
    reader_body = ft.Column(spacing=4)
    reader_head = ft.Row(spacing=6)

    async def open_read(path):
        _J["mode"] = "read"
        _J["open"] = path
        reader.visible = True
        reader_body.controls = [sub("loading...", 11)]
        cat = _entry_cat(entries, path)
        label, icon, colour = cat_meta(cat)
        reader_head.controls = [
            ft.Icon(icon, size=15, color=colour),
            ft.Text(_entry_title(entries, path), size=14,
                    weight=ft.FontWeight.W_600, color=p["text"], expand=True),
            ft.TextButton("Edit", on_click=lambda e, path=path: _run(
                page, open_compose, path)),
            ft.TextButton("Close", on_click=close_read)]
        repaint()
        text = await asyncio.to_thread(M.journal_read, path)
        reader_body.controls = (_markdown(text, p) if text is not None
                                else [ft.Text("Could not read that entry.",
                                              size=12, color=p["pink"])])
        repaint()

    def close_read(e):
        _J["mode"] = "list"
        _J["open"] = None
        rebuilder(page, build)()

    reader.controls = [reader_head, ft.Divider(height=1, color=p["border"]),
                       reader_body]

    # -------------------------------------------------------------- list
    rows = M.journal_filter(entries, _J["cat"], _J["q"], _J["sort"])

    def entry_card(e):
        label, icon, colour = cat_meta(e.get("cat"))
        acts = [
            ft.TextButton("Read", on_click=lambda ev, path=e["path"]: _run(
                page, open_read, path))]
        if live:
            acts += [
                ft.TextButton("Edit", on_click=lambda ev, path=e["path"]: _run(
                    page, open_compose, path)),
                ft.TextButton("Delete", style=ft.ButtonStyle(color=p["pink"]),
                              on_click=confirm_delete(e))]
        return card(ft.Column(spacing=5, controls=[
            ft.Row(spacing=6, controls=[
                chip(label, colour, icon, small=True),
                ft.Container(expand=True),
                sub(e.get("date") or "", 10)]),
            ft.Text(e.get("title") or "(untitled)", size=13,
                    weight=ft.FontWeight.W_500, color=p["text"]),
            *([ft.Text(e["excerpt"], size=10, color=p["text3"], max_lines=2,
                       overflow=ft.TextOverflow.ELLIPSIS)] if e.get("excerpt")
              else []),
            ft.Row(spacing=2, controls=acts),
        ]), pad=10)

    def confirm_delete(entry):
        """Two taps, like the browser's confirm(). One tap deletes writing."""
        def handler(e):
            btn = e.control
            if getattr(btn, "data", None) != "armed":
                btn.data = "armed"
                btn.content = ft.Text("Really delete?", size=13,
                                      color=p["pink"], weight=ft.FontWeight.W_600)
                repaint()
                return
            _run(page, do_delete, entry["path"])
        return handler

    async def do_delete(path):
        w.busy()
        ok, msg = await asyncio.to_thread(M.journal_delete, path)
        w.report(ok, msg)
        if ok:
            _J["mode"] = "list"
            _J["open"] = None
            rebuilder(page, build)()
            return
        repaint()

    if not known:
        listing = [card(sub("The journal is unavailable - the server has never "
                            "answered /api/journal/tree on this device.", 11),
                        pad=12)]
    elif not entries:
        listing = [card(sub("No entries yet - hit New entry to start.", 11), pad=12)]
    elif not rows:
        listing = [card(sub("No entries match that filter.", 11), pad=12)]
    else:
        listing = [entry_card(e) for e in rows]

    header = ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        title("Journal", 15), ft.Container(expand=True),
                        ft.FilledButton("New entry", height=32,
                                        icon=ft.Icons.ADD,
                                        on_click=lambda e: _run(page, open_compose,
                                                                None))])

    listing_box = ft.Column(spacing=8, visible=_J["mode"] == "list", controls=[
        stats_row,
        ft.Row(spacing=6, wrap=True, controls=chips),
        ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
               controls=[
                   search,
                   ft.IconButton(ft.Icons.SEARCH, icon_size=17,
                                 icon_color=p["accent"], on_click=do_search),
                   sort_pick]),
        *listing,
    ])

    return ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, controls=[
        *([] if live else [offline_banner(p, "the journal", sample=False)]),
        header, compose, reader, listing_box, w.control,
    ])


def _entry(entries, path):
    for e in entries:
        if e.get("path") == path:
            return e
    return {}


def _entry_title(entries, path):
    return _entry(entries, path).get("title") or ""


def _entry_cat(entries, path):
    return _entry(entries, path).get("cat") or ""
