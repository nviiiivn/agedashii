"""The machine detail, and why the topbar status dots looked dead.

Two jobs, one file, because they turned out to be the same bug seen from
two ends: Lab built every tab before it painted anything, so the page was
both far too slow to open and far too thin once it did.

Every fixture here is a verbatim slice of what the live dashbored server
answered on 2026-09-21 with the new probes in place, including the awkward
parts - the Tower's product_name really is "To Be Filled By O.E.M.", the
Yoga really reports no systemd units because Void runs runit, LibreELEC
really has no lsblk, and SP3 really is down and answers {"error":
"unreachable"} for every widget it has.
"""
import ast
import os
import unittest

from agedashii_tofu import api
from agedashii_tofu.pages import lab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _machines_source():
    """The text of lab._machines, for the checks that are about what it
    does *not* build."""
    with open(os.path.join(ROOT, "agedashii_tofu/pages/lab.py")) as fh:
        src = fh.read()
    start = src.index("\ndef _machines(page):")
    end = src.index("\ndef ", start + 1)
    return src[start:end]


# /api/dashboard, trimmed to the fields under test.
DASH = {
    "blavksaba": {
        "display": "Tower", "color": "#58a6ff",
        "widgets": {
            "system": {
                "cpu": "45", "mem_used": "16771", "mem_total": "64215",
                "disk": "1.7T/1.9T (89%)", "temp": "46C",
                "gpu": "NVIDIA GeForce RTX 3080, 13429 MiB, 20480 MiB, 35 %",
                "uptime": "1d 5h 19m", "hostname": "blavksaba",
                "greeting": ("OS: CachyOS x86_64\n"
                             "Kernel: Linux 6.18.40-1-cachyos-lts\n"
                             "Shell: bash 5.3.15\n"
                             "CPU: Intel(R) Core(TM) i7-9700K (8) @ 4.90 GHz\n"
                             "Memory: 16.33 GiB / 62.71 GiB (26%)\n"
                             "Local IP (eno2): 192.0.2.24/24\n"
                             "Uptime: 1 day, 5 hours, 19 mins"),
                "cpu_percent": 45, "memory_percent": 26, "disk_percent": 89,
                "gpu_percent": 35,
                # the new probes
                "model": "ASRock Z390 Phantom Gaming 4S",
                "machine_type": "Desktop", "os_id": "cachyos",
                "os_name": "CachyOS Linux", "os_base": "arch",
                "cpu_name": "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz",
                "cpu_cores": 8, "gpu_temp": "44C",
                "gpu_pci": "NVIDIA Corporation GA102 [GeForce RTX 3080] (rev a1)",
                "units_running": 23, "units_total": 52,
                "disks": [{"name": "/dev/sda", "rotational": False,
                           "bytes": 2000398934016, "model": "Samsung SSD 870 QVO 2TB"},
                          {"name": "/dev/nvme1n1", "rotational": True,
                           "bytes": 1000204886016, "model": "WDC WDS100T2B0C-00PXH0"}],
            },
            "network": {
                "interfaces": [{"name": "eno2", "ip": "192.0.2.24/24", "state": "UP"},
                               {"name": "docker0", "ip": "172.17.0.1/16", "state": "UP"},
                               {"name": "veth83c27bb@if2",
                                "ip": "fe80::2c3a:9ff:fe85:639/64", "state": "UP"}],
                "connections": 12,
                "bandwidth": {"lo": {"rx": 2469256754, "tx": 2469256754},
                              "eno2": {"rx": 298204977, "tx": 344440074},
                              "docker0": {"rx": 4007, "tx": 4895487}},
                "rates": {"lo": {"rx": 9999.0, "tx": 9999.0},
                          "eno2": {"rx": 4902.0, "tx": 8759.4},
                          "docker0": {"rx": 11.0, "tx": 22.0}},
                "rate_window": 12.0,
            },
            "services": {"services": [{"port": "22", "process": "sshd", "active": True},
                                      {"port": "6969", "process": "syncthing",
                                       "active": True}]},
            "terminal": {"ready": True},
        },
    },
    # Void runs runit: systemctl prints nothing, which parsed as 0 of 0.
    "void": {
        "display": "Yoga", "color": "#d29922",
        "widgets": {
            "system": {
                "cpu": "33", "mem_used": "2755", "mem_total": "7661",
                "disk": "191G/233G (87%)", "temp": "46C", "gpu": "",
                "uptime": "3d 3h 13m", "hostname": "void",
                "greeting": ("OS: Void Linux x86_64\n"
                             "Host: 80V4 (Lenovo YOGA 710-14IKB)\n"
                             "Kernel: Linux 6.18.26_1\n"
                             "Local IP (wlp1s0): 192.0.2.254/24"),
                "cpu_percent": 33, "memory_percent": 36, "disk_percent": 87,
                "gpu_percent": 0,
                "model": "LENOVO 80V4", "vendor": "LENOVO",
                "machine_type": "Laptop", "os_id": "void", "os_base": "void",
                "cpu_name": "Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz",
                "cpu_cores": 4,
                "gpu_pci": "Intel Corporation Kaby Lake-U GT2 [HD Graphics 620] (rev 02)",
                "disks": [{"name": "/dev/sda", "rotational": False,
                           "bytes": 256060514304,
                           "model": "SAMSUNG MZNTY256HDHP-000L2"}],
            },
            "network": {"interfaces": [], "connections": 11,
                        "bandwidth": {"wlp1s0": {"rx": 6545722009, "tx": 736922004}}},
            "services": {"services": []},
        },
    },
    "sp3": {
        "display": "SP3", "color": "#f85149",
        "widgets": {"system": {"error": "unreachable"},
                    "services": {"error": "unreachable"},
                    "network": {"error": "unreachable"},
                    "processes": {"error": "unreachable"},
                    "terminal": {"ready": True}},
    },
}

