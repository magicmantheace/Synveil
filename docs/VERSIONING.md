# Synveil Versioning

Synveil uses semantic-style release versions for the operating system as a whole while allowing individual components and packages to carry their own upstream or component versions.

## OS release version

Format:

```text
MAJOR.MINOR.PATCH
```

Before the system is ready for general use, releases remain in the `0.x.y` range.

Interpretation:

- **MAJOR** — compatibility boundary for a mature release line.
- **MINOR** — new system capabilities, architecture milestones, or significant user-visible functionality.
- **PATCH** — compatible fixes, security updates, and small improvements.

This is a project convention, not a promise that every internal package shares the OS version.

## Development builds

Development artifacts should identify both the declared version and Git state.

Preferred human form:

```text
0.1.0-dev+g<short-sha>
```

A build manifest should additionally record:

- full Git commit SHA,
- source manifest revision,
- dirty/clean source state,
- target architecture,
- build timestamp or reproducible-build epoch,
- toolchain identity,
- kernel version/config identity.

Do not use timestamps as the sole version identity.

## Image identity

Generated images should eventually use predictable names such as:

```text
synveil-0.1.0-dev-g1a2b3c4-x86_64.img
synveil-0.1.0-dev-g1a2b3c4-x86_64.iso
```

## Package/component versions

Third-party packages retain their upstream versions plus Synveil package metadata/revision when needed.

Synveil-native components may initially move with the repository, but APIs and persistent schemas must be versioned independently of binary release versions.

Examples:

```text
synveil.action/v1
synveil.telemetry/v1
synveil.policy/v1
```

A system release number must never be used as a substitute for a protocol/schema compatibility version.
