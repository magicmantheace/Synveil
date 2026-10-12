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

## AI-related validation

No AI planner result is accepted as validation of deterministic system behavior.

For example, a planner saying that an action "would be safe" does not validate policy enforcement. Tests must request the action and demonstrate the deterministic policy result.

Likewise, explanations are validated against transaction evidence rather than judged solely by natural-language plausibility.

## Roadmap updates

Roadmap checkboxes should normally be updated in the same commit as, or after, the evidence that justifies completion.

If an implementation exists but validation is blocked by infrastructure, leave the item unchecked and document the blocker instead of overstating project maturity.

## Audit format validation

The [audit v1 contract](AUDIT.md) has unit coverage for schema/build/process
identity, ordering, unavailable time, and escaped field content. Its host
integration test requires the actual core to answer status and emit valid
startup, socket, request, and shutdown records. This validates the format and
emitter; it does not establish persistent storage or a durable journal.

The [fc78195 result](validation/supervision-fc78195/README.md) leaves supervision,
protocol, and absent-core guest cases pending. Independent guest cases must not
be skipped merely because a previous guest test failed.
