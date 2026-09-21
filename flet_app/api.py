"""NVII-DASH API client — offline-first, backed by flet_app.store.

Pages do `from .. import api as M`. Unknown attribute lookups fetch real data
from the dashbored server and reshape it to mock.py structures; on connection
failure they fall back to mock data. Real-but-empty (e.g. foodzen) stays empty.

WHAT CHANGED, AND WHY
---------------------
Every read used to be a blocking GET behind a 30-second in-memory dict, and
every write a blocking POST that was simply *lost* when there was no signal.
Kill the app, or walk into a tunnel, and both the cache and the write went
with it.

Now both go through `flet_app.store`, a SQLite cache + outbox on the device:

  reads   `_get` answers from the store. A path we have never fetched is
          fetched inline, exactly as before; a path we already hold is
          returned instantly and refreshed *behind* the UI. So a read works
          with the server down, and it still works after a restart.

  writes  every write helper queues into the outbox, which applies the change
          to the cached copy in the same transaction — so the screen updates
          with the radio off — and then tries to send it immediately if the
          network looks worth trying. `sync()` drains whatever is left.

Three things a caller can ask, and what they mean:

  STATUS_LIVE()        did the *last request* reach the server (unchanged)
  provenance(path)     'live' | 'cached' | 'queued' | 'none', per endpoint
  sync_status()        the whole picture, including "3 changes waiting"

`provenance` is the honest one: LIVE means a fresh answer off the wire,
CACHED means this device's older copy of the server's real data, QUEUED means
our own write that the server has not accepted yet, NONE means we have never
had this endpoint and whatever is on screen came from `mock` — sample data,
which the pages must keep labelling as such.

Nothing here ever presents sample data as real: the store only ever holds
bytes the server sent or writes the user made.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, mock
from . import foodzen as _fz
from .store import DEFAULT_TTL, Store, StoreError, Unreachable, default_db_path

TTL = 30

WMO = {0: "clear", 1: "partly cloudy", 2: "partly cloudy", 3: "overcast",
       45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
       61: "rain", 63: "rain", 65: "rain", 71: "snow", 73: "snow", 75: "snow",
       80: "showers", 81: "showers", 82: "showers", 95: "storm", 96: "storm", 99: "storm"}

FOODZEN_PATH = "/api/foodzen/state"

# What a caller gets back from provenance().
LIVE = "live"        # fetched from the server within the TTL
CACHED = "cached"    # the server's data, but this device's older copy of it
QUEUED = "queued"    # our own write, applied here, not yet accepted by the server
NONE = "none"        # never fetched; anything on screen is mock sample data

# The message an offline write returns. Deliberately not a bare "saved":
# the user's change is safe on the device, and the server has not seen it.
QUEUED_MESSAGE = "saved on this device, will sync"

# How long a known-offline verdict is trusted before we try the wire again.
# Short enough that walking out of a tunnel is noticed quickly, long enough
# that a page full of panels does not fire one request per panel per rebuild.
ONLINE_RECHECK = 20.0


# ------------------------------------------------------------------ the store
_store = None
_store_lock = threading.Lock()
_probing = False


def store():
    """The device's local store, created on first use."""
    global _store
    with _store_lock:
        if _store is None:
            _store = _new_store()
        return _store


def _peek():
    """The store if one has been opened, without opening one.

    Status questions (STATUS_LIVE, provenance) get asked over and over while
    a page builds. Creating a database to answer "have we got anything for
    this?" with "no" would be an odd way to spend a first launch.
    """
    with _store_lock:
        return _store


def set_store(new_store):
    """Point the module at a different store. Returns the old one.

    Tests use this to hand in a tempfile-backed store; nothing in the app
    calls it.
    """
    global _store
    with _store_lock:
        old, _store = _store, new_store
        return old


def _new_store():
    st = Store(default_db_path(), ttl=TTL if TTL > 0 else DEFAULT_TTL,
               probe=lambda: config.probe(config.SERVER_URL))
    # main.py probes the server before the first page is built. Adopting that
    # verdict means the first write of a session does not have to rediscover
    # it, and an offline launch queues instantly instead of stalling.
    if config.STATUS.get("at"):
        st.set_online(bool(config.STATUS.get("ok")),
                      str(config.STATUS.get("detail") or ""))
    return st


# -------------------------------------------------------------- cache keying
def _key(path):
    """The cache key for an API path: the server it came from, then the path.

    Scoped by origin on purpose. A cached row is one server's answer, and the
    app can be re-pointed at a different address in Settings at any time - so
    keying on the path alone means switching servers silently shows the old
    server's data, and a restart pointed somewhere new shows it again with
    nothing to notice it by.

    The cost, accepted: the LAN address and the mDNS name are the same box,
    and the app does not know that, so switching between them re-fetches
    everything once. Each address keeps its own rows, so switching back is
    instant again, and neither can ever answer for the other.
    """
    return config.normalise(config.SERVER_URL) + path


def _keys(mapping):
    return {_key(p): v for p, v in (mapping or {}).items()}


# ---------------------------------------------------------------- invalidation
# Cached entries are marked *stale*, never deleted: deleting is how an offline
# app loses the only copy it has. A stale entry is still shown, and refreshed
# behind the UI the moment the server is reachable again.
_invalid = {}        # cache key -> anything fetched before this is stale
_invalid_all = 0.0   # the same, for every key at once


def _invalidate(*paths):
    now = time.time()
    for path in paths:
        _invalid[_key(path)] = now


def refresh():
    """Mark every cached answer stale, so the next read re-fetches it.

    Settings calls this when the server address changes and `_forms.rebuilder`
    after every write. It used to empty the cache outright, which offline
    threw away the app's only copy of the data *and* the visible effect of a
    queued write. Marking stale gets the re-read without the amnesia.
    """
    global _invalid_all
    _invalid_all = time.time()
    _invalid.clear()


def _invalidated(hit):
    """True when someone said this entry is no longer to be trusted.

    Stronger than being past its TTL: a TTL-stale answer is still the best
    thing we have and gets shown while a refresh runs behind it, but an entry
    dropped by a write or by Settings pointing at a different server has to
    be re-read *before* it is shown again.
    """
    return hit.fetched_at < max(_invalid_all, _invalid.get(hit.path, 0.0))


def _stale(hit, ttl=None):
    if hit is None:
        return True
    return _invalidated(hit) or hit.is_stale(TTL if ttl is None else ttl)


class _CacheShim:
    """Compatibility for the old `M._cache` dict.

    pages/_wat2do_parts.py does `M._cache.pop(path, None)` to force a re-read
    and settings does `.clear()` via refresh(). Both still mean "ask the
    server again", they just no longer throw the data away first.
    """

    def pop(self, path, default=None):
        _invalidate(path)
        return default

    def clear(self):
        refresh()

    def get(self, path, default=None):
        hit = store().get(_key(path))
        return default if hit is None else (hit.fetched_at, hit.data)

    def __contains__(self, path):
        return store().get(_key(path)) is not None

    def __len__(self):
        return store().stats().get("cached", 0)


_cache = _CacheShim()


# ------------------------------------------------------------------- transport
class _Failed(StoreError):
    """A request that did not land, carrying the server's own words.

    `gateway` means nothing of ours answered — no network, or an auth gateway
    answered instead. A queued write must not be charged a retry attempt for
    either of those; see `_is_gateway`.

    A StoreError so that when the store records why a refresh failed it keeps
    `detail` as written - already a line for a human - instead of prefixing
    it with this class's name.
    """

    def __init__(self, detail, *, gateway=False):
        super().__init__(detail or "request failed")
        self.detail = detail or "request failed"
        self.gateway = gateway


# Codes that mean "something in front of the server answered": the auth
# gateway redirect this app is built around (config._NoRedirect), the login
# challenges behind it, and Cloudflare's edge 403.
_GATEWAY_CODES = (301, 302, 303, 307, 308, 401, 403, 407)


def _server_said(exc):
    """Pull the server's own error message out of a failed request.

    dashbored answers 4xx/5xx with a JSON body like {"error": "bad bike"},
    but urllib raises HTTPError and that body goes unread - so the user was
    shown "server returned HTTP 500" while the server had already explained
    exactly what was wrong. The body can only be read once, so it happens
    here, in one place.
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.strip()[:200] or None
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            if data.get(key):
                return str(data[key])[:200]
    return None


def _describe_failure(exc):
    """Human-readable reason, preferring whatever the server actually said."""
    if isinstance(exc, urllib.error.HTTPError):
        said = _server_said(exc)
        if said:
            return said
    return config._describe(exc)


def _is_gateway(exc):
    """True when the dashbored API itself never answered.

    Offline, DNS failure, timeout, an auth gateway's 302, Cloudflare's 403,
    or a 200 that is an HTML login page rather than JSON. None of these are
    the queued write's fault, so none of them may burn one of its attempts -
    that is exactly how a week without signal parks the whole outbox.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _GATEWAY_CODES
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError, ValueError)):
        return True
    return False


def _qs(path, params):
    return path + "?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None})


def _body_bytes(raw):
    if isinstance(raw, bytes):
        return raw
    return raw.encode() if isinstance(raw, str) else b""


def _request(method, path, *, data=None, timeout=10, empty_ok=False):
    """One request to the configured server. Returns parsed JSON or raises.

    The server address is read from config on every call rather than captured
    at import, so changing it in Settings takes effect immediately. Redirects
    are not followed - a 302 means an auth gateway intercepted us, and
    following it would hand back an HTML login page that fails JSON parsing
    and looks identical to the server being down.
    """
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(config.SERVER_URL + path, data=data,
                                 headers=headers, method=method)
    try:
        with config._opener.open(req, timeout=timeout) as r:
            body = r.read().decode()
        out = {} if (empty_ok and not body) else json.loads(body)
    except Exception as e:  # noqa: BLE001 - every failure is described, not hidden
        detail = _describe_failure(e)
        config._remember(config.SERVER_URL, False, detail)
        raise _Failed(detail, gateway=_is_gateway(e)) from e
    config._remember(config.SERVER_URL, True, "connected")
    return out


def _fetch(path, timeout=2.5):
    """GET `path`, raising on failure so the store records what went wrong."""
    return _request("GET", path, timeout=timeout)


def _get(path, timeout=2.5):
    """GET a JSON path, from the device's store or the server, or None.

    Signature and return shape are unchanged: None still means "we have
    nothing for this". What is new is that "nothing" now takes a restart with
    an empty database to achieve, rather than a dropped connection.
    """
    return _read(path, lambda: _fetch(path, timeout))


def _post(path, payload, timeout=6):
    """POST JSON to the configured server, or None. See _request on redirects.

    Straight to the wire, no outbox: this is for calls that *compute*
    something (eatzen/parse, ai/chat, valhalla/route) where a stale answer is
    worse than no answer. Durable writes go through `_queue_write`.
    """
    try:
        return _request("POST", path, data=json.dumps(payload).encode(),
                        timeout=timeout, empty_ok=True)
    except _Failed:
        return None


def _post_qs(path, params, raw_body=None, timeout=15):
    """POST to an endpoint that takes its arguments in the query string.

    Several dashbored write routes (journal/new, journal/delete, vault/save,
    garage/records/add, garage/spec/save) read urllib.parse.parse_qs(query)
    rather than a JSON body, and vault/save additionally takes the file
    content as the raw body. Returns the decoded JSON, or None.
    """
    try:
        return _request("POST", _qs(path, params), data=_body_bytes(raw_body),
                        timeout=timeout, empty_ok=True)
    except _Failed:
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


