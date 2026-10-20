#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Kill the guest core and require commands to work in its recovery shell."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, open_demo_terminal  # noqa: E402
from state_disk import create_state_disk  # noqa: E402
from audit_fixture import AUDIT_SEED_AWK  # noqa: E402

COMMAND = (
    "core_pid=$(pidof veil-core); "
    "if [ -n \"$core_pid\" ] && kill -KILL $core_pid; then "
    "for retry in 1 2 3 4 5 6 7 8 9 10; do "
    "kill -0 $core_pid 2>/dev/null || break; sleep 0.1; done; "
    "if ! kill -0 $core_pid 2>/dev/null && "
    "! timeout -s KILL 2 /usr/bin/synctl --json status; then "
    "mkdir -p /tmp/recovery-check && "
    "printf '%s' shell-alive > /tmp/recovery-check/probe && "
    "[ \"$(cat /tmp/recovery-check/probe)\" = shell-alive ] && "
    "printf '\\n%s%s\\n' SYNVEIL_ RECOVERY_OK; fi; fi\n"
)
STATE_RECOVERY = ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
                   b"SYNVEIL_STATE_UNAVAILABLE"},
                  b"SYNVEIL_STATE_READY", b"SYNVEIL_STATE_RECOVERY_OK",
                  "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null && "
                  "printf '%s' shell-alive > /tmp/state-recovery-probe && "
                  "[ \"$(cat /tmp/state-recovery-probe)\" = shell-alive ]; then "
                  "printf '\\n%s%s\\n' SYNVEIL_ STATE_RECOVERY_OK; fi\n")
