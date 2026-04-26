# /adr — Architecture Decision Record

Create a new ADR (Architecture Decision Record) under `docs/adr/`. ADRs capture *why* we chose a design, so future-us (and Claude) can answer "why did we do it this way?" without archaeology.

## Usage

```
/adr "Use Redis Streams instead of pub/sub for WS fan-out"
/adr                        # interactive — prompts for title
```

## What it does

1. Looks up the next ADR number by listing `docs/adr/NNNN-*.md` (zero-padded, 4 digits)
2. Generates the file with the template below, slug derived from title
3. Opens the file so the user can fill in sections — does **not** guess the decision content
4. Suggests adding a one-line reference in `CLAUDE.md` if the decision changes a global convention

## Template (written to `docs/adr/NNNN-slug.md`)

```
# ADR-NNNN: <Title>

- Status: Proposed | Accepted | Deprecated | Superseded by ADR-NNNN
- Date: YYYY-MM-DD
- Deciders: <names / roles>
- Domain: dispatch | payments | auth | safety | corporate | platform | infra
- Affects: <files, modules, or services impacted>

## Context

<What problem are we solving? What are the constraints? What did we try that
didn't work? Link incidents, tickets, or prior ADRs.>

## Decision

<The decision in 2-4 sentences. Imperative voice: "We will do X."
Include the key numbers (timeouts, limits, thresholds) here.>

## Consequences

### Positive
- <list>

### Negative / trade-offs
- <list>

### Neutral
- <list>

## Alternatives considered

### <Alt 1>
Why rejected: <one line>

### <Alt 2>
Why rejected: <one line>

## Rollout

- Migration path: <how existing code moves to the new approach>
- Feature flag: <yes/no, name if yes>
- Rollback plan: <how to undo if it regresses>

## Spinr-specific impact

- Money / payments: <any effect on fare calc, payouts, Stripe flows>
- Safety / insurance periods: <any effect on SOS, period logging>
- PIPEDA / retention: <any effect on data lifecycle>
- Regulatory (SK/SGI): <any effect on driver eligibility, trip log retention>
- Performance SLAs: <any effect on P95 targets>

## References

- Incident/ticket links
- Related ADRs
- External docs
```

## Conventions

- **One decision per ADR.** If you find yourself listing multiple decisions, split them.
- **Never edit an accepted ADR** — supersede it with a new ADR and mark the old one `Superseded by ADR-NNNN`
- **Numbering is append-only** — same rule as migrations
- Keep it short. A good ADR is readable in 3 minutes.

## When to write one

- Anything that changes a global convention (affects multiple services or future code)
- Anything where a reviewer is likely to ask "why didn't we just ...?"
- Anything with regulatory, security, or money implications
- Any new background loop, external dependency, or cross-service contract

## When NOT to write one

- Internal refactors with no API impact
- Bug fixes (postmortem is the right artifact)
- Style or formatting changes
- Choices fully covered by existing ADRs

## Do NOT

- Do not write the ADR *for* the user — you may draft the Context/Consequences sections based on conversation, but the Decision must come from them
- Do not commit an ADR with `Status: Proposed` without flagging that it still needs sign-off
- Do not backfill ADRs for decisions already shipped months ago, unless someone explicitly asks
