# Synveil Repository Rules

These rules apply to humans and coding agents working in this repository.

## Source of truth

Git is the source of truth.

Before changing architecture or implementation:

1. Read this file.
2. Read `README.md`.
3. Read `ROADMAP.md`.
4. Read the relevant documents under `docs/`.
5. For roadmap completion or validation claims, read `docs/VALIDATION.md`.
6. Inspect existing implementation and tests before changing behavior.

Do not silently replace an accepted architectural decision. If a decision must change, document why and update all affected design documents in the same work.

## Product definition

Synveil is an AI-native Linux operating system, not a conventional Linux distribution with a chatbot installed on top.

The operating system must remain functional without its AI components. AI reasoning and privileged system authority must remain separate.

## Architectural invariants

The following are hard constraints unless the project owner explicitly changes them:

- AI models do not receive unrestricted root shells.
- Privileged mutations use typed actions with explicit schemas.
- All privileged actions pass through deterministic policy enforcement.
- High-impact actions require stronger authorization than low-impact reversible tuning.
- Actions are logged with cause, parameters, result, and available rollback information.
- Reversible changes should be checkpointed before execution.
- The evaluator must compare observed results against the stated goal.
- Learned preferences are not equivalent to permanent authorization.
- User policy always overrides learned optimization behavior.
- The machine must boot and provide a recovery path when the AI stack is absent or broken.
- Cloud inference must never be required for core system operation.
- User behavioral data is local-first and must not leave the machine by default.
- No component may hide an optimization or configuration change from the user.

## Implementation preferences

### Languages

- **Rust** is preferred for new Synveil privileged daemons, services, policy code, IPC, and system tooling.
- **C** is appropriate where required by low-level bootstrap, libc/kernel interfaces, boot code, or dependencies.
- **Python/shell** may be used for build orchestration and developer tooling when they are not part of the trusted runtime control plane.
- Avoid introducing a language solely for convenience if the same job fits the existing stack.

### Dependencies

Prefer small, auditable dependencies. Every runtime dependency added to the trusted control plane increases the security surface.

Do not make a large framework a foundational dependency without documenting the tradeoff.

### Interfaces

Prefer structured IPC and explicit schemas over parsing human-readable command output.

Human CLI output is not a stable machine API.

### Configuration

Configuration should be declarative, versionable, and inspectable. Machine-generated configuration must identify what generated it and why.

## Security and AI safety

Treat model output as untrusted input.

A model can request an action; it cannot grant itself permission to perform that action.

Never implement an escape hatch where a planner can bypass policy by falling back to shell execution. Debug tooling with elevated access must remain clearly separate from the production AI execution path.

Secrets, credentials, tokens, private keys, and user content must not be placed into model context unless explicitly required by an authorized feature.

## Optimization rules

An optimization must define:

- the goal being optimized,
- the metric or observable used to judge it,
- the permitted cost/tradeoff,
- the intended lifetime of the change,
- whether and how it can be reversed.

Whenever practical, capture a baseline before mutation and compare it to the result afterward.

Do not call a change an optimization merely because it sounds plausible.

## Git discipline

- Keep commits focused and descriptive.
- Commit meaningful progress rather than accumulating a large uncommitted change.
- Do not rewrite shared history without explicit instruction.
- Do not delete user work just to make a test pass.
- Update documentation when behavior or architecture changes.
- Keep generated build artifacts out of Git unless they are intentionally versioned fixtures.

## Testing

Early development prioritizes fast feedback:

1. static checks / formatting,
2. unit tests,
3. targeted host integration tests,
4. short QEMU smoke boots,
5. longer system tests only when the change warrants them.

A bootable-image change should eventually have an automated QEMU smoke test.

Implementation existing in Git is not, by itself, validation. Roadmap items should only be checked complete when the evidence defined in `docs/VALIDATION.md` exists.

## Roadmap discipline

Work from the earliest incomplete dependency on the roadmap unless the user directs otherwise.

Do not build a desktop experience before the underlying control loop is demonstrably functional.

Do not prematurely build a generalized package ecosystem before the bootstrap image and native control plane can be reproduced reliably.

## Documentation

Architecture documents describe accepted direction, not brainstorming. Open questions should be explicitly marked as such.

When a decision becomes settled, record it in the relevant document rather than relying on chat history.
