#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Refusal tests for tools/write-test-media.sh, on disposable fixtures.

The writer runs in its fixture mode (SYNVEIL_TESTMEDIA_FIXTURE=1)
against a synthetic sysfs/proc tree built here: in fixture mode it
can never prompt and never write, it only runs its checks and exits.
The point of the suite is the in-use check: mounted filesystems and
swap must be traced through their sysfs backing-device relationships
(device-mapper slaves, partitions' parent disks, md components, loop
backing files), because a mapper volume's name says nothing about
the disk underneath it. Any source whose backing chain cannot be
resolved must refuse the write (fail closed).
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "tools" / "write-test-media.sh"


class WriterFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.devices = base / "sys" / "devices"
        self.class_block = base / "sys" / "class" / "block"
        self.class_block.mkdir(parents=True)
        self.proc = base / "proc"
        self.proc.mkdir()
        self.mounts = []
        self.swaps = []
        self.image = base / "synveil-x86_64.img"
        self.image.write_bytes(b"synveil-test-image" * 4096)
        self.image_sha = hashlib.sha256(self.image.read_bytes()).hexdigest()

    # -- fixture sysfs builders --------------------------------------
    def _link_class(self, real_dir, name):
        link = self.class_block / name
        link.symlink_to(os.path.relpath(real_dir, self.class_block))

    def add_disk(self, name, *, removable="1", size_sectors=8388608,
                 transport="usb", model="Fixture SSD", serial="FX-1"):
        real = self.devices / "pci" / "block" / name
        real.mkdir(parents=True)
        (real / "removable").write_text(removable + "\n")
        (real / "size").write_text(str(size_sectors) + "\n")
        device = real / "device"
        device.mkdir()
        (device / "model").write_text(model + "\n")
        (device / "vendor").write_text("Fixture\n")
        (device / "serial").write_text(serial + "\n")
        if transport is not None:
            (device / "transport").write_text(transport + "\n")
        self._link_class(real, name)
        return real

    def add_partition(self, disk, name):
        real = self.devices / "pci" / "block" / disk / name
        real.mkdir(parents=True)
        (real / "partition").write_text("1\n")
        (real / "size").write_text("1048576\n")
        self._link_class(real, name)
        return real

    def add_dm(self, name, dm_name, slaves):
        real = self.devices / "virtual" / "block" / name
        real.mkdir(parents=True)
        (real / "size").write_text("1048576\n")
        dm = real / "dm"
        dm.mkdir()
        (dm / "name").write_text(dm_name + "\n")
        slaves_dir = real / "slaves"
        slaves_dir.mkdir()
        for slave in slaves:
            (slaves_dir / slave).symlink_to(
                os.path.relpath(self.class_block / slave, slaves_dir))
        self._link_class(real, name)
        return real

    def add_md(self, name, components):
        real = self.devices / "virtual" / "block" / name
        real.mkdir(parents=True)
        (real / "size").write_text("1048576\n")
        md = real / "md"
        md.mkdir()
        for comp in components:
            (md / ("dev-" + comp)).mkdir()
        self._link_class(real, name)
        return real

    def add_loop(self, name, backing_file):
        real = self.devices / "virtual" / "block" / name
        real.mkdir(parents=True)
        (real / "size").write_text("1048576\n")
        loop = real / "loop"
        loop.mkdir()
        (loop / "backing_file").write_text(backing_file + "\n")
        self._link_class(real, name)
        return real

    # -- invocation ---------------------------------------------------
    def run_writer(self, device="/dev/sdb", sha=None):
        (self.proc / "mounts").write_text(
            "".join(f"{src} {mp} ext4 rw 0 0\n" for src, mp in self.mounts))
        swaps = ["Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"]
        swaps += [f"{src}\t\t\t\tpartition\t1024\t0\t-1\n" for src in self.swaps]
        (self.proc / "swaps").write_text("".join(swaps))
        env = {
            **os.environ,
            "SYNVEIL_TESTMEDIA_FIXTURE": "1",
            "SYNVEIL_TESTMEDIA_SYSFS": str(self.class_block),
            "SYNVEIL_TESTMEDIA_PROC": str(self.proc),
        }
        return subprocess.run(
            ["bash", str(WRITER), "--image", str(self.image),
             "--device", device,
             "--expected-sha256", sha or self.image_sha],
            env=env, capture_output=True, text=True, timeout=30)

    def assert_refused(self, result, needle="in use"):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("CHECKS_PASSED", result.stdout)
        self.assertIn(needle, result.stderr + result.stdout)

    def assert_passed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNVEIL_TEST_MEDIA_CHECKS_PASSED", result.stdout)
        self.assertNotIn("SYNVEIL_TEST_MEDIA_WRITTEN", result.stdout)


