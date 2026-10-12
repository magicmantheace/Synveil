#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test network UI data using fake procfs routes and DHCP resolver records."""
from pathlib import Path
import subprocess
import socket
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

    def route_interface(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route"
            path.write_text(content)
            result = subprocess.run(
                [str(self.executable), "--route-interface", str(path)],
                check=True, capture_output=True, text=True, timeout=2)
            return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_routed_non_loopback_interface_is_selected(self):
        found = self.route_interface(
            "Iface Destination Gateway Flags\n"
            "lo 00000000 0202000A 0003\n"
            "enp3s0 00000000 0101A8C0 0003\n")
        self.assertEqual(found, {"found": "1", "interface": "enp3s0"})

    def test_unusable_and_unsafe_routes_are_ignored(self):
        for item in ("eth0 00000000 0202000A 0001",
                     "eth0 0100000A 0202000A 0003",
                     "eth0 00000000 00000000 0003",
                     "lo 00000000 0202000A 0003",
                     "bad/name 00000000 0202000A 0003",
                     "eth0 00000000 100000000 0003"):
            with self.subTest(item=item):
                self.assertEqual(self.route_interface(item + "\n"),
                                 {"found": "0", "interface": ""})

    def test_route_table_scan_is_bounded(self):
        self.assertEqual(
            self.route_interface(
                "garbage\n" * 128 + "enp3s0 00000000 0202000A 0003\n"),
            {"found": "0", "interface": ""})

    def test_auto_discovers_installed_nic_without_eth0_assumption(self):
        interfaces = [name for _, name in socket.if_nameindex() if name != "lo"]
        if not interfaces:
            self.skipTest("host exposes no non-loopback NIC")
        chosen = interfaces[0]
        route = f"{chosen} 00000000 0202000A 0003\n"
        state = self.sample(route, "nameserver 10.0.2.3\n", "auto")
        self.assertEqual(state["interface"], chosen)
        self.assertEqual(state["present"], "1")
        self.assertEqual(state["gateway"], "10.0.2.2")

    def test_auto_fallback_never_identifies_loopback_as_nic(self):
        interfaces = [name for _, name in socket.if_nameindex() if name != "lo"]
        state = self.sample("lo 00000000 0202000A 0003\n",
                            "nameserver 127.0.0.1\n", "auto")
        self.assertNotEqual(state["interface"], "lo")
        if not interfaces:
            self.assertEqual(state["present"], "0")
            self.assertEqual(state["interface"], "")

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
