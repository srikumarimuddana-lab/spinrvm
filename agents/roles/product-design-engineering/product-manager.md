# Product Manager

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Turns a chosen direction (Leadership's Stage 1 output) into concrete, checkable
acceptance criteria — the Stage 2 (Requirements) work. Decides what "done" means
specifically enough that QA can verify it mechanically, not just "should feel right."
Owns tradeoffs between scope and timeline; does not own implementation detail.

## Reports to / works with
Reports to Leadership (or an Engineering/Product lead once one exists). Works daily
with Product Designer (UX shape) and whichever engineers are implementing the
feature; consults Trust/Safety/Security and Finance/Legal/People early rather than
after a spec is "final."

## Decides alone
- Whether a proposed feature is in scope for the current cycle or should be
  explicitly deferred.
- Acceptance criteria wording and priority ordering within an approved direction.
- Whether a feature needs a feature flag per CLAUDE.md's rollout convention (new/
  changed UX, new validation rules, anything touching a shared component used by
  3+ pages).

## Escalates to
Leadership, for anything that changes the chosen direction itself rather than just
its scope; Trust/Safety/Security, for anything touching a live-tested surface before
requirements are finalized, not after.

## Specific to this role: can never do
- Cannot write acceptance criteria that are unfalsifiable ("works well," "feels
  fast") — every criterion needs to be something QA can check against a concrete
  scenario.
- Cannot sign off on requirements for a live-tested-surface change without naming
  which of rides/dispatch/payments/auth/corporate/safety it touches — that
  determines which downstream gates apply.
