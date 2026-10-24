#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Native-image asset boundaries and independent RGB window evidence."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import native_weston_assets as assets
import native_weston_vm as vm
spec = importlib.util.spec_from_file_location('native_weston_smoke', ROOT / 'tests/smoke-native-weston.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def frame(x=100, y=70, w=400, h=240):
    width, height = 800, 600
    data = bytearray(bytes((11, 21, 36)) * width * height)
    # A separate visible terminal-shaped region with light glyph fragments.
    for row in range(50, 450):
        for col in range(20, 620):
            offset = (row * width + col) * 3
            data[offset:offset + 3] = bytes((230, 230, 230) if row % 16 == 0 and col < 80 else (0, 0, 0))
    for row in range(h):
        for col in range(w):
            rgb = ((38, 60, 86) if row < 28 else
                   (30 + 45 * col // w, 45 + 35 * row // h, 116))
            offset = ((row + y) * width + col + x) * 3
            data[offset:offset + 3] = bytes(rgb)
    return width, height, bytes(data)


class NativeVM(unittest.TestCase):
    def test_system_identity_auth_and_boot_files_are_not_dependency_assets(self):
        for path in ('etc/passwd', 'etc/shadow', 'etc/group', 'init', 'bin/sh',
                     'etc/init.d/rcS', 'boot/kernel', '../usr/lib/libc.so.6', '/usr/lib/libc.so.6'):
            self.assertFalse(assets.allowed(path), path)
        for path in ('usr/lib/libc.so.6', 'usr/lib/weston/desktop-shell.so',
                     'usr/bin/weston-terminal', 'etc/fonts/fonts.conf'):
            self.assertTrue(assets.allowed(path), path)

    def test_symlink_cannot_escape_explicit_component_set(self):
        assets.safe_link('usr/lib/libwayland-client.so', 'libwayland-client.so.0')
        assets.safe_link('lib64', 'lib')
        for path, target in (('usr/lib/libc.so.6', '../../etc/shadow'),
                             ('usr/lib/libc.so.6', '/etc/passwd'),
                             ('lib64', '/home/owner')):
            with self.assertRaises(ValueError):
                assets.safe_link(path, target)

    def test_native_base_file_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'output'
            (source / 'usr/bin').mkdir(parents=True)
            (output / 'usr/bin').mkdir(parents=True)
            (source / 'usr/bin/weston').write_bytes(b'component')
            (output / 'usr/bin/weston').write_bytes(b'original')
            with self.assertRaises(ValueError):
                vm.copy_asset(source, output, 'usr/bin/weston', {'kind': 'file', 'executable': True})
            self.assertEqual((output / 'usr/bin/weston').read_bytes(), b'original')

    def test_real_native_gradient_location_and_geometry_required(self):
        for geometry in ((100, 70, 400, 240), (160, 130, 464, 288), (30, 35, 336, 192)):
            image = frame(*geometry)
            self.assertEqual(smoke.original_window(*image), geometry)
            self.assertTrue(smoke.terminal_pixels_visible(*image, geometry))
        image = frame()
        self.assertEqual(smoke.terminal_point(*image), (100, 310))
        width, height, data = image
        solid = bytes((11, 21, 36)) * width * height
        self.assertIsNone(smoke.original_window(width, height, solid))
        # A background with the native gradient's blue channel alone is not
        # sufficient; its red/green bands must also match the actual client.
        false_frame = bytes((220, 220, 116)) * width * height
        self.assertIsNone(smoke.original_window(width, height, false_frame))


if __name__ == '__main__':
    unittest.main()
