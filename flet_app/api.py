"""NVII-DASH API client — drop-in replacement for `mock`.

Pages do `from .. import api as M`. Unknown attribute lookups fetch real data
from the dashbored server and reshape it to mock.py structures; on connection
failure they fall back to mock data. Real-but-empty (e.g. foodzen) stays empty.
"""
import json
import time
import urllib.parse
import urllib.request

from . import config, mock
from . import foodzen as _fz

TTL = 30
_cache = {}

WMO = {0: "clear", 1: "partly cloudy", 2: "partly cloudy", 3: "overcast",
       45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
       61: "rain", 63: "rain", 65: "rain", 71: "snow", 73: "snow", 75: "snow",
       80: "showers", 81: "showers", 82: "showers", 95: "storm", 96: "storm", 99: "storm"}


def _get(path, timeout=2.5):
    """GET a JSON path from the configured server, or None.

    The server address is read from config on every call rather than captured
    at import, so changing it in Settings takes effect immediately. Redirects
    are not followed - a 302 means an auth gateway intercepted us, and
    following it would hand back an HTML login page that fails JSON parsing
    and looks identical to the server being down.
    """
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        with config._opener.open(config.SERVER_URL + path, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        _cache[path] = (time.time(), data)
        config._remember(config.SERVER_URL, True, "connected")
        return data
    except Exception as e:
        config._remember(config.SERVER_URL, False, config._describe(e))
        return None


def _post(path, payload, timeout=6):
    """POST JSON to the configured server, or None. See _get on redirects."""
    try:
        req = urllib.request.Request(
            config.SERVER_URL + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with config._opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        config._remember(config.SERVER_URL, False, config._describe(e))
        return None


def _wx(code):
    try:
        return WMO.get(int(code or 0), "—")
    except (TypeError, ValueError):
        return "—"


def machines():
    d = _get("/api/dashboard")
    if d is None:
        return None
    out = []
    for mid, m in d.items():
        w = (m.get("widgets") or {}).get("system") or {}
        online = bool(w)
        ram = "—"
        try:
            if w.get("mem_total"):
                ram = f"{int(w['mem_used']) // 1024}/{int(w['mem_total']) // 1024} GB"
        except (KeyError, ValueError):
            pass
        osname = (w.get("greeting") or "").replace("OS:", "").split("·")[0].strip()[:28]
        out.append({"id": mid, "display": m.get("display", mid), "color": m.get("color", "#58a6ff"),
                    "online": online, "os": osname or "—", "uptime": w.get("uptime", "—"),
                    "cpu": w.get("cpu", "—"), "ram": ram, "temp": w.get("temp", "—"),
                    "docker": "", "host": ""})
    return out


def weather():
    d = _get("/api/weather")
    if d is None:
        return None
    cur = d.get("current") or {}
    daily = d.get("daily") or {}
    times = daily.get("time") or []
    mx = daily.get("temperature_2m_max") or []
    mn = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    pp = (d.get("hourly") or {}).get("precipitation_probability") or []
    days = []
    for i in range(1, min(5, len(times))):
        try:
            wd = time.strftime("%a", time.strptime(times[i], "%Y-%m-%d"))
        except (ValueError, IndexError):
            wd = ""
        hi = round(mx[i]) if i < len(mx) else "—"
        lo = round(mn[i]) if i < len(mn) else "—"
        days.append({"d": wd, "t": f"{hi}°/{lo}°", "desc": _wx(codes[i] if i < len(codes) else 0)})
    return {"now": {"t": round(cur.get("temperature_2m") or 0), "desc": _wx(cur.get("weather_code")),
                    "h": f"{round(mx[0])}°" if mx else "—", "l": f"{round(mn[0])}°" if mn else "—",
                    "wind": round(cur.get("wind_speed_10m") or 0),
                    "rain": pp[0] if pp else 0, "city": "Oakland"}, "days": days}


def feeds():
    d = _get("/api/feeds")
    if d is None:
        return None
    out = []
    for i in (d.get("items") or [])[:20]:
        meta = (i.get("date") or "") + (f" · {i['location']}" if i.get("location") else "")
        out.append({"src": i.get("source", "?"), "tag": i.get("type", ""),
                    "title": i.get("title", ""), "meta": meta})
    return out


def wat2do_state(date=None):
    if date is None:
        date = time.strftime("%Y-%m-%d")
    d = _get(f"/api/wat2do/state?date={date}")
    if d is None:
        return None
    checks = d.get("checks") or {}
    items = []
    for s in (d.get("items") or []):
        items.append({"t": (s[2:] if s.startswith("> ") else s).strip() or s,
                      "sub": s.startswith(">"), "done": bool(checks.get(s)), "_key": s})
    return {"date": d.get("date", date),
            "pain": d.get("pain") if d.get("pain") is not None else 0,
            "spoons": d.get("spoons") if d.get("spoons") is not None else 3,
            "items": items, "wx_call": None}


def wat2do_save(state):
    checks = {}
    for it in state.get("items", []):
        checks[it.get("_key", it.get("t", ""))] = bool(it.get("done"))
    return _post("/api/wat2do/save", {"date": state.get("date"), "checks": checks,
                                      "pain": state.get("pain"), "spoons": state.get("spoons")})


def garage_bikes():
    t = _get("/api/garage/tree")
    if t is None:
        return None
    out = []
    for b in (t.get("bikes") or []):
        bid = b.get("name", "")
        st = _get(f"/api/garage/stats?bike={bid}") or {}
        out.append({"id": bid, "name": b.get("label", bid), "year": "", "make": "",
                    "mi_since_svc": st.get("miles_since_service") or 0,
                    "total_mi": st.get("total_miles") or 0,
                    "color": "#3fb950", "status": "running"})
    return out


def trips():
    d = _get("/api/trips")
    if not isinstance(d, list):
        return None
    return [{"id": t.get("id", ""), "name": t.get("name", t.get("id", "—")),
             "mi": t.get("miles", ""), "date": t.get("date", "")} for t in d]


def rides_list():
    d = _get("/api/rides")
    if d is None:
        return None
    out = []
    for r in (d.get("rides") or []):
        out.append({"name": (r.get("filename") or "").replace(".gpx", ""),
                    "pts": "", "mi": "", "top": ""})
    return out


def projects():
    d = _get("/api/projects")
    if not isinstance(d, list):
        return None
    out = []
    for r in d:
        name = r.get("name") or (r.get("full_name") or "").split("/")[-1] or "—"
        out.append({"name": name, "lang": r.get("language") or "",
                    "desc": r.get("description") or ""})
    return out


def foodzen():
    d = _get("/api/foodzen/state")
    if d is None:
        return None
    lists = d.get("lists") or []
    lst = lists[0].get("items", []) if lists else []
    names = [i.get("name", "") if isinstance(i, dict) else str(i) for i in lst]
    need = [n.get("name", "") if isinstance(n, dict) else str(n) for n in (d.get("need") or [])]
    pantry = d.get("pantry") or {}
    pitems = pantry.get("items", pantry) if isinstance(pantry, dict) else []
    if isinstance(pitems, list):
        pnames = [x.get("name", "") if isinstance(x, dict) else str(x) for x in pitems]
    else:
        pnames = []
    return {"list": [n for n in names if n], "need": [n for n in need if n],
            "pantry": [n for n in pnames if n], "fixation": None,
            # Richer view for the editable UI. The flat name lists above are
            # kept because mock.py and the older page code expect them.
            "list_id": lists[0].get("id") if lists else None,
            "list_name": lists[0].get("name") if lists else None,
            "items": lst if isinstance(lst, list) else [],
            "need_items": d.get("need") or [],
            "pantry_items": pitems if isinstance(pitems, list) else []}


# ---------------------------------------------------------------- FoodZen writes
# The server saves one whole collection at a time (POST {kind, data}), so every
# mutation is read-modify-write. That is a lost-update risk if the website is
# open on the same collection at the same moment; the window is small and the
# server itself has no merge, so the app re-reads immediately before writing
# and invalidates its cache immediately after.

def foodzen_state(force=True):
    """Raw, shape-normalised FoodZen state, or None if the server is unreachable."""
    if force:
        _cache.pop("/api/foodzen/state", None)
    d = _get("/api/foodzen/state")
    return None if d is None else _fz.normalise_state(d)


def foodzen_save(kind, data):
    if kind not in _fz.KINDS:
        return False
    r = _post("/api/foodzen/save", {"kind": kind, "data": data})
    return bool(r and r.get("ok"))


def _foodzen_apply(mutate):
    """Read state, apply `mutate`, save only the collections it changed.

    Returns (ok, message). A failure is reported rather than swallowed so the
    UI can say the write did not land instead of showing it as applied.
    """
    state = foodzen_state(force=True)
    if state is None:
        return False, "server unreachable"
    try:
        changed = mutate(state)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if not changed:
        return True, "no change"
    for kind in sorted(changed):
        if not foodzen_save(kind, state[kind]):
            return False, f"saving {kind} failed"
    _cache.pop("/api/foodzen/state", None)
    return True, "saved"


def foodzen_add(name):
    return _foodzen_apply(lambda s: _fz.add_item(s, name))


def foodzen_toggle(name, done):
    return _foodzen_apply(lambda s: _fz.set_done(s, name, done))


def foodzen_remove(name):
    return _foodzen_apply(lambda s: _fz.remove_item(s, name))


def foodzen_need_add(name):
    return _foodzen_apply(lambda s: _fz.add_need(s, name))


def foodzen_need_remove(name):
    return _foodzen_apply(lambda s: _fz.remove_need(s, name))


def foodzen_pantry_add(name):
    return _foodzen_apply(lambda s: _fz.add_pantry(s, name))


def foodzen_pantry_remove(name):
    return _foodzen_apply(lambda s: _fz.remove_pantry(s, name))


def foodzen_need_to_list():
    return _foodzen_apply(_fz.need_to_list)


# ------------------------------------------------------------------ transport
def _post_qs(path, params, raw_body=None, timeout=15):
    """POST to an endpoint that takes its arguments in the query string.

    Several dashbored write routes (journal/new, journal/delete, vault/save,
    garage/records/add, garage/spec/save) read urllib.parse.parse_qs(query)
    rather than a JSON body, and vault/save additionally takes the file
    content as the raw body. Returns the decoded JSON, or None.
    """
    url = config.SERVER_URL + path + "?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None})
    data = raw_body if isinstance(raw_body, bytes) else (
        raw_body.encode() if isinstance(raw_body, str) else b"")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with config._opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        config._remember(config.SERVER_URL, False, config._describe(e))
        return None


def _ok(resp, *, key="ok"):
    """(ok, message) from a write response, surfacing the server's own error."""
    if resp is None:
        return False, config.STATUS.get("detail") or "server unreachable"
    if resp.get("error"):
        return False, str(resp["error"])
    if key in resp and not resp[key]:
        return False, "server refused the write"
    return True, "saved"


# -------------------------------------------------------------------- journal
JOURNAL_CATEGORIES = ["Mental-Health", "Trip-Reports", "Mechanicry",
                      "New-Projects", "Dr-Visits", "Medications"]


def journal_read(rel):
    d = _get(f"/api/journal/read?path={urllib.parse.quote(rel)}")
    return None if d is None else d.get("content", "")


def journal_new(category, title):
    """Create an entry. Returns (ok, message_or_path)."""
    if category not in JOURNAL_CATEGORIES:
        return False, f"unknown category {category!r}"
    if not (title or "").strip():
        return False, "title required"
    r = _post_qs("/api/journal/new", {"category": category, "title": title.strip()})
    if r is None:
        return False, config.STATUS.get("detail") or "server unreachable"
    if r.get("error"):
        return False, str(r["error"])
    _cache.pop("/api/journal/tree", None)
    return True, r.get("path", "")


def journal_save(rel, content):
    ok, msg = _ok(_post("/api/journal/save", {"path": rel, "content": content}))
    if ok:
        _cache.pop("/api/journal/tree", None)
        _cache.pop(f"/api/journal/read?path={urllib.parse.quote(rel)}", None)
    return ok, msg


def journal_delete(rel):
    ok, msg = _ok(_post_qs("/api/journal/delete", {"path": rel}))
    if ok:
        _cache.pop("/api/journal/tree", None)
    return ok, msg


# --------------------------------------------------------------------- garage
GARAGE_RECORD_TYPES = ("mileage", "services", "expenses")


def garage_records(bike, rtype):
    if rtype not in GARAGE_RECORD_TYPES:
        return None
    d = _get(f"/api/garage/records?bike={urllib.parse.quote(bike)}&type={rtype}")
    return None if d is None else (d.get("records") or [])


def garage_record_add(bike, rtype, record):
    """Append a mileage / service / expense record.

    The server assigns `id` and `created_at` under a lock (garage_records_add),
    so the app deliberately does not invent them.
    """
    if rtype not in GARAGE_RECORD_TYPES:
        return False, f"unknown record type {rtype!r}"
    if not isinstance(record, dict) or not record:
        return False, "empty record"
    r = _post_qs("/api/garage/records/add", {"bike": bike, "type": rtype},
                 raw_body=json.dumps(record))
    ok, msg = _ok(r)
    if ok:
        _cache.pop(f"/api/garage/records?bike={urllib.parse.quote(bike)}&type={rtype}", None)
        _cache.pop("/api/garage/tree", None)
    return ok, msg


def garage_read(rel):
    d = _get(f"/api/garage/read?path={urllib.parse.quote(rel)}")
    return None if d is None else d.get("content", "")


def garage_save(rel, content):
    ok, msg = _ok(_post("/api/garage/save", {"path": rel, "content": content}))
    if ok:
        _cache.pop("/api/garage/tree", None)
    return ok, msg


def garage_spec_save(bike, spec):
    ok, msg = _ok(_post_qs("/api/garage/spec/save", {"bike": bike},
                           raw_body=json.dumps(spec)))
    if ok:
        _cache.pop(f"/api/garage/spec?bike={urllib.parse.quote(bike)}", None)
    return ok, msg


# ---------------------------------------------------------------------- trips
def trip_save(trip):
    """Save a whole trip object. The server requires id and name."""
    if not isinstance(trip, dict) or not trip.get("id") or not trip.get("name"):
        return False, "id and name required"
    ok, msg = _ok(_post("/api/trips/save", trip))
    if ok:
        _cache.pop("/api/trips", None)
    return ok, msg


# ---------------------------------------------------------------------- vault
def vault_save(host, rel, content, lib=None):
    """Write a note to a machine's vault. Content goes in the raw body."""
    if not host or not rel:
        return False, "host and path required"
    return _ok(_post_qs("/api/vault/save", {"host": host, "path": rel, "lib": lib},
                        raw_body=content))


def journal():
    d = _get("/api/journal/tree")
    if d is None:
        return None
    out = []
    for c in (d.get("categories") or []):
        for f in (c.get("children") or []):
            if f.get("name") == "_TEMPLATE.md":
                continue
            out.append({"cat": c.get("label", c.get("name", "")),
                        "title": f.get("title") or f.get("name", "—"),
                        "date": (f.get("mtime") or "")[:10],
                        # Path the write endpoints want: category dir + the
                        # file path, which the tree gives relative to it.
                        "path": f"{c.get('path') or c.get('name', '')}/{f.get('path', '')}",
                        "name": f.get("name", "")})
    return out


def STATUS_LIVE():
    """True when the last request actually reached the server.

    Pages use this to decide whether to offer editing at all, and to show the
    sample-data banner. Named in caps because module __getattr__ below falls
    through to mock for anything it does not recognise, and a lowercase helper
    reads like one of the data accessors.
    """
    return bool(config.STATUS.get("ok"))


def ai_model():
    d = _get("/api/ai/models?provider=openrouter", timeout=4)
    if isinstance(d, dict):
        d = d.get("models") or d.get("items") or []
    if isinstance(d, list) and d:
        m = d[0]
        if isinstance(m, str):
            return m
        return m.get("id") or m.get("name") or "openrouter/auto"
    return "openrouter/auto"


def ai_chat(provider, model, messages):
    norm = [{"role": m.get("role", "user"), "content": m.get("content", m.get("text", ""))}
            for m in messages]
    r = _post("/api/ai/chat", {"provider": provider, "model": model, "messages": norm}, timeout=30)
    if not r or r.get("error"):
        return None
    ch = (r.get("choices") or [{}])
    msg = ch[0].get("message", {}) if ch else {}
    return r.get("response") or r.get("content") or r.get("message") or msg.get("content")


_MAP = {"MACHINES": machines, "WEATHER": weather, "FEEDS": feeds,
        "WAT2DO": wat2do_state, "BIKES": garage_bikes, "TRIPS": trips,
        "RIDES": rides_list, "PROJECTS": projects, "FOODZEN": foodzen,
        "JOURNAL": journal}


def __getattr__(name):
    if name in _MAP:
        val = _MAP[name]()
        if val is None:
            val = getattr(mock, name)
        return val
    return getattr(mock, name)


def refresh():
    _cache.clear()