TERMINAL_LIFETIME = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
     b"SYNVEIL_VT_READY", b"SYNVEIL_DESKTOP_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_TERMINAL_LIFETIME_OK",
    "desktop=$(pidof synveil-display); terminal=; "
    "for retry in $(seq 1 50); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "if [ -n \"$desktop\" ] && [ -n \"$terminal\" ] && "
    "kill -KILL $desktop; then "
    "for retry in $(seq 1 50); do "
    "if ! kill -0 $terminal 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ TERMINAL_LIFETIME_OK; fi; break; fi; "
    "sleep 0.1; done; fi\n"
)
ENROLL_RECOVERY = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
     b"SYNVEIL_SEAT_READY", b"SYNVEIL_VT_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_ENROLL_SMOKE_OK",
    "if [ ! -e /var/lib/synveil/auth/credential.phc ]; then "
    "secret=$(cat /proc/sys/kernel/random/uuid); "
    "if [ -n \"$secret\" ] && "
    "printf '%s' \"$secret\" | /usr/sbin/veil-enroll --password-stdin >/tmp/veil-enroll.log 2>&1 && "
    "grep -Fxq SYNVEIL_CREDENTIAL_ENROLLED /tmp/veil-enroll.log && "
    "[ \"$(stat -c '%a:%u:%h' /var/lib/synveil/auth/credential.phc)\" = 600:0:1 ] && "
    "[ \"$(stat -c '%a:%u' /var/lib/synveil/auth)\" = 700:0 ] && "
    "old=$(sha256sum /var/lib/synveil/auth/credential.phc) && "
    "! printf '%s' \"$secret\" | /usr/sbin/veil-enroll --password-stdin >/dev/null 2>&1; then "
    "if [ \"$(sha256sum /var/lib/synveil/auth/credential.phc)\" = \"$old\" ] && "
    "[ -S /run/synveil/veil-seat.sock ]; then "
    "printf '%s' \"$secret\" | /usr/sbin/veil-seat --login-stdin > /tmp/seat-client.log 2>&1 & "
    "holder=$!; terminal=; "
    "for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "if [ -n \"$terminal\" ] && "
    "grep -Fxq SYNVEIL_SEAT_ACTIVE /tmp/seat-client.log; then "
    "kill -TERM \"$holder\" && wait \"$holder\" 2>/dev/null || true; "
    "for retry in $(seq 1 60); do "
    "if ! kill -0 \"$terminal\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ ENROLL_SMOKE_OK; fi; break; fi; "
    "sleep 0.1; done; fi; fi; fi; fi\n"
)
ENROLL_REBOOT = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_ENROLL_REBOOT_OK",
    "if [ -s /var/lib/synveil/auth/credential.phc ] && "
    "[ \"$(stat -c '%a:%u:%h' /var/lib/synveil/auth/credential.phc)\" = 600:0:1 ] && "
    "grep -q '^[$]argon2id[$]v=19[$]m=65536,t=3,p=1[$]' "
    "/var/lib/synveil/auth/credential.phc; then "
    "printf '\\n%s%s\\n' SYNVEIL_ ENROLL_REBOOT_OK; fi\n"
)
SEAT_LOCKED = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_SEAT_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_SEAT_LOCKED_OK",
    "if [ -S /run/synveil/veil-seat.sock ] && "
    "[ \"$(stat -c '%a:%u' /run/synveil/veil-seat.sock)\" = 600:0 ] && "
    "[ ! -e /var/lib/synveil/auth/credential.phc ] && "
    "pidof veil-seat >/dev/null && "
    "! printf '%s' guest-no-default-login | /usr/sbin/veil-seat --login-stdin "
    ">/tmp/seat-denied.log 2>&1 && "
    "grep -q 'login denied' /tmp/seat-denied.log && "
    "timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ SEAT_LOCKED_OK; fi\n"
)
SEAT_CRASH = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
     b"SYNVEIL_SEAT_READY", b"SYNVEIL_VT_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_SEAT_CRASH_OK",
    "secret=$(cat /proc/sys/kernel/random/uuid); "
    "if [ ! -e /var/lib/synveil/auth/credential.phc ] && "
    "[ -n \"$secret\" ] && "
    "printf '%s' \"$secret\" | /usr/sbin/veil-enroll --password-stdin >/dev/null; then "
    "printf '%s' \"$secret\" | /usr/sbin/veil-seat --login-stdin >/tmp/seat-crash-client.log 2>&1 & "
    "holder=$!; broker=; terminal=; "
    "for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "for candidate in $(pidof veil-seat); do "
    "child=$(cat /proc/$candidate/task/$candidate/children 2>/dev/null); "
    "if [ -n \"$child\" ]; then broker=$candidate; break; fi; done; "
    "if [ -n \"$terminal\" ] && [ -n \"$broker\" ] && "
    "grep -Fxq SYNVEIL_SEAT_ACTIVE /tmp/seat-crash-client.log && "
    "kill -KILL \"$broker\"; then "
    "for retry in $(seq 1 60); do "
    "if ! kill -0 \"$terminal\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ SEAT_CRASH_OK; fi; break; fi; "
    "sleep 0.1; done; fi; fi\n"
)
AUDIT_ROTATION = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_AUDIT_ROTATION_OK",
    'd=/var/log/synveil-audit-rotation-test; journal="$d/journal";\n'
    'if mkdir -m 700 "$d" && awk \'BEGIN {\n'
    '    prefix = "{\\"schema\\":\\"synveil.audit/v1\\",\\"component\\":\\"veil-core\\",\\"pid\\":1,\\"sequence\\":1,\\"unix_time_ms\\":null,\\"version\\":\\"fixture\\",\\"git_revision\\":null,\\"event\\":\\"guest_fixture\\",\\"fields\\":{\\"padding\\":\\""\n'
    '    suffix = "\\"}}\\n"\n'
    '    padding = ""\n'
    '    for (i = length(prefix) + length(suffix); i < 8192; i++) padding = padding "x"\n'
    '    line = prefix padding suffix\n'
    '    for (i = 0; i < 512; i++) printf "%s", line\n'
    '}\' > "$journal" &&\n'
    'chmod 600 "$journal" && [ "$(stat -c %s "$journal")" = 4194304 ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null; then\n'
    'old=$(sha256sum "$journal" | cut -d " " -f1);\n'
    'SYNVEIL_AUDIT_PATH="$journal" timeout -s KILL 10 /usr/sbin/veil-core --once --socket "$d/core.sock" >"$d/core.log" 2>&1 & helper=$!; ok=;\n'
    'for retry in $(seq 1 40); do\n'
    'if SYNVEIL_EXPECT_AUDIT_STATE=healthy timeout -s KILL 2 /usr/bin/synctl --socket "$d/core.sock" --json status >"$d/status.json" 2>/dev/null; then ok=1; break; fi; sleep 0.1; done;\n'
    'if [ "$ok" = 1 ] && wait "$helper" &&\n'
    'grep -Fq \'"persistent_audit": {\' "$d/status.json" &&\n'
    'grep -Fq \'"configured": true\' "$d/status.json" &&\n'
    'grep -Fq \'"state": "healthy"\' "$d/status.json" &&\n'
    'grep -Fq \'"failed_writes": 0\' "$d/status.json" &&\n'
    '[ "$(sha256sum "$journal.1" | cut -d " " -f1)" = "$old" ] &&\n'
    '[ "$(stat -c "%a:%u:%h" "$journal.1")" = 600:0:1 ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal.1" >"$d/archive-report.json" &&\n'
    'grep -Fq \'"records":512\' "$d/archive-report.json" &&\n'
    'mkdir -m 700 "$d/exports" &&\n'
    '/usr/sbin/veil-audit export --path "$journal.1" --output "$d/exports/snapshot" >"$d/export-receipt.json" &&\n'
    'digest=$(sed -n \'s/.*"digest":"\\([0-9a-f]*\\)".*/\\1/p\' "$d/export-receipt.json") &&\n'
    '[ "${#digest}" = 128 ] &&\n'
    '/usr/sbin/veil-audit verify --path "$d/exports/snapshot" --digest "$digest" >/dev/null &&\n'
    '[ "$(stat -c "%a:%u:%h" "$d/exports/snapshot")" = 600:0:1 ] &&\n'
    '! /usr/sbin/veil-audit export --path "$journal.1" --output "$d/exports/snapshot" >/dev/null 2>&1 &&\n'
    '[ "$(sha256sum "$d/exports/snapshot" | cut -d " " -f1)" = "$old" ] &&\n'
    'sed s/guest_fixture/guest_changed/ "$d/exports/snapshot" >"$d/exports/changed" &&\n'
    'chmod 600 "$d/exports/changed" &&\n'
    '/usr/sbin/veil-audit check --path "$d/exports/changed" >/dev/null &&\n'
    '! /usr/sbin/veil-audit verify --path "$d/exports/changed" --digest "$digest" >/dev/null 2>&1 &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then\n'
    # All replacements below are synthetic fixture data in this private test
    # directory. Never fill, truncate or alter the main core's journal.
    'audit_probe() {\n'
    'log="$d/$1.log"; socket="$d/$1.sock"; status="$d/$1.status.json"; expected="$2";\n'
    'SYNVEIL_AUDIT_PATH="$journal" timeout -s KILL 10 /usr/sbin/veil-core --once --socket "$socket" >"$log" 2>&1 & helper=$!; ok=;\n'
    'for retry in $(seq 1 40); do\n'
    'if SYNVEIL_EXPECT_AUDIT_STATE="$expected" timeout -s KILL 2 /usr/bin/synctl --socket "$socket" --json status >"$status" 2>/dev/null; then ok=1; break; fi; sleep 0.1; done;\n'
    'failed=$(sed -n \'s/.*"failed_writes": \\([0-9][0-9]*\\).*/\\1/p\' "$status");\n'
    '[ "$ok" = 1 ] && wait "$helper" &&\n'
    'grep -Fq \'"persistent_audit": {\' "$status" &&\n'
    'grep -Fq \'"configured": true\' "$status" &&\n'
    'grep -Fq "\\"state\\": \\"$expected\\"" "$status" &&\n'
    '{ [ "$expected" != healthy ] || [ "$failed" = 0 ]; } &&\n'
    '{ [ "$expected" != degraded ] || [ "$failed" -gt 0 ]; } &&\n'
    'grep -Fq \'"event":"request"\' "$log" &&\n'
    'grep -Fq \'"event":"shutdown"\' "$log";\n'
    '};\n'
    'if cp "$journal.1" "$journal" &&\n'
    'audit_probe capacity degraded &&\n'
    'grep -Fq "audit archive exists; retention capacity reached" "$d/capacity.log" &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$old" ] &&\n'
    '[ "$(sha256sum "$journal.1" | cut -d " " -f1)" = "$old" ] &&\n'
    '[ "$(stat -c "%a:%u:%h:%s" "$journal")" = 600:0:1:4194304 ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    '/usr/sbin/veil-audit check --path "$journal.1" >/dev/null &&\n'
    'printf \'{"schema":\' > "$journal"; then\n'
    'tail=$(sha256sum "$journal" | cut -d " " -f1);\n'
    'if audit_probe incomplete degraded &&\n'
    'grep -Fq "persistent audit journal has an incomplete final record" "$d/incomplete.log" &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$tail" ] &&\n'
    '[ "$(sha256sum "$journal.1" | cut -d " " -f1)" = "$old" ] &&\n'
    '! /usr/sbin/veil-audit check --path "$journal" >/dev/null 2>&1 &&\n'
    'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then\n'
    "printf '\\n%s%s\\n' SYNVEIL_ AUDIT_ROTATION_OK; fi; fi; fi; fi\n"
)

