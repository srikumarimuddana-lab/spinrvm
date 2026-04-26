# Runbook — Supabase Outage Response

**Owner:** `devops` + `backend` · **Severity:** CRITICAL (org-wide)
**D18 dimension** · **Target availability:** 99.9%

---

## Detection

Automatic alerts trigger from:
- `/healthz` endpoint returning non-200 (Supabase check fails)
- Background-loop heartbeats stop (backend/core/lifespan.py)
- Elevated 503 rate from `db_supabase.py` error paths
- Supabase status page alert (`https://status.supabase.com`)

Escalation channel: `#incidents` + PagerDuty on-call rotation.

---

## Triage (first 5 minutes)

1. [ ] Confirm scope: is it Supabase auth, database, storage, realtime, or all?
   Check status.supabase.com and Supabase dashboard.
2. [ ] Confirm impact: how many users affected? Measure via 5xx rate on
   `/rides`, `/auth/verify-otp`, `/payments`.
3. [ ] Open an incident doc: `reports/incidents/YYYY-MM-DD-supabase-<slug>.md`
4. [ ] Post initial update to status page: "Investigating elevated errors"

---

## Mitigation Options (in order of preference)

### Option 1: Wait for Supabase recovery (most outages)
If Supabase status page shows active incident and ETA < 30 min:
- Surface in-app banner: "We're experiencing issues. New rides disabled."
- Disable new ride creation (`POST /rides` returns 503)
- Allow in-progress rides to continue (WebSocket + Redis state)
- Continue polling status page every 5 min

### Option 2: Failover to read-replica (if configured)
If available:
- Route read-only traffic (ride history, earnings) to replica
- Write path stays down; log writes to Redis queue for replay
- **Caveat:** Replica failover is NOT yet implemented (open item — needs spec)

### Option 3: Degraded-mode service
If Supabase primary is unreachable but cached state is warm:
- Serve cached user profiles from Redis (15 min stale OK)
- Disable wallet top-up, payment-method add (writes required)
- Allow active-ride completion via in-memory state + write-back queue
- All writes queued to Redis list `spinr:writequeue:pending` for replay

### Option 4: Staging data-plane switch (worst case)
If outage > 2 h and Supabase confirms extended downtime:
- Cannot route traffic to staging (different data); staging is dev-only
- Pause service entirely; announce maintenance; wait for Supabase
- Regulatory note: if > 4 h impact, prepare PIPEDA/OPC breach-notification draft
  (availability is a covered principle)

---

## Communication Template

**Status page / in-app banner:**
```
⚠️ Service Disruption
Some features are temporarily unavailable due to a database issue with our
provider. Active rides are not affected. We'll update every 15 minutes.
Last updated: HH:MM UTC
```

**User email (if > 1 h):** Draft in `docs/incident-response.md` templates.

---

## Post-Incident

Within 72 hours:
- [ ] Publish incident post-mortem: `reports/postmortems/YYYY-MM-DD-supabase.md`
- [ ] Check if PIPEDA availability notification required (> 4 h + PII affected)
- [ ] File action items in OPEN-ITEMS-TRACKER (infra, data, backend owners)
- [ ] Refresh this runbook with any new learnings
- [ ] If any data write was lost: surface via SOS / compliance flow

---

## Drill

Tabletop annually (see `docs/external-testing.md` § 6).
Scenario: "Supabase down 6 h" — walk through Options 1–4.

---

## Known Dependencies

- **Redis**: serves degraded-mode cached reads. Its own runbook at `docs/runbooks/redis-down.md`.
- **Stripe**: independent; payment flows may still function if Stripe is up.
- **Firebase**: independent; auth via Firebase path remains if OTP Supabase path fails.