CFG = {"blavksaba": {"display": "Tower", "host": "192.0.2.24", "user": "nvii"},
       "void": {"display": "Yoga", "host": "192.0.2.254", "user": "nvii"},
       "sp3": {"display": "SP3", "host": "192.0.2.23", "user": "nvii"}}

DOCKER = {
    "blavksaba": {"display": "Tower", "containers": [
        {"name": "umb-api", "state": "running"},
        {"name": "umb-wiki", "state": "running"},
        {"name": "old", "state": "exited"}]},
    "void": {"display": "Yoga", "containers": [{"name": "agent-zero", "state": "exited"}]},
    # An unreachable host answers with an empty list *and* an error. The
    # empty list alone is what made SP3 report "0 containers" as a reading.
    "sp3": {"display": "SP3", "containers": [], "error": "unreachable"},
}


def detail(mid):
    return api.machine_detail(DASH, mid, CFG, DOCKER)


class NothingUnmeasuredIsEverZero(unittest.TestCase):
    """SP3 is down. Every field it has must be None, not 0 and not a guess.

    `bool(widget)` is not an aliveness test: every widget of an unreachable
    machine is {"error": "unreachable"}, which is truthy, and that one line
    had SP3 showing online across the whole app.
    """

    def test_every_numeric_field_on_a_dead_machine_is_none(self):
        d = detail("sp3")
        for key in ("cpu_pct", "cpu_cores", "cpu_temp", "ram_used_mb",
                    "ram_total_mb", "ram_pct", "gpu_pct", "gpu_temp",
                    "disk_pct", "net_rx_rate", "net_tx_rate", "net_rx_total",
                    "net_tx_total", "net_connections", "services_listening",
                    "units_running", "units_total", "containers_running",
                    "containers_total"):
            self.assertIsNone(d[key], f"{key} on a down machine must be unknown")

    def test_every_text_field_on_a_dead_machine_is_none(self):
        d = detail("sp3")
        for key in ("name", "hostname", "machine_type", "os", "os_base",
                    "kernel", "uptime", "ip", "cpu_name", "ram_type",
                    "gpu_name"):
            self.assertIsNone(d[key], f"{key} on a down machine must be unknown")

    def test_it_is_offline_and_says_why(self):
        d = detail("sp3")
        self.assertFalse(d["online"])
        self.assertEqual(d["error"], "unreachable")

    def test_the_address_we_would_reach_it_on_is_still_known(self):
        # Where we *would* ssh is config, not a measurement of the machine,
        # so it survives the machine being down.
        self.assertEqual(detail("sp3")["address"], "nvii@192.0.2.23")

    def test_an_empty_container_list_from_a_dead_host_is_not_zero(self):
        self.assertIsNone(detail("sp3")["containers_total"])

    def test_no_systemd_is_not_zero_services(self):
        # Void runs runit. The server sends no unit counts at all for it.
        d = detail("void")
        self.assertIsNone(d["units_total"])
        self.assertIsNone(d["units_running"])

    def test_a_machine_with_no_gpu_has_no_gpu_load(self):
        # gpu_percent is 0 in the payload for every machine without one.
        # Rendering that as "0%" claims an idle GPU that is not there.
        self.assertEqual(DASH["void"]["widgets"]["system"]["gpu_percent"], 0)
        self.assertIsNone(detail("void")["gpu_pct"])
        self.assertIsNone(detail("void")["gpu_temp"])

    def test_a_machine_with_a_gpu_reports_it(self):
        d = detail("blavksaba")
        self.assertEqual(d["gpu_name"], "NVIDIA GeForce RTX 3080")
        self.assertEqual((d["gpu_mem_used_mb"], d["gpu_mem_total_mb"]), (13429, 20480))
        self.assertEqual(d["gpu_pct"], 35.0)
        self.assertEqual(d["gpu_temp"], "44C")

    def test_an_unknown_machine_does_not_raise(self):
        d = api.machine_detail(DASH, "not-a-machine")
        self.assertFalse(d["online"])
        self.assertIsNone(d["cpu_pct"])
        self.assertFalse(api.machine_detail(None, "x")["online"])


