# ADR-014: Full distributed tracing (OpenTelemetry) deferred until multi-replica latency debugging is actually painful

- Status: Accepted
- Date: 2026-09-04
- Deciders: Claude Code session, in response to a direct product-owner question ("what is D2 for, in layman's terms, and what's the benefit of having vs. not having it") while working `ACTION_ITEMS.md`'s D2 item
- Domain: observability
- Affects: no code — this ADR records a deliberate non-decision (do not build yet) so it is a documented choice rather than a silently-skipped backlog item

## Context

`ACTION_ITEMS.md`'s **D2** reads, in full: *"Distributed tracing — request-ID
propagation exists (`X-Request-ID`); full OpenTelemetry only if multi-replica
latency debugging becomes painful."* Its own text already answers the
question it poses — the item was never a request to build tracing now, it was
a placeholder marking a future trigger condition.

**What exists today:**
- Every request carries an `X-Request-ID` (see `backend/core/middleware.py`),
  so an operator debugging one request can grep every backend log line tagged
  with that ID and reconstruct what happened, on the single replica that
  handled it.
- [ADR-010](010-metrics-aggregation-and-alerting.md) already evaluated three
  options for cross-replica observability — (a) self-hosted Prometheus scrape,
  (b) push to a managed Prometheus-compatible backend (the option that ADR
  adopted), and **(c) full OpenTelemetry SDK export (traces + metrics + logs
  unified)**. ADR-010 explicitly declined (c) for that decision, not because
  it is a bad idea, but because it is strictly more work than (b) to answer
  the question ADR-010 was actually trying to answer ("what's our real P95"),
  and it deferred (c) explicitly to "once distributed tracing... becomes the
  priority." **D2 is that deferred trigger, not a new question.**

**What tracing would add over what exists:** a per-request *waterfall*
showing how much time was spent in each step across every service the
request touched (DB call, third-party API call, WebSocket fan-out, etc.),
automatically and across replicas — rather than an operator manually
correlating `X-Request-ID`-tagged log lines by hand, one replica's logs at a
time. It becomes meaningfully more valuable specifically when a single
request's handling is split across multiple backend replicas (Spinr runs
both Fly.io and Railway per [ADR-007](007-fly-primary-railway-standby.md)),
since stitching a multi-replica request together from plain logs is genuinely
harder than a single-replica one.

**What it costs to have:** new tracing-backend infrastructure (to run and
pay for, on top of the metrics backend ADR-010 already stood up), new
instrumentation code at call sites across the backend, and ongoing
maintenance — for a benefit that is currently theoretical. Reviewed
`ACTION_ITEMS.md`'s own incident/debugging history (the P0/P1 items, the C-
and B-series findings) for a case where the *absence* of tracing was itself
the blocker to root-causing something: none was found. Every latency
investigation on record so far (e.g. B6's Directions-latency work, the
various dispatch-latency findings) was blocked on **not having aggregated
metrics** (ADR-010's actual gap) or on **not having a specific number
measured yet** (e.g. C50's Phase 0 T3 per-phase timing), never on "we
couldn't tell which replica or which step a slow request spent its time in."

## Decision

**Do not build OpenTelemetry / full distributed tracing now.** Keep the
existing `X-Request-ID` propagation as the debugging tool for single-replica
request tracing, and treat ADR-010's Prometheus-based aggregation (once
fully live) as the tool for aggregate SLA/KPI visibility. Revisit this
decision — and reopen D2 as an active item rather than a deferred one — the
first time either of these becomes concretely true:

1. A real production debugging session is measurably slowed down because a
   request's latency needs to be decomposed *across multiple replicas* and
   `X-Request-ID` log-correlation plus the ADR-010 metrics pipeline are not
   enough to find the bottleneck; or
2. Spinr's replica topology or request-fan-out pattern changes in a way that
   makes cross-replica correlation a routine need rather than an edge case
   (e.g. a new service split off the monolith, a request that legitimately
   spans many internal hops).

This is not a rejection of OpenTelemetry as a future direction — ADR-010
already named it as "the right long-term answer once tracing becomes the
priority." This ADR exists so that "not now" is a recorded, reasoned
decision with a named trigger condition, not a backlog item that quietly
never gets picked up because nothing ever forces the question.

## Consequences

- **No code change, no new infrastructure, no new dependency.** This ADR is
  documentation-only.
- `ACTION_ITEMS.md`'s D2 stays open in spirit but is no longer an ambiguous
  "should someone build this?" item — it now points here, with an explicit
  trigger condition instead of a vague "if it becomes painful."
- If either trigger condition in the Decision section fires, the next step
  is *not* to immediately implement OpenTelemetry — it is to re-evaluate at
  that time against whatever the actual pain point turns out to be (a
  narrower fix, like enriching `X-Request-ID` log correlation across
  replicas, may resolve it more cheaply than a full tracing rollout;
  OpenTelemetry may still be the right answer, but that should be decided
  against the real problem, not assumed).
- Nothing about this decision blocks or is blocked by ADR-010's metrics
  aggregation work, which proceeds independently.
