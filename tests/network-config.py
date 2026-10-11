#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise the QEMU DHCP callback without changing host network settings."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CALLBACK = ROOT / "build/rootfs/udhcpc-script"


class DHCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls"
        self.resolv = self.root / "resolv.conf"
        for name in ("ifconfig", "route"):
            executable = self.bin / name
            executable.write_text('#!/bin/sh\nprintf "%s %s\\n" "$0" "$*" >> "$LOG"\n')
            executable.chmod(0o755)

    def run_hook(self, action="bound", **options):
        env = {**os.environ,
               "SYNVEIL_NETWORK_PATH": str(self.bin) + ":" + os.environ.get("PATH", ""),
               "SYNVEIL_RESOLV_CONF": str(self.resolv),
               "LOG": str(self.log), "interface": "eth0",
               "ip": "10.0.2.15", "subnet": "255.255.255.0",
               "router": "10.0.2.2", "dns": "10.0.2.3 1.1.1.1"}
        env.update(options)
        return subprocess.run(["sh", str(CALLBACK), action], env=env,
                              capture_output=True, text=True, timeout=3)

    def test_bound_configures_address_route_and_dns(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.log.read_text()
        self.assertIn("ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up", data)
        self.assertIn("route add default gw 10.0.2.2 dev eth0", data)
        self.assertEqual(self.resolv.read_text(),
                         "nameserver 10.0.2.3\nnameserver 1.1.1.1\n")

    def test_invalid_interface_ip_and_action_do_not_mutate(self):
        for action, env in (("bound", {"interface": "wlan0"}),
                            ("bound", {"ip": "not-an-ip"}),
                            ("bound", {"subnet": "255.999.255.0"}),
                            ("shutdown", {})):
            self.assertNotEqual(self.run_hook(action, **env).returncode, 0)
            self.assertFalse(self.log.exists())
            self.assertFalse(self.resolv.exists())

    def test_rejects_untrusted_dns_router_values(self):
        result = self.run_hook(router="nonsense", dns="8.8.8.8 bad-data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.resolv.read_text(), "nameserver 8.8.8.8\n")
        self.assertNotIn("route add", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