class EveryFieldTheOwnerAskedFor(unittest.TestCase):
    """The list, one assertion per item, against real payload values."""

    def setUp(self):
        self.d = detail("blavksaba")

    def test_nickname_and_official_name_are_different_things(self):
        self.assertEqual(self.d["nickname"], "Tower")
        self.assertEqual(self.d["name"], "ASRock Z390 Phantom Gaming 4S")

    def test_type_os_and_os_base(self):
        self.assertEqual(self.d["machine_type"], "Desktop")
        self.assertEqual(self.d["os"], "CachyOS x86_64")
        self.assertEqual(self.d["os_base"], "arch")

    def test_kernel_and_hostname_and_uptime(self):
        self.assertEqual(self.d["kernel"], "Linux 6.18.40-1-cachyos-lts")
        self.assertEqual(self.d["hostname"], "blavksaba")
        self.assertEqual(self.d["uptime"], "1d 5h 19m")

    def test_the_ip_comes_from_the_machine_not_from_our_config(self):
        self.assertEqual(self.d["ip"], "192.0.2.24/24")
        self.assertEqual(self.d["address"], "nvii@192.0.2.24")

    def test_cpu_name_cores_load_and_temperature(self):
        self.assertEqual(self.d["cpu_name"],
                         "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz")
        self.assertEqual(self.d["cpu_cores"], 8)
        self.assertEqual(self.d["cpu_pct"], 45.0)
        self.assertEqual(self.d["cpu_temp"], "46C")

    def test_ram_amount_load_and_type(self):
        self.assertEqual((self.d["ram_used_mb"], self.d["ram_total_mb"]),
                         (16771, 64215))
        self.assertEqual(self.d["ram_pct"], 26.0)
        # dmidecode needs root, so no machine here can answer this. Absent,
        # not invented: the card prints an em dash.
        self.assertIsNone(self.d["ram_type"])

    def test_ram_type_is_reported_when_the_machine_can_answer(self):
        dash = {"x": {"display": "X", "widgets": {"system": {
            "hostname": "x", "ram_type": "DDR4", "ram_speed": "3200 MT/s"}}}}
        self.assertEqual(api.machine_detail(dash, "x")["ram_type"],
                         "DDR4 @ 3200 MT/s")

    def test_disks_carry_a_name_a_kind_and_a_size(self):
        self.assertEqual(self.d["disk_used"], "1.7T")
        self.assertEqual(self.d["disk_total"], "1.9T")
        self.assertEqual(self.d["disk_pct"], 89.0)
        self.assertEqual(len(self.d["disks"]), 2)
        self.assertEqual(lab._disk_line(self.d["disks"][0]),
                         "Samsung SSD 870 QVO 2TB — SSD, 1.8 TB")
        self.assertEqual(lab._disk_line(self.d["disks"][1]),
                         "WDC WDS100T2B0C-00PXH0 — HDD, 931.5 GB")

    def test_network_now_and_across_this_uptime(self):
        # Only the real link counts. lo doubles every byte and docker0
        # counts every container's traffic a second time.
        self.assertEqual(self.d["net_rx_rate"], 4902.0)
        self.assertEqual(self.d["net_tx_rate"], 8759.4)
        self.assertEqual(self.d["net_rx_total"], 298204977)
        self.assertEqual(self.d["net_tx_total"], 344440074)
        self.assertEqual(self.d["net_connections"], 12)

    def test_only_real_interfaces_are_listed(self):
        self.assertEqual([i["name"] for i in self.d["net_ifaces"]], ["eno2"])

    def test_services_and_containers(self):
        self.assertEqual(self.d["services_listening"], 2)
        self.assertEqual((self.d["units_running"], self.d["units_total"]), (23, 52))
        self.assertEqual((self.d["containers_running"], self.d["containers_total"]),
                         (2, 3))

    def test_a_rate_the_server_has_not_sampled_twice_yet_is_unknown(self):
        # void's network widget carries counters but no rates: the server
        # has only one sample. That is not "idle".
        d = detail("void")
        self.assertIsNone(d["net_rx_rate"])
        self.assertIsNone(d["net_tx_rate"])
        self.assertEqual(d["net_rx_total"], 6545722009)

    def test_a_measured_zero_rate_is_shown_as_zero_not_as_unknown(self):
        self.assertEqual(lab._rate(0.0), "0 B/s")
        self.assertEqual(lab._rate(None), "—")
        self.assertEqual(lab._rate(4902.0), "4.8 KB/s")


