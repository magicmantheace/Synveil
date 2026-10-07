# ADR 0004: Bootstrap Rust Userspace With the Built-In GNU Target and Synveil Sysroot

- Status: Proposed
- Date: 2026-10-06
- Supersedes: none
- Superseded by: none

## Context

Phase 2 intends to introduce Rust-based native Synveil services such as `veil-core` and `synctl`.

The GNU C toolchain uses the explicit target identity:

```text
x86_64-synveil-linux-gnu
```

Rust presents a different tradeoff.

Rust's custom target JSON interface is unstable, and building the Rust standard library for a custom target currently relies on Cargo's unstable `-Z build-std` path and a pinned nightly compiler. Creating a custom Rust target merely to encode the Synveil vendor field would not change the Linux/glibc ABI of the generated program.

At the same time, Synveil should not silently embed an arbitrary precompiled host/rustup standard library into its base OS without recording and controlling that provenance.

## Proposed decision

For initial Phase 2 bring-up:

1. use Rust's built-in `x86_64-unknown-linux-gnu` code-generation target;
2. use Synveil's `x86_64-synveil-linux-gnu-gcc` as the linker;
3. link against the Synveil target sysroot rather than host distribution libraries;
4. pin the exact Rust bootstrap toolchain used by the build;
5. use the matching pinned Rust standard-library source and rebuild target standard-library crates from source for Synveil;
6. treat the host Rust compiler/Cargo as bootstrap build tools, analogous to the host compiler used to construct Synveil's GNU cross toolchain;
7. record Rust compiler/source identity in the machine-readable build manifest.

This ADR remains **Proposed** until the approach is exercised successfully against the completed Phase 1 sysroot.

## Why not name the Rust target x86_64-synveil-linux-gnu immediately?

The executable ABI is Linux + glibc regardless of the vendor label.

Using a custom JSON target solely for branding would:

- depend on an unstable target-specification format;
- couple Synveil to compiler-version-specific JSON fields;
- add another compatibility surface;
- provide little runtime benefit.

Synveil identity remains explicit in:

- the C/C++ cross-tool prefix;
- the sysroot;
- OS release metadata;
- component build metadata;
- package metadata.

A native Rust target can be revisited if Synveil later needs code-generation behavior that differs from the built-in GNU/Linux target.

## Standard-library provenance

Simply using a precompiled Rust standard library is acceptable for an exploratory developer build, but it should not become the canonical source-built Synveil image path.

The intended canonical build should compile the target standard library from pinned source using the same pinned Rust compiler generation.

The precise implementation depends on the pinned Rust release because Cargo's standard-library build interface is not yet stable.

## Linker and sysroot

Conceptually, Cargo should be configured so target links flow through:

```text
x86_64-synveil-linux-gnu-gcc
```

That compiler already carries Synveil's target/sysroot configuration.

The build must include a link audit that rejects accidental dependencies on host-distribution library paths.

Crates containing C/C++ build scripts will require explicit target compiler environment configuration rather than being allowed to discover the host compiler implicitly.

## Panic/unwind runtime

The Phase 1 compiler is deliberately a minimal C bootstrap compiler.

Before accepting this ADR, Phase 2 bring-up must verify whether the selected Rust standard-library configuration requires shared GCC unwind/runtime libraries that Phase 1 does not yet provide.

If required, Synveil should extend the GNU toolchain after glibc exists rather than bypass the sysroot with host runtime libraries.

Using host `libgcc_s` to make the Rust build pass is not acceptable.

## Alternatives considered

### Custom Rust JSON target named x86_64-synveil-linux-gnu

Provides consistent naming but currently adds unstable target-specification coupling with little ABI benefit.

### Precompiled rustup std without rebuilding it

Simple and useful for experimentation, but weaker source provenance for binaries that become part of the base system.

### Build the entire Rust compiler from source immediately

Strongest bootstrap provenance, but unnecessarily expensive before Synveil has even launched its first native service. A staged bootstrap compiler plus source-built target standard library is a better initial tradeoff.

### Use musl for native Synveil services

Would make static deployment easier, but introduces a second libc ABI into a system whose general-purpose userspace decision is glibc. Avoid unless a concrete requirement later justifies it.

### Write Phase 2 core in C

Would avoid Rust bootstrapping temporarily but contradicts the accepted direction of using Rust for Synveil's privileged native control plane without solving the eventual Rust bootstrap problem.

## Consequences

### Positive

- Reuses a well-supported Rust GNU/Linux code-generation target.
- Keeps actual native linking inside the Synveil sysroot.
- Avoids custom-target instability for branding alone.
- Preserves the goal of source-built target runtime code.
- Lets the Rust compiler itself be treated as a stage-0 bootstrap tool initially.

### Negative / tradeoffs

- Canonical Phase 2 builds will initially depend on pinned nightly/build-std behavior unless that functionality stabilizes before implementation.
- Rust's displayed target triple will not contain the Synveil vendor name.
- The build must explicitly guard C build scripts and linker discovery against host contamination.
- Shared unwind/runtime requirements may force a post-glibc GNU toolchain stage before native Rust services can ship.

## Security implications

Compiler and standard-library provenance becomes part of Synveil's software supply chain.

The build must record exact Rust toolchain/source identity and must not allow host library search paths to satisfy missing target dependencies silently.

## Reversibility

High during early Phase 2.

Changing the Rust build target before public SDK/ABI guarantees exist primarily affects build configuration. It becomes more expensive once third-party Synveil-native Rust development is supported.

## Validation required before acceptance

- Phase 1 sysroot is complete and boot-tested.
- A pinned Rust toolchain can rebuild the required target standard-library crates.
- A minimal Rust program links through the Synveil GCC driver.
- ELF inspection shows the expected x86_64 GNU/Linux interpreter/runtime.
- No host-distribution library path satisfies target link dependencies.
- The program executes in the Synveil QEMU image.
- Panic/runtime behavior is explicitly tested.
- `veil-core` and `synctl` can be built by the same reproducible path.

## Upstream references

- Rust custom targets: https://doc.rust-lang.org/rustc/targets/custom.html
- Cargo unstable build-std documentation: https://doc.rust-lang.org/cargo/reference/unstable.html#build-std
- Cargo target linker configuration: https://doc.rust-lang.org/cargo/reference/config.html#target
