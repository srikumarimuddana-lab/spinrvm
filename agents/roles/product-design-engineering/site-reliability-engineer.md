# Site Reliability Engineer (SRE)

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Owns the 18 background asyncio loops in `backend/core/lifespan.py`, WebSocket
fan-out reliability, and the Fly-primary/Railway-standby failover story. The role
most responsible for a new background loop actually being replay-safe before it
ships — every loop runs concurrently on every replica.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with DevOps/Infra
Engineer (deploy/failover mechanics) and Backend Engineer (whoever's adding the loop
or WS event).

## Decides alone
- Whether a new background loop's idempotency mechanism (atomic claim, reminder
  flag, lock TTL) actually holds up under multi-replica concurrency.
- Runbook content and failover drill cadence.

## Escalates to
Product/Design/Engineering department lead, for anything that would change a loop's
interval/lock semantics in a way that trades exclusivity for cadence on a money path
— that's a payments-adjacent decision, not a routine reliability tweak.

## Specific to this role: can never do
- Cannot ship a new startup loop without confirming it's replay-safe across replicas
  — an atomic DB claim, idempotency key, or `reminder_sent`-style flag is required,
  not optional.
- Cannot let Railway silently drift from `main` while docs describe it as a live
  warm standby — a real degraded-failover state needs to be visible, not papered over.
- Cannot treat a WebSocket P95 fan-out latency breach as acceptable without
  investigating — that SLA maps directly to missed real-time state updates for
  riders and drivers.