class GreetingParsing(unittest.TestCase):
    """The fetch blob is the only place the kernel and the LAN IP live."""

    G = DASH["blavksaba"]["widgets"]["system"]["greeting"]

    def test_a_named_line_comes_back_whole(self):
        self.assertEqual(api.greeting_field(self.G, "Kernel"),
                         "Linux 6.18.40-1-cachyos-lts")
        self.assertEqual(api.greeting_field(self.G, "OS"), "CachyOS x86_64")

    def test_the_interface_name_baked_into_the_key_is_tolerated(self):
        # The key really is "Local IP (eno2)".
        self.assertEqual(api.greeting_field(self.G, "Local IP"),
                         "192.0.2.24/24")

    def test_an_escaped_newline_is_still_a_line_break(self):
        # lil-faegola's greeting arrives with literal backslash-n.
        blob = "OS: GNU/Linux 6.12.56\\nHost: lil-faegola\\nKernel: 6.12.56"
        self.assertEqual(api.greeting_field(blob, "Host"), "lil-faegola")
        self.assertEqual(api.greeting_field(blob, "Kernel"), "6.12.56")

    def test_a_label_that_is_not_there_is_none_not_an_empty_string(self):
        self.assertIsNone(api.greeting_field(self.G, "Motherboard"))
        self.assertIsNone(api.greeting_field("", "OS"))
        self.assertIsNone(api.greeting_field(None, "OS"))


