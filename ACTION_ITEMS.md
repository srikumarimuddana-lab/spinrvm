# Spinr — Production Readiness Action Items

> **How to use this file (for Claude / any AI session):** pick the highest-priority
> `[ ]` item, read its *Files* and *Acceptance* fields, implement it following
> `CLAUDE.md` conventions (≤3 files per subtask, one logical change per commit,
> tests required), then flip it to `[x]` with the PR/commit reference in the
> *Done* column. Do not re-litigate `[x]` items. Companion document with full
> context: `docs/PRODUCTION_READINESS.md`.

_Last updated: 2026-06-09 (branch `claude/rideshare-analysis-optimization-zjhsyb`)._

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
- [ ] **Status:** open (latent PIPEDA violation — no production callsite yet)
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
