#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test network UI data using fake procfs routes and DHCP resolver records."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPLAY = ROOT / "src/veil-display"


class NetworkDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.build.name) / "network-diag"
        subprocess.run(
            ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
             str(DISPLAY / "network_diag.c"), str(ROOT / "tests/network-diag-test.c"),
             "-I", str(DISPLAY), "-o", str(cls.executable)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def sample(self, route, dns, interface="lo"):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / "route", Path(temp) / "resolv.conf"]
            for path, data in zip(paths, (route, dns)):
                path.write_text(data)
            result = subprocess.run(
                [str(self.executable), interface, *map(str, paths)],
                text=True, capture_output=True, timeout=2, check=True,
            )
            return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_route_gateway_uses_procfs_little_endian(self):
        state = self.sample(
            "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
            "lo 00000000 0202000A 0003 0 0 0 00000000\n",
            "nameserver 10.0.2.3\n",
        )
        self.assertEqual(state["present"], "1")
        self.assertEqual(state["link"], "1")
        self.assertEqual(state["ip"], "127.0.0.1")
        self.assertEqual(state["gateway"], "10.0.2.2")
        self.assertEqual(state["dns"], "10.0.2.3")

    def test_wrong_interface_or_unusable_route_rejected(self):
        for route in (
            "eth0 00000000 0202000A 0003 0 0 0 00000000\n",
            "lo 00000000 0202000A 0001 0 0 0 00000000\n",
            "lo 0100000A 0202000A 0003 0 0 0 00000000\n",
            "lo 00000000 100000000 0003 0 0 0 00000000\n",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.sample(route, "")["gateway"], "")

    def test_bad_and_ipv6_nameservers_are_ignored(self):
        state = self.sample("", "nameserver bad\nnameserver ::1\n"
                                     "nameserver 192.168.5.1\n")
        self.assertEqual(state["dns"], "192.168.5.1")
        self.assertEqual(self.sample("", "search mydomain\n")["dns"], "")

    def test_absent_interface_never_claims_dns_or_gateway(self):
        state = self.sample("missing0 00000000 0202000A 0003\n",
                            "nameserver 10.0.2.3\n", "missing0")
        self.assertEqual(state, {"present": "0", "link": "0", "ip": "",
                                 "gateway": "", "dns": ""})

    def test_long_route_table_is_bounded(self):
        route = "garbage\n" * 128 + "lo 00000000 0202000A 0003\n"
        self.assertEqual(self.sample(route, "")["gateway"], "")


if __name__ == "__main__":
    unittest.main()