# ----------------------------------------------------------------- read path
def _read(path, fetch_fn, ttl=None):
    """Answer `path` from the store, refreshing behind the UI when stale."""
    key = _key(path)
    try:
        st = store()
        hit = st.get(key)
    except Exception:  # noqa: BLE001 - no local database is not a dead app
        return _direct(fetch_fn)

    if hit is not None and hit.is_local:
        # Our own queued write. The server's copy is behind it, so re-reading
        # would undo on screen what the user just did.
        return hit.data
    if hit is None:
        # Nothing known. Fetch inline, exactly as the app always did - unless
        # we have just established there is no network, in which case a page
        # of uncached panels would be a page of timeouts.
        if not _worth_trying():
            return None
        got = st.read(key, fetch_fn, ttl=0, background=False)
        return None if got is None else got.data
    if _invalidated(hit):
        # Explicitly dropped - by a write, or by Settings pointing somewhere
        # else. Re-read before showing it again, inline, the way the old
        # cleared-cache code did; the copy we hold is the fallback if that
        # fails, not the answer.
        # st.refresh, not st.read: read declines to fetch anything while it
        # believes it is offline, and this branch is the one place that has
        # to ask anyway - deciding not to is _worth_trying's job.
        if _worth_trying():
            st.refresh(key, fetch_fn)
            got = st.get(key)
            return hit.data if got is None else got.data
        _wake()
    elif _stale(hit, ttl):
        st.read(key, fetch_fn, ttl=0, background=True)
        _wake()
    return hit.data


def _direct(fetch_fn):
    try:
        return fetch_fn()
    except _Failed:
        return None


def _cached(path, default=None):
    """What the store holds for `path`, without ever being the reason for a crash."""
    try:
        return store().get_data(_key(path), default)
    except Exception:  # noqa: BLE001
        return default


def _reread(path, timeout=6):
    """Fetch `path` now if the wire is worth trying; return what we hold either way.

    The read-modify-write helpers use this: they want the freshest base they
    can get, but offline the device's own copy is a perfectly good base and
    refusing to edit it is the bug this module exists to remove.
    """
    key = _key(path)
    try:
        st = store()
        hit = st.get(key)
    except Exception:  # noqa: BLE001
        return _direct(lambda: _fetch(path, timeout))
    if hit is not None and hit.is_local:
        return hit.data
    if _worth_trying():
        st.refresh(key, lambda: _fetch(path, timeout))
    got = st.get(key)
    return None if got is None else got.data


def _worth_trying():
    """Whether to spend a request finding out, rather than assuming offline.

    "We are offline" is a belief about one address. Settings pointing the app
    somewhere else - the LAN address instead of the VPN one, say - makes that
    belief worthless, so a changed address always gets a try.
    """
    try:
        state = store().status
    except Exception:  # noqa: BLE001
        return True
    if state["online"]:
        return True
    if config.normalise(config.STATUS.get("url") or "") != config.normalise(config.SERVER_URL):
        return True
    return time.time() - (state["at"] or 0.0) >= ONLINE_RECHECK


def _wake():
    """While offline, re-probe now and then so returning signal is noticed.

    One probe for the whole app, not one fetch per stale endpoint: the store
    will not refresh anything while it believes it is offline, so something
    has to change that belief, and /api/config is the cheapest way to.
    """
    global _probing
    st = store()
    state = st.status
    if state["online"] or time.time() - (state["at"] or 0.0) < ONLINE_RECHECK:
        return
    with _store_lock:
        if _probing:
            return
        _probing = True

    def run():
        global _probing
        try:
            st.check_online()
        finally:
            with _store_lock:
                _probing = False

    threading.Thread(target=run, name="nvii-probe", daemon=True).start()


# ---------------------------------------------------------------- write path
# Every durable write is queued first and sent second, so the same code path
# runs online and off. The envelope records *how* to send it, because the
# server takes some writes as a JSON body and others in the query string with
# the content raw in the body.

def _envelope(*, json_body=None, params=None, body=None, timeout=None):
    env = {}
    if params is not None:
        env["params"] = params
        env["body"] = body or ""
    else:
        env["json"] = json_body
    if timeout:
        env["timeout"] = timeout
    return env


def _send(method, path, env):
    """Put one queued write on the wire. Returns (response, ok, message).

    Raises `Unreachable` when nothing of ours answered (the store must not
    count that as an attempt) and `StoreError` carrying the server's own text
    for anything the server actually rejected.
    """
    env = env if isinstance(env, dict) else {}
    timeout = env.get("timeout") or 15
    try:
        if "params" in env:
            resp = _request("POST", _qs(path, env["params"]),
                            data=_body_bytes(env.get("body")),
                            timeout=timeout, empty_ok=True)
        else:
            resp = _request(method or "POST", path,
                            data=json.dumps(env.get("json")).encode(),
                            timeout=timeout, empty_ok=True)
    except _Failed as e:
        if e.gateway:
            raise Unreachable(e.detail) from e
        raise StoreError(e.detail) from e
    ok, msg = _ok(resp)
    return resp, ok, msg


def _drain(limit=None):
    """Flush the outbox, remembering what happened to each item.

    Returns (FlushResult, sent_ids, failures, responses) where `failures` is
    {item_id: (was_offline, message)} — the caller needs that split to tell a
    write that is merely waiting for signal from one the server refused.
    """
    sent, failures, responses = set(), {}, {}

    def send(item):
        try:
            resp, ok, msg = _send(item.method, item.path, item.payload)
        except Unreachable as e:
            failures[item.id] = (True, str(e))
            raise
        except Exception as e:  # noqa: BLE001 - the store decides what it means
            failures[item.id] = (False, str(e))
            raise
        responses[item.id] = resp
        if ok:
            sent.add(item.id)
        else:
            failures[item.id] = (False, msg)
        return ok, msg

    return store().flush(send, limit=limit), sent, failures, responses


class _Result:
    """What a queued write did: (ok, message) plus why, for the helpers."""

    __slots__ = ("ok", "message", "queued", "response")

    def __init__(self, ok, message, queued=False, response=None):
        self.ok, self.message = ok, message
        self.queued, self.response = queued, response

    @property
    def pair(self):
        return self.ok, self.message


def _queue_write(path, *, json_body=None, params=None, body=None, timeout=None,
                 local=None, coalesce_key=None, invalidate=(), method="POST"):
    """Queue a write, apply it locally, and send it now if that is plausible.

    Returns a `_Result`. The (ok, message) contract every write helper owes
    its caller comes out of `.pair`:

      sent      (True, "saved")               the server has it
      queued    (True, QUEUED_MESSAGE)        the device has it, the server will
      refused   (False, "<the server's own words>")

    A refusal leaves the item in the outbox: the store parks poison after five
    attempts rather than deleting it, and nothing in this app throws a user's
    write away on its own. `sync_status()` is where those surface.
    """
    env = _envelope(json_body=json_body, params=params, body=body, timeout=timeout)
    try:
        # The outbox keeps the bare path - it is what the URL is built from at
        # send time - while the local effect lands under the origin-scoped
        # cache key, so it is only ever shown for the server it is going to.
        item_id = store().enqueue(method, path, env, local=_keys(local) or None,
                                  coalesce_key=coalesce_key)
    except Exception as e:  # noqa: BLE001 - no local database: send it now or lose it
        try:
            resp, ok, msg = _send(method, path, env)
        except Unreachable as unreachable:
            return _Result(False, str(unreachable) or "server unreachable")
        except Exception as failure:  # noqa: BLE001
            return _Result(False, str(failure) or f"{type(e).__name__}: {e}")
        if ok:
            _invalidate(*invalidate)
        return _Result(ok, msg, response=resp)

    if invalidate:
        _invalidate(*invalidate)
    if not _worth_trying():
        return _Result(True, QUEUED_MESSAGE, queued=True)

    _, sent, failures, responses = _drain()
    if item_id in sent:
        return _Result(True, "saved", response=responses.get(item_id))
    if item_id in failures:
        offline, msg = failures[item_id]
        if offline:
            return _Result(True, QUEUED_MESSAGE, queued=True)
        return _Result(False, msg, queued=True)
    # Not tried this time: behind an older write to the same path, or backing
    # off. Still queued, still ours, still going to be sent.
    return _Result(True, QUEUED_MESSAGE, queued=True)


# --------------------------------------------------------------- sync surface
def sync(limit=None):
    """Drain the outbox. Returns a summary dict; never raises.

    Call it when the app comes to the foreground, when Settings finds the
    server again, or from a "sync now" button.
    """
    try:
        result, _, failures, _ = _drain(limit)
    except Exception as e:  # noqa: BLE001 - syncing must never take the app down
        return {"sent": 0, "failed": 0, "parked": 0, "skipped": 0, "offline": True,
                "pending": 0, "errors": [f"{type(e).__name__}: {e}"]}
    return {"sent": result.sent, "failed": result.failed, "parked": result.parked,
            "skipped": result.skipped, "offline": result.aborted,
            "pending": pending_count(),
            "errors": [msg for offline, msg in failures.values() if not offline]}


def pending_count():
    """How many writes are waiting to reach the server."""
    try:
        return store().pending_count()
    except Exception:  # noqa: BLE001
        return 0


def sync_status():
    """Everything the UI needs to be honest about where the data stands."""
    try:
        st = store()
        stats, state = st.stats(), st.status
    except Exception as e:  # noqa: BLE001
        return {"online": False, "detail": f"{type(e).__name__}: {e}", "live": False,
                "pending": 0, "parked": 0, "conflict": 0, "cached": 0, "label": ""}
    pending = stats.get("pending", 0)
    stuck = stats.get("parked", 0) + stats.get("conflict", 0)
    parts = []
    if pending:
        parts.append(f"{_plural(pending, 'change')} waiting")
    if stuck:
        parts.append(f"{_plural(stuck, 'change')} the server refused")
    return {"online": bool(state["online"]), "detail": state["detail"],
            "live": STATUS_LIVE(), "pending": pending,
            "parked": stats.get("parked", 0), "conflict": stats.get("conflict", 0),
            "cached": stats.get("cached", 0), "label": ", ".join(parts)}


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def provenance(path):
    """Where the data for `path` came from: LIVE, CACHED, QUEUED or NONE."""
    st = _peek()
    if st is None:
        return NONE
    try:
        hit = st.get(_key(path))
    except Exception:  # noqa: BLE001
        return NONE
    if hit is None:
        return NONE
    if hit.is_local:
        return QUEUED
    return CACHED if _stale(hit, TTL if TTL > 0 else DEFAULT_TTL) else LIVE


def retry_parked():
    """Put writes the server refused back in the queue. Returns how many."""
    try:
        return store().retry_parked()
    except Exception:  # noqa: BLE001
        return 0


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
        # An unreachable machine answers {"error": "unreachable"}, which is a
        # non-empty dict - so bool(w) called it online, and SP3 showed a live
        # status dot and a live row on Home while it was down.
        online = bool(w) and not w.get("error")
        ram = "—"
        try:
            if w.get("mem_total"):
                ram = f"{int(w['mem_used']) // 1024}/{int(w['mem_total']) // 1024} GB"
        except (KeyError, ValueError):
            pass
        osname = (w.get("greeting") or "").replace("OS:", "").split("·")[0].strip()[:28]
        out.append({"id": mid, "display": m.get("display", mid), "color": m.get("color", "#58a6ff"),
                    "online": online, "error": str(w.get("error") or ""),
                    "os": osname or "—", "uptime": w.get("uptime", "—"),
                    "cpu": w.get("cpu", "—"), "ram": ram, "temp": w.get("temp", "—"),
                    # What the topbar dots draw. None, not 0, when the
                    # machine is down or did not report - an empty ring says
                    # "unknown" where a full-circle 0% says "idle".
                    "load": _reading(w.get("cpu_percent"), online),
                    "mem_load": _reading(w.get("memory_percent"), online),
                    "docker": "", "host": ""})
    return out


