# Synveil Roadmap

This roadmap is ordered by dependency. Phase numbers describe architectural maturity, not release versions.

## Resident intelligence direction

AthenZ is Synveil's sole resident intelligence, with specializations it can
load or consult when needed. Its evolution will use the owner's developmental
AI project. Synveil owns deterministic system authority, explicit system
policy, observations, transactions, audit and recovery; AthenZ owns reasoning,
cognitive memory, planning and specialization selection. Evolution never
grants additional system permissions. See
[ADR 0012](docs/adr/0012-athenz-resident-intelligence.md).

Inspect the current AthenZ implementation and identify the developmental AI
repository before defining executable integration schemas. This decision
does not claim existing interoperability or change the dependency order:
audit, observation, policy/actions and evaluation precede autonomous control.
The first basic desktop test remains independent of AthenZ integration.

## First user test install — basic desktop milestone

The project owner wants the first hands-on test install to include a basic
desktop. Development builds continue to be tested automatically in QEMU.
A minimal subset of Phases 11 and 12 moves forward once its dependencies are
ready; full AI optimization and package management are not prerequisites.

- [x] Validate core-failure and absent-core recovery in guest tests
  - Native QEMU recovery, restart exhaustion, absent-binary, protocol and observer
    checks passed at [810f66d](docs/validation/native-810f66d/README.md).
- [ ] Provide persistent writable storage that survives reboot
  - QEMU /var state disk survived independent write/reboot/read in the
    [partial guest evidence](docs/validation/persistence-2e1e54c/README.md).
    The demo user's protected bind-mounted home and preferences survived
    separate QEMU boots at [f642d6f](docs/validation/native-f642d6f/README.md).
    State-path hardening and missing/corrupt state-disk recovery passed
    [full QEMU validation at 8c4290f](docs/validation/native-8c4290f/README.md).
    Installed-system root storage, trusted mount identity and installer
    remain open.
- [ ] Provide a documented installation path on a supported test target
  - A guarded private QEMU boot/state bundle with SHA256 verification and
    an offline launcher is implemented; user-facing validation is still open.
- [x] Add display, keyboard, mouse, and required firmware/driver support
  - The specific QEMU virtio GPU/USB keyboard and tablet passed framebuffer,
    evdev input and VT guest checks in [d875da39](docs/validation/native-d875da39/README.md);
    physical hardware support is still open.
- [ ] Establish a usable networking configuration path
  - QEMU eth0 DHCP guest check passed in
    [d875da39](docs/validation/native-d875da39/README.md);
    network settings UI and real hardware remain open.
- [ ] Select and build a minimal graphical session with terminal and launcher
  - QEMU framebuffer menu/autostart and fixed UID 1000 terminal guest
    checks passed at [d875da39](docs/validation/native-d875da39/README.md).
    The bounded crash-restart supervisor and QMP-initiated demo-shell
    termination after display crash passed
    [guest validation at 8c4290f](docs/validation/native-8c4290f/README.md).
    Authenticated login, compositor and physical installation remain open.
- [ ] Provide basic settings for the supported display/input/network setup
  - High-contrast/pointer-size controls now save a private versioned
    record when the QEMU ext4 state volume is mounted; host tests cover
    atomic writing and invalid/symlink records. The QMP-driven GUI Save ->
    independent QEMU boot -> Load test passed at
    [f642d6f](docs/validation/native-f642d6f/README.md).
    Display modes and network settings remain open.
- [ ] Validate desktop startup, reboot persistence, and console fallback
- [ ] Publish a checksummed test image and installation/test instructions

The desktop remains usable without AI. These checks are an earlier test
milestone, not completion of the entire hardware or desktop phases. See
[the first-install contract](docs/FIRST_INSTALL.md).

## Phase 0 — Foundation

**Goal:** define what Synveil is before implementation creates accidental architecture.

- [x] Lock project name: Synveil
- [x] Define AI-native product identity
- [x] Define deterministic-plane / intelligence-plane separation
- [x] Define initial security boundary
- [x] Define bootstrap strategy
- [x] Establish repository rules
- [x] Select project license: MPL-2.0 core; explicitly scoped Apache-2.0 SDKs/libraries
- [x] Add build/release versioning convention
- [x] Add architecture-decision-record format

**Exit:** architecture and repository rules are sufficient to begin bootstrap implementation without relying on chat history.

## Phase 1 — From-source boot

**Goal:** generate a minimal Synveil system from pinned upstream source.

- [x] Add top-level build entry point
- [x] Create source manifest format
- [x] Pin Linux/binutils/compiler/glibc/BusyBox inputs and hashes
- [x] Build isolated target toolchain
- [x] Build minimal rootfs
- [x] Build QEMU-oriented kernel
- [x] Implement bootstrap `/init`
- [x] Generate bootable x86_64 image
- [x] Automate QEMU launch
- [x] Add serial-console boot smoke test
- [x] Produce machine-readable build manifest

