# Synveil

**Linux that adapts to you.**

Synveil is an AI-native Linux operating system designed around a continuously active, local-first intelligence layer. Rather than bolting a chatbot onto a conventional desktop, Synveil treats machine intelligence as part of the operating system control plane.

The long-term goal is a system that learns how its user works, continuously observes system health and workload behavior, proposes or applies safe optimizations, measures the result, and keeps or rolls back changes according to explicit user policy.

AthenZ is the intended sole resident intelligence. It will draw on
specializations as needed and evolve through the owner's developmental AI
project. Synveil provides its operating-system home and deterministic authority
boundary. Integration is planned, not implemented; see
[ADR 0012](docs/adr/0012-athenz-resident-intelligence.md).

## Core idea

Synveil separates intelligence from authority.

The AI may observe, reason, predict, and plan. It does **not** receive an unrestricted shell or arbitrary root access. Every privileged change must be expressed as a typed system action and pass through deterministic policy, validation, execution, auditing, and rollback layers.

```text
Observe -> Understand -> Predict -> Plan
                         |
                         v
                    Policy Check
                         |
                         v
                      Snapshot
                         |
                         v
                       Act
                         |
                         v
                      Measure
                         |
                         v
                Keep / Modify / Rollback
```

The operating system must remain usable if every AI component is stopped.

## Initial architecture

- **Linux kernel** — hardware, process, memory, filesystem, networking, and driver foundation.
- **Synveil Core** — deterministic privileged control plane.
- **Observer** — structured telemetry and workload/event collection.
- **Memory** — durable user preferences, machine characteristics, observations, and learned behavior.
- **Planner** — local AI reasoning and optimization planning.
- **Policy Engine** — deterministic authorization and safety boundaries.
- **Executor** — performs typed privileged actions transactionally.
- **Evaluator** — measures effects and decides whether changes should be retained or reverted.
- **synctl** — human-facing command-line control and inspection tool.
- **Desktop integration** — later user interface for explanations, approvals, policy, and activity history.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed design.

## Non-negotiable properties

1. **AI is optional for boot and basic operation.**
2. **No unrestricted AI-to-root shell path.**
3. **Privileged actions are typed, policy-checked, auditable, and reversible where technically possible.**
4. **Local-first operation and local ownership of behavioral data.**
5. **User intent outranks automatic optimization.**
6. **Changes must be measurable; optimizations without evidence are not considered successful.**
7. **Safe failure beats clever failure.**
8. **The project builds its own system image rather than rebadging an existing distribution.**

## Bootstrap target

The first milestone is intentionally small:

> Build a pinned-source x86_64 UEFI image, boot it in QEMU, and reach a Synveil-controlled bootstrap userspace without borrowing another distribution's target root filesystem.

After that foundation is proven, the bootstrap sequence continues through native Synveil services, telemetry, one typed reversible action, evaluation, and finally the first bounded AI-planned optimization loop.

The bootstrap is documented in [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md).

## Repository layout

```text
/
├── AGENTS.md
├── README.md
├── ROADMAP.md
├── docs/               # architecture, bootstrap, security and validation docs
├── build/              # Phase 1 build system, configs and bootstrap rootfs
├── packages/           # package recipes/manifests (future)
├── src/                # Synveil-native userspace components (Phase 2+)
├── tests/              # source/image/QEMU validation
└── tools/              # source-lock and build-manifest tooling
```

## Project status

**Phase 1 and the authenticated graphical QEMU prototype are validated.**

The pinned-source bootstrap pipeline has built and booted an x86_64 UEFI image
in QEMU. See the [Phase 1 evidence record](docs/validation/phase1-4c40d41/README.md)
and the [native image status evidence](docs/validation/native-image-79a41c2/README.md).
Core supervision/recovery, read-only observation, QEMU graphics, UID 1000
demo terminal, opt-in DHCP and two-boot state storage passed together in
the [d875da39 full QEMU run](docs/validation/native-d875da39/README.md).
The authenticated framebuffer login, credential enrollment through the
developer serial fixture, non-root graphical session, and guest session
revocation also passed at [5cf3fb5](https://github.com/magicmantheace/Synveil/actions/runs/38297915844).
Graphical first-user password creation, rejection of mismatched confirmation,
and authentication across two independent QEMU boots passed at
[d4604f7](https://github.com/magicmantheace/Synveil/actions/runs/38319031076).
The full native build, graphical recovery/authentication, and independent
boots of the packaged authenticated image passed together at
[e8f371a](docs/validation/native-e8f371a/README.md). This includes graphical
first-user setup, persistent login, locked serial, nonroot session revocation
and preservation of the package's blank state disk. Audit inspection, persistent
journal checks and the downloadable authenticated preview also passed at
[262d275](docs/validation/native-262d275/README.md). Rollover, exhausted
audit capacity, incomplete-tail refusal and streamed archive checks passed at
[3fb0158](docs/validation/native-3fb0158/README.md). Offline audit export/verification,
tmpfs read-only recovery and the settings reboot check passed together at
[612bd80](docs/validation/native-612bd80/README.md). Unnamed snapshot publication
and tmpfs export failure/recovery passed at
[9f722d0](docs/validation/native-9f722d0/README.md). Disposable ext4 exhaustion and
recovered audit/export passed at [fcba067](docs/validation/native-fcba067/README.md).
Acknowledged audit and snapshot bytes survived abrupt emulator loss and reboot
at [00476e5](docs/validation/native-00476e5/README.md). Completed-rollover
reboot verification, strict digest evidence and both previously failing graphical
gates passed together at [1edc09e](docs/validation/native-1edc09e/README.md).
Archive-only layout recovery and the held VT chord passed in the full
[c97f554 run](docs/validation/native-c97f554/README.md).
Released-key graphical login, terminal return, production audit adapter and all
existing native gates passed together at
[4390a8c](docs/validation/native-4390a8c/README.md).
The later [aa17982 run](docs/validation/native-aa17982/README.md) also passed every native gate.
Corrected owner-controlled archive release and all 44 native steps passed at
[da43fc1](docs/validation/native-da43fc1/README.md).
This remains an experimental VM, **not** a production desktop, signed release,
owner-recovery system, or physical installer. The first hands-on test contract
is in [ROADMAP.md](ROADMAP.md) and [docs/FIRST_INSTALL.md](docs/FIRST_INSTALL.md).

## Licensing

Synveil-authored operating-system and control-plane code is licensed under the **Mozilla Public License 2.0 (MPL-2.0)** by default. Selected future SDKs and integration libraries may use **Apache-2.0** when explicitly scoped.

Third-party components retain their upstream licenses. See [docs/LICENSING.md](docs/LICENSING.md) and [LICENSE](LICENSE).


## Development references

- [Build Synveil](docs/BUILDING.md)
- [Bootstrap plan](docs/BOOTSTRAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY_MODEL.md)
- [Source provenance](docs/SOURCE_PROVENANCE.md)
- [Validation policy](docs/VALIDATION.md)
- [Native core bring-up](docs/NATIVE_CORE.md)
- [Roadmap](ROADMAP.md)
