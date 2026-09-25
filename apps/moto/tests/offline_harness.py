"""A fake server and an offline-capable TestCase, for this app's tests.

Lifted out of Tofu's `tests/test_api_offline.py` when Trips, Garage, Wat2Do
and FoodZen left Tofu. It depends on nothing from any app - it swaps
`config._opener` for an object that answers out of a dict, and hands out a
Store on a tempfile - so no test here touches a socket and "the server is
down" is a boolean rather than a firewall rule.

Copied into each app rather than shared: tests/test_package_boundaries.py
forbids one app importing another, and test scaffolding is not worth an
exception to that.
"""
import email.message
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from agedashii_washi import api, config
from agedashii_washi.store import Store


class Response:
    """What `_opener.open` hands back: a context manager with .read()."""

    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else str(body).encode()

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, payload, headers=None):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    fp = io.BytesIO(body.encode() if isinstance(body, str) else body)
    return urllib.error.HTTPError(url, code, "test", hdrs, fp)


class FakeServer:
    """A dashbored that can be switched off, with no socket anywhere near it."""

    MISSING = object()

    def __init__(self, base):
        self.base = base
        self.data = {}          # path -> what a GET answers with
        self.posts = []         # (path, decoded body) in the order received
        self.gets = []
        self.up = True
        self.mode = "up"        # 'up' | 'down' | 'gateway' | 'cloudflare'
        self.post_error = None  # (code, payload) every POST answers with

    # -- the bit config._opener provides -------------------------------------
    def open(self, target, timeout=None):
        url = target if isinstance(target, str) else target.full_url
        path = url[len(self.base):] if url.startswith(self.base) else url
        method = "GET" if isinstance(target, str) else target.get_method()
        body = b"" if isinstance(target, str) else (target.data or b"")

        if not self.up or self.mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError("no route to host"))
        if self.mode == "gateway":
            raise http_error(url, 302, "", {"Location": "https://auth.example/login"})
        if self.mode == "cloudflare":
            raise http_error(url, 403, "<html>denied</html>", {"Server": "cloudflare"})

        if method == "GET":
            self.gets.append(path)
            if path == config.PROBE_PATH:
                return Response(b'{"ok": true}')
            value = self.data.get(path, self.MISSING)
            if value is self.MISSING:
                raise http_error(url, 404, {"error": f"no such path {path}"})
            return Response(json.dumps(value))

        self.posts.append((path, self._decode(body)))
        if self.post_error:
            raise http_error(url, self.post_error[0], self.post_error[1])
        return Response(b'{"ok": true}')

    @staticmethod
    def _decode(body):
        text = body.decode() if isinstance(body, bytes) else str(body)
        try:
            return json.loads(text)
        except ValueError:
            return text

    # -- convenience ---------------------------------------------------------
    def posted_to(self, path):
        return [b for p, b in self.posts if p.split("?")[0] == path]


BLANK_FOODZEN = {"templates": [], "lists": [], "need": [],
                 "pantry": {"items": []}, "recipes": [], "purchases": []}


class ApiOfflineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "store.sqlite3"

        self.base = "http://dashbored.test:6900"
        self.server = FakeServer(self.base)

        self._old_opener, config._opener = config._opener, self.server
        self._old_url, config.SERVER_URL = config.SERVER_URL, self.base
        self._old_status = dict(config.STATUS)
        config.STATUS.update({"ok": False, "detail": "not checked yet",
                              "url": self.base, "at": 0.0})

        self.store = self.new_store()
        self._old_store = api.set_store(self.store)
        self._old_ttl, api.TTL = api.TTL, 3600   # a read stays fresh unless asked
        api._invalid.clear()
        api._invalid_all = 0.0
        self.addCleanup(self.restore)

    def new_store(self):
        """A store on the same file - i.e. what a restart of the app sees."""
        return Store(self.db, ttl=3600, probe=lambda: config.probe(config.SERVER_URL))

    def restore(self):
        self.settle()
        api.set_store(self._old_store)
        api.TTL = self._old_ttl
        api._invalid.clear()
        api._invalid_all = 0.0
        config._opener = self._old_opener
        config.SERVER_URL = self._old_url
        config.STATUS.clear()
        config.STATUS.update(self._old_status)
        self.store.close()

    @staticmethod
    def settle(timeout=2.0):
        """Wait for any background refresh/probe thread the read path started."""
        for t in threading.enumerate():
            if t is threading.current_thread():
                continue
            if t.name.startswith(("store-refresh", "agedashii-probe")):
                t.join(timeout)

    # -- helpers -------------------------------------------------------------
    def seed_foodzen(self, state=None):
        """One successful connection, so the device has a copy to edit."""
        self.server.data[api.FOODZEN_PATH] = json.loads(
            json.dumps(state or BLANK_FOODZEN))
        got = api._get(api.FOODZEN_PATH)
        self.assertIsNotNone(got, "the fake server should have answered")
        return got

    def go_offline(self):
        self.server.up = False
        self.store.set_online(False, "test: unplugged")

    def go_online(self):
        self.server.up = True
        self.store.set_online(True, "test: plugged back in")

    def grocery_names(self):
        fz = api.foodzen() or {}
        return list(fz.get("list") or [])
