# Spinr — Production Readiness Action Items

> **How to use this file (for Claude / any AI session):** pick the highest-priority
> `[ ]` item, read its *Files* and *Acceptance* fields, implement it following
> `CLAUDE.md` conventions (≤3 files per subtask, one logical change per commit,
> tests required), then flip it to `[x]` with the PR/commit reference in the
> *Done* column. Do not re-litigate `[x]` items. Companion document with full
> context: `docs/PRODUCTION_READINESS.md`.

_Last updated: 2026-06-09 (branch `claude/rideshare-analysis-optimization-zjhsyb`). Sections: A=launch-gating, B=pre-launch fixes, C=operational, D=post-launch, E=industry-parity._

---

## P0 — Launch gating (code)

### A1. Per-module test-coverage floors for money paths
- [ ] **Status:** open — the single biggest remaining gap
- **Why:** CLAUDE.md mandates ≥90% for `routes/payments.py` + `services/fare_service.py`
  and ≥80% for `routes/rides.py` + `services/dispatch_service.py`; the global floor
  in `backend/pytest.ini` is only 60%.
- **Files:** `backend/pytest.ini`, new tests under `backend/tests/`
- **Approach:** measure current per-file coverage (`pytest --cov --cov-report=term-missing`),
  write tests for the uncovered branches (fare tiers, surge, corporate, promo, refund,
  webhook types), then enforce with `coverage report --fail-under` per path or a
  `ci-guardrails` step. Ratchet, don't big-bang.
- **Acceptance:** CI fails if payments/fare coverage drops below 90% or rides/dispatch below 80%.

### A2. Post-deploy smoke test in CI
- [ ] **Status:** open
- **Why:** deploys to Fly/Railway succeed or fail silently; a bad deploy is currently
  discovered by users. The smoke script from PR #172 already exists.
- **Files:** `.github/workflows/deploy-fly.yml`, `.github/workflows/deploy-backend.yml`
- **Approach:** add a job after deploy that curls `/health`, exercises auth (expect 401
  not 500), and the fare-service health path with `--fail-with-body`; page on failure.
- **Acceptance:** a deliberately broken deploy turns the workflow red within minutes.

### A3. PIPEDA breach record register
- [ ] **Status:** open (regulatory)
- **Why:** referenced by `docs/runbooks/data-breach.md` but never created; PIPEDA
  requires a 24-month breach record.
- **Files:** create `docs/audit/breach-record.md`
- **Acceptance:** template with columns (date, scope, RROSH assessment, notified?,
  evidence location) and a "no entries to date" first row.

## P1 — Fix before launch (code)

### B1. `track_driver_online` accepts raw GPS for third-party analytics
- [x] **Status:** done — geohash-string-only signature; lat/lng dict raises
  TypeError, non-geohash string raises ValueError; contract pinned in
  `backend/tests/test_analytics_geohash.py`
- **Files:** `backend/utils/analytics.py:346`
- **Approach:** change the signature to accept a geohash string only; never accept
  or forward a lat/lng dict to Mixpanel/Amplitude. Add a test pinning the contract.
- **Acceptance:** no analytics interface accepts raw coordinates; test added.

### B2. Disputes store full legal names + RLS too broad + rounding
- [ ] **Status:** open (3 related fixes, one migration + one route change)
- **Files:** `backend/routes/disputes.py:188` (full name in response/at rest),
  `backend/routes/disputes.py:227` (`int(...)` without `_round()` floors refund cents),
  new migration (next free slot — check `backend/migrations/`) replacing the
  `FOR ALL TO authenticated` policy from `10_disputes_table.sql` with enumerated
  SELECT/UPDATE + append-only trigger (pattern: `audit_logs`, migration 51).
- **Acceptance:** disputes responses carry `user_id`/display alias only; refund cents
  use `int(_round(amount * 100))`; DELETE on disputes blocked at DB level.

### B3. Driver location-update hot path (perf + Maps spend)
- [ ] **Status:** open — threatens the <150ms location-write SLA
- **Files:** `backend/routes/websocket.py:601-688`, `backend/utils/breadcrumbs.py`
- **Approach (3 sub-items, separate commits):**
  1. cache the driver's active ride in Redis (5s TTL) so `persist_ride_breadcrumbs`
     doesn't re-query per ping;
  2. recompute Google Maps ETA only when the driver moved >100m since the last
     calc (Redis key `driver:{id}:last_eta_loc`);
  3. batch breadcrumb writes (~10 points / 10s) instead of per-ping.
