# ADR 0003: Use x86_64-synveil-linux-gnu as the Initial GNU Target Triplet

- Status: Accepted
- Date: 2026-10-06
- Supersedes: none
- Superseded by: none

## Context

Synveil needs a stable target identity for its GNU bootstrap toolchain, sysroot, build metadata, package recipes, and future cross-compilation interfaces.

Using the host distribution's target prefix would blur the boundary between host and target. Using a generic target such as `x86_64-linux-gnu` would work at the ABI level but would make Synveil-produced cross tools difficult to distinguish from distribution-provided tools.

GNU configuration triplets allow a vendor field between architecture and operating-system identity.

## Decision

The initial x86_64 GNU userspace target is:

```text
x86_64-synveil-linux-gnu
```

Interpretation:

- architecture: `x86_64`
- vendor/project identity: `synveil`
- kernel: `linux`
- userspace ABI/libc family: `gnu`

Cross tools therefore use names such as:

```text
x86_64-synveil-linux-gnu-gcc
x86_64-synveil-linux-gnu-ld
x86_64-synveil-linux-gnu-readelf
```

The target sysroot is namespaced beneath the same target identity.

## Rationale

This target name:

- clearly separates target tools from host tools;
- preserves the conventional Linux GNU ABI identity;
- gives build logs and manifests an unambiguous Synveil target;
- leaves architecture-specific expansion straightforward, for example a future `aarch64-synveil-linux-gnu` target;
- has already been accepted by the pinned binutils/GCC/glibc bootstrap path far enough to begin the target libc build.

The vendor field is project identity, not a claim that Synveil changes the Linux syscall ABI or glibc ABI.

## Alternatives considered

### x86_64-linux-gnu

Maximally conventional, but collides conceptually and often operationally with host/distribution cross-toolchains.

### x86_64-pc-linux-gnu

Conventional historical vendor field, but loses Synveil identity and does not describe the project any better.

### x86_64-unknown-linux-gnu

Neutral and broadly supported, but similarly loses target provenance in compiler/tool names.

### A non-GNU target suffix

Not appropriate while the selected general-purpose userspace is glibc/GNU.

## Consequences

### Positive

- Tool names and build paths clearly identify Synveil.
- Target metadata can use one stable value.
- Host contamination is easier to spot in logs.
- Future architectures can follow a predictable convention.

### Negative / tradeoffs

- Some third-party build systems may have incomplete triplet parsing and require canonicalization or patches.
- Synveil must test package compatibility rather than assume every project accepts a custom vendor field.

## Security implications

The target name itself is not a security boundary. Clear target separation reduces the chance of accidentally invoking or packaging host tools, but technical sysroot and dependency checks remain necessary.

## Reversibility

Moderate early, expensive later.

Changing the target prefix after a package ecosystem, SDK, or published toolchain exists would require migration of compiler names, sysroot paths, package metadata, caches, and documentation.

## Validation

The Phase 1 toolchain must configure and build pinned binutils, GCC, and glibc using this target identity and successfully link a target program against the Synveil sysroot.