**Exit:** one command can produce an image that boots to a Synveil console in QEMU.

Validated at `4c40d41b979090cc72b370a8dad540e7f0a6f735` in the
[Phase 1 evidence record](docs/validation/phase1-4c40d41/README.md).

## Phase 2 — Native deterministic core

**Goal:** replace shell-script orchestration of runtime behavior with Synveil-native services.

- [x] Initialize Rust workspace
- [x] Implement `veil-core`
- [x] Define versioned local protocol
- [x] Implement `synctl status`
- [x] Establish service lifecycle/supervision
- [x] Add structured logging
- [ ] Add durable audit record format
- [x] Add recovery behavior when core services fail

**Exit:** Synveil native code starts on boot and exposes health/status without AI.

Native startup and versioned status are validated at
`79a41c291884c40c881a89a2fc031bd33e9034de` in the
[native image evidence record](docs/validation/native-image-79a41c2/README.md).
Core-crash recovery passed at `248775a` in the
[recovery evidence](docs/validation/core-crash-248775a/README.md).
Absent-core recovery, supervision and structured logging passed in
[guest validation at 810f66d](docs/validation/native-810f66d/README.md).
Persistent journal appends now enforce a prototype 4 MiB per-file limit
with nonblocking stable-directory/per-file locks. One no-overwrite archive
slot is implemented; later capacity failures retain console events without
deleting history. Guest rollover passed at
[f973531](docs/validation/native-f973531/README.md). Extended archive retention,
disk-full policy and tamper evidence remain open. Offline read-only `veil-audit check` structural
inspection is implemented with a bounded schema/count report; it does not
authenticate records or prove completeness. Its exact-revision native guest
validation passed at [262d275](docs/validation/native-262d275/README.md).
Expanded guest gates for exhausted archive capacity and incomplete tails are
validated at [3fb0158](docs/validation/native-3fb0158/README.md).
The isolated tmpfs ENOSPC/recovered-space gate passed native guest validation at
[ebc9f4e](docs/validation/native-ebc9f4e/README.md). A read-only/read-write
remount recovery extension passed in the
[partial ffd0b0a run](docs/validation/native-ffd0b0a/README.md), whose graphical
settings gate failed. Full revalidation, including settings reboot persistence,
passed at [612bd80](docs/validation/native-612bd80/README.md).
Ext4 disk-full policy, power-loss and tamper evidence remain open.
Owner-controlled offline snapshot export and verification against a separately
retained digest passed native validation at
[612bd80](docs/validation/native-612bd80/README.md). Later unnamed-inode
publication and tmpfs export storage-failure gates passed at
[9f722d0](docs/validation/native-9f722d0/README.md). A disposable ext4 exhaustion
and recovered-export gate passed at
[fcba067](docs/validation/native-fcba067/README.md). Acknowledged journal/snapshot
verification after abrupt emulator stop passed in the full
[00476e5 run](docs/validation/native-00476e5/README.md).
A completed-rollover extension retains separate active/archive digests on the
host and verifies both originals and snapshots after reboot; this gate passed
in the [partial c8a759b run](docs/validation/native-c8a759b/README.md). That run
failed two graphical gates, so full revalidation remains open. Interrupted
rotation remains open.
Exports preserve source history and do not release the archive slot. Explicit retention release,
independent integrity anchoring and power-loss testing remain open.

## Phase 3 — Observer

**Goal:** give Synveil a structured view of its machine.

- [ ] CPU metrics
  - Read-only cumulative CPU counters and logical count implemented; live utilization sampling remains open.
- [ ] memory/pressure metrics
  - Read-only MemTotal/MemAvailable and bounded Linux PSI CPU, memory, and I/O
    stall snapshots implemented. Host/guest validation of PSI is required;
    pressure history, attribution, and long-term measurements remain open.
- [ ] process lifecycle and resource metrics
  - Read-only 1/5/15-minute Linux load averages and runnable/total thread
    counts are now parsed with strict schema checks. They are not CPU
    utilization or process inventory; full lifecycle metrics remain open.
    The expanded QEMU observer check is pending.
- [ ] disk/I/O metrics
  - Raw, per-device read/write counts, 512-byte sector counters, in-flight I/O,
    and cumulative busy time are exposed read-only by `synctl observe`.
    The strict parser rejects malformed counters and bounds device output.
    Partition and whole-device counters are never summed. Missing procfs
    diskstats is explicitly unavailable, not estimated or fabricated;
    interval-based throughput and persistent history remain open.
