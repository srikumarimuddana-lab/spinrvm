# 09 — Roadmap & Launch Checklist

> **Read time:** ~15 min
> **Audience:** Everyone. Program mgmt lives here.

---

## Phased remediation plan (4–6 weeks)

### Phase 0 — "Stop the bleeding" (Week 1, ~5 eng-days)

**Goal:** Remove every defect that silently halts critical flows or allows data loss.

| # | Item | Doc | Owner |
|---|---|---|---|
| 0.1 | Set `min_machines_running = 1` + split worker process | D1 / B8 | DevOps + Backend |
| 0.2 | Fix dead code in `core/lifespan.py` (add real DB health check) | B1 | Backend |
| 0.3 | Add Stripe webhook idempotency (`stripe_events` table) | B2 | Backend |
| 0.4 | Add security-headers middleware | S1 | Backend |
| 0.5 | Swap in-memory rate limiter → Redis (Upstash) | S2 / P1 | Backend + DevOps |
| 0.6 | Fix duplicate migration prefixes + bootstrap Alembic | B4 | Backend |

**Exit criteria:** All six items deployed to staging, validated, then production. Zero P0 items from the Top-10 remain open.

---

### Phase 1 — "Identity & data integrity" (Week 2, ~6 eng-days)

| # | Item | Doc |
|---|---|---|
| 1.1 | Refresh token + access-token rotation; revocation list | S3 |
| 1.2 | Complete RLS coverage (20+ tables) | S4 |
| 1.3 | Migrate to Supavisor pooled Postgres endpoint | B7 |
| 1.4 | Add critical indexes (7-query list) | P4 |
| 1.5 | Move Stripe webhook processing to async queue | P7 |
| 1.6 | Deep health endpoint + `bg_task_heartbeat` | B9 / T15 |

**Exit criteria:** RLS audit SQL returns zero tables without policies. Refresh-token flow live behind a feature flag.

---

### Phase 2 — "Scale & observability" (Week 3, ~7 eng-days)

| # | Item | Doc |
|---|---|---|
| 2.1 | WebSocket fan-out via Redis pub/sub | B3 |
| 2.2 | Sentry mandatory (backend + mobile) + sourcemaps | T1 |
| 2.3 | Prometheus `/metrics` + Grafana dashboard | T3 |
| 2.4 | SLO doc + alert policy with burn rates | T2 |
| 2.5 | Load test baseline (k6) | T8 |
| 2.6 | Synthetic monitors (health + rider flow) | T9 |
| 2.7 | Log aggregation (Loki/Datadog) | T10 |

---

### Phase 3 — "Compliance & safety" (Week 4, ~6 eng-days)

| # | Item | Doc |
|---|---|---|
| 3.1 | Data retention policy + cron enforcement | C1 |
| 3.2 | PIPEDA doc + Privacy Officer designation | C2 |
| 3.3 | ToS / Privacy acceptance audit trail | C3 |
| 3.4 | Driver classification doc + IC agreement | C4 |
| 3.5 | PCI SAQ-A attestation | C5 |
| 3.6 | "Share ride" + in-app 911 button | C7 |
| 3.7 | Price-transparency breakdown in rider UI | C9 |

---

### Phase 4 — "Polish" (Weeks 5–6, ~8 eng-days)

| # | Item | Doc |
|---|---|---|
| 4.1 | i18n (en + fr-CA) across 3 apps | F3 |
| 4.2 | A11y pass on 12 critical mobile screens | F4 |
| 4.3 | Upgrade rider-app to Expo SDK 55; align deps | F1 |
| 4.4 | Offline/idempotent ride request + WS queue | F2 |
| 4.5 | Move surge/dispatch to PostGIS | B10 / P5 / P6 |
| 4.6 | Partition `gps_breadcrumbs` | P8 |
| 4.7 | EAS channel hygiene + staged rollout | D6 |
| 4.8 | Backup/DR + restore drill | D2 |
| 4.9 | Staging environment parity | D4 |
| 4.10 | Multi-region standby | D11 |

---

## Effort summary

| Severity | Count | Total effort (eng-days) |
|---|---|---|
| P0 | 10 | ~12 |
| P1 | 45 | ~30 |
| P2 | 25 | ~15 |
| P3 | 12 | ~5 |
| **Total** | **92** | **~62 (≈ 4–5 weeks with 3 engineers in parallel)** |

---

## Go / No-Go launch checklist

Before the first public paying user, every P0 must be ✅. Sign-off required by each owner.

### Engineering

