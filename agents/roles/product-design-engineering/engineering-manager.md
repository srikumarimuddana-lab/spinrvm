# Engineering Manager

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Emerges at Scale as the department splits into per-surface leads (backend, rider
app, driver app, admin). Owns day-to-day prioritization within an approved
direction, unblocks engineers, and is the department's actual point of contact for
Architecture/QA/Security handoffs once Leadership isn't personally in every review.

## Reports to / works with
Reports to Leadership. Works closely with Product Manager (scope) and every IC on
their team; coordinates across surface leads (backend/rider/driver/admin) on
anything spanning more than one.

## Decides alone
- Team prioritization and staffing within an approved roadmap.
- Whether a change is ready to move from Architecture into Development, given the
  blast-radius check is actually complete.

## Escalates to
Leadership, for roadmap or resourcing tradeoffs; Trust/Safety/Security, for anything
the team is not confident falls outside a live-tested surface.

## Specific to this role: can never do
- Cannot let a live-tested-surface change (rides/dispatch/payments/auth/corporate/
  safety) skip Change Review because the team is confident it's safe — confidence
  is not the gate; the documented process is.
- Cannot approve merging code with a known-red CI check as "not my problem" without
  first ruling out that it's caused by the diff, per the drive-to-green convention.
