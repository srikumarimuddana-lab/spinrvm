# 06 — Testing, Observability & Reliability

> **Read time:** ~15 min
> **Audience:** QA lead, SRE, Backend lead

---

## Executive verdict

Tests exist and target 80% backend coverage; Playwright+axe on admin is strong. **Observability** is the weaker half — Sentry optional, no `/metrics` endpoint, no SLOs defined, no incident-response runbook.

---

## P0 findings

### P0-T1 — Sentry is optional in production

**Evidence:** `backend/server.py` guards Sentry init behind `SENTRY_DSN` presence — if unset, backend runs without APM.

**Impact:** In a production incident, root-cause analysis relies on Loguru JSON logs scraped ad-hoc. Mean time to diagnose (MTTD) is 3–5× longer than with APM. Crash stack-traces from mobile users are invisible unless Crashlytics is also wired for JS errors (it is not by default).

**Fix (S):**
```python
# backend/core/middleware.py::_validate_production_config
if not settings.SENTRY_DSN:
    raise RuntimeError("SENTRY_DSN required in production")
```
Extend to mobile:
- Wrap the React root in a Sentry `ErrorBoundary`.
- `@sentry/react-native` alongside Crashlytics.
- Upload sourcemaps on every EAS build.

---

### P0-T2 — No SLOs, no alert policy

**Evidence:** No SLO doc, no uptime targets, no error-budget tracking.

**Impact:** Team cannot decide "is this on-call worthy?" objectively. Leads to alert fatigue or silent degradation.

**Fix (M):**
Define SLOs and publish:
| SLI | SLO | Measurement |
|---|---|---|
| Ride request availability | 99.5% / 28d | `POST /rides/request` 2xx + latency <2s |
| Driver dispatch success | 99.0% | `searching` → `driver_assigned` within 60s |
| Payment capture success | 99.9% | `payment_intent.succeeded` within 5 min of ride complete |
| WS uptime per driver | 99.0% | heartbeat gap <45s |
| Admin dashboard TTI | <3s (p75) | Lighthouse in CI |

Alert on **burn rate** (multi-window, multi-burn-rate). Host SLO math in Grafana/Honeycomb/Datadog.

---

## P1 findings

### P1-T3 — No metrics endpoint

**Evidence:** No `/metrics` Prometheus endpoint; no StatsD/OpenTelemetry export.

**Impact:** Cannot build a dashboard for request rate, latency percentiles, active drivers, active rides.

**Fix (M):**
- Add `prometheus-fastapi-instrumentator`. Expose `/metrics` (auth-gated).
- Scrape with Grafana Cloud / Prometheus.
- Key gauges: `active_drivers`, `active_rides_by_status`, `ws_connections`, `bg_task_last_run_seconds_ago`.
- Key counters: `ride_requests_total`, `ride_cancellations_total{reason=…}`, `payment_retries_total`, `webhook_events_total{type=…}`.

---

### P1-T4 — Test coverage not enforced

**Evidence:** `backend/pytest.ini` target stated as 80%; not verified to be CI-failing.

**Fix (S):** See D8 — gate CI on `--cov-fail-under=80` and publish coverage to Codecov/Coveralls.

---

### P1-T5 — Mobile test coverage is weak

**Evidence:** Jest + react-native-testing-library installed. No integration/E2E tests for mobile ride flow.

**Fix (M):**
- Add **Detox** or **Maestro** for end-to-end ride flow (rider requests → driver accepts → complete). Maestro (YAML-based, simpler) is a good fit.
- Run on EAS Build's hosted emulator for every PR touching `rider-app/` or `driver-app/`.

---

### P1-T6 — No contract tests between frontend and backend

**Fix (M):**
- Backend exposes OpenAPI via FastAPI (`/openapi.json`). Already available.
- Generate TS types with `openapi-typescript` into `shared/api-types.ts`.
- CI: fail if generated types differ from committed (enforces regen on API change).
- Add Pact-style consumer contract tests for high-risk endpoints (ride request, payment).

---

### P1-T7 — Incident-response runbook missing

**Fix (S):** Add `docs/runbooks/INCIDENT_RESPONSE.md`:
- Severity ladder (SEV1/2/3).
- On-call rotation (PagerDuty).
- "If Stripe webhooks are failing …" playbook.
- "If WS dispatch miss rate >10% …" playbook.
- Post-incident template with RCA, corrective actions, action-items tracked.

---

### P1-T8 — No load-test baseline

**Fix (M):**
- k6 or Locust script simulating peak hour: 500 riders, 200 drivers, 50 rps of requests.
- Run weekly against staging; publish p50/p95/p99 trend chart.
- Regression alarm if p95 grows >20% week-over-week.

---

### P1-T9 — No synthetic monitoring

**Fix (S):**
- UptimeRobot / Better Stack / Pingdom for `/health` from 3 regions.
- Checkly or Playwright Cloud to run a 6-step rider flow every 5 min.

---

### P1-T10 — Log aggregation not defined

**Evidence:** Loguru writes JSON to stdout. Fly collects, but querying is clunky.

**Fix (M):** Ship to Grafana Loki, Datadog, or Better Stack. Add structured fields: `request_id`, `user_id`, `ride_id`, `latency_ms`, `route`.

---

## P2 findings

### P2-T11 — No chaos/failure-injection

Periodically kill Redis, kill Stripe (via mock), drop WS; verify graceful degradation.

### P2-T12 — No feature-flag rollout rollback

`features.py` reads from DB. Add a one-click "kill-switch" admin UI per feature.

### P2-T13 — No user-session replay

Admin-dashboard and web — consider FullStory / Sentry Session Replay (mask PII).

### P2-T14 — Traces not correlated with logs

Add OpenTelemetry trace IDs to every log line. Propagate via `traceparent` header.

### P2-T15 — No "freshness" test for background tasks

Add a `bg_task_heartbeat` table: each loop writes `last_run_at`. `/health/workers` returns 503 if any heartbeat >2× expected interval.

---

## P3 findings

- **T16** — No mutation testing (mutmut/Stryker) — later-stage tech investment.
- **T17** — No a11y gate on mobile CI — mirror admin's axe gate via @axe-core/react.
- **T18** — No visual regression tests (Percy/Chromatic) on admin.

---

## Positive findings

- ✅ Playwright + axe-core on admin dashboard.
- ✅ Structured Loguru JSON logs.
- ✅ Request correlation IDs (`X-Request-ID`).
- ✅ pytest coverage target defined.
- ✅ CI emits a GitHub issue on failure + Slack notification.
- ✅ Trivy SARIF uploaded to GitHub Security tab.

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| T1 Sentry mandatory | P0 | S |
| T2 SLOs | P0 | M |
| T3 /metrics | P1 | M |
| T4 coverage gate | P1 | S |
| T5 mobile E2E | P1 | M |
| T6 contract tests | P1 | M |
| T7 runbook | P1 | S |
| T8 load test | P1 | M |
| T9 synthetic monitors | P1 | S |
| T10 log aggregation | P1 | M |
| T11–T15 | P2 | S–M |
| T16–T18 | P3 | S–M |

---

*Continue to → [07_PERFORMANCE_SCALABILITY.md](./07_PERFORMANCE_SCALABILITY.md)*
