# Corrected Muse A/B and graphical-update synchronization

Source: `magicmantheace/shiny-dollop`, `projects/synveil/`, fixed tip
`dcaf954c63c2004fbd6bebf3b0ce8c6c0ff3fe31`. Nine new commits after the last
reviewed `b49ed0dd3a5be70854a63e546cd35d6a579b0340`:
`1c492f1`, `6866c35`, `d46a8c5`, `ff17803`, `05c12d6`, `ae4403a`,
`8167ced`, `55d9db8`, `dcaf954`.

This pass also reconsiders the previously deferred guest/GUI work from the
30 commits between `66c50b6` and `b49ed0d`. All source is Muse's completed
implementation, not an independent competing implementation.

## Transferred implementation

- Guest `veil-ab` agent, boot-hook chain selection, persisted trial accounting,
  inactive-slot verification/staging, health plus explicit owner confirmation,
  owner rollback, and diagnostic fail-closed path. No firmware-variable writes.
- Required kernel configuration, native packaging/rootfs integration, separate
  A/B proof assembly, and authenticated Wayland graphical updater.
- Muse's in-tree SHA-256, SHA-512 and Ed25519 verification and existing tests.
  Test success is not a cryptographic security audit or production trust setup.
- Release packager with exclusive output/staging creation and refusal tests.
- Corrected agent/GUI proof gates and journal-replayed scratch extraction,
  synchronized known-file fixture and cumulative GUI log handling.
- Both regenerated proof records, build/acceptance receipts and twelve raw
  boot logs, copied verbatim and explicitly attributed to Muse.

Selectively applied project-path changes since 66c50b6, retaining existing
original improvements and integration evidence. Source/test files are copied
without behavior changes. Muse's ROADMAP completion changes are not adopted:
the original repository has not independently reproduced these guest boots.
FIRST_INSTALL.md and PHYSICAL_BOOT.md restrictions remain unchanged. The
separate obsolete pinned-b49 candidate CI job is replaced by checks of the
actual transferred source, with correct FreeType/pthread/math dependencies
for GUI compilation. No test is weakened or failure suppressed.

## Independent local verification

- Host A/B transaction suite: 18/18 PASS.
- Proof-gate negative/positive regression suite: 12/12 PASS.
- Release-format and output-refusal tests: 9/9 PASS locally. The fresh real
  release-image construction test requires absent host image tools and runs
  in CI; it is not claimed passed locally.
- Native build and rootfs fixtures: 16/16 and 10/10 PASS.
- Offline runner fixtures: 8/8 PASS.
- Agent and GUI regenerated receipt validators: zero violations.
- Agent/GUI build, source, disk and generation identities agree across their
  committed receipts (built/tested source 55d9db88aecc96fdc07d0d695e2855148a69ca4c).
- The exact defective old receipt from b49ed0d is now rejected with three
  semantic violations (wrong confirmed versions and missing baseline file).
- The earlier destructive-output reproduction now refuses the existing file
  and preserves `OWNER-DATA-KEEP` byte-for-byte before any disk command.
- Relevant shell/Python syntax and code whitespace checks PASS.

QMP input suite locally: 16 tests ran, with 12 error reports/subtests caused by
AF_UNIX creation returning EPERM. This environment blocks Unix sockets; these
are not asserted to be regressions. Unchanged tests run again on ordinary
Ubuntu CI as part of the complete host tier.

CI verifies the exact integration revision: pinned Rust formatting and Clippy,
workspace tests/permission fixtures under three umasks, all ten release tests
(including real GPT/ext4 image creation), proof-gate tests, source-pinned GUI
compilation and the complete existing host tier. Independent checks continue
following a failure, while the failure remains visible. Initial status:
PENDING. Consult PR #40 for exact-revision results.

The earlier pinned-b49 candidate job was cancelled before the remaining
checks completed; it is not a passing Clippy/test/GUI result. Its formatting
failure was reproduced in run 40321649081 and is superseded only when the
new exact-source checks finish. Previous original host checks at 35278f0
completed successfully, but do not validate the newly transferred guest code.

## Attributed guest evidence and limits

Muse's corrected agent record covers nine scenarios and its graphical record
covers four. The raw logs include the matching v2/B and v1/A confirmation
markers, three unhealthy-candidate failures followed by trial exhaustion,
and corrupt-slot fail-closed diagnostics. Its regenerated known-file baseline
contains the expected nonempty bytes. The validator checks pass locally;
these are reviews of upstream evidence, not new QEMU boots by this worker.

Source-built compiler/SDK and disk/generation image bytes remain unavailable
locally. Independent full native QEMU and GUI boot reproduction is BLOCKED.
Receipt hashes are checked for internal consistency, not against image bytes
that are not present. No native boot or physical hardware result is invented.

Publisher key remains development-only. Browser, HTTPS/downloads, user-changed
settings survival, installed-system recovery and physical hardware remain
unproven. Corrupt slots reaching diagnostics does not repair an installed OS.
No physical-media use is authorized by this synchronization.

## Scope and stop

Existing integration branch/PR #40; no merge, release, force-push, Windows or
firmware changes, physical writes, or shiny-dollop mutations. No independent
feature development occurred. Muse remains primary and the original roadmap
remains paused after this bounded transfer/test pass.
