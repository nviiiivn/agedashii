"""Tests for agedashii_tofu/config.py — server discovery and persistence.

These run offline. Anything that needs a server gets a throwaway
http.server on a loopback port, so the suite does not depend on the real
dashbored being up, on the LAN, or on Cloudflare.

The cases that matter most are the ones about *why* a connection failed.
The app falls back to sample data whenever the server cannot be reached, so
"unreachable", "behind a login" and "blocked at the edge" all look identical
from the UI unless config reports them apart — which is the bug this module
exists to prevent.
"""
import asyncio
import http.server
import json
import socketserver
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu import config  # noqa: E402


def serve(handler_cls):
    """Start a throwaway server on an OS-assigned port. Returns (srv, base)."""
    class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = TS(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def stop(srv):
    """Shut down and close, so the suite does not leak sockets."""
    srv.shutdown()
    srv.server_close()


def handler(status=200, body=b'{"ok":1}', headers=None, ctype="application/json"):
    hdrs = dict(headers or {})
    # send_response() emits its own Server header from version_string(), so a
    # test that wants to control Server has to override that rather than add a
    # second one - otherwise the response carries both.
    server_name = hdrs.pop("Server", None)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def version_string(self):
            return server_name or super().version_string()

        def do_GET(self):
            self.send_response(status)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
    return H


class FakePrefs:
    """Stands in for flet's SharedPreferences (async get/set)."""

    def __init__(self, initial=None, broken=False):
        self.store = dict(initial or {})
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise RuntimeError("storage unavailable")
        return self.store.get(key)

    async def set(self, key, value):
        if self.broken:
            raise RuntimeError("storage unavailable")
        self.store[key] = value
        return True


class ConfigTestCase(unittest.TestCase):
    """Restores module globals, which config mutates by design."""

    def setUp(self):
        self._saved = (config.SERVER_URL, list(config.CANDIDATES), dict(config.STATUS))

    def tearDown(self):
        config.SERVER_URL, config.CANDIDATES, status = self._saved
        config.STATUS.clear()
        config.STATUS.update(status)


class TestNormalise(ConfigTestCase):
    def test_adds_scheme_and_strips_slash(self):
        self.assertEqual(config.normalise("192.0.2.21:6900"), "http://192.0.2.21:6900")
        self.assertEqual(config.normalise("http://h:1/"), "http://h:1")
        self.assertEqual(config.normalise("  https://a.b/  "), "https://a.b")

    def test_keeps_https(self):
        self.assertEqual(config.normalise("https://x.y"), "https://x.y")

    def test_empty_inputs(self):
        for v in ("", "   ", None):
            with self.subTest(v=v):
                self.assertEqual(config.normalise(v), "")


class TestProbe(ConfigTestCase):
    def test_json_server_is_connected(self):
        srv, base = serve(handler())
        try:
            ok, detail = config.probe(base)
            self.assertTrue(ok, detail)
            self.assertEqual(detail, "connected")
        finally:
            stop(srv)

    def test_redirect_is_reported_as_auth_not_as_down(self):
        srv, base = serve(handler(302, b"", {"Location": "https://auth.example.com/oidc/auth"}))
        try:
            ok, detail = config.probe(base)
            self.assertFalse(ok)
            self.assertIn("blocked by auth", detail)
            self.assertIn("auth.example.com", detail)
        finally:
            stop(srv)

    def test_cloudflare_403_is_named_as_such(self):
        srv, base = serve(handler(403, b"denied", {"Server": "cloudflare"}, "text/html"))
        try:
            ok, detail = config.probe(base)
            self.assertFalse(ok)
            self.assertIn("Cloudflare", detail)
        finally:
            stop(srv)

    def test_plain_403_is_not_blamed_on_cloudflare(self):
        srv, base = serve(handler(403, b"nope", {"Server": "nginx"}, "text/html"))
        try:
            ok, detail = config.probe(base)
            self.assertFalse(ok)
            self.assertNotIn("Cloudflare", detail)
            self.assertIn("403", detail)
        finally:
            stop(srv)

    def test_non_json_response_is_distinguished(self):
        srv, base = serve(handler(200, b"<html>hello</html>", ctype="text/html"))
        try:
            ok, detail = config.probe(base)
            self.assertFalse(ok)
            self.assertIn("not with dashbored JSON", detail)
        finally:
            stop(srv)

    def test_nothing_listening(self):
        ok, detail = config.probe("http://127.0.0.1:1", timeout=2)
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)

    def test_empty_address(self):
        ok, detail = config.probe("")
        self.assertFalse(ok)
        self.assertEqual(detail, "no address set")

    def test_sends_a_non_urllib_user_agent(self):
        """Cloudflare 403s the default Python-urllib agent at the edge."""
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen["ua"] = self.headers.get("User-Agent", "")
                body = b'{"ok":1}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv, base = serve(H)
        try:
            config.probe(base)
            self.assertIn("agedashii", seen.get("ua", ""))
            self.assertNotIn("Python-urllib", seen.get("ua", ""))
        finally:
            stop(srv)


