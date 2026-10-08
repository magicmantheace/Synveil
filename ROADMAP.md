# Synveil Roadmap

This roadmap is ordered by dependency. Phase numbers describe architectural maturity, not release versions.

## First user test install — basic desktop milestone

The project owner wants the first hands-on test install to include a basic
desktop. Development builds continue to be tested automatically in QEMU.
A minimal subset of Phases 11 and 12 moves forward once its dependencies are
ready; full AI optimization and package management are not prerequisites.

- [ ] Validate core-failure and absent-core recovery in guest tests
- [ ] Provide persistent writable storage that survives reboot
- [ ] Provide a documented installation path on a supported test target
- [ ] Add display, keyboard, mouse, and required firmware/driver support
- [ ] Establish a usable networking configuration path
- [ ] Select and build a minimal graphical session with terminal and launcher
- [ ] Provide basic settings for the supported display/input/network setup
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
- [ ] Establish service lifecycle/supervision
- [ ] Add structured logging
- [ ] Add durable audit record format
- [ ] Add recovery behavior when core services fail

**Exit:** Synveil native code starts on boot and exposes health/status without AI.

Native startup and versioned status are validated at
`79a41c291884c40c881a89a2fc031bd33e9034de` in the
[native image evidence record](docs/validation/native-image-79a41c2/README.md).
Core-crash recovery passed at `248775a` in the
[recovery evidence](docs/validation/core-crash-248775a/README.md).
Absent-core recovery, supervision, and durable audit work remain open.

## Phase 3 — Observer

**Goal:** give Synveil a structured view of its machine.

- [ ] CPU metrics
- [ ] memory/pressure metrics
- [ ] process lifecycle and resource metrics
- [ ] disk/I/O metrics
- [ ] network metrics
- [ ] thermal/power interface where available
- [ ] event normalization
- [ ] ephemeral telemetry storage
- [ ] historical aggregation
- [ ] `synctl observe`

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

## Phase 6 — Local intelligence

**Goal:** introduce AI only after the safe control path exists.

- [ ] Define inference service interface
- [ ] Select first local inference runtime
- [ ] Support lightweight resident intelligence
- [ ] Support larger on-demand reasoning model
- [ ] Implement structured planner output
- [ ] Reject invalid/unavailable actions safely
- [ ] Add planner simulation/dry-run mode
- [ ] Add model provenance/versioning to decisions

**Exit:** a local model can propose a valid plan but remains incapable of bypassing policy.

## Phase 7 — First autonomous loop

**Goal:** demonstrate the defining Synveil behavior end to end.

- [ ] Detect synthetic workload
- [ ] create optimization goal
- [ ] generate plan
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

- [ ] explicit preference store
- [ ] learned-hypothesis store
- [ ] provenance/confidence/expiry
- [ ] workload profiles
- [ ] user-defined optimization priorities
- [ ] conflicts and precedence
- [ ] forget/reset controls
- [ ] portable/exportable user policy
- [ ] learned-behavior review UI/API

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
