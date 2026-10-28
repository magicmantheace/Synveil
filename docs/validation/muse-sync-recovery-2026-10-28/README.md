# Bounded Muse recovery synchronization and complete host review

Reviewed fixed Muse tip `ef72f3fbcf925ad420c93e4b195b28f708cbb73d`
from `magicmantheace/shiny-dollop`, `projects/synveil/`, branch
`synveil/first-boot-desktop-muse`. There are 28 commits after the
previously transferred runtime snapshot d3a7d1d, including the previously
reviewed record reconciliation at 68df81f. No independent feature work.

## Accepted

29 completed upstream files are transferred without behavior changes (one
unused-import cleanup uses the import list already present in Muse ef72f3f):
tier-2 authenticated text maintenance; its init and kernel entry; the
existing Argon2id credential-state probe; native build/packaging and
real-libgcc_eh archive selection; the maintenance UKI/ESP wiring; corrected
guest drivers; attributed gates A-C receipts and raw logs. Implementation
is selected at `915751db24383ce9355cb2964949c0aa99332304`, before tier-4
reset changes. Later source has the reset implementation mixed into these
files, so copying latest versions wholesale would import the deferred path.

The reconciled original test-media record is now retained as HISTORICAL
upstream evidence (source 9dbeda0, disk 4be22ada...), superseded by Muse's
new recovery-inclusive package. It is not a current media recommendation.
Its JSON/prose contradiction was fixed at 68df81f, but raw destroyed-state
boot logs for that package remain absent from Git. No independent boot or
artifact-byte hash proof is asserted by retaining those receipts.

## Deferred

The latest tier-4 recovery-factor reset/enrollment/rotation implementation,
related credential replacement APIs and reset UKI/init wiring are NOT
transferred. Review found password replacement precedes factor revocation;
if revocation fails the actual reset function emits
`SYNVEIL_RECOVERY_RESET_OK_REVOKE_FAILED` and returns success while the
factor record stays active. A crash between the two writes has the same
ordering concern. This contradicts the approved single-use behavior.
See MUSE_HANDOFF.md for the unchanged-function fault test and exact scope.

The latest recovery-inclusive image (83fc83a5...) and its readiness/install
claim updates are deferred with the reset code. Muse's documentation
explicitly says authenticated maintenance/reset were not exercised on the
hardware-profile package, and no physical TG01 run exists. Current package
receipts and screenshots remain upstream; they are not relabeled as original
repository validation. FIRST_INSTALL.md, PHYSICAL_BOOT.md and ROADMAP remain
unchanged in this integration. NetSurf remains untransferred/unverified.

## Independent tests actually run locally

A new integration-only runner enumerates every existing Python unittest
entry point. It does not invoke standalone guest smoke drivers without
artifacts, and records every failure/timeout in its JSON summary.
57 suites ran, reporting 509 tests: 51 suites passed and six failed locally.
The failing suites are:

- ab-release-crosscheck: sgdisk missing (nine other cases pass).
- core-health, native-protocol-probe, network-diag, qmp-input: socket
  creation prohibited here with EPERM.
- desktop-lifetime: process-lifetime observation assertion fails in this
  restricted execution environment. No pass is inferred; ordinary CI reruns it.

The independent large-media test skips locally without GPT tools. Rust,
Wayland development headers, QEMU, native SDK, generation disk and Chromium
payload are not installed here. No Rust, host compositor, native guest or
physical boot is claimed from local execution.

Separately, the exact latest Muse snapshot's recovery-factor host contracts
15/15, maintenance contracts 5/5, proof gates 30/30 and guest-driver Python
syntax checks pass locally. These are contracts, not runtime reset validation.

## Exact-revision CI

The integration job runs pinned Rust formatting/Clippy/workspace tests under
umasks 0007, 0022 and 0077; real GPT/ext4 and large-media readback; all Python
unittest entry points; source-pinned update/files GUI compilation and bootstrap
lint. A separate read-only candidate job checks the fixed latest Muse
snapshot's full Rust workspace, Clippy, formatting and all Python unittests,
then runs the reset revocation fault test. No failure is suppressed.
Candidate failure is kept separate from checks of accepted integrated code.
Initial revision 654e739: all 57 integrated Python suites pass in CI,
including all six locally failing suites, real large-media construction and
both GUI compilations. Rust tests pass under all three umasks. The only
accepted-code failure was Clippy rejecting an unused Read import in the
older maintenance snapshot. The import is removed using Muse's latest import
list, with no runtime change; verification of that cleanup is pending.
Run 40707255322, integrated job 122882595459 records the initial failure.

The fault test extracts the actual upstream run_reset function unchanged,
compiles it against deterministic storage/input doubles, and injects a
revocation write failure after a successful password replacement. Its safety
assertion fails if reset returns success while the simulated factor stays
active. This is a control-flow fault test, not a real guest, on-disk crash or
physical power-cut reproduction. Local execution is blocked by missing rustc;
CI executed it against exact ef72f3f and reproduced the defect:
password_changed=true, revoke_attempted=true, factor_active=true, success=true,
followed by the failing safety assertion. Read-only candidate job 122882595994
in run 40707255322 passed normal formatting, Clippy, all 175 Rust tests and
56 Python suites (514 reported tests), then failed this additional fault test.
See reset-revocation-fault.log for the relevant verbatim CI lines. No normal
candidate test failure is being invented from the added assertion.

Prior browser integration b648a25 completed successfully in run 40537124946,
job 122305276528. That older pass is historical, not this transfer's result.

## Scope and stop

Dedicated integration branch `development/muse-verified-sync-2026-10-24`,
PR #40 only. No merge, force-push, release, physical write, firmware/Windows
change or shiny-dollop mutation. Development remains paused; Muse is primary.
A/B/browser/authentication improvements are retained. Owner installation and
production signing remain unproven; no installer-ready claim is made.

## Exact transfer provenance

- `build/rootfs/init` from `915751d`
- `build/scripts/kernel.sh` from `915751d`
- `src/veil-core/src/bin/veil-recovery.rs` from `915751d`
- `src/veil-core/src/credential.rs` from `915751d`
- `tests/native-build.py` from `915751d`
- `tests/native-rootfs.py` from `915751d`
- `tests/recovery-maint.py` from `915751d`
- `tests/smoke-recovery-maint-ab.py` from `915751d`
- `tests/smoke-recovery-maint.py` from `915751d`
- `tools/native_ab.py` from `915751d`
- `tools/native_build.py` from `915751d`
- `tools/package_native.py` from `915751d`
- `docs/validation/recovery-gates-2026-10-27/README.md` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-a-fixture.json` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-a-serial.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b1-attempt2.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b1-verdict.json` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt1-verdict.json` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt1.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt2.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt3-audit.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt3-maint-restore.log` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt3-verdict.json` from `ef72f3f`
- `docs/validation/recovery-gates-2026-10-27/gate-b2-attempt3.log` from `ef72f3f`
- `docs/validation/test-media-2026-10-27/README.md` from `68df81f`
- `docs/validation/test-media-2026-10-27/build-receipt.json` from `68df81f`
- `docs/validation/test-media-2026-10-27/guest-validation-usb.json` from `68df81f`
- `docs/validation/test-media-2026-10-27/guest-validation-vga.json` from `68df81f`
- `tests/smoke-native-joined-hardware.py` from `68df81f`