AUDIT_ENOSPC_SEED_AWK = AUDIT_SEED_AWK.replace("8192", "4096").replace("512", "1")
AUDIT_ENOSPC = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_AUDIT_ENOSPC_OK",
    'd=/run/synveil-audit-enospc-test; store="$d/store"; passed=;\n'
    'if mkdir -m 700 "$d" && mkdir -m 700 "$store" &&\n'
    'mount -t tmpfs -o size=1048576,mode=0700,nosuid,nodev tmpfs "$store"; then\n'
    'journal="$store/journal";\n'
    'audit_probe() {\n'
    'log="$d/$1.log"; socket="$d/$1.sock"; status="$d/$1.status.json"; expected="$2";\n'
    'SYNVEIL_AUDIT_PATH="$journal" timeout -s KILL 10 /usr/sbin/veil-core --once --socket "$socket" >"$log" 2>&1 & helper=$!; ok=;\n'
    'for retry in $(seq 1 40); do\n'
    'if SYNVEIL_EXPECT_AUDIT_STATE="$expected" timeout -s KILL 2 /usr/bin/synctl --socket "$socket" --json status >"$status" 2>/dev/null; then ok=1; break; fi; sleep 0.1; done;\n'
    'failed=$(sed -n \'s/.*"failed_writes": \\([0-9][0-9]*\\).*/\\1/p\' "$status");\n'
    '[ "$ok" = 1 ] && wait "$helper" &&\n'
    'grep -Fq \'"persistent_audit": {\' "$status" &&\n'
    'grep -Fq \'"configured": true\' "$status" &&\n'
    'grep -Fq "\\"state\\": \\"$expected\\"" "$status" &&\n'
    '{ [ "$expected" != healthy ] || [ "$failed" = 0 ]; } &&\n'
    '{ [ "$expected" != degraded ] || [ "$failed" -gt 0 ]; } &&\n'
    'grep -Fq \'"event":"request"\' "$log" &&\n'
    'grep -Fq \'"event":"shutdown"\' "$log";\n'
    '};\n'
    "if awk '" + AUDIT_ENOSPC_SEED_AWK + "' > \"$journal\" &&\n"
    'chmod 600 "$journal" && : > "$store/.audit-lock" && chmod 600 "$store/.audit-lock" &&\n'
    'mkdir -m 700 "$store/copies" &&\n'
    '[ "$(stat -c %s "$journal")" = 4096 ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null; then\n'
    'old=$(sha256sum "$journal" | cut -d " " -f1);\n'
    'if ! dd if=/dev/zero of="$store/filler" bs=1024 count=2048 2>"$d/fill.log" &&\n'
    'audit_probe full degraded &&\n'
    'grep -Fq "No space left on device" "$d/full.log" &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$old" ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >"$d/full-report.json" &&\n'
    'grep -Fq \'"records":1,\' "$d/full-report.json" &&\n'
    '! /usr/sbin/veil-audit export --path "$journal" --output "$store/copies/snapshot" >"$d/export-full.log" 2>&1 &&\n'
    'grep -Fq "StorageFull" "$d/export-full.log" &&\n'
    '[ ! -e "$store/copies/snapshot" ] &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$old" ] &&\n'
    'rm "$store/filler" && audit_probe resumed healthy &&\n'
    '! grep -Fq "persistent write failed" "$d/resumed.log" &&\n'
    '[ "$(stat -c %s "$journal")" -gt 4096 ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    'mount -o remount,ro "$store"; then\n'
    'before_ro=$(sha256sum "$journal" | cut -d " " -f1);\n'
    'if audit_probe readonly degraded &&\n'
    'grep -Fq "Read-only file system" "$d/readonly.log" &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$before_ro" ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    '! /usr/sbin/veil-audit export --path "$journal" --output "$store/copies/snapshot" >"$d/export-readonly.log" 2>&1 &&\n'
    'grep -Fq "ReadOnlyFilesystem" "$d/export-readonly.log" &&\n'
    '[ ! -e "$store/copies/snapshot" ] &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" = "$before_ro" ] &&\n'
    'mount -o remount,rw "$store" && audit_probe writable healthy &&\n'
    '! grep -Fq "persistent write failed" "$d/writable.log" &&\n'
    '[ "$(sha256sum "$journal" | cut -d " " -f1)" != "$before_ro" ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    '/usr/sbin/veil-audit export --path "$journal" --output "$store/copies/snapshot" >"$d/export-recovered.json" &&\n'
    'digest=$(sed -n \'s/.*"digest":"\\([0-9a-f]*\\)".*/\\1/p\' "$d/export-recovered.json") &&\n'
    '[ "${#digest}" = 128 ] &&\n'
    '/usr/sbin/veil-audit verify --path "$store/copies/snapshot" --digest "$digest" >/dev/null &&\n'
    '[ "$(sha256sum "$store/copies/snapshot" | cut -d " " -f1)" = "$(sha256sum "$journal" | cut -d " " -f1)" ] &&\n'
    'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then passed=1; fi; fi; fi;\n'
    'if umount "$store" && [ "$passed" = 1 ]; then\n'
    "printf '\\n%s%s\\n' SYNVEIL_ AUDIT_ENOSPC_OK; fi; fi\n"
)

