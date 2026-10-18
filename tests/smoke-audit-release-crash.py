#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Interrupt a traced native release, kill QEMU, then verify recovery on reboot."""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_crash", ROOT / "tests/smoke-audit-crash.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
PHASES = ("before-unlink", "after-unlink", "unlink-synced", "partial-completion",
          "completion-written", "completion-synced")
READ_MARKER = b"SYNVEIL_RELEASE_GUEST_RECOVERY_OK"
STORE = "/var/lib/synveil/audit-crash-test"


def read_command(phase, digest, archive_digest):
    if phase not in PHASES or any(re.fullmatch(r"[0-9a-f]{128}", d) is None
                                 for d in (digest, archive_digest)):
        raise ValueError("invalid release recovery inputs")
    checks = [f'/usr/sbin/veil-audit verify --path {STORE}/{path} --digest {expected} >/dev/null'
              for path, expected in (("journal", digest), ("copies/snapshot", digest),
                                     ("copies/archive", archive_digest))]
    inspect = (f'/usr/sbin/veil-audit inspect-release --path {STORE}/journal '
               f'--snapshot {STORE}/copies/archive --authorization {STORE}/copies/release.json '
               f'--digest {archive_digest} >/run/release-inspection.json')
    checks += [inspect, 'grep -Fq \'"archive_state":"not-inspected"\' /run/release-inspection.json']
    if phase == "completion-written":
        checks.append('grep -Eq \'"state":"(authorization-only|incomplete-completion|completion-recorded)"\' /run/release-inspection.json')
    elif phase == "completion-synced":
        checks.append('grep -Fq \'"state":"completion-recorded"\' /run/release-inspection.json')
    elif phase == "partial-completion":
        checks.append('grep -Eq \'"state":"(authorization-only|incomplete-completion)"\' /run/release-inspection.json')
    else:
        checks.append('grep -Fq \'"state":"authorization-only"\' /run/release-inspection.json')
    if phase == "before-unlink":
        checks.append(f'[ -f {STORE}/journal.1 ] && [ ! -L {STORE}/journal.1 ]')
    elif phase in ("unlink-synced", "partial-completion", "completion-written", "completion-synced"):
        checks.append(f'[ ! -e {STORE}/journal.1 ] && [ ! -L {STORE}/journal.1 ]')
    # Before journal-directory sync, either replayed namespace is possible.
    # Observe it first, then use a fresh receipt only if an archive survived.
    release = (f'/usr/sbin/veil-audit release-archive --path {STORE}/journal '
               f'--snapshot {STORE}/copies/archive --authorization {STORE}/copies/recovered.json '
               f'--digest {archive_digest}')
    checks.append(f'old=$(sha256sum {STORE}/copies/release.json | cut -d " " -f1)')
    observed = ('if [ -e ' + STORE + '/journal.1 ] || [ -L ' + STORE + '/journal.1 ]; then\n'
                f'/usr/sbin/veil-audit verify --path {STORE}/journal.1 --digest {archive_digest} >/dev/null &&\n'
                'printf "\\n%s%s\\n" SYNVEIL_ RELEASE_ARCHIVE=present &&\n'
                f'{release} >/dev/null &&\n'
                f'[ ! -e {STORE}/journal.1 ] && [ ! -L {STORE}/journal.1 ];\n'
                'else\n'
                'printf "\\n%s%s\\n" SYNVEIL_ RELEASE_ARCHIVE=absent &&\n'
                f'! {release} >/dev/null 2>&1 &&\n'
                f'[ ! -e {STORE}/copies/recovered.json ] && [ ! -L {STORE}/copies/recovered.json ]; fi')
    checks += [observed, f'[ "$(sha256sum {STORE}/copies/release.json | cut -d " " -f1)" = "$old" ]',
               inspect]
    checks += [f'/usr/sbin/veil-audit verify --path {STORE}/{path} --digest {expected} >/dev/null'
               for path, expected in (("journal", digest), ("copies/snapshot", digest),
                                      ("copies/archive", archive_digest))]
    script = ('if ' + ' &&\n'.join(checks) + '; then\n'
              'printf "\\n%s%s\\n" SYNVEIL_ RELEASE_GUEST_RECOVERY_OK; fi\n')
    return f's={STORE};\n' + script.replace(STORE, '$s')


def smoke(timeout):
    logs = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out")) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for phase in PHASES:
        with tempfile.TemporaryDirectory(prefix="synveil-release-guest-") as directory:
            disk = Path(directory) / "state.ext4"
            audit.create_state_disk(disk, 64)
            first = logs / f"qemu-release-cut-{phase}-write.log"
            second = logs / f"qemu-release-cut-{phase}-read.log"
            command = audit.write_command(rollover=True) + (
                'if [ "${#archive_digest}" = 128 ]; then\n'
                f'/usr/libexec/synveil-test/audit-release-cut {phase} /usr/sbin/veil-audit '
                f'{STORE} "$archive_digest"; fi\n')
            marker = b"SYNVEIL_RELEASE_GUEST_CUT=" + phase.encode()
            if not audit.persistence.boot_round(disk, marker, command.replace("; ", ";\n"), timeout,
                                                first, abrupt=True, required=audit.READY):
                return False
            try:
                evidence = first.read_bytes()
                digest = audit.retained_digest(evidence, b"DIGEST")
                archive_digest = audit.retained_digest(evidence, b"ARCHIVE_DIGEST")
            except (ValueError, OSError):
                return False
            if not audit.persistence.boot_round(disk, READ_MARKER,
                                                read_command(phase, digest, archive_digest), timeout,
                                                second, required=audit.READY):
                return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=75)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    passed = smoke(args.timeout)
    print("[synveil] live release across guest interruption " + ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
