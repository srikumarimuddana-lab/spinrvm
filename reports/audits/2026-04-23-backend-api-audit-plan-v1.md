# Backend API — Production-Readiness Audit Plan v1

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Scope:** `backend/` (all routers, services, utils, middleware, migrations) — **excludes** `backend/routes/admin/` (separate audit)
**Framework:** `audit-framework/` — ground-rules.md applies
**Follows:** Driver App v4 (2026-04-18), Rider App v1 (in progress) — same methodology
**Module scope file:** `audit-framework/modules/backend-api.md`

---

## Prompt (paste into a fresh agent call)

```
You are running a production-readiness audit of the Spinr backend API.

CONTEXT
- Read CLAUDE.md (project root) for critical conventions: Decimal-only money,
  dual-import pattern, ride-state guards, race-condition filter on ride accept,
  JWT trust model (admin vs rider/driver), Stripe idempotency, OTP hashing,
  Redis transparency, WebSocket auth handshake, background task safety.
- Read audit-framework/ground-rules.md BEFORE flagging anything. Do not reflag
  items already marked intentional there (e.g. 4-digit OTP, "1234" dev bypass,
  test Stripe keys in dev).
- Read audit-framework/modules/backend-api.md for the applicable-dimension list.
- Graphify knowledge graph lives at graphify-out/. Read GRAPH_REPORT.md first.

SCOPE
- All files under backend/ EXCEPT backend/routes/admin/ (audited separately).
- Backend-exposed WebSocket: backend/routes/websocket.py, socket_manager.py,
  utils/ws_pubsub.py.
- Background loops started in backend/core/lifespan.py (surge, dispatch,
  payment retry, doc expiry, corporate auto-topup, low-balance nudge,
  allowance reset).
- Migrations + RLS policies under backend/migrations/.

DIMENSIONS TO RUN (from audit-framework/dimensions/, API-applicable only)
  01 Feature completeness
  02 Authentication & session
  03 Encryption & secrets
  04 Input validation
  07 State machine & dispatch
  08 Payments & earnings
  09 Test coverage
  10 Error handling & resilience
  11 Security headers & CORS
  12 Compliance (PII/PCI/PIPEDA)
  14 Performance & scalability
  (Skip 05, 06 GPS-UI, 13 notification-UI, 15, 16 — UI-only.)

SEVERITY SCALE
  CRITICAL | HIGH | MEDIUM | LOW | PASS | RECOMMENDATION
  Match the rider-app-v1.txt finding format exactly.

DELIVERABLE
  reports/audits/2026-04-23-backend-api-v1.txt
  Use audit-framework/templates/audit-output.txt as the skeleton. One section
  per dimension. Record PASS results too — not just failures.

GROUND RULES (must-read before writing any finding)
- Do NOT silently swallow errors. If backend code replaces a failing call with a
  generic fallback that hides the symptom, that's a HIGH finding (see CLAUDE.md).
- `logger.warning` on a DB/auth/payment error is a finding. Must be logger.error
  with the underlying exception (DatabaseError.details["original"]).
- Money arithmetic: any `float(`/`+ 0.01`/`* 1.13` in fare code = CRITICAL.
  All fare math must go through `_d()`, `_round()`, `_f()`.
- Ride state transitions without `_require_ride_in_state()` = HIGH.
- Supabase updates on `rides` without the `{'status': 'searching'}` filter on
  accept = CRITICAL race condition.
- Stripe webhooks without `claim_stripe_event()` idempotency = CRITICAL.
- Background loops in backend/core/lifespan.py that are NOT replay-safe across
  replicas = HIGH.
- OTP stored in plaintext anywhere = CRITICAL; OTP SHA-256 hash at rest = PASS.
- Rate limiter / OTP lockout in in-process dict without REDIS_URL in prod = HIGH.
- CORS with `allow_origins=["*"]` in production = CRITICAL.
- Any non-admin route that trusts the `role` claim in JWT = HIGH.
```

---

## Branch Strategy

```bash
# Audit findings branch (current)
git checkout claude/review-pending-audits-Pu1aP

# Remediation branches — one per sprint after findings are tallied
git checkout -b fix/backend-p0-critical
git checkout -b fix/backend-p1-before-beta
git checkout -b fix/backend-p2-before-launch
git checkout -b fix/backend-p3-hardening
```

---

## Pre-Audit Scans (record at top of findings file)

```bash
# 1. Python dependency vulnerabilities
cd backend && pip-audit 2>&1 | tail -20

# 2. Live secrets / key leaks
grep -rn "sk_live_\|SUPABASE_SERVICE_ROLE\|postgres://" backend/ \
  --include="*.py" --include="*.env" -l

# 3. Float usage in money code (must be zero outside of core/config)
grep -rn "float(" backend/routes/ backend/services/ backend/utils/fare*.py \
  --include="*.py"

# 4. Ruff baseline
cd backend && ruff check . 2>&1 | tail -20

# 5. Test coverage baseline
cd backend && pytest --cov=backend --cov-report=term 2>&1 | tail -10

# 6. Bare `except:` and `except Exception:` (error-swallowing smell)
grep -rn "except Exception" backend/ --include="*.py" | wc -l
grep -rn "except:" backend/ --include="*.py" | wc -l

# 7. `logger.warning` on DB/auth/payment paths
grep -rn "logger\.warning" backend/routes/auth.py backend/routes/payments.py \
  backend/routes/rides.py backend/routes/webhooks.py backend/db_supabase.py

# 8. Pydantic model coverage for routes (are all request bodies typed?)
grep -rn "def .*request: dict" backend/routes/ --include="*.py"

# 9. Missing idempotency on Stripe webhooks
grep -n "claim_stripe_event" backend/routes/webhooks.py

# 10. Background loops declared in lifespan
grep -n "create_task\|asyncio\.create_task" backend/core/lifespan.py
```

---

## Phase Breakdown (run as separate agent calls to fit context)

| Phase | Dimensions | Focus | Est. |
|-------|------------|-------|------|
| A | 01, 02, 03, 04 | Feature completeness + Auth + Secrets + Input validation | 8–11 h |
| B | 07, 08, 11 | Ride state machine + Stripe + Security headers/CORS/rate limits | 7–10 h |
| C | 09, 10, 12 | Test coverage + Error handling + PIPEDA/PCI/RLS | 8–12 h |
| D | 14 | Performance: N+1 queries, index coverage, WS fan-out, BG loops | 2–3 h |
| **E** | **17, 18, 19, 20, 21, 22** | **Observability · DR/BCP · Fraud · Reconciliation · Threat model · Third-party** | **8–12 h** |
| **Total** | **17** | | **~33–48 h** |

### Phase A sub-tasks (D01–D04)
- A.1 Feature completeness: inventory routers mounted in `backend/server.py`; confirm every public route has a Pydantic model, auth dependency, and docstring.
- A.2 Authentication: walk `routes/auth.py` end-to-end (OTP send → verify → JWT issue → refresh → revoke → logout-all); audit `utils/refresh_tokens.py`, `utils/crypto.py`.
- A.3 Encryption & secrets: `core/config.py` startup validation; Supabase Vault use (migration 32); SAFE-* expiry vs dispatch enforcement.
- A.4 Input validation: every route's Pydantic schema; `validators.py` (GPS, phone, money); webhook signature windows; file upload magic-byte.

### Phase B sub-tasks (D07–D08, D11)
- B.1 Ride state machine: `_require_ride_in_state_*` coverage on every transition; atomic accept (`claim_ride_atomic`); CANCELLED vs TRIP_STARTED invariant; WS event parity per transition.
- B.2 Payments: Stripe `PaymentIntent` idempotency; webhook `claim_stripe_event`; timestamp-tolerance window; Connect transfer flow; corporate wallet delta via `corporate_wallet_apply_delta` only.
- B.3 Security headers & CORS: `core/middleware.py` allow_origins (prod enumerated), HSTS, CSP, X-Content-Type-Options; SlowAPI per-route limits; App Check middleware enforcement.

### Phase C sub-tasks (D09–D10, D12)
- C.1 Test coverage: `pytest --cov` baseline vs gate (P3-4 depth); per-router test count; backend regression-test coverage of each closed P0–P3 item.
- C.2 Error handling: `utils/error_handling.py` exception hierarchy; `request_id` propagation; no `logger.warning` on DB/auth/payment (CLAUDE.md rule); 503 vs 502 vs 500 discipline.
- C.3 Compliance: RLS per table (`migrations/`); PII column encryption + REVOKE (migration 32); DSAR endpoint `/drivers/me/export-data` coverage + 30-d SLA; soft-delete + retention-horizon purge job; data-residency (Supabase region).

### Phase D sub-task (D14)
- D.1 Performance: `EXPLAIN ANALYZE` on dispatch hot path; index coverage for WHERE/ORDER BY in migrations; pagination on every list endpoint; WS fan-out budget (`ws_pubsub.py`); 7 background loop replay-safety + liveness metrics.

### Phase E sub-tasks (D17–D22) — NEW

These six dimensions are often skipped at launch "because it works on my machine". They are the ones the first incident will surface. Run them before beta.

- E.1 **Observability (D17)**
  - Structured JSON logging: every log line has `ts`, `level`, `request_id`, `user_id`, `route`, `event`, `duration_ms`.
  - Grep: no `logger.warning` on `routes/auth.py`, `routes/payments.py`, `routes/webhooks.py`, `routes/rides.py`, `db_supabase.py` — these MUST be `logger.error` with full exception (CLAUDE.md).
  - SLI/SLO docs committed: dispatch p95, WS delivery, Stripe webhook lag, FCM success rate.
  - Background-loop heartbeats: each of the 7 asyncio loops emits a metric + log every run; alert on 2× missed interval.
  - Mobile crash reporting: PII scrub rules, source-map upload CI step, ANR telemetry.
  - Output: at least one finding or PASS per bullet in `dimensions/17-observability.md`.

- E.2 **DR / BCP (D18)**
  - Declared RTO/RPO per surface (dispatch, API, admin, payments).
  - Supabase region = Canadian — confirm in project settings.
  - PITR retention ≥ 7 days; off-Supabase logical backup cron.
  - Graceful degradation paths for Stripe/Twilio/FCM/Maps outages (circuit breakers, queued settlement).
  - Feature-flag kill switches for onboarding, dispatch, payments.
  - Restore drill evidence (past 90 days).
  - Output: findings per bullet in `dimensions/18-dr-bcp.md`.

- E.3 **Fraud (D19)**
  - Device fingerprint + install-id on signup; velocity rules.
  - Impossible-travel GPS plausibility check in `validators.py`.
  - Stripe Radar rules (Canadian BINs) and chargeback evidence bundling.
  - Promo / referral abuse guards (first-ride caps, self-referral detection).
  - OFAC/PEP screening on corporate accounts.
  - Output: findings per bullet in `dimensions/19-fraud.md`.

- E.4 **Financial Reconciliation (D20)**
  - Daily cron: Stripe ↔ DB ↔ wallet ↔ payout deltas = 0; any non-zero pages on-call.
  - Wallet mutation discipline: zero direct `UPDATE wallets SET` outside `corporate_wallet_apply_delta`.
  - Tax ledger: GST 5% + SK-PST 6% line-itemised on receipt and stored as separate columns.
  - T4A generator — confirm it actually produces a PDF (links to P4-7 PARTIAL).
  - Append-only `financial_events` table with 7-year retention (CRA).
  - Output: findings per bullet in `dimensions/20-financial-reconciliation.md`.

- E.5 **Threat Model (D21)**
  - STRIDE per asset (ride record · fare calc · driver-PII · Stripe conn · admin JWT · service-role key · WS channel · background loops).
  - Ride-share-specific scenarios: fake driver, GPS spoofing, surge manipulation, SIM-swap OTP, cancellation-fee farming, SOS abuse, corporate wallet siphon.
  - Detection for each threat (not just "mitigated" — "can we see it happen?").
  - Output: threat-model document under `docs/threat-model/backend.md` or equivalent + findings per bullet in `dimensions/21-threat-model.md`.

- E.6 **Third-Party Risk (D22)**
  - `docs/vendor-inventory.md` exists with Supabase, Stripe, Firebase (Auth/FCM/AppCheck/Crashlytics), Google Maps, Twilio, Gemini, Railway, Vercel, Sentry (if used), Cloudinary (if used).
  - DPA on file per vendor; breach-notification contact recorded; sub-processor list reviewed annually.
  - Data residency declared per vendor; cross-border flows disclosed in privacy policy (Gemini US is the usual miss).
  - Secrets rotation cadence documented + tested; lockfiles committed; pip-audit + npm audit clean.
  - Docker base image scanned + non-root user + pinned digest.
  - Output: findings per bullet in `dimensions/22-third-party.md`.

---

## Routes Not Yet Audited (from modules/backend-api.md)

Priority targets — these were discovered but never audited during the driver-app pass:

- `backend/routes/disputes.py`
- `backend/routes/fare_split.py`
- `backend/routes/fares.py`
- `backend/routes/favorites.py`
- `backend/routes/loyalty.py`
- `backend/routes/promotions.py`
- `backend/routes/corporate_accounts.py`
- `backend/routes/corporate_company.py`
- `backend/routes/corporate_rider.py`
- `backend/routes/corporate_wallet.py`
- `backend/routes/wallet.py`
- `backend/routes/safety.py`
- `backend/routes/quests.py`
- `backend/routes/addresses.py`
- `backend/routes/notifications.py`
- `backend/routes/support.py`
- `backend/routes/users.py`
- `backend/routes/settings.py`
- `backend/routes/faqs.py`

For each: (1) auth guard? (2) Pydantic input models? (3) rate limit? (4) RLS on
any direct Supabase access? (5) error paths return proper HTTPException, not
bare `{"error": ...}`?

---

## Backend-Specific Risk Checklist

### Auth (D02)
- [ ] JWT secret ≥ 32 chars, fails fast in prod (`core/config.py`)
- [ ] Refresh tokens SHA-256 hashed, rotated on use, `revoked_at` honoured
- [ ] OTP: 5 failures/hour → 24h Redis lockout; hashed at rest
- [ ] Admin role from JWT trusted; rider/driver role always re-read from `users`
- [ ] WebSocket first-message `{"type": "auth"}` enforced within N seconds
- [ ] Token lifetimes: 15 min access (rider/driver), 12 h (admin), 30 d refresh

### Payments (D08)
- [ ] Stripe webhook signature verified before parsing body
- [ ] `claim_stripe_event(event_id)` called before every webhook side-effect
- [ ] No `float` in fare/wallet math — grep zero in backend/services/fare*, wallet*
- [ ] `corporate_wallet_apply_delta` PG function used for all wallet deltas
- [ ] Payment-method attach requires authenticated user; cannot attach to other user

### State Machine (D07)
- [ ] All ride transitions guarded by `_require_ride_in_state(...)`
- [ ] `CANCELLED` only valid pre-TRIP_STARTED
- [ ] Accept handler filters `{'status': 'searching'}` — zero rows → 409
- [ ] Every state change emits a WS event (driver + rider channels)

### Security headers / CORS (D11)
- [ ] `allow_origins` enumerated (no `*`) in production
- [ ] HSTS, X-Content-Type-Options, Referrer-Policy, CSP present
- [ ] Per-route rate limits via SlowAPI; Redis-backed in prod
- [ ] App Check enforced on mobile-originating routes

### Compliance (D12)
- [ ] RLS policies on every table in `backend/migrations/`
- [ ] PII columns (phone, email, licence#, bank) logged on admin access only
- [ ] Data-retention / soft-delete strategy documented
- [ ] Driver PII stripped from rider-facing responses and vice versa

### Performance (D14)
- [ ] No per-ride N+1 on driver-nearby queries
- [ ] Pagination on every list endpoint (drivers, rides, notifications, audit)
- [ ] Indexes match WHERE/ORDER BY in `backend/migrations/`
- [ ] WS pub/sub channel `spinr:ws:dispatch` has bounded fan-out
- [ ] 7 background loops are replay-safe across N replicas

---

## Regulatory Matrix (REQUIRED tagging)

Canonical reference: `audit-framework/regulatory-matrix.md`. Every finding
tags one or more IDs from the table below in its `regulations:` field. If a
dimension lists a regulation here and you produce neither a finding nor a
PASS for it, you haven't audited it — go back.

| ID | Where it bites in backend/ | Concrete check |
|---|---|---|
| PIPEDA | `routes/users.py`, `routes/auth.py`, DB schema | DSAR endpoint exists · breach notification path · Canadian region pinned for Supabase |
| CPPA | `services/fare_service.py`, surge engine | Explanation payload available for every price quoted |
| CASL | `routes/notifications.py`, SMS helpers, email templates | Consent ledger table · unsubscribe path · sender ID in every outbound message |
| OLA | Every `HTTPException(detail=...)` + push/SMS/email templates | French parity — no hardcoded English error strings |
| AML | `routes/wallet.py`, `routes/corporate_*.py` | $10k aggregate threshold flag · KYC on corporate sign-up · STR pipeline hook |
| CRA | `services/fare_service.py`, payout flow, corporate invoices | T4A trigger ≥$500/yr · GST/HST + PST line-items · BN-9 validation on corporate |
| COMP | Surge engine, fare estimator | Upfront price disclosed before booking · surge cap policy in code |
| E911 | `routes/safety.py`, SOS endpoint | Rider GPS forwarded to emergency contact + pre-filled 911 SMS fallback |
| CRTC | SMS vendor integration | Verified sender · STOP/HELP keywords · Twilio compliance hooks |
| SK-TNC | `routes/drivers.py`, dispatch | Driver permit # · vehicle permit # stored · checked at dispatch |
| SGI | Driver document model, dispatch | Insurance expiry **blocks** dispatch (not just warns) |
| SK-PST | Fare calculator, receipts | 6% PST line item on taxable portion |
| PCI-DSS | `routes/payments.py`, webhooks | No PAN at rest · HMAC signature + timestamp window on webhooks · tokenised-only card flow |
| SOC2 | Observability, change mgmt | Audit log immutability · deployment approval trail |
| SAFE-CRC/DRV/VEH | `utils/document_expiry.py`, dispatch | Expired credentials produce a HARD dispatch block |

Untagged CRITICAL/HIGH findings will be rejected during self-review.

---

## Output Schema (REQUIRED)

Every finding in `2026-04-23-backend-api-v1.txt` MUST also appear in a
machine-readable block at the end of the file under a `===FINDINGS-YAML===`
fence. Downstream tooling (cross-audit dedup, regression tracking, ticket
auto-generation) depends on this contract.

```yaml
===FINDINGS-YAML===
- id: B-02-1                             # B = backend · <dim> · <seq>
  severity: CRITICAL                     # CRITICAL|HIGH|MEDIUM|LOW|PASS|RECOMMENDATION
  dimension: 02                          # 01..16 (+17..22 if adopted)
  title: "JWT role claim trusted for non-admin tokens"   # ≤80 chars
  evidence:
    file: backend/routes/rides.py
    lines: [412, 418]
    snippet: "if claims.get('role') == 'driver': ..."    # ≤5 lines
  root_cause: "Role read from JWT instead of users table."
  impact: "Privilege escalation — rider token forged with role=driver accepts rides."
  fix:                                   # ≤3 imperative bullets
    - "Replace claims['role'] with get_user_role(user_id)."
    - "Add regression test for forged role claim."
  effort_hours: 3
  regression_test: "backend/tests/test_auth.py::test_role_not_trusted_from_jwt"
  sprint: P0                             # P0..P4
  owners: [backend]
  regulations: [PIPEDA, PCI-DSS]         # empty list if none
  confidence: high                       # high=code read · med=inferred · low=hypothesis
  duplicate_of: null                     # prior finding ID if rediscovered
===END-FINDINGS-YAML===
```

### Mandatory rules
- No `CRITICAL` or `HIGH` finding without `file` + `lines` + `snippet`. Otherwise
  downgrade to `RECOMMENDATION` and mark `confidence: low`.
- Before emitting a finding, grep for prior-audit matches:
  `grep -E "file:.*<path>" reports/audits/2026-04-18-driver-app-*.txt reports/audits/2026-04-19-rider-app-v1.txt`
  If a match exists with the same root cause, set `duplicate_of: <prior_id>` and skip.
- Live secrets (Stripe `sk_live_`, Supabase service-role) found in code: redact
  in `snippet` as `"sk_live_[REDACTED]"`; never paste the literal value.

### Done-sentinel
After the YAML block, emit on its own line:
```
===AUDIT-COMPLETE=== dimensions_run=<N> findings=<N> critical=<N> high=<N>
```
Driver scripts detect truncation by the absence of this line.

### Self-review pass (run before emitting the sentinel)
1. Re-read every `CRITICAL`/`HIGH` — confirm `file:line` still exists.
2. Group by `file`; if ≥3 findings target the same file, check whether they
   share a root cause → collapse into one with sub-bullets.
3. Severity calibration: does each `CRITICAL` meet "app crash / data breach /
   complete feature failure in production"? If not → `HIGH`.
4. Cross-check `regulations` field: any PII handler without `PIPEDA`? Any
   Stripe path without `PCI-DSS`? Any SMS/email without `CASL`?

### Worked examples (gold standard — match this exact format)

One HIGH finding:
```yaml
- id: B-08-3
  severity: HIGH
  dimension: 08
  title: "Stripe webhook lacks timestamp-window check — replay risk"
  evidence:
    file: backend/routes/webhooks.py
    lines: [62, 78]
    snippet: |
      sig = request.headers.get("Stripe-Signature")
      event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
  root_cause: "construct_event verifies HMAC but tolerance defaults to 300s Stripe-side — we don't clamp it."
  impact: "Captured webhook can be replayed days later; claim_stripe_event dedups by event_id but not all downstream effects are idempotent (e.g. reconciliation log)."
  fix:
    - "Pass tolerance=300 explicitly to construct_event."
    - "Reject any event with timestamp > tolerance and log as security event."
    - "Add regression test in tests/test_webhooks.py for stale timestamp."
  effort_hours: 2
  regression_test: "backend/tests/test_webhooks.py::test_stale_webhook_rejected"
  sprint: P0
  owners: [backend]
  regulations: [PCI-DSS]
  confidence: high
  duplicate_of: null
```

One PASS finding (document intentional correctness — do not skip these):
```yaml
- id: B-07-1
  severity: PASS
  dimension: 07
  title: "Atomic ride-accept filter prevents two-driver race"
  evidence:
    file: backend/db_supabase.py
    lines: [425, 448, 460, 465]
    snippet: ".update({...}).eq(\"id\", ride_id).in_(\"status\", [\"searching\", \"driver_assigned\"])"
  root_cause: null
  impact: null
  fix: []
  effort_hours: 0
  regression_test: "backend/tests/test_rides.py::test_no_double_accept"
  sprint: null
  owners: []
  regulations: [SK-CPPA]
  confidence: high
  duplicate_of: null
```

---

## Remediation Sprints

| Sprint | Priority | File to create |
|--------|----------|----------------|
| P0 | CRITICAL | `reports/remediation/backend-P0-critical-fix-now.md` |
| P1 | HIGH     | `reports/remediation/backend-P1-before-beta.md` |
| P2 | MEDIUM   | `reports/remediation/backend-P2-before-launch.md` |
| P3 | LOW      | `reports/remediation/backend-P3-hardening.md` |
| P4 | RECOMMEND | `reports/remediation/backend-P4-future-features.md` |

Use `audit-framework/templates/remediation-group.md` as skeleton.
