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