class WriterSafetyTests(WriterFixture):
    def test_clean_removable_target_passes(self):
        self.add_disk("sda", removable="0", transport="scsi")
        self.add_partition("sda", "sda1")
        self.add_disk("sdb")
        self.mounts = [("/dev/sda1", "/"), ("tmpfs", "/run")]
        self.assert_passed(self.run_writer())

    def test_directly_mounted_target_partition_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb1")
        self.mounts = [("/dev/sdb1", "/data")]
        self.assert_refused(self.run_writer())

    def test_luks_volume_on_target_refused(self):
        # The case the old prefix check missed: the mount source is a
        # mapper name; the disk underneath is the target's partition.
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb2")
        self.add_dm("dm-0", "cryptroot", ["sdb2"])
        self.mounts = [("/dev/mapper/cryptroot", "/")]
        self.assert_refused(self.run_writer())

    def test_lvm_over_luks_on_target_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb2")
        self.add_dm("dm-0", "cryptroot", ["sdb2"])
        self.add_dm("dm-1", "vg0-root", ["dm-0"])
        self.mounts = [("/dev/mapper/vg0-root", "/")]
        self.assert_refused(self.run_writer())

    def test_swap_on_mapper_backed_by_target_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb2")
        self.add_dm("dm-0", "cryptswap", ["sdb2"])
        self.swaps = ["/dev/dm-0"]
        self.assert_refused(self.run_writer())

    def test_swap_directly_on_target_partition_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb1")
        self.swaps = ["/dev/sdb1"]
        self.assert_refused(self.run_writer())

    def test_md_array_with_target_component_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb1")
        self.add_disk("sdc", serial="FX-2")
        self.add_partition("sdc", "sdc1")
        self.add_md("md0", ["sdb1", "sdc1"])
        self.mounts = [("/dev/md0", "/srv")]
        self.assert_refused(self.run_writer())

    def test_loop_backed_by_file_on_target_filesystem_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb2")
        self.add_dm("dm-0", "cryptroot", ["sdb2"])
        self.add_loop("loop0", "/data/disk.img")
        self.mounts = [("/dev/mapper/cryptroot", "/data"),
                       ("/dev/loop0", "/snap")]
        self.assert_refused(self.run_writer())

    def test_unresolvable_mapper_source_fails_closed(self):
        self.add_disk("sdb")
        self.mounts = [("/dev/mapper/ghost", "/")]
        self.assert_refused(self.run_writer(), "cannot determine")

    def test_mapper_without_slaves_fails_closed(self):
        self.add_disk("sdb")
        real = self.devices / "virtual" / "block" / "dm-9"
        real.mkdir(parents=True)
        (real / "size").write_text("1024\n")
        (real / "dm").mkdir()
        (real / "dm" / "name").write_text("empty\n")
        (real / "slaves").mkdir()
        self._link_class(real, "dm-9")
        self.mounts = [("/dev/dm-9", "/")]
        self.assert_refused(self.run_writer(), "cannot determine")

    def test_system_on_other_disk_target_still_passes(self):
        self.add_disk("sda", removable="0", transport="scsi")
        self.add_partition("sda", "sda2")
        self.add_dm("dm-0", "cryptroot", ["sda2"])
        self.add_disk("sdb")
        self.mounts = [("/dev/mapper/cryptroot", "/")]
        self.swaps = ["/dev/dm-0"]
        self.assert_passed(self.run_writer())

    def test_non_removable_target_refused(self):
        self.add_disk("sdb", removable="0", transport="scsi")
        self.assert_refused(self.run_writer(), "not removable")

    def test_digest_mismatch_refused(self):
        self.add_disk("sdb")
        self.assert_refused(
            self.run_writer(sha="0" * 64), "digest mismatch")

    def test_partition_device_refused(self):
        self.add_disk("sdb")
        self.add_partition("sdb", "sdb1")
        self.assert_refused(
            self.run_writer(device="/dev/sdb1"), "whole disk")

    def test_undersized_target_refused(self):
        self.add_disk("sdb", size_sectors=16)
        self.assert_refused(self.run_writer(), "smaller than the image")


if __name__ == "__main__":
    unittest.main()