class PhysicalInterfaces(unittest.TestCase):
    """What counts as "the network" for a bytes total."""

    def test_real_links_are_physical(self):
        for name in ("eth0", "eno2", "wlan0", "wlp1s0", "enp3s0"):
            self.assertTrue(api.iface_is_physical(name), name)

    def test_loopback_bridges_veths_and_tunnels_are_not(self):
        for name in ("lo", "docker0", "br-13c4b738e040", "veth83c27bb",
                     "tailscale0", "wg0", "tun0", "virbr0"):
            self.assertFalse(api.iface_is_physical(name), name)

    def test_nothing_is_not_an_interface(self):
        self.assertFalse(api.iface_is_physical(""))
        self.assertFalse(api.iface_is_physical(None))


class GpuColumns(unittest.TestCase):
    def test_nvidia_smis_row_is_split(self):
        self.assertEqual(
            api.gpu_columns("NVIDIA GeForce RTX 3080, 13429 MiB, 20480 MiB, 35 %"),
            ("NVIDIA GeForce RTX 3080", 13429, 20480, 35.0))

    def test_no_gpu_is_four_unknowns_not_four_zeroes(self):
        self.assertEqual(api.gpu_columns(""), (None, None, None, None))
        self.assertEqual(api.gpu_columns(None), (None, None, None, None))


class TheDotsCarryLoad(unittest.TestCase):
    """The topbar dots were one bit each. They show CPU load now."""

    def _machines(self, dashboard):
        from unittest import mock as _mock
        with _mock.patch.object(api, "_get", return_value=dashboard):
            return {m["id"]: m for m in api.machines()}

    def test_a_live_machine_reports_its_load(self):
        got = self._machines(DASH)
        self.assertEqual(got["blavksaba"]["load"], 45.0)
        self.assertEqual(got["blavksaba"]["mem_load"], 26.0)

    def test_a_down_machine_reports_no_load_rather_than_zero(self):
        got = self._machines(DASH)
        self.assertFalse(got["sp3"]["online"])
        self.assertIsNone(got["sp3"]["load"],
                          "an empty ring says unknown; a full circle at 0% "
                          "says idle, and SP3 is not idle, it is off")
        self.assertIsNone(got["sp3"]["mem_load"])

    def test_a_live_machine_that_did_not_report_load_is_still_unknown(self):
        got = self._machines({"x": {"display": "X", "widgets": {
            "system": {"uptime": "1d", "hostname": "x"}}}})
        self.assertTrue(got["x"]["online"])
        self.assertIsNone(got["x"]["load"])


class TappingADotOpensThatMachine(unittest.TestCase):
    """Not just the Lab tab - the machine, first in the list and unfolded."""

    def test_main_sets_the_machine_id_on_the_way_in(self):
        with open(os.path.join(ROOT, "main.py")) as fh:
            src = fh.read()
        self.assertIn('lab.OPEN_MACHINE["id"] = mid', src,
                      "the dot handler must say which machine it meant")
        self.assertIn('_TAB_INDEX["lab"] = 0', src)

    def test_leaving_lab_forgets_it(self):
        with open(os.path.join(ROOT, "main.py")) as fh:
            tree = ast.parse(fh.read())
        clears = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Subscript)
                          and getattr(getattr(t.value, "attr", None), "strip", lambda: None)()
                          == "OPEN_MACHINE" for t in n.targets)]
        self.assertTrue(clears, "render() must clear OPEN_MACHINE off the Lab page")

    def test_the_default_is_no_machine_asked_for(self):
        self.assertIn("id", lab.OPEN_MACHINE)


