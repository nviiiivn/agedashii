"""FoodZen state mutations, ported 1:1 from the website's JS.

Pure functions over the state dict that `/api/foodzen/state` returns and
`/api/foodzen/save` accepts. No I/O here - callers do the transport - so all
of this is testable offline.

Why port rather than invent: the phone and the browser write to the *same*
JSON files on the server, one whole collection at a time. If the app shaped a
grocery item even slightly differently (missing `qty`, a different `cat`, a
lowercase name) the website would render it wrong or drop it, and the damage
would only show up later. Every shape and rule below is taken from
dashbored-web/app.js:

    list        app.js:6762   {id, name, date, items:[]}
    list item   app.js:6876   {name, qty, note, cat, done}
    need item   app.js:7009   {name, qty, note, cat, added}
    pantry item app.js:7037   {name, cat, updated}
    save        app.js:6751   POST {kind, data: state[kind]}  (whole collection)

Each mutator returns the set of collection names that changed, so a caller
saves only what it has to.
"""
import re
import time

from .foodzen_catalog import CATALOG

# Exactly the collections /api/foodzen/save accepts; anything else comes back
# "bad kind" and queueing it would poison the outbox with a write that can
# never land. Kept in step with the allowlist in dashbored-v3.py by hand -
# verified against the live server, where all ten return 200 and an invented
# kind returns 400.
KINDS = ("templates", "lists", "need", "pantry", "recipes", "purchases",
         "bpm", "eatzen", "nutrition_overrides", "custom_foods")

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]