# Reuse the same native probes and byte-preservation/export checks on ext4.
# Only the bounded filler and recovery section differ from the tmpfs gate.
_ext4_script = AUDIT_ENOSPC[3].replace(
    'd=/run/synveil-audit-enospc-test; store="$d/store"; passed=;',
    'd=/run/synveil-audit-ext4-test; store=/var/lib/synveil/audit-ext4-test; passed=;'
).replace(
    'if mkdir -m 700 "$d" && mkdir -m 700 "$store" &&\n'
    'mount -t tmpfs -o size=1048576,mode=0700,nosuid,nodev tmpfs "$store"; then\n',
    "if awk '$1==\"/dev/vdb\" && $2==\"/var\" && $3==\"ext4\" { found=1 } END { exit !found }' /proc/mounts &&\n"
    'mkdir -m 700 "$d" && mkdir -m 700 "$store"; then\n'
).replace('count=2048', 'count=65536').replace(
    'audit_probe full degraded &&\n',
    'grep -Fq "No space left on device" "$d/fill.log" && sync &&\n'
    'audit_probe full degraded &&\n'
).replace(
    'rm "$store/filler" && audit_probe resumed healthy &&\n',
    'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null &&\n'
    'rm "$store/filler" && sync && audit_probe resumed healthy &&\n'
)
_ext4_ro_start = _ext4_script.index('mount -o remount,ro')
_ext4_ro_end = _ext4_script.index('/usr/sbin/veil-audit export', _ext4_script.index('audit_probe writable'))
_ext4_script = _ext4_script[:_ext4_ro_start] + _ext4_script[_ext4_ro_end:]
_ext4_script = _ext4_script.replace('then passed=1; fi; fi; fi;', 'then passed=1; fi; fi;').replace(
    'if umount "$store" && [ "$passed" = 1 ]; then',
    'if [ "$passed" = 1 ]; then'
).replace('AUDIT_ENOSPC_OK', 'AUDIT_EXT4_OK')
AUDIT_EXT4 = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_AUDIT_EXT4_OK", _ext4_script,
)

