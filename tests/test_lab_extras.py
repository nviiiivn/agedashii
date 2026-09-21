"""The pure logic behind Lab and Extras.

Every fixture in here is a verbatim slice of what the live dashbored server
answered on 2026-09-21, not an invented shape. Where a value looks odd -
``"45C"`` for a temperature, ``{"error": "unreachable"}`` for a whole widget,
docker publishing the same port twice - that is what the server really sends,
and it is exactly the sort of thing that has shipped broken here before.
"""
import unittest

from flet_app import api
from flet_app.pages import lab


# What /api/dashboard answered, trimmed to the fields under test.
DASH = {
    "blavksaba": {
        "display": "Tower", "color": "#58a6ff",
        "widgets": {
            "system": {"cpu": "24", "mem_used": "16587", "mem_total": "64215",
                       "disk": "1.7T/1.9T (89%)", "temp": "45C",
                       "gpu": "NVIDIA GeForce RTX 3080, 14381 MiB, 20480 MiB, 22 %",
                       "uptime": "1d 3h 54m", "hostname": "blavksaba",
                       "greeting": ("OS: CachyOS x86_64\nKernel: Linux 6.18.40-1-cachyos-lts\n"
                                    "Shell: bash 5.3.15"),
                       "cpu_percent": 24, "memory_percent": 26,
                       "disk_percent": 89, "gpu_percent": 22},
            "ollama": {"models": [{"name": "hermes3:latest"},
                                  {"name": "granite4:3b"}],
                       "running": ["granite4:3b"]},
            "terminal": {"ready": True},
        },
    },
    # SP3 is genuinely down and answers this for every widget it has.
    "sp3": {
        "display": "SP3", "color": "#f85149",
        "widgets": {"system": {"error": "unreachable"},
                    "services": {"services": []},
                    "dotfiles": {"error": "unreachable"},
                    "network": {"error": "unreachable"},
                    "processes": {"error": "unreachable"},
                    "terminal": {"ready": True}},
    },
}


class UnreachableIsNotAlive(unittest.TestCase):
    """The one-line bug that showed SP3 green while it was down.

    Every widget on an unreachable machine is ``{"error": "unreachable"}``,
    which is a non-empty dict - so ``bool(widget)`` says yes. This is the
    same trap already fixed once in ``api.machines()``; everything in Lab now
    asks ``widget_alive`` instead.
    """

    def test_a_widget_with_data_is_alive(self):
        self.assertTrue(api.widget_alive(api.machine_widget(DASH, "blavksaba", "system")))

    def test_a_widget_carrying_only_an_error_is_not(self):
        w = api.machine_widget(DASH, "sp3", "system")
        self.assertTrue(w, "the fixture really is a non-empty dict")
        self.assertFalse(api.widget_alive(w))

    def test_a_missing_widget_is_not_alive_and_does_not_raise(self):
        self.assertEqual(api.machine_widget(DASH, "sp3", "ollama"), {})
        self.assertFalse(api.widget_alive(api.machine_widget(DASH, "nope", "system")))
        self.assertFalse(api.widget_alive(api.machine_widget(None, "sp3", "system")))

    def test_a_machine_whose_entry_is_not_a_dict_is_survivable(self):
        self.assertEqual(api.machine_widget({"x": "broken"}, "x", "system"), {})


class OllamaModels(unittest.TestCase):
    def test_names_come_back_in_order(self):
        self.assertEqual(api.ollama_models(DASH, "blavksaba"),
                         ["hermes3:latest", "granite4:3b"])

    def test_a_down_machine_lists_nothing_rather_than_raising(self):
        self.assertEqual(api.ollama_models(DASH, "sp3"), [])


