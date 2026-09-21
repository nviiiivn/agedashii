"""Extras: the Gitea repos, the AI chat, Future Finds, and Obligations.

Projects and AI both used to be sketches of the website's real thing.
Projects listed 21 repo *names* and put an "open in new" icon beside each
one that opened nothing; the website lets you walk a repo's file tree, read
any file in it, and see its last commits. AI hardcoded one provider and a
chip reading "model: auto" while the code underneath had already resolved a
real model id; the website offers three providers, their live model lists,
and a machine picker for the local one.

Two rules that shaped this:

* Nothing here is ever invented. There is no mock repo list and no mock
  chat; where the server has nothing, the page says what is missing and
  names the endpoint that would have supplied it.
* The chat is live-only and says so. A question queued for later is not a
  question, and the reply would have nowhere to land - so with no signal it
  refuses and gives the reason, rather than queueing or quietly pretending.

Page state - which repo you opened, which file you were reading, the chat
history, the provider you picked - lives at module level. Every write on
any page repaints the whole app, and a chat that empties itself when you
save a gem somewhere else is not a chat.
"""
import flet as ft

from ..ui import PAL, PAD, BRD, card, chip, title, sub, dot
from .. import api as M
from .. import panels
from .lab import _tabs, _show, _dismiss, _error_card


def build(page):
    # XNAV moved to Trips, where the rest of the ride pipeline lives; the
    # copy here was a mock that could never record anything.
    #
    # Future Finds lives here rather than as a fourth tab on Wat2Do, where
    # the website keeps it, for one reason: a new top-level page has to be
    # declared in prefs.PAGE_KEYS or the layout editor cannot show it, and a
    # tab inside Wat2Do would sit under that page's own date/checklist state.
    # Extras is where the app already puts the things that are their own
    # thing. `key` makes the tab bar remember which one you were on across
    # the repaint every write triggers - without it, saving a gem bounced
    # you back to Projects.
    # Future lives on Wat2Do, which is where the website keeps it. It was
    # mounted here as well for a while; two mounts of one module is not
    # mirroring, and the drawer would have offered the same page twice.
    return _tabs(page, [("Projects", _projects(page)), ("AI", _ai(page)),
                        ("Obligations", _obl())], key="extras")


# ------------------------------------------------------------------ projects

# Which repo is open, which file is being read, and what the search box says.
# Module level so a repaint does not throw you back to the repo list.
_PROJ = {"owner": None, "repo": None, "tree": None, "info": None,
         "commits": None, "file": None, "text": None, "error": None,
         # A failed *file* read keeps its own key. Sharing "error" with the
         # tree meant one unreadable file made the next repaint declare the
         # whole repository unopenable.
         "file_error": None, "query": ""}


def _fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n:.0f} B"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


def _close_repo():
    _PROJ.update({"owner": None, "repo": None, "tree": None, "info": None,
                  "commits": None, "file": None, "text": None, "error": None,
                  "file_error": None})


def _tree_groups(tree):
    """Blobs only, grouped by directory, directories in order.

    The website counted `tree.length` for its "Files (n)" heading, which
    includes the directory entries it then declines to render - so "Files
    (8)" sat above seven files. Only blobs are counted here.
    """
    blobs = [t for t in (tree or [])
             if isinstance(t, dict) and t.get("type") != "tree" and t.get("path")]
    # By (directory, filename), so the repo root comes first and every
    # directory's files stay together. Sorting on the whole path instead -
    # what the website does - interleaves README.md between Grimoire/ and
    # System/, under whichever heading happened to be open.
    blobs.sort(key=lambda t: (str(t["path"]).rpartition("/")[0],
                              str(t["path"]).rpartition("/")[2]))
    groups, order = {}, []
    for t in blobs:
        path = str(t["path"])
        directory = path.rpartition("/")[0]
        if directory not in groups:
            groups[directory] = []
            order.append(directory)
        groups[directory].append(t)
    return [(d, groups[d]) for d in order], len(blobs)


def _commit_line(c):
    """(sha, subject, date, author) from one Gitea commit row."""
    commit = (c or {}).get("commit") or {}
    author = commit.get("author") or {}
    message = str(commit.get("message") or "").split("\n")[0] or "(no message)"
    return (str((c or {}).get("sha") or "")[:7], message,
            str(author.get("date") or "")[:10], str(author.get("name") or ""))


