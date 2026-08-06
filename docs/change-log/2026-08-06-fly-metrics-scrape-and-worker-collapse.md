# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Surface(s) | backend (Fly deploy config) |
| Domain (Sentry tag) | admin (observability) |
| PR / commit link | (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Related issue or gap ID | `ACTION_ITEMS.md` C11 / CR-2026-008 / issue #3295; ADR-010 |

## 1. Issue / gap identified

Two problems, both blocking any trustworthy number:

1. **No application metrics reach Grafana.** Fly provides managed Prometheus +
   Grafana (`fly-metrics.net`) and collects *platform* metrics automatically,
   but *application* metrics require a `[metrics]` block in `fly.toml`. There
   was none, so no `spinr_*` series existed — the exact series that answer
   CLAUDE.md's SLA and KPI questions.
2. **The counters were split across workers.** `Dockerfile:108` runs
   `uvicorn --workers ${UVICORN_WORKERS:-4}` and `fly.toml` set it to `2`.
   `utils/metrics.py` is per-process by design, so `/metrics` returned
   **whichever worker answered the request**.

## 2. Root cause

(1) is simply a config that was never added — ADR-010 assumed a Grafana Cloud +
agent architecture and did not consider Fly's built-in Prometheus.

(2) is an interaction nobody had connected. `metrics.py`'s docstring is explicit
that it is per-process, and ADR-010 §1 reasons carefully about cross-*replica*
aggregation — but both treat "one replica" as "one process". With forked
workers that is false. ADR-010 mentions the 2-worker setup exactly once
(line 302) and only as a *memory* consideration for the agent, never as a
metrics-correctness problem.

The consequence is worse than missing data. Prometheus models a counter as
monotonic; a decrease means "process restarted, counter reset". Alternating
scrapes between worker A (say 100) and worker B (say 80) reads as a reset on
every other scrape, so `rate()` and therefore `histogram_quantile()` return
garbage. **Every P95 and every alert rule in
`docs/runbooks/metrics-alerting.md` would have been wrong** — and wrong in the
worst way, since a plausible-looking number invites action.

## 3. Fix / remediation

- `UVICORN_WORKERS` `2` → `1`, so one scrape target equals one counter set.
- `METRICS_PORT = "9091"` starts the private listener (previous commit).
- A `[metrics]` block pointing Fly's scraper at `9091`.

Chosen over the alternatives because it is correct *by construction*, needs no
new dependency, and does not rewrite `metrics.py` — a module on every hot path.
`prometheus_client` multiprocess mode would also be correct but means a new
dependency plus a storage rewrite; moving counters to Redis would put a network
call inside the dispatch and fare hot paths we are trying to measure.

## 4. Risk & impact on existing functionality

**Blast radius: Fly deploy only.** `fly.toml` is not read by Railway, Render,
Docker Compose, or local development. No Python source changed in this commit.

### The real risk: halving workers is a capacity change

This is the part to scrutinise. Mitigating factors, in order of weight:

- **`[[vm]] size = "shared-cpu-1x"` is a single vCPU.** Two forked workers
  contend for one core, so for an asyncio-bound FastAPI app the throughput gain
  from the second worker was already marginal — most of this app's time is spent
  awaiting Supabase, Stripe, and Google Maps, not burning CPU.
- Memory pressure *improves*. ADR-010 records that 4 workers on
  `shared-cpu-1x`/1gb was memory-tight, which is why it was already cut to 2.
- Fly autoscales: `auto_start_machines = true` with `min_machines_running = 2`
  and a 200-connection soft limit, so load spreads to more machines.

**Residual risk:** if throughput per machine drops more than expected, Fly
starts more machines — raising cost, not dropping requests. Watch machine count
and p95 latency after deploy. **Raise `min_machines_running` before raising
workers**: scaling horizontally keeps metrics correct, scaling workers breaks
them again.

### Other effects

- **Railway is unaffected and therefore still split.** It has its own env and,
  per C5, its deploy is blocked and drifting. This is consistent with ADR-010
  §4, which already says to scope loop alerting to `provider="fly"` and never
  sum across providers. Railway's metrics were not trustworthy before this
  change and are not after it.
- **New network listener on 9091.** Unauthenticated, but absent from
  `[http_service]`, so fly-proxy never routes it. Asserted in verification.
- No interaction with the ride state machine, dispatch, money/wallet deltas,
  auth, or any DB table. No migration.

## 5. User-experience effect

**Nobody, if capacity holds.** No rider, driver, corporate-admin, or
internal-admin surface changes. No copy change. Nothing is visible mid-session.

The honest caveat: a worker-count change is a capacity change, and a capacity
shortfall *would* be user-visible as latency. That is why §9 requires a load
check before merge rather than treating this as invisible.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/fly.toml` | `UVICORN_WORKERS` 2→1 | One scrape target = one counter set; without it every rate and quantile is wrong |
| `backend/fly.toml` | `METRICS_PORT = "9091"` | Starts the private listener for Fly's scraper |
| `backend/fly.toml` | New `[metrics]` block | Points Fly's managed Prometheus at that port |

## 7. Before / after

```toml
# Before
[env]
  UVICORN_WORKERS = "2"
# (no [metrics] block — no spinr_* series in Grafana)
```

```toml
# After
[env]
  UVICORN_WORKERS = "1"
  METRICS_PORT = "9091"

[metrics]
  port = 9091
  path = "/metrics"
```

Counter behaviour a scraper observes, with 2 workers vs 1:

```
# Before (2 workers, one port) — alternating, reads as repeated resets
scrape 1 -> spinr_dispatch_offer_sent_total 100   (worker A)
scrape 2 -> spinr_dispatch_offer_sent_total  80   (worker B)  <- rate() sees a reset
scrape 3 -> spinr_dispatch_offer_sent_total 103   (worker A)

# After (1 worker) — monotonic, rate() correct
scrape 1 -> 100
scrape 2 -> 104
scrape 3 -> 109
```

## 8. Rollback plan

**No redeploy of application code required.** Revert `fly.toml` and redeploy, or
faster, without touching the repo at all:

```bash
fly secrets set UVICORN_WORKERS=2 -a spinr-backend-yyz   # restores capacity immediately
```

`[env]` values can be overridden by `fly secrets`, so capacity is recoverable in
one command if the load check was wrong in production. Removing the `[metrics]`
block requires a redeploy but is inert — losing it costs visibility, not
service.

Nothing durable is written and no live state is touched (no ride rows, wallet
deltas, Stripe charges, or insurance-period rows), so there is no data-level
remediation to plan.

## 9. Verification performed

- [x] `fly.toml` parsed with `tomllib`; asserted `[metrics].port == 9091` and
      that it differs from `[http_service].internal_port` — i.e. the
      unauthenticated port is **not** the fly-proxy-routed one.
- [x] Private listener covered by `tests/test_metrics_server.py` → **16 passed**,
      including that it exposes only `/metrics` and never returns normally.
- [x] Regression across metrics/health/server suites → **53 passed**.
- [x] Blast-radius grep — `UVICORN_WORKERS` appears in `Dockerfile:108`,
      `fly.toml`, and `routes/admin/monitoring.py:571` (reads it for display
      only). No logic branches on worker count.
- [x] Reviewed against ADR-010 §4 (per-provider evaluation, never summed).
- [ ] **Load sanity check on 2 → 1 workers — NOT DONE. Required before merge.**
- [ ] Not feature-flagged; `fly secrets set` is the equivalent instant lever (§8).

## 10. What was NOT verified

- **The load check is outstanding and this must not merge without it.** The
  single-vCPU argument in §4 is reasoning from `[[vm]] size`, not a measurement.
  No load test was run, and none can be from a dev sandbox.
- **Never deployed.** No Fly deploy was performed, so it is unproven that Fly's
  scraper actually reaches `:9091`, that `spinr_*` series appear in Grafana, or
  that `:9091` is genuinely unreachable publicly. The port-isolation claim rests
  on Fly's documented routing behaviour plus the asserted config, **not** on an
  observed 6PN scrape or an attempted public connection. Verify both after the
  first deploy.
- **Fly's scrape-auth limitation is from community/vendor documentation**, not
  from testing this app against Fly's scraper.
- **Prometheus counter-reset behaviour is reasoned from Prometheus semantics**,
  not observed in this deployment — precisely because nothing scrapes today.
- `ruff check .` remains red repo-wide (36 pre-existing errors), unrelated.
