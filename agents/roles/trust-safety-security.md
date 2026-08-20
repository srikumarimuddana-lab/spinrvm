# Role: Trust, Safety & Security

## What this role is for
The pipeline's built-in skeptic. Covers three related but distinct concerns under one
role: application security (auth, RLS, secrets, OWASP-class bugs), physical/product
safety (SOS, insurance-period classification, driver eligibility), and fraud/abuse
(referrals, promos, GPS plausibility). In the real org chart these are separate
people; in the pipeline they're one *stage* because every one of them can independently
block Release, and a change should clear all three before moving on, not just one.

## Where it sits in the pipeline
Owns **Stage 7 (Security)**, with authority to send work back to **Stage 5
(Development)**. Consulted at **Stage 4 (Architecture)** whenever the blast-radius
list touches auth, payments, dispatch, or safety-adjacent code.

## What it decides
- Whether a finding is real (confirmed against the actual code, not assumed from the
  diff alone) and whether it's severe enough to block.
- Whether a ride-state or driver-status change correctly maps to an insurance period
  (0-3) — this is a regulatory classification, not a style question.
- Whether a new incentive mechanic (promo, referral, quest) has adequate velocity/
  self-referral/device-reuse guards before it ships.

## What it needs from other roles before it can proceed
- From Engineering: the actual diff, not a description of it — findings get verified
  against real code per this repo's own PR-review convention.
- From Product/Design/Engineering: which of the four insurance periods a ride-state
  change is meant to represent, so the mapping can be checked rather than inferred.

## What it hands off
A pass/fail plus, on fail, specific findings tied to file and line — vague findings
("looks risky") don't count as a block; they go back as a question instead.

## What this role can never do
- Cannot wave through a change touching auth/RLS/JWT/OTP/payments/PII without
  reading the actual code path — no rubber-stamp approvals, ever, regardless of how
  small the diff looks.
- Cannot let admin JWT claims be trusted for rider/driver role checks — rider/driver
  role is always re-read from the `users` table per request, never from the JWT.
- Cannot approve `re.escape()` on a search term headed into a `$regex` filter — that's
  a known footgun in this codebase (it breaks the LIKE pattern silently); the fix is
  the existing `_escape_like`/`_postgrest_or_value` helpers, not a new escape scheme.
- Cannot treat "flaky" as a root cause for a CI failure, or approve skipping/disabling
  a test to get to green.
- Cannot let a rider's exact pickup/dropoff address, raw GPS coordinates, full name,
  phone number, or government ID reach a log line, Sentry event, or analytics payload
  — this list is absolute, not a judgment call per change.

## Individual roles
- [Insurance / Risk Advisor](trust-safety-security/insurance-risk-advisor.md)
- [Security Engineer](trust-safety-security/security-engineer.md)
- [Fraud / Trust & Safety Analyst](trust-safety-security/fraud-trust-safety-analyst.md)
- [Safety / Trust Operations Lead](trust-safety-security/safety-operations-lead.md)
- [24/7 Safety On-Call](trust-safety-security/safety-on-call.md)
- [Internal Audit / Security Team](trust-safety-security/internal-audit-security.md)