def _projects(page):
    p = PAL()
    if _PROJ["owner"] and _PROJ["repo"]:
        return _repo_detail(page)

    repos, err = M.projects_list()
    if repos is None:
        return ft.Column(spacing=10, controls=[
            _error_card("Cannot list your repositories",
                        err or "no detail", ft.Icons.CLOUD_OFF)])
    if not repos:
        return ft.Column(spacing=10, controls=[card(ft.Column(spacing=6, controls=[
            title("No repositories", 14),
            sub("Gitea answered, and it has nothing to list.", 11)]), pad=14)])

    def rerender():
        from ..ui import S
        fn = getattr(S, "rerender", None)
        if callable(fn):
            fn()
        else:
            page.update()

    def open_repo(owner, name):
        async def handler(e):
            _close_repo()
            _PROJ["owner"], _PROJ["repo"] = owner, name
            data, terr = M.project_tree(owner, name)
            if terr:
                _PROJ["error"] = terr
            else:
                _PROJ["tree"] = (data or {}).get("tree") or []
                _PROJ["info"] = (data or {}).get("info") or {}
                _PROJ["commits"] = (data or {}).get("commits") or []
            rerender()
        return handler

    query = (_PROJ["query"] or "").lower()
    shown = [r for r in repos if not query
             or query in str(r.get("full_name") or r.get("name") or "").lower()
             or query in str(r.get("description") or "").lower()
             or query in str(r.get("language") or "").lower()]

    def repo_card(r):
        owner, name = M.repo_owner_name(r)
        meta = []
        if r.get("language"):
            meta.append(chip(str(r["language"]), p["cyan"], small=True))
        for key, glyph, colour in (("stars_count", "★", p["yellow"]),
                                   ("forks_count", "⑂", p["text2"]),
                                   ("open_issues_count", "!", p["orange"])):
            if r.get(key):
                meta.append(chip(f"{glyph} {r[key]}", colour, small=True))
        for topic in (r.get("topics") or [])[:2]:
            meta.append(chip(f"#{topic}", p["purple"], small=True))
        if r.get("private"):
            meta.append(chip("private", p["red"], small=True))
        if r.get("empty"):
            meta.append(chip("empty", p["text2"], small=True))

        return ft.Container(
            ink=True, border_radius=12, on_click=open_repo(owner, name),
            content=card(ft.Column(spacing=6, controls=[
                ft.Row(spacing=8, controls=[
                    ft.Icon(ft.Icons.FOLDER_COPY, color=p["orange"], size=18),
                    ft.Column(spacing=1, expand=True, controls=[
                        ft.Text(str(r.get("full_name") or name), size=13,
                                weight=ft.FontWeight.W_500, color=p["text"],
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        sub(str(r.get("description") or "no description"), 10)]),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, color=p["text2"]),
                ]),
                ft.Row(spacing=5, wrap=True, run_spacing=5,
                       controls=meta) if meta else ft.Container(height=0),
            ]), pad=10))

    search = ft.TextField(hint_text="filter by name, description or language",
                          value=_PROJ["query"], dense=True, filled=True,
                          text_size=12, expand=True, border_color=p["border"],
                          focused_border_color=p["accent"], color=p["text"])
    listing = ft.Column(spacing=8, controls=[repo_card(r) for r in shown])
    counter = ft.Text("", size=11, color=p["text2"])

    def recount():
        counter.value = (f"{len(shown)} of {len(repos)} repositories"
                         if query else f"{len(repos)} repositories")

    def do_filter(e=None):
        _PROJ["query"] = (search.value or "").strip()
        q = _PROJ["query"].lower()
        rows = [r for r in repos if not q
                or q in str(r.get("full_name") or r.get("name") or "").lower()
                or q in str(r.get("description") or "").lower()
                or q in str(r.get("language") or "").lower()]
        listing.controls = [repo_card(r) for r in rows] or [
            card(sub(f"Nothing matches “{_PROJ['query']}”.", 11), pad=12)]
        counter.value = (f"{len(rows)} of {len(repos)} repositories"
                         if q else f"{len(repos)} repositories")
        page.update()

    search.on_change = do_filter
    search.on_submit = do_filter
    recount()

    def search_card():
        return card(ft.Column(spacing=6, controls=[
            ft.Row([title("Projects", 14), ft.Container(expand=True), counter]),
            search,
            sub("Everything Gitea serves on the Pi. Tap a repository to walk "
                "its files and read its last commits.", 10),
        ]))

    return ft.Column(spacing=10, expand=True, controls=[
        ft.Container(expand=True, content=panels.layout("extras.projects", {
            "search": search_card, "repos": lambda: listing}))])


