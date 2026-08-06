# Runbook: Metrics Aggregation & Alerting Setup

**What this covers:** Getting Spinr's own `spinr_*` metrics into the Grafana
that Fly already provides, and the alert rules that make the SLA and KPI targets
measurable instead of aspirational.

**Severity:** Setup task. Implements the intent of
[ADR-010](../adr/010-metrics-aggregation-and-alerting.md) by a simpler route —
see §2. Tracked as `ACTION_ITEMS.md` **C11** / CR-2026-008 / issue
[#3295](https://github.com/srikumarimuddana-lab/spinrvm/issues/3295).

**Status:** 🟡 **Config committed, not yet deployed or verified.**
`backend/fly.toml` now carries a `[metrics]` block and `UVICORN_WORKERS = 1`;
neither has run in production. Until a deploy happens, every P95 and KPI number
in `CLAUDE.md` remains unverified.

**Prerequisites:** Fly deploy access, and access to
[fly-metrics.net](https://fly-metrics.net) for the org (already provisioned).
**No Grafana Cloud account and no external metrics vendor are required.**

**Effort:** deploy + verify. Far less than ADR-010's 4–8 h estimate, which
assumed standing up a vendor and an agent.

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

## 2. Why there is no agent and no vendor

ADR-010 §1 chose "agent per machine → managed Prometheus SaaS" because it
assumed we had to bring our own backend. **We do not.** Fly ships a managed
Prometheus and a hosted Grafana (`fly-metrics.net`) with every organisation, and
its scraper runs inside the platform.

That collapses ADR-010's Phase 4 to a config change and **retires the open
"colocated vs standalone agent" decision entirely** — with it goes the
Trivy/C6 risk of modifying the digest-pinned runtime image. `backend/Dockerfile`
is untouched.

What was needed instead, and is already committed:

| Problem | Resolution |
|---|---|
| Fly collects only *platform* metrics (CPU/memory/HTTP) unless told otherwise | `[metrics]` block in `backend/fly.toml` |
| Fly's scraper **cannot send an `Authorization` header**, but `/metrics` is token-gated and fail-closed | Private listener on `:9091` (`backend/metrics_server.py`), absent from `[http_service]` so fly-proxy never routes it — private by construction, not by auth |
| Two forked uvicorn workers meant `/metrics` returned whichever worker answered, so counters appeared to reset every other scrape and `rate()` returned garbage | `UVICORN_WORKERS = 1`, so one scrape target = one counter set |

The third one is the important one and is **not** in ADR-010 — it reasons about
cross-*replica* aggregation and treats a replica as a single process. See
`docs/change-log/2026-08-06-fly-metrics-scrape-and-worker-collapse.md`.

`backend/grafana-agent.yaml` stays in the repo, inert, as a fallback if we ever
leave Fly.

⚠ **The worker change is a capacity change and has not been load tested.** Do
not deploy it without watching machine count and p95 latency. Instant rollback,
no redeploy: `fly secrets set UVICORN_WORKERS=2 -a spinr-backend-yyz`.

---

## 3. Setup steps

1. **Check whether it is already working.** In
   [fly-metrics.net](https://fly-metrics.net), query
   `spinr_dispatch_offer_sent_total`. If it returns data, app-metric scraping is
   already live and you can skip to §4.
2. **Deploy the branch** carrying the `[metrics]` block and `UVICORN_WORKERS=1`.
3. **Confirm the private listener came up.** In the app logs, look for
   `Private metrics listener starting on :9091/metrics`. If instead you see
   `could not bind`, `UVICORN_WORKERS` is still > 1 somewhere.
4. **Confirm the port is not public.** From outside Fly:
   ```bash
   curl -sS --max-time 5 http://api-spinr.spinr.ca:9091/metrics ; echo "exit=$?"
   ```
   Must fail to connect. A 200 here means the port got routed and an
   unauthenticated metrics endpoint is on the internet — stop and fix before
   going further.
5. **Confirm the public endpoint is still gated:**
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://api-spinr.spinr.ca/metrics
   # expect 503 (fail-closed, unauthenticated) in production
   ```
   A **200** means `METRICS_AUTH_TOKEN` is unset and operational data is public —
   fix that first; it also exposes `/health/dependencies`, which shares the gate.
6. **Watch capacity** for the worker change (§2) — machine count and p95 latency.
7. Build the panel in §4, then enable only the two day-one rules in §5.

**Railway stays excluded.** Per ADR-010 §4 and C5 its deploy is blocked and it is
drifting from `main`; it also still runs multiple workers, so its counters remain
split. Monitoring a known-stale build produces noise you would only have to
suppress later.

---

## 4. The one query that matters most

```promql
histogram_quantile(
  0.95,
  sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket[5m])) by (le)
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
impact; the rest are mechanically identical once scraping is live.

> ### ⚠ There is no `provider` label under Fly's built-in scraping
>
> ADR-010 §4 specifies a `provider="fly"` label, set by the **agent's** static
> config. Fly's own scraper does not set it — it applies its own labels
> (`app`, `instance`, `region`). **A query carrying that matcher returns
> "No data", which is easy to misread as "no breaches."**
>
> Today, scope by app instead — or omit the matcher entirely, since Fly's
> Prometheus only holds Fly apps:
>
> ```promql
> {app="spinr-backend-yyz"}     # correct today
> {provider="fly"}              # returns nothing — agent-era label
> ```
>
> The rules below are written **without** a provider matcher for that reason.
> Re-introduce an equivalent (`app=`) matcher if a second app ever writes into
> the same Prometheus. ADR-010 §4's underlying rule still stands and still
> matters: **never sum loop metrics across providers**, because a healthy Fly
> loop would mask a dead Railway one.

### 1. Dispatch latency breach — **P1, page**
```promql
histogram_quantile(0.95,
  sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket[5m])) by (le)
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
  sum(rate(spinr_dispatch_offer_accepted_total[1h]))
/ sum(rate(spinr_dispatch_offer_sent_total[1h]))
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
time() - spinr_loop_heartbeat_timestamp_seconds > 2 * <loop_interval_seconds>
```
Per-loop threshold; see `LOOP_THRESHOLDS` in `backend/utils/loop_monitor.py`.

Three things to get right here:

- **Fly-only is currently automatic** — Fly's Prometheus scrapes only Fly, and
  Railway is not scraped at all (§3). That happens to be the right scope while
  C5 is open: a ticking heartbeat from a stale, non-deploying Railway build is
  not evidence the *current* code is healthy, and treating it as a green light
  would be actively misleading. Do not "fix" this by adding Railway before C5
  closes.
- **Never sum across providers** once Railway is scraped. Loops run on both by
  design, so summing hides a dead one behind a live one. Group `by (loop)` today
  and add the provider dimension the moment a second source exists.
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
prevent. Railway is not scraped today (§3), so this rule only becomes
available once C5 closes and Railway is brought into monitoring.

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
3. Confirm `:9091` is unreachable from the public internet (§3 step 4). No
   Trivy check is needed — unlike ADR-010's colocated-agent option, nothing
   here modifies `backend/Dockerfile`, so the hardened image is untouched.
4. Unblock `ACTION_ITEMS.md` **B6**: pull the real p99 for
   `spinr_fare_directions_duration_ms` and re-tune the fare-estimate wait budget.
   B6 has been explicitly blocked waiting for this pipeline.
5. Update C11 with the completion date.

---

## 8. What this does not cover

- **Cost.** Fly's managed Prometheus is included with the organisation, so there
  is no metrics vendor bill. The cost that *is* real and unmeasured is the
  worker change in §2: if one worker per machine reduces throughput, Fly starts
  more machines. Watch machine count after deploy — this shows up as compute
  spend, not as a monitoring line item.
- **Retention.** Fly's managed Prometheus has its own retention window, which we
  have not checked against what an incident review needs. If you need longer
  history than it keeps, that is a separate remote-write decision — the inert
  `backend/grafana-agent.yaml` is the starting point.
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
