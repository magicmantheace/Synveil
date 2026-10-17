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
from audit_fixture import AUDIT_SEED_AWK  # noqa: E402

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


def write_command(rollover=False, archive_only=False):
    if rollover and archive_only:
        raise ValueError("choose one archive setup")
    if not (rollover or archive_only):
        return WRITE
    # Construct the post-rename layout; this does not interrupt a live rename.
    seed_path = '$journal.1' if archive_only else '$journal'
    seed = (
        "if awk '" + AUDIT_SEED_AWK + f"' > \"{seed_path}\" &&\n"
        f'chmod 600 "{seed_path}" && [ "$(stat -c %s "{seed_path}")" = 4194304 ] &&\n'
        # Only fixture preparation is globally synchronized. Every native
        # recovery append/export still happens afterwards without global sync.
        + ('[ ! -e "$journal" ] && [ ! -L "$journal" ] && sync &&\n' if archive_only else '')
        + f'old=$(sha256sum "{seed_path}" | cut -d " " -f1); then\n'
    )
    archive_checks = (
        '[ "$(sha256sum "$journal.1" | cut -d " " -f1)" = "$old" ] &&\n'
        '[ "$(stat -c "%a:%u:%h:%s" "$journal.1")" = 600:0:1:4194304 ] &&\n'
        '/usr/sbin/veil-audit check --path "$journal.1" >"$d/archive-report.json" &&\n'
        'grep -Fq \'"records":512,\' "$d/archive-report.json" &&\n'
        '/usr/sbin/veil-audit export --path "$journal.1" --output "$store/copies/archive" >"$d/archive-export.json" &&\n'
        'archive_digest=$(sed -n \'s/.*"digest":"\\([0-9a-f]*\\)".*/\\1/p\' "$d/archive-export.json") &&\n'
        '[ "${#archive_digest}" = 128 ] &&\n'
        '/usr/sbin/veil-audit verify --path "$journal.1" --digest "$archive_digest" >/dev/null &&\n'
        '/usr/sbin/veil-audit verify --path "$store/copies/archive" --digest "$archive_digest" >/dev/null'
    )
    return WRITE.replace('SYNVEIL_AUDIT_PATH=', seed + 'SYNVEIL_AUDIT_PATH=', 1).replace(
        '--digest "$digest" >/dev/null; then',
        '--digest "$digest" >/dev/null &&\n' + archive_checks + '; then'
    ).replace(
        'printf "\\n%s%s%s\\n" SYNVEIL_ AUDIT_CRASH_DIGEST=',
        'printf "\\n%s%s%s\\n" SYNVEIL_ AUDIT_CRASH_ARCHIVE_DIGEST= "$archive_digest";\n'
        'printf "\\n%s%s%s\\n" SYNVEIL_ AUDIT_CRASH_DIGEST='
    ) + 'fi\n'


