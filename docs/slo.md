# Service-Level Objectives (SLOs)

**Purpose:** Define what "good enough" means for each user-facing and internal
Spinr surface. SLOs are the target; SLIs are the measurements; error budgets
drive release decisions.

**Owner:** `devops` + `backend` · **Review cadence:** quarterly + after any
CRITICAL incident.

**D17 dimension** · **D18 references these for RTO/RPO**

---

## Principles

1. SLOs are user-centric — we measure what riders and drivers actually
   experience, not internal implementation metrics.
2. Error budgets are consumed, not accumulated. Missing an SLO this quarter
   means we freeze non-critical releases until back on track.
3. SLOs are reviewed after every incident; if real impact was worse than
   SLO threshold, tighten or investigate why the SLI missed it.

---

## Rider-Facing SLIs / SLOs

| SLI | Definition | SLO target | Alerting threshold | Owner |
|---|---|---|---|---|
| Ride-request → driver-assigned latency | p99 time from `POST /rides` to `driver_assigned` WS event | ≤ 30 s | ≥ 45 s sustained 5 min | backend |
| Payment processing latency | p95 time from fare settlement to rider-visible "paid" state | ≤ 5 s | ≥ 10 s sustained 5 min | backend |
| Live-tracking update freshness | p95 time between successive driver GPS updates visible to rider | ≤ 3 s | ≥ 10 s | backend + mobile |
| Rider app cold-start time | p95 time from tap to interactive home screen | ≤ 3 s | ≥ 5 s | rider-app |
| Ride-creation success rate | `% POST /rides` returning 2xx when a driver is available | ≥ 99.5% | < 99% over 30 min | backend |
| Payment success rate | `% payment attempts` completing without manual intervention | ≥ 98% | < 95% over 1 h | backend |
| Rider crash-free sessions | Crashlytics sessions without crash | ≥ 99.5% | < 99% over 24 h | rider-app |

---

## Driver-Facing SLIs / SLOs

| SLI | Definition | SLO target | Alerting threshold | Owner |
|---|---|---|---|---|
| Ride-offer delivery latency | p95 time from dispatch decision to driver sees offer | ≤ 2 s | ≥ 5 s sustained 5 min | backend |
| Accept-button responsiveness | p95 time from accept tap to `driver_assigned` confirmation | ≤ 2 s | ≥ 5 s | backend |
| GPS-to-backend freshness | p95 time between driver GPS emit and backend receipt | ≤ 3 s | ≥ 8 s | backend + mobile |
| Earnings display accuracy | Rate at which driver-displayed earnings match backend within $0.01 | 100% | Any mismatch | backend |
| Payout initiation latency | Time from payout-trigger to Stripe Connect transfer created | ≤ 10 s | ≥ 30 s | backend |
| Driver crash-free sessions | Crashlytics | ≥ 99.5% | < 99% over 24 h | driver-app |
| WebSocket uptime | Time WS connection healthy while driver is online | ≥ 99% | Disconnects > 3 per hour | backend |

---

## Backend / Infra SLIs / SLOs

| SLI | Definition | SLO target | Alerting threshold | Owner |
|---|---|---|---|---|
| API availability | `% successful responses` across all public endpoints | ≥ 99.9% over 30 days | < 99.5% over 1 h | backend + devops |
| API p99 latency | p99 response time across the `/rides`, `/drivers`, `/payments` endpoints | ≤ 500 ms | ≥ 1 s sustained 10 min | backend |
| Supabase availability (dependency) | Tracked via status page | ≥ 99.9% | Any user-facing impact | infra |
| Redis availability | `/healthz` Redis check passing | ≥ 99.9% | < 99.5% | devops |
| Stripe webhook processing | `% webhook events processed within 30s of receipt` | ≥ 99% | < 95% over 1 h | backend |
| Background-loop liveness | All 7 `lifespan.py` loops emit heartbeat every cycle | 100% | Any silent loop > 10 min | backend + devops |
| Daily reconciliation delta | Stripe↔DB↔wallet delta after daily cron | $0.00 | Any delta > $1 | backend |

---

## Admin-Facing SLOs (operational)

| SLI | Definition | SLO target | Alerting threshold | Owner |
|---|---|---|---|---|
| Admin dashboard TTFB | p95 time-to-first-byte for main admin pages | ≤ 500 ms | ≥ 1 s | admin |
| Bulk-op response time | p99 time to execute a bulk admin action (scoped ≤ 10k records) | ≤ 30 s | ≥ 60 s | backend + admin |
| Admin-action audit log write | % admin actions that produce an audit_log row | 100% | Any missing | backend |

---

## RTO / RPO (D18 cross-reference)

| Scenario | RTO (recovery time) | RPO (data loss window) |
|---|---|---|
| Supabase outage (full) | 4 h | 5 min (PITR granularity) |
| Redis outage | 15 min (graceful degradation) | N/A (cache only) |
| Backend process crash | 30 s (autoscale restart) | 0 (stateless) |
| Stripe outage | 1 h before user impact (queued) | 0 (webhook queue) |
| Entire region outage | 8 h (manual failover) | 15 min |

---

## Error Budget Policy

Each SLO has an implicit error budget. For a 99.9% target over 30 days, the
budget is ~43 minutes of downtime.

**Actions when budget is 50% consumed (yellow):**
- File a `yellow-budget` incident in tracker
- Review recent deploys for contributor causes
- Defer non-critical releases

**Actions when budget is 100% consumed (red):**
- **Freeze all non-P0 releases** until back under budget
- Mandatory post-mortem for each contributing incident
- Engineering leadership reviews budget weekly until green

**Budget reset:** rolling 30-day window (not calendar month).

---

## Alerting Channels

| Severity | Channel | Response target |
|---|---|---|
| CRITICAL (user-facing outage) | PagerDuty page + `#incidents` | Acknowledge ≤ 5 min |
| HIGH (degraded SLO, burst) | `#incidents` + Slack tag `@oncall` | Acknowledge ≤ 15 min |
| MEDIUM (budget warning, yellow) | `#engineering-alerts` | Review next business day |
| LOW / info | Dashboard only | Review weekly |

---

## Measurement Infrastructure

**Current state (2026-04-24):**
- Supabase provides its own status/metrics
- Stripe has its own dashboards
- Firebase Crashlytics for mobile
- Backend has ad-hoc logging (no structured request_id end-to-end — this is
  an open D17 item)

**Required for production SLO tracking:**
- [ ] Structured JSON logging with `request_id` threaded end-to-end
- [ ] Metrics pipeline (Prometheus, Datadog, or equivalent) aggregating SLIs
- [ ] Error-budget dashboard (monthly + 30-day rolling windows)
- [ ] Burn-rate alerting (Page when error budget is consumed at > 10× normal rate)
- [ ] Status page auto-updated from health checks

**These are open gaps** that will be filed as OPEN-ITEMS-TRACKER entries after
the backend Phase E audit runs D17 against the current implementation.

---

## Review Cadence

- Monthly: SLO attainment report in `reports/slo-reports/YYYY-MM.md`
- Quarterly: SLO target review (tighten or relax as product matures)
- After every incident: adjust SLIs if they missed the real user impact

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial SLO definitions; measurement infra gaps noted | audit-framework |