class TestSelect(ConfigTestCase):
    def test_skips_dead_and_adopts_the_first_live_one(self):
        srv, base = serve(handler())
        try:
            results = config.select(["http://127.0.0.1:1", base], timeout=3)
            self.assertEqual(len(results), 2)
            self.assertFalse(results[0][1])
            self.assertTrue(results[1][1])
            self.assertEqual(config.SERVER_URL, base)
            self.assertTrue(config.STATUS["ok"])
        finally:
            stop(srv)

    def test_stops_at_the_first_success(self):
        srv, base = serve(handler())
        try:
            results = config.select([base, "http://127.0.0.1:1"], timeout=3)
            self.assertEqual(len(results), 1, "should not probe candidates after a hit")
        finally:
            stop(srv)

    def test_all_dead_records_a_failure(self):
        results = config.select(["http://127.0.0.1:1", "http://127.0.0.1:2"], timeout=2)
        self.assertEqual(len(results), 2)
        self.assertFalse(any(ok for _, ok, _ in results))
        self.assertFalse(config.STATUS["ok"])


class TestPersistence(ConfigTestCase):
    def test_saved_address_is_tried_first(self):
        srv, base = serve(handler())
        try:
            prefs = FakePrefs({config.KEY_URL: base,
                               config.KEY_CANDIDATES: ["http://127.0.0.1:1", base]})
            results = asyncio.run(config.load(prefs))
            self.assertEqual(results[0][0], base, "saved URL should be probed before others")
            self.assertEqual(config.SERVER_URL, base)
        finally:
            stop(srv)

    def test_an_address_saved_before_the_rename_is_still_found(self):
        """Renaming the key namespace must not lose the server address.

        Of everything in storage this is the one that matters most: without
        it an existing install stops finding the server it was pointed at
        and falls back to the shipped LAN defaults, which anywhere but that
        LAN means an app that cannot reach anything at all.
        """
        srv, base = serve(handler())
        try:
            prefs = FakePrefs({config.LEGACY_KEY_URL: base,
                               config.LEGACY_KEY_CANDIDATES: ["http://127.0.0.1:1", base]})
            results = asyncio.run(config.load(prefs))
            self.assertEqual(results[0][0], base)
            self.assertEqual(config.SERVER_URL, base)
        finally:
            stop(srv)

    def test_first_launch_falls_back_to_defaults(self):
        prefs = FakePrefs()
        asyncio.run(config.load(prefs))
        self.assertEqual(config.CANDIDATES, config.DEFAULT_CANDIDATES)

    def test_unavailable_storage_does_not_crash_startup(self):
        prefs = FakePrefs(broken=True)
        asyncio.run(config.load(prefs))  # must not raise
        self.assertTrue(config.SERVER_URL)

    def test_save_round_trips(self):
        prefs = FakePrefs()
        ok = asyncio.run(config.save(prefs, url="10.0.0.5:6900"))
        self.assertTrue(ok)
        self.assertEqual(prefs.store[config.KEY_URL], "http://10.0.0.5:6900")
        self.assertIsInstance(prefs.store[config.KEY_CANDIDATES], list)

    def test_save_reports_failure_rather_than_raising(self):
        self.assertFalse(asyncio.run(config.save(FakePrefs(broken=True))))

    def test_saved_values_are_types_shared_preferences_accepts(self):
        """SharedPreferences rejects anything but str/int/float/bool/list[str]."""
        prefs = FakePrefs()
        asyncio.run(config.save(prefs, url="http://h:1", candidates=["http://h:1", "http://h:2"]))
        self.assertIsInstance(prefs.store[config.KEY_URL], str)
        stored = prefs.store[config.KEY_CANDIDATES]
        self.assertTrue(all(isinstance(x, str) for x in stored), stored)


class TestApiUsesConfig(ConfigTestCase):
    def test_api_reads_the_address_at_call_time(self):
        """Changing the server in Settings must take effect without a restart."""
        payload = json.dumps({"m1": {"display": "Box", "widgets": {"system": {"uptime": "1d"}}}}).encode()
        srv, base = serve(handler(200, payload))
        try:
            from agedashii_tofu import api
            api.refresh()
            config.SERVER_URL = base
            machines = api.machines()
            self.assertIsInstance(machines, list)
            self.assertEqual(machines[0]["display"], "Box")
        finally:
            stop(srv)


if __name__ == "__main__":
    unittest.main()