SCENARIOS = {
    "audit-ext4": AUDIT_EXT4,
    "audit-enospc": AUDIT_ENOSPC,
    "audit-rotation": AUDIT_ROTATION,
    "seat-crash": SEAT_CRASH,
    "seat-locked": SEAT_LOCKED,
    "enrolled-reboot": ENROLL_REBOOT,
    "enroll": ENROLL_RECOVERY,
    "desktop-terminal": TERMINAL_LIFETIME,
    "state-missing": STATE_RECOVERY,
    "state-corrupt": STATE_RECOVERY,
    "desktop": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
                 b"SYNVEIL_DESKTOP_READY"},
                b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_DESKTOP_RECOVERY_OK",
                "old=$(pidof synveil-display); "
                "if [ -n \"$old\" ] && kill -KILL $old; then "
                "for retry in $(seq 1 45); do "
                "current=$(pidof synveil-display); "
                "if [ -n \"$current\" ] && [ \"$current\" != \"$old\" ] && "
                "timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
                "printf '\\n%s%s\\n' SYNVEIL_ DESKTOP_RECOVERY_OK; break; fi; "
                "sleep 0.1; done; fi\n"),
    "demo": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
              b"SYNVEIL_VT_READY", b"SYNVEIL_DESKTOP_READY"},
             b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_DEMO_OK",
             "if [ -x /usr/bin/synveil-vt ] && "
             "grep -q '^synveil:x:1000:1000:' /etc/passwd; then "
             "/usr/bin/synveil-vt --demo-terminal >/tmp/demo-terminal.log 2>&1 & "
             "for retry in $(seq 1 30); do "
             "for status in /proc/[0-9]*/status; do "
             "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
             "grep -Eq '^Gid:[[:space:]]*1000' \"$status\" && "
             "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
             "pid=$(basename $(dirname \"$status\")); "
             "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
             "printf '\\n%s%s\\n' SYNVEIL_ DEMO_OK; "
             "break 2; fi; fi; done; "
             "sleep 0.1; done; fi\n"),
    "supervision": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_SUPERVISION_OK",
                    "is_alive() { kill -0 \"$1\" 2>/dev/null && "
                    "! grep -q '^State:.*Z' /proc/$1/status 2>/dev/null; }; "
                    "supervisor=; worker=; "
                    "for parent in $(pidof veil-core); do "
                    "child=$(cat /proc/$parent/task/$parent/children 2>/dev/null); "
                    "if [ -n \"$child\" ]; then supervisor=$parent; worker=$child; break; fi; done; "
                    "restarted=; "
                    "if [ -n \"$supervisor\" ] && kill -KILL $worker; then "
                    "for retry in $(seq 1 40); do "
                    "child=$(cat /proc/$supervisor/task/$supervisor/children 2>/dev/null); "
                    "if [ -n \"$child\" ] && [ \"$child\" != \"$worker\" ] && "
                    "timeout -s KILL 1 /usr/bin/synctl --json status; then restarted=1; break; fi; "
                    "sleep 0.1; done; fi; "
                    "if [ \"$restarted\" = 1 ]; then "
                    "for round in 1 2 3; do "
                    "child=$(cat /proc/$supervisor/task/$supervisor/children 2>/dev/null); "
                    "[ -n \"$child\" ] && kill -KILL $child; sleep 0.5; done; "
                    "for retry in $(seq 1 20); do "
                    "is_alive $supervisor || break; sleep 0.1; done; "
                    "if ! is_alive $supervisor && "
                    "! timeout -s KILL 2 /usr/bin/synctl --json status; then "
                    "printf '%s' shell-alive > /tmp/supervision-probe && "
                    "[ \"$(cat /tmp/supervision-probe)\" = shell-alive ] && "
                    "printf '\\n%s%s\\n' SYNVEIL_ SUPERVISION_OK; fi; fi\n"),
    "crash": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
              b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_RECOVERY_OK", COMMAND),
    "absent": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_UNAVAILABLE"},
               b"SYNVEIL_CORE_READY", b"SYNVEIL_ABSENT_OK",
               "if [ ! -e /usr/sbin/veil-core ] && ! pidof veil-core; then "
               "printf '%s' shell-alive > /tmp/absent-probe && "
               "[ \"$(cat /tmp/absent-probe)\" = shell-alive ] && "
               "printf '\\n%s%s\\n' SYNVEIL_ ABSENT_OK; fi\n"),
    "observe": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_OBSERVE_OK",
                "if timeout -s KILL 2 /usr/bin/synctl --json observe > /tmp/synveil-observe.json && "
                "grep -Fq 'synveil.observe/v1' /tmp/synveil-observe.json && "
                "grep -Fq 'total_bytes' /tmp/synveil-observe.json && "
                "grep -Fq 'total_ticks' /tmp/synveil-observe.json && "
                "grep -Fq '\"busy_basis_points\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"sample_window_ms\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"logical_cpus\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"frequency\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"thermal\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"processes\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"start_time_ticks\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"energy\"' /tmp/synveil-observe.json && "
                "grep -Fq 'total_threads' /tmp/synveil-observe.json && "
                "grep -Fq 'average_1m' /tmp/synveil-observe.json && "
                "grep -Fq 'sectors_read_512b' /tmp/synveil-observe.json && "
                "grep -Fq 'rx_bytes' /tmp/synveil-observe.json && "
                "grep -Fq '\"pressure\"' /tmp/synveil-observe.json && "
                "grep -Fq '\"io\"' /tmp/synveil-observe.json; then "
                "printf '\\n%s%s\\n' SYNVEIL_ OBSERVE_OK; fi\n"),
    "protocol": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                 b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_PROTOCOL_OK",
                 "if timeout -s KILL 10 /usr/libexec/synveil-test/protocol-probe && "
                 "timeout -s KILL 2 /usr/bin/synctl --json status; then "
                 "printf '\\n%s%s\\n' SYNVEIL_ PROTOCOL_OK; fi\n"),
}