# --------------------------------------------------------- one machine, fully
# /api/dashboard has always carried far more per machine than the app read:
# the fetch greeting alone holds the kernel, the CPU's name and the LAN
# address, and the network widget holds per-interface byte counters. The
# server-side probes added alongside this fill the rest - chassis, hardware
# model, OS base, drives, GPU temperature, current network rate.
#
# One rule runs through every function below: a value that was not measured
# comes back as None, never as 0 and never as a guess. An unreachable
# machine answers {"error": "unreachable"} for each widget, and that dict is
# truthy - the trap that once had SP3 showing green everywhere - so aliveness
# is `widget_alive`, and when the system widget is not alive every field
# derived from it is None regardless of what else the payload contains.

# Interfaces that are not the machine's link to the network: loopback,
# docker's bridges and veth pairs, VPN tunnels. Summing these into "network
# traffic" double-counts every container byte and makes a quiet host look
# busy - lil-faegola carries ten of them.
_VIRTUAL_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "vnet",
                           "tun", "tap", "wg", "tailscale", "zt", "cni",
                           "flannel", "kube", "dummy")


def iface_is_physical(name):
    """Whether an interface name is the machine's own link, not a virtual one."""
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n == "lo" or n.startswith("lo:"):
        return False
    return not any(n.startswith(pre) for pre in _VIRTUAL_IFACE_PREFIXES)


def greeting_field(greeting, label):
    """One "Label: value" line out of a fetch-style greeting blob, or None.

    lil-faegola's greeting arrives with the backslash-n escaped rather than
    newlines, so both are treated as line breaks. A label is matched on its
    own line only - "Local IP (eth0)" has to match the prefix "Local IP"
    because the interface name is baked into the key.
    """
    want = str(label or "").strip().lower()
    if not want:
        return None
    for line in str(greeting or "").replace("\\n", "\n").split("\n"):
        line = line.strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key == want or key.startswith(want + " "):
            value = value.strip()
            if value:
                return value
    return None


def _pos_int(v):
    """A whole number above zero, or None. "?" and "" and 0 are all None."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _reading(v, known=True):
    """A percentage the machine actually reported, or None."""
    if not known:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if 0.0 <= n <= 100.0 else None


def _text(v):
    s = str(v if v is not None else "").strip()
    # "?" is what the server's shell fallbacks print when a command failed.
    return None if s in ("", "?", "-", "—") else s


def gpu_columns(raw):
    """nvidia-smi's CSV row -> (name, used MB, total MB, load %). None each.

    Shape, live: "NVIDIA GeForce RTX 3080, 13315 MiB, 20480 MiB, 0 %". Only
    the Tower has one; every other machine sends "" and must not report a
    0 % GPU it does not have.
    """
    parts = [p.strip() for p in str(raw or "").split(",")]
    if not parts or not parts[0]:
        return None, None, None, None
    def mib(i):
        if len(parts) <= i:
            return None
        digits = re.sub(r"[^\d]", "", parts[i])
        return int(digits) if digits else None
    load = None
    if len(parts) > 3:
        digits = re.sub(r"[^\d]", "", parts[3])
        load = float(digits) if digits else None
    return parts[0], mib(1), mib(2), load


def _disk_split(raw):
    """"1.7T/1.9T (89%)" -> ("1.7T", "1.9T", 89.0), each None if unreadable."""
    s = str(raw or "").strip()
    m = re.match(r"^\s*([^/\s]+)\s*/\s*([^\s(]+)\s*(?:\((\d+)%?\))?\s*$", s)
    if not m:
        return None, None, None
    used, total, pct = m.group(1), m.group(2), m.group(3)
    if "?" in used or "?" in total:
        return None, None, None
    return used, total, (float(pct) if pct is not None else None)


def machine_detail(dash, mid, cfg=None, docker=None):
    """Everything the server knows about one machine. None means unmeasured.

    `cfg` is /api/config, `docker` is /api/docker_list; both optional,
    because the caller may have them already and neither is worth a second
    request. Never raises on an odd payload - a machine the server has never
    heard of comes back as an offline record rather than an exception.
    """
    m = (dash or {}).get(mid)
    m = m if isinstance(m, dict) else {}
    sysw = machine_widget(dash, mid, "system")
    netw = machine_widget(dash, mid, "network")
    svcw = machine_widget(dash, mid, "services")
    alive = widget_alive(sysw)
    greeting = sysw.get("greeting") if alive else None
    conf = ((cfg or {}).get(mid) or {}) if isinstance(cfg, dict) else {}

    out = {
        "id": mid,
        "nickname": m.get("display") or mid,
        "color": m.get("color") or "#58a6ff",
        "online": alive,
        "error": _text(sysw.get("error")),
    }

    def sysfield(key):
        return _text(sysw.get(key)) if alive else None

    # ---- identity
    out["name"] = sysfield("model") or (greeting_field(greeting, "Host") if alive else None)
    out["hostname"] = sysfield("hostname")
    out["machine_type"] = sysfield("machine_type")
    out["os"] = ((greeting_field(greeting, "OS") if alive else None)
                 or sysfield("os_name"))
    out["os_base"] = sysfield("os_base")
    out["kernel"] = (greeting_field(greeting, "Kernel") if alive else None)
    out["uptime"] = sysfield("uptime")

    # ---- address. The greeting names the interface it read, which is the
    # one the machine itself considers primary; the interface list is the
    # fallback, and /api/config's host is the address *we* reach it on, which
    # is a different fact and kept separate.
    ip = greeting_field(greeting, "Local IP") if alive else None
    if not ip and alive:
        for i in (netw.get("interfaces") or []):
            if isinstance(i, dict) and iface_is_physical(i.get("name")) \
                    and "." in str(i.get("ip") or ""):
                ip = str(i["ip"])
                break
    out["ip"] = ip
    out["address"] = (f"{conf.get('user')}@{conf['host']}"
                      if conf.get("host") and conf.get("user")
                      else _text(conf.get("host")))

    # ---- cpu
    out["cpu_name"] = sysfield("cpu_name") or (greeting_field(greeting, "CPU") if alive else None)
    out["cpu_cores"] = _pos_int(sysw.get("cpu_cores")) if alive else None
    out["cpu_pct"] = _reading(sysw.get("cpu_percent"), alive)
    out["cpu_temp"] = sysfield("temp")

    # ---- memory
    out["ram_used_mb"] = _pos_int(sysw.get("mem_used")) if alive else None
    out["ram_total_mb"] = _pos_int(sysw.get("mem_total")) if alive else None
    out["ram_pct"] = _reading(sysw.get("memory_percent"), alive and bool(out["ram_total_mb"]))
    rtype = sysfield("ram_type")
    rspeed = sysfield("ram_speed")
    out["ram_type"] = (f"{rtype} @ {rspeed}" if rtype and rspeed else rtype)

    # ---- gpu. gpu_percent is 0 on every machine with no GPU, so it is only
    # a reading when nvidia-smi actually answered.
    gname, gused, gtotal, gload = gpu_columns(sysw.get("gpu") if alive else "")
    out["gpu_name"] = gname or sysfield("gpu_pci")
    out["gpu_mem_used_mb"] = gused
    out["gpu_mem_total_mb"] = gtotal
    out["gpu_pct"] = gload if gname else None
    out["gpu_temp"] = sysfield("gpu_temp")

    # ---- disks
    used, total, dpct = _disk_split(sysw.get("disk") if alive else "")
    out["disk_used"], out["disk_total"] = used, total
    out["disk_pct"] = dpct if dpct is not None else _reading(sysw.get("disk_percent"),
                                                         alive and total is not None)
    out["disks"] = [d for d in (sysw.get("disks") or []) if isinstance(d, dict)] if alive else []

    # ---- network. Counters are since boot, so their sum is this uptime's
    # total; the rate is the server differencing two samples and is absent
    # until it has two.
    bw = netw.get("bandwidth") if isinstance(netw.get("bandwidth"), dict) else {}
    rates = netw.get("rates") if isinstance(netw.get("rates"), dict) else {}
    net_alive = widget_alive(netw)

    def _sum(src, field):
        vals = [v.get(field) for n, v in src.items()
                if iface_is_physical(n) and isinstance(v, dict)
                and isinstance(v.get(field), (int, float))]
        return sum(vals) if vals else None

    out["net_rx_total"] = _sum(bw, "rx") if net_alive else None
    out["net_tx_total"] = _sum(bw, "tx") if net_alive else None
    out["net_rx_rate"] = _sum(rates, "rx") if net_alive else None
    out["net_tx_rate"] = _sum(rates, "tx") if net_alive else None
    out["net_ifaces"] = [i for i in (netw.get("interfaces") or [])
                         if isinstance(i, dict) and iface_is_physical(i.get("name"))
                         ] if net_alive else []
    out["net_connections"] = (netw.get("connections")
                              if net_alive and isinstance(netw.get("connections"), int)
                              else None)

    # ---- services. The server's probe lists listening ports, all of which
    # are by definition up, so "up of total" comes from systemd instead; a
    # machine with no systemd (void runs runit) reports neither, and that is
    # an em dash, not 0 of 0.
    out["services_listening"] = (len([s for s in (svcw.get("services") or [])
                                      if isinstance(s, dict)])
                                 if widget_alive(svcw) else None)
    out["units_running"] = sysw.get("units_running") if alive else None
    out["units_total"] = _pos_int(sysw.get("units_total")) if alive else None
    if out["units_total"] is None:
        out["units_running"] = None

    # ---- docker
    out["containers_running"] = out["containers_total"] = None
    host = (docker or {}).get(mid) if isinstance(docker, dict) else None
    # An unreachable host answers containers: [] with an error beside it, and
    # a machine that is down cannot be asked at all - both are unknown, not
    # zero. Only a live host that really answered gets a count.
    if isinstance(host, dict) and not host.get("error") and alive:
        rows = [c for c in (host.get("containers") or []) if isinstance(c, dict)]
        out["containers_total"] = len(rows)
        out["containers_running"] = sum(1 for c in rows if c.get("state") == "running")
    return out


def flipper(query="motorcycle"):
    """Real listings from the server's scanner, or None if it cannot be had.

    Returns only the rows the server was willing to score - it now flags
    non-listings (a $1 placeholder, a service ad, a Reddit question) with
    `not_a_deal`, and those must never be shown as finds.
    """
    d = flipper_scan(query)
    if d is None:
        return None
    return [x for x in (d.get("listings") or []) if x.get("score") is not None]


def flipper_scan_path(query="motorcycle", min_price=None, max_price=None):
    """The scanner URL for one search, matching the website's parameters.

    Separate from the fetch so the page can ask `provenance()` whether the
    result on screen came off the wire or out of the cache - a scan can take
    two minutes, and "this is the answer from twenty minutes ago" is a
    different statement from "this is live".
    """
    params = {"query": query or "motorcycle"}
    for key, value in (("min_price", min_price), ("max_price", max_price)):
        text = str(value).strip() if value not in (None, "") else ""
        if text:
            params[key] = text
    return "/api/flipper/scan?" + urllib.parse.urlencode(params)


def flipper_scan(query="motorcycle", min_price=None, max_price=None, timeout=180):
    """The scanner's whole answer, or None. Never invents an empty result.

    The page needs more than the listings: the market median every score is
    measured against, how many listings each source returned, and - the part
    that was missing from both ends until recently - *why* the nine Reddit
    sources returned nothing.

    The timeout is long because this really is slow: a cold
    `/api/flipper/scan?query=motorcycle` took 639 listings across seven
    Craigslist regions to return here. Callers must show a loading state
    before calling and must call it off the UI thread.
    """
    d = _get(flipper_scan_path(query, min_price, max_price), timeout=timeout)
    return d if isinstance(d, dict) else None


# ------------------------------------------------- keyless third-party reads
# RainViewer and Open-Meteo's geocoder are public, keyless and *not* the
# dashbored server. Three rules follow from that and are enforced here rather
# than in each caller:
#
#   * a failure must not touch config.STATUS. Rainviewer being down says
#     nothing about whether the dashbored server is up, and marking the app
#     offline over a radar tile would grey out every other page.
#   * a failure must come back as a sentence, not as None. "Rain radar
#     unavailable" with no reason is what the Rain button used to do.
#   * there must be a timeout the caller chooses. The first call to
#     rainviewer from this machine took over 25 s and the next took 0.64 s.

def _third_party(url, timeout, headers=None):
    """GET JSON from a public API. Returns (data, error-sentence-or-None)."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except TimeoutError:
        return None, f"no answer within {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001 - reported, never raised at the UI
        return None, config._describe(e)
    try:
        return json.loads(raw), None
    except ValueError:
        return None, "answered, but not with JSON"


