# Synveil Architecture

Status: **initial accepted architecture; implementation has not started**

## 1. System model

Synveil consists of two major planes:

### Deterministic system plane

Responsible for boot, process supervision, policy, privileged execution, state transitions, recovery, storage, networking, package integrity, and auditing.

It must not depend on probabilistic model output for correctness.

### Intelligence plane

Responsible for observation interpretation, preference modeling, prediction, planning, natural-language interaction, and optimization proposals.

Its output is treated as untrusted structured input by the deterministic plane.

```text
+-------------------------------------------------------------+
|                       Human / UI / CLI                      |
+-------------------------------+-----------------------------+
                                |
                                v
+-------------------------------------------------------------+
|                    Intelligence Plane                       |
|                                                             |
|  Observer -> Context -> Memory -> Planner -> Proposed Plan  |
+-----------------------------------------------+-------------+
                                                |
                                      typed action plan
                                                |
                                                v
+-------------------------------------------------------------+
|                    Deterministic Plane                       |
|                                                             |
| Policy -> Validator -> Transaction -> Executor -> Audit     |
|    ^                                      |                 |
|    |                                      v                 |
|    +------------- Evaluator <- Telemetry -------------------+
+-------------------------------------------------------------+
                                |
                                v
+-------------------------------------------------------------+
| Linux kernel / drivers / filesystems / hardware             |
+-------------------------------------------------------------+
```

## 2. Bootstrap platform

Initial target:

- architecture: **x86_64**
- firmware: **UEFI**
- virtualization target: **QEMU/KVM**
- kernel: upstream Linux, version pinned by the build manifest
- libc target for the eventual general-purpose system: **glibc**
- bootstrap userspace: minimal purpose-built root filesystem; BusyBox may be used only as an early bootstrap dependency
- primary native control-plane language: **Rust**
- build host: initially Linux; cross-build/reproducible builders come later

Why glibc: Synveil aims to become a practical general-purpose desktop/workstation OS. Broad compatibility with existing Linux applications matters more than minimizing libc size.

Why BusyBox only for bootstrap: it lets Phase 1 prove boot/image/recovery mechanics without making BusyBox the permanent identity or service architecture of Synveil.

## 3. Native components

Names are provisional implementation names but establish responsibility boundaries.

### veil-core

Privileged deterministic control service. Owns the system action API and coordinates policy, transactions, execution, and audit.

It is not an LLM host.

### veil-observer

Collects structured machine state and events. Initial inputs:

- CPU utilization, frequency, pressure, and thermal state
- memory utilization and pressure
- disk capacity and I/O pressure
- process lifecycle and resource use
- network utilization
- service state
- boot/session state

Later inputs may include GPU telemetry, battery, compositor activity, application semantics, and hardware-specific sensors.

### veil-memory

Stores durable structured knowledge:

- explicit user preferences,
- machine capabilities,
- workload profiles,
- observations,
- optimization experiments,
- outcomes,
- confidence and expiration.

Explicit preferences and learned hypotheses must be distinguishable.

### veil-planner

Consumes user goals, relevant memory, and structured observations. Produces a typed plan.

The planner cannot execute the plan directly.

### veil-policy

Deterministically answers whether an action is:

- denied,
- allowed automatically,
- allowed only within limits,
- requires user approval,
- requires an elevated/recovery workflow.

Policy evaluation must not call an LLM.

### veil-executor

Executes approved actions. Privileged operations should be implemented as narrow capabilities.

### veil-evaluator

Measures whether an applied change achieved its stated goal within the allowed tradeoff.

### veil-infer

Owns local model lifecycle and inference routing. This allows the rest of the OS to request reasoning without coupling core services to a specific model/runtime.

The always-resident path should be lightweight. Larger models should be loaded or scheduled when their reasoning ability is worth the resource cost.

### synctl

Command-line interface for humans and development tooling.

Target examples:

```sh
synctl status
synctl observe
synctl policy show
synctl plan explain <id>
synctl actions
synctl history
synctl optimize
synctl rollback <transaction>
synctl ai stop
synctl ai start
```

## 4. Action model

Every privileged mutation must be represented by a versioned action schema.

Conceptual example:

