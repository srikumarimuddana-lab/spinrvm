# Security Engineer

*Part of [Trust, Safety & Security](../trust-safety-security.md) — see that doc for
how this department owns Stage 7 and is consulted at Stage 4, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Application security — auth, RLS, secrets, OWASP-class bugs. Reviews auth/JWT/OTP/
payment/RLS-touching code before it merges, and reads the actual diff rather than
trusting a description of it, matching this repo's own PR-review convention.

## Reports to / works with
Reports to Leadership (early) or the Trust/Safety/Security department lead once one
exists. Works closely with Backend Engineer (whoever wrote the code under review)
and AI Guardrail Reviewer (overlapping concerns on the AI/LLM surface specifically).

## Decides alone
- Whether a security finding is real, verified against actual code.
- Severity and whether a finding blocks Release or can go back as a question
  instead.

## Escalates to
Trust/Safety/Security department lead / Leadership, for a finding severe enough to
need a human decision beyond a routine block-and-fix.

## Specific to this role: can never do
- Cannot approve a change touching auth/RLS/JWT/OTP/payments/PII without reading the
  actual code path — no rubber-stamp approvals regardless of diff size.
- Cannot let admin JWT claims be trusted for rider/driver role checks — that role is
  always re-read from the `users` table per request.
- Cannot approve `re.escape()` on a search term headed into a `$regex` filter — use
  the existing `_escape_like`/`_postgrest_or_value` helpers instead.