def _repo_detail(page):
    p = PAL()
    owner, repo = _PROJ["owner"], _PROJ["repo"]
    info = _PROJ["info"] or {}

    def rerender():
        from ..ui import S
        fn = getattr(S, "rerender", None)
        if callable(fn):
            fn()
        else:
            page.update()

    async def back(e):
        _close_repo()
        rerender()

    head = card(ft.Column(spacing=6, controls=[
        ft.Row(spacing=6, controls=[
            ft.IconButton(ft.Icons.ARROW_BACK, icon_size=18, icon_color=p["text2"],
                          tooltip="back to the repository list", on_click=back),
            ft.Text(str(info.get("full_name") or f"{owner}/{repo}"), size=14,
                    weight=ft.FontWeight.W_600, color=p["text"], expand=True,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            chip(str(info.get("default_branch") or "?"), p["accent"], small=True),
        ]),
        sub(str(info.get("description") or "no description"), 11),
        ft.Row(spacing=5, wrap=True, run_spacing=5, controls=[
            c for c in (
                chip(str(info["language"]), p["cyan"], small=True)
                if info.get("language") else None,
                # Gitea reports repo size in KiB, not bytes.
                chip(_fmt_bytes(float(info["size"]) * 1024), p["text2"], small=True)
                if info.get("size") else None,
                chip(str(info["updated_at"])[:10], p["text2"], small=True)
                if info.get("updated_at") else None,
            ) if c is not None]),
    ]))

    if _PROJ["error"]:
        return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=[
            head, _error_card(f"Could not open {owner}/{repo}", _PROJ["error"])])

    groups, count = _tree_groups(_PROJ["tree"])
    viewer = ft.Column(spacing=6)

    def paint_file():
        viewer.controls.clear()
        if _PROJ["file"] is None:
            viewer.controls.append(sub("Pick a file to read it here.", 11))
        elif _PROJ["text"] is None:
            viewer.controls.append(
                ft.Text(_PROJ["file_error"] or "the server sent nothing", size=11,
                        color=p["red"], selectable=True))
        else:
            text = _PROJ["text"]
            viewer.controls.append(ft.Row([
                ft.Text(_PROJ["file"], size=11, color=p["text"], expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                sub(_fmt_bytes(len(text.encode("utf-8", "replace"))), 10)]))
            if len(text) > 200000:
                viewer.controls.append(
                    sub(f"{_fmt_bytes(len(text))} — too large to show here.", 11))
            elif not text.strip():
                viewer.controls.append(sub("the file is empty", 11))
            else:
                viewer.controls.append(ft.Container(
                    padding=PAD(8, 10), border_radius=8, bgcolor=p["bg2"],
                    border=BRD(p["border"], 1),
                    content=ft.Text(text[:200000], size=10, color=p["text"],
                                    selectable=True, font_family="monospace")))

    def open_file(path):
        async def handler(e):
            text, ferr = M.project_file(owner, repo, path)
            _PROJ["file"], _PROJ["text"] = path, text
            _PROJ["file_error"] = ferr
            paint_file()
            page.update()
        return handler

    tree_rows = []
    for directory, blobs in groups:
        if directory:
            tree_rows.append(sub(directory + "/", 10))
        for t in blobs:
            path = str(t["path"])
            tree_rows.append(ft.Row(spacing=6, controls=[
                ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=13,
                        color=p["accent"] if path == _PROJ["file"] else p["text2"]),
                ft.TextButton(content=path.rpartition("/")[2], height=26,
                              style=ft.ButtonStyle(
                                  color=p["accent"] if path == _PROJ["file"]
                                  else p["text"]),
                              on_click=open_file(path)),
                ft.Container(expand=True),
                sub(_fmt_bytes(t.get("size")), 10),
            ]))
    if not tree_rows:
        tree_rows = [sub("Gitea listed no files on the default branch.", 11)]

    paint_file()
    commits = [_commit_line(c) for c in (_PROJ["commits"] or [])][:8]

    body = [head, card(ft.Column(spacing=4, controls=[
        title(f"Files ({count})", 13), *tree_rows]))]
    body.append(card(ft.Column(spacing=6, controls=[
        title("File", 13), viewer])))
    if commits:
        body.append(card(ft.Column(spacing=4, controls=[
            title(f"Recent commits ({len(_PROJ['commits'] or [])})", 13),
            *[ft.Row(spacing=8, controls=[
                ft.Text(sha, size=10, color=p["accent"], width=60,
                        font_family="monospace"),
                ft.Text(msg, size=11, color=p["text"], expand=True,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                sub(f"{date}{' · ' + who if who else ''}", 10),
            ]) for sha, msg, date, who in commits]])))
    return ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=body)