# Frame tiles: 256px, colour scheme 2 (MeteoRain blue to red), smoothed, no
# snow overlay - the same `/256/{z}/{x}/{y}/2/1_0.png` suffix the website uses.
RAINVIEWER_INDEX = "https://api.rainviewer.com/public/weather-maps.json"


def rainviewer_frames(timeout=15):
    """(frames, error). Each frame is {"time": epoch_seconds, "url": template}.

    `url` is a slippy-tile template ready for a TileLayer. An empty list with
    no error means RainViewer answered and is currently publishing no radar
    frames, which is a real answer and not a failure.
    """
    d, err = _third_party(RAINVIEWER_INDEX, timeout)
    if err:
        return None, err
    if not isinstance(d, dict) or not d.get("host"):
        return None, "RainViewer sent no tile host"
    host = str(d["host"]).rstrip("/")
    past = ((d.get("radar") or {}).get("past") or [])
    frames = []
    for f in past:
        path = (f or {}).get("path")
        when = (f or {}).get("time")
        if not path or when is None:
            continue
        frames.append({"time": int(when),
                       "url": f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_0.png"})
    return frames, None


def geocode(name, count=8, timeout=10):
    """(places, error) from Open-Meteo's keyless geocoder.

    Each place is {name, admin1, country, lat, lon}. An empty list means the
    geocoder answered and knows nowhere by that name.
    """
    text = (name or "").strip()
    if len(text) < 2:
        return [], None
    url = ("https://geocoding-api.open-meteo.com/v1/search?"
           + urllib.parse.urlencode({"name": text, "count": count,
                                     "language": "en", "format": "json"}))
    d, err = _third_party(url, timeout)
    if err:
        return None, err
    out = []
    for r in ((d or {}).get("results") or []):
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        out.append({"name": r.get("name") or "?",
                    "admin1": r.get("admin1") or "",
                    "country": r.get("country") or "",
                    "lat": float(r["latitude"]), "lon": float(r["longitude"])})
    return out, None


def point_forecast(lat, lon, timeout=12):
    """(forecast, error) for any point, in the /api/weather/multi shape.

    Reshaped into `{"current": {...}, "daily": {...}}` so the Bay tab's
    renderers draw an arbitrary point without a second set of them. Asks
    Open-Meteo for exactly the fields the dashbored server asks for, in the
    same units - Fahrenheit and mph - so a tapped point and a named station
    are directly comparable. Three daily rows and no hourly series, because
    that is all the rest of the page has and a strip that only appeared for
    tapped points would be a different promise.
    """
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": f"{float(lat):.4f}", "longitude": f"{float(lon):.4f}",
               "current": ("temperature_2m,apparent_temperature,weather_code,"
                           "wind_speed_10m,wind_gusts_10m,"
                           "relative_humidity_2m,precipitation"),
               "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,wind_speed_10m_max"),
               "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
               "timezone": "auto", "forecast_days": 3}))
    d, err = _third_party(url, timeout)
    if err:
        return None, err
    if not isinstance(d, dict) or d.get("error"):
        reason = (d or {}).get("reason") if isinstance(d, dict) else None
        return None, str(reason or "Open-Meteo refused the request")
    return {"current": d.get("current") or {}, "daily": d.get("daily") or {},
            "lat": d.get("latitude", lat), "lon": d.get("longitude", lon)}, None


def nowplaying():
    """What Navidrome is actually playing, or an honest statement that it is
    not.

    This card showed a hardcoded "Midnight City - M83" to everyone forever,
    because NOWPLAYING was never wired to a real source and fell through to
    mock.py. The server has had a real Subsonic probe all along, arriving as
    the `media` widget on whichever machine runs Navidrome.

    Never returns None. Returning None would hand the card back to the mock
    constant, which is the exact failure being fixed: a dashboard that
    invents a song is worse than one that says nothing is playing.
    """
    d = _get("/api/dashboard")
    if d is None:
        return {"title": "Not connected", "artist": "no link to the server",
                "playing": False}
    media = None
    for m in (d or {}).values():
        w = (m.get("widgets") or {}).get("media")
        if w:
            media = w
            break
    if media is None:
        return {"title": "No media source", "artist": "no Navidrome machine",
                "playing": False}
    if media.get("status") == "offline":
        return {"title": "Navidrome offline", "playing": False,
                "artist": str(media.get("error") or "unreachable")[:60]}
    sessions = media.get("now_playing") or []
    if media.get("status") != "playing" or not sessions:
        return {"title": "Nothing playing", "artist": media.get("source") or "Navidrome",
                "playing": False}
    s = sessions[0]
    artist = " · ".join(x for x in (s.get("artist"), s.get("album")) if x)
    return {"title": s.get("title") or "Unknown track", "playing": True,
            "artist": artist or (media.get("source") or "Navidrome"),
            "image": s.get("image") or ""}


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
    root = ""
    for s in (d.get("items") or []):
        is_sub = s.startswith(">") or s.startswith("  ")
        text = (s[2:] if s.startswith("> ") else s).strip() or s
        # The website keys a sub-task as "<root> / <sub>", not by its raw
        # template line. Keying by the raw line made the phone and the
        # browser disagree about every single sub-task.
        if is_sub:
            key = f"{root} / {text}" if root else text
        else:
            key = text
            root = text
        items.append({"t": text, "sub": is_sub,
                      "done": bool(checks.get(key)), "_key": key})
    return {"date": d.get("date", date),
            "pain": d.get("pain") if d.get("pain") is not None else 0,
            "spoons": d.get("spoons") if d.get("spoons") is not None else 3,
            "items": items, "wx_call": None}


def wat2do_save(state):
    """Save a day's checks, pain and spoons. Returns (ok, message).

    Coalesced per day: an afternoon of ticking boxes with no signal becomes
    one queued write carrying the final state of that day, not forty.
    """
    date = state.get("date") or time.strftime("%Y-%m-%d")
    checks = {}
    for it in state.get("items", []):
        checks[it.get("_key", it.get("t", ""))] = bool(it.get("done"))
    payload = {"date": date, "checks": checks, "pain": state.get("pain"),
               "spoons": state.get("spoons")}
    path = f"/api/wat2do/state?date={date}"
    doc = _cached(path)
    doc = dict(doc) if isinstance(doc, dict) else {}
    doc["date"] = date
    doc["checks"] = {**(doc.get("checks") or {}), **checks}
    doc["pain"], doc["spoons"] = payload["pain"], payload["spoons"]
    if not doc.get("items"):
        doc["items"] = [k for k in checks if k]
    return _queue_write("/api/wat2do/save", json_body=payload, timeout=10,
                        local={path: doc}, coalesce_key=f"wat2do:{date}").pair


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
    d = _get(FOODZEN_PATH)
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
            "pantry_items": pitems if isinstance(pitems, list) else [],
            # The website has six FoodZen tabs; the app only ever read enough
            # for one. These are the collections the other five are built
            # from - list templates, the recipe vault and meal plan, and the
            # purchase history behind spending and the frequency radar.
            "lists": lists,
            "templates": d.get("templates") or [],
            "recipes": d.get("recipes") or [],
            "purchases": d.get("purchases") or [],
            "eatzen": d.get("eatzen") or [],
            "bpm": d.get("bpm") or []}


# ---------------------------------------------------------------- FoodZen writes
# The server saves one whole collection at a time (POST {kind, data}), so every
# mutation is read-modify-write. It re-reads immediately before writing when it
# can, and queues the result as one item per collection - see the CONFLICTS
# note in store.py for what that costs when the website edits the same list.

def foodzen_state(force=True):
    """Raw, shape-normalised FoodZen state, or None if this device has none.

    `force` asks the server for a fresh copy first, as it always did; with no
    signal it falls back to the device's copy instead of failing, which is
    what makes an offline edit possible at all.
    """
    d = _reread(FOODZEN_PATH) if force else _get(FOODZEN_PATH)
    return None if d is None else _fz.normalise_state(d)


def _foodzen_queue(kind, data, state=None):
    """Queue one whole-collection FoodZen write, applied locally.

    Coalesced per collection: ten edits to the grocery list with no signal
    collapse to one queued write carrying the final list, because the wire
    format is "here is the whole list" and only the last one matters.
    """
    if state is None:
        state = foodzen_state(force=False) or _fz.blank_state()
    local = dict(state)
    local[kind] = data
    return _queue_write("/api/foodzen/save", json_body={"kind": kind, "data": data},
                        local={FOODZEN_PATH: local}, coalesce_key=f"foodzen:{kind}")


def foodzen_save(kind, data):
    if kind not in _fz.KINDS:
        return False
    return _foodzen_queue(kind, data).ok


def _foodzen_apply(mutate):
    """Read state, apply `mutate`, queue only the collections it changed.

    Returns (ok, message). A failure is reported rather than swallowed so the
    UI can say the write did not land instead of showing it as applied, and a
    write made with no signal says so rather than claiming the server has it.
    """
    state = foodzen_state(force=True)
    if state is None:
        return False, "no FoodZen copy on this device yet - connect once first"
    try:
        changed = mutate(state)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if not changed:
        return True, "no change"
    queued = False
    for kind in sorted(changed):
        if kind not in _fz.KINDS:
            # A kind the server would reject must not reach the outbox: it can
            # never land, so it would sit at the head of the queue forever.
            # Refuse here and say so, rather than showing the write as applied.
            return False, f"saving {kind} failed - the server does not accept it"
        res = _foodzen_queue(kind, state[kind], state)
        if not res.ok:
            return False, f"saving {kind} failed - {res.message}"
        queued = queued or res.queued
    return True, (QUEUED_MESSAGE if queued else "saved")


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


# ---- the five sections the app was missing --------------------------------
# All of these go through _foodzen_apply, so they queue offline and coalesce
# exactly like the grocery writes do.

def foodzen_template_save(name=None):
    """Save the current grocery list as a reusable template."""
    return _foodzen_apply(lambda s: _fz.template_save(s, name))


def foodzen_template_apply(template_id):
    """Add every item of a template to the current list."""
    return _foodzen_apply(lambda s: _fz.template_apply(s, template_id))


def foodzen_recipe_save(recipe):
    """Put a recipe into the vault. Used by Scrape once you approve it."""
    return _foodzen_apply(lambda s: _fz.recipe_save(s, recipe))


def foodzen_recipe_to_list(recipe_id):
    """Add a saved recipe's ingredients to the grocery list."""
    return _foodzen_apply(lambda s: _fz.recipe_to_list(s, recipe_id))


