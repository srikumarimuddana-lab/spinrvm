# Dimension 18 — Disaster Recovery & Business Continuity

**Question:** If Supabase / Stripe / Redis / Railway goes down, or a bad release corrupts data, can we recover — and how long does it take?

---

## Checklist

### Recovery targets (declared + tested)
- [ ] **RTO** (recovery time objective) documented per surface — e.g. dispatch ≤ 15 min, admin ≤ 2 h
- [ ] **RPO** (recovery point objective) documented — acceptable data-loss window
- [ ] Both targets tested at least annually — not only written down

### Database (Supabase / Postgres)
- [ ] Point-in-Time Recovery (PITR) enabled and retention window ≥ 7 days
- [ ] Supabase region is **Canadian** (PIPEDA-comfortable) — verify in project settings
- [ ] Daily logical backup exported off-Supabase (compliance audit trail)
- [ ] Migration rollback procedure documented for each migration (not just apply)
- [ ] Test restore drill executed in the last 90 days — evidence file exists

### Redis
- [ ] Managed Redis (Railway / Upstash) with persistence on, not best-effort memory
- [ ] Replica + automatic failover configured
- [ ] Rate-limit / OTP-lockout in-memory fallback is **alerting**, not silent — Redis failure is a declared incident
- [ ] Redis data classes documented: which keys survive restart, which are ephemeral

### Application (Railway / Render)
- [ ] Multi-replica deployment — single instance down ≠ outage
- [ ] Graceful shutdown: in-flight requests complete; WS connections drained
- [ ] Health endpoint returns 503 during shutdown (load balancer drains before SIGKILL)
- [ ] Deployment rollback ≤ 5 min — rollback button documented + tested
- [ ] Feature-flag kill switches for: driver onboarding, dispatch, payments, wallet

### External dependencies (graceful degradation)
- [ ] Stripe down → ride completes, fare queued for settlement; rider told "payment will retry"
- [ ] Twilio down → SMS OTP unavailable, but app check-in + email fallback if configured
- [ ] Firebase FCM down → app falls back to in-app banner + WS-delivered notifications
- [ ] Google Maps quota exhausted → cached estimates + fallback routing; rider warned
- [ ] Circuit breakers on every external call with explicit budget (timeout, max retries)

### Data export / portability
- [ ] Full schema + data export procedure documented (for vendor migration)
- [ ] Critical data (rides, payments, driver PII) can be reconstituted outside Supabase if needed

### Incident response
- [ ] Runbook per declared incident class (Stripe down, Redis down, Supabase outage, mass dispatch failure)
- [ ] On-call rotation with primary + secondary + escalation
- [ ] Communication templates (Slack / email / status page / regulator notice for PIPEDA breach)
- [ ] Post-incident review cadence; retro doc per sev-1/sev-2

### Chaos / drill cadence
- [ ] Backup restore drill ≥ 1×/year
- [ ] Regional failover test ≥ 1×/year
- [ ] Dependency kill drill (disable Redis in staging and observe) ≥ 1×/quarter
- [ ] "Full outage" tabletop ≥ 1×/year

---

## Common Findings

- **"We have backups but we've never restored them"** — PITR enabled, but restore path untested.
- **"RTO/RPO numbers live in a Notion doc nobody has read"** — not operationalised.
- **"Redis is in-process fallback in prod"** — ground-rules allow fallback in dev only.
- **"Stripe outage stopped all rides"** — no degraded-mode flow for queued settlement.
- **"No rollback procedure for the last 5 migrations"** — forward-only migrations are a single-point-of-failure.

## How to Test

```bash
# Check for circuit breakers on external calls
grep -rn "circuit_breaker\|httpx\.Timeout\|max_retries\|backoff" \
  backend/routes/payments.py backend/routes/webhooks.py backend/features.py

# Graceful shutdown wiring
grep -n "on_shutdown\|lifespan\|SIGTERM" backend/core/lifespan.py \
  backend/server.py

# Migration rollback policy
ls backend/migrations/ | head -20
# Each "NN_*.sql" should have a matching "NN_*_rollback.sql" or a documented rollback SQL in a comment.

# Health probes
grep -rn "/health\|/ready\|/live" backend/routes/ backend/server.py
```

## Regulatory tags
`PIPEDA` (data-residency on Canadian region; breach-notification runbook) · `SOC2` (change mgmt, availability, IR) · `SK-TNC` (service continuity for municipal permits) · `SGI` (insurance/dispatch continuity)
