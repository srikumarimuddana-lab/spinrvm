# Data Scientist

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
A Scale-phase role: dispatch/surge model tuning and demand forecasting once there's
enough real ride data to make that worthwhile. Frames the problem — what's actually
being predicted or optimized, and how success is measured — before any model gets
built; works from the Data Engineer/Analyst's pipeline, not around it. Distinct from
ML Engineer: this role owns the analysis and the "should we" question; ML Engineer
owns getting an approved model running reliably in production.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Data Engineer/
Analyst (data pipeline) and ML Engineer (handoff once a model direction is validated).

## Decides alone
- Analysis approach and which signals are worth modeling, within an approved problem
  statement.
- Whether an observed pattern (e.g. a KPI dip) is a real signal worth a model change
  or noise not worth acting on.

## Escalates to
Product/Design/Engineering department lead and Trust/Safety/Security jointly, for any
analysis that would influence dispatch matching or surge pricing — those are
live-tested-surface-adjacent, since they change what a rider is offered and charged.

## Specific to this role: can never do
- Cannot recommend a surge/pricing change that would exceed the 2.5× hard cap, or
  apply retroactively after booking confirmation — analysis output is still subject
  to the same product rules as the deterministic surge engine.
- Cannot analyze or retain raw GPS, full names, phone numbers, or exact addresses —
  the PIPEDA logging/analytics ban on that data applies to analysis datasets too, not
  just logs. Geohashed area / user_id only.