def foodzen_purchase_save(purchase):
    """Record a parsed receipt, which is what Spending and Freq radar read."""
    return _foodzen_apply(lambda s: _fz.purchase_save(s, purchase))


def foodzen_scrape(url):
    """(recipe, error). Ask the server to fetch and parse a recipe URL.

    Not queueable and not cacheable: it is a live fetch of someone else's
    page, so offline it fails honestly rather than pretending.
    """
    if not STATUS_LIVE():
        return None, "needs a connection - it fetches the page live"
    data = _post("/api/foodzen/scrape", {"url": url})
    if data is None:
        return None, _describe_failure("/api/foodzen/scrape")
    if data.get("error"):
        return None, str(data["error"])
    return data, None


def foodzen_receipt(text, store="", date=None):
    """(parsed, error). Ask the server to turn receipt text into priced rows."""
    if not STATUS_LIVE():
        return None, "needs a connection - the parser runs on the server"
    body = {"mode": "text", "text": text, "store": store}
    if date:
        body["date"] = date
    data = _post("/api/foodzen/receipt", body)
    if data is None:
        return None, _describe_failure("/api/foodzen/receipt")
    if data.get("error"):
        return None, str(data["error"])
    return data, None


# ------------------------------------------------------------ maps / rides
def mbtiles_path():
    """Where the offline map archive lives, or None if none is installed.

    Checked in order of how deliberate each location is: an explicit setting
    wins, then a file shipped inside the app bundle, then one downloaded
    beside the app. Returning None is a normal outcome - the map falls back
    to online tiles and says so.
    """
    import os
    from pathlib import Path
    candidates = []
    override = os.environ.get("NVII_MBTILES")
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve().parent.parent
    candidates += [here / "assets" / "maps" / "bayarea.mbtiles",
                   here / "maps" / "bayarea.mbtiles",
                   Path.home() / ".nvii-dash" / "bayarea.mbtiles"]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _gpx(name, points):
    """Turn recorded fixes into GPX the server and every other tool can read.

    Speed goes in a GPX extension rather than being dropped: it comes from
    the GPS receiver's own Doppler calculation and is better than anything
    recomputed from the points later.
    """
    import html as _h
    import time as _t
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="NVII-DASH" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <metadata><name>{_h.escape(name)}</name></metadata>\n"
        f"  <trk><name>{_h.escape(name)}</name><trkseg>\n")
    rows = []
    for q in points:
        stamp = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(q.get("t") or 0))
        bits = [f'    <trkpt lat="{q["lat"]}" lon="{q["lon"]}">']
        if q.get("alt") is not None:
            bits.append(f'<ele>{q["alt"]:.1f}</ele>')
        bits.append(f"<time>{stamp}</time>")
        if q.get("speed") is not None:
            bits.append(f'<extensions><speed>{q["speed"]:.2f}</speed></extensions>')
        bits.append("</trkpt>")
        rows.append("".join(bits))
    return head + "\n".join(rows) + "\n  </trkseg></trk>\n</gpx>\n"


def ride_save(name, points, distance_m=0.0):
    """Upload a recorded track as GPX. Returns (ok, message).

    Never coalesced: each recording is its own file and its own afternoon.
    Two rides queued behind a tunnel both go up.
    """
    if not points:
        return False, "no points recorded"
    payload = {"filename": f"{name}.gpx", "content": _gpx(name, points)}
    res = _queue_write("/api/rides/upload", json_body=payload, timeout=60,
                       invalidate=("/api/rides",))
    if not res.ok:
        return False, res.message
    tail = " - on this device, will sync" if res.queued else ""
    return True, f"{len(points)} points, {distance_m / 1609.344:.1f} mi{tail}"


# --------------------------------------------------------- EatZen / BPM
# The server owns the food table and the parser, so the app stays thin and
# the phone and the browser can never disagree about a sodium figure.

DASH_TARGETS = (1500, 2300)


def nutrition_search(query, limit=10):
    """Food-name autocomplete against the server's table (USDA + your own)."""
    if not (query or "").strip():
        return []
    d = _get(f"/api/nutrition/search?q={urllib.parse.quote(query)}&limit={int(limit)}")
    return [] if d is None else (d.get("results") or [])


def eatzen_parse(text, llm=False, model="", machine=""):
    """Price a line of food. Returns the server's result dict, or None.

    `llm` lets the server use the local model to pick among candidates it
    shortlisted; numbers are computed server-side either way. Not queued:
    this asks a question, it does not save anything.
    """
    payload = {"text": text}
    if llm:
        payload.update({"llm": True, "model": model or "gemma4:latest",
                        "machine": machine or ""})
    return _post("/api/eatzen/parse", payload, timeout=90 if llm else 20)


def eatzen_entries():
    st = foodzen_state(force=False)
    return [] if st is None else (st.get("eatzen") or [])


def eatzen_log(entry):
    """Append one intake entry."""
    return _foodzen_apply(lambda s: (s.setdefault("eatzen", []).insert(0, entry),
                                     {"eatzen"})[1])


def eatzen_delete(entry_id):
    def mutate(state):
        before = state.get("eatzen") or []
        state["eatzen"] = [e for e in before if e.get("id") != entry_id]
        return {"eatzen"} if len(state["eatzen"]) != len(before) else set()
    return _foodzen_apply(mutate)


def eatzen_day_total(entries, day):
    """Sodium logged on a given YYYY-MM-DD, for the BPM cross-reference."""
    return sum((e.get("totals") or {}).get("na") or 0
               for e in entries if str(e.get("ts") or "")[:10] == day)


def custom_food_save(record):
    """Teach the server a food it does not know, from its label."""
    def mutate(state):
        foods = [f for f in (state.get("custom_foods") or [])
                 if f.get("n") != record.get("n")]
        foods.append(record)
        state["custom_foods"] = foods
        return {"custom_foods"}
    return _foodzen_apply(mutate)


def bpm_readings():
    st = foodzen_state(force=False)
    rows = [] if st is None else (st.get("bpm") or [])
    return sorted(rows, key=lambda b: str(b.get("ts") or ""), reverse=True)


def bpm_add(reading):
    return _foodzen_apply(lambda s: (s.setdefault("bpm", []).insert(0, reading),
                                     {"bpm"})[1])


def bpm_delete(reading_id):
    def mutate(state):
        before = state.get("bpm") or []
        state["bpm"] = [b for b in before if b.get("id") != reading_id]
        return {"bpm"} if len(state["bpm"]) != len(before) else set()
    return _foodzen_apply(mutate)


def bp_category(sys_, dia):
    """American Heart Association ranges. Information, not advice.

    Kept identical to the website's bpCategory so a reading is never labelled
    one thing on the phone and another in the browser.
    """
    try:
        sys_, dia = float(sys_), float(dia)
    except (TypeError, ValueError):
        return ("normal", "—")
    if sys_ >= 180 or dia >= 120:
        return ("crisis", "hypertensive crisis")
    if sys_ >= 140 or dia >= 90:
        return ("high2", "stage 2")
    if sys_ >= 130 or dia >= 80:
        return ("high1", "stage 1")
    if sys_ >= 120:
        return ("elev", "elevated")
    return ("normal", "normal")


# -------------------------------------------------------------------- journal
JOURNAL_CATEGORIES = ["Mental-Health", "Trip-Reports", "Mechanicry",
                      "New-Projects", "Dr-Visits", "Medications"]


def journal_read(rel):
    d = _get(f"/api/journal/read?path={urllib.parse.quote(rel)}")
    return None if d is None else d.get("content", "")


def journal_new(category, title):
    """Create an entry. Returns (ok, message_or_path).

    Never coalesced: this appends, the server assigns the filename, and two
    entries written offline are two entries - collapsing them would silently
    throw one away.
    """
    if category not in JOURNAL_CATEGORIES:
        return False, f"unknown category {category!r}"
    if not (title or "").strip():
        return False, "title required"
    res = _queue_write("/api/journal/new",
                       params={"category": category, "title": title.strip()},
                       invalidate=("/api/journal/tree",))
    if not res.ok:
        return False, res.message
    if res.queued:
        return True, QUEUED_MESSAGE
    return True, (res.response or {}).get("path", "")


def journal_save(rel, content):
    """Save an entry's text. Coalesced per file - the last version wins."""
    read_path = f"/api/journal/read?path={urllib.parse.quote(rel)}"
    return _queue_write("/api/journal/save", json_body={"path": rel, "content": content},
                        local={read_path: {"content": content}},
                        coalesce_key=f"journal:save:{rel}",
                        invalidate=("/api/journal/tree",)).pair


def journal_delete(rel):
    """Delete an entry. Coalesced per file - deleting twice is deleting once."""
    local = None
    tree = _journal_tree_without(rel)
    if tree is not None:
        local = {"/api/journal/tree": tree}
    return _queue_write("/api/journal/delete", params={"path": rel},
                        local=local, coalesce_key=f"journal:delete:{rel}",
                        invalidate=(f"/api/journal/read?path={urllib.parse.quote(rel)}",)).pair


def _journal_tree_without(rel):
    """The cached journal tree with `rel` removed, so the row goes offline too.

    Path composition matches journal() below; a mismatch just means the row
    stays on screen until the delete reaches the server, which is the old
    behaviour, not a new bug.
    """
    tree = _cached("/api/journal/tree")
    if not isinstance(tree, dict):
        return None
    hit = False
    for c in (tree.get("categories") or []):
        base = c.get("path") or c.get("name", "")
        kept = [f for f in (c.get("children") or [])
                if f"{base}/{f.get('path', '')}" != rel]
        hit = hit or len(kept) != len(c.get("children") or [])
        c["children"] = kept
    return tree if hit else None


# --------------------------------------------------------------------- garage
GARAGE_RECORD_TYPES = ("mileage", "services", "expenses")

# The three note folders the server will create a markdown file in. It
# rejects anything else with HTTP 400 "bad section" (BIKE_SUBDIRS), so the
# picker offers exactly these rather than a free-text box that can only fail.
GARAGE_NOTE_SECTIONS = ("Notes", "Mods", "Refs")

# The fields each record type actually has, in the order the website's form
# and table use them, and which of them are numbers.
#
# This is here rather than in the page because the app was writing
# `{"label": ..., "odo": ..., "amount": ...}` for all three types. `label`
# is read by nothing - not the website's tables, not the server's stats -
# and `amount` only exists on expenses, so a service logged from the app
# showed up on the website as a date, an odometer and four blank columns,
# and contributed nothing to "total spent". Naming the schema once means
# the app and the website cannot drift again without this constant changing.
GARAGE_RECORD_FIELDS = {
    "mileage": ("date", "odo", "gallons", "price_per_gal", "cost", "notes"),
    "services": ("date", "odo", "type", "parts_cost", "labor_cost", "notes"),
    "expenses": ("date", "category", "vendor", "amount", "notes"),
}
GARAGE_NUMERIC_FIELDS = frozenset((
    "odo", "gallons", "price_per_gal", "cost", "parts_cost", "labor_cost",
    "amount"))

# The spec sheet, in the website's order. `garageSaveSpec` sends all sixteen
# every time - it is a whole-object write - so a key left out here would be
# erased from the server's _BIKE.json the first time the app saved.
GARAGE_SPEC_FIELDS = ("year", "make", "model", "nickname", "cc", "condition",
                      "reg_expires", "vin", "plate", "color", "purchased",
                      "purchase_price", "odo_at_purchase", "odo_current",
                      "insurance", "notes")
# Kept as numbers when they parse as one, exactly as the website does, so a
# year does not come back from the server as the string "1993".
GARAGE_SPEC_NUMERIC = frozenset(("year", "cc", "odo_at_purchase",
                                 "odo_current", "purchase_price"))


