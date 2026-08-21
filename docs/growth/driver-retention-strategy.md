# Driver Retention Strategy — Post-Launch

**Status:** strategy reference, not a runbook. Owner: driver-ops/growth
lead. Nothing here is code-verified or Spinr-specific data — there is no
Spinr retention history yet to measure against.

## 1. The uncomfortable finding, stated up front

Spinr's 0% commission is a real structural advantage, but NBER driver-churn
research indicates commission is **not** the dominant churn driver —
income *unpredictability*, vehicle operating costs, and support-response
failures are bigger levers in the literature. Removing commission helps,
but retention still has to be actively managed on those three axes, not
assumed solved by the pricing model.

## 2. Industry baseline (not Spinr numbers — context for what "good" looks like)

Gridwise's 2026 driver report: Uber retains ~33% of drivers by month 11,
Lyft ~25%. These are large-incumbent numbers with very different economics
(commission-based, much larger markets) — not directly comparable to a
Saskatoon-scale launch, but useful as a floor: if Spinr's early cohort
churn looks *worse* than these numbers even with 0% commission, that's a
signal something in support/onboarding/income-predictability is broken,
not that the model doesn't work.

## 3. Where the strongest evidenced opportunity is: support responsiveness

Uber's Greenlight Hub network (in-person driver support locations) has
reportedly closed ~40% of its locations. Reduced in-person support capacity
at an incumbent is exactly the kind of gap a smaller, more responsive
platform can differentiate on — cheaply, since Spinr doesn't need physical
hub locations to win here, just faster and more human support response
than what drivers report experiencing at incumbents.

Cross-reference: AALDEF's 2025 findings (also cited in
`docs/legal/driver-deactivation-appeals-policy.md`) — 70-76% no-notice
deactivations and 95% appeal failure at incumbents — are a specific,
severe version of the same "support responsiveness" gap. A driver who
trusts that Spinr's appeals process is real (see the appeals-policy doc's
different-reviewer mechanism) has a concrete reason to stay over an
incumbent, but only once the bracketed SLA numbers in that doc are filled
in with real, honored numbers — an unenforced promise is worse than no
promise.

## 4. Tiered retention program (draft structure — no numbers committed)

| Tier | Trigger | Mechanism (illustrative, not committed) |
|---|---|---|
| Tier 1 — first 30 days | New driver, first rides | Fast onboarding support response; proactive check-in after first 5 rides (catch early friction before it becomes a silent churn) |
| Tier 2 — 30-90 days | Driver has an established ride pattern | Milestone recognition (not necessarily cash — could be priority support, early access to features); income-predictability tooling (e.g. surfacing expected-earnings info, not just per-ride fares) |
| Tier 3 — 90+ days, high-volume | Driver is a top-quartile earner/reliability | Consider for driver-advisory input (product feedback loop) — a cheap retention lever incumbents rarely offer at this scale |

**Explicitly avoid:** anything that reads as a shift requirement, minimum
weekly ride quota, or mandatory availability window to qualify for a tier —
that crosses into control-of-work risk per CLAUDE.md and
`docs/legal/independent-contractor-agreement.md` §1.2. Tier qualification
should be based on activity *observed*, never activity *required*.

## 5. Income-predictability levers (the biggest NBER-cited factor)

- Transparent fare breakdown (already a CLAUDE.md receipt-transparency
  requirement — leverage it as a retention message, not just a compliance
  checkbox: drivers should be able to see exactly why they earned what they
  earned).
- Consider surfacing a "typical earnings for this time/area" signal to help
  drivers plan online-hours decisions — this is a product feature request,
  not something this doc can spec; flag to product if pursued.
- Any milestone bonus (see `docs/growth/driver-acquisition-strategy.md`
  §4) should have a payout timeline the driver can actually trust — tie
  this back to `docs/finance/stripe-payout-readiness.md` before promising
  bonus timing.

## 6. First 6 months — measurement plan

Since there's no Spinr retention data yet, the first job is establishing a
baseline, not hitting a target:

1. Track week-over-week active-driver retention from week 1 (the CLAUDE.md
   KPI table already has a ≥80% weekly-active-driver-retention target —
   use that as the north star, but expect the first cohort's numbers to be
   noisy at n=5-10 drivers).
2. Exit-interview (or exit-survey) every driver who goes fully inactive in
   the first 90 days — small enough cohort size to do this manually early
   on, and the qualitative signal will be more useful than the KPI number
   alone at this scale.
3. Feed findings back into this doc and `ACTION_ITEMS.md` — this is meant
   to be a living document, not a one-time plan.

## Sources (external research, not Spinr-verified)

NBER driver-churn studies (commission is not the dominant factor); Gridwise
2026 driver retention report (Uber/Lyft month-11 retention figures); Uber
Greenlight Hub closure reporting (~40% of locations); AALDEF 2025
driver-deactivation-appeals report (reused here — see
`docs/legal/driver-deactivation-appeals-policy.md` for the primary
citation). None of this is Spinr operational data.
