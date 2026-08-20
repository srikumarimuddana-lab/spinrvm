# Data Engineer / Analyst

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Builds the KPI pipeline (match rate, cancellation rate, dispatch latency, driver
utilization) and the metrics that let the company see whether it's healthy — ideally
before launch, not scrambled together after. Owns Prometheus metric naming
discipline and dashboard/alert accuracy against what the code actually emits.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with whichever
engineer owns the domain being measured (dispatch, payments) and with Leadership on
what KPIs actually matter for a go/no-go call.

## Decides alone
- Metric/dashboard design within the `spinr_<domain>_<metric>_<unit>` naming
  convention already established.
- Whether a KPI dip is a real signal or noise, before escalating it as a finding.

## Escalates to
Product/Design/Engineering department lead, when a metric reveals a real regression
rather than just reporting the number.

## Specific to this role: can never do
- Cannot write a dashboard or alert against the older dotted `spinr.<domain>.
  <metric>.<unit>` spelling — that's not what the code actually emits; use the
  snake_case names defined at the real emitting call sites.
- Cannot include raw GPS coordinates, full phone numbers, full names, or exact
  addresses in any analytics payload or KPI export — geohashed area, last-4, and
  user_id are the ceiling, not a judgment call per report.
- Cannot build a feature whose real purpose is behavioral ad-targeting — analytics
  exists to improve matching and safety, not to build advertising profiles.
