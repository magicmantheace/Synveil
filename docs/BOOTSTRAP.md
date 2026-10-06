# Synveil Bootstrap Plan

## Objective

Produce a reproducible x86_64 UEFI-capable Synveil image built from pinned source inputs, boot it automatically in QEMU, and evolve that image into the first complete Observe -> Plan -> Policy -> Execute -> Evaluate loop.

This is a bootstrap, not the final distribution architecture.

## Host assumptions

The first builder may run on an existing Linux development host. The resulting target root filesystem must not depend on the host distribution's runtime files.

Host tools are scaffolding. They are not Synveil.

## Stage 0 — repository/build skeleton

Create:

```text
build/
  manifests/
  scripts/
  toolchain/
packages/
src/
tests/
tools/
```

Add one top-level build entry point.

Desired developer experience:

```sh
./build.sh toolchain
./build.sh rootfs
./build.sh kernel
./build.sh image
./build.sh qemu
./build.sh smoke
```

The implementation may evolve, but one discoverable entry point should remain.

## Stage 1 — pinned source manifest

Track exact versions and cryptographic hashes for at least:

- Linux kernel,
- binutils,
- GCC or chosen bootstrap compiler,
- glibc,
- BusyBox bootstrap userspace,
- UEFI/bootloader dependency selected during implementation.

Never silently download "latest."

The build should cache downloaded source archives separately from generated artifacts.

## Stage 2 — cross/bootstrap toolchain

Construct a toolchain capable of producing target binaries without linking target userspace against host distribution libraries.

Initial architecture: `x86_64-synveil-linux-gnu` or an equivalent explicit target identity if upstream tooling constraints require a conventional triplet.

Expected conceptual sequence:

1. binutils,
2. Linux API headers,
3. bootstrap compiler,
4. glibc headers/start files,
5. compiler runtime/libgcc,
6. glibc,
7. final compiler/runtime pieces.

Exact implementation should follow the requirements of the pinned toolchain versions rather than hard-code folklore.

## Stage 3 — minimal root filesystem

Build a root filesystem containing only what is needed to boot and debug:

```text
/bin
/dev
/etc
/proc
/root
/run
/sbin
/sys
/tmp
/usr
/var
```

Use BusyBox initially for essential bring-up tools.

Create an explicit `/init` that:

1. mounts proc/sys/dev/run,
2. initializes required device/runtime state,
3. starts a console,
4. reports build identity,
5. fails into a recovery shell when startup fails.

## Stage 4 — kernel

Build an upstream Linux kernel from a committed configuration.

First configuration should prioritize QEMU virtio hardware and developer observability over physical hardware breadth.

Required early capabilities should include:

- initramfs support,
- devtmpfs,
- proc/sysfs,
- virtio block/network/console,
- cgroups needed by later control-plane work,
- namespaces required by future isolation,
- pressure stall information if supported by the chosen kernel,
- Unix domain sockets.

Commit the effective kernel config or a reproducible fragment strategy.

## Stage 5 — bootable image

Select and pin a UEFI-compatible boot path.

First success criterion:

> QEMU reaches the Synveil console from a generated image without using a host distribution root filesystem.

Automate the QEMU invocation. Developers should not have to remember a long command line.

## Stage 6 — Synveil native process

Introduce the first Rust binary, initially `veil-core`, into the image.

First behavior:

- start reliably,
- expose a Unix socket,
- return version/build information,
- return basic health state,
- shut down cleanly.

Add `synctl status`.

## Stage 7 — Observer

Add a small structured telemetry collector for:

- CPU utilization,
- memory availability/pressure,
- load/process count,
- uptime,
- selected kernel/system identity.

Expose telemetry through a stable schema.

No LLM is required yet.

## Stage 8 — typed action + policy

Implement the first harmless reversible action.

Recommended first action:

**SetProcessPriority** within a tightly bounded range for a test process.

Why this action:

- it proves privilege separation,
- it can be scoped,
- it is measurable,
- it can be reversed,
- failure is unlikely to damage the image.

Pipeline:

```text
request
 -> schema validation
 -> deterministic policy
 -> baseline capture
 -> execution
 -> audit record
 -> result measurement
 -> rollback/expiry
```

## Stage 9 — Evaluator

Define a synthetic test workload and show that Synveil can measure a before/after outcome.

The purpose is not to demonstrate a dramatic performance win. The purpose is to prove the experimental machinery and retain/revert decision path.

## Stage 10 — Planner

Only after the deterministic loop works, add local model inference.

The first planner should have a deliberately tiny action vocabulary. Model output must validate against the same action schema used by non-AI tests.

A planner failure should result in a rejected plan, not an ad-hoc fallback shell command.

## Stage 11 — first complete autonomous experiment

Target demonstration:

1. observer detects a known synthetic workload,
2. planner identifies an allowed optimization,
3. policy authorizes it,
4. executor applies it,
5. evaluator measures the outcome,
6. action expires or is reverted,
7. `synctl history` explains what happened.

This is the first point at which Synveil proves its defining architectural idea.

## Build outputs

Long-term build artifacts should converge on:

```text
out/
  synveil-x86_64.iso
  synveil-x86_64.img
  synveil-initramfs.cpio.zst
  synveil-rootfs.tar.zst
  manifests/
  logs/
```

The exact formats may change during bootstrap.

## Reproducibility target

Early builds need not be bit-for-bit reproducible immediately, but sources, versions, inputs, configuration, and commands must be recorded from the beginning so reproducibility can be tightened rather than retrofitted.

## What not to build yet

Do not start with:

- a desktop shell,
- a graphical AI assistant,
- a broad package repository,
- a custom libc,
- a large model runtime,
- a custom kernel,
- production secure boot,
- dozens of optimization actions.

First prove that Synveil can build itself, boot, observe, make one authorized reversible change, evaluate it, and recover.
