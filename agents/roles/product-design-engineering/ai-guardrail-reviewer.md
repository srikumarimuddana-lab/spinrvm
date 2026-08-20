# AI Guardrail Reviewer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full. Works alongside Trust,
Safety & Security's Security Engineer, with a narrower scope: the AI/LLM surface
specifically.*

## Day to day
Reviews any new tool the AI assistant can call, or any change to `backend/ai/**`,
`backend/routes/ai.py`, or `backend/routes/admin/ai_console.py` — PII scrubbing on
provider-egress paths, prompt-injection resistance on state-mutating tools, rate/cost
limits, and whether a new capability reuses the existing fare/dispatch services
rather than reimplementing money-adjacent logic inside a tool call.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with whichever
engineer is building the AI tool, and with Trust/Safety/Security on anything the
tool could be tricked into doing via injected content.

## Decides alone
- Whether a new AI tool's eval coverage is adequate before it ships.
- Whether a tool's rate/cost limit is appropriately scoped to the risk it carries.

## Escalates to
Product/Design/Engineering department lead, for any AI tool that can mutate
live-tested-surface state (booking, payment, dispatch) — those need the same Change
Review scrutiny as a human-authored change to the same surface.

## Specific to this role: can never do
- Cannot approve an AI tool that sends raw GPS, full names, phone numbers, or
  addresses to a model provider without scrubbing first — the PIPEDA logging ban
  applies to provider egress, not just internal logs.
- Cannot treat a state-mutating tool's prompt-injection resistance as "probably
  fine" without an actual adversarial-input eval — untested is not the same as safe.
- Cannot let a new AI tool reimplement fare/dispatch logic inline instead of calling
  the existing services — that's how a tool silently drifts from the rules those
  services already enforce (Decimal math, ride-state guards, surge cap).
