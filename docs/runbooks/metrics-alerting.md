# Runbook: Metrics Aggregation & Alerting Setup

**What this covers:** Standing up cross-replica metrics aggregation and the
alert rules that make Spinr's SLA and KPI targets measurable instead of
aspirational.

**Severity:** Setup task. Implements [ADR-010](../adr/010-metrics-aggregation-and-alerting.md);
tracked as `ACTION_ITEMS.md` **C11** / CR-2026-008 / issue
[#3295](https://github.com/srikumarimuddana-lab/spinrvm/issues/3295).

**Status:** 🔴 **Not provisioned.** Nothing scrapes `/metrics`. Every P95 and
KPI number published in `CLAUDE.md` is currently unverified.

**Prerequisites:** Grafana Cloud account (free tier suffices); Fly deploy
access; the production `METRICS_AUTH_TOKEN`.

**Effort:** ~4–8 h per ADR-010 §5, plus account lead time.

---

## 1. What already works, so you do not rebuild it

ADR-010 §2 is blunt about this: **the local instrumentation was never the gap.**

- `backend/utils/metrics.py` already implements cumulative-bucket histograms and
  already emits valid `_bucket{le=…}` / `_sum` / `_count` exposition.
- `/metrics` already serves it, already gated by `METRICS_AUTH_TOKEN`, already
  **fail-closed in production**.
- Real SLA-relevant series are already emitted from real call sites.

**The gap is aggregation only.** Do not add OpenTelemetry (ADR-010 §68 rejects
it for this phase), and do not rewrite `metrics.py`.

---

## 2. ⚠ Open decision: where the agent runs

**Resolve this before deploying anything.** Both options are viable; they trade
against each other and the decision has not been made.

| | Colocated process | Standalone Fly app |
|---|---|---|
| How | Add a process to `backend/fly.toml` `[processes]`, scraping `localhost` | Separate Fly app scraping over the private 6PN network |
| Discovery | None needed — always finds its own app | Needs Fly Machines-API glue; `.internal` DNS **load-balances** rather than fanning out to all replicas |
| Risk | Modifies the digest-pinned, Trivy-scanned runtime image — may reopen that scan surface (C6 / CR-2026-002) | Leaves the hardened image untouched |
| ADR-010 says | Recommended (simplest) | — |

**Recommendation while live app testing is active: standalone**, to avoid
touching the hardened image. ADR-010 favours colocated for simplicity; the
difference is risk appetite, not correctness.

`backend/grafana-agent.yaml` in this repo is written for the **colocated** shape
and is **inert** — nothing loads it. If you choose standalone, adapt the
`static_configs` target and add per-replica discovery.

---

## 3. Setup steps

1. Create the Grafana Cloud account (or confirm one exists). Note the Prometheus
   **remote-write URL**, **username/instance ID**, and **API key**.
2. **Verify `METRICS_AUTH_TOKEN` is actually set in Fly production secrets.**
   This is verification, not a code change — `/metrics` already fails closed
   without it, so if it is unset the endpoint is currently returning 503 to
   everything and the agent will scrape nothing.
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://api-spinr.spinr.ca/metrics
   # expect 503 (fail-closed, unauthenticated) in production
   curl -s -o /dev/null -w '%{http_code}\n' \
        -H "Authorization: Bearer $METRICS_AUTH_TOKEN" \
        https://api-spinr.spinr.ca/metrics
   # expect 200
   ```
   A **200 on the first call** means the token is unset and operational data is
   public. Fix that first — it also exposes `/health/dependencies`, which shares
   the gate.
3. Deploy the agent per §2, with static labels `provider=fly`,
   `instance=$FLY_ALLOC_ID`.
4. **Railway is deliberately excluded.** Per ADR-010 §4 and C5, Railway's deploy
   is blocked and it is drifting from `main`. Monitoring a known-stale build
   produces noise you will only have to suppress later. Revisit once C5 closes.
5. Build the first dashboard panel (§4).
6. Enable **only the two day-one rules** (§5), then widen.

---

## 4. The one query that matters most

```promql
histogram_quantile(
  0.95,
  sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket{provider="fly"}[5m])) by (le)
)
```

This is the first time dispatch P95 will be a **measured fact** rather than an
assumption. Worth telling stakeholders on its own.

> ### ⚠ The one mistake to avoid
>
> **Always** `histogram_quantile` over **summed buckets**. **Never** average a
> per-replica quantile:
>
> ```promql
> # WRONG — mathematically meaningless, even if such a series existed
> avg(spinr_dispatch_offer_to_accept_p95)
>
> # RIGHT — buckets are additively aggregatable across replicas
> histogram_quantile(0.95, sum(rate(..._bucket[5m])) by (le))
> ```
>
> Prometheus histogram buckets sum correctly across series; quantiles do not.
> This is the classic footgun and it silently produces a plausible wrong number.

The `2000` ms bucket boundary needed for an exact SLA read **now exists** —
`DISPATCH_MS_BUCKETS` was added for precisely this, so the threshold is a bucket
bound rather than an interpolation across `1000 → 2500`.

---

## 5. Alert rules (ADR-010 §3)

Enable **1 and 2 first.** They have the clearest ride-abandonment and money
impact; the rest are mechanically identical once the agent is live.

### 1. Dispatch latency breach — **P1, page**
```promql
histogram_quantile(0.95,
  sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket{provider="fly"}[5m])) by (le)
) > 2000
```
Sustained 5 m. SLA: P95 offer→accept < 2 s. Failure impact: ride abandonment.

### 2. Payment failure rate — **P1, page**
```promql
  sum(rate(spinr_payment_settlement_total{outcome="failed"}[10m]))