- [ ] network metrics
  - Strict, bounded `/proc/net/dev` counters now report per-interface RX/TX
    bytes, packets, errors, and drops through read-only `synctl observe`.
    Missing procfs data is explicitly unavailable; instantaneous bandwidth,
    connection inventory, network traffic inspection and history remain open.
- [ ] thermal/power interface where available
- [ ] event normalization
- [ ] ephemeral telemetry storage
- [ ] historical aggregation
- [ ] `synctl observe`
  - CPU/memory/uptime schema passed native QEMU at
    [810f66d](docs/validation/native-810f66d/README.md); additional metrics,
    utilization deltas and historical aggregation remain open.

**Exit:** the system can describe current workload and resource pressure through structured APIs without parsing human CLI output.

## Phase 4 — Action/policy engine

**Goal:** safely mutate the machine without AI involvement.

- [ ] Versioned action schema
- [ ] action registry
- [ ] deterministic policy engine
- [ ] authorization levels
- [ ] precondition validation
- [ ] transaction records
- [ ] first reversible action: bounded process-priority control
- [ ] expiry/scoping
- [ ] rollback
- [ ] `synctl actions`
- [ ] `synctl policy show`

**Exit:** a test client can request an action and the system safely allows/denies/executes/reverts it.

## Phase 5 — Evaluator

**Goal:** prove whether system mutations help.

- [ ] goal schema
- [ ] baseline measurement
- [ ] outcome measurement
- [ ] tradeoff/constraint representation
- [ ] keep/revert decision logic
- [ ] synthetic benchmark workload
- [ ] experiment history
- [ ] explanation sourced from transaction evidence

**Exit:** Synveil can run a deterministic optimization experiment and report evidence for its result.

## Phase 6 — AthenZ resident intelligence

**Goal:** integrate AthenZ as the sole intelligence after the safe control path exists.

- [ ] Inspect AthenZ and developmental AI interfaces; record exact repository revisions and compatibility requirements
- [ ] Define versioned local AthenZ observation, plan, result and lifecycle interfaces
- [ ] Integrate the AthenZ-managed inference/runtime path without a separate Synveil intelligence
- [ ] Support AthenZ resident service lifecycle, resource budgets and AI-disabled operation
- [ ] Support on-demand specializations under AthenZ identity and existing permissions
- [ ] Implement structured AthenZ planner output
- [ ] Reject invalid/unavailable actions safely
- [ ] Add planner simulation/dry-run mode
- [ ] Add model provenance/versioning to decisions
- [ ] Validate developmental AI candidate updates before activation; preserve a recoverable last-known-good version
- [ ] Test version mismatch, unavailable specializations, failed updates and AthenZ restart without loss of OS recovery

**Exit:** AthenZ can propose a valid local plan through the versioned interface,
while specializations and evolved versions remain unable to bypass policy.
Boot, desktop and recovery work with AthenZ stopped or incompatible.

## Phase 7 — First autonomous loop

**Goal:** demonstrate Synveil's control loop with AthenZ end to end.

- [ ] Detect synthetic workload
- [ ] create optimization goal
- [ ] generate an AthenZ plan with intelligence/specialization version provenance
- [ ] authorize
- [ ] checkpoint/baseline
- [ ] apply
- [ ] measure
- [ ] keep/revert
- [ ] expire scoped tuning
- [ ] explain via `synctl history`

**Exit:** Observe -> Plan -> Policy -> Execute -> Evaluate works unattended for a bounded safe scenario.

## Phase 8 — Memory and personalization

**Goal:** adapt to a person rather than merely react to metrics.

AthenZ owns cognitive memory and learned personalization. Synveil retains
explicit system policy and authoritative configuration/transaction evidence.
The integration must define access, retention, export and deletion boundaries
without duplicating AthenZ's general memory system.

- [ ] explicit preference store
- [ ] learned-hypothesis store
- [ ] provenance/confidence/expiry
- [ ] workload profiles
- [ ] user-defined optimization priorities
- [ ] conflicts and precedence
- [ ] forget/reset controls
- [ ] portable/exportable user policy
- [ ] learned-behavior review UI/API
- [ ] Define AthenZ memory access and developmental learning data boundaries with explicit consent and provenance
- [ ] Preserve user policy across AthenZ evolution, specialization changes and intelligence rollback

**Exit:** repeat workloads are tuned differently according to explicit and learned user preferences without converting observations into unrestricted authority.

## Phase 9 — Broader optimization capabilities

**Goal:** expand the action vocabulary carefully.

Candidate areas:

- [ ] CPU governor/frequency policy
- [ ] process scheduling/affinity
- [ ] I/O priority
- [ ] cgroup resource allocation
- [ ] memory/cache behavior
- [ ] service lifecycle optimization
- [ ] network tuning
- [ ] GPU workload routing
- [ ] power/battery policy
- [ ] thermal/acoustic policy
- [ ] compilation/build optimization
- [ ] game/application profiles

