# ADR 0012: AthenZ as Synveil's Sole Resident Intelligence

- Status: Accepted
- Date: 2026-10-14
- Supersedes: none; refines the intelligence responsibilities in ARCHITECTURE.md
- Superseded by: none

## Context

The owner intends Synveil to be the operating-system home for AthenZ. AthenZ
will be the sole intelligence, drawing on specializations as needed, and will
evolve using the owner's developmental AI project. The previous roadmap left
the resident intelligence unspecified and could lead to duplicated planning,
memory and model-management systems.

## Decision

AthenZ owns cognitive memory, reasoning, planning, personalization and selection
of specializations. Synveil owns boot, hardware, service supervision, structured
observations, explicit system policy, deterministic validation/execution,
transactions, evaluation evidence, audit and independent recovery.

Specializations operate through AthenZ with existing permissions. They do not
become separate autonomous system authorities. Evolved intelligence versions
must remain subject to the same deterministic policy boundary.

Define a versioned local integration contract after inspecting the current
`magicmantheace/AthenZ` repository and identifying/inspecting the developmental
AI project. Protocol details, specialization formats and evolutionary update
criteria remain open. This decision does not assert that these projects
already interoperate or are production-ready.

## Rationale

One resident intelligence preserves coherent memory and responsibility while
specializations provide additional capabilities when needed. A versioned
boundary allows AthenZ to evolve without coupling core correctness to its
internal implementation.

## Alternatives considered

- Independent Synveil intelligence: would duplicate the owner's AthenZ work.
- Multiple independent resident agents: conflicts with the chosen single
  intelligence and complicates attribution and permissions.
- AthenZ inside the privileged core: would couple model behavior and evolution
  to trusted system authority and recovery.

## Consequences

### Positive

- Integration replaces duplicated intelligence implementation work.
- Specialization and evolution provenance can be attached to system plans.
- Synveil remains usable while AthenZ is absent, stopped or incompatible.

### Negative / tradeoffs

- Integration requires coordination across project versions and data schemas.
- Candidate evolution requires compatibility/regression evidence and rollback.
- Cognitive memory and authoritative system state need explicit access,
  retention, consent and deletion boundaries.

## Security implications

AthenZ and its specializations remain outside the deterministic trust base.
Learning does not grant authority. Updates cannot silently change human policy,
erase authoritative audit evidence or expose unrestricted privileged execution.
Resource budgets, typed capabilities and independent recovery remain required.

## Reversibility

Another intelligence would require a new owner decision and compatible
identity/memory migration. Keeping the system protocol versioned and the core
independent of AthenZ internals reduces that migration cost.

## Validation

Record exact Synveil/AthenZ/developmental AI revisions and specialization
identity in integration evidence. Test valid and denied typed plans,
incompatible versions, unavailable specializations, failed candidate activation,
last-known-good rollback and resource limits. Demonstrate boot, desktop and
recovery with AthenZ stopped. No integration roadmap item becomes complete
from this architectural decision alone.