/ sum(rate(spinr_payment_settlement_total[10m]))
> 0.01
```
Sustained 10 m **and** ≥ 20 samples in-window. The sample guard matters: without
it, one failure out of one attempt at 03:00 reads as a 100 % failure rate and
pages someone for nothing.

### 3. WS fan-out latency — **P2 → P1**
```promql
histogram_quantile(0.95, sum(rate(spinr_ws_fanout_duration_ms_bucket[5m])) by (le)) > 100
```
Warn at 5 m sustained, page at 15 m. SLA: < 100 ms. Impact: missed state updates.

### 4. Match rate below KPI — **P3, business-hours notify**
```promql
  sum(rate(spinr_dispatch_offer_accepted_total{provider="fly"}[1h]))
/ sum(rate(spinr_dispatch_offer_sent_total{provider="fly"}[1h]))
< 0.85
```
Not incident-grade. Signals dispatch radius too tight or a driver-supply gap.

### 5. Presence-filter degradation — **P2**
```promql
sum(rate(spinr_dispatch_presence_filter_failed_total[5m])) > 0
```
Sustained 5 m. Turns a buried `logger.warning` into something actionable rather
than found by grepping logs after the fact.

### 6. Background-loop stall — **P1**
```promql
time() - spinr_loop_heartbeat_timestamp_seconds{provider="fly"} > 2 * <loop_interval_seconds>
```
Per-loop threshold; see `LOOP_THRESHOLDS` in `backend/utils/loop_monitor.py`.

Three things to get right here:

- **Scope to `provider="fly"` only** while C5 is open. A ticking heartbeat from a
  stale, non-deploying Railway build is not evidence the *current* code is
  healthy — treating it as a green light is actively misleading.
- **Never sum across providers.** Loops run on both by design; summing hides a
  dead one behind a live one. Always `by (provider, loop)`.
- **A never-started loop emits no series at all** (deliberately — a `0` would
  read as "last ticked in 1970" and false-alarm every deploy for loops that wait
  for their first window, like `stripe_reconcile` at 02:00 UTC). To catch that
  case you need `absent()`, not this expression.

The in-app `loop_watchdog` remains the **primary** stall signal because it does
not depend on this pipeline being healthy. This rule is the second, independent
path.

### 7. Railway serving traffic while C5 is open — **P1**
```promql
sum(rate(spinr_dispatch_offer_sent_total{provider="railway"}[5m])) > 0
```
Any real dispatch traffic on Railway means the CNAME silently pointed or fell
back to a **stale, drifted build** — exactly the failure ADR-007 exists to
prevent. Requires the Railway agent, so it only becomes available after C5
closes and step 4 is revisited.

### 8. Dependency down — **P1** *(new, not in ADR-010)*
```promql
spinr_dependency_up{dependency=~"supabase|redis"} == 0
```
`1` = serving, `0.5` = degraded, `0` = down or unconfigured.

Scope to infrastructure we own. Do **not** alert on
`stripe|twilio|google_maps|firebase` here: those report *configuration
presence*, not liveness, so a `0` means "credentials missing" — worth a
one-time check, not a recurring page. Real vendor failure shows up in rule 2.

---

## 6. Recommended dashboard panels

| Panel | Query basis |
|---|---|
| Dispatch P95 vs 2 s SLA | §4 |
| Fare calc P95 vs 300 ms | `spinr_fare_calc_duration_ms_bucket` |
| WS fan-out P95 vs 100 ms | `spinr_ws_fanout_duration_ms_bucket` |
| Payment success rate vs 99 % | rule 2, inverted |
| Match rate vs 85 % | rule 4 |
| Dependency status | `spinr_dependency_up` |
| Loop heartbeat age | rule 6 expression, `by (loop)` |
| DB circuit state | `spinr_db_circuit_state` |
| Redis memory % | `spinr_redis_used_memory_percent` |

---

## 7. Verification

1. Confirm the panel in §4 populates from **real production traffic** — not just
   that the query parses.
2. Let the rules run **without paging enabled** for at least a day and confirm
   they do not false-fire under normal load. Rule 2's low-volume behaviour
   overnight is the one most likely to misbehave.
3. If you chose the **colocated** option in §2, confirm the `docker-image-scan`
   (Trivy) CI check is **still green** — that was the stated risk of that path.
4. Unblock `ACTION_ITEMS.md` **B6**: pull the real p99 for
   `spinr_fare_directions_duration_ms` and re-tune the fare-estimate wait budget.
   B6 has been explicitly blocked waiting for this pipeline.
5. Update C11 with the completion date.

---

## 8. What this does not cover

- **Cost.** Grafana Cloud's free tier should fit current cardinality (roughly a
  dozen metric names, low label cardinality), but this has **not** been measured
  against real series counts. Set a billing alert before enabling paging.
- **Tracing.** Metrics answer "is dispatch slow", not "why was *this* offer
  slow". That is OTLP/tracing work, deliberately deferred (ADR-010 §68).
- **KPIs not derivable from backend counters.** Driver utilisation, weekly
  retention, and support response time come from other systems. Phase 2 of
  `plans/monitoring-observability-implementation-plan.md` proposes a
  `kpi_service` for the DB-derived ones.
- **SOS-unacknowledged alerting.** Needs new backend instrumentation, not a
  rule (ADR-010 §3). Phase 2.
- **Whether any threshold is right.** Every number here comes from `CLAUDE.md`'s
  target tables, not from observed production distributions. Expect to tune.
