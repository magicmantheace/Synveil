# Validation Policy

Synveil distinguishes between **implemented**, **validated**, and **complete**.

A roadmap item may have code in the repository without being considered complete when its defining behavior has not yet been exercised.

## Evidence classes

### Static validation

Examples:

- shell syntax checks,
- Python compilation,
- JSON/schema validation,
- formatting,
- source-lock structural checks.

Static validation proves that source is structurally usable. It does not prove runtime behavior.

### Build validation

The component is successfully built from the committed inputs in a clean or adequately isolated environment.

Build evidence should record:

- Synveil Git commit,
- source versions/pins,
- target,
- toolchain identity,
- artifact hashes,
- relevant build logs.

### Runtime validation

The built artifact actually performs its defining behavior.

Examples:

- an image reaches Synveil `/init`,
- `veil-core` accepts a status request,
- policy denies a forbidden action,
- rollback restores a baseline state.

### System validation

Multiple components operate together through the intended architecture.

This is the strongest form of evidence for roadmap phase exit criteria.

## Phase 1 validation contract

Phase 1 is complete only when one committed Synveil revision demonstrates the following in one reproducible pipeline:

1. bootstrap scripts pass static validation;
2. all source inputs are materialized from the committed source lock;
3. the target cross/bootstrap toolchain builds;
4. glibc is installed into the isolated target sysroot;
5. the target compiler successfully links a target program against that sysroot;
6. BusyBox builds for the target;
7. Synveil's root filesystem and `/init` are generated;
8. the pinned Linux kernel builds with the committed Synveil configuration;
9. the EFI-stub kernel is placed in a GPT disk's EFI System Partition;
10. image inspection finds `EFI/BOOT/BOOTX64.EFI`;
11. OVMF boots the image in QEMU;
12. Synveil `/init` emits `SYNVEIL_BOOT_OK` on the serial console;
13. `out/manifests/build.json` records the build and artifact identity;
14. validation logs/manifests are retained as CI evidence.

Only after this evidence exists should the corresponding Phase 1 roadmap items be checked complete.

## AthenZ integration and evolution validation contract

[ADR 0012](adr/0012-athenz-resident-intelligence.md) selects AthenZ as the sole
resident intelligence. Integration evidence must identify exact Synveil,
AthenZ and developmental AI revisions, plus specialization/update provenance.
Before Phase 6 can close, tests must demonstrate version-compatible local
observations and typed plans, deterministic rejection of forbidden/malformed
requests, bounded resources, and boot/desktop/recovery with AthenZ unavailable.
Specialization changes and evolved candidates must not expand permissions.
Failed or incompatible activation must allow last-known-good intelligence
recovery while preserving human policy and system evidence. Memory/learning
data access, consent and deletion boundaries require explicit tests once the
cross-project contract is defined. Documentation of this direction is not
runtime integration evidence.

The complete native target QEMU run
[8c4290f](validation/native-8c4290f/README.md) passed, including
absent/non-ext4 state devices, graphical crash recovery, actual QMP
terminal launch and cleanup, and two independent boots for preferences
and home data. It remains developer-only VM evidence.

## Phase 2 validation direction

Phase 2 will require runtime evidence that:

- native Synveil code launches in the booted image;
- `veil-core` owns a local IPC endpoint;
- `synctl status` obtains a versioned structured response;
- malformed requests fail safely;
- the system remains recoverable when `veil-core` is absent or crashes.