def norm(name):
    """app.js fzNorm: lowercase, drop all but letters and spaces, collapse runs."""
    s = re.sub(r"[^a-z ]", "", str(name or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def title_case(name):
    """app.js fzTitleCase: capitalise the first letter of each word."""
    return re.sub(r"\b([a-z])(\w*)", lambda m: m.group(1).upper() + m.group(2),
                  str(name or ""))


def auto_cat(name):
    """app.js fzAutoCat, reproduced exactly - including its bug.

    Substring matching runs in *both* directions and the first hit in table
    order wins, so short names get captured by unrelated longer entries. The
    website really does return:

        shampoo -> Meat      (contains "ham")
        tea     -> Meat      (substring of "steak")
        bread   -> Frozen    (substring of "garlic bread")
        cornstarch -> Produce (contains "corn")

    An "improved" version here would be worse, not better: the phone and the
    browser write to the same file, and an item would land in a different
    aisle depending on which one you added it from. Parity first. Fixing the
    categoriser is a change to make in BOTH clients at once, not unilaterally
    here - see the note in tests/test_foodzen.py.
    """
    n = norm(name)
    if not n:
        return "Other"
    for key, cat in CATALOG.items():
        if n in key or key in n:
            return cat
    return "Pantry"


def new_list_name(state, today=None):
    """app.js fzListName + the dedupe loop in fzEnsureList."""
    t = time.localtime(today) if today else time.localtime()
    base = f"grocery-list-{MONTHS[t.tm_mon - 1]}-{t.tm_mday}"
    existing = {l.get("name") for l in (state.get("lists") or [])}
    name, n = base, 2
    while name in existing:
        name = f"{base}-{n}"
        n += 1
    return name


def blank_state():
    return {"templates": [], "lists": [], "need": [], "pantry": {"items": []},
            "recipes": [], "purchases": []}


def normalise_state(state):
    """Fill in anything the server left out, so mutators can assume shape."""
    s = dict(state or {})
    for k in ("templates", "lists", "need", "recipes", "purchases"):
        if not isinstance(s.get(k), list):
            s[k] = []
    pantry = s.get("pantry")
    if not isinstance(pantry, dict):
        pantry = {"items": pantry if isinstance(pantry, list) else []}
    if not isinstance(pantry.get("items"), list):
        pantry["items"] = []
    s["pantry"] = pantry
    return s


def ensure_list(state, list_id=None):
    """Return (list_obj, changed). Creates the first list if there is none.

    app.js fzEnsureList prepends the new list and saves immediately.
    """
    lists = state["lists"]
    if list_id:
        for l in lists:
            if l.get("id") == list_id:
                return l, set()
    if lists:
        return lists[0], set()
    new = {"id": f"fz-l-{int(time.time() * 1000)}",
           "name": new_list_name(state),
           "date": time.strftime("%Y-%m-%d"),
           "items": []}
    lists.insert(0, new)
    return new, {"lists"}


def add_item(state, name, list_id=None):
    """Add to the grocery list, or bump qty if already there (app.js fzBrowseAdd)."""
    if not norm(name):
        return set()
    lst, changed = ensure_list(state, list_id)
    target = norm(name)
    for it in lst["items"]:
        if norm(it.get("name")) == target:
            it["qty"] = (it.get("qty") or 1) + 1
            return changed | {"lists"}
    lst["items"].append({"name": title_case(name), "qty": 1, "note": "",
                         "cat": auto_cat(name), "done": False})
    return changed | {"lists"}


def set_done(state, name, done, list_id=None):
    lst, changed = ensure_list(state, list_id)
    target = norm(name)
    for it in lst["items"]:
        if norm(it.get("name")) == target:
            it["done"] = bool(done)
            return changed | {"lists"}
    return changed


def remove_item(state, name, list_id=None):
    lst, changed = ensure_list(state, list_id)
    target = norm(name)
    before = len(lst["items"])
    lst["items"] = [i for i in lst["items"] if norm(i.get("name")) != target]
    return changed | ({"lists"} if len(lst["items"]) != before else set())


def add_need(state, name):
    """app.js:7009 - needs carry `added` rather than `done`."""
    if not norm(name):
        return set()
    target = norm(name)
    for n in state["need"]:
        if norm(n.get("name")) == target:
            return set()
    state["need"].append({"name": title_case(name), "qty": 1, "note": "",
                          "cat": auto_cat(name), "added": int(time.time() * 1000)})
    return {"need"}


def remove_need(state, name):
    target = norm(name)
    before = len(state["need"])
    state["need"] = [n for n in state["need"] if norm(n.get("name")) != target]
    return {"need"} if len(state["need"]) != before else set()


def add_pantry(state, name):
    """app.js:7037 - pantry items carry `updated`, no qty."""
    if not norm(name):
        return set()
    target = norm(name)
    for p in state["pantry"]["items"]:
        if norm(p.get("name")) == target:
            p["updated"] = int(time.time() * 1000)
            return {"pantry"}
    state["pantry"]["items"].append({"name": title_case(name), "cat": auto_cat(name),
                                     "updated": int(time.time() * 1000)})
    return {"pantry"}


def remove_pantry(state, name):
    target = norm(name)
    items = state["pantry"]["items"]
    before = len(items)
    state["pantry"]["items"] = [p for p in items if norm(p.get("name")) != target]
    return {"pantry"} if len(state["pantry"]["items"]) != before else set()


def need_to_list(state, list_id=None):
    """Move everything on the needs list onto the grocery list."""
    changed = set()
    for n in list(state["need"]):
        changed |= add_item(state, n.get("name"), list_id)
    if state["need"]:
        state["need"] = []
        changed.add("need")
    return changed


# ---- templates, recipes, purchases ---------------------------------------
# The five collections the app never touched. Shapes copied from the website
# so the two stay readable to each other - ids are "fz-<letter>-<epoch ms>"
# exactly as app.js writes them (fzScrapeSave app.js:7712, the purchase at
# app.js:7791, the template at app.js:7438).

def _new_id(prefix):
    return f"fz-{prefix}-{int(time.time() * 1000)}"


def template_save(state, name=None):
    """Save the current list's items as a reusable template."""
    lst, _ = ensure_list(state)      # returns (list_obj, changed)
    items = lst.get("items") or []
    if not items:
        raise ValueError("the list is empty - nothing to save as a template")
    state.setdefault("templates", [])
    state["templates"].insert(0, {
        "id": _new_id("t"),
        "name": name or (lst.get("name") or "template"),
        "items": [{"name": i.get("name", ""), "qty": i.get("qty", 1),
                   "cat": i.get("cat", "")} for i in items if i.get("name")],
    })
    return {"templates"}


def template_apply(state, template_id, list_id=None):
    """Add every item of a template to the current list."""
    tpl = next((t for t in (state.get("templates") or [])
                if t.get("id") == template_id), None)
    if tpl is None:
        raise ValueError("that template is gone")
    changed = set()
    for it in (tpl.get("items") or []):
        nm = it.get("name") if isinstance(it, dict) else str(it)
        if nm:
            changed |= add_item(state, nm, list_id)
    return changed


def recipe_save(state, recipe):
    """Put a recipe into the vault, in the website's shape."""
    ings = []
    for i in (recipe.get("ingredients") or []):
        if isinstance(i, dict):
            ings.append({"qty": str(i.get("qty") or ""), "name": str(i.get("name") or "")})
        else:
            ings.append({"qty": "", "name": str(i)})
    state.setdefault("recipes", [])
    state["recipes"].insert(0, {
        "id": _new_id("r"),
        "title": str(recipe.get("title") or "Untitled"),
        "image": str(recipe.get("image") or ""),
        "servings": recipe.get("servings"),
        # The website writes time_min, not time. Using a different key here
        # would make a recipe saved from the app show no cooking time there.
        "time_min": recipe.get("time_min", recipe.get("time")),
        "tags": [str(t) for t in (recipe.get("tags") or [])],
        "ingredients": ings,
        "instructions": [str(x) for x in (recipe.get("instructions") or [])],
        "url": str(recipe.get("url") or ""),
        "added": int(time.time() * 1000),
    })
    return {"recipes"}


def recipe_to_list(state, recipe_id, list_id=None):
    """Add a saved recipe's ingredients to the grocery list."""
    rec = next((r for r in (state.get("recipes") or [])
                if r.get("id") == recipe_id), None)
    if rec is None:
        raise ValueError("that recipe is gone")
    changed = set()
    for i in (rec.get("ingredients") or []):
        nm = i.get("name") if isinstance(i, dict) else str(i)
        if nm:
            changed |= add_item(state, nm, list_id)
    return changed


def purchase_save(state, purchase):
    """Record a parsed receipt - what Spending and Freq radar are built from."""
    items = []
    total = 0.0
    for it in (purchase.get("items") or []):
        try:
            price = round(float(it.get("price") or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
        try:
            qty = int(float(it.get("qty") or 1))
        except (TypeError, ValueError):
            qty = 1
        items.append({"name": str(it.get("name") or ""), "qty": qty,
                      "price": price, "cat": str(it.get("cat") or auto_cat(it.get("name")))})
        total += price * qty
    if not items:
        raise ValueError("no lines to save")
    stated = purchase.get("total")
    try:
        total = round(float(stated), 2) if stated else round(total, 2)
    except (TypeError, ValueError):
        total = round(total, 2)
    state.setdefault("purchases", [])
    state["purchases"].insert(0, {
        "id": _new_id("p"),
        "date": str(purchase.get("date") or time.strftime("%Y-%m-%d")),
        "store": str(purchase.get("store") or ""),
        "items": items, "total": total, "source": "import",
    })
    return {"purchases"}