def _garage_records_path(bike, rtype):
    return f"/api/garage/records?bike={urllib.parse.quote(bike)}&type={rtype}"


def garage_tree():
    """The server's bikes: label, per-type counts and the note folders.

    Deliberately not `M.BIKES`. That accessor is in `_MAP`, so when
    `garage_bikes()` returns None - which is what "no server" looks like -
    module `__getattr__` substitutes `mock.BIKES`: two invented Hondas with
    invented odometers and an invented "running" status. This returns None
    and lets the page say so.
    """
    d = _get("/api/garage/tree")
    if not isinstance(d, dict):
        return None
    return d.get("bikes") or []


def garage_stats(bike):
    """Aggregates for one bike, or None. Values may legitimately be null.

    `mpg_avg`, `cost_per_mile`, `miles_since_service` and
    `days_since_service` are null until there is enough logged to compute
    them, and null is not zero: "0 miles since service" is a claim that the
    bike was serviced today.
    """
    d = _get(f"/api/garage/stats?bike={urllib.parse.quote(bike)}")
    return d if isinstance(d, dict) else None


def garage_spec(bike):
    """The spec sheet for one bike, or None. `{}` means "none saved yet"."""
    d = _get(f"/api/garage/spec?bike={urllib.parse.quote(bike)}")
    if not isinstance(d, dict):
        return None
    spec = d.get("spec")
    return spec if isinstance(spec, dict) else {}


def garage_note_new(bike, section, title):
    """Create a markdown note under Notes/Mods/Refs. (ok, path-or-message).

    Never coalesced, for the same reason journal_new is not: the server
    names the file, and two notes written offline are two notes.
    """
    if section not in GARAGE_NOTE_SECTIONS:
        return False, f"unknown section {section!r}"
    if not (title or "").strip():
        return False, "title required"
    if not (bike or "").strip():
        return False, "pick a bike"
    res = _queue_write("/api/garage/new",
                       params={"bike": bike, "sub": section,
                               "title": title.strip()},
                       invalidate=("/api/garage/tree",))
    if not res.ok:
        return False, res.message
    if res.queued:
        return True, QUEUED_MESSAGE
    # /api/garage/new answers {"path": rel} and carries no "ok" key, so the
    # path is the only confirmation there is that the file exists.
    return True, (res.response or {}).get("path", "created")


def garage_records(bike, rtype):
    if rtype not in GARAGE_RECORD_TYPES:
        return None
    d = _get(_garage_records_path(bike, rtype))
    return None if d is None else (d.get("records") or [])


def garage_record_add(bike, rtype, record):
    """Append a mileage / service / expense record. Returns (ok, message).

    The server assigns `id` and `created_at` under a lock (garage_records_add),
    so the app deliberately does not invent them - and for the same reason
    this write is never coalesced: two services logged offline are two
    services, and collapsing them by bike would lose one.
    """
    if rtype not in GARAGE_RECORD_TYPES:
        return False, f"unknown record type {rtype!r}"
    if not isinstance(record, dict) or not record:
        return False, "empty record"
    path = _garage_records_path(bike, rtype)
    local = None
    doc = _cached(path)
    if isinstance(doc, dict) and isinstance(doc.get("records"), list):
        doc["records"] = list(doc["records"]) + [dict(record)]
        local = {path: doc}
    return _queue_write("/api/garage/records/add", params={"bike": bike, "type": rtype},
                        body=json.dumps(record), local=local,
                        invalidate=("/api/garage/tree",)).pair


def garage_read(rel):
    d = _get(f"/api/garage/read?path={urllib.parse.quote(rel)}")
    return None if d is None else d.get("content", "")


def garage_save(rel, content):
    """Save a garage note. Coalesced per file - the last version wins."""
    read_path = f"/api/garage/read?path={urllib.parse.quote(rel)}"
    return _queue_write("/api/garage/save", json_body={"path": rel, "content": content},
                        local={read_path: {"content": content}},
                        coalesce_key=f"garage:file:{rel}",
                        invalidate=("/api/garage/tree",)).pair


def garage_spec_save(bike, spec):
    """Save a bike's spec sheet. Coalesced per bike - it is a whole-object write.

    The edit is now applied to the cached `/api/garage/spec` response too.
    It could not be before, because nothing in the app read that endpoint
    and the wrapper shape was unknown; the Spec tab reads it now, and the
    server's answer has been curled - `{"spec": {...}}`. Without this, a
    spec edited with the radio off saved into the outbox and then re-read as
    the old values, which looks exactly like the write being lost.
    """
    spec_path = f"/api/garage/spec?bike={urllib.parse.quote(bike)}"
    return _queue_write("/api/garage/spec/save", params={"bike": bike},
                        body=json.dumps(spec), local={spec_path: {"spec": dict(spec)}},
                        coalesce_key=f"garage:spec:{bike}",
                        invalidate=()).pair


# ---------------------------------------------------------------------- trips
def trip_save(trip):
    """Save a whole trip object. The server requires id and name.

    Coalesced per trip id: editing one trip all afternoon is one queued write.
    """
    if not isinstance(trip, dict) or not trip.get("id") or not trip.get("name"):
        return False, "id and name required"
    local = None
    cached = _cached("/api/trips")
    if isinstance(cached, list):
        merged = [t for t in cached
                  if not isinstance(t, dict) or t.get("id") != trip["id"]]
        local = {"/api/trips": merged + [dict(trip)]}
    return _queue_write("/api/trips/save", json_body=trip, local=local,
                        coalesce_key=f"trip:{trip['id']}",
                        invalidate=("/api/trips",)).pair


# ---------------------------------------------------------------------- vault
def vault_save(host, rel, content, lib=None):
    """Write a note to a machine's vault. Content goes in the raw body.

    Coalesced per (machine, path): the app has no way to read a vault back,
    so the queued copy is the only copy and the newest one is the one to send.
    """
    if not host or not rel:
        return False, "host and path required"
    return _queue_write("/api/vault/save", params={"host": host, "path": rel, "lib": lib},
                        body=content, coalesce_key=f"vault:{host}:{rel}").pair


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


# ------------------------------------------------------------------- the lab
#
# Everything below reads or drives the machines. Three rules run through it:
#
# * A request the *server* answered with an error is not the same thing as the
#   server being unreachable. `_get_said` keeps them apart, because `_get`
#   collapses both into None and additionally marks the whole app offline -
#   which is plainly wrong when the answer was "Gitea HTTP 404: Not Found".
# * Nothing here is ever queued for later. Waking, rebooting or restarting a
#   unit is an instruction about *now*; replaying one an hour later when the
#   signal comes back would act on a machine nobody asked about at that
#   moment. They refuse offline and say why.
# * `dotfile_save` is the exception, and goes through the outbox: it is a
#   document, the newest copy is the one to send, and the server applies it
#   whenever it arrives.

def _get_said(path, timeout=6):
    """GET `path` -> (data, error). `error` is the server's own words, or None.

    Unlike `_get` this never consults or fills the store and never flips the
    connection status on an HTTP status code. A 404 from /api/projects/tree
    means "no such repo", not "the phone has no signal", and treating it as
    the latter took the whole app offline mid-browse.
    """
    req = urllib.request.Request(config.SERVER_URL + path, method="GET")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        said = _server_said(e)
        if e.code in _GATEWAY_CODES:
            config._remember(config.SERVER_URL, False, config._describe(e))
        return None, said or config._describe(e)
    except Exception as e:  # noqa: BLE001 - the connection really did fail
        detail = config._describe(e)
        config._remember(config.SERVER_URL, False, detail)
        return None, detail
    config._remember(config.SERVER_URL, True, "connected")
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None, "responded, but not with dashbored JSON"
    if isinstance(data, dict) and data.get("error"):
        return data, str(data["error"])
    return data, None


