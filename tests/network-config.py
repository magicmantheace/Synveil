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
CONFIGURE = ROOT / "build/rootfs/configure-network"


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

    def test_selected_interface_is_used_for_all_dhcp_mutations(self):
        result = self.run_hook(interface="enp0s3")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn("ifconfig enp0s3 10.0.2.15", calls)
        self.assertIn("route add default gw 10.0.2.2 dev enp0s3", calls)

    def test_configure_selects_carrier_before_disconnected_interface(self):
        sysnet = self.root / "net"
        for name, carrier in (("lo", "1"), ("eth0", "0"),
                              ("enp0s3", "1"), ("wlan0", "1")):
            path = sysnet / name
            path.mkdir(parents=True)
            (path / "carrier").write_text(carrier)
            (path / "type").write_text("1")
        (sysnet / "wlan0/wireless").mkdir()
        for name, body in {
            "timeout": '#!/bin/sh\nshift 3\nexec "$@"\n',
            "udhcpc": '#!/bin/sh\nprintf "udhcpc %s\\n" "$*" >> "$LOG"\n',
            "ip": '#!/bin/sh\nprintf "ip %s\\n" "$*" >> "$LOG"\necho "inet 10.0.2.15/24"\n',
        }.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        result = subprocess.run(
            ["sh", str(CONFIGURE)], capture_output=True, text=True, timeout=3,
            env={**os.environ, "SYNVEIL_NET_CLASS_DIR": str(sysnet),
                 "SYNVEIL_NETWORK_PATH": str(self.bin) + ":" + os.environ.get("PATH", ""),
                 "LOG": str(self.log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNVEIL_NETWORK_READY", result.stdout)
        self.assertIn("udhcpc -i enp0s3", self.log.read_text())
        self.assertIn("ip -4 addr show dev enp0s3", self.log.read_text())

    def test_configure_rejects_non_ethernet_and_wireless_only_devices(self):
        sysnet = self.root / "only-other-devices"
        (sysnet / "lo").mkdir(parents=True)
        (sysnet / "lo/type").write_text("772")
        (sysnet / "wlan0/wireless").mkdir(parents=True)
        (sysnet / "wlan0/type").write_text("1")
        (sysnet / "wlan0/carrier").write_text("1")
        (self.bin / "udhcpc").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "udhcpc").chmod(0o755)
        (self.bin / "timeout").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "timeout").chmod(0o755)
        (self.bin / "ip").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "ip").chmod(0o755)
        result = subprocess.run(
            ["sh", str(CONFIGURE)], capture_output=True, text=True, timeout=3,
            env={**os.environ, "SYNVEIL_NET_CLASS_DIR": str(sysnet),
                 "SYNVEIL_NETWORK_PATH": str(self.bin) + ":" + os.environ.get("PATH", ""),
                 "LOG": str(self.log)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no usable wired interface", result.stdout)
        self.assertFalse(self.log.exists())

    def test_invalid_interface_ip_and_action_do_not_mutate(self):
        for action, env in (("bound", {"interface": "../lo"}),
                            ("bound", {"interface": "lo"}),
                            ("bound", {"interface": "invalid name"}),
                            ("bound", {"interface": "0123456789abcdef"}),
                            ("bound", {"interface": "eth0;rm"}),
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
