# Monitoring build-out — items 4, 5, 8, 9, 10, 11, 12

## Context

Spinr is mid-live-testing with real users. Error tracking is solid (Sentry on
backend + both mobile apps + admin, Crashlytics fallback, Loguru→Sentry bridge),
but error tracking is one layer of about ten. Today:

- **No metrics aggregation.** `backend/utils/metrics.py` is per-process by
  design (its own docstring says so). `/metrics` exposes it, but nothing
  scrapes it — so every P95 SLA and KPI target published in `CLAUDE.md` is
  aspirational, not measured. This is `ACTION_ITEMS.md` **C11** / **ADR-010** /
  issue #3295, and it already blocks item **B6**.
- **Nothing probes production.** Deployment is Fly primary / Railway warm
  standby with *manual* Cloudflare DNS failover, and per **C5** Railway's
  deploy is blocked so the standby is silently drifting. A Fly outage would be
  discovered via support tickets, and the thing we'd fail over to is stale.
- **`/health` checks the database only** — no Stripe/Twilio/Maps/FCM/Redis
  visibility.
- **Funnel analytics is a no-op stub.** `shared/analytics/index.ts` returns
  `noop()` for all 11 call sites, so signup→booking→completion drop-off is
  invisible during exactly the phase where it matters most.
- **Paging is SOS-only.** `utils/safety_paging.py` is a good pattern but
  nothing else can reach an on-call human; **C2**'s token-theft tripwire emits
  a correctly-tagged Sentry event that no alert rule matches.

**Intended outcome:** a real dispatch-latency P95 on a dashboard, two alert
rules that page a human, an external prober that catches a Fly outage before
users do, and dependency + KPI visibility — without touching ride, dispatch,
payment, or auth business logic.

### Scope reality: this is not all code

Item 4's entire MVP (ADR-010 §5) and item 11's C2 fix are **vendor/infra
config**, not source changes — C2 is logged as *"~5 min in Sentry UI, no
code."* Those cannot be executed from a dev sandbox. The plan therefore splits
into **Track A** (in-repo code) and **Track B** (an operator checklist this
work produces but cannot run).

### What already exists and must be reused, not rebuilt

| Need | Reuse | Path |
|---|---|---|
| Counters/gauges/histograms | `inc` / `set_gauge` / `observe` / `time_ms` / `timed` | `backend/utils/metrics.py` |
| Prometheus exposition | `render_prometheus()` — already valid `_bucket`/`_sum`/`_count` | `backend/utils/metrics.py:153` |
| Scrape auth | `METRICS_AUTH_TOKEN`, already **fail-closed in production** | `backend/server.py:238` |
| Paging transport | PagerDuty Events v2 shape, `app_settings`-driven, dark-by-default, never raises | `backend/utils/safety_paging.py` |
| Webhook alert path | `check_and_alert()` + `ALERT_WEBHOOK_URL` | `backend/utils/loop_alert.py:33`, `core/config.py:208` |
| Loop heartbeats | `record_heartbeat()` / `get_loop_status()` | `backend/utils/loop_monitor.py:40` |
| Ride KPIs (partial) | acceptance/cancellation rates + `admin_driver_acceptance_rates` PG aggregate | `backend/routes/admin/analytics.py:141` |
| Security event tagging | tagged Sentry emit `spinr_alert=refresh_token_reuse` | `backend/utils/refresh_tokens.py:263` |
| PII redaction | `geohash()`, scrubbers | `backend/utils/pii.py`, `shared/utils/pii.ts` |
| Analytics swap point | no-op facade, deliberately built for drop-in replacement | `shared/analytics/index.ts` |

**ADR-010 §2 is explicit: the local instrumentation was never the gap.** Do not
rewrite `metrics.py`. Do not add OpenTelemetry (§68 rejects it for this phase).

---

## Phase 1 — approved for execution now

Low risk by construction: no state-machine changes, no safety path, no new
background loops, no money arithmetic, no migrations.

### 1. Dispatch SLA histogram bucket
`backend/utils/metrics.py`, `backend/tests/test_metrics.py`

`DEFAULT_MS_BUCKETS` jumps `1000 → 2500`, so the 2000 ms dispatch SLA sits mid-bucket
and `histogram_quantile()` interpolates across it (ADR-010 §2 flags this). Add a
`DISPATCH_MS_BUCKETS` tuple including `2000` and pass it at the
`spinr_dispatch_offer_to_accept_duration_ms` call site only. **Do not change
`DEFAULT_MS_BUCKETS`** — the first observation pins bucket layout per metric name,
so a global change silently invalidates existing series.