```json
{
  "schema": "synveil.action/v1",
  "kind": "SetCpuGovernor",
  "goal_id": "compile-123",
  "scope": {
    "cpus": "all"
  },
  "parameters": {
    "governor": "performance"
  },
  "lifetime": {
    "type": "workload",
    "until_process_group_exits": 4120
  }
}
```

An action definition specifies:

- valid inputs,
- privilege required,
- policy category,
- preconditions,
- conflicts,
- execution implementation,
- observable effects,
- rollback behavior,
- audit fields.

## 5. Authorization levels

Initial conceptual classes:

### L0 — Observe

Read-only system information. No state mutation.

### L1 — Ephemeral low-risk tuning

Automatically reversible changes scoped to a session or workload, such as permitted scheduling hints.

### L2 — Persistent system tuning

Changes that persist across sessions but have a well-defined rollback.

### L3 — Software/system mutation

Package changes, service enablement, boot configuration, driver changes, or other broader system modifications.

### L4 — Destructive or security-critical

Data deletion, credential/security policy changes, storage repartitioning, firewall/security boundary changes, recovery configuration, and similar operations.

Default policy should become progressively stricter from L0 to L4.

These levels are authorization categories, not model confidence levels.

## 6. Transaction model

An executor transaction should contain:

- unique ID,
- originating goal,
- planner/model identity if applicable,
- requested actions,
- policy decisions,
- baseline measurements,
- snapshot/checkpoint reference when available,
- execution timestamps,
- result,
- post-change measurements,
- evaluator decision,
- rollback reference,
- human approvals/overrides.

The transaction log is the basis for trustworthy explanations.

## 7. Telemetry architecture

Telemetry should use structured events rather than scraping command output.

Early implementation can use Linux interfaces such as procfs, sysfs, pressure stall information, netlink, cgroups, and explicit service APIs.

Longer term, eBPF may provide richer event streams where justified, but Synveil should not make eBPF a requirement for the first bootable system.

Telemetry storage should separate:

- high-frequency ephemeral metrics,
- summarized historical metrics,
- durable optimization evidence,
- user-facing activity history.

## 8. IPC

Initial IPC should favor local Unix domain sockets with a versioned structured protocol.

The protocol choice should remain simple enough to inspect and fuzz. Rust type definitions must not become the only protocol specification.

Before external compatibility matters, JSON over Unix sockets is acceptable for bring-up. A compact binary format can be adopted after schemas stabilize.

## 9. Service supervision and PID 1

Synveil eventually intends to own its service-management model, but writing a production PID 1 is not a Phase 1 prerequisite.

Bootstrap sequence:

1. minimal initramfs `/init`,
2. prove kernel/rootfs/console/recovery,
3. introduce a small Synveil supervisor,
4. harden lifecycle/reaping/shutdown semantics,
5. only then consider making the supervisor permanent PID 1.

This avoids letting an init-system project block validation of the AI-native control architecture.

## 10. Storage and rollback

System-level rollback is a core goal but not required for the first shell boot.

The design should eventually support filesystem snapshots or image generations so package and configuration changes can be reverted independently of AI memory.

AI memory is never the authoritative record of system configuration.

## 11. Package/build direction

Synveil will build its base system from pinned upstream sources rather than inherit a binary package base from Debian, Ubuntu, Arch, Fedora, or another distribution.

The project will need:

- source manifests,
- integrity hashes,
- build recipes,
- dependency graph,
- package metadata,
- rootfs/image composition,
- binary package format or generation model,
- signing/update metadata,
- reproducibility tracking.

This is intentionally staged after a minimal source-built boot path exists.

## 12. Recovery

At minimum, recovery must eventually provide:

- boot option with AI stack disabled,
- policy reset,
- inspectable transaction/audit history,
- rollback of the most recent system mutation when supported,
- access to console and networking,
- repair/rebuild of AI state without touching user files.

Recovery authority belongs to the human user.

## 13. Open design questions

The following are intentionally not locked yet:

- final bootloader choice,
- final package/archive format,
- final filesystem/snapshot strategy,
- permanent PID 1/service supervisor design,
- desktop environment/compositor strategy,
- local inference runtime and model families,
- long-term IPC encoding,
- secure boot/signing architecture,
- update channel and repository design,
- licensing.

These should be decided when their roadmap dependency approaches rather than prematurely.
