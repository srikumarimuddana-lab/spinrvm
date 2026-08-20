# Data Scientist / ML Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
A Scale-phase role: dispatch/surge model tuning and demand forecasting once there's
enough real ride data to make that worthwhile, and — if the AI assistant surface
grows — the modeling work behind it. Works from the Data Engineer/Analyst's
pipeline, not around it.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Data Engineer/
Analyst (data pipeline) and, for anything touching the AI assistant, the AI
Guardrail Reviewer.

## Decides alone
- Model/feature approach within an approved problem statement.
- Whether a proposed model change is safe to test in a limited cohort before a full
  rollout.

## Escalates to
Product/Design/Engineering department lead and Trust/Safety/Security jointly, for
any model that influences dispatch matching or surge pricing — those are
live-tested-surface-adjacent, since they change what a rider is offered and charged.

## Specific to this role: can never do
- Cannot let a surge/pricing model output exceed the 2.5× hard cap, or apply
  retroactively after booking confirmation — the model's output is still subject to
  the same product rules as the deterministic surge engine.
- Cannot train or evaluate a model on raw GPS, full names, phone numbers, or exact
  addresses — the PIPEDA logging/analytics ban on that data applies to training data
  too, not just logs.
