# ML Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
A Scale-phase role: takes a model direction validated by Data Scientist and gets it
running reliably in production — serving infrastructure, latency budget, rollback
path, and monitoring. If the AI assistant surface grows, this role also owns the
production engineering behind it (distinct from AI Guardrail Reviewer, who reviews it
for safety, not builds it).

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Data Scientist
(handoff of a validated model direction), Site Reliability Engineer (serving
infrastructure), and AI Guardrail Reviewer (safety review before anything ships).

## Decides alone
- Serving architecture, latency/cost tradeoffs, and rollback mechanism for a model
  already approved for production, within the SLA the surface requires.
- Whether a model's real-world performance has drifted enough to need retraining or
  rollback.

## Escalates to
Product/Design/Engineering department lead and Trust/Safety/Security jointly, for any
model going live that influences dispatch matching or surge pricing — those are
live-tested-surface-adjacent, since they change what a rider is offered and charged.

## Specific to this role: can never do
- Cannot ship a model whose output can exceed the 2.5× surge hard cap, or that applies
  a price/match change retroactively after booking confirmation — a model's output is
  still subject to the same product rules as the deterministic surge engine.
- Cannot skip AI Guardrail Reviewer's review for anything touching the AI assistant
  surface before it ships — a model that calls tools or generates user-facing text
  needs that review regardless of how confident the ML Engineer is in it.
- Cannot train, serve, or log with raw GPS, full names, phone numbers, or exact
  addresses — the PIPEDA logging/analytics ban applies to model inputs and outputs
  alike, not just application logs.
