# Privacy / Compliance Lead (Data Protection Officer)

*Part of [Finance, Legal & People](../finance-legal-people.md) — see that doc for how
this department owns the compliance angle of Stage 8, and for the department-wide
can't-do list this role inherits in full.*

## Day to day
Owns PIPEDA data-flow review, data-minimization discipline (every new field tied to
a stated purpose), retention schedules, and breach protocol. Starts as an advisory
review function before real user PII exists, formalizes into a dedicated Data
Protection Officer at Scale.

## Reports to / works with
Reports to Leadership (early) or the Finance/Legal/People department lead once
formalized. Works closely with every engineer touching a data flow — this role
reviews before PII starts moving through a new surface, not after.

## Decides alone
- Whether a new data field is justified by a stated purpose, or should be dropped/
  narrowed.
- Whether a data flow needs region-residency confirmation (Supabase must stay in a
  Canadian region).

## Escalates to
Leadership, for any suspected PII exposure — that's a P0 incident with a 24-hour
scope-assessment clock and a possible 72-hour Privacy Commissioner notification
requirement, not a routine review finding.

## Specific to this role: can never do
- Cannot approve raw GPS coordinates, full phone numbers, full names, email
  addresses, payment card numbers, or government IDs reaching a log line, Sentry
  event, or analytics payload — this list is absolute.
- Cannot approve moving primary storage (Supabase, Stripe customer data, Firebase)
  out of a Canadian-region match without legal sign-off — a data-residency change is
  a compliance event, not a routine infra decision.
