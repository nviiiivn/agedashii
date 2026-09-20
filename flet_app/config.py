"""Which dashbored server this app talks to, and how it finds it.

The app is a client for one dashbored server (dashbored-v3.py, port 6900).
Which URL actually reaches it depends on where the device is:

  on the LAN        http://192.0.2.21:6900   direct, no auth
  on WireGuard      the same LAN address, through the tunnel
  anywhere else     nothing, today

That last line is measured, not assumed. iamprettyfamous.online sits behind
VoidAuth OIDC forward-auth at the Caddy layer, and every path 302s to
auth.iamprettyfamous.online - `/api/dashboard` and `/api/config` included
(verified 2026-09-19). So a phone on cellular cannot reach the API over the
public hostname at all; it has to come in over the VPN, or the server needs a
token-auth path that does not exist yet.

That is why `probe()` refuses to follow redirects. If it followed them it
would fetch the login page, fail to parse it as JSON, and the app would fall
back to sample data looking like an ordinary network blip. Instead a 302 is
reported as exactly what it is: reachable, but behind a login.

Candidates are tried in order and the first that answers wins; the winner is
remembered so the next launch starts there.
"""
import json
import time
import urllib.error
import urllib.request

# Keys in Flet's SharedPreferences (persists across launches on every platform).
KEY_URL = "nvii.server_url"
KEY_CANDIDATES = "nvii.server_candidates"

# Shipped defaults. A private RFC1918 address is not a secret, and the app is
# useless without somewhere to point it; both are editable in Settings.
DEFAULT_CANDIDATES = [
    "http://192.0.2.21:6900",  # AITP on the LAN, and over WireGuard
    "http://aitp.local:6900",     # mDNS, for a LAN whose DHCP moved things
]

# The endpoint probed to decide whether a URL is a live dashbored. Chosen
# because it is a cheap, side-effect-free GET that returns JSON and exists on
# every version of the server.
PROBE_PATH = "/api/config"

SERVER_URL = DEFAULT_CANDIDATES[0]
CANDIDATES = list(DEFAULT_CANDIDATES)

# Result of the last probe, for the Settings screen to display honestly.
STATUS = {"ok": False, "detail": "not checked yet", "url": SERVER_URL, "at": 0.0}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into an HTTPError instead of silently following it.

    A 302 here means an auth gateway intercepted the request. Following it
    yields an HTML login page that would fail JSON parsing and be
    indistinguishable from the server being down.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Cloudflare's managed bot rules 403 the default "Python-urllib/3.x" agent at
# the edge, before the request ever reaches the origin - which shows up as a
# baffling 403 instead of the real answer. Any other agent passes; verified
# 2026-09-19 against iamprettyfamous.online, where "NVII-DASH/1.0" reaches the
# auth layer and gets the honest 302 while "Python-urllib/3.13" gets 403.
# This is not a disguise: it is this client's real name.
USER_AGENT = "NVII-DASH/1.0 (+dashbored client)"

_opener = urllib.request.build_opener(_NoRedirect)
_opener.addheaders = [("User-Agent", USER_AGENT)]


def normalise(url):
    """Trim a user-typed URL into something urllib can use.

    Accepts '192.0.2.21:6900' and 'http://host:6900/' alike.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url


def _describe(exc):
    """Turn a request failure into one line a human can act on.

    Shared by probe() and the api module so a failure reads the same wherever
    it surfaced. The redirect case is called out specifically because it is
    the expected off-LAN outcome and means something quite different from the
    server being down.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (301, 302, 303, 307, 308):
            host = (exc.headers.get("Location") or "").split("/")[2:3]
            where = host[0] if host else "a login page"
            return f"blocked by auth - redirects to {where} (use the VPN)"
        # get_all, not get: a response can legitimately carry more than one
        # Server header (a proxy chain appends rather than replaces), and get()
        # would only ever see the first.
        servers = " ".join(exc.headers.get_all("Server") or []).lower()
        if exc.code == 403 and "cloudflare" in servers:
            return "blocked by Cloudflare at the edge (bot rule), not by the server"
        return f"server returned HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable - {getattr(exc, 'reason', exc)}"
    if isinstance(exc, json.JSONDecodeError):
        return "responded, but not with dashbored JSON"
    return f"{type(exc).__name__}: {exc}"


def probe(url, timeout=3.0):
    """Check whether `url` is a reachable dashbored server.

    Returns (ok, detail). `detail` is written for a human to read in Settings.
    """
    url = normalise(url)
    if not url:
        return False, "no address set"
    try:
        with _opener.open(url + PROBE_PATH, timeout=timeout) as r:
            body = r.read(65536)
        json.loads(body.decode("utf-8", "replace"))
        return True, "connected"
    except Exception as e:  # noqa: BLE001 - surface anything odd rather than hide it
        return False, _describe(e)


def select(candidates=None, timeout=2.0):
    """Probe each candidate in order; adopt the first that answers.

    Returns a list of (url, ok, detail) for every candidate tried, so Settings
    can show why the others were skipped rather than just the winner.
    """
    global SERVER_URL
    results = []
    for url in (candidates if candidates is not None else CANDIDATES):
        ok, detail = probe(url, timeout=timeout)
        results.append((url, ok, detail))
        if ok:
            SERVER_URL = normalise(url)
            _remember(url, True, detail)
            return results
    if results:
        _remember(results[0][0], False, results[0][2])
    return results


def _remember(url, ok, detail):
    STATUS.update({"ok": ok, "detail": detail, "url": normalise(url), "at": time.time()})


def set_url(url):
    """Point the app at `url` and probe it. Returns (ok, detail)."""
    global SERVER_URL
    url = normalise(url)
    ok, detail = probe(url)
    SERVER_URL = url
    _remember(url, ok, detail)
    return ok, detail


async def load(prefs):
    """Restore the saved address, then confirm it still answers.

    `prefs` is a flet SharedPreferences instance. Storage can be empty (first
    launch) or unavailable (some web contexts), so every read is guarded and
    falls back to the shipped defaults.
    """
    global SERVER_URL, CANDIDATES
    try:
        saved = await prefs.get(KEY_URL)
        saved_list = await prefs.get(KEY_CANDIDATES)
    except Exception:
        saved, saved_list = None, None

    if isinstance(saved_list, list) and saved_list:
        CANDIDATES = [normalise(u) for u in saved_list if normalise(u)]
    if isinstance(saved, str) and normalise(saved):
        SERVER_URL = normalise(saved)
        # Try the remembered one first, then the rest as fallbacks.
        ordered = [SERVER_URL] + [u for u in CANDIDATES if u != SERVER_URL]
    else:
        ordered = list(CANDIDATES)
    return select(ordered)


async def save(prefs, url=None, candidates=None):
    """Persist the current (or given) address so the next launch starts there."""
    global CANDIDATES
    if candidates is not None:
        CANDIDATES = [normalise(u) for u in candidates if normalise(u)]
    target = normalise(url) if url else SERVER_URL
    try:
        await prefs.set(KEY_URL, target)
        await prefs.set(KEY_CANDIDATES, list(CANDIDATES))
        return True
    except Exception:
        return False
