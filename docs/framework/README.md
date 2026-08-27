# The Spinr Engineering Framework

> The operating system for building Spinr into the best ride-share product
> in the world — not by imitating Uber/Lyft at smaller scale, but by being
> **verifiably honest, regulatory-native, and driver-first** in ways
> incumbents structurally cannot copy. Every claim in these documents is
> grounded in this repository: a file, a gate, a test run, or a tracked
> backlog item. Where something is aspirational or broken, the framework
> says so by ID instead of implying coverage.

## The pillars

| # | Pillar | One-line contract |
|---|--------|-------------------|
| 1 | [Delivery lifecycle](01-delivery-lifecycle.md) | Discuss → brainstorm → requirements → design → develop → validate → test → align → deploy → operate, each stage producing an artifact the next stage checks |
| 2 | [Product & requirements](02-product-requirements.md) | The competitive thesis, the "is NOT" guardrail filter, and how an idea becomes a checkable requirement |
| 3 | [Architecture & platform](03-architecture-platform.md) | The topology and the eight invariant principles that keep it coherent |
| 4 | [Quality engineering](04-quality-engineering.md) | Test tiers, coverage ratchets, the 22-agent review fleet, and the anti-overclaim ledger |
| 5 | [Security, compliance & safety](05-security-compliance-safety.md) | Layered enforcement of security posture, PIPEDA, SK/SGI regulation, and the safety surface |
| 6 | [Operations & deployment](06-operations-deployment.md) | Ship dark, roll back by DNS, observe by numbers, run by runbook |
| 7 | [Scorecard & roadmap](07-scorecard.md) | Where we honestly stand per pillar and the shortest path to world-class |
| 8 | [Autonomous swarm protocol](08-swarm-protocol.md) | The continuous inspect → brainstorm → debate → implement → attack → verify loop, bound to the repo's real agents and invariants (`/spinr-swarm`) |
| 9 | [Enhancement charter](09-enhancement-charter.md) | The swarm's standing mission backlog: eight enhancement tracks dispositioned against current code, the swarm-usage risk register, and the charter operating rules (`/spinr-swarm charter <A-H>`) |

## How the framework binds to the repo

This is a governance layer over machinery that already exists — it indexes,
it does not duplicate:

- **Constitution**: `CLAUDE.md` (root) holds the enforceable conventions;
  `docs/PROJECT_BLUEPRINT.md` explains the six-layer AI-delivery stack
  (constitution → skills → subagents → hooks → CI → living docs).
- **Backlog & state**: `ACTION_ITEMS.md` (single prioritized backlog),
  `docs/PRODUCTION_READINESS.md` (launch verdict),
  `.claude/context/` (sprint + domain contracts).
- **Decisions**: `docs/adr/` (12 ADRs), `docs/audit/` decision write-ups.
- **Evidence**: `docs/change-log/` (750+ entries), `docs/audit/` (50+
  reports), `docs/ci/gate-health-2026-08.md` (inert-gate ledger).
- **Execution**: 19 slash-command skills, 22 reviewer agents, pre-commit
  hooks, 30 CI workflows, 53 runbooks.

## The three framework laws

Everything in the seven pillars reduces to these:

1. **Every claim is checkable.** A requirement names its verify step; a fix
   names its test; a "tests pass" names the command and count; a coverage
   number is measured, not estimated.
2. **Every gap is named.** Inert gates, unexercised drills, uncovered
   surfaces, and unverified boundaries are stated with a tracking ID —
   silence never implies coverage. This is the single biggest cultural
   advantage over incumbent-scale process theater.
3. **Every invariant lives in code.** Product promises (0% commission,
   2.5× surge cap, pre-booking surge visibility, append-only insurance
   audit) are enforced where they cannot drift: invariants, hooks, CI
   gates, and reviewer agents — with docs as the explanation, never the
   only enforcement.

## Using the framework

- **Starting any task** → Pillar 1 stage contracts; decompose per
  `CLAUDE.md`; regulated surfaces pull their domain contract doc.
- **Proposing a feature** → Pillar 2 requirement standard + guardrail
  filter first.
- **Changing structure** → Pillar 3 principles; write the ADR.
- **Before merge** → Pillar 4 test obligations + Pillar 1 stage-8 gates.
- **Touching money/auth/compliance/safety** → Pillar 5 protocol + the
  relevant auditor agents.
- **Shipping or on call** → Pillar 6 release discipline and runbooks.
- **Planning a cycle** → Pillar 7 scorecard tells you what actually moves
  the product toward world-class.

Maintenance: pillars change by PR like code, with the same review gates.
The scorecard (Pillar 7) is re-graded when its cited backlog items close;
its date stamp is the framework's freshness signal.