### 2. Loop heartbeat gauge
`backend/utils/loop_monitor.py`, `backend/core/lifespan.py`

Expose `spinr_loop_heartbeat_timestamp_seconds{loop=...}` per ADR-010 §3, giving a
second alert path independent of the webhook.

**Design detail:** `record_heartbeat` stores `time.monotonic()`, which is
meaningless to `time()` in PromQL. Record a parallel wall-clock `time.time()`
value for the gauge; leave the monotonic value alone since existing staleness
logic depends on it.

### 3. Dependency health probe
`backend/utils/dependency_health.py` (new), `backend/tests/test_dependency_health.py` (new)

Probe Supabase, Redis, Stripe, Twilio, Google Maps, FCM. Contract, mirroring
`safety_paging.py`: short timeout per probe, results cached (~30 s) so scrapes
can't stampede upstreams, **never raises**, and never returns credentials,
internal hostnames, or raw upstream error bodies.

### 4. Expose dependency health
`backend/server.py`

Add `/health/dependencies` and emit `spinr_dependency_up{dependency=...}` gauges
so the same probe feeds both the prober and Grafana.

**Security:** an unauthenticated endpoint announcing "stripe: down" is
operational intel. Gate it behind the existing `METRICS_AUTH_TOKEN` fail-closed
pattern (`server.py:238`), or serve a terse public shape and a detailed authed
one. Leave plain `/health` untouched — it is the Fly/Railway liveness check.

### 5. Uptime + synthetic runbook (Track B)
`docs/runbooks/uptime-monitoring.md` (new)

Prober setup (Better Stack / Checkly / UptimeRobot), what to probe (`/health`
liveness vs `/health/dependencies` depth), intervals, thresholds, and escalation.
Must state the C5/ADR-007 failover consequence explicitly: an alert here may mean
*"fail over to a stale build."*

### 6. Security alert rules (Track B)
`docs/runbooks/security-alerts.md` (new)

Copy-pasteable Sentry rules. **C2 needs no code** — the tagged event already
ships. Document the exact match (`tag:spinr_alert=refresh_token_reuse`), plus
OTP-lockout spike (`spinr_auth_otp_lockout_total` exists) and admin-action
anomalies.

### 7. Metrics agent config + alerting runbook (Track B)
`docs/runbooks/metrics-alerting.md` (new), `backend/grafana-agent.yaml` (new, inert)

Grafana Cloud setup and all seven ADR-010 §3 rules as PromQL. Include the
correctness warning ADR-010 §2 stresses: always
`histogram_quantile(0.95, sum(rate(..._bucket[5m])) by (le))`, **never**
`avg(per_replica_p95)`. Document per-provider evaluation (§4) — loop metrics run
on both providers by design and must never be summed; scope loop alerts to
`provider="fly"` while C5 is open.

**⚠ Open decision — you did not pick, so Phase 1 does not decide it.** The agent
config lands as an inert file; **no `Dockerfile` or `fly.toml` edit** in this
phase.

- *Standalone Fly app* — avoids touching the digest-pinned, Trivy-scanned image
  (C6/CR-2026-002); costs Fly Machines-API discovery glue, since `.internal` DNS
  load-balances instead of fanning out.
- *Colocated process* — ADR-010's own recommendation; simpler (scrapes
  localhost, no discovery) but modifies the hardened image and may reopen the
  Trivy surface.

Recommendation: standalone, given live testing. Needs your call before Phase 4.

---

## Phase 2 — KPI + paging (not yet approved)

8. `backend/services/kpi_service.py` + test — compute the `CLAUDE.md` KPI table.
   Read-only. Rates are count ratios, but any **fare sum must use `Decimal`**
   per the money rule.
9. `backend/routes/admin/kpi.py` + `routes/admin/__init__.py` — `/admin/kpi` with
   target, actual, breach flag. Follow the module-gate convention documented at
   `routes/admin/__init__.py:29-42`.
10. `backend/core/lifespan.py` + kpi_service — loop publishing KPI gauges.
    ⚠ New background loop: runs on **every replica**; needs the replay-safety
    contract from the `spinr-background-loop` skill.
