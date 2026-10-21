#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-network, no-privilege tests for the opt-in multiwindow desktop preview."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class DesktopLiveProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "checkout"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "experiments/desktop-live").mkdir(parents=True)
        shutil.copy2(ROOT / "tools/desktop_live.sh", self.root / "tools/desktop_live.sh")
        for name in ("packages.list.chroot", "live-config.conf", "lightdm.conf",
                     "guest-proof-session.sh", "guest-proof-relay.sh",
                     "guest-proof.desktop", "guest-proof.service",
                     "login-gate-prestart.sh", "login-gate-reporter.sh",
                     "login-gate-lightdm.conf", "login-gate-reporter.service",
                     "login-enroll-prestart.sh", "login-enroll-reporter.sh",
                     "login-enroll-lightdm.conf", "login-enroll-reporter.service",
                     "login-enroll-session-logout.sh",
                     "login-enroll-session-logout.desktop"):
            shutil.copy2(ROOT / "experiments/desktop-live" / name,
                         self.root / "experiments/desktop-live" / name)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        (self.bin / "lb").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" >> "$TEST_LB_LOG"\n'
        )
        (self.bin / "id").write_text(
            '#!/bin/sh\n[ "$1" = -u ] || exit 3\nprintf "%s\\n" "$TEST_UID"\n'
        )
        for name in ("lb", "id"):
            (self.bin / name).chmod(0o755)
        self.log = self.root / "lb-commands"
        self.env = {**os.environ,
                    "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "TEST_LB_LOG": str(self.log),
                    "TEST_UID": "1000"}

    def invoke(self, *args, uid="1000", login_gate="0"):
        return subprocess.run(
            ["bash", str(self.root / "tools/desktop_live.sh"), *args],
            env={**self.env, "TEST_UID": uid,
                 "SYNVEIL_DESKTOP_LOGIN_GATE": login_gate},
            capture_output=True, text=True, timeout=10,
        )

    def test_explicit_configuration_creates_only_directory_files(self):
        result = self.invoke("configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / "out/desktop-live"
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        packages = (out / "config/package-lists/synveil-desktop.list.chroot").read_text()
        for p in ("xfwm4", "xfce4-panel", "xfce4-terminal", "thunar",
                  "xfce4-settings", "lightdm", "network-manager"):
            self.assertIn(p + "\n", packages)
        self.assertNotIn("calamares\n", packages)
        args = self.log.read_text().splitlines()
        for required in ("config", "trixie", "amd64", "iso-hybrid", "none",
                         "main contrib non-free-firmware"):
            self.assertIn(required, args)
        opts = args[args.index("--bootappend-live") + 1]
        self.assertIn("live-config.noroot", opts)
        self.assertIn("boot=live components", opts)
        self.assertIn("console=ttyS0,115200n8", opts)
        self.assertIn("console=tty0", opts)
        for flag in ("persistence", "persistence-label=synvmhome",
                     "persistence-storage=filesystem"):
            self.assertIn(flag, opts)

        failsafe = args[args.index("--bootappend-live-failsafe") + 1]
        self.assertIn("nomodeset", failsafe)
        self.assertIn("live-config.noroot", failsafe)
        self.assertIn("persistence-label=synvmhome", failsafe)

        self.assertTrue((out / ".synveil-desktop-live-prototype").exists())
        self.assertFalse(any(out.rglob("*.iso")))
        self.assertFalse((self.root / "out/synveil-x86_64.img").exists())

    def test_refuses_existing_tree_and_symlink_without_modification(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        previous = self.log.read_bytes()
        duplicate = self.invoke("configure")
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.log.read_bytes(), previous)
        shutil.rmtree(self.root / "out/desktop-live")
        victim = self.root / "unrelated"
        victim.mkdir()
        (victim / "keep").write_text("SAFE")
        (self.root / "out/desktop-live").symlink_to(victim, target_is_directory=True)
        denied = self.invoke("configure")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual((victim / "keep").read_text(), "SAFE")

    def test_symlink_output_parent_is_rejected(self):
        victim = self.root / "unrelated"
        victim.mkdir()
        (self.root / "out").symlink_to(victim, target_is_directory=True)
        denied = self.invoke("configure")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(list(victim.iterdir()), [])

    def test_build_is_separate_explicit_and_requires_privileges(self):
        self.assertNotEqual(self.invoke("build", uid="0").returncode, 0)
        self.assertEqual(self.invoke("configure").returncode, 0)
        before = self.log.read_text()
        rejected = self.invoke("build", uid="1000")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.log.read_text(), before)
        accepted = self.invoke("build", uid="0")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.log.read_text().splitlines()[-1], "build")
        # The fake live-build never touches disks or creates a boot ISO.
        self.assertFalse(any((self.root / "out").rglob("*.iso")))

    def test_existing_iso_is_not_overwritten(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        iso = self.root / "out/desktop-live/synveil-desktop-preview-amd64.hybrid.iso"
        iso.write_bytes(b"already exists")
        result = self.invoke("build", uid="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(iso.read_bytes(), b"already exists")

    def test_proof_is_session_only_and_reports_actual_window_operations(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        base = self.root / "out/desktop-live/config/includes.chroot"
        session = base / "usr/local/bin/synveil-guest-proof-session"
        reporter = base / "usr/local/libexec/synveil-guest-proof-relay"
        autostart = base / "etc/xdg/autostart/synveil-guest-proof.desktop"
        service = base / "etc/systemd/system/synveil-guest-proof.service"
        wants = base / "etc/systemd/system/multi-user.target.wants/synveil-guest-proof.service"
        for p in (session, reporter):
            self.assertTrue(p.is_file())
            self.assertTrue(p.stat().st_mode & 0o111)
            subprocess.run(["sh", "-n", str(p)], check=True)
        self.assertIn("Exec=/usr/local/bin/synveil-guest-proof-session",
                      autostart.read_text())
        self.assertIn("ExecStart=/usr/local/libexec/synveil-guest-proof-relay",
                      service.read_text())
        self.assertTrue(wants.is_symlink())
        self.assertEqual(wants.resolve(), service.resolve())
        text = session.read_text()
        for operation in ("wmctrl -ir", "xwininfo -id", "xdotool windowminimize",
                          "xprop -id", "thunar", "xfce4-terminal"):
            self.assertIn(operation, text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS", text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_PROBE_FAILED", text)
        for operation in ("xfce4-settings-manager", "pgrep -u", "xfce4-panel",
                          "wmctrl -ia \"$settings\"", 'xwininfo -id "$settings"'):
            self.assertIn(operation, text)
        packages = (self.root / "out/desktop-live/config/package-lists/"
                    "synveil-desktop.list.chroot").read_text()
        self.assertIn("procps\n", packages)
        relay_text = reporter.read_text()
        self.assertIn("SYNVEIL_QEMU_RELAY_STARTED", relay_text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_PROBE_STARTED", relay_text)

    def test_optional_locked_greeter_isolated_from_autologin_profile(self):
        self.assertEqual(self.invoke("configure", login_gate="1").returncode, 0)
        args = self.log.read_text().splitlines()
        normal = args[args.index("--bootappend-live") + 1]
        failsafe = args[args.index("--bootappend-live-failsafe") + 1]
        for flags in (normal, failsafe):
            self.assertIn("live-config.noautologin", flags)
            self.assertIn("live-config.noroot", flags)
        root = self.root / "out/desktop-live/config/includes.chroot"
        marker = root / "etc/synveil/ci-login-gate"
        prestart = root / "usr/local/libexec/synveil-login-gate-prestart"
        reporter = root / "usr/local/libexec/synveil-login-gate-reporter"
        greeter = root / "etc/lightdm/lightdm.conf.d/90-synveil-ci-login-gate.conf"
        unit = root / "etc/systemd/system/lightdm.service.d/50-synveil-ci-login-gate.conf"
        wants = (root / "etc/systemd/system/graphical.target.wants/"
                 "synveil-login-gate-reporter.service")
        self.assertTrue(marker.is_file())
        self.assertTrue(prestart.stat().st_mode & 0o111)
        self.assertTrue(reporter.stat().st_mode & 0o111)
        self.assertTrue(wants.is_symlink())
        self.assertEqual(wants.resolve(), (root / "etc/systemd/system/"
                         "synveil-login-gate-reporter.service").resolve())
        for sh in (prestart, reporter):
            subprocess.run(["sh", "-n", str(sh)], check=True)
        self.assertIn("passwd -l synveil", prestart.read_text())
        self.assertIn("getent passwd synveil", prestart.read_text())
        self.assertIn("ExecStartPre=/usr/local/libexec/synveil-login-gate-prestart",
                      unit.read_text())
        self.assertIn("allow-guest=false", greeter.read_text())
        self.assertIn("autologin-user=", greeter.read_text())
        self.assertIn("SYNVEIL_QEMU_LOGIN_GREETER_LOCKED", reporter.read_text())
        self.assertTrue((self.root / "out/desktop-live/.synveil-desktop-login-gate")
                        .is_file())

    def test_opt_in_pam_login_uses_fw_cfg_and_no_baked_credential(self):
        self.assertEqual(self.invoke("configure", login_gate="2").returncode, 0)
        args = self.log.read_text().splitlines()
        for option in ("--bootappend-live", "--bootappend-live-failsafe"):
            self.assertIn("live-config.noautologin", args[args.index(option) + 1])
        root = self.root / "out/desktop-live/config/includes.chroot"
        self.assertTrue((root / "etc/synveil/ci-login-enroll").exists())
        self.assertFalse((root / "etc/synveil/ci-login-gate").exists())
        prestart = root / "usr/local/libexec/synveil-login-enroll-prestart"
        reporter = root / "usr/local/libexec/synveil-login-enroll-reporter"
        logout = root / "usr/local/libexec/synveil-login-enroll-session-logout"
        logout_autostart = root / ("etc/xdg/autostart/"
                                   "synveil-login-enroll-session-logout.desktop")
        for script in (prestart, reporter, logout):
            self.assertTrue(script.is_file())
            subprocess.run(["sh", "-n", str(script)], check=True)
        src = prestart.read_text()
        self.assertIn("qemu_fw_cfg", src)
        self.assertIn("chpasswd", src)
        self.assertIn("synveil/ci-secret/raw", src)
        # A numeric regexp character class is not a built-in password.
        self.assertIn("IFS= read -r secret", src)
        self.assertIn("printf 'synveil:%s", src)
        self.assertNotIn('chpasswd --encrypted', src)
        self.assertNotIn('secret=hardcoded', src)
        self.assertIn("SYNVEIL_QEMU_TEST_AUTH_SESSION_ACTIVE", reporter.read_text())
        self.assertIn("SYNVEIL_QEMU_TEST_AUTH_RELOCKED", reporter.read_text())
        self.assertIn("SYNVEIL_QEMU_TEST_AUTH_RELOGIN_ACTIVE", reporter.read_text())
        self.assertIn("pgrep -u", reporter.read_text())
        self.assertIn("exec xfce4-session-logout --logout --fast", logout.read_text())
        self.assertIn("SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS", logout.read_text())
        self.assertIn("Exec=/usr/local/libexec/synveil-login-enroll-session-logout",
                      logout_autostart.read_text())
        self.assertIn("pgrep -u", reporter.read_text())
        self.assertTrue((root / "etc/systemd/system/lightdm.service.d/"
                         "50-synveil-ci-login-enroll.conf").is_file())
        self.assertTrue((root / "etc/lightdm/lightdm.conf.d/"
                         "90-synveil-ci-login-enroll.conf").is_file())
        wants = root / ("etc/systemd/system/graphical.target.wants/"
                        "synveil-login-enroll-reporter.service")
        self.assertTrue(wants.is_symlink())
        self.assertTrue((self.root / "out/desktop-live/"
                         ".synveil-desktop-login-enroll").is_file())
        runner = (ROOT / "tests/smoke-desktop-live.py").read_text()
        self.assertIn("secrets.token_hex(16)", runner)
        self.assertIn("name=opt/synveil/ci-secret,string=", runner)
        self.assertIn("type_test_secret(qmp, wrong_secret", runner)
        self.assertIn("type_test_secret(qmp, ephemeral_secret", runner)
        self.assertIn('pam_stage = "relock"', runner)
        self.assertIn('pam_stage = "relock-wrong"', runner)
        self.assertIn('pam_stage = "relogin"', runner)

    def test_default_preview_never_includes_locked_gate(self):
        self.assertEqual(self.invoke("configure", login_gate="0").returncode, 0)
        opts = self.log.read_text().splitlines()
        self.assertNotIn("live-config.noautologin",
                         opts[opts.index("--bootappend-live") + 1])
        self.assertFalse((self.root / "out/desktop-live/config/includes.chroot/"
                          "etc/synveil/ci-login-gate").exists())
        self.assertFalse((self.root / "out/desktop-live/"
                          ".synveil-desktop-login-gate").exists())
        self.assertFalse((self.root / "out/desktop-live/config/includes.chroot/"
                          "etc/xdg/autostart/"
                          "synveil-login-enroll-session-logout.desktop").exists())

    def test_invalid_login_gate_rejected_before_configuration(self):
        bad = self.invoke("configure", login_gate="anything")
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.root / "out/desktop-live").exists())

    def test_qemu_status_requires_exact_guest_marker_and_real_scanout(self):
        spec = importlib.util.spec_from_file_location(
            "desktop_smoke", ROOT / "tests/smoke-desktop-live.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.probe_status(b"EFI loading\n"), "pending")
        self.assertEqual(module.probe_status(b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n"),
                         "passed")
        self.assertEqual(module.probe_status(
            b"SYNVEIL_QEMU_DESKTOP_PROBE_FAILED\n"
            b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n"), "failed")
        self.assertEqual(module.probe_status(
            b"X-SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS-Y\n"), "pending")
        image = self.root / "screen.ppm"
        # Deterministic RGB gradient, 320x200, not a single flat color.
        pixels = bytes((n % 256 for n in range(320 * 200 * 3)))
        image.write_bytes(b"P6\n320 200\n255\n" + pixels)
        self.assertTrue(module.screenshot_is_nonblank(image))
        image.write_bytes(b"P6\n320 200\n255\n" + bytes(320 * 200 * 3))
        self.assertFalse(module.screenshot_is_nonblank(image))

    def test_boot_menu_detection_is_exact_and_only_sends_return(self):
        spec = importlib.util.spec_from_file_location(
            "desktop_smoke_menu", ROOT / "tests/smoke-desktop-live.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        width, height = 800, 600
        raw = bytearray(width * height * 3)
        # Synthesize the verified Debian live menu signature: mostly black,
        # with a large gold logo in the upper-left. No OCR or fixture images.
        for y in range(50, 200):
            for x in range(45, 180):
                i = 3 * (y * width + x)
                raw[i:i + 3] = b"\xd3\xbc\x00"
        with tempfile.TemporaryDirectory() as temporary:
            screen = Path(temporary) / "probe.ppm"
            screen.write_bytes(b"P6\n800 600\n255\n" + raw)
            self.assertTrue(module.is_debian_live_boot_menu(screen))
            with patch.object(module, "capture_screenshot"), \
                 patch.object(module, "send_keys") as sender:
                self.assertTrue(module.advance_known_live_menu(
                    Path("/tmp/unused-qmp.sock"), screen))
                sender.assert_called_once_with(
                    Path("/tmp/unused-qmp.sock"), ("ret",), timeout=8)
            with patch.object(module, "capture_screenshot"), \
                 patch.object(module, "send_keys") as sender:
                screen.write_bytes(b"P6\n800 600\n255\n" +
                                   bytes(width * height * 3))
                self.assertFalse(module.advance_known_live_menu(
                    Path("/tmp/unused-qmp.sock"), screen))
                sender.assert_not_called()
            # An ordinary black QEMU/firmware screen never gets typed into.
            self.assertFalse(module.is_debian_live_boot_menu(screen))

    def test_pam_relock_markers_are_exact_and_stage_specific(self):
        spec = importlib.util.spec_from_file_location(
            "pam_relock_serial", ROOT / "tests/smoke-desktop-live.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for marker in (mod.PAM_RELOCKED, mod.PAM_RELOGIN_ACTIVE):
            self.assertIn(marker, mod.locked_greeter_serial_markers(
                b"synveil login: " + marker + b"\n"))
            self.assertNotIn(marker, mod.locked_greeter_serial_markers(
                b"untrusted-prefix" + marker + b"\n"))
            self.assertNotIn(marker, mod.locked_greeter_serial_markers(
                marker + b" forged\n"))
        src = (ROOT / "tests/smoke-desktop-live.py").read_text()
        self.assertIn('pam_stage = "relock"', src)
        self.assertIn('pam_stage = "relock-wrong"', src)
        self.assertIn('pam_stage = "relogin"', src)
        self.assertIn("PAM_RELOGIN_ACTIVE in markers", src)
        self.assertIn("PAM_RELOCKED in markers", src)

    def test_lightdm_serial_getty_prompt_is_not_false_negative_or_false_positive(self):
        spec = importlib.util.spec_from_file_location(
            "login_gate_serial", ROOT / "tests/smoke-desktop-live.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yes = mod.LOGIN_LOCKED
        actual = b"synveil login: " + yes + b"\n"
        self.assertIn(yes, mod.locked_greeter_serial_markers(actual))
        self.assertIn(yes, mod.locked_greeter_serial_markers(yes + b"\n"))
        for invalid in (b"untrusted: " + yes + b"\n",
                        b"xxx" + yes + b"\n",
                        yes + b" forged\n",
                        b"synveil login: xx" + yes + b"\n"):
            with self.subTest(value=invalid):
                self.assertNotIn(yes, mod.locked_greeter_serial_markers(invalid))
        self.assertIn(mod.PASS, mod.locked_greeter_serial_markers(
            b"synveil login: " + mod.PASS + b"\n"))
        self.assertIn(mod.LOGIN_FAILED, mod.locked_greeter_serial_markers(
            mod.LOGIN_FAILED + b"\n"))

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(self.root / "tools/desktop_live.sh")],
                       check=True)


if __name__ == "__main__":
    unittest.main()