def read_command(digest, archive_digest=None, release_archive=False):
    if release_archive and archive_digest is None:
        raise ValueError("archive release needs a retained archive digest")
    for expected in (digest,) if archive_digest is None else (digest, archive_digest):
        if re.fullmatch(r"[0-9a-f]{128}", expected) is None:
            raise ValueError("invalid retained digest")
    paths = [("journal", digest), ("copies/snapshot", digest)]
    if archive_digest is not None:
        paths += [("journal.1", archive_digest), ("copies/archive", archive_digest)]
    verification = ' &&\n'.join(
        f'/usr/sbin/veil-audit verify --path /var/lib/synveil/audit-crash-test/{path} '
        f'--digest {expected} >/dev/null' for path, expected in paths
    )
    if release_archive:
        store = '/var/lib/synveil/audit-crash-test'
        verification += (
            f' &&\n/usr/sbin/veil-audit release-archive --path {store}/journal '
            f'--snapshot {store}/copies/archive --authorization {store}/copies/release.json '
            f'--digest {archive_digest} >/dev/null &&\n'
            f'[ ! -e {store}/journal.1 ] && [ ! -L {store}/journal.1 ] &&\n'
            f'grep -Fq \'"phase":"authorized-before-unlink"\' {store}/copies/release.json &&\n'
            f'grep -Fq \'"schema":"synveil.audit-retention-release/v1"\' {store}/copies/release.json &&\n'
            f'/usr/sbin/veil-audit inspect-release --path {store}/journal '
            f'--snapshot {store}/copies/archive --authorization {store}/copies/release.json '
            f'--digest {archive_digest} >{store}/copies/inspection.json &&\n'
            f'grep -Fq \'"state":"completion-recorded"\' {store}/copies/inspection.json &&\n'
            f'grep -Fq \'"archive_state":"not-inspected"\' {store}/copies/inspection.json &&\n'
            f'/usr/sbin/veil-audit verify --path {store}/journal --digest {digest} >/dev/null &&\n'
            f'/usr/sbin/veil-audit verify --path {store}/copies/archive --digest {archive_digest} >/dev/null'
        )
    return (
        'if ' + verification + ' &&\n'
        'timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then\n'
        'printf "\\n%s%s\\n" SYNVEIL_ AUDIT_CRASH_READ_OK; fi\n'
    )


def retained_digest(log, label):
    prefix = b"SYNVEIL_AUDIT_CRASH_" + label + b"="
    # Count every candidate, including malformed ones. Filtering valid values
    # first would silently discard a conflicting or truncated second receipt.
    candidates = [line.removesuffix(b"\r")[len(prefix):]
                  for line in log.split(b"\n") if line.startswith(prefix)]
    if len(candidates) != 1 or re.fullmatch(rb"[0-9a-f]{128}", candidates[0]) is None:
        raise ValueError("missing or ambiguous retained digest")
    return candidates[0].decode("ascii")



def smoke(timeout, rollover=False, archive_only=False, release_archive=False):
    if release_archive and not (rollover or archive_only):
        raise ValueError("archive release needs an archive setup")
    command = write_command(rollover, archive_only)
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    prefix = ("qemu-audit-archive-only-crash" if archive_only else
              "qemu-audit-rollover-crash" if rollover else "qemu-audit-crash")
    if release_archive:
        prefix += "-release"
    write_log = logs / f"{prefix}-write.log"
    read_log = logs / f"{prefix}-read.log"
    # Never accept a supplied disk. Keep it alive through both boots and waits.
    with tempfile.TemporaryDirectory(prefix="synveil-audit-crash-") as directory:
        disk = Path(directory) / "state.ext4"
        create_state_disk(disk, 64)
        if not persistence.boot_round(disk, WRITE_MARKER, command.replace("; ", ";\n"),
                                      timeout, write_log, abrupt=True, required=READY):
            return False
        evidence = write_log.read_bytes()
        try:
            digest = retained_digest(evidence, b"DIGEST")
            archive_digest = retained_digest(evidence, b"ARCHIVE_DIGEST") if rollover or archive_only else None
        except ValueError:
            return False
        # The expected digest comes from the first boot's host-retained log,
        # never from a digest file potentially changed with the guest disk.
        return persistence.boot_round(disk, READ_MARKER, read_command(digest, archive_digest, release_archive),
                                      timeout, read_log, required=READY)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60)
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--rollover", action="store_true",
                       help="also retain and verify a completed rollover archive")
    setup.add_argument("--archive-only", action="store_true",
                       help="construct an archive with no active journal before native recovery")
    parser.add_argument("--release-archive", action="store_true",
                        help="after reboot verification, explicitly release only the copied archive")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if args.release_archive and not (args.rollover or args.archive_only):
        parser.error("--release-archive requires --rollover or --archive-only")
    passed = smoke(args.timeout, args.rollover, args.archive_only, args.release_archive)
    print("[synveil] acknowledged audit after abrupt QEMU stop " + ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