class LabDoesNotBuildEveryTabBeforeItPaints(unittest.TestCase):
    """The proven cause of the dead-looking dots.

    Building all five tabs took 15.4 s cold against the live server here and
    9.84 s on the owner's laptop, inside a synchronous on_click - so the old
    frame stayed up and every tap during it was dropped. build() hands
    _tabs callables now; _tabs builds only the selected one.
    """

    def test_build_passes_callables_not_built_controls(self):
        with open(os.path.join(ROOT, "agedashii_tofu/pages/lab.py")) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "_tabs"
                    and any(kw.arg == "key" and getattr(kw.value, "value", None) == "lab"
                            for kw in node.keywords)):
                continue
            bodies = [t.elts[1] for t in node.args[1].elts]
            self.assertTrue(all(isinstance(b, ast.Lambda) for b in bodies),
                            "every Lab tab body must be deferred")
            return
        self.fail("no _tabs(key='lab') call in lab.py")

    def test_only_the_selected_tab_is_built(self):
        built = []

        class FakePage:
            def update(self): pass

        def maker(name):
            def build():
                built.append(name)
                return None
            return build

        lab._TAB_INDEX["probe"] = 2
        try:
            lab._tabs(FakePage(), [(n, maker(n)) for n in "abcde"], key="probe")
        finally:
            lab._TAB_INDEX.pop("probe", None)
        self.assertEqual(built, ["c"], "four tabs were built that nobody asked for")

    def test_a_tab_is_built_when_it_is_selected(self):
        built = []

        class FakePage:
            def update(self): pass

        class Ev:
            class control:
                selected_index = 1

        tabs = lab._tabs(FakePage(),
                         [("a", lambda: built.append("a")),
                          ("b", lambda: built.append("b"))], key="probe2")
        lab._TAB_INDEX.pop("probe2", None)
        self.assertEqual(built, ["a"])
        tabs.on_change(Ev())
        self.assertEqual(built, ["a", "b"])
        lab._TAB_INDEX.pop("probe2", None)

    def test_plain_controls_still_work_for_every_other_page(self):
        class FakePage:
            def update(self): pass

        sentinel = object()
        tabs = lab._tabs(FakePage(), [("a", sentinel), ("b", sentinel)])
        view = tabs.content.controls[1]
        self.assertEqual([slot.content for slot in view.controls],
                         [sentinel, sentinel])

    def test_a_collapsed_card_builds_none_of_the_heavy_sections(self):
        """The regression: expanding was made the default and the clean
        card was lost. Both exist, and the heavy half is built on demand.

        Measured against the live server: the Machines tab was 1756
        controls, about 350 a machine, including 84 dotfile editors and
        four terminals nobody had opened. Collapsed it is 112.
        """
        src = _machines_source()
        collapsed = src.split("if not is_open:")[1].split("detail = M.machine_detail")[0]
        for heavy in ("dotfiles_section", "terminal_section", "power_row",
                      "_gauge", "_machine_detail_body", "ollama_models"):
            self.assertNotIn(heavy, collapsed,
                             f"{heavy} is built for a card nobody opened")

    def test_the_open_card_is_remembered_across_a_repaint(self):
        src = _machines_source()
        self.assertIn("OPEN_MACHINE.get(\"id\")", src)
        # Opening must not re-read the server: it is a layout change.
        self.assertIn("def repaint():", src)
        self.assertIn("rerender", src)

    def test_machines_reads_docker_from_the_store_not_the_wire(self):
        # M.docker_list() SSHes to five hosts and took 6.17 s live, which was
        # most of the freeze. Home already stopped doing this on its hot
        # path; the only call left in _machines must be the background one.
        with open(os.path.join(ROOT, "agedashii_tofu/pages/lab.py")) as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_machines")
        deferred = {id(x) for d in ast.walk(fn)
                    if isinstance(d, ast.AsyncFunctionDef) for x in ast.walk(d)}

        def calls(name):
            return [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                    and getattr(c.func, "attr", None) == name
                    and getattr(getattr(c.func, "value", None), "id", None) == "M"]

        cached = calls("_cached")
        self.assertTrue(any(a.args and getattr(a.args[0], "value", None)
                            == "/api/docker_list" for a in cached),
                        "_machines must paint from the store's copy")
        for call in calls("docker_list"):
            self.assertIn(id(call), deferred,
                          "a blocking docker_list is back on the paint path")


if __name__ == "__main__":
    unittest.main()
