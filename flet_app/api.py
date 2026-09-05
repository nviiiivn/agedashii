"""NVII-DASH API client — drop-in replacement for `mock`.

Pages do `from .. import api as M`. Unknown attribute lookups fetch real data
from the dashbored server and reshape it to mock.py structures; on connection
failure they fall back to mock data. Real-but-empty (e.g. foodzen) stays empty.
"""
import json
import time
import urllib.request

from . import mock

BASE = "http://192.0.2.21:6900"
TTL = 30
_cache = {}

WMO = {0: "clear", 1: "partly cloudy", 2: "partly cloudy", 3: "overcast",
       45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
       61: "rain", 63: "rain", 65: "rain", 71: "snow", 73: "snow", 75: "snow",
       80: "showers", 81: "showers", 82: "showers", 95: "storm", 96: "storm", 99: "storm"}


def _get(path, timeout=2.5):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        _cache[path] = (time.time(), data)
        return data
    except Exception:
        return None


def _post(path, payload, timeout=6):
    try:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception:
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
            "pantry": [n for n in pnames if n], "fixation": None}


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
                        "date": (f.get("mtime") or "")[:10]})
    return out


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