Native boot/status at `79a41c2` is recorded in
[the native image evidence](validation/native-image-79a41c2/README.md).
`native-smoke` requires boot and successful core status in QEMU.
`recovery-smoke` additionally boots a guest, kills its core, requires status to
fail, and verifies shell file operations. Core-crash recovery passed at `248775a` in
[the recovery record](validation/core-crash-248775a/README.md).
The separate absent-core boot case remains required. `guest-fixture --case absent`
builds and boots a separate image with its core binary removed; it must prove
boot completion, core absence, and functioning shell file operations.
`guest-fixture --case protocol` builds a separate test image with a fixed probe
that requires malformed/schema/method errors and healthy status afterwards.
The missing-core and malformed-protocol guest cases, plus
`recovery-smoke --case supervision` (automatic restart, exhaustion and shell
recovery), passed in the [810f66d native run](validation/native-810f66d/README.md).
The same run passed read-only observation, DHCP, virtual framebuffer/input/VT
readiness, and two separate QEMU persistence boots. The **entire workflow
failed** on a separate graphical desktop marker formatting assertion.
Individual component checks are recorded as partial evidence, not a
successful end-to-end desktop image.

A new `recovery-smoke --case desktop` guest scenario kills only
`synveil-display`, requires the bounded UI supervisor to announce a
restart, checks that a different desktop process is running and that
`synctl status` still responds. The serial console remains available.
Host fixtures ensure an echoed command does not count as recovery evidence.

Two recovery-smoke guest cases now require the serial shell and healthy
`synctl status` when the explicitly opt-in `/dev/vdb` disk is absent
or a temporary attached block image contains no valid ext4 filesystem.
They never identify, open or format a physical disk. These cases require
their own native-image guest validation.

An additional QMP-driven `recovery-smoke --case desktop-terminal` exercise
navigates the real Terminal tile, confirms the UID 1000/no-new-privileges VT1
shell, kills the graphical launcher, and requires that shell to terminate while
`synctl status` and serial recovery remain available. Host QMP tests enforce
the two Enter actions and the allowlisted key sequence. Full native QEMU
evidence is still pending; a successful host process-tree fixture alone does
not validate guest VT cleanup.

A dedicated `ui-preferences-smoke` test now sends keyboard actions through
a private QEMU monitor into the actual Settings UI on the first boot,
requires the expected saved file, then boots a second independent QEMU
instance and requires `SYNVEIL_SETTINGS_LOADED` emitted by the menu.
It also verifies a UID-1000-owned demo-home file survives. The QMP input
helper is host-only, opt-in, key allowlisted, and its protocol is exercised
against a fake monitor. The host unit test separately checks the menu's
atomic file writer, corrupted records and symlink protections. The real
two-QEMU-process save/load and demo-home checks passed at
[f642d6f](validation/native-f642d6f/README.md), but that result predates
the latest state-directory hardening and graphical crash-restart guest gate.

The [d875da39 full native QEMU run](validation/native-d875da39/README.md)
passed its build pipeline and all guest gates: core recovery, observer,
framebuffer/input/desktop readiness, UID 1000 terminal, DHCP, and two
independent ext4 boots. This validates a *developer VM*, not a physical
installer, authenticated user desktop, or subsequent commits.

The [first user test-install contract](FIRST_INSTALL.md) adds desktop startup,
persistent storage across reboot, input/network usability, and console fallback
checks before a user-facing test image is offered.

The pinned-source Argon2id verifier and one-shot developer-VM `veil-enroll`
tool now have native packaging/link-map gates and host tests for valid/invalid
credentials, unsafe file modes/links, and refusal to replace a credential.
The QEMU workflow additionally checks enrollment using an ephemeral
guest-generated test secret, then starts a *different* QEMU process to
verify the private PHC record survived reboot. The new native guest evidence
is pending; this is not an interactive login or an approved owner install.

The isolated graphical-login guest fixture now stages an alternate VM boot
image with the *explicit* authenticated-desktop marker while leaving the
default developer menu image untouched. Its fresh, separately formatted
QEMU state disk receives a host-generated 128-bit test-only password only
after serial echo is disabled, and a private QMP monitor types that secret
into the actual framebuffer login screen. The guest must first show a real
UID/GID 1000, NoNewPrivs=1 VT1 **graphical session**, then demonstrate
that a fixed on-desktop Terminal tile starts a child UID1000 shell and
the shell's `exit` returns to the **same living dashboard**. Next, Escape
must log out and remove all living UID1000 session descendants. Changing
VTs must also revoke the lease, and killing only the root login GUI (not
the user session) must cause its separate UID1000 session to die while
serial `synctl status` continues. Test-only QMP commands are allowlisted
and never include an arbitrary privileged shell command. This stronger
end-to-end gate **awaits native QEMU validation**; host keyboard and
fixture tests cannot substitute for guest proof.

