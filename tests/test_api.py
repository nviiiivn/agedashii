"""api.machines() - the online flag the status dots and Home both read."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agedashii_tofu import api  # noqa: E402

class MachineOnlineFlag(unittest.TestCase):
    """A machine that cannot be reached must not read as up.

    /api/dashboard answers {"error": "unreachable"} for a dead host. That is
    a non-empty dict, and the old test was bool(w), so SP3 showed a live
    status dot in the topbar and a live row on Home while it was down.
    """

    def _machines(self, dashboard):
        with mock.patch.object(api, "_get", return_value=dashboard):
            return {m["id"]: m for m in api.machines()}

    def test_an_unreachable_host_is_offline(self):
        got = self._machines({
            "sp3": {"display": "SP3", "widgets": {"system": {"error": "unreachable"}}},
        })
        self.assertFalse(got["sp3"]["online"])
        self.assertEqual(got["sp3"]["error"], "unreachable")

    def test_a_live_host_is_online(self):
        got = self._machines({
            "tower": {"display": "Tower",
                      "widgets": {"system": {"uptime": "1d 2h", "cpu": 12}}},
        })
        self.assertTrue(got["tower"]["online"])
        self.assertEqual(got["tower"]["error"], "")

    def test_no_system_widget_at_all_is_offline(self):
        got = self._machines({"x": {"display": "X", "widgets": {}}})
        self.assertFalse(got["x"]["online"])