- [ ] `min_machines_running ≥ 1` in fly.toml
- [ ] Worker process separate from API; both always-on
- [ ] DB health check runs on boot; boot fails on DB outage
- [ ] Stripe webhook idempotent with `stripe_events` table
- [ ] Security-headers middleware deployed
- [ ] Rate limiter backed by Redis
- [ ] Refresh token + revocation live
- [ ] RLS on every table in `public` schema
- [ ] WebSocket fan-out works across ≥2 machines (load-tested)
- [ ] Alembic migrations adopted; duplicate prefixes resolved
- [ ] All critical indexes applied
- [ ] Sentry DSN set in production; test error captured
- [ ] `/metrics` live + Grafana dashboard with 5 key gauges
- [ ] SLO doc published; alerts wired to PagerDuty
- [ ] Load test passed: 500 riders, 200 drivers, p95 <2s
- [ ] Synthetic monitor for `/health` + rider flow green for 72h

### Security

- [ ] TruffleHog + Trivy both green in CI
- [ ] No secret visible in image layers
- [ ] CSP tested against admin dashboard (no console errors)
- [ ] Penetration test scheduled (pre-launch internal + post-launch external)
- [ ] `SECURITY.md` published

### Compliance

- [ ] Data retention policy live + first nightly run passed
- [ ] PIPEDA doc published with Privacy Officer
- [ ] ToS / Privacy acceptance persisted with version
- [ ] Driver IC agreement counter-signed
- [ ] PCI SAQ-A attested
- [ ] Cancellation policy visible in app
- [ ] Fare breakdown visible in app

### UX / Mobile

- [ ] Rider & driver apps on same Expo SDK
- [ ] Offline-tolerant ride request flow passing manual test
- [ ] EAS OTA channels: staged rollout enabled
- [ ] A11y manual pass on VoiceOver + TalkBack
- [ ] French locale at 100% for launch screens

### Ops

- [ ] Incident-response runbook published; dry-run done
- [ ] On-call rotation live in PagerDuty
- [ ] Backup + restore drill completed; RTO/RPO recorded
- [ ] Rollback tested: deploy SHA-N, roll back to SHA-(N-1) in <5 min
- [ ] Staging env matches prod (Fly region, Supabase plan, Stripe mode)

### Business

- [ ] Support email (`support@spinr.app`) live; first 3 tickets resolved in training
- [ ] Driver background-check provider contracted
- [ ] Commercial insurance policy active; certificate on file
- [ ] Provincial / municipal TNC license filings complete (Saskatoon, Regina, etc.)

---

## Rollback plan

**Trigger:** any of:
- P1-sev incident open >15 min with user-facing impact on core flow.
- Crash-free users on current release <98% for 30 min.
- Payment success rate drops >5 pp vs. 7-day baseline.

**Steps:**

1. **Backend:** `flyctl releases rollback <SHA-prev>` (target both `app` and `worker` processes).
2. **Mobile:** EAS Update → publish prior `production` channel release. Target: OTA rollback <15 min.
3. **Admin:** Vercel → promote previous deployment (one click).
4. **DB:** **No migration rollback without DBA approval.** Migrations are forward-only; backward compat built-in. If a migration is the cause, restore PITR snapshot to a new DB + re-point backend (RTO <60 min).
5. **Comms:** Post-incident update within 30 min to status page, users notified if data loss possible.

Keep the prior release deployable for **at least 2 releases** before retiring (i.e., we can always roll back two versions without rebuild).

---

## Success criteria / post-launch (first 30 days)

| Metric | Target |
|---|---|
| `/health` uptime | ≥ 99.5% |
| Crash-free users (mobile) | ≥ 99.0% |
| Ride request p95 latency | < 2.0s |
| WS connection stability | ≥ 99.0% heartbeat success |
| Payment capture success | ≥ 99.5% |
| Dispatch accept rate within 60s | ≥ 90% |
| Open P1 security items | 0 |

If any metric trends below target for 72h → halt marketing spend, focus engineering.

---

## Long-term investments (Quarter 2+)

- Managed WebSocket (Ably / Pusher) instead of homegrown Redis pub/sub.
- Read-replica for analytics + dashboard.
- Multi-region failover.
- Fraud detection model (device fingerprinting, behavioral signals).
- Driver matching ML model (current logic is heuristic).
- Carbon accounting + sustainability report.
- In-house iOS & Android native modules for high-frequency location updates.

---

## Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| CTO | | | |
| Head of Engineering | | | |
| Security Lead | | | |
| Privacy / Compliance | | | |
| Product | | | |
| SRE / On-call Lead | | | |

---

*End of audit bundle. Return to → [00_INDEX.md](./00_INDEX.md)*
