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

## First multiwindow desktop runtime evidence (experimental)

The isolated Debian Trixie/XFCE live image passed an **actual UEFI/QEMU
graphical runtime** at commit `c0242259f69a6ba4888421e9ce671644c67d9a5d`,
in [Actions run 39541711454](https://github.com/magicmantheace/Synveil/actions/runs/39541711454).
A fixed host keypress advanced the recognized Debian live selection menu;
the guest loaded Linux and XFCE, the non-root session launched Thunar and
XFCE Terminal, Xfwm4 performed real move/resize/maximize/minimize/restore
operations, and QMP captured a nonblank 1280x800 framebuffer. The archived
guest serial log contains the successful interaction marker; see the
[exact-revision evidence record](validation/desktop-live-c024225/README.md).

This evidence **does not** apply to the distinct source-built Synveil
hardware-kernel profile. It does **not** validate first-user credential
enrollment, interactive authenticated login, persistent home, physical
NVIDIA graphics, removable-media installation, A/B update transactions or
rollback. The same unchanged ISO subsequently passed **two independent OVMF/QEMU
cold starts**, including the active XFCE panel and a third genuine Settings
Manager window, at source revision `437020d26070d0e2a231ad79087b39026fcfb47c`
([completed CI run 39593790664](https://github.com/magicmantheace/Synveil/actions/runs/39593790664),
[exact-revision evidence](validation/desktop-live-437020d/README.md)).
The later development revision `0579de0a237e823e60843e00e9e7521ff6d53dd4`
completed a **four-boot** offline OVMF/QEMU run with the same XFCE desktop
checks. Two boots had no writable disk; then a third wrote a fixed benign
non-root home file to the separate 256 MiB `synvmhome` ext4 image, and a
fourth independent QEMU process recovered the expected bytes and UID.
The host required separate `HOME_WRITTEN` and `HOME_RECOVERED` serial
markers plus the real graphical desktop and framebuffer proof in
[completed Actions run 39597968232](https://github.com/magicmantheace/Synveil/actions/runs/39597968232).
A separate non-guest [file-only ext4 build check](https://github.com/magicmantheace/Synveil/actions/runs/39598691537)
confirmed the label and `/home`-only `persistence.conf`. See
[the exact-revision evidence record](validation/desktop-home-0579de/README.md).

This proves a **developer live-home file survives two independent VM boots**,
not encrypted or authenticated storage, trustworthy owner login, installed
system A/B boot, owner recovery, physical boot or signed updates. The
normal Synveil install milestone remains incomplete.

## Same-boot PAM logout and relock runtime evidence

The subsequent disposable XFCE profile at exact SHA
`ad7b2b76726b250b784594667ae7b805d515cc9e` passed
[full QEMU run 39639758799](https://github.com/magicmantheace/Synveil/actions/runs/39639758799):
wrong password denied, correct password opened a non-root XFCE session,
user-owned `xfce4-session-logout --logout --fast` after actual Xfwm4
multiwindow proof, LightDM greeter returned with the old session and
panel absent, another wrong password was denied, and a second correct
password opened a fresh non-root desktop. QMP retained **separate RGB
captures** of the first authenticated session, the relocked greeter and
second desktop. See [exact tested evidence](validation/desktop-pam-relock-ad7b2b7/README.md).

This is *one VM boot with two distinct user sessions*, **not two
independent machine reboots**. The credential was still a temporary
CI-injected secret, not an owner-enrolled persistent password.
Installed native Synveil account authority and owner recovery remain
unverified.

## First working password-authenticated *developer VM* graphical login

An isolated Debian/XFCE image at exact SHA
`00edc3db054111f95da18af4bb1e4e332074ff87` passed
[Actions 39626829728](https://github.com/magicmantheace/Synveil/actions/runs/39626829728).
QEMU generated a fresh random 128-bit test credential, supplied it
only through a disposable virtual `fw_cfg` channel, and typed an
independently random **wrong** password first. No user desktop
started during that negative observation interval. A **correct**
password typed through the private QMP keyboard then launched
an actual non-root XFCE session and panel, with QMP screenshot.
See [exact-revision evidence](validation/desktop-pam-00edc3d/README.md).

This is **PAM/LightDM integration evidence in a guest**, not secure
first-owner enrollment, persistence of authentication across
cold boots, production secret handling, normal logout/relock,
native Synveil broker integration, or an installed operating
system. The first owner boot gate remains unchecked.

## Experimental LightDM lock-gate validation (separate from login)

The Debian/XFCE four-boot persistence proof remains isolated to the
developer live account; it is **not an authenticated desktop**.
A separately opt-in profile is configured with
`SYNVEIL_DESKTOP_LOGIN_GATE=1`, Debian
`live-config.noautologin`, and a mandatory LightDM prestart that locks
the disposable `synveil` system account before exposing its greeter.
The profile also disables LightDM guest and autologin shortcuts.

A complete `desktop-login-gate.yml` guest proof must:

1. Boot the distinct image through QEMU OVMF and the recognized live
   boot menu, without a writable home or physical device.
2. Observe a real LightDM + GTK greeter, the locked system account,
   no `xfce4-session` or panel, and an allowlisted guest serial marker.
3. Press exactly one Return without entering any password and fail if
   a non-root desktop user session starts.
4. Capture actual guest graphics; a black screen, firmware boot menu,
   mere source assertion or service marker alone does not pass.

This is **negative authentication**: it deliberately provisions no
working user password. It cannot establish successful password
verification, initial enrollment, logout, reboot relock, recovery or
native Synveil session authority. Passing this gate is necessary but
not sufficient to release a real signed-in desktop.

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

The read-only-storage native run at `ffd0b0a` (38634696072) passed its audit
gates but failed the graphical settings write/reboot gate: the retained write
log contains no settings-save marker and the settings file never appeared.
It must not be reported as a complete successful pipeline. QMP key injection
now holds each press briefly before release so USB polling can observe it;
the full [612bd80 run](validation/native-612bd80/README.md) passed the settings
write/reboot gate and all existing native, guest and packaged-VM gates.

## Offline audit export validation contract

Basic snapshot export/verify, replacement refusal and well-formed edit detection
passed at [612bd80](validation/native-612bd80/README.md). This does not validate
later publication changes or storage-failure gates. Those unnamed-inode
publication and tmpfs full/read-only export checks subsequently passed in the
complete [9f722d0 run](validation/native-9f722d0/README.md).

Snapshot export must preserve the source, hold cooperative inspection locks,
copy exactly the bounded validated bytes, enforce private separate output
storage, refuse replacement and report only counts/digest metadata. Tests must
exercise the actual CLI, lock refusal, malformed source and unsafe destinations,
and detect edits that remain structurally valid using an independently retained
digest. Target build/link audits and guest export/verify execution are required
before runtime completion. A digest match is not origin authentication or an
independent retention/integrity system; archive deletion remains unavailable.

The unnamed-inode implementation must discard unpublished failed copies
without unlinking destination pathnames, exclusively publish verified bytes,
and preserve competing files/symlinks even when the output directory is moved.
Unsupported filesystems/procfs must fail closed. Errors after publication may
leave a complete copy without a success receipt; no pathname deletion is allowed.

The isolated tmpfs gate must also require export failure on full/read-only
storage with no partial snapshot and unchanged source, then successful
export/verification after recovery. Specific failure categories prevent a
permissions or invalid-input error from passing as storage-exhaustion evidence.
These tmpfs scenarios passed at
[9f722d0](validation/native-9f722d0/README.md). The later disposable `audit-ext4`
scenario requires a newly created host-side image, mounted ext4 identity,
actual ENOSPC warnings, unchanged seeded history, healthy main-core IPC while
full, failed export without partial output, and successful appends/export after
space recovery. Host control-flow fixtures are not native ext4 evidence;
exact-revision guest execution passed in the complete
[fcba067 run](validation/native-fcba067/README.md).

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
silent sink failures and automatic repair. These gates passed at
[3fb0158](validation/native-3fb0158/README.md); they do not establish power-loss durability.

The archive host suite also checks a state disk modified during copying and
restored before final validation, and refuses hardlinked source members. Each
copied member's streaming digest must match its expected digest; the state disk
uses the blank-state digest recorded at package creation. Open descriptors are
checked before and after copying for regular-file identity, size, owner, mode
and single-link count. Native archive/upload validation passed at
[3fb0158](validation/native-3fb0158/README.md).

The later `audit-enospc` gate tests a separate 1 MiB tmpfs, not the main ext4
state disk. It requires real ENOSPC, byte-preserved valid history, core status
and console events, resumed appends after freeing synthetic storage, and
successful unmount. Native guest execution passed at
[ebc9f4e](validation/native-ebc9f4e/README.md). The subsequent read-only/read-write
remount extension requires byte preservation and healthy console/IPC under
EROFS, followed by valid resumed appends. Host fixtures cover control flow;
the isolated extension passed in the partial
[ffd0b0a run](validation/native-ffd0b0a/README.md). Full settings revalidation
passed at [612bd80](validation/native-612bd80/README.md). Ext4-specific failure and
power-loss guarantees remain open.

## Abrupt emulator stop after acknowledged audit writes

The `audit-crash-smoke` gate requires a private newly generated ext4 image,
first-boot native append/export/verification without a global guest sync,
host-retained digest evidence, verified emulator-child SIGKILL and wrapper
reaping, then independent reboot and exact-byte verification of both files.
Missing/ambiguous identity, failed signal, unexpected wrapper status, digest
echo or duplicate evidence, and failed reboot verification must fail closed.
Host fixtures do not establish native filesystem durability. This basic gate
passed in the full [00476e5 run](validation/native-00476e5/README.md).
That guest run validates only
acknowledged writes under emulator-process loss, not interrupted writes,
rotation crash windows, drive-cache loss or physical power failure.

The later `audit-crash-smoke --rollover` gate adds a fully acknowledged rollover
of a 4 MiB synthetic journal. It must verify seed preservation, archive metadata
and count before cutting the emulator, retain separate active/archive digests
on the host, and verify both originals and both exported snapshots after reboot.
Host fixtures must reject lost/ambiguous archive evidence and execute the shell
under corruption, count and export/copy failures. This gate passed in the
[partial c8a759b run](validation/native-c8a759b/README.md), which failed two
graphical gates. The later full
[1edc09e run](validation/native-1edc09e/README.md) passed all gates, including
both graphical regressions and completed-rollover recovery. Completed-rollover
survival does not prove recovery from an interrupted rotation.

The `audit-crash-smoke --archive-only` gate constructs a valid 4 MiB archive
with no active journal, representing the layout after archival rename and
before active-file creation. Fixture preparation synchronizes the seed and its
directory before starting the helper; no global sync follows native recovery
appends or exports. A failed seed sync must prevent helper startup and success
evidence. The native helper must create and acknowledge a
new active journal without changing the retained archive. Both originals and
their exported snapshots must then survive the same verified emulator stop and
independent reboot, checked against separate host-retained digests. Host tests
execute the generated shell for both archive layouts, including corrupt history,
wrong record counts and failed or changed archive exports. This gate passed
in the full [c97f554 run](validation/native-c97f554/README.md). Constructing
this layout does not prove interruption
of a live rename/fsync operation or physical power-loss recovery.

Digest receipt parsing counts every exact-prefix candidate line, including
malformed values. A valid receipt accompanied by a truncated, invalid or
conflicting second receipt must fail for both active and archived evidence.
Do not filter to well-formed values before deciding whether the evidence is
unique. Host regressions cover both receipt orders, CRLF, incorrect lengths,
uppercase values, trailing whitespace and embedded NUL bytes. This parser
hardening passed host CI and the full
[1edc09e native run](validation/native-1edc09e/README.md).

## Single-keyboard graphical QEMU target

Graphical QEMU launches now use `q35,i8042=off,vmport=off` with the explicitly
configured USB keyboard and tablet. Serial-only launches keep the plain Q35
machine. Q35's otherwise implicit PS/2 keyboard can leave the guest's first
evdev choice different from QMP's input target. The c8a759b failures are
consistent with that ambiguity, but do not identify the selected device.
Both previously failing graphical scenarios and every existing native gate
passed at [1edc09e](validation/native-1edc09e/README.md). That establishes
this QEMU configuration, without proving which device caused the earlier
failures; guest authentication policy is unchanged.

Host argument tests require one explicit USB keyboard and the disabled i8042
controller for both GUI and headless-video launches, and ordinary Q35 for
serial-only launches. Native preflight requires QEMU to advertise its i8042
machine property before spending time building the toolchain/image. Physical
multiple-keyboard handling is outside this QEMU hardware target.

## Observable QMP VT-switch chord

The later QMP helper sends fixed Ctrl+Alt+F2 key-down events together, holds
them for the same bounded interval as a single key, then sends reverse-order
release events in a separate command. The previous combined down/up command
could expose only an unpressed USB report to a guest poll. Key release is
attempted in a finally block even if acknowledgement or holding fails. Existing
key allowlists and the single fixed VT target remain unchanged.

Host regressions observe the full held chord during a simulated USB poll,
require release after interrupted holds and uncertain key-down delivery, and
validate both QMP commands over a mock socket. Native VT-switch, graphical
session-revocation and every existing guest gate passed in the full
[c97f554 run](validation/native-c97f554/README.md).

## Audit I/O error boundaries

A private storage adapter enables deterministic host tests of EIO returns at
write, file synchronization, archive rename and parent-directory synchronization.
The rotation matrix must stop at the failed operation, retain the full previous
journal in either its active or archived location, preserve archive inode
identity, and recover with independently inspected records on a later append.
The ordinary append matrix must preserve prior history and propagate write or
sync failure. A partial-write error must preserve the exact incomplete tail,
leave retained archives unchanged, and refuse further append and inspection.
Complete bytes may remain after sync failure; the tests must not assume an error
means no mutation or claim exactly-once retries. All four new tests passed
[host CI at 6e7d7ed](https://github.com/magicmantheace/Synveil/actions/runs/38837141454),
with 111 workspace tests, formatting and clippy. Full native revalidation of the
production adapter passed at [4390a8c](validation/native-4390a8c/README.md). Simulated EIO returns
do not establish real block-device failure, interrupted rotation or power-loss
durability; the durable-audit roadmap item remains open.

Post-merge host CI at `200f903` exposed a failure at the new rotation fixture's
boolean archive-inspection assertion after the earlier run passed. Parallel
process launches can transiently inherit an audit flock until exec. The fault
fixtures now use bounded WouldBlock-only inspection retries with error output;
all other inspection errors fail immediately, and partial-tail assertions
require InvalidData rather than accepting any error. Production lock handling
remains nonblocking. The combined fixture correction and terminal-return
diagnostics passed [host CI at 29e9504](https://github.com/magicmantheace/Synveil/actions/runs/38844827501),
including 112 Rust tests, 14 graphical-login fixtures, formatting and clippy.

## Terminal-return failure diagnostics

The [partial 200f903 run](validation/native-200f903/README.md) passed all audit
and recovery gates, but graphical login stopped at `checking-user-return`
after successful authentication and confirmed UID1000 terminal creation. No
return-to-dashboard marker arrived; the log does not identify whether the shell
remained alive or its dashboard became unavailable. This is a failed pipeline,
not full native validation of the storage adapter.

The guest return check now emits one of three fixed failure reasons: missing
identity, a living non-zombie shell, or unavailable dashboard. The host stops
on any exact failure marker before considering later success text, including
after the user-desktop crash/relogin sequence. Host fixtures execute the generated
shell against synthetic process metadata for all outcomes and reject contradictory
late success. These diagnostics preserve the same return-success requirements;
they do not establish a fix for the native graphical failure. The host fixtures
passed at `29e9504`; fresh native validation is required.

## Released-key login acquisition

The later native `99caf0c` run failed the same graphical return check and emitted
`SYNVEIL_USER_RETURN_FAILED_SHELL_ALIVE`. All audit gates passed. This narrows
the failure to a living terminal shell; it does not prove why `exit` was missed.
A candidate race is reacquiring EVIOCGRAB before Ctrl+Alt+F2 release, preventing
Linux's console keyboard handler from seeing modifier-up events. The pinned Linux v6.18.55
[keyboard handler](https://github.com/gregkh/linux/blob/v6.18.55/drivers/tty/vt/keyboard.c)
refreshes LEDs on handler restart, without rebuilding its key-down bitmap.

The candidate fix quiets the dedicated tty2 login VT using verified K_OFF,
then waits for an idle physical keyboard before grabbing it, drains bounded
pre-login events, and rechecks key state. If the recheck sees a held key, release
the grab and retry. K_OFF suppresses console character delivery while still
allowing the console modifier handler to process releases before the grab.
tty1 and authorization rules remain unchanged. Host C tests model immediate
idle, held release, a query/grab race, persistent held input, ioctl/read/poll
failure, truncated events and event flooding. Full native graphical and all
existing guest gates passed together at
[4390a8c](validation/native-4390a8c/README.md), including terminal-return and
graphical cleanup markers in the downloaded, digest-verified guest log. This
validates the configured QEMU target without proving the earlier exact root cause.

## Active-path replacement after rotation

A test-only storage adapter substitutes unsafe objects after successful archive
rename and parent-directory sync, before reopening the next active journal.
The symlink, hardlink, directory and FIFO cases must all return failure without
reaching the record-write operation. The retained archive must keep its exact
bytes and inode, unrelated target data must stay unchanged, and the writer must
leave the substituted object in place. After the fixture explicitly removes
its own object, a normal append must recover with a valid active journal and
the same retained archive. Host CI at `d70c745` passed all four cases,
113 Rust tests, formatting and clippy. The
production writer is unchanged; this adds neither runtime fault hooks nor
automatic repair, retention release, device-error or power-loss evidence.

## Process loss at audit rotation boundaries

The test executable launches an exact-filtered worker that kills itself with
SIGKILL after rename, after directory synchronization, before new-active write,
or after a 17-byte partial write. The parent verifies signal 9, archived bytes
and inode, lock release and independently inspected restart records. A partial
tail must remain exact and further appends must fail with InvalidData. The seed
is synchronized before the child starts; no sync or cleanup precedes inspecting
the interrupted state. A five-second timeout kills/reaps the child and fails
instead of accepting a watchdog kill as evidence.

Host CI at `f75de2bec42834c27688359c82039ad890d809fb` passed all four cases,
115 Rust tests, formatting and clippy
([run 38907418988](https://github.com/magicmantheace/Synveil/actions/runs/38907418988)).
There are no production crash flags. These are userspace
process cuts at known operation boundaries while the host kernel and cache
survive; interrupted kernel operations, guest reboot during rotation, real
device errors and volatile-cache/physical power loss remain unproven.

## Complete but unacknowledged audit records after process loss

The process-loss matrix now includes cuts immediately after a complete write,
after its file sync and after its final parent-directory sync. A write-completed
flag ensures a rotation's initial old-file sync cannot satisfy the new-record
sync boundary. Rotation coverage expands from four to seven cuts. Five append
cuts also run against both a missing active journal and an existing journal:
before write, after a partial write, after full write, after file sync and after
directory sync (17 cases total).

Complete unacknowledged records must survive and remain before a distinct
restart event, with independently inspected record counts and no truncation or
deduplication. Partial tails must be refused by both inspection and append.
Existing active and archived inodes remain stable; ordinary appends must not
create an archive. The shared worker requires signal 9 and a bounded timeout.
Host CI at `72df7894ab8d1684ee49c391bf0ed7436f50d956` passed all 17 cases,
the held-lock fixture regression, all 117 Rust tests, formatting and clippy
([run 38914789468](https://github.com/magicmantheace/Synveil/actions/runs/38914789468)).
Kernel/cache survival still limits these results to process failure.

The full [aa17982 native run](validation/native-aa17982/README.md) passed all
43 steps before the process-loss fixtures were merged. It includes the unsafe
active-path regression source, but does not include these later SIGKILL tests.

The first extension CI run at `27f89ec` passed all 17 process-cut cases but
failed two older successful-append fixtures with WouldBlock. More concurrent
fork/exec activity can inherit open flocks until exec. Successful fixture
appends now retry only WouldBlock for a bounded interval, as inspection already
does; production append and deliberate contention/error assertions are direct
and nonblocking. A regression holds/releases a real lock, requires exactly one
new record, and verifies that an incomplete tail returns InvalidData unchanged.
The corrected source passed the fresh `72df789` host run above; the first
run remains a failed pipeline.

## Offline retention release candidate

The release candidate requires a verified distinct private snapshot and a new
durable authorization receipt before removing only the derived `.1` archive.
Host tests require unchanged active bytes/inode and copy, accurate pre-unlink
receipt ordering (authorization then synchronized completion), digest/structure
mismatch refusal, symlink/permission/alias
and receipt-collision refusal, and nonblocking busy-lock failure. CLI coverage
exercises explicit arguments and verifies no event-content disclosure. A full
journal fixture must resume a second rotation after release while preserving
the original snapshot. Host CI at `b27fe1d67db4bd2cd5379770b883db4f8b7000ae`
passed all 122 Rust tests, formatting and clippy
([run 38994925827](https://github.com/magicmantheace/Synveil/actions/runs/38994925827)).
[Bootstrap lint 38994928997](https://github.com/magicmantheace/Synveil/actions/runs/38994928997)
also passed, including real process-cut host fixtures. That host checkpoint
predates the initial native proof below. Interrupted release, real sync/unlink
device errors and power-loss behavior remain unvalidated.

The native workflow now includes `audit-crash-smoke --rollover
--release-archive`: retain separate digests, abruptly stop/reap the emulator,
verify both journals and snapshots on reboot, then explicitly release only the
copied archive. Require archive absence, durable pre-unlink receipt semantics,
and renewed active/copy digest verification before the read-success marker.
The original crash gates retain their preservation-only behavior and log names;
release uses separate logs. The 11-test harness passed locally (two CI-only
signal tests skipped), with shell syntax and malformed/missing archive-input
checks. Initial native execution passed at `e81d6d5` as recorded below; the
corrected candidate still requires fresh native proof. The feature branch runs
separately, without cancelling a development-branch native run.

## Active-journal release precondition and storage-error boundaries

The initial `e81d6d5` release candidate passed all 44 native steps in
[run 38995115558](https://github.com/magicmantheace/Synveil/actions/runs/38995115558),
including the new release gate. Review nevertheless found that an unsafe active
symlink could survive release as a dangling path. The corrected candidate opens,
locks and validates the active journal, requires a distinct archive/copy inode,
and rechecks active identity before unlink. Missing, symlinked, hardlinked,
directory, FIFO, permissive, incomplete and invalid active paths must preserve
archive/copy bytes and their own inode/type without publishing authorization.
A valid empty active journal is allowed. Corrected source
`da43fc1b0e7ca87a4cc96304997cddb539517939` passed all 126 Rust tests, formatting
and clippy ([host run 39038885162](https://github.com/magicmantheace/Synveil/actions/runs/39038885162)),
plus [bootstrap lint 39038889259](https://github.com/magicmantheace/Synveil/actions/runs/39038889259).
Fresh [native run 39038885250](https://github.com/magicmantheace/Synveil/actions/runs/39038885250)
is pending; the initial successful run is not proof of this correction.

A private statically dispatched adapter tests EIO at all eleven release storage
steps: copy sync, copy-directory sync, anonymous receipt creation, intent write,
intent sync, publication, receipt-directory sync, archive unlink,
journal-directory sync, completion write and completion sync. Each error must
stop later operations, preserve active/copy bytes and inodes, and retain the
archive until unlink actually succeeds. A published intent can remain after an
error; post-unlink errors must not recreate history. A final sync error can leave
complete but unacknowledged completion bytes. Pre-unlink recovery uses a fresh
receipt, while an absent archive refuses retry without changing old receipts.
Partial intent writes must remain unpublished; partial completion writes must
preserve the valid intent and exact incomplete tail. These are host error-return
and partial-write fixtures, not real device EIO or process/power-loss evidence.

## Process loss during offline archive release

The test-only release matrix covers nine SIGKILL points from partial anonymous
intent through synchronized completion. Require a matching fixed checkpoint and
actual signal-9 exit, reaped children, exact active/copy bytes and inode identity,
expected archive/receipt state, no named partial intent, kernel lock release,
fresh-receipt pre-unlink recovery and unchanged refusal after unlink. The matrix
itself runs in an isolated exact-filtered test process, with bounded watchdogs;
a watchdog termination cannot count as a successfully reached cut.

Baseline file/directory sync occurs only before the cut. Observe interrupted
state before recovery synchronizes anything. Partial completion preserves the
valid first intent and a 17-byte incomplete tail; complete unacknowledged
completion may remain after process loss. No runtime fault flags, receipt repair,
automatic retry or archive recreation is added. Corrected timeout revision
`4f4c0a2248adb95ffc2b9934a8831249af28f3fc` passed all nine cuts, 129 Rust tests,
formatting and clippy ([run 39046865707](https://github.com/magicmantheace/Synveil/actions/runs/39046865707)).
Kernel/cache survival limits the proof to process loss, not guest/device/cache/power failure.

The matrix's outer watchdog reserves all nine five-second worker budgets plus
30 seconds for fixture synchronization and recovery (75 seconds total). It owns
a dedicated process group and terminates descendants on outer timeout. The
initial 15-second aggregate budget was corrected after review; even successful
workers must not collectively exhaust an undersized outer deadline.

## Offline release command recovery checks

`src/veil-core/tests/audit_retention_cli.rs` exercises the built `veil-audit`
binary in separate processes. It checks missing/duplicate required flags,
foreign flags, incomplete pairs, a foreign command and malformed/non-UTF-8
digests against intact active/archive/snapshot fixtures. Refusals must produce
no success output, disclose no supplied path/digest/event content, leave all
three histories unchanged and publish no authorization receipt.

The replay case first completes a release, then retries both with the archive
absent and after placing an identical-byte archive in the slot. The old receipt
must remain byte-for-byte intact, and the new archive must survive the retry.
A fresh explicit receipt can release that archive while preserving the active
inode/history, snapshot and old receipt. Separate exclusive file locks on the
active journal, archive and snapshot must refuse before receipt publication;
release succeeds after the locks are dropped. Each release subprocess has a
five-second watchdog; watchdog termination is a test failure.

These host command-boundary checks passed at
`2261ec59e5b6438cd457fb1c91ef781eb506a250`: all 132 Rust tests, formatting and
clippy passed in [run 39052487780](https://github.com/magicmantheace/Synveil/actions/runs/39052487780),
job `117453710277`.
They supplement the nine-cut process-loss matrix without adding guest live-release
interruption, real storage faults, cache loss, power-loss or authenticated integrity proof.

## Release receipt inspection

`inspect-release` adds read-only observation of authorization-only, partial
completion and recorded completion states. Unit fixtures verify that receipt,
snapshot and surviving histories remain unchanged, including when the active
file is absent or the archive slot has been reused. Malformed, duplicate-key,
extra-line, oversize, unsafe-mode/link, mismatched and busy inputs must refuse;
missing scope locks must remain absent. The nine actual SIGKILL release cuts
now exercise inspection of their residual receipts before recovery or sync.
The built CLI checks each state and refuses malformed receipts/foreign output
flags without echoing event contents.

The native post-reboot release gate now also runs the inspector and requires
`completion-recorded` with `archive_state:"not-inspected"` before rechecking
journal/snapshot checksums. Local Python driver checks passed 11 tests with two
real-process fixtures skipped because this environment proxies process identity.
Source `39d26273c9b54fb9c18c73ec0a65e0d0849923fe` passed all 136 Rust tests,
formatting and clippy in [run 39058995249](https://github.com/magicmantheace/Synveil/actions/runs/39058995249),
job `117473083817`. All 44 native steps passed at
[9059bf6](validation/native-9059bf6/README.md); the inspector is now merged.
Inspection never upgrades a process-cut observation into guest/cache/power-loss proof.

The preceding CLI-only review correction at `953c368` also passed 132 Rust tests,
formatting and clippy in [run 39058533152](https://github.com/magicmantheace/Synveil/actions/runs/39058533152)
before PR #25 merged. It checks actual supplied malformed digest values for
error disclosure. The prior merged source `8d8dfed` passed all 44 native steps in
[run 39052189785](https://github.com/magicmantheace/Synveil/actions/runs/39052189785).

## Initial live release guest interruption

The initial isolated `audit-release-crash` fixture targeted four live release syscall
boundaries and eight independent QEMU sessions. Its exact checkpoints, replay
expectations and scope are defined in [AUDIT.md](AUDIT.md#live-release-guest-interruption-fixture).
The regular image and native runtime contain no tracing helper or fault controls.
The new fixture becomes an additional required step in the native workflow.

Local shell recovery tests passed four tests covering all allowed archive/receipt
states, forbidden completion/presence combinations, corrupt copies, missing or
ambiguous host-retained digests, cut failure and reboot failure. Fixture staging
checks passed eight tests, including baseline isolation. The C tracer compiled
with `-Wall -Wextra -Werror`; argument refusal passed locally, while six real
tracing subcases skipped because local policy blocks ptrace. Under GitHub Actions,
tracing unavailability is a failure, not a skip.

Host source `d3a0360cd4ad04564abd131d706f6930d05f1519` passed all four real
tracing boundaries and both refusal subcases without skips, four shell recovery
tests, eight staging tests and the full 136-test Rust workspace with formatting
and clippy in [run 39065469118](https://github.com/magicmantheace/Synveil/actions/runs/39065469118),
job `117492261989`. The tracing worker in this host suite is a controlled syscall
fixture, not the native Rust command in a guest. That result alone did not
establish native guest recovery; the corrected native proof is recorded below.

The initial native candidate `798b905` failed the live-release guest step in
[run 39065683248](https://github.com/magicmantheace/Synveil/actions/runs/39065683248).
Review identified that the tracer recognized `AT_EMPTY_PATH` publication, while
production uses `linkat(AT_FDCWD, "/proc/self/fd/<fd>", ..., AT_SYMLINK_FOLLOW)`.
The earlier controlled worker used the same incorrect assumption as the tracer;
its passing host result did not establish compatibility with the native command.
The corrected tracer reads the bounded procfs descriptor source and tracks that
receipt descriptor. The controlled worker now uses the production publication
form and rejects the old form as a negative case. A separate host CI step traces
the actual built `veil-audit` at every cut and runs its read-only inspector on
each residual receipt. The corrected results follow.


Corrected source `18f43f16b81ad42e31a30d733675c5c64cab008d` passed
[host run 39071600912](https://github.com/magicmantheace/Synveil/actions/runs/39071600912),
job `117510191274`: all four actual built-CLI cuts followed by successful
inspection of unchanged residual receipts, seven controlled-worker tracing/refusal
subcases without skips, the recovery/staging suites, all 136 Rust tests,
formatting and clippy. The new actual-CLI check is a separate post-build host
step, not a simulation worker or guest proof. Only teardown lock contention is
retried while a killed tracee closes its descriptors; every other inspector
failure fails immediately. The corrected native run supersedes the failed
`798b905` guest candidate; physical power-loss and authenticated integrity remain open.

Merged inspector source `4cfd054` separately passed all native steps in
[run 39064960467](https://github.com/magicmantheace/Synveil/actions/runs/39064960467).
The live-release fixture failure does not invalidate that earlier inspection proof.


Corrected source `d846b81` passed all 45 native steps in
[run 39071778326](https://github.com/magicmantheace/Synveil/actions/runs/39071778326),
including all four guest cuts and eight independent boots. The downloaded ZIP's
SHA256, fixed checkpoint/kill/recovery markers and fixture-manifest hash were
verified; see [retained evidence](validation/native-d846b81/README.md). The
post-unlink/pre-directory-sync archive was present after replay, an allowed
outcome; both later cuts observed archive absence. PR #27 is merged.

## Completion receipt guest-reboot extension candidate

The matrix now additionally stops after the full completion line's final newline
write returns, and after its subsequent receipt-file fsync returns. Both cuts
occur before the caller receives the CLI report. The first accepts any valid
retained authorization/completion prefix after reboot, including a complete but
unacknowledged line. The synchronized cut requires `completion-recorded`. Both
require archive absence, retained-copy/active checksums and unchanged old receipts.
The six-cut matrix uses twelve fresh QEMU boot sessions.

The controlled syscall worker, actual built-CLI host checks, recovery shell
fixtures and staging proof counts now include both cuts. Local C compilation,
argument refusal, four recovery shell tests and eight staging tests passed;
nine tracing subcases skipped locally because ptrace is blocked. CI must execute
them without skips. Fresh native validation for the extension remains pending. The four-cut proof above does not cover these new boundaries or
physical device/cache/power loss.

The initial extension host run `39161261783` passed the new controlled tracing
suite, then exposed transient `WouldBlock` during positive setup of
`stable_directory_lock_blocks_readers_and_writers_without_mutation`. That setup
now uses the existing test-only bounded contention retry, which waits for
parallel forked children to close inherited descriptors at exec. Deliberate
contention assertions still call production operations directly and must refuse
immediately; no runtime lock or retry behavior changes.


Corrected extension source `f04ade734c4acb80f321005e393684847378701e`
passed [host run 39161435398](https://github.com/magicmantheace/Synveil/actions/runs/39161435398),
job `117775393623`: nine controlled-worker subcases without skips, all six
actual built-CLI cuts and subsequent receipt inspection, four recovery shell
tests, eight staging tests, all 136 Rust tests, formatting and clippy. This
includes complete-but-unacknowledged records after the final newline and after
file sync. Exact source `cbf272ce007a0ad7a15a346289fbd6f3c484cc8f`
then passed all 45 steps in
[native run 39161628602](https://github.com/magicmantheace/Synveil/actions/runs/39161628602),
job `117775955379`, including all six cuts across twelve independent boots.
The downloaded artifact, fixture hash and unique checkpoint, kill/reap and
recovery markers were verified; see
[retained evidence](validation/native-cbf272c/README.md). The first two cuts
observed archive presence after reboot and the four later cuts observed absence.
This remains guest kernel-loss/filesystem-replay evidence with host page cache;
it is not physical power-loss, volatile-device-cache, real device-error or
independently authenticated integrity proof.

## Persistent audit sink status candidate

The versioned `status` result now includes the configured persistent-audit
state and a process-local failed-write count. A configured sink is `healthy`
only after its latest append and synchronization succeeds; an append, file-sync,
rotation or directory-sync failure makes it `degraded`. A later successful
append restores `healthy` while retaining the count. No configured path reports
`disabled`; `starting` covers the interval before a configured process attempts
its first write. The core service itself remains ready so recovery can inspect
the failure.

The real-core host integration requires `healthy` with zero failures for a
private writable journal and `degraded` with the exact preceding failure count
for an incomplete journal, while retaining existing console warnings and byte
preservation checks. After fixture-controlled removal of the damaged pathname,
the next successful request must report `healthy` without clearing the count.
The full 136-test Rust workspace, formatting and clippy passed on the pinned
1.99 toolchain locally and in exact-revision host run
[39163064705](https://github.com/magicmantheace/Synveil/actions/runs/39163064705)
at `bf9e1fd`, including six-cut tracing of the built release CLI. The rollover,
tmpfs exhaustion/read-only and ext4 exhaustion guest commands now capture the
real helper's status and require configured `healthy`/zero or
`degraded`/nonzero results as appropriate. Their 31 host control-flow tests
reject incorrect state, configuration and counts. Exact source `141e20e` passed
all host checks in [run 39163522216](https://github.com/magicmantheace/Synveil/actions/runs/39163522216),
including the full Rust suite, actual built-CLI tracing, formatting and clippy.
Exact source `141e20e8b558a7ef36016f684acfad958694b072` then passed all
45 native steps in
[run 39163522194](https://github.com/magicmantheace/Synveil/actions/runs/39163522194),
job `117781621003`. The downloaded ZIP's SHA256 and clean-revision build
manifests matched; retained rollover, tmpfs and ext4 logs contained every
required health transition and one final pass marker each. See
[retained evidence](validation/native-141e20e/README.md). This does not complete
durable audit or establish survival of a successful synchronization across
device-cache or physical power loss.

## Live aggregate CPU utilization candidate

The read-only observer now takes two aggregate `/proc/stat` samples across a
minimum 100 ms interval and reports busy time as integer basis points together
with the actual monotonic sample window. Guest counters are excluded because
Linux already includes them in user/nice. A zero-delta interval,
logical-topology change or permitted iowait decrease reports `unavailable`
without inventing a percentage; malformed or backward non-guest counters other
than iowait fail the observation. Cumulative counters come from the later sample.

Host cases cover a 25.00 percent delta, guest-time accounting, iowait decrease,
zero delta, topology change and an individual counter regression. The full
140-test Rust workspace, formatting, clippy and all 31 recovery control-flow
tests passed locally and in exact-revision host run
[39253717313](https://github.com/magicmantheace/Synveil/actions/runs/39253717313).
Exact source `255101d0c5329bba12546466480ec748ec1e3097` then passed all 45
native steps in
[run 39253717232](https://github.com/magicmantheace/Synveil/actions/runs/39253717232),
job `118064035542`. The downloaded native artifact matched its published SHA256;
both manifests identify the clean tested revision, and the retained guest log
contains `SYNVEIL_OBSERVE_OK` after requiring the utilization and window fields.
See [retained evidence](validation/native-255101d/README.md). This is aggregate
CPU busy time, not per-CPU or per-process attribution or historical telemetry.

## Per-logical-CPU frequency candidate

The observer reads bounded current-frequency values from Linux cpufreq sysfs,
preferring `scaling_cur_freq` and falling back to `cpuinfo_cur_freq`. Frequencies
are integer kHz and sorted by numeric logical CPU ID. Missing drivers and offline
CPUs and temporarily busy policies remain explicit through `unavailable` or
`partial` status; malformed data makes only the optional frequency object
invalid. Host fixtures cover primary and fallback files, ordering, absent
cpufreq directories, busy-policy errors and malformed values. The native
observer gate requires the frequency object even when QEMU exposes no cpufreq
driver. Exact revision `376e2b43fe70a3550009a6d02e267a98e5fe1d34` passed all
143 Rust tests, formatting and clippy in
[host run 39300793592](https://github.com/magicmantheace/Synveil/actions/runs/39300793592),
then passed all 45 native steps in
[run 39300793735](https://github.com/magicmantheace/Synveil/actions/runs/39300793735).
The downloaded artifact matched its published SHA256, both manifests identify
the clean tested revision, and the guest log contains `SYNVEIL_OBSERVE_OK` after
requiring the frequency object. See
[retained evidence](validation/native-376e2b4/README.md). QEMU exercised the
unavailable-driver result; positive and partial readings remain host-fixture
evidence rather than physical-hardware validation.

## Per-logical-CPU utilization candidate

The observer parser now validates every logical CPU accounting row in both
samples, matches stable numeric CPU IDs, and derives a bounded busy-basis-point
record for each CPU over the aggregate sample window. Host tests cover distinct
busy and idle CPUs, numeric ordering, malformed rows, duplicate identifiers,
topology changes, guest-time accounting, iowait decreases and counter rollback.
The native guest gate requires the per-CPU utilization fields from the real
`synctl --json observe` response. The client structurally validates the
versioned result, sorted IDs, per-record status and bounded values before it can
exit successfully. Exact revision `23f323345b073008357a4ca283b9ae86d1e3ec2f`
passed all 145 Rust tests, formatting and clippy in
[host run 39301950391](https://github.com/magicmantheace/Synveil/actions/runs/39301950391),
then passed all 45 native steps in
[run 39301950501](https://github.com/magicmantheace/Synveil/actions/runs/39301950501).
The downloaded artifact matched its published SHA256, both manifests identify
the clean tested revision, and the guest log contains `SYNVEIL_OBSERVE_OK` after
requiring the per-CPU fields through the structurally validating native client.
See [retained evidence](validation/native-23f3233/README.md). This is a live QEMU
sample window, not per-process attribution, historical telemetry, or physical-
hardware validation.

## Thermal-zone observation candidate

The observer reads at most 256 Linux thermal zones, sorts their numeric IDs and
reports signed kernel temperatures in millidegrees Celsius with bounded type
labels. Missing thermal support and unreadable zones are explicit; malformed
zone data invalidates only the optional thermal object. Host fixtures cover
ordering, signed values, partial availability, absence and malformed values. The
QEMU observer gate requires the thermal object even when the virtual machine
exposes no thermal zones.
Exact revision `b5f5cae2d0971b200ce7e7ea76fd7c2965a221a1` passed all 147 Rust
tests, formatting and clippy in
[host run 39302160972](https://github.com/magicmantheace/Synveil/actions/runs/39302160972),
then passed all 45 native steps in
[run 39302160871](https://github.com/magicmantheace/Synveil/actions/runs/39302160871).
The downloaded artifact matched its published SHA256, both manifests identify
the clean tested revision, and the guest log contains `SYNVEIL_OBSERVE_OK` after
requiring the thermal object. See
[retained evidence](validation/native-b5f5cae/README.md). QEMU exercised the
unavailable-zone result; positive and partial readings remain host-fixture
evidence. Power and energy measurements, hardware alarm interpretation,
cooling policy, and physical-hardware validation remain open.

## Bounded process snapshot

The observer scans at most 16384 `/proc` directory entries and returns at most
128 PID-sorted process records. The native client rejects a successful response
above that producer cap. Before encoding, the core removes highest-PID records
and increments the omitted count until the complete response, including
the request ID and every other metric, fits the 64 KiB IPC frame. PID plus
kernel start time provides stable identity across PID reuse. Records contain
scheduling topology/state, cumulative CPU ticks and point-in-time memory
counters; argv, environments and executable paths are never opened. The name
embedded in `stat` is not returned. Per-process files are limited to 4096 bytes,
and exit or permission races are counted explicitly. Host fixtures cover names
containing closing parentheses, numeric ordering, disappearing processes, PID
mismatch, oversized/truncated stat data, non-UTF-8 embedded names and invalid
ranges. The native client structurally validates status/count relationships,
sorted identity and every exposed counter. The QEMU observer gate now requires
the process object and `start_time_ticks` from that real client path.
Exact revision `316f74518335fe4df32ba0bef225547b1c8c6d01` passed host and all
43 named native workflow steps after composition with the energy extension and
the client cap correction. The downloaded native artifact matched the
published SHA256, both build manifests identify the clean tested revision, and
the guest log contains `SYNVEIL_OBSERVE_OK` after requiring the process object
and stable start-time identity. See
[retained evidence](validation/native-316f745/README.md).
Lifecycle events, interval deltas, persistent history and process control remain
open.

## Powercap energy observation

The observer reads at most 256 Linux powercap zones from a bounded traversal of
control-type and nested zone directories, sorts hierarchical numeric zone IDs,
and reports bounded names, cumulative microjoules and maximum counter range.
Canonical directory targets are deduplicated and traversal is capped at 4,096
entries. Missing and unreadable zones are explicit; malformed labels, counters
or out-of-range energy invalidate only the optional energy object. Host fixtures
cover nested hierarchy, ordering, partial availability, absence and malformed
range relationships. The QEMU observer gate requires the energy object even
when the virtual machine exposes no powercap zones. Exact revision
`bca81e5866c6797efcd029796f506737222e0a01` passed host and all 45 native
workflow steps. The downloaded native artifact matched the published SHA256,
both build manifests identify the clean tested revision, and the guest log
contains `SYNVEIL_OBSERVE_OK` after requiring the energy object. See
[retained evidence](validation/native-bca81e5/README.md). QEMU exercised the
unavailable-zone result; positive and partial readings remain host-fixture
evidence. Instantaneous power, power-limit control, and physical-hardware
validation remain open.