def smoke(timeout_seconds, case="crash"):
    required, forbidden, marker, command = SCENARIOS[case]
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    log = out / "logs" / ("qemu-recovery-smoke.log" if case == "crash"
                          else f"qemu-{case}-smoke.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    # These cases use only developer QEMU state devices, never host disks.
    guest_env = dict(os.environ)
    # Both scenarios authenticate and activate tty1 through a real fbdev
    # seat, even though the controlling smoke script runs over serial.
    # Do not rely on the workflow author to remember this QEMU option.
    if case in ("enroll", "seat-crash"):
        guest_env["SYNVEIL_QEMU_VIDEO"] = "1"
    corrupt_disk = None
    audit_disk = None
    qmp_directory = None
    if case == "desktop-terminal":
        qmp_directory = tempfile.TemporaryDirectory(prefix="synveil-qmp-")
        qmp_socket = Path(qmp_directory.name) / "monitor.sock"
        guest_env["SYNVEIL_QEMU_QMP_SOCKET"] = str(qmp_socket)
        guest_env["SYNVEIL_QEMU_VIDEO"] = "1"
    if case == "state-missing":
        guest_env.pop("SYNVEIL_QEMU_STATE_DISK", None)
    elif case == "state-corrupt":
        corrupt_disk = tempfile.TemporaryDirectory(prefix="synveil-invalid-ext4-")
        path = Path(corrupt_disk.name) / "invalid.ext4"
        with path.open("wb") as state:
            state.truncate(16 * 1024 * 1024)
        guest_env["SYNVEIL_QEMU_STATE_DISK"] = str(path)
    if case == "audit-ext4":
        # Never fill a caller-supplied image. Always create a disposable disk.
        audit_disk = tempfile.TemporaryDirectory(prefix="synveil-audit-ext4-")
        path = Path(audit_disk.name) / "state.ext4"
        try:
            create_state_disk(path, 64)
        except BaseException:
            audit_disk.cleanup()
            raise
        guest_env["SYNVEIL_QEMU_STATE_DISK"] = str(path)
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        env=guest_env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_seconds
    seen = set()
    pending = b""
    sent = False
    try:
        with selectors.DefaultSelector() as selector, log.open("wb") as evidence:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(min(0.2, max(0, deadline - time.monotonic())))
                if not events:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                evidence.write(chunk)
                evidence.flush()
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    seen.add(line.rstrip(b"\r"))
                    if len(seen) > 4096:
                        seen = {value for value in seen if value.startswith(b"SYNVEIL_")}
                if forbidden in seen:
                    return False
                if not sent and required <= seen:
                    seen.discard(marker)
                    if case == "desktop-terminal":
                        seen.discard(b"SYNVEIL_DESKTOP_RESTARTING")
                        try:
                            open_demo_terminal(qmp_socket, timeout=6)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil-smoke] demo QMP failed: " +
                                            str(error) + "\n").encode())
                            return False
                    # BusyBox's line editor caps each input line. Keep compound
                    # test commands intact, but place shell statements on lines.
                    script = command.replace("; ", ";\n") + "# SYNVEIL_TEST_COMMAND_END\n"
                    process.stdin.write(script.encode())
                    process.stdin.flush()
                    sent = True
                if sent and marker in seen and (
                    (case not in ("desktop", "desktop-terminal") or
                     b"SYNVEIL_DESKTOP_RESTARTING" in seen) and
                    (case != "seat-crash" or
                     b"SYNVEIL_SEAT_RESTARTING" in seen)
                ):
                    return True
                if len(pending) > 65536:
                    return False
        return False
    finally:
        # Terminate QEMU and its shell wrapper together, including on timeout.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        if audit_disk is not None:
            audit_disk.cleanup()
        if corrupt_disk is not None:
            corrupt_disk.cleanup()
        if qmp_directory is not None:
            qmp_directory.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=SCENARIOS, default="crash")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("SYNVEIL_SMOKE_TIMEOUT", "60")))
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    passed = smoke(args.timeout, args.case)
    print(f"[synveil] QEMU {args.case} smoke " + ("passed" if passed else "failed"))
    if not passed:
        out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
        name = "recovery" if args.case == "crash" else args.case
        path = out / f"logs/qemu-{name}-smoke.log"
        if path.exists():
            print(path.read_text(errors="replace")[-16000:])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
