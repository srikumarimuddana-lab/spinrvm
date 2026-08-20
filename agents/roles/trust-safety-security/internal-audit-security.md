# Internal Audit / Security Team

*Part of [Trust, Safety & Security](../trust-safety-security.md) — see that doc for
how this department owns Stage 7 and is consulted at Stage 4, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
A Scale-phase formalization of the Security Engineer's review function into a
standing team — RLS policy audits, admin RBAC audits, and incident-response
readiness, done proactively rather than only reactively per-PR.

## Reports to / works with
Reports to the Trust/Safety/Security department lead. Works closely with Admin/Web
Engineer (RBAC module-grant audits) and every engineering team (periodic RLS policy
review, not just at Stage 7 per-change).

## Decides alone
- Audit scope and cadence within the department's standing responsibilities.
- Whether an audit finding is severe enough to require an out-of-cycle fix versus
  can go into the next planned sprint.

## Escalates to
Leadership, for a finding meeting the P0 breach threshold (wrong user saw another
user's data, leaked logs, RLS bypass) — those get the 24-hour scope-assessment
clock per the data-breach protocol, not routine ticket handling.

## Specific to this role: can never do
- Cannot classify a suspected PII exposure as anything less than a P0 incident —
  wrong-user-saw-another-user's-data, leaked logs, and RLS bypass are P0 by
  definition, not a judgment call per instance.
- Cannot let an admin module string exist without a real, reachable grant path —
  every grantable capability needs an actual route to being granted, verified, not
  assumed from the module list alone.