class DockerPorts(unittest.TestCase):
    """docker ps sends one string, and publishes IPv4 and IPv6 separately.

    The website ran Object.entries() over that string, walking its
    characters, so its PORTS column read "0, 1, 2, 3...". Folding the
    duplicate mapping away is the whole job here.
    """

    def test_the_same_mapping_published_twice_is_shown_once(self):
        self.assertEqual(
            api.docker_ports("0.0.0.0:6999->6999/tcp, [::]:6999->6999/tcp"),
            "6999->6999/tcp")
        self.assertEqual(
            api.docker_ports("0.0.0.0:8002->8002/tcp, :::8002->8002/tcp"),
            "8002->8002/tcp")

    def test_unpublished_ports_survive_alongside_published_ones(self):
        self.assertEqual(
            api.docker_ports("22/tcp, 9000-9009/tcp, 0.0.0.0:5080->80/tcp, "
                             "[::]:5080->80/tcp"),
            "22/tcp, 9000-9009/tcp, 5080->80/tcp")

    def test_a_loopback_bind_keeps_its_own_mapping(self):
        self.assertEqual(api.docker_ports("3890/tcp, 127.0.0.1:3001->3000/tcp"),
                         "3890/tcp, 3001->3000/tcp")

    def test_nothing_published_is_empty_not_the_word_none(self):
        self.assertEqual(api.docker_ports(""), "")
        self.assertEqual(api.docker_ports(None), "")


class RepoOwner(unittest.TestCase):
    """Gitea has two repos called the same thing under different owners."""

    def test_full_name_is_split(self):
        self.assertEqual(api.repo_owner_name({"full_name": "umbreality/alexandria",
                                              "name": "alexandria"}),
                         ("umbreality", "alexandria"))

    def test_owner_login_is_the_fallback(self):
        self.assertEqual(api.repo_owner_name({"name": "listarr",
                                              "owner": {"login": "media"}}),
                         ("media", "listarr"))

    def test_a_row_with_neither_does_not_raise(self):
        self.assertEqual(api.repo_owner_name({}), ("", ""))


class MachineCardFormatting(unittest.TestCase):
    def test_a_temperature_gets_one_unit_not_two(self):
        # The old card printed f"{m['temp']}°C" over the server's "45C".
        self.assertEqual(lab._temp("45C"), "45 °C")
        self.assertEqual(lab._temp("58C"), "58 °C")
        self.assertEqual(lab._temp(""), "", "no reading means no row, not 0 °C")
        self.assertEqual(lab._temp(None), "")

    def test_the_os_line_is_one_line(self):
        # The old code split the greeting on "·", which is not in it, so
        # Tower's OS read "CachyOS x86_64\nKernel: Linux".
        greeting = DASH["blavksaba"]["widgets"]["system"]["greeting"]
        self.assertEqual(lab._os_line(greeting), "CachyOS x86_64")

    def test_an_escaped_newline_is_handled_too(self):
        # lil-faegola's greeting arrives with literal backslash-n in it.
        self.assertEqual(lab._os_line("OS: GNU/Linux 6.12.56\\nHost: lil-faegola"),
                         "GNU/Linux 6.12.56")

    def test_a_greeting_with_no_os_line_falls_back_to_its_first_line(self):
        self.assertEqual(lab._os_line("Host: void\nKernel: 6.18"), "Host: void")
        self.assertEqual(lab._os_line(""), "")

    def test_the_gpu_name_drops_the_memory_and_load_columns(self):
        self.assertEqual(lab._gpu_name(DASH["blavksaba"]["widgets"]["system"]["gpu"]),
                         "NVIDIA GeForce RTX 3080")
        self.assertEqual(lab._gpu_name(""), "")

    def test_ps_percentages_are_strings_and_must_be_cast(self):
        # A raw .toFixed on these took the website's whole machine grid down.
        self.assertEqual(lab._num("89.2"), 89.2)
        self.assertIsNone(lab._num("not a number"))
        self.assertIsNone(lab._num(None))


class ContainerOrder(unittest.TestCase):
    def test_running_first_then_by_name(self):
        rows = [{"name": "zebra", "state": "running"},
                {"name": "alpha", "state": "exited"},
                {"name": "beta", "state": "running"},
                {"name": "gamma", "state": "created"}]
        self.assertEqual([c["name"] for c in sorted(rows, key=lab._ct_sort)],
                         ["beta", "zebra", "gamma", "alpha"])

    def test_a_state_docker_has_never_heard_of_sorts_last(self):
        rows = [{"name": "weird", "state": "quantum"},
                {"name": "gone", "state": "exited"}]
        self.assertEqual([c["name"] for c in sorted(rows, key=lab._ct_sort)],
                         ["gone", "weird"])


if __name__ == "__main__":
    unittest.main()
