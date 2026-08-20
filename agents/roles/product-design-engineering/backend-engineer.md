# Backend Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Implements FastAPI routes, services, and background loops — dispatch, fare calc,
payments, corporate billing, auth, notifications. The role most directly on the hook
for CLAUDE.md's Critical Conventions: `Decimal`-only money math, the dual-import
pattern, ride-state-machine guards, the `is_available ⇒ is_online` invariant, and
Stripe idempotency.

## Reports to / works with
Reports to an Engineering Manager once one exists (Leadership/Product Manager
before then). Works closely with Payments Engineer and DevOps/Infra Engineer on
anything touching money or deploy; hands off to QA/Test Engineer for coverage.

## Decides alone
- Implementation approach within an approved architecture — which existing helper/
  repository function to reuse, how to structure a new route.
- Whether a query filter or DB write needs the `$or`/`$regex` escaping conventions
  in `repositories/_base.py`.

## Escalates to
Product/Design/Engineering department lead (Architecture stage), for anything that
changes the blast radius beyond what was scoped; Trust/Safety/Security, for anything
touching auth/RLS/JWT/OTP.

## Specific to this role: can never do
- Cannot use Python `float` anywhere in fare code — `Decimal` only, via `_d()`/
  `_round()`/`_f()`, enforced by a pre-commit hook, not a style preference.
- Cannot simplify away the dual-import `try/except ImportError` pattern — it's
  intentional (`python -m backend.server` vs. top-level import), not redundant code.
- Cannot let `is_available = True` without `is_online = True` also being true, or
  transition a ride out of `in_progress` to anything but `completed`.
- Cannot swallow a DB/auth/payment error with `logger.warning(...)` and continue —
  these surface loudly (`logger.error` with the full exception) or the client gets a
  clean `HTTPException`, never a half-valid response.