# ------------------------------------------------------------------------ ai

# Kept at module level so a repaint elsewhere in the app does not wipe the
# conversation, and so the provider you picked is still picked afterwards.
_AI = {"provider": "openrouter", "model": None, "host": "blavksaba",
       "messages": [], "note": ""}


def _ai(page):
    p = PAL()

    def rerender():
        from ..ui import S
        fn = getattr(S, "rerender", None)
        if callable(fn):
            fn()
        else:
            page.update()

    provider = _AI["provider"]
    dash = M.dashboard() if provider == "ollama" else None

    # --- what models can this provider actually run right now ---------------
    if provider == "ollama":
        # Only machines the server actually probed for Ollama. lil-faegola and
        # sp3 have no ollama widget at all, so offering them as somewhere to
        # run a model would be offering something that cannot happen.
        hosts = [k for k in sorted(dash or {})
                 if isinstance((dash or {}).get(k), dict)
                 and "ollama" in (((dash or {})[k].get("widgets") or {}))]
        if _AI["host"] not in hosts and hosts:
            _AI["host"] = hosts[0]
        models = M.ollama_models(dash, _AI["host"]) if hosts else []
        model_err = None if models else (
            "the dashbored server is not answering" if dash is None else
            f"{_AI['host']} has no Ollama models installed, or is unreachable")
    else:
        hosts = []
        models, model_err = M.ai_models(provider)
        models = models or []
    if _AI["model"] not in models:
        _AI["model"] = models[0] if models else None

    def pick_provider(e):
        _AI["provider"] = e.control.value or "openrouter"
        _AI["model"] = None
        rerender()

    def pick_model(e):
        _AI["model"] = e.control.value

    def pick_host(e):
        _AI["host"] = e.control.value
        _AI["model"] = None
        rerender()

    # on_select throughout: Flet 0.86's Dropdown has no on_change, and setting
    # one is accepted silently and never fires.
    provider_dd = ft.Dropdown(
        value=provider, dense=True, filled=True, text_size=12, width=170,
        border_color=p["border"], color=p["text"], on_select=pick_provider,
        options=[ft.dropdown.Option(key=k, text=label)
                 for k, label in M.AI_PROVIDERS])
    model_dd = ft.Dropdown(
        value=_AI["model"], dense=True, filled=True, text_size=12, expand=True,
        border_color=p["border"], color=p["text"], on_select=pick_model,
        options=[ft.dropdown.Option(key=m, text=_model_label(m)) for m in models])
    controls = [provider_dd, model_dd]
    if provider == "ollama" and hosts:
        controls.insert(1, ft.Dropdown(
            value=_AI["host"], dense=True, filled=True, text_size=12, width=140,
            border_color=p["border"], color=p["text"], on_select=pick_host,
            options=[ft.dropdown.Option(key=k, text=(dash[k].get("display") or k))
                     for k in hosts]))

    note = ft.Text(_AI["note"], size=11, color=p["text2"], selectable=True)
    stream = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    def bubble(m):
        mine = m["role"] == "user"
        failed = m.get("failed")
        colour = p["accent"] if mine else (p["surface"] if not failed
                                           else ft.Colors.with_opacity(0.12, p["red"]))
        return ft.Row(
            alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START,
            controls=[ft.Container(
                width=300, padding=PAD(10, 12), border_radius=14, bgcolor=colour,
                border=BRD(p["red"] if failed else p["border"], 0 if mine else 1),
                content=ft.Text(m["text"], size=13, selectable=True,
                                color="#ffffff" if mine else
                                (p["red"] if failed else p["text"])))])

    def paint(extra=None):
        if not _AI["messages"]:
            stream.controls = [card(ft.Column(spacing=4, controls=[
                title("Agedashii AI", 13),
                sub("Ask about the homelab. The machines, their containers and "
                    "their services are described to the model with every "
                    "message.", 11)]), pad=12)]
        else:
            stream.controls = [bubble(m) for m in _AI["messages"]]
        if extra is not None:
            stream.controls.append(extra)

    entry = ft.TextField(hint_text="Message…", border_radius=12, bgcolor=p["bg2"],
                         border_color=p["border"], focused_border_color=p["accent"],
                         color=p["text"], dense=True, expand=True, multiline=True,
                         min_lines=1, max_lines=4)

    async def send(e):
        text = (entry.value or "").strip()
        if not text:
            return
        if not _AI["model"]:
            _AI["note"] = "pick a model first — " + (model_err or "none are listed")
            note.value = _AI["note"]
            note.color = p["pink"]
            page.update()
            return
        entry.value = ""
        _AI["messages"].append({"role": "user", "text": text})
        _AI["note"] = f"asking {_model_label(_AI['model'])}…"
        note.value, note.color = _AI["note"], p["text2"]
        paint(ft.Row(controls=[ft.Text("…", size=13, color=p["text2"])]))
        page.update()

        history = [{"role": "user" if m["role"] == "user" else "assistant",
                    "content": m["text"]}
                   for m in _AI["messages"][-10:] if not m.get("failed")]
        reply, err = M.ai_say(_AI["provider"], _AI["model"], history,
                              host=_AI["host"] if _AI["provider"] == "ollama" else None)
        if err:
            # The server answers an unreachable Ollama with HTTP 200 and
            # {"error": "Ollama unreachable: timed out"}. The old code turned
            # every one of those into "(offline — is the Pi's dashbored up?)",
            # which is a guess; this prints what actually came back.
            _AI["messages"].append({"role": "ai", "text": err, "failed": True})
            _AI["note"] = "the provider refused — its reason is in the thread"
            note.color = p["pink"]
        else:
            _AI["messages"].append({"role": "ai", "text": reply})
            _AI["note"] = ""
            note.color = p["text2"]
        note.value = _AI["note"]
        paint()
        page.update()

    entry.on_submit = send

    async def clear(e):
        _AI["messages"].clear()
        _AI["note"] = ""
        note.value = ""
        paint()
        page.update()

    async def reload(e):
        rerender()

    paint()
    header = ft.Row(spacing=6, wrap=True, run_spacing=6, controls=controls + [
        ft.IconButton(ft.Icons.REFRESH, icon_size=17, icon_color=p["text2"],
                      tooltip="re-read the model list", on_click=reload),
        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=17, icon_color=p["red"],
                      tooltip="clear this conversation", on_click=clear),
    ])
    warn = []
    if model_err:
        warn.append(sub(model_err, 10))
    if not M.STATUS_LIVE():
        warn.append(sub("No connection to the server. Chat cannot be queued for "
                        "later — an answer that arrives tomorrow is not an "
                        "answer — so it will refuse until the server is back.", 10))

    return ft.Column(spacing=8, expand=True, controls=[
        header, *warn,
        ft.Container(expand=True, content=stream),
        note,
        ft.Row([entry, ft.IconButton(ft.Icons.SEND, icon_color=p["accent"],
                                     on_click=send)]),
    ])


