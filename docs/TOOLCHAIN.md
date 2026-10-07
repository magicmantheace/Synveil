# Synveil Toolchain Staging

Synveil's compiler toolchain is bootstrapped in stages. The purpose is to avoid depending on the host distribution's target runtime while also avoiding circular dependencies between GCC and glibc.

## Stage 0 — host tools

The development host supplies build-time programs such as:

- host C/C++ compiler,
- make,
- shell,
- Python,
- bison/flex,
- archive/compression utilities.

These tools build the cross toolchain itself. They are not copied into the Synveil target filesystem.

## Stage 1 — target binutils

Synveil builds binutils for:

```text
x86_64-synveil-linux-gnu
```

with an explicit Synveil sysroot.

This produces the target assembler, linker, object inspection tools, and related utilities needed by later stages.

## Stage 2 — bootstrap GCC

The first GCC is intentionally minimal:

- C language only,
- no target libc headers,
- no shared runtime,
- no target libstdc++,
- no threading runtime,
- no multilib.

Its purpose is to compile libc and the first target C programs, not to serve as Synveil's eventual developer compiler.

## Stage 3 — Linux UAPI headers

The pinned Linux source installs sanitized userspace API headers into the isolated Synveil sysroot.

glibc is configured against these headers, not the host's kernel headers.

## Stage 4 — glibc

glibc is then cross-built into the Synveil sysroot.

Because the bootstrap GCC intentionally has no target libstdc++, glibc is built with an empty make-time `CXX` variable. glibc's support build therefore selects its C-only bootstrap helper rather than attempting to link against a C++ runtime that cannot exist yet.

After installation, the bootstrap compiler must successfully link a target C executable against the new sysroot.

## Stage 5 — bootstrap userspace and kernel

The resulting environment can build:

- the temporary BusyBox bootstrap userspace,
- ordinary target C programs,
- the Linux kernel and EFI-stub boot image.

This is sufficient for Phase 1's boot objective.

## Future final compiler

A mature Synveil toolchain should add a second GCC construction after libc exists.

That compiler can enable, as required:

- C,
- C++,
- shared libgcc,
- libstdc++,
- POSIX threads,
- additional runtime libraries,
- developer-oriented tooling.

The final toolchain is deliberately not a prerequisite for proving Synveil's source-built boot path.

## Rust

Synveil-native control-plane services are intended to be written primarily in Rust.

Rust does not replace this bootstrap chain: target Rust programs still need a linker and, for the selected GNU userspace target, glibc/runtime integration.

Phase 2 will define the first supported Rust target/build path after Phase 1 has established the base system.

## Host contamination rule

A target binary must not silently link against libraries from the host distribution.

The target compiler is configured with an explicit sysroot, and generated target artifacts should be inspectable for their interpreter, architecture, and linked-library expectations.

Longer term, Synveil will make these checks automated and increasingly hermetic.