11. `admin-dashboard/.../dashboard/kpi/page.tsx` + api lib — target-vs-actual page.
    ⚠ Requires a real `npm run build`, not just `tsc --noEmit`.
12. `backend/utils/paging.py` + test — generalize `safety_paging` into a
    severity-routed (P0/P1/P2) pager. Same contract: `app_settings`-driven, dark
    by default, never raises.
13. `backend/utils/loop_alert.py` + `utils/safety_paging.py` — route both through
    it. ⚠ **Touches the SOS path**; must be provably behavior-preserving.
14. `backend/utils/sos_ack_monitor.py` + `core/lifespan.py` — SOS-unacknowledged
    re-page (ADR-010 §3 flags this as required follow-up).
    ⚠ **Safety-critical *and* a new loop** — the highest-risk item in this plan.
15. `docs/runbooks/on-call.md` (exists) — extend with severity matrix + escalation.
16. `backend/utils/security_events.py` — uniform `spinr_alert` tagging helper;
    wire OTP lockout and admin-action anomalies.

## Phase 3 — canary + analytics (not yet approved)

17. `backend/scripts/synthetic_ride_canary.py` + test — book→match→complete probe.
    ⚠ Creates **real rides**; staging-only hard guard, dedicated canary account,
    must never run against production. Needs `spinr-dispatch-reviewer`.
18. `shared/analytics/index.ts` + provider module — **PostHog self-hosted in
    ca-central-1** (your decision). The 11 call sites do not change; the stub was
    built as a drop-in swap point.
    ⚠ PIPEDA: residency is the reason for self-hosting — PostHog's managed
    EU/US regions would **not** satisfy it. Enforce the never-log list (no raw
    GPS, no full phone/name/email) at the provider boundary, reusing
    `shared/utils/pii.ts`. `CLAUDE.md` bans ad SDKs and behavioral retargeting.

## Phase 4 — operator actions (Track B, cannot be done from a sandbox)

Grafana Cloud account + remote-write key; deploy the agent (after the placement
decision); set `METRICS_AUTH_TOKEN` in Fly secrets (verify — it fails closed);
build the dispatch-P95 panel; enable the two day-one alert rules; create the
Sentry rules from runbook #6; provision the external prober; configure
`sos_paging_webhook_url` in `app_settings`.

---

## Verification

**Per subtask**
- `cd backend && pytest -m unit` for the touched module; `ruff check .` and
  `ruff format .`.
- Subtask 1: assert the `2000` bucket appears in `render_prometheus()` output and
  that `DEFAULT_MS_BUCKETS` consumers are byte-identical.
- Subtask 2: assert the gauge is epoch-based (`time() - gauge` is sane), and that
  existing staleness detection still uses monotonic.
- Subtasks 3–4: unit-test every probe's failure path (timeout, non-200, raise)
  asserting the probe returns rather than raises; assert no credential or
  hostname appears in the response body; assert production without
  `METRICS_AUTH_TOKEN` fails closed.

**Phase-level**
- `pytest -m "not slow"` clean before push.
- `curl localhost:$PORT/health` unchanged (byte-identical shape) — it is the
  platform liveness check.
- `curl -H "Authorization: Bearer $METRICS_AUTH_TOKEN" localhost:$PORT/metrics`
  and confirm the new gauges render in valid exposition format.
- Point a local Prometheus at `/metrics` and run the ADR-010 §3 dispatch
  expression to confirm it parses and returns.

**Required by `CLAUDE.md`**
- Subtasks 3–4 change the `/health` surface → **Change Impact & Risk Log entry
  required**, via `docs/templates/CHANGE_IMPACT_LOG.md` into
  `docs/change-log/YYYY-MM-DD-<slug>.md`.
- Blast-radius check before each: grep every caller of the touched helper.
- No automated PR review is running (C7 + C9) — a manual
  `spinr-security-auditor` pass is required for the `/health/dependencies`
  surface before merge.

**What this plan cannot verify**
- No Grafana Cloud account, Fly deploy, or Sentry UI access from this
  environment — Track B is delivered as documentation only and is unverified
  until an operator runs it.
- Alert rules are unproven against real traffic; false-fire behavior is unknown
  until they run (ADR-010 §5 calls this out).
- Dependency probes will be tested against **mocked** upstreams, not live
  Stripe/Twilio/Maps.
- No visual regression tooling exists for the admin dashboard (standing gap),
  so Phase 2 #11 would be reasoned about, not screenshotted.