## AI-related validation

No AI planner result is accepted as validation of deterministic system behavior.

For example, a planner saying that an action "would be safe" does not validate policy enforcement. Tests must request the action and demonstrate the deterministic policy result.

Likewise, explanations are validated against transaction evidence rather than judged solely by natural-language plausibility.

## Roadmap updates

Roadmap checkboxes should normally be updated in the same commit as, or after, the evidence that justifies completion.

If an implementation exists but validation is blocked by infrastructure, leave the item unchecked and document the blocker instead of overstating project maturity.

## Audit format validation

Prototype persistent audit writes now enforce a per-journal 4 MiB bound:
cooperative writers acquire a nonblocking kernel file lock before the
capacity check and append. If the file is full or locked, the core emits
a console warning instead of silently declaring that the audit record was
persisted. This guards limited developer-VM state storage; audit rotation,
retention, loss accounting and tamper-evident chaining remain release gates.
It is not evidence of completed archival or fault-tolerant logging.

The [audit v1 contract](AUDIT.md) has unit coverage for schema/build/process
identity, ordering, unavailable time, and escaped field content. Its host
integration test requires the actual core to answer status and emit valid
startup, socket, request, and shutdown records. This validates the format and
emitter; it does not establish persistent storage or a durable journal.

The [fc78195 result](validation/supervision-fc78195/README.md) leaves supervision,
protocol, and absent-core guest cases pending. Independent guest cases must not
be skipped merely because a previous guest test failed.


## Offline validation during GitHub Actions unavailability

When CI minutes or GitHub Actions are unavailable, tests can run on a trusted
Linux build host **without invoking Actions or downloading dependencies**:

- `bash build.sh offline-check --level quick` — shell/source lint, host-side
  Python fixtures and C display build; no Rust toolchain or QEMU required.
- `bash build.sh offline-check --level host` — quick checks plus
  `cargo fmt`, `cargo test --offline`, and
  `cargo clippy --offline -- -D warnings`. Dependencies must already be
  cached. Missing Rust/caches are failures, not silently skipped.
- `bash build.sh offline-check --level guest` — host checks plus guest
  smoke and the isolated graphical-login QEMU fixture, using the **already
  built** native image and toolchain. It never formats a physical device.
  Guest tests are not attempted when the native boot image is missing.
- `bash build.sh offline-check --level quick --plan` lists the exact
  checks without running them.

Every invocation saves a per-command log and structured
`out/local-validation/<timestamp>-<commit>/results.json` receipt showing
passed, failed, timed-out and blocked tests. The receipt carries the Git
commit, tracked-worktree cleanliness and tracked-diff SHA-256.
**Host-only green cannot be described as a passing boot, login or install.**
Only guest-tier checks against a clean, manifest-matched image can support
end-to-end QEMU claims. Keep failed logs and re-run after fixes. Do not
turn off CI checks or fabricate their status while Actions is unavailable.

### Authenticated tty1 crash recovery gate (new code, runtime evidence pending)

A normal logout is insufficient evidence of terminal recovery. The root VT
launcher must normalize tty1 to canonical/echo/signal mode before each
new authenticated user session, even if the preceding dashboard was killed
while in raw mode. Compile/run the `synveil-vt --self-test` host fixture,
then force a dashboard crash and complete an independent second login and
fixed shell open/exit in native QEMU. Source presence alone is not a pass.

### Isolated host check of VT safety helpers

