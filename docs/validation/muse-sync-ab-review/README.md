# Muse A/B candidate test and selective transfer

Source snapshot: `b49ed0dd3a5be70854a63e546cd35d6a579b0340` from
`magicmantheace/shiny-dollop`, `projects/synveil/`. Reviewed 30 commits after
`66c50b649c4fe580de81dd9d24825f26391a3936`, including the earlier deferred
host v2 core. No independent feature fixes were implemented.

## Transferred

`tools/ab_update.py` and `tests/ab-update.py`, byte-identical to Muse, plus
host-tier registration. This is explicitly the regular-file host transaction
model: it cannot establish guest boot, EFI slot selection, guest signature
verification or physical installation. The independent publisher trust and
bidirectional migration/retained rollback guards supersede the previously
reproduced Pass A migration defect. The host suite passes 18/18 locally.

The existing integration remains `development/muse-verified-sync-2026-10-24`,
PR #40 targeting `development/first-boot-desktop`. No merge or history rewrite.

## Isolated candidate tests

Copied the relevant Muse source into an isolated temporary checkout and ran:

- `tests/ab-update.py`: 18/18 PASS.
- `tests/ab-release-crosscheck.py`: 3/3 PASS (metadata/signature format
  interoperability only, not actual media creation).
- Existing integration `tests/offline-checks.py`: 8/8 PASS.

Added a separate CI job that checks out exactly the Muse SHA read-only and
runs pinned Rust formatting/Clippy/workspace tests, both host A/B suites,
source-pinned protocol GUI compilation and the complete host tier. It does
not import unverified guest code into the original source tree. Exact-revision
CI results were pending at initial commit. The first candidate job failed
`cargo fmt --all --check` at b49ed0d in `src/veil-core/src/bin/veil-ab.rs`
and `src/veil-core/src/ab.rs`:
https://github.com/magicmantheace/Synveil/actions/runs/40321649081/job/121567802539
Clippy, tests and GUI compile did not run in that first job because formatting
failed first. Follow-up CI separates checks and runs independent checks even
after failures without suppressing their failure status; see PR #40 checks.

Local cargo/rustc, Weston, QEMU, source-built SDK and proof disk/generation
images are absent. Actual candidate QEMU/GUI boots are BLOCKED here, not
failed boots. The candidate build receipt in Git does not supply its referenced
image bytes. No expensive unrelated full source rebuild was launched.

## Independent acceptance failures

The upstream committed receipt
`docs/validation/ab-qemu-proof-2026-10-26/smoke-receipt.json` reports all
scenario pass flags and `bootable_update_rollback_demonstrated: true`, yet:

1. S2 `confirmed.version` is 1 / generation v1, not the required v2.
2. S3 `confirmed.version` is 2 / generation v2, not the required v1.
3. S0 `known_file` is null. Later equality booleans cannot prove preservation
   of a real known file; missing-on-both-sides compares equal.

The current `tests/smoke-native-ab.py` passes `ok` to `record()` without gating
it on the extracted confirmation identities or survival booleans. The final
flag uses scenario pass flags, so it can remain true despite those failures.
A source inspection also finds `terminal_command()` returns true for commands
with no expected marker after a delay; a successful keystroke injection is not
proof that the shell executed the known-file creation.

These are failures of the acceptance evidence. They do not independently prove
that the guest updater itself failed to boot or preserve data. The record notes
journal-dirty offline extraction, which must be resolved with reliable reads
and hard assertions before the result is accepted.

The saved proof predates the owner-confirmation/GUI changes; no committed
passing GUI proof receipt was found in this fixed snapshot. It cannot validate
the latest guest/GUI behavior. No production key or physical boot proof exists.

## Additional reproduced builder safety failure

`tools/ab_release.py::build_media()` opens the supplied output with `wb` and
truncates it before running sgdisk. It does not require a newly created,
non-symlink regular file. In an isolated temporary directory, create an
existing file containing `OWNER-DATA-KEEP`, call `build_media()` with disk-tool
execution mocked to stop immediately, and inspect it: it has already become
167772160 bytes and the original contents are gone. No physical device was
used in this reproduction. The same unrestricted output path is exposed by
`--out`. The builder also removes an existing derived staging directory.

This violates the file-only/non-destructive output safety expected of these
prototypes. The release builder, its cross-check registration and guest/GUI
integration remain deferred; do not fix independently or imply that its three
passing interoperability tests cover output safety.

## Handoff and scope

Muse must harden receipt assertions and artifact extraction, prove nonempty
known-file persistence, regenerate current-source guest and GUI evidence, and
make image/staging outputs exclusive and safe before transfer. Guard all
related temporary paths, key paths and output parents against symlink and
existing-data replacement, and add refusal tests.

The corrected no-firmware-write chain-load design is useful, but its guest
implementation, cryptographic implementation, kernel changes, GUI, packaging
and boot proof remain candidate-only until their required validation is
satisfied. The current original guest architecture and hardware restrictions
are unchanged. The original roadmap remains paused. No physical writes,
firmware changes, release, merge or shiny-dollop modifications occurred.
