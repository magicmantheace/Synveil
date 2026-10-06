# Synveil

**Linux that adapts to you.**

Synveil is an AI-native Linux operating system designed around a continuously active, local-first intelligence layer. Rather than bolting a chatbot onto a conventional desktop, Synveil treats machine intelligence as part of the operating system control plane.

The long-term goal is a system that learns how its user works, continuously observes system health and workload behavior, proposes or applies safe optimizations, measures the result, and keeps or rolls back changes according to explicit user policy.

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

> Build a reproducible x86_64 UEFI image from source, boot it in QEMU, reach a Synveil-controlled userspace, collect basic telemetry, and execute one policy-approved reversible optimization through the full control loop.

The bootstrap is documented in [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md).

## Repository layout

```text
/
├── AGENTS.md
├── README.md
├── ROADMAP.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BOOTSTRAP.md
│   ├── PRINCIPLES.md
│   └── SECURITY_MODEL.md
├── build/              # build-system implementation (future)
├── packages/           # package recipes/manifests (future)
├── src/                # Synveil-native userspace components (future)
├── tests/              # host/QEMU/integration tests (future)
└── tools/              # developer tooling (future)
```

## Project status

**Phase 1 — from-source boot.**

The bootstrap build system is being validated toward the first reproducible x86_64 UEFI/QEMU boot. The roadmap is maintained in [ROADMAP.md](ROADMAP.md).

## Licensing

Synveil-authored operating-system and control-plane code is licensed under the **Mozilla Public License 2.0 (MPL-2.0)** by default. Selected future SDKs and integration libraries may use **Apache-2.0** when explicitly scoped.

Third-party components retain their upstream licenses. See [docs/LICENSING.md](docs/LICENSING.md) and [LICENSE](LICENSE).
