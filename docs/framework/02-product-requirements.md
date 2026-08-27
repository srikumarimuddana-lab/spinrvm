# Pillar 2 — Product & Requirements

> What Spinr is building, why it can beat incumbents, and how a product idea
> becomes a checkable requirement. Competitive claims here are strategy, not
> measured fact; the measurable side lives in the KPI table and Pillar 7's
> scorecard.

## Competitive thesis — how a Saskatchewan startup beats Uber and Lyft

Spinr does not out-scale incumbents; it out-positions them on five axes they
structurally cannot follow:

1. **0% driver commission.** Driver keeps 100% of the fare. Uber/Lyft take
   ~25–40% per trip; their entire P&L depends on that take rate, so they
   cannot match this without abandoning their business model. Monetization is
   SaaS corporate accounts, premium rider features, and partner referrals —
   never per-trip cuts. This is the single strongest driver-acquisition and
   driver-retention lever in the market.
2. **Bounded, honest pricing.** Hard 2.5× auto-surge cap (provincially
   framed, tighter than any regulation requires), surge always visible before
   booking, never retroactive, never on corporate rides, and every receipt
   line maps to a disclosed item. Incumbents' unbounded surge is their most
   hated feature; Spinr's cap is a marketing claim that is also a code
   invariant (`SURGE_CAP` clamped at every fare-calc call site).
3. **Regulatory-native, not regulation-fighting.** PIPEDA, the Saskatchewan
   Transportation Act, SGI insurance periods, WCAG 2.1 AA, WAV support, and
   T4A tax artifacts are design inputs, not compliance retrofits. Incumbents
   enter markets and litigate; Spinr enters aligned. In a province-first
   strategy this converts regulators and municipalities from adversaries to
   references.
4. **Community-scale trust.** Saskatchewan-first means driver supply, rider
   demand, and brand live in communities incumbents treat as rounding errors.
   Safety surface (SOS, check-in loop, emergency contacts) and the
   no-data-harvesting stance (no ad SDKs, no behavioral retargeting) are
   trust features incumbents cannot credibly claim.
5. **Corporate B2B as the profit engine.** The corporate layer (accounts,
   memberships, allowances, master-wallet fallback, KYB) sits on top of the
   consumer product without touching ride/driver logic. SMB and municipal
   accounts in underserved markets are the revenue that funds the 0%
   consumer promise. See `docs/CORPORATE_B2B.md` and
   `docs/CORPORATE_B2B_GTM.md`.

The moat is credibility: every one of these claims is enforced by a code
invariant, a test, or an audit row — so the marketing can never drift from
the product.

## Product guardrails — the "is NOT" list is a requirement filter

`CLAUDE.md` → "What Spinr Is NOT" is the first gate every feature idea passes.
A proposal that violates it is flagged, not implemented, regardless of
revenue upside: no per-trip commission, no unbounded/hidden surge, no
driver-control patterns (contractor classification risk), no data harvesting,
no hidden fees, no country-agnostic genericization, no 911 replacement.
Guardrails are what stop "make it better than Uber" from decaying into
"make it into Uber."

## Requirement standards

Every requirement, before implementation starts, must state:

| Field | Test of adequacy |
|---|---|
| Problem statement | One sentence; a stranger could tell whether it's solved |
| Affected persona(s) | rider / driver / corporate-admin / internal-admin |
| Surface(s) | backend, rider-app, driver-app, admin-dashboard, shared |
| Acceptance criteria | Each one checkable by a named test, command, or manual step |
| Regulated-surface flags | touches rides? money? auth? corporate? safety? PII? |
| Guardrail check | passes the "is NOT" filter; names the closest guardrail |
| KPI linkage | which KPI/SLA row this moves or risks |

Rules of engagement (from `CLAUDE.md`, restated as requirement law):
- Ambiguity is named and asked about, never silently resolved — with extra
  force on the ride state machine, money paths, and insurance periods.
- Vague asks are converted to checkable ones ("fix the bug" → "a test that
  reproduces it, then passes").
- No speculative features or unasked-for configurability; the minimum change
  that solves the stated problem.

## Domain contracts — requirements inherit, they don't restate

Deep domain rules live in versioned context docs and bind every requirement
that enters their area:

- `.claude/context/domain-dispatch.md` — matching, offer timeout, batch offers
- `.claude/context/domain-payments.md` — fare calc, surge, Stripe, corporate billing
- `.claude/context/domain-corporate.md` — account/membership/policy lifecycle
- `.claude/context/domain-safety.md` — SOS, insurance periods, emergency flows
- `.claude/context/regulatory-sk.md` — Saskatchewan Transportation Act obligations
- `.claude/context/brand-spinr.md` — brand system for customer-facing work

A requirement that contradicts its domain contract is wrong or the contract
needs a recorded amendment (ADR + doc update) — never a silent divergence.

## Personas and their non-negotiables

| Persona | Non-negotiables every feature must preserve |
|---|---|
| Rider | Fare known before booking; surge visible pre-booking; one active ride; SOS reachable in-ride; PIPEDA rights (export/correct/delete) |
| Driver | 100% of fare; insurance period correctness (a liability, not a UX detail); contractor autonomy (no forced shifts/uniforms); document-expiry blocks going online, never silent lapse |
| Corporate admin | Spend visibility scoped to their company only; allowance caps enforced atomically; exports match receipts/tax rules |
| Internal admin | Module-gated RBAC (`require_module`); every action audit-logged; JWTs trusted only for admin tokens |

## Prioritization

- `ACTION_ITEMS.md` is the single prioritized backlog (A/B/C bands); work is
  picked from open `[ ]` items, and every unfixable finding lands there.
- `docs/PRODUCTION_READINESS.md` holds the full context behind backlog items;
  `.claude/context/sprint-current.md` holds the active sprint slice.
- KPI/SLA breaches outrank feature work: a product below its match-rate or
  latency targets is losing the only competition that matters — see the KPI
  table in `CLAUDE.md` for targets and their below-target signals.
