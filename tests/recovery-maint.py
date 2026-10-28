#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host contract checks for the ADR 0010 tier-2 authenticated
maintenance path (veil-recovery).

These checks pin the security shape of the implementation: init
ordering, the separately audited verifier (never the seat broker),
exact-match enumerated commands, no shell interpretation of input,
and the ESP maintenance entry. Guest validation is still required
before calling this tier demonstrated.
"""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "build/rootfs/init"
KERNEL = ROOT / "build/scripts/kernel.sh"
BINARY = ROOT / "src/veil-core/src/bin/veil-recovery.rs"
NATIVE_AB = ROOT / "tools/native_ab.py"


class AuthenticatedMaintenanceTests(unittest.TestCase):
    def test_boot_mode_follows_state_mount_precedes_services_and_locks(self):
        source = INIT.read_text()
        start = source.index("# BEGIN AUTHENTICATED_MAINTENANCE")
        end = source.index("# END AUTHENTICATED_MAINTENANCE")
        guarded = source[start:end]
        self.assertIn("synveil.recovery=maint", guarded)
        self.assertIn("/usr/sbin/veil-recovery", guarded)
        self.assertIn("serial_lock_forever", guarded)
        self.assertNotIn("/bin/sh", guarded)
        self.assertNotIn("boot_failure_recovery", guarded)
        # The verifier needs the state mount; everything else waits.
        self.assertLess(source.index("/usr/libexec/synveil/mount-state"), start)
        self.assertLess(start, source.index("/usr/libexec/synveil/start-core"))
        self.assertLess(start, source.index("/usr/libexec/synveil/start-seat"))
        self.assertLess(start, source.index("/usr/libexec/synveil/start-desktop"))
        subprocess.run(["sh", "-n", str(INIT)], check=True)

    def test_kernel_flag_is_fixture_only_and_validated(self):
        source = KERNEL.read_text()
        self.assertIn("SYNVEIL_KERNEL_TEST_RECOVERY_MAINT", source)
        self.assertIn('die "SYNVEIL_KERNEL_TEST_RECOVERY_MAINT must be 0 or 1"',
                      source)
        self.assertIn('KERNEL_CMDLINE="$KERNEL_CMDLINE synveil.recovery=maint"',
                      source)

    def test_binary_authenticates_through_adr0009_verifier_not_seat(self):
        source = BINARY.read_text()
        self.assertIn("verify_root_credential", source)
        self.assertIn("root_credential_state", source)
        self.assertIn("RootCredentialState::NoState", source)
        self.assertIn("RootCredentialState::Absent", source)
        self.assertIn("RootCredentialState::Invalid", source)
        # No seat broker, no login IPC, no shell anywhere in the path.
        self.assertNotIn("veil-seat", source)
        self.assertNotIn("seat::", source)
        self.assertNotIn("login_from_reader", source)
        self.assertNotIn('Command::new("/bin/sh")', source)
        self.assertNotIn("Command::new(\"sh\")", source)
        # The only external program is stty with fixed arguments.
        commands = [line.strip() for line in source.splitlines()
                    if "Command::new" in line]
        self.assertEqual(commands, ['Command::new("/bin/stty")'])
        self.assertIn("args([setting])", source)

    def test_commands_are_enumerated_and_exact_match(self):
        source = BINARY.read_text()
        for command in ('"status"', '"restore"', '"reboot"', '"exit"'):
            self.assertIn(command, source)
        self.assertIn("SYNVEIL_RECOVERY_MAINT_REJECTED", source)
        # Bounded session shape: attempts, commands, line lengths.
        self.assertIn("MAX_ATTEMPTS: u32 = 3", source)
        self.assertIn("MAX_COMMANDS: u32 = 16", source)
        self.assertIn("MAX_PASSWORD_LINE: usize = 1024", source)
        self.assertIn("MAX_COMMAND_LINE: usize = 64", source)
        # Restore goes through the typed A/B rollback action and its
        # postcondition is verified against a reloaded control record.
        self.assertIn("ab::rollback()", source)
        self.assertIn("verify_restore_postcondition", source)
        self.assertIn("ab::load_control(&layout)", source)
        # Password material is wiped; outcomes go to the audit log.
        self.assertIn("secret::clear(&mut password)", source)
        self.assertIn('AUDIT_LOG_PATH: &str = "/var/log/synveil/recovery-maintenance.log"',
                      source)
        self.assertIn('audit("auth-ok"', source)
        self.assertIn('audit("auth-denied"', source)

    def test_esp_carries_maintenance_entry(self):
        source = NATIVE_AB.read_text()
        self.assertIn('"EFI/synveil/maintenance.efi": maintenance["uki"]',
                      source)
        self.assertIn('env["SYNVEIL_KERNEL_TEST_RECOVERY_MAINT"] = "1"', source)
        self.assertIn('"maintenance": maintenance', source)


if __name__ == "__main__":
    unittest.main()