- **Acceptance:** ≤1 Maps ETA call per driver per 100m moved; no per-ping rides query;
  breadcrumb correctness covered by existing tests.

### B4. WS per-user rate limit is per-replica only
- [ ] **Status:** open (acknowledged P3 in code; formalized here)
- **Files:** `backend/socket_manager.py:27-37`
- **Approach:** promote the message counter to Redis (`INCR` + `EXPIRE` 1s window)
  with in-process fallback when Redis is absent.
- **Acceptance:** cap holds at N msg/s per user across all replicas.

## P2 — Operational (no/low code — needs a human with dashboard access)

### C1. Failover drill — Railway ↔ Fly
- [ ] **Status:** never exercised
- **Action:** run the cutover in `docs/runbooks/railway-fly-failover.md` end-to-end
  in a low-traffic window; record actual timings and surprises back into the runbook.

### C2. Sentry alert rule — refresh-token theft tripwire
- [ ] **Status:** open (~5 min in Sentry UI, no code)
- **Action:** alert on message `REFRESH TOKEN REUSE DETECTED` → email/PagerDuty.
  The loguru→Sentry bridge already delivers it; it just needs a rule.

### C3. Production env sweep on Fly/Railway
- [ ] **Status:** partially done (SENTRY_DSN deployed via Fly Sentry extension — verify
  boot log shows "Sentry SDK initialized for error monitoring")
- **Action:** confirm `SENTRY_DSN`, `REDIS_URL`/`RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL`,
  Firebase App Check enforcement, and `ENV=production` are set on **both** providers
  (standby drifts silently).

### C4. Staff MFA rollout comms
- [ ] **Status:** code shipped; people not yet notified
- **Action:** tell all admin staff that the next login forces authenticator enrollment
  (ADMIN_MFA_ENFORCED). Ensure ≥2 active super_admin accounts exist for the
  lost-phone reset path.

## P3 — Post-launch backlog (tracked, not gating)

- [ ] **D1. PostGIS surge query** — `surge_engine.py` caps at 500 drivers with Python
  point-in-polygon; move the count server-side when driver count approaches the cap.
- [ ] **D2. Distributed tracing** — request-ID propagation exists (`X-Request-ID`);
  full OpenTelemetry only if multi-replica latency debugging becomes painful.
- [ ] **D3. Driver destination mode** — biggest driver-retention feature gap vs industry.
- [ ] **D4. Driver heatmap UI** — `utils/demand_forecast.py` exists server-side; surface
  it in the driver app.
- [ ] **D5. In-app VoIP calls** — Twilio Proxy PSTN masking already covers the need;
  VoIP is a cost/quality upgrade.
- [ ] **D6. Read-only root filesystem** — blocked on host migration off Railway.
- [ ] **D7. Admin analytics Redis cache** — 5-min cache on cancellation-breakdown
  (`routes/admin/analytics.py:72`), drops dashboard DB load ~98%.
- [ ] **D8. Payment-retry admin alert via WS broadcast** — replace per-admin push loop
  (`utils/payment_retry.py:80`) with one `broadcast_to_admins`.

## P4 — Industry-parity good-to-haves (verified missing 2026-06-09)

_Not launch-gating, but every mature platform at this stage has them. Ordered by
how much they de-risk a public launch._

- [ ] **E1. Staging environment** — deploys currently go `main` → production
  (Fly + Railway) with no intermediate environment. Stand up a staging Fly app +
  throwaway Supabase project with synthetic data; point a `staging` branch or
  manual workflow at it. Prereq for E2, E4, and safe migration rehearsal.
- [ ] **E2. Marketplace load/simulation testing** — `perf_baseline.py` is
  micro-benchmarks only; there is no k6/Locust harness and no rider/driver bot
  simulator. For a dispatch marketplace this is the industry-standard way to find
  the breaking point of matching, WS fan-out, and surge under load *before* a
  Saturday night does. Build: bot harness creating N concurrent ride requests +
  M online drivers against staging; assert the SLA table in CLAUDE.md holds.