def _live_post(path, *, json_body=None, body=None, timeout=20):
    """POST now or not at all -> (data, error). Never queued. See the note above."""
    if not _worth_trying():
        return None, (config.STATUS.get("detail")
                      or "no connection to the server, and this cannot be queued")
    data = json.dumps(json_body).encode() if json_body is not None else _body_bytes(body)
    headers = {"Content-Type": "application/json"} if json_body is not None else {}
    req = urllib.request.Request(config.SERVER_URL + path, data=data,
                                 headers=headers, method="POST")
    try:
        with config._opener.open(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        said = _server_said(e)
        if e.code in _GATEWAY_CODES:
            config._remember(config.SERVER_URL, False, config._describe(e))
        return None, said or config._describe(e)
    except Exception as e:  # noqa: BLE001
        detail = config._describe(e)
        config._remember(config.SERVER_URL, False, detail)
        return None, detail
    config._remember(config.SERVER_URL, True, "connected")
    try:
        out = json.loads(raw or "{}")
    except ValueError:
        return None, "responded, but not with dashbored JSON"
    if isinstance(out, dict) and out.get("error"):
        return out, str(out["error"])
    return out, None


def dashboard():
    """Raw /api/dashboard: {machine id: {display, color, widgets: {...}}}.

    The timeout is generous because the server SSHes to every machine to build
    it. Shared with `machines()` through the store, so asking for both costs
    one request.
    """
    d = _get("/api/dashboard", timeout=25)
    return d if isinstance(d, dict) else None


def machine_widget(dash, mid, name):
    """One widget dict for one machine, or {}. Never raises on a odd payload."""
    m = (dash or {}).get(mid)
    if not isinstance(m, dict):
        return {}
    w = (m.get("widgets") or {}).get(name)
    return w if isinstance(w, dict) else {}


def widget_alive(w):
    """Whether a widget carries data rather than an excuse.

    SP3 answers every widget with ``{"error": "unreachable"}``, and that is a
    non-empty dict - so `bool(w)` called it alive and the machine showed a
    green light while it was down. Same shape, same trap, in every widget.
    """
    return bool(w) and not w.get("error")


def machine_config():
    """/api/config: {id: {display, host, user, color}}. Hosts for SSH and power."""
    d = _get("/api/config", timeout=8)
    return d if isinstance(d, dict) else None


def docker_list():
    """/api/docker_list -> (hosts, error).

    Shape, verified against the live server: ``{mid: {"display", "color",
    "containers": [{"name", "image", "state", "status", "ports"}]}}``. `ports`
    is docker's own one-line string, not a dict.
    """
    return _get_said("/api/docker_list", timeout=30)


def docker_ports(raw):
    """Readable published ports from docker's ``ps`` string.

    IPv4 and IPv6 publish the same mapping twice ("0.0.0.0:8080->80/tcp,
    :::8080->80/tcp"); showing it once is the whole point. A dict is accepted
    too in case a caller ever passes docker's JSON shape.
    """
    if not raw:
        return ""
    if isinstance(raw, dict):
        return ", ".join(str(k) for k in raw)
    seen = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        host, sep, rest = part.rpartition(":")
        mapped = part
        if sep and "->" in rest:
            out_port, _, inner = rest.partition("->")
            mapped = f"{out_port}->{inner}"
        if mapped not in seen:
            seen.append(mapped)
    return ", ".join(seen)


POWER_ACTIONS = ("wake", "shutdown", "reboot")


def power(mid, action):
    """Wake / shut down / reboot a machine now. Returns (ok, message).

    Live only. The server answers 200 ``{"ok": true, "output": ...}`` even
    when the SSH call reported "WoL tools not installed", so the output is
    handed back verbatim rather than summarised into a success.
    """
    if action not in POWER_ACTIONS:
        return False, f"unknown power action '{action}'"
    if not mid:
        return False, "no machine given"
    data, err = _live_post(f"/api/power/{action}/{urllib.parse.quote(mid)}")
    if err:
        return False, err
    return True, str((data or {}).get("output") or "").strip() or "(the server said nothing)"


SERVICE_ACTIONS = ("start", "stop", "restart", "status")


def service_control(host, service, action):
    """docker-then-systemctl on one machine. Returns (ok, output).

    ``ok`` from this endpoint means "the SSH call completed", including when
    it completed by printing "Unit caddy.service could not be found" - so the
    caller must show the output, not a tick.
    """
    if action not in SERVICE_ACTIONS:
        return False, f"unknown service action '{action}'"
    if not host or not service:
        return False, "machine and unit name are both required"
    data, err = _live_post("/api/service/control",
                           json_body={"host": host, "service": service,
                                      "action": action}, timeout=45)
    if err:
        return False, err
    return True, str((data or {}).get("output") or "").strip() or "(no output)"


def run_command(host, command, timeout=40):
    """Run one command over the server's SSH. Returns (output, error).

    The body is the command itself, not JSON - that is what /api/run reads.
    """
    if not host or not (command or "").strip():
        return None, "machine and command are both required"
    data, err = _live_post("/api/run?host=" + urllib.parse.quote(host),
                           body=command.strip(), timeout=timeout)
    if err:
        return None, err
    return str((data or {}).get("output") or ""), None


def docker_logs(host, container, lines=80):
    """The last `lines` of a container's log, or (None, why)."""
    if not container:
        return None, "no container named"
    return run_command(host, f"docker logs --tail {int(lines)} {container}")


def dotfile_read(host, path):
    """One dotfile's contents from a machine. Returns (text, error)."""
    if not host or not path:
        return None, "machine and path are both required"
    data, err = _get_said(
        f"/api/dotfile/read?host={urllib.parse.quote(host)}"
        f"&path={urllib.parse.quote(path)}", timeout=20)
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "the server sent no file"
    return str(data.get("content") or ""), None


def dotfile_save(host, path, content):
    """Write a dotfile back. Queued, because it is a document, not an order.

    Coalesced per (machine, path): the app holds no copy of the file, so the
    queued one is the only one and the newest wins.
    """
    if not host or not path:
        return False, "machine and path are both required"
    return _queue_write("/api/dotfile/save",
                        params={"host": host, "path": path}, body=content,
                        coalesce_key=f"dotfile:{host}:{path}").pair


# ----------------------------------------------------------------- the extras
def projects_list():
    """Every repo Gitea knows, raw -> (list, error).

    Kept raw on purpose: `projects()` above flattens to three fields for the
    old list, and the detail view needs full_name, default_branch and the
    counts the flattening threw away.
    """
    d, err = _get_said("/api/projects", timeout=15)
    if err:
        return None, err
    return (d, None) if isinstance(d, list) else (None, "the server sent no repo list")


def repo_owner_name(repo):
    """(owner, name) for a repo row, from full_name and falling back to owner.login."""
    full = str((repo or {}).get("full_name") or "")
    if "/" in full:
        owner, _, name = full.partition("/")
        return owner, name
    owner = ((repo or {}).get("owner") or {}).get("login") or ""
    return str(owner), str((repo or {}).get("name") or "")


def project_tree(owner, repo):
    """/api/projects/tree -> ({info, branch, tree, commits}, error)."""
    if not owner or not repo:
        return None, "owner and repo are both required"
    data, err = _get_said(
        f"/api/projects/tree?owner={urllib.parse.quote(owner)}"
        f"&repo={urllib.parse.quote(repo)}", timeout=20)
    if err:
        return None, err
    return (data, None) if isinstance(data, dict) else (None, "the server sent no tree")


def project_file(owner, repo, path):
    """One file out of a repo -> (text, error). The server base64-decodes it."""
    if not (owner and repo and path):
        return None, "owner, repo and path are all required"
    data, err = _get_said(
        f"/api/projects/file?owner={urllib.parse.quote(owner)}"
        f"&repo={urllib.parse.quote(repo)}&path={urllib.parse.quote(path)}", timeout=20)
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "the server sent no file"
    return str(data.get("decoded") or ""), None


def future_curated():
    """/api/future/curated -> (dict of named lists, error).

    Every value is a list of {name, note, lat, lon, ...} except
    ``events_stale_h``, a float saying how old the scraped events are.
    """
    d, err = _get_said("/api/future/curated", timeout=20)
    if err:
        return None, err
    return (d, None) if isinstance(d, dict) else (None, "the server sent no curated lists")


# ------------------------------------------------------------------------- ai
AI_PROVIDERS = (("ollama", "Ollama (LAN)"), ("openrouter", "OpenRouter (free)"),
                ("zen", "OpenCode Zen (free)"))

# The website sends this with every message; without it the model has no idea
# what machines it is being asked about.
AI_SYSTEM = ("You are NVII, a helpful AI assistant with context about this "
             "homelab. The lab includes: Tower (blavksaba), Pi 5 (ai-tp), Yoga "
             "workstation (void), Media Pi (lil-faegola), SP3 downloads box. "
             "Be concise and helpful.")


def ai_models(provider):
    """Model ids for a hosted provider -> (list, error).

    Ollama is not one of these: its models are per-machine and already in the
    dashboard payload, under widgets.ollama.models.
    """
    if provider == "ollama":
        return None, "ollama models come from the machine, not from this endpoint"
    d, err = _get_said("/api/ai/models?provider=" + urllib.parse.quote(provider or ""),
                       timeout=12)
    if err:
        return None, err
    models = (d or {}).get("models") if isinstance(d, dict) else d
    if not isinstance(models, list) or not models:
        return None, "the server listed no models for this provider"
    return [str(m) if isinstance(m, str) else str(m.get("id") or m.get("name") or "")
            for m in models if m], None


def ollama_models(dash, mid):
    """Model names installed on one machine, from the dashboard payload."""
    w = machine_widget(dash, mid, "ollama")
    if not widget_alive(w):
        return []
    return [str(m.get("name") or "") for m in (w.get("models") or [])
            if isinstance(m, dict) and m.get("name")]


def ai_say(provider, model, messages, host=None):
    """One turn of chat -> (reply, error). The server's own words on failure.

    `messages` are {role, content}. The system prompt is added here so every
    caller gets the same one. Ollama goes to a different endpoint and needs
    the machine to run on; the hosted providers do not take one.

    Live only, and deliberately: a queued question answered tomorrow is not an
    answer, and the reply would have nowhere to land.
    """
    norm = [{"role": "system", "content": AI_SYSTEM}]
    for m in messages or []:
        norm.append({"role": m.get("role", "user"),
                     "content": m.get("content", m.get("text", ""))})
    if provider == "ollama":
        if not host:
            return None, "pick which machine should run the model"
        payload = {"host": host, "model": model, "messages": norm}
        data, err = _live_post("/api/ollama/chat", json_body=payload, timeout=120)
    else:
        payload = {"provider": provider, "model": model, "messages": norm}
        data, err = _live_post("/api/ai/chat", json_body=payload, timeout=120)
    if err:
        return None, err
    d = data or {}
    reply = d.get("response") or d.get("content")
    if not reply:
        msg = d.get("message")
        if isinstance(msg, dict):
            reply = msg.get("content")
        elif isinstance(msg, str):
            reply = msg
    if not reply:
        choices = d.get("choices") or []
        if choices and isinstance(choices[0], dict):
            reply = (choices[0].get("message") or {}).get("content")
    if not reply:
        return None, "the provider answered, but with no text in it"
    return str(reply), None


def STATUS_LIVE():
    """True when the last request actually reached the server.

    Pages use this to decide whether to offer editing at all, and to show the
    sample-data banner. Named in caps because module __getattr__ below falls
    through to mock for anything it does not recognise, and a lowercase helper
    reads like one of the data accessors.

    It answers about the *connection*, not about the data: with the store in
    place a page can hold real, honest, previously-fetched data while this is
    False. `provenance(path)` is the one to ask about the data.

    Two things know whether we are connected now - the last request, which
    updates config.STATUS, and the store, which is also told by its own probe
    and by a flush. The newer verdict wins, so the background probe that
    notices signal coming back is not ignored just because no page happened
    to fetch anything since.
    """
    ok = bool(config.STATUS.get("ok"))
    st = _peek()
    if st is None:
        return ok
    try:
        state = st.status
    except Exception:  # noqa: BLE001
        return ok
    if (state["at"] or 0.0) > (config.STATUS.get("at") or 0.0):
        return bool(state["online"])
    return ok


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


# ===========================================================================
# Parity block: Home overview, Feeds, Vault, Journal
#
# Everything below brings Home, Tools > Feeds, Tools > Vault and Tools >
# Journal level with the website. It is a separate block of new functions
# rather than edits to the readers above on purpose: several people were
# working in this file at once, and widening `feeds()` or `weather()` under
# them is the one kind of merge conflict this app can least afford to lose
# quietly.
#
# Nothing here adds an UPPERCASE module constant that resolves data.
# `__getattr__` at the bottom falls through to `mock` for any name this
# module does not define, so a capitalised accessor missing from `_MAP`
# renders sample data forever - that is how a hardcoded "now playing" song
# survived for months. Lower-case functions cannot fall through it.
# ===========================================================================

def local_today():
    """Today, where the phone is standing. Never UTC.

    The website had eleven `toISOString().slice(0,10)` sites, and after about
    4pm Pacific every one of them said tomorrow - Wat2Do opened the wrong
    day's checklist. `time.strftime` with no struct formats localtime, which
    is the whole point of this existing rather than being inlined.
    """
    return time.strftime("%Y-%m-%d")


def feeds_full(force=False):
    """The whole /api/feeds payload, not the first twenty items of it.

    `feeds()` above trims to 20 rows of {src, tag, title, meta} for the old
    card list. The website shows every item - 76 on the live server today -
    filterable by type, each one a link, each with its source's own label and
    colour, under a line naming any source that could not be reached. None of
    that survives the trim, so this hands back the payload as sent:

        {"ts": 1790021777.5,
         "items": [{source, type, title, url, date, iso, desc, tags, img,
                    location}, ...],
         "errors": {source: why}, "sources": {source: {label, color}}}
    """
    path = "/api/feeds?force=1" if force else "/api/feeds"
    d = _get(path, timeout=30 if force else 8)
    if not isinstance(d, dict):
        return None
    return {"ts": d.get("ts"), "items": list(d.get("items") or []),
            "errors": dict(d.get("errors") or {}),
            "sources": dict(d.get("sources") or {})}


def feed_types(items):
    """Which filter chips have anything behind them, in the website's order.

    The website ships All/Local/Events/Routes and grows an RSS chip only when
    an RSS item turns up. A chip that filters to nothing is a dead control, so
    a type with no items is left off rather than shown empty.
    """
    seen = {i.get("type") for i in (items or [])}
    order = (("all", "All"), ("local", "Local"), ("event", "Events"),
             ("route", "Routes"), ("rss", "RSS"))
    return [(k, label) for k, label in order if k == "all" or k in seen]


def feed_filter(items, kind="all"):
    """The items one chip shows. 'all' is every item, in server order."""
    if kind == "all":
        return list(items or [])
    return [i for i in (items or []) if i.get("type") == kind]


# ---------------------------------------------------------------------- vault
VAULT_LIBS = ("DeVault", "vault")
VAULT_DEFAULT_HOST = "ai-tp"


def vault_tree(host=VAULT_DEFAULT_HOST, lib="DeVault"):
    """(tree, error). The tree is a list of {name, type, path[, children]}.

    The server answers a bare JSON *array* on success and an object with an
    `error` key otherwise - "no vault_path configured" for every machine but
    ai-tp today, 404 "unknown machine" for a name it does not have. Those are
    different from "this phone has no signal", and the page says which, so
    they are kept apart here instead of both collapsing to None.

    A file's `path` already comes back relative to the vault root, so it can
    be handed straight to /api/vault/read and /api/vault/save.
    """
    d = _get(_qs("/api/vault/tree", {"host": host, "lib": lib}), timeout=15)
    if d is None:
        return None, (config.STATUS.get("detail") or "server unreachable")
    if isinstance(d, dict):
        return None, str(d.get("error") or "unexpected reply")
    if not isinstance(d, list):
        return None, "unexpected reply"
    return d, None


def vault_flatten(tree):
    """A vault tree as [{depth, dir, name, path, parent}, ...], depth-first.

    Flet has no <details>, so the browser's collapsible tree becomes an
    indented list whose folders fold by path. `parent` is the path of the
    enclosing folder ("" at the root) so a row can be hidden when any
    ancestor is closed.
    """
    out = []

    def walk(items, depth, parent):
        for item in (items or []):
            if not isinstance(item, dict):
                continue
            name = item.get("name") or ""
            path = item.get("path") or name
            is_dir = item.get("type") == "dir"
            out.append({"depth": depth, "dir": is_dir, "name": name,
                        "path": path, "parent": parent})
            if is_dir:
                walk(item.get("children") or [], depth + 1, path)

    walk(tree, 0, "")
    return out


def vault_visible(rows, open_dirs):
    """The rows to draw given which folder paths are open.

    A row shows when every folder above it is open. Checking only its
    immediate parent would leave grandchildren on screen under a closed
    grandparent, which is what collapsing is for.
    """
    opened = set(open_dirs or ())
    closed = set()
    out = []
    for r in rows:
        parent = r.get("parent") or ""
        if parent and (parent in closed or parent not in opened):
            if r["dir"]:
                closed.add(r["path"])
            continue
        if r["dir"] and r["path"] not in opened:
            closed.add(r["path"])
        out.append(r)
    return out


def vault_read(host, lib, rel):
    """A vault note's text, or None. Never invents an empty file."""
    if not host or not rel:
        return None
    d = _get(_qs("/api/vault/read", {"host": host, "lib": lib, "path": rel}),
             timeout=15)
    if not isinstance(d, dict) or d.get("error"):
        return None
    return d.get("content", "")


def vault_write(host, rel, content, lib=None):
    """Save an existing vault note, keeping this device's copy in step.

    `vault_save` above is the blind composer's write: it never read the file,
    so it caches nothing. This one is called from the viewer, which has just
    read it, so the cached read is updated too - reopening the note with no
    signal then shows what you saved rather than what it used to say.

    Coalesced per (host, lib, path): it is a whole-file write, and the newest
    one is the one to send.
    """
    if not host or not rel:
        return False, "host and path required"
    read_path = _qs("/api/vault/read", {"host": host, "lib": lib, "path": rel})
    return _queue_write("/api/vault/save",
                        params={"host": host, "path": rel, "lib": lib},
                        body=content,
                        local={read_path: {"path": rel, "content": content}},
                        coalesce_key=f"vault:{host}:{lib}:{rel}").pair


# -------------------------------------------------------------------- journal
def journal_list():
    """Journal entries with everything the list view needs, or None.

    `journal()` above answers {cat, title, date, path, name} and is what the
    JOURNAL constant resolves to; leaving it alone keeps that contract. The
    website's list also needs the category *key* (to colour and filter by),
    the excerpt (to search), and a sortable timestamp.

    `_TEMPLATE.md` is skipped here as it is there: the server flags it
    `tpl: true`, and it is scaffolding, not an entry.
    """
    d = _get("/api/journal/tree")
    if not isinstance(d, dict):
        return None
    out = []
    for c in (d.get("categories") or []):
        base = c.get("path") or c.get("name", "")
        for f in (c.get("children") or []):
            if f.get("tpl") or f.get("name") == "_TEMPLATE.md":
                continue
            if f.get("type") not in (None, "file"):
                continue
            mtime = f.get("mtime") or ""
            out.append({
                "cat": c.get("name", ""),
                "cat_label": c.get("label", c.get("name", "")),
                "path": f"{base}/{f.get('path', '')}",
                "name": f.get("name", ""),
                "title": f.get("title") or journal_title_from(f.get("name", "")),
                "excerpt": f.get("excerpt") or "",
                "mtime": mtime, "date": mtime[:10],
                "size": f.get("size") or 0, "ts": _journal_ts(mtime)})
    return out


def journal_title_from(filename):
    """Title a `YYYY-MM-DD_some-slug.md` filename, the way the website does."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    if len(stem) > 11 and stem[4:5] == "-" and stem[7:8] == "-" and stem[10:11] == "_":
        stem = stem[11:]
    return " ".join(w[:1].upper() + w[1:] for w in stem.replace("-", " ").split())


def _journal_ts(mtime):
    """Seconds since the epoch for an ISO mtime, or 0. Read as LOCAL time.

    The server writes `2026-08-13T13:42:48` with no zone and means local time
    on the server, which is this timezone. Reading it as UTC would shift every
    entry seven hours and file this morning's note under yesterday - the same
    bug the website had eleven times over.
    """
    if not mtime:
        return 0.0
    try:
        return time.mktime(time.strptime(mtime[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def journal_template(category):
    """The category's `_TEMPLATE.md` with YYYY-MM-DD replaced by today, or "".

    The same prefill the website's compose form does. Empty string when the
    template cannot be had: a blank body still makes a working entry, and a
    failed fetch must not stop you writing one.
    """
    if category not in JOURNAL_CATEGORIES:
        return ""
    text = journal_read(f"{category}/_TEMPLATE.md")
    return (text or "").replace("YYYY-MM-DD", local_today())


def journal_stats(entries, now=None):
    """(total, this week, this month, top category key) for the stat row."""
    rows = list(entries or [])
    now = time.time() if now is None else now
    week_ago = now - 7 * 86400
    lt = time.localtime(now)
    month_start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    counts = {}
    for e in rows:
        counts[e.get("cat", "")] = counts.get(e.get("cat", ""), 0) + 1
    top = max(counts, key=lambda k: (counts[k], k)) if counts else ""
    return (len(rows),
            sum(1 for e in rows if (e.get("ts") or 0) >= week_ago),
            sum(1 for e in rows if (e.get("ts") or 0) >= month_start),
            top)


def journal_filter(entries, cat="all", query="", sort="new"):
    """Filter and sort the entry list exactly as the website's jnRenderList."""
    rows = [e for e in (entries or []) if cat == "all" or e.get("cat") == cat]
    q = (query or "").strip().lower()
    if q:
        rows = [e for e in rows
                if q in (e.get("title") or "").lower()
                or q in (e.get("excerpt") or "").lower()]
    if sort == "az":
        rows.sort(key=lambda e: (e.get("title") or "").lower())
    elif sort == "old":
        rows.sort(key=lambda e: e.get("ts") or 0)
    else:
        rows.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return rows


# ------------------------------------------------------------------- overview
def overview(blocking_docker=False):
    """Home's stat strip and per-machine rows, or None when never fetched.

    One /api/dashboard read - shared with `machines()` through the store, so
    Home does not pay for it twice - plus the container count, which is asked
    for separately and is allowed to fail on its own. The website shows an em
    dash for that one tile rather than losing the whole strip.

    `blocking_docker` is off for the first paint. /api/docker_list SSHes to
    five machines and was measured at 6.0, 5.9 and 6.1 seconds on three
    consecutive Home builds - six seconds of blank screen for one number.
    Off, the count comes from whatever the store already holds and is None
    otherwise; Home fetches it behind the page and repaints when it lands.

    `containers` being None means docker has not been read *yet or at all*.
    The difference between "nothing is running" and "we do not know" is
    exactly the kind of thing this app is not allowed to smudge into a zero.
    """
    d = dashboard()
    if not isinstance(d, dict):
        return None
    rows, online, services_up = [], 0, 0
    for mid, m in d.items():
        w = machine_widget(d, mid, "system")
        up = widget_alive(w)
        online += 1 if up else 0
        for s in (machine_widget(d, mid, "services").get("services") or []):
            if s.get("active"):
                services_up += 1
        rows.append({"id": mid, "display": (m or {}).get("display", mid),
                     "color": (m or {}).get("color", "#58a6ff"), "online": up,
                     "error": str(w.get("error") or ""),
                     "cpu": _pct(w.get("cpu_percent")),
                     "ram": _pct(w.get("memory_percent")),
                     "disk": _pct(w.get("disk_percent")),
                     "uptime": w.get("uptime") or ""})
    rows.sort(key=lambda r: (not r["online"], r["display"].lower()))
    return {"machines": rows, "online": online, "total": len(rows),
            "services": services_up,
            "containers": docker_running(blocking=blocking_docker)}


def _pct(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def docker_running(blocking=True):
    """How many containers are running across every host, or None.

    Store-backed rather than `docker_list()`, which goes straight to the
    wire every time: the Lab page wants the server's own error text for a
    host that would not answer, Home wants one integer it can show with no
    signal. `blocking=False` answers only from what the store already holds,
    so the first paint never waits on five SSH sessions.

    None means docker has not been read, which Home shows as an em dash.
    A zero here would be a claim, and on this server it would be a false one:
    34 containers are running as this is written.
    """
    d = (_get("/api/docker_list", timeout=30) if blocking
         else _cached("/api/docker_list"))
    if not isinstance(d, dict):
        return None
    n = 0
    for m in d.values():
        for c in ((m or {}).get("containers") or []):
            if c.get("state") == "running":
                n += 1
    return n


def weather_days(count=3):
    """The next `count` days *including today*, or None.

    `weather()` above starts at index 1 because its card is a "what is coming"
    strip. Home's forecast row starts at today and labels it "Today", which is
    what the website shows - and the live server only sends three days at all,
    so skipping one there leaves two.
    """
    d = _get("/api/weather")
    if not isinstance(d, dict):
        return None
    daily = d.get("daily") or {}
    times = daily.get("time") or []
    hi = daily.get("temperature_2m_max") or []
    lo = daily.get("temperature_2m_min") or []
    code = daily.get("weather_code") or []
    today = local_today()
    out = []
    for i, t in enumerate(times[:count]):
        # Labelled by the date itself, not by position. The website calls
        # index 0 "Today" unconditionally; with a cached payload from
        # yesterday - which this app keeps on purpose so the page works with
        # no signal - that files yesterday's high under today's name.
        try:
            name = "Today" if t == today else time.strftime(
                "%a", time.strptime(t, "%Y-%m-%d"))
        except (ValueError, TypeError):
            name = t
        out.append({"day": name, "date": t,
                    "hi": round(hi[i]) if i < len(hi) and hi[i] is not None else None,
                    "lo": round(lo[i]) if i < len(lo) and lo[i] is not None else None,
                    "code": code[i] if i < len(code) else None})
    return out


def weather_current():
    """The raw `current` block, or None.

    Home needs humidity and apparent temperature, which `weather()`'s summary
    does not carry - and it must *not* claim a rain chance, because this
    server sends no `hourly` block at all. See home.py.
    """
    d = _get("/api/weather")
    if not isinstance(d, dict):
        return None
    cur = d.get("current")
    return cur if isinstance(cur, dict) else None


_MAP = {"MACHINES": machines, "WEATHER": weather, "FEEDS": feeds,
        "WAT2DO": wat2do_state, "BIKES": garage_bikes, "TRIPS": trips,
        "RIDES": rides_list, "PROJECTS": projects, "FOODZEN": foodzen,
        "JOURNAL": journal, "NOWPLAYING": nowplaying}


def __getattr__(name):
    if name in _MAP:
        val = _MAP[name]()
        if val is None:
            val = getattr(mock, name)
        return val
    return getattr(mock, name)
