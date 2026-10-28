#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verify transferred state discovery and real, regular-file GPT/ext4 layouts.

No block devices, loop mounts or physical storage are used. Discovery tests
execute the production resolver with only its sysfs root and sleep replaced.
Image tests require the ordinary host image tools; they do not prove boot.
"""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from state_disk import STATE_PARTUUID, create_state_disk


class DiscoveryTests(unittest.TestCase):
    def resolve(self, identities, late=False):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            blocks = work / 'blocks'
            blocks.mkdir()
            def partition(name, uuid, label):
                entry = blocks / name
                entry.mkdir()
                (entry / 'uevent').write_text(
                    f'PARTUUID={uuid}\nPARTNAME={label}\n')
            for name, uuid, label in identities:
                partition(name, uuid, label)
            source = (ROOT / 'build/rootfs/mount-state').read_text()
            source = source.split("state=$(wait_for_state_device)", 1)[0]
            source = source.replace('/sys/class/block/*', str(blocks) + '/*')
            if late:
                # Enumerate the state partition after the first empty scan.
                action = (f'mkdir -p {blocks}/sda2; '
                          f"printf 'PARTUUID={STATE_PARTUUID}\\nPARTNAME=Synveil State\\n' > {blocks}/sda2/uevent")
            else:
                action = ':'
            script = ('sleep() { ' + action + '; }\n' + source +
                      '\nwait_for_state_device\n')
            return subprocess.run(['sh', '-c', script], text=True,
                                  capture_output=True, timeout=3)

    def test_exact_identity_accepts_any_device_name(self):
        result = self.resolve([('nvme0n1p2', STATE_PARTUUID, 'Synveil State')])
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, '/dev/nvme0n1p2'))

    def test_wrong_uuid_or_name_is_not_accepted(self):
        result = self.resolve([('sda2', STATE_PARTUUID, 'Other'),
                               ('vdb1', 'wrong', 'Synveil State')])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_duplicate_partitions_fail_closed(self):
        result = self.resolve([(name, STATE_PARTUUID, 'Synveil State')
                               for name in ('sda2', 'vdb1')])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('waiting', result.stderr)

    def test_late_partition_is_found_after_wait(self):
        result = self.resolve([], late=True)
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, '/dev/sda2'))
        self.assertIn('waiting', result.stderr)

    def test_absent_partition_stops_at_bound(self):
        result = self.resolve([])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.count('waiting'), 1)


@unittest.skipUnless(all(shutil.which(p) for p in
    ('sgdisk', 'gawk', 'mkfs.ext4', 'mkfs.vfat', 'mmd', 'mcopy', 'mtype', 'e2fsck')),
    'real image tools unavailable')
class ImageTests(unittest.TestCase):
    def verify_state(self, image, number):
        info = subprocess.check_output(['sgdisk', '-i', str(number), str(image)], text=True)
        self.assertIn(STATE_PARTUUID.upper(), info)
        self.assertIn("'Synveil State'", info)
        first = int(re.search(r'First sector: (\d+)', info)[1])
        last = int(re.search(r'Last sector: (\d+)', info)[1])
        length = (last - first + 1) * 512
        with image.open('rb') as stream:
            stream.seek(first * 512 + 1024)
            superblock = stream.read(1024)
            self.assertEqual(superblock[56:58], b'\x53\xef')
            blocks = struct.unpack_from('<I', superblock, 4)[0]
            block_size = 1024 << struct.unpack_from('<I', superblock, 24)[0]
            self.assertEqual(blocks * block_size, length)
            stream.seek(first * 512)
            filesystem = image.parent / 'extracted-state.ext4'
            with filesystem.open('wb') as dest:
                remaining = length
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    self.assertTrue(chunk)
                    dest.write(chunk)
                    remaining -= len(chunk)
        subprocess.run(['e2fsck', '-fn', str(filesystem)], check=True,
                       capture_output=True, timeout=20)
        check = subprocess.check_output(['sgdisk', '-v', str(image)], text=True)
        self.assertIn('No problems found', check)

    def image(self, state):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            out = work / 'out'
            out.mkdir()
            (out / 'synveil-x86_64.efi').write_bytes(b'fixture EFI payload')
            env = {k: v for k, v in os.environ.items() if not k.startswith('SYNVEIL_')}
            env.update(SYNVEIL_OUT_DIR=str(out), SYNVEIL_WORK_DIR=str(work / 'work'),
                       SYNVEIL_TOOLCHAIN_DIR=str(work / 'toolchain'),
                       SYNVEIL_ESP_SIZE_MB='64', SYNVEIL_STATE_SIZE_MB=str(state))
            subprocess.run(['bash', str(ROOT / 'build/scripts/image.sh')],
                           env=env, check=True, capture_output=True, timeout=30)
            image = out / 'synveil-x86_64.img'
            if state:
                self.verify_state(image, 2)
            else:
                info = subprocess.check_output(['sgdisk', '-p', str(image)], text=True)
                self.assertNotIn('Synveil State', info)
            info = subprocess.check_output(['sgdisk', '-i', '1', str(image)], text=True)
            first = int(re.search(r'First sector: (\d+)', info)[1])
            payload = subprocess.check_output(['mtype', '-i',
                f'{image}@@{first * 512}', '::/EFI/BOOT/BOOTX64.EFI'])
            self.assertEqual(payload, b'fixture EFI payload')

    def test_default_image_retains_single_esp(self):
        self.image(0)

    def test_joined_image_state_fits_partition_and_has_valid_filesystem(self):
        self.image(64)

    def test_separate_state_disk_fits_partition_and_has_valid_filesystem(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'state.img'
            create_state_disk(image, 64)
            self.verify_state(image, 1)


if __name__ == '__main__':
    unittest.main()