For the current pointer/tty hardening changes, a standalone C translation
unit reproducing the committed `safe_pointer_keys` and
`restore_session_tty_mode` helper bodies was compiled locally with
`gcc -O2 -std=c11 -Wall -Wextra -Werror`. It passed an exhaustive
bitmap check of all Linux key codes below `BTN_MISC` and tty mode
restoration assertions. **This is not compilation of Synveil's complete
`vt.c`, nor of a native target image.** The same exhaustive pointer
check is included in the committed `synveil-vt --self-test` source for
future clean host validation.

### Authenticated framebuffer process boundary

A CLOEXEC or closed framebuffer FD does not stop a `fork` child from
inheriting an already-mapped framebuffer. The UID1000 dashboard must
mark its framebuffer `MADV_DONTFORK` before it spawns an interactive
terminal, failing closed if this cannot be enforced. The host build suite
now includes `tests/display-mapping.c`, which forks a real child after
marking a mapping and observes `ENOMEM` from `mincore` in the child.
That is host helper validation only; the actual fbdev driver mapping still
needs exact-image guest validation.

### UID1000 GUI memory privacy

The host display suite now compiles and executes
`tests/display-seat-privacy.c`, which invokes the same
`PR_SET_DUMPABLE,0` helper used by the authenticated dashboard and
verifies the resulting kernel state in a child. The GUI fails closed if
this call cannot be honored. This does not prove framebuffer or UI security
under a full native guest with multiple UID1000 processes.

### October 12 GitHub Actions guest integration failures

The public repository's native target workflow successfully built the pinned
source/toolchain and ran most guest gates at commit `b8f637a`, but the
credential enrollment and seat-broker crash scenarios timed out without
`/dev/fb0`: their QEMU launch lacked the explicit video flag. Both smoke
cases now force `SYNVEIL_QEMU_VIDEO=1` in the guest harness, regardless of
the calling workflow's environment, and enrollment requires the actual
`SYNVEIL_VT_READY` marker. The host recovery fixtures assert the video
setting is present. This corrects the test setup, not the evidence status:
the revised guest scenarios require a new QEMU run.

The isolated graphical-login fixture at that revision enrolled a fresh
credential and rejected the wrong password, but then rejected the subsequent
correct-password attempt. The root graphical login previously emitted
`SYNVEIL_GRAPHICAL_AUTH_DENIED` **before** re-opening and grabbing the login
keyboard. Its QMP test uses that marker to send the next password; the new
code emits denial evidence only after the login VT is restored, the keyboard
is regrabbed and the screen is repainted. This removes one proven input
readiness race, but does not by itself establish that the entire QEMU guest
flow will now pass.

### Serial-lock fixture observation window

On the public-runner native job at commit `4df5fcd`, the otherwise successful
bootstrap lint stopped on the `echo-only` serial-lock fixture. That fixture
printed a deliberately harmless echoed probe and exited immediately, which
the real negative smoke test correctly treats as inconclusive: it must watch
the still-running guest for the full three-second security window. The fixture
now stays alive for that observation period, and a new `early-exit` case
explicitly verifies that premature EOF still fails. No root serial access is
weakened. This requires new host CI confirmation.

## Graphical first-user provisioning gate

At the exact image revision offered to an owner, require a QEMU run of the
isolated 'graphical-onboard' fixture that:

- boots an authenticated image with locked serial and a separate blank ext4
  state volume;
- enters a fresh synthetic password twice through the private QMP keyboard,
  proves successful one-shot enrollment, then authenticates to a non-root
  graphical session without exposing a root console;
- boots a second, different QEMU process using the same state and refuses to
  present first-user enrollment again while accepting the existing password;
- rejects failure and echo-only scenarios in the host fixture; and
- retains existing authenticated seat lease, crash and VT restoration checks.

At October 12, 2026 the new source, positive QMP scenario and negative host
fixtures are implemented; end-to-end new guest evidence is pending. None of
the first-install or full production security boxes is satisfied solely by
host validation.


## Authenticated VM package evidence gate

