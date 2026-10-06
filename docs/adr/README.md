# Architecture Decision Records

Synveil uses Architecture Decision Records (ADRs) for decisions that materially constrain future implementation.

Use an ADR when a decision:

- selects a foundational dependency or technology,
- establishes a protocol or persistent format,
- changes a security/trust boundary,
- changes a previously accepted architectural direction,
- would be expensive or disruptive to reverse later.

ADRs live in this directory and use four-digit sequence numbers:

```text
0001-example-decision.md
0002-another-decision.md
```

Use `0000-template.md` as the starting point.

## Status values

- **Proposed** — under consideration.
- **Accepted** — current architectural direction.
- **Superseded** — replaced by a later ADR.
- **Rejected** — considered and deliberately not chosen.

When superseding an ADR, link both directions.

Do not create ADRs for routine implementation details that are easily reversible.