- [ ] **E3. Forced-upgrade gate for mobile apps** — no minimum-supported-version
  check exists. Old app binaries in the wild will eventually hit removed/changed
  APIs. Add `min_supported_version` to `app_settings`, a version header from the
  apps, a 426-style backend response, and an "update required" screen in both apps.
  Cheap now, impossible to retrofit onto clients that are already old.
- [ ] **E4. Synthetic monitoring + SLO alerting** — nothing external probes the
  platform; a total outage is currently discovered by users. Add an external
  monitor (Checkly/UptimeRobot/Grafana synthetic) hitting `/health`, auth, and
  fare-estimate every minute from outside, alerting to PagerDuty. Tie alert
  thresholds to the CLAUDE.md SLA table (SLO + error budget).
- [ ] **E5. Kill switches / feature flags** — `app_settings` covers config, but
  there are no documented kill switches for the risky subsystems (surge engine,
  scheduled dispatch, promo redemption, corporate billing). Add boolean flags
  checked at the top of each loop/path + admin UI toggles, so a misbehaving
  subsystem can be disabled in seconds without a deploy.
- [ ] **E6. Pre-launch DAST + third-party pentest** — SAST/Semgrep run in CI, but
  nothing exercises the running app (OWASP ZAP baseline scan against staging on a
  schedule), and a payments+PII platform should have one external penetration
  test before public launch. Budget item; book it.
- [ ] **E7. Backup-restore drill** — `docs/runbooks/pitr-restore.md` exists but
  (like the failover runbook) has never been exercised. Restore a Supabase PITR
  snapshot into a scratch project, verify row counts + a sample ride lifecycle,
  record actual RTO in the runbook. A backup is only real after a restore.
- [ ] **E8. CODEOWNERS + review routing** — no `.github/CODEOWNERS`. Route
  `backend/routes/payments*`/`services/fare*`/`migrations/` to designated
  reviewers so money/schema changes can't merge on a drive-by approval.
- [ ] **E9. Blameless postmortem template** — `data-breach.md` has one for
  breaches; generalize to `docs/templates/postmortem.md` (timeline, impact,
  5-whys, action items with owners) and link it from the incident runbooks.
- [ ] **E10. License compliance scan** — dependency *vulnerability* audit exists;
  add license checking (`pip-licenses` + `license-checker` in CI, fail on
  GPL/AGPL in shipped surfaces). Matters for SOC 2 and any future diligence.
- [ ] **E11. a11y checks in CI** — WCAG 2.1 AA is a stated regulatory mandate and
  axe is already in admin-dashboard devDeps, but nothing runs it in CI. Wire
  axe into the Playwright E2E suite for the customer-facing surfaces.
- [ ] **E12. On-call & escalation policy doc** — PagerDuty is referenced by
  alerts, but there is no rotation/escalation/severity-matrix document. One page:
  who is paged, when P0 vs P1, response-time expectations (support SLA says <2h P1).

## Recently completed (do not redo)

| Item | Where |
|---|---|
| `/auth/refresh` reported 30-day `access_expires_at` (real TTL 15 min) | `b5648ba` |
| SOS accepts expired-but-signature-valid token (`get_current_user_allow_expired`) | `rides.py:4178` |
| `confirm_payment` raw dict → Pydantic model | `routes/payments.py:303` |
| Dispatch pushes moved off the request path (<2s offer SLA) | `e9283fc` |
| Estimate polyline fetch overlapped with fare work (<300ms SLA) | `d322709` |
| Partial recency index for estimate driver page | `5788367` |
| Admin JWT error-detail leaks fixed + MFA challenge audience pinned | `e3281ed` |
| MFA enforced for all staff logins (`ADMIN_MFA_ENFORCED`, enroll-scoped token) | `test_admin_mfa_enforcement.py` |
| Super-admin MFA reset (lost phone) + staff-page UI | `884b091`, `664d195` |
| TOTP secrets / backup-code hashes stripped from staff list/get | `664d195` |
| Production boot without `SENTRY_DSN` logs unmissable ERROR | `server.py` |
| All 6 sprint P0s + P1/P2 audit findings | `.claude/context/sprint-current.md` |