def _model_label(model_id):
    """"nvidia/nemotron-3-ultra-550b-a55b:free" -> "nemotron-3-ultra-550b-a55b"."""
    name = str(model_id or "")
    if name.endswith(":free"):
        name = name[:-5]
    name = name.rpartition("/")[2] or name
    if name.endswith("-free"):
        name = name[:-5]
    return name or str(model_id or "")


# ----------------------------------------------------------------- obligations

def _obl():
    p = PAL()

    def row(o):
        return card(ft.Row(spacing=10, controls=[
            ft.Icon(ft.Icons.ERROR_OUTLINE if o["urg"] == 'high'
                    else ft.Icons.CHECK_CIRCLE_OUTLINE,
                    color=p["red"] if o["urg"] == 'high' else p["text2"], size=18),
            ft.Column(spacing=1, expand=True, controls=[
                ft.Text(o["t"], size=13, color=p["text"]),
                sub(o["due"], 10)]),
        ]), pad=10)
    items = M.OBLIGATIONS if isinstance(M.OBLIGATIONS, list) else []
    if not items:
        # There is no obligations store on the server - the website's own
        # Obligations view says "coming soon". This used to show invented
        # ones instead: "Renew motorcycle insurance, Sep 12" and a DMV
        # appointment. Those are deadlines you might act on, and they were
        # never real.
        return ft.Column(spacing=8, controls=[card(ft.Column(spacing=6, controls=[
            title("Nothing tracked yet", 14),
            sub("There is no obligations store on the server yet, so this "
                "shows nothing rather than inventing deadlines. The website's "
                "own Obligations view says the same: email aggregation with "
                "spam filtering, coming soon.", 11)]),
            pad=14)])
    return ft.Column(spacing=8, controls=[row(o) for o in items])
