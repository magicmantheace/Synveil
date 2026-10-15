#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verify acknowledged audit bytes after abruptly killing a disposable QEMU VM."""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from state_disk import create_state_disk  # noqa: E402

spec = importlib.util.spec_from_file_location("persistence_smoke", ROOT / "tests/smoke-persistence.py")
persistence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persistence)
READY = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_STATE_READY", b"SYNVEIL_CORE_READY"}
WRITE_MARKER = b"SYNVEIL_AUDIT_CRASH_WRITE_OK"
READ_MARKER = b"SYNVEIL_AUDIT_CRASH_READ_OK"
WRITE = (
    'd=/run/synveil-audit-crash-test; store=/var/lib/synveil/audit-crash-test;\n'
    'if mkdir -m 700 "$d" && mkdir -m 700 "$store" && mkdir -m 700 "$store/copies"; then\n'
    'journal="$store/journal"; socket="$d/core.sock";\n'
    'SYNVEIL_AUDIT_PATH="$journal" timeout -s KILL 10 /usr/sbin/veil-core --once --socket "$socket" >"$d/core.log" 2>&1 & helper=$!; ok=;\n'
    'for retry in $(seq 1 40); do\n'
    'if timeout -s KILL 2 /usr/bin/synctl --socket "$socket" --json status >/dev/null 2>&1; then ok=1; break; fi; sleep 0.1; done;\n'
    'if [ "$ok" = 1 ] && wait "$helper" &&\n'
    '! grep -Fq "persistent write failed" "$d/core.log" &&\n'
    'grep -Fq \'"event":"request"\' "$d/core.log" &&\n'
    'grep -Fq \'"event":"shutdown"\' "$d/core.log" &&\n'
    '[ -s "$journal" ] &&\n'
    '/usr/sbin/veil-audit check --path "$journal" >/dev/null &&\n'
    '/usr/sbin/veil-audit export --path "$journal" --output "$store/copies/snapshot" >"$d/export.json" &&\n'
    'digest=$(sed -n \'s/.*"digest":"\\([0-9a-f]*\\)".*/\\1/p\' "$d/export.json") &&\n'
    '[ "${#digest}" = 128 ] &&\n'
    '/usr/sbin/veil-audit verify --path "$journal" --digest "$digest" >/dev/null &&\n'
    '/usr/sbin/veil-audit verify --path "$store/copies/snapshot" --digest "$digest" >/dev/null; then\n'
    'printf "\\n%s%s%s\\n" SYNVEIL_ AUDIT_CRASH_DIGEST= "$digest";\n'
    'printf "\\n%s%s\\n" SYNVEIL_ AUDIT_CRASH_WRITE_OK; fi; fi\n'
)


def read_command(digest):
    if re.fullmatch(r"[0-9a-f]{128}", digest) is None:
        raise ValueError("invalid retained digest")
    return (
        'if /usr/sbin/veil-audit verify --path /var/lib/synveil/audit-crash-test/journal '
        f'--digest {digest} >/dev/null &&\n'
        '/usr/sbin/veil-audit verify --path /var/lib/synveil/audit-crash-test/copies/snapshot '
        f'--digest {digest} >/dev/null &&\n'
        'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then\n'
        'printf "\\n%s%s\\n" SYNVEIL_ AUDIT_CRASH_READ_OK; fi\n'
    )


def smoke(timeout):
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    write_log = logs / "qemu-audit-crash-write.log"
    read_log = logs / "qemu-audit-crash-read.log"
    # Never accept a supplied disk. Keep it alive through both boots and waits.
    with tempfile.TemporaryDirectory(prefix="synveil-audit-crash-") as directory:
        disk = Path(directory) / "state.ext4"
        create_state_disk(disk, 64)
        if not persistence.boot_round(disk, WRITE_MARKER, WRITE.replace("; ", ";\n"),
                                      timeout, write_log, abrupt=True, required=READY):
            return False
        matches = re.findall(rb"^SYNVEIL_AUDIT_CRASH_DIGEST=([0-9a-f]{128})\r?$",
                             write_log.read_bytes(), re.MULTILINE)
        if len(matches) != 1:
            return False
        digest = matches[0].decode("ascii")
        # The expected digest comes from the first boot's host-retained log,
        # never from a digest file potentially changed with the guest disk.
        return persistence.boot_round(disk, READ_MARKER, read_command(digest),
                                      timeout, read_log, required=READY)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    passed = smoke(args.timeout)
    print("[synveil] acknowledged audit after abrupt QEMU stop " + ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
