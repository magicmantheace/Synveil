# ADR 0001: License Synveil Core Under MPL-2.0

- Status: Accepted
- Date: 2026-10-06
- Supersedes: none
- Superseded by: none

## Context

Synveil is intended to be both an open operating-system core and a platform that can support proprietary applications, hardware integrations, and commercial adoption.

A permissive license such as MIT or Apache-2.0 would maximize reuse but would allow distributed modifications to Synveil core files to remain closed. Strong project-wide copyleft such as GPL-3.0 would provide stronger reciprocity but could create unnecessary friction for integrations that merely combine with Synveil.

## Decision

Synveil-authored operating-system and control-plane code will use **Mozilla Public License 2.0 (MPL-2.0)** by default.

Future SDKs, client libraries, protocol bindings, and integration libraries may use **Apache License 2.0** when broad embedding and reuse are the primary goal. Such components must be explicitly scoped and licensed; Apache-2.0 is not an implicit repository-wide alternative.

Third-party components retain their upstream licenses.

## Rationale

MPL-2.0 provides file-level copyleft. Distributed modifications to MPL-covered source remain open while separate files can be combined into larger works under different terms.

That fits Synveil's architecture:

- the operating-system control plane should remain inspectable and improvable as open infrastructure;
- applications and hardware integrations should not be forced into the core project's license solely because they interoperate with Synveil;
- the project benefits from the patent grants included in MPL-2.0.

Apache-2.0 is a better fit for selected developer-facing libraries whose primary purpose is widespread integration.

## Alternatives considered

### Apache-2.0 repository-wide

Strong adoption characteristics and explicit patent terms, but distributed modifications to Synveil's own core could remain closed.

### GPL-3.0 repository-wide

Strong reciprocity, but broader copyleft than needed for the desired application/integration ecosystem.

### MIT/BSD

Simple and permissive, but weaker patent language and no reciprocity requirement for modifications.

## Consequences

### Positive

- Synveil core modifications distributed in source form remain under MPL-2.0.
- Separate proprietary applications can coexist with the platform.
- Patent grants are explicit.
- The license is established, standardized, and OSI-recognized.
- SDKs can later use Apache-2.0 where permissive reuse is strategically useful.

### Negative / tradeoffs

- The repository may eventually contain multiple licenses and therefore requires clear per-component metadata.
- Contributors and distributors must understand file-level licensing boundaries.
- Third-party license compatibility must be reviewed when code is copied into MPL-covered files.

## Security implications

No direct runtime security boundary changes.

Keeping distributed modifications to core control-plane files available under MPL can improve auditability, but licensing is not a substitute for technical security controls.

## Reversibility

Relicensing existing contributions later may require permission from all relevant copyright holders. This decision therefore becomes increasingly difficult to reverse as outside contributions accumulate.

New, clearly separated components can still adopt compatible licenses where appropriate.

## Validation

- Root `LICENSE` contains MPL-2.0.
- Licensing policy is documented.
- New Synveil-authored core source files use `SPDX-License-Identifier: MPL-2.0`.
- Apache-2.0 components, if introduced, declare their scope explicitly.
