# Spinr marketplace load simulation (E2)

Two-sided marketplace simulation: N rider bots booking rides + M driver
bots online with 1 Hz GPS pings, real dispatch/offer/accept matchmaking,
and trips driven to `completed`. Purpose: find the platform's breaking
point **before** a Saturday night does, and pin the CLAUDE.md SLA table
under load.

**Never point this at production.** It books real rides. The dev OTP
bypass (`1234`) only works when the target's `ENV != production`, which
doubles as a safety interlock — production targets will fail login.

## Prerequisites

1. **A staging environment** (ACTION_ITEMS E1). As of 2026-06-10 none
   exists — deploys go straight to prod. Stand up the staging Fly app +
   throwaway Supabase project first; this harness is ready for it.
2. **Seeded bot accounts.** Bots log in by phone with the dev OTP:
   - riders: `+13065550002, ...0004, ...0006, …` (even suffixes)
   - drivers: `+13065550003, ...0005, …` (odd suffixes) — each needs a
     `drivers` row that is `is_verified=true, status='active'`, with a
     vehicle type and non-expired documents, or `go_online` will refuse.
   - a service area polygon covering Saskatoon downtown
     (52.1332, -106.6700) — or set `LOADTEST_CENTER_LAT/LNG`.
3. `pip install -r requirements.txt` (locust + websocket-client).

## Running

```bash
export LOADTEST_BASE_URL=https://staging-api.spinr.ca
mkdir -p results

# Scenario A — steady state (sanity): 45 riders / 15 drivers, 10 min
locust -f locustfile.py --headless -u 60 -r 2 -t 10m \
  --host "$LOADTEST_BASE_URL" --csv results/steady

# Scenario B — ramp to breaking point: add 4 users/s until SLAs break
locust -f locustfile.py --headless -u 600 -r 4 -t 30m \
  --host "$LOADTEST_BASE_URL" --csv results/ramp
```

The 3:1 rider:driver weight matches a supply-constrained Saturday night;
override with class picking (`locust -f locustfile.py RiderBot DriverBot`).

While it runs, watch the backend's own telemetry — that is the point of
the instrumentation this harness pairs with:

- `GET /metrics` (Bearer `METRICS_AUTH_TOKEN`):
  `spinr_dispatch_offer_sent_total`, `spinr_dispatch_offer_accepted_total`,
  `spinr_dispatch_offer_to_accept_duration_ms`, `spinr_fare_calc_duration_ms`,
  `spinr_payment_settlement_total`, `spinr_ws_fanout_duration_ms`,
  `spinr_db_thread_pool_threads` / `spinr_db_thread_pool_max_workers`,
  `spinr_db_retry_total`, `spinr_db_circuit_state`
- admin monitoring → replica panel: DB thread-pool `queued_calls` (queue
  growth = the pool is the bottleneck), circuit-breaker state, Redis memory.
- `spinr_db_thread_pool_queue_depth`, `spinr_db_calls_rejected_total`
  (labelled by `reason`), `spinr_rate_limit_violation_total` (by `path`) —
  these are the three signals the `capacity_watchdog` loop alerts on, so a
  run that trips them should also produce a webhook alert. **A ramp that
  saturates the pool without an alert arriving is itself a finding**: check
  `ALERT_WEBHOOK_URL` and the cooldown state before trusting the next run.

Capacity limits, the per-layer ceilings, and what to do when an alert
fires are documented in
[`docs/runbooks/capacity-scaling.md`](../docs/runbooks/capacity-scaling.md).
Read §1 before interpreting a breach — the four layers (rate limits, Fly
connections, DB thread pool, Supabase tier) fail differently, and the
lowest one binds.

## SLA gates

`test_stop` asserts (exit code 1 on breach):

| Gate | Source |
|---|---|
| fare estimate P95 < 300 ms | CLAUDE.md SLA table |
| dispatch offer→accept P95 < 2 s | CLAUDE.md SLA table |

`market:request-to-accept` (rider-perceived match time) is recorded as a
custom metric for trending, not gated — it includes bot think-time.

## Recording the breaking point

A-grade platforms know their number. After each ramp run, append a row:

| Date | Commit | Env | Riders/Drivers at breach | First symptom | Bottleneck | Notes |
|------|--------|-----|--------------------------|---------------|------------|-------|
| _example_ 2026-06-14 | abc1234 | staging-fly-1x-shared | 240/80 | estimate P95 612ms | DB thread pool saturated (queued_calls>50) | retest after DB_THREAD_POOL_SIZE=96 |
| | | | | | | |

### Expected breaking point after the 2026-08-07 burst-capacity change

Nothing below is measured — it is the arithmetic the config was sized on,
recorded so the first real run has a prediction to falsify rather than a
blank page. **If a run contradicts these, the run is right.**

| Layer | Predicted ceiling | Predicted first symptom |
|---|---|---|
| Fly connections | 8 machines × 750 soft / 1000 hard ⇒ ~6,000 concurrent WS users | Connection refusals at the proxy, before any app-level error |
| DB thread pool | running machines × 2 workers × 64 = 1,024 at full 8-machine wake | `spinr_db_thread_pool_queue_depth` > 50, then estimate P95 breach |
| Supabase tier | Unknown — tier-dependent, not autoscaling | `spinr_db_calls_rejected_total{reason=circuit_open}` rising |
| Rate limits | Per user, so should NOT bind on legitimate load | `spinr_rate_limit_violation_total` climbing on one `path` |

The genuinely untested assumption is **CPU on `shared-cpu-1x`**: 750
connections per machine was sized against memory (tens of KB per WS), and
CPU was never profiled. If a ramp breaches SLAs well below 6,000 users
with a healthy DB pool, CPU is the bottleneck — add machines
(`flyctl scale count`), do not raise `soft_limit`.

Rate limits binding during a ramp is most likely a **harness** artifact:
bot users that share one token key to one bucket. Give each bot its own
authenticated user before concluding the limits are too tight.

"First symptom" = the first SLA gate or error-rate (>1% non-2xx) to
breach during the ramp. Locust's `results/ramp_stats_history.csv` gives
the user count at that timestamp; correlate with `/metrics` scrapes.

## Status (2026-06-10)

Harness written against the live API contract (`/api/v1` paths, OTP auth,
WS auth-first message, `new_ride_assignment` offers, pickup-OTP verify).
**Not yet executed** — blocked on E1 (no staging environment), and this
authoring environment has no egress to a deployable target. First run
owner should expect to tweak: the estimate-response field names
(`estimates`/`fares`), `drivers:me` response nesting, and seeding quirks.