Every action requires policy, measurement, rollback/expiry, and audit semantics before autonomous use.

## Phase 10 — Package and system generations

**Goal:** make Synveil maintainable as an actual distribution.

- [ ] package recipe format
- [ ] dependency graph/resolution
- [ ] binary artifact format
- [ ] signed repository metadata
- [ ] package transactions
- [ ] system generations/snapshots
- [ ] atomic upgrade/rollback strategy
- [ ] `synctl` package integration
- [ ] AI-assisted optimization of source-built packages only through controlled build policy

**Exit:** the base system can update and roll back without borrowing another distribution's package manager.

## Phase 11 — Hardware install and recovery

**Goal:** move beyond QEMU safely.

- [ ] installer/image deployment path
- [ ] hardware discovery
- [ ] broader kernel/firmware coverage
- [ ] networking setup
- [ ] boot recovery entry
- [ ] AI-disabled safe mode
- [ ] repair tooling
- [ ] disk encryption design
- [ ] signed/verified boot design

**Exit:** a supported physical x86_64 machine can install, boot, update, and recover Synveil.

## Phase 12 — Desktop/workstation experience

**Goal:** make AI-native behavior understandable and controllable to normal users.

- [ ] choose compositor/desktop strategy
- [ ] settings UI
- [ ] activity/decision history
- [ ] optimization explanations
- [ ] approval prompts
- [ ] autonomy modes
- [ ] workload/profile UI
- [ ] privacy/data controls
- [ ] model/resource controls

Potential autonomy modes:

- **Observe** — learn and explain only.
- **Suggest** — recommend actions.
- **Safe Auto** — automatically apply policy-approved low-risk reversible actions.
- **Autonomous** — broader automation within explicit policy boundaries.

## Phase 13 — Developer/application platform

**Goal:** allow applications to participate in Synveil without surrendering system control.

- [ ] application workload hints
- [ ] structured optimization intents
- [ ] application-specific metrics
- [ ] scoped capability requests
- [ ] developer SDK
- [ ] event subscriptions
- [ ] plugin isolation model
- [ ] compatibility policy

## Phase 14 — Multi-architecture and maturity

- [ ] ARM64 bootstrap
- [ ] reproducible builds
- [ ] deterministic release pipeline
- [ ] update channels
- [ ] signed releases
- [ ] extensive fuzzing of privileged protocols
- [ ] failure injection
- [ ] performance regression infrastructure
- [ ] long-running autonomous-system tests

## North-star demonstrations

These are product-level tests the architecture should eventually make possible:

1. **Developer workload:** detect a large build, temporarily optimize resources for compilation, measure build latency, restore prior state, and remember the user's stated performance/noise preference.
2. **Gaming workload:** detect gameplay, suppress permitted background contention, tune CPU/GPU/process policy, and restore the desktop profile after exit.
3. **Local AI workload:** reserve GPU/RAM resources for inference while respecting foreground responsiveness.
4. **Laptop workload:** adapt performance, battery, thermals, and acoustics based on explicit priorities and context.
5. **Regression recovery:** identify that a previous optimization degraded the target metric and automatically roll it back with a clear explanation.

### Current authenticated desktop implementation (guest proof outstanding)

A fixed UID1000 framebuffer dashboard with keyboard, optional QEMU absolute
pointer, Status/Storage/Network/Terminal/Settings pages, private preferences,
and fixed user-shell handoff is now connected to the experimental seat broker.
The root login and developer menu remain distinct. The new path has not yet
passed a native QEMU build/boot and does not satisfy the first-install
requirements until authenticated session/VT/input/persistence/recovery
evidence is recorded for the exact current commit.

### Experimental masked first-user setup (guest proof pending)

The authenticated preview has an isolated, keyboard-only two-stage first
password enrollment path using the existing one-shot Argon2id credential
utility. The persistent-state and no-developer-root guard are mandatory.
The new 'graphical-onboard' guest fixture boots a locked-serial image,
completes graphical setup, logs in, reboots with the same test state disk and
requires ordinary login. It remains an incomplete dependency until an
exact-revision native QEMU run and independent recovery design are validated.


### Authenticated QEMU bundle (physical installation remains open)

A successful locked-serial, two-boot first-user QEMU fixture now emits
a hash-bound validation receipt. The optional `auth-vm-bundle`
packager verifies that exact revision and the embedded authentication
profile, then creates a private offline QEMU image with a brand-new
state disk. The previous unauthenticated developer VM bundle remains
isolated. This is an experimental VM path and does not complete
the first-test physical installer, installed root, recovery, signing
or production desktop prerequisites.
