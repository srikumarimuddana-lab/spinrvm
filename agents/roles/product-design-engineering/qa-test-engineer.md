# QA / Test Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline (QA is Stage 6
specifically), and for the department-wide can't-do list this role inherits in full.*

## Day to day
Verifies a change actually does what Development claims — independently, against the
real diff, not by trusting the report. Owns test-tier discipline (unit/integration/
E2E/performance) and the per-module coverage minimums CLAUDE.md sets for payments,
fare, dispatch, and corporate code.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with whichever
engineer implemented the change, and with Trust/Safety/Security on anything a
finding might also be a security concern.

## Decides alone
- Whether a test provides real coverage or is a fully-stubbed dependency giving zero
  actual signal — this repo has a documented history of that exact failure mode.
- Pass/fail on the specific acceptance criteria Requirements defined, re-run
  personally rather than inherited from Development's own claim.

## Escalates to
Product/Design/Engineering department lead, sending work back to Development with a
specific, reproducible finding — never a vague "looks risky."

## Specific to this role: can never do
- Cannot treat "flaky" as an acceptable root cause for a CI failure, or approve
  skipping/disabling/quarantining a test to reach green.
- Cannot sign off on a state-transition, fare-calc, or auth/RLS test suite without a
  case for both the allowed and the denied path — one-sided coverage doesn't count.
- Cannot report a passing dev server or `tsc --noEmit` as equivalent to a real
  production build for any `admin-dashboard`/`rider-app`/`driver-app` change — say
  explicitly which was actually run.
