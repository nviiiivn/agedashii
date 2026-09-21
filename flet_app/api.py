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


def flipper(query="motorcycle"):
    """Real listings from the server's scanner, or None if it cannot be had.

    Returns only the rows the server was willing to score - it now flags
    non-listings (a $1 placeholder, a service ad, a Reddit question) with
    `not_a_deal`, and those must never be shown as finds.
    """
    d = _get("/api/flipper/scan?query=" + urllib.parse.quote(query))
    if d is None:
        return None
    return [x for x in (d.get("listings") or []) if x.get("score") is not None]


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
            "pantry_items": pitems if isinstance(pitems, list) else []}


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


def _garage_records_path(bike, rtype):
    return f"/api/garage/records?bike={urllib.parse.quote(bike)}&type={rtype}"


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

    Not applied to the cached /api/garage/spec response: nothing in the app
    reads that endpoint yet, so the shape the server wraps it in is unknown
    and inventing one would be sample data by another name. Marked stale
    instead, exactly as the old code popped it.
    """
    spec_path = f"/api/garage/spec?bike={urllib.parse.quote(bike)}"
    return _queue_write("/api/garage/spec/save", params={"bike": bike},
                        body=json.dumps(spec), coalesce_key=f"garage:spec:{bike}",
                        invalidate=(spec_path,)).pair


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