On completion of both independent QEMU boots, the graphical-onboard
fixture writes a result-bound SHA256 validation receipt. A separate
host packaging test checks that authenticated VM bundling rejects
wrong/missing receipts, stale commits, altered guest EFI/initramfs
artifacts, unsafe auth markers, root-serial override and replacement
of existing user state. A real native CI integration step runs the
packager only after the graphical-onboard guest step succeeds.
The packaged state volume must be freshly initialized rather than
copied from the synthetic test fixture. This test provides no
signature, hardware installer or owner recovery certification.

### Authenticated VM recovery and archive alias validation

Packaged authenticated QEMU previews must contain a root-owned, non-writable,
executable independent read-only recovery-status binary in their embedded
initramfs. Host checks reject missing, symlinked, nonexecutable and writable
copies. The archive scanner also rejects ambiguous path components that could
alias privileged files, while requiring safe metadata for the cpio root record.
This is a packaging gate, not a password reset or physical installer. Native
guest validation of each revision is still required.

## Isolated guest kernel build-object reuse

The native target pipeline builds its base pinned Linux kernel once, then
isolated guest fixtures seed a separate, **shared guest-only build directory**
with a copy of the baseline's compiled objects. A fingerprint of the baseline
kernel configuration and image prevents reuse when the baseline changes.
The guest build path stays stable across sequential fixtures, avoiding Kbuild
recompilation from changed output paths. Each fixture still stages its own
modified rootfs, assembles a
new initramfs, runs the kernel configuration/relink with that initramfs,
generates a separate GPT/UEFI boot image, and boots it in QEMU. The sole
build acceleration is avoiding recompilation of unrelated Linux source
objects for each test; it does not reuse a prior guest success or relax
authentication, recovery, or image hash gates.

Only the test fixture builder sets `SYNVEIL_KERNEL_SEED_WORK`, and it
must point to a complete baseline source and kernel build from the same
pipeline. The kernel script fails closed if the seed is missing or invalid,
rejects a symlinked guest build cache, and refuses to overwrite the base
output. Guest builds execute sequentially under the checkout build lock;
they must not be run concurrently against the shared cache. The real kernel
build without a seed remains a clean source build. Host fixture tests check isolation
and seed wiring; native CI must prove the optimized path works before
calling the full guest pipeline validated.

## Full authenticated prototype baseline: October 14, 2026

At [e8f371a](validation/native-e8f371a/README.md), every native build and
guest step in run 38556585136 passed, including independent two-boot testing
of the actual packaged authenticated VM. This supersedes the pending baseline
guest claims above for that revision only. Later audit inspection and archive
publication changes require their own exact-revision build/guest evidence.
Physical installation and production recovery/security remain open.

## Downloadable authenticated preview gate

Archive creation/upload is conditional on independent packaged-image guest
success. The archive tool reuses the same bundle validator as that smoke test,
requires the generated blank-state digest and exact file inventory, and refuses
modified launchers, symlinks, permissions/owner mismatches and output replacement.
Host tests inspect and extract the generated tar to verify private modes,
state identity, deterministic archive bytes and failure cleanup. Native runtime
validation of the audit inspector and archive/upload path passed at
[262d275](validation/native-262d275/README.md). The original bounded rotation
guest gate passed at [f973531](validation/native-f973531/README.md), alongside
all existing guest and downloadable-preview gates.

The later audit rollover gate also exercises exhausted archival capacity and
an incomplete synthetic active journal. Success requires healthy native IPC,
console request/shutdown events and the specific sink-failure notice, byte
preservation of active and archived evidence, and inspector refusal of the
incomplete file. Host shell fixtures cover pass/failure control flow, including
silent sink failures and automatic repair. These gates are pending their own
exact-revision QEMU run and do not establish power-loss durability.
The archive host suite also checks a state disk modified during copying and
restored before final validation, and refuses hardlinked source members. Each
copied member's streaming digest must match its expected digest; the state disk
uses the blank-state digest recorded at package creation. Open descriptors are
checked before and after copying for regular-file identity, size, owner, mode
and single-link count. These additional checks have host evidence only until
their own exact-revision native archive/upload run succeeds.
