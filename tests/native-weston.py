#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Fail closed if package recipes replace native compiler or generate an OS."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_weston", ROOT / "tools/native_weston.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class Boundaries(unittest.TestCase):
    def config(self):
        # These derived settings are produced by real pinned Kconfig. The
        # actual configuration is checked again before any package compilation.
        text = w.configuration(w.ffi.TOOLCHAIN, Path("/tmp/native-overrides.mk"))
        return text + ('BR2_PACKAGE_WESTON_DRM=y\n'
                       'BR2_TOOLCHAIN_EXTERNAL_PREFIX=' + json.dumps(w.ffi.TARGET) + '\n')

    def test_native_compiler_and_software_compositor_configuration(self):
        w.source_pin()
        w.check_configuration(self.config(), w.ffi.TOOLCHAIN)

    def test_foreign_or_internal_toolchain_refused(self):
        for old, new in (('BR2_TOOLCHAIN_EXTERNAL_GCC_16=y', ''),
                         ('BR2_TOOLCHAIN_EXTERNAL_CUSTOM_GLIBC=y', ''),
                         ('BR2_TOOLCHAIN_EXTERNAL_CXX=y', ''),
                         (json.dumps(w.ffi.TARGET), '"x86_64-linux-gnu"'),
                         (json.dumps(str(w.ffi.TOOLCHAIN)), '"/usr"')):
            with self.assertRaises(ValueError):
                w.check_configuration(self.config().replace(old, new), w.ffi.TOOLCHAIN)

    def test_os_image_init_and_unneeded_graphics_stack_refused(self):
        for key in ('BR2_TARGET_ROOTFS_TAR', 'BR2_TARGET_ROOTFS_EXT2',
                    'BR2_LINUX_KERNEL', 'BR2_TARGET_GRUB2', 'BR2_TOOLCHAIN_BUILDROOT',
                    'BR2_PACKAGE_BUSYBOX', 'BR2_PACKAGE_SYSTEMD', 'BR2_PACKAGE_XORG7',
                    'BR2_PACKAGE_MESA3D'):
            with self.assertRaises(ValueError):
                w.check_configuration(self.config() + key + '=y\n', w.ffi.TOOLCHAIN)

    def test_unsigned_dependency_download_and_missing_seat_support_refused(self):
        for key in ('BR2_DOWNLOAD_FORCE_CHECK_HASHES', 'BR2_PACKAGE_SEATD_DAEMON',
                    'BR2_PACKAGE_WESTON_SHELL_DESKTOP', 'BR2_PACKAGE_WESTON_HEADLESS'):
            with self.assertRaises(ValueError):
                w.check_configuration(self.config().replace(key + '=y', ''), w.ffi.TOOLCHAIN)


if __name__ == '__main__':
    unittest.main()
