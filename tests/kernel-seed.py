#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise guest-only kernel object reuse without compiling Linux."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ("DRM", "VIRTIO_PCI", "DRM_VIRTIO_GPU", "DRM_FBDEV_EMULATION",
            "DRM_CLIENT_DEFAULT_FBDEV", "FB_DEVICE", "VT", "VT_CONSOLE",
            "FRAMEBUFFER_CONSOLE", "INPUT_EVDEV", "USB_HID", "USB_XHCI_HCD")

FAKE_MAKE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$SYNVEIL_TEST_MAKE_LOG"
for arg in "$@"; do
    case "$arg" in O=*) output="${arg#O=}" ;; esac
done
[[ "${!#}" != x86_64_defconfig ]] || exit 98
if [[ "${!#}" == bzImage ]]; then
    cat "$SYNVEIL_OUT_DIR/synveil-initramfs.cpio" > "$output/arch/x86/boot/bzImage"
    printf '%s\n' "${SYNVEIL_KERNEL_TEST_RECOVERY_STATUS:-0}" >> "$output/arch/x86/boot/bzImage"
fi
"""


class GuestKernelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "build/scripts"
        scripts.mkdir(parents=True)
        for name in ("common.sh", "kernel.sh"):
            shutil.copy2(ROOT / "build/scripts" / name, scripts / name)
        self.work = self.root / "work"
        source = self.work / "src/linux-kernel"
        (source / "scripts/kconfig").mkdir(parents=True)
        (source / "scripts/kconfig/merge_config.sh").write_text("#!/bin/sh\nexit 0\n")
        (source / "scripts/kconfig/merge_config.sh").chmod(0o755)
        (source / "scripts/config").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$SYNVEIL_TEST_CONFIG_ARGS\"\n"
        )
        (source / "scripts/config").chmod(0o755)
        self.base = self.work / "kernel-build"
        (self.base / "arch/x86/boot").mkdir(parents=True)
        (self.base / "include/config").mkdir(parents=True)
        (self.base / ".config").write_text(
            "".join(f"CONFIG_{option}=y\n" for option in GRAPHICS)
        )
        (self.base / "include/config/auto.conf").write_text("CONFIG_SMP=y\n")
        (self.base / "arch/x86/boot/bzImage").write_bytes(b"base kernel")
        self.toolchain = self.root / "toolchain"
        bin_dir = self.toolchain / "bin"
        bin_dir.mkdir(parents=True)
        gcc = bin_dir / "x86_64-synveil-linux-gnu-gcc"
        gcc.write_text("#!/bin/sh\nexit 0\n")
        gcc.chmod(0o755)
        make = bin_dir / "make"
        make.write_text(FAKE_MAKE)
        make.chmod(0o755)
        self.env = {
            **os.environ,
            "SYNVEIL_KERNEL_SEED_WORK": str(self.work),
            "SYNVEIL_TOOLCHAIN_DIR": str(self.toolchain),
            "SYNVEIL_TARGET": "x86_64-synveil-linux-gnu",
            "SYNVEIL_TEST_MAKE_LOG": str(self.root / "make.log"),
            "SYNVEIL_TEST_CONFIG_ARGS": str(self.root / "config-args"),
        }

    def invoke(self, case, payload=b"payload", *, seed=None, recovery=False):
        out = self.root / "out" / case
        out.mkdir(parents=True, exist_ok=True)
        (out / "synveil-initramfs.cpio").write_bytes(payload)
        env = {
            **self.env,
            "SYNVEIL_OUT_DIR": str(out),
            "SYNVEIL_WORK_DIR": str(self.root / "isolated" / case),
            "SYNVEIL_KERNEL_SEED_WORK": str(seed or self.work),
            "SYNVEIL_KERNEL_TEST_RECOVERY_STATUS": "1" if recovery else "0",
        }
        result = subprocess.run(
            ["bash", str(self.root / "build/scripts/kernel.sh")],
            env=env, capture_output=True, text=True, timeout=10,
        )
        return result, out

    def test_separate_images_and_baseline_integrity(self):
        baseline = (self.base / "arch/x86/boot/bzImage").read_bytes()
        first, out_a = self.invoke("first", b"alpha")
        self.assertEqual(first.returncode, 0, first.stderr)
        cache = self.work / "guest-fixtures/shared-kernel-build"
        self.assertTrue((cache / ".synveil-base-identity").is_file())
        marker = cache / "preserve-across-fixtures"
        marker.write_text("marker")
        second, out_b = self.invoke("second", b"beta", recovery=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(marker.read_text(), "marker")
        self.assertEqual((out_a / "synveil-x86_64.efi").read_bytes(), b"alpha0\n")
        self.assertEqual((out_b / "synveil-x86_64.efi").read_bytes(), b"beta1\n")
        self.assertEqual((self.base / "arch/x86/boot/bzImage").read_bytes(), baseline)
        self.assertNotIn("x86_64_defconfig", (self.root / "make.log").read_text())
        self.assertIn("INITRAMFS_SOURCE", (self.root / "config-args").read_text())
        self.assertIn("CMDLINE", (self.root / "config-args").read_text())

    def test_baseline_identity_change_invalidates_guest_cache(self):
        first, _ = self.invoke("initial")
        self.assertEqual(first.returncode, 0, first.stderr)
        cache = self.work / "guest-fixtures/shared-kernel-build"
        marker = cache / "stale"
        marker.write_text("do not reuse")
        with (self.base / ".config").open("a") as config:
            config.write("# new baseline identity\n")
        again, _ = self.invoke("changed")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(marker.exists())

    def test_missing_or_symlinked_seed_refuses_without_touching_base(self):
        original = (self.base / "arch/x86/boot/bzImage").read_bytes()
        missing, _ = self.invoke("missing", seed=self.root / "missing")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("complete base kernel build required", missing.stderr)
        link = self.root / "link"
        link.symlink_to(self.work, target_is_directory=True)
        denied, _ = self.invoke("symlink", seed=link)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("non-symlink work directory", denied.stderr)
        self.assertEqual((self.base / "arch/x86/boot/bzImage").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
