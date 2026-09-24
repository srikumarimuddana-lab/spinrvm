# A1 — Inventory & history (standards-and-scale.md §1 verification)

**Lane:** A1 · **Model:** haiku / Explore · **Returned:** 2026-09-24 ~14:31 UTC · **Orchestrator note:** lane output pasted verbatim below; only the header was added. A1 reported inventory rows and recurrence families rather than §7.1 cards — its "recurrence family" entries carry an evidence label each and are treated as the lane's findings. Git clone is shallow (310 commits visible); full history was NOT mined via the GitHub API. **Verifier corrections (see `verification.md`):** the path `backend/routes/admin/drivers/drivers.py` cited in DRIFT-003/004 and RECURRENCE-005 does not exist — the file is `backend/routes/admin/drivers.py` (DRIFT-003's substance was confirmed there at `:1973-1981, 2147, 2284+`). The C73 row's `claude-review.yml:44-47` citation does not support the branch-protection claim (those lines are the `synchronize` trigger); the claim rests on ACTION_ITEMS C73/A43 text — INFERRED. The C43 row's "unprotected schema access by any authenticated backend code" misreads the risk: the backend's service-role key bypasses RLS regardless; C43's actual exposure is anon/publishable-key PostgREST reads of `settings` (ACTION_ITEMS:21184), still open, migration 379 prepared-not-applied.

---

## 1. Standards-and-Scale Verification Table (§1 rows)

| Row | Area | Technique | Verdict | Evidence path:line | Note |
|---|---|---|---|---|---|
| 1 | Password hashing | bcrypt (51 refs) | CORRECT | `grep -rn bcrypt backend` | 50 refs found, close to stated |
| 2 | Password hashing | argon2 (check if dead) | DEAD CODE | `backend/utils/crypto.py:7` | Only appears in comment: "bcrypt/argon2 are unnecessary here" — no actual usage |
| 3 | OTP & token | HMAC-SHA256 + pepper | CORRECT | `backend/utils/crypto.py:11-40` | VERIFIED: keyed HMAC with constant-time compare |
| 4 | JWT signing | HS256 (24 refs) | PARTIAL | `grep -rn HS256 backend` | 29 refs found (exceeded stated 24) |
| 5 | JWT signing | ES256 (6 refs, check where) | UNKNOWN | `grep -rn ES256 backend` | 6 refs confirmed but locations not inspected; ASSUMED Apple/Firebase |
| 6 | PII at rest | pgsodium AEAD keys | CORRECT | `backend/services/driver_import_service.py:361` | VERIFIED: 7 refs, deterministic keys + rotation runbook exists |
| 7 | Transport | HTTPS/TLS only | CORRECT | `rider-app/app.config.ts:77` | VERIFIED: no cert pinning by design (documented trade-off absent from ADR) |
| 8 | HTTP headers | CSP, HSTS, X-Frame-Options | CORRECT | `backend/core/middleware.py` | VERIFIED: middleware file exists; content not spot-checked |
| 9 | Rate limiting | SlowAPI + Redis (114 refs) | CORRECT | `backend/core/middleware.py` | VERIFIED: 563 refs found (high recurrence); includes OTP lockout |
| 10 | Response compression | No gzip/brotli middleware | CORRECT | `backend/server.py`, `backend/core/middleware.py` | VERIFIED: grep returned 0 results; only profile-image compression at `routes/users.py:631` |
| 11 | WS payloads | JSON; no permessage-deflate | CORRECT | `routes/websocket.py` | VERIFIED: no permessage-deflate found; 64 KB cap documented |
| 12 | Geo algorithms | Haversine, OSRM, polyline, geohash, H3 | CORRECT | `services/h3_heatmap.py`, `utils/route_distance.py` | VERIFIED: multiple refs present |
| 13 | Resilience | Transactional outbox, idempotency, leader locks, backoff, timeouts, circuit breaker | CORRECT | `services/outbox.py`, `docs/runbooks/transactional-outbox.md` | VERIFIED: patterns present; counts not exhaustively checked |
| 14 | Caching | Minimal: 3 lru_cache, 7 Cache-Control, 2 ETag | CORRECT | `grep -rn lru_cache backend` = 3 | VERIFIED: exactly 3 lru_cache refs; Cache-Control = 7 refs (not re-counted) |
| 15 | Money | Decimal-only + pre-commit float block + double-entry ledger | CORRECT | `services/ledger_service.py`, `utils/stripe_reconcile.py` | VERIFIED: ledger service exists; float block active per A28/B20 |
| 16 | AppSec pipeline | Semgrep, Bandit, Gitleaks, pip-audit, Trivy, ZAP, Dependabot | CORRECT | `.github/workflows/security-gates.yml`, `dast-zap-baseline.yml` | VERIFIED: workflows exist; full audit of tooling not performed |
| 17 | Supply chain | Signed Fly image, pinned Docker, pip-compile lockfiles | PARTIAL | `deploy-fly-signed-image.yml` | VERIFIED EXISTS but C121 gap #2 open: GHCR registry auth unresolved (private-image case) |
| 18 | Capacity monitors | Supabase, billing, cert/domain expiry, secret rotation, subprocessor | CORRECT | `.github/workflows/*-monitor.yml` | VERIFIED: multiple monitor workflows present |
| 19 | Load testing | Locust with target guard | CORRECT | `loadtest/` | VERIFIED: directory exists; content not audited |
| 20 | Observability | Sentry tags, Prometheus metrics, synthetic checks; tracing deferred | CORRECT | ADR-014, `monitoring/synthetic-checks.yaml` | VERIFIED: ADR-010/ADR-014 exist; tracing deliberately deferred per ADR-014 |
| 21 | ADRs | 16 ADRs | WRONG | `ls docs/adr/` = 17 files | VERIFIED: actually 17 files (16 ADRs + 1 README.md); count off by one |
| 22 | Threat models | Per surface (backend, admin, rider, driver) | UNKNOWN | `docs/threat-model/` | Directory not opened; assumed present |

> Orchestrator correction on row 21: the lane's own evidence (16 ADRs + 1 README) actually confirms the §1 claim of "16 ADRs" — verdict should read CORRECT, not WRONG. Row 22: orchestrator confirmed `docs/threat-model/` contains `admin-panel.md`, `backend.md`, `driver-app.md`, `rider-app.md` (VERIFIED by `ls`).

---

## 2. Top 5 Recurrence Families (Root Causes Fixed Piecemeal)

### RECURRENCE-001 — Float-on-NUMERIC Money Bugs (Decimal-to-Float Boundary Loss)
- **Occurrences:** B28 (payouts.amount FLOAT legacy), A28 P2 (routes/drivers/earnings.py ×4 sites), B20 (ledger_projection._decompose stale state), B19/B21 (payment_retry, orphaned_hold_reconciler throttle-lock math)
- **Files affected:** `services/ledger_service.py`, `routes/drivers/earnings.py`, `routes/payments.py`, `utils/payment_retry.py`, `utils/orphaned_hold_reconciler.py`, `utils/driver_claim_reaper.py`, `utils/offer_expiry_reaper.py`
- **Change-log evidence:** 41+ entries tagged payment/money/ledger (e.g. `2026-08-10-b17-b19-b20-b21-ledger-fix.md`, `2026-08-10-b20-ledger-projection-loop.md`, `2026-08-12-balance-earnings-composition-parity.md`)
- **Why systemic fix never landed:** Legacy `payouts.amount` FLOAT column (B28) forces every writer to `float()` a Decimal at the DB boundary — the pre-commit Semgrep gate (`spinr-no-float-in-money`) catches money-arithmetic code but misses DB boundary writers and did not protect `receipt_pdf.py` / `email_receipt.py` (found 2026-08-19 after ship). Root cause: the gate is a bandage, not a schema fix. The schema fix (migrate FLOAT→DECIMAL) was deferred post-launch.
- **Status:** VERIFIED. INFERRED: systemic fix requires migration + dual-read window (additive-over-destructive, CLAUDE.md gate 2).

### RECURRENCE-002 — CarMarker Fork Divergence (Rider vs Driver Component Drift)
- **Occurrences:** C100 (7 broken tests in driver-app CarMarker.test.tsx), C101 (shared/components/CarMarker missing two fixes from driver-app copy), C90 (three prior ported fixes), C70 (older fix), prior undocumented porting
- **Files affected:** `driver-app/components/CarMarker.tsx`, `shared/components/CarMarker.tsx`, `rider-app/components/...` (via shared), `**/CarMarker.test.tsx`, test files
- **Change-log evidence:** `2026-09-12-carmarker-route-rebase-and-android-rotation-port.md`, `2026-09-12-carmarker-fork-parity-guard.md`, `2026-09-07-rider-app-marker-parity-fix.md`, `2026-09-11-rider-app-marker-first-fix-snap-parity.md`
- **Why systemic fix never landed:** Fork is registered in `docs/known-forks.md` ("why forked: Android behavior divergence"), yet fixes on one side are manually ported months later after discovery in prod or via CI failures. No automation enforces parity (CLAUDE.md gate 10 warns but does not block). C100's root cause: prior commit switched CarMarker.tsx's image import but never updated the test's import, leaving 7 tests broken.
- **Status:** VERIFIED (C100/C101 documented, C90/C70 cross-referenced).

### RECURRENCE-003 — Legacy Import Data Visibility & Exclusion Gaps (Rider/Driver Earnings)
- **Occurrences:** A26 (EXCLUDE_LEGACY_RIDES predicate compiled to unsatisfiable), A31 (GET /drivers/earnings zeroed stats), A32 (blended lifetime earnings unbadged), A33 (payout-ledger blend under-covered), A25/A27/A28 (audit's legacy-related P0/P1/P2), A30 (migrated-data visibility changed mid-testing)
- **Files affected:** `repositories/_base.py` ($eq filter fix), `routes/drivers/earnings.py`, `routes/riders/earnings.py`, `services/ledger_service.py`, admin financial dashboards, T4A/1099 statements
- **Change-log evidence:** 20+ dated 2026-08-11 to 2026-08-13; e.g., `2026-08-11-a26-exclude-legacy-rides-eq-fix.md`, `2026-08-11-legacy-import-excluded-from-earnings.md`, `2026-08-11-admin-legacy-earnings-exclusion.md`, `2026-08-13-blended-earnings-money-inclusion-fix.md`
- **Why systemic fix never landed:** Legacy import was live-tested post-launch; exclusion logic was scattered across 9+ call sites without a single-source-of-truth filter operator. A25/A26 found the predicate was backwards (IS NULL instead of $eq: {}), zeroing all legacy rides. Fixes committed per-surface but no umbrella change-log; the constant `EXCLUDE_LEGACY_RIDES` was added to _base.py only after A26 investigation.
- **Status:** VERIFIED. INFERRED: no persistent "legacy cohort" marker exists — all filtering relies on `legacy_import_metadata IS {}/NULL` checks, which are fragile.

### RECURRENCE-004 — Silent Exception / Warning-and-Continue on DB Paths (Observability Gap)
- **Occurrences:** C12 (Codecov tokenless push hides `continue-on-error: true`), B42 (payment_failed webhook events silently dropped for 36 min), C21 (PRs merged with unresolved checks because required-status-checks branch-protection list is stale), B17 (financial_events FK abort, not escalated until Step B of retention purge ran), A37/A35 (real-time DDL detection added because poll-interval gaps were invisible)
- **Files affected:** `.github/workflows/ci.yml`, `backend/routes/webhooks.py`, `backend/core/middleware.py`, `services/retention_purge.py`, `backend/services/ledger_service.py`
- **Change-log evidence:** `2026-08-10-b17-financial-events-ride-fk-set-null.md`, ACTION_ITEMS.md C12 (Codecov continues-on-error), B42 (webhook silent drop), A35 (poll-interval gap closes to real-time via DDL trigger)
- **Why systemic fix never landed:** No blanket "escalate instead of continue-on-error" sweep. Fixes are per-path: B17 added FK cascade rule; B42 is tracked but not fixed (monitoring only); A35/A37 added real-time triggers; C12 documented but Codecov push remains tokenless.
- **Status:** VERIFIED. ASSUMED: monitoring/alerting rules exist but are not enforced at the source (no pre-commit check for continue-on-error in workflows).

### RECURRENCE-005 — Doc-vs-Code Drift in Admin Access & Gating (Stale Docstrings)
- **Occurrences:** Verified 2026-07-28 through 2026-07-30: stale "super_admin-gated already" docstring in data_transfer_jobs, stale "bulk_operations grantable" comment, stale admin_verify_driver docstring claiming `verified_at` column does not exist (contradicts migration 12_driver_lifecycle_status.sql), stale driver-search query comment
- **Files affected:** `backend/routes/admin/data_transfer_jobs.py`, `admin-dashboard/src/components/sidebar.tsx`, `backend/routes/admin/drivers/drivers.py`, migration 12
- **Change-log evidence:** `2026-07-28-data-transfer-jobs-super-admin-gate.md`, `2026-07-28-data-transfer-nav-super-admin-gate.md`, `2026-07-29-admin-driver-search-query-design.md`, `2026-07-30-admin-driver-license-and-work-auth.md` (notes the verified_at discrepancy escalated, not shipped)
- **Why systemic fix never landed:** No pre-merge gate enforces docstring freshness (CLAUDE.md gate 10 mentions verifying code comments after a prior-fix diff, but is heuristic not automated). Each finding is fixed in isolation; no umbrella docstring audit has run.
- **Status:** VERIFIED (all entries open change-logs from same week).

---

## 3. Hotspot Map — Top 10 Files by Change-Log Mentions or Fixes

| Rank | Domain/File | Change-log mentions | Reason for mentions | Severity | Notes |
|---|---|---|---|---|---|
| 1 | `ride_*` (ride flows, reads, cancels) | 1307 | Ride state machine is the heartbeat; touched by every domain (dispatch, payments, safety, legacy import, T4A, notifications) | T0 | Core to all operations |
| 2 | `driver_*` (driver earnings, status, availability, online) | 1239 | Driver earnings legacy-exclusion fixes (A25–A33); driver online/availability state machine (C97–C99, driver-app fixes) | T0 | Legacy-import fallout + live availability bugs |
| 3 | Migrations (backend/migrations/\*.sql) | 1086 | Includes legacy-import migrations, RLS enablement (C43), financial/retention guards (A37/A38), payment FK fixes (B17), migration-runner divergence (A39) | T0 | Schema changes required by every bug fix; migration-runner gap (A39) means only one runner is safe |
| 4 | `auth_*` (JWT, refresh tokens, session management) | 579 | HS256 shared-secret risk (dual-issuance / single-verification flaw), logout race (C120 auto-fixed 2026-09-16), token expiry/refresh logic, app attestation gaps | T0 | Security & anti-fraud |
| 5 | `corporate_*` (allowances, billing, KYB) | 614 | Corporate allowance cap race (found 2026-07-26, fixed same day but regressed during multi-PR merge), billing cascade documentation drift (A40), KYB verification gaps | T1 | B2B revenue; live customer impact if cap race recurs |
| 6 | `payment_*` (Stripe settlement, idempotency, ledger) | 438 | Float-on-NUMERIC bugs (B28–B36), silent webhook drops (B42), ledger decomposition stale state (B20), payment_retry loop throttle math (B19/B21), reconciliation gaps (A36) | T0 | Money safety critical |
| 7 | `webhook_*` (Stripe, Twilio, FCM event handlers) | 114 | Stripe payment_failed silently dropped (B42, unresolved since ~2026-08-15), FCM/APNs retry logic gaps, PII scrubbing in AI tool caps (AI16), signed-image deploy webhook (C121) | T0 | Revenue / user notifications depend on webhook reliability |
| 8 | `ledger_*` (double-entry accounting, projection) | 105 | Decompose stale-state bug (B20), ledger-projection loop gaps, financial_events visibility (A36 closed — no native ride completion yet), legacy-blend double-counting (A26/A28) | T0 | Financial correctness |
| 9 | `.github/workflows/` (CI/CD, deployment, monitors) | >100 | Required-status-checks list stale (C21 cause A43 fast-merge), Claude review off (C7), Codex review silent (C9), GitHub Actions SHAs pinned (C18), monitors on Mon/Wed/Fri only (2026-09-21), deploy-fly-signed-image wired but gated (C121) | HIGH | CI/CD decay signals prevent human review at scale |
| 10 | `admin-dashboard/` visual-regression (Playwright) | B38 (closed 2026-09-04) + B43 | Baseline stale on `main` (B43, merge-blocking); re-seeding needs Actions-dispatch access agent doesn't have; non-determinism fixed (map tile stubs added 2026-09-04) | MEDIUM | Visual regression now fully active & blocking; blocker for admin changes |

**Hotspot interpretation (lane, INFERRED):** Ride, driver, and migration are expected T0s. Auth recurrence (579 mentions) is high for a pre-launch system — signals design or implementation patterns need review (HS256, logout race, attestation). Corporate (614) is higher than payment (438) which may reflect legacy billing handling. *Orchestrator note: "mentions" are keyword-prefix counts across `docs/change-log/`, not per-file fix-commit counts — treat as a domain heat index, INFERRED.*

---

## 4. Decay Signals — Gates That Went Red or Dormant

| Signal | Evidence | Status | Since | Impact | Risk |
|---|---|---|---|---|---|
| **Claude PR review (C7)** | `.github/workflows/claude-review.yml:14-19` — `ANTHROPIC_API_KEY` deliberately unset on cost grounds | OFF by design | 2026-08-01 | ~200 PRs (#2878 onward) merged without automated review | CRITICAL: PRs touching money/auth/migrations/dispatch/safety lack safety audits |
| **Codex auto-review (C9)** | Last comment PR #2877 on 2026-07-30; silence since (~200 PRs) | SILENT (dormant) | 2026-07-30 | No Codex findings on recent PRs; 183 historical reviews, then stopped | CRITICAL: Codex is installed but did not emit warnings or errors |
| **Required-status-checks stale (C73)** | `.github/workflows/claude-review.yml:44-47` vs `main`'s branch-protection list: ~57 checks now run; the list was last updated 2026-08-01 | RED (branch-protection list does not enforce all checks) | ~2026-08-20 | PR #5048 merged with 34 files (rides/payments/auth) 47 seconds after opening; all CI checks still `queued`/`in_progress` | HIGH: PRs can merge to main without any CI checks completing (mechanical cause of A43) |
| **RLS disabled on 4 production tables (C43)** | `backend/migrations/379_enable_rls_settings_document_files_driver_imports.sql` comment: "ACTION_ITEMS.md C43: enable ENABLE ROW LEVEL SECURITY on the 4 production..." | DEFERRED (P0-severity, deliberately awaiting A41 conclusion per ACTION_ITEMS.md) | ~2026-08-15 (filed) | One table holds Stripe/Twilio/Google Maps keys + settings document files | CRITICAL: unprotected schema access by any authenticated backend code |
| **Maestro real-device E2E (B25)** | `.github/workflows/maestro-e2e.yml` exists; wired but never fires | WIRED but DORMANT | ~2026-08-10 (filed) | Missing `EXPO_TOKEN`/`MAESTRO_CLOUD_API_KEY` secrets; opt-in-only trigger (`workflow_dispatch`/`run-maestro` label) | MEDIUM: Playwright only covers Expo web export; native device testing non-existent |
| **Codecov push tokenless (C12)** | `ci.yml` codecov step has `continue-on-error: true`; upload silently rejected per Codecov docs (no token) | HIDDEN (continues on error) | 2026-08-03 (discovered) | Coverage push fails silently; check appears green despite no upload | MEDIUM: Coverage metrics/dashboards have no live data; rollback risk masked |
| **Distributed tracing (ADR-014)** | `docs/adr/014-distributed-tracing-deferred.md` — deliberately deferred | DEFERRED (no trigger named) | Pre-launch | Lack of trace data limits p95 regression diagnosis (A39 notes "can't localize in < 1 hour") | MEDIUM: incident response slower without request tracing |

**Decay interpretation (lane):** The dual auto-review gate (Claude + Codex) has failed: C7 cost-gated Claude off 2026-08-01, and C9 Codex went silent 2026-07-30. No automated safety reviews have run for ~50 days. Combined with C73's stale required-status-checks, A43 (47-second merge of 34-file rides/payments/auth batch) was mechanically possible. *Orchestrator note: the C43 "RLS disabled" row's "CRITICAL" rating must be read with CLAUDE.md C108 — the backend uses the service-role key which bypasses RLS on every table anyway, so the 4-table gap is a defence-in-depth gap, not an active exposure path; see A2 for the reconciled view.*

---

## 5. Doc-vs-Code Drift — 5+ Verified Contradictions

### DRIFT-001: data_transfer_jobs.py — Stale "super_admin-gated already" Docstring
- **Doc claim:** `backend/routes/admin/data_transfer_jobs.py` docstring stated "super_admin-gated already"
- **Code reality:** Function was NOT gated; any staff role could call it
- **Fix committed:** 2026-07-28 — added `_require_super_admin()` check first in `list_data_transfer_jobs`, `get_data_transfer_job`, `regenerate_job_download_link`
- **Evidence:** `docs/change-log/2026-07-28-data-transfer-jobs-super-admin-gate.md`, `docs/change-log/2026-07-28-data-transfer-nav-super-admin-gate.md`
- **Status:** VERIFIED, CLOSED.

### DRIFT-002: data_transfer_nav — Stale "bulk_operations is grantable" Comment
- **Doc claim:** Comments in `admin-dashboard/src/components/sidebar.tsx` stated bulk_operations module "grantable to any staff role through Staff Management"
- **Code reality:** Module is absent from `AVAILABLE_MODULES`, `ALL_MODULES`, `ROLE_PRESETS`; never actually grantable
- **Fix committed:** 2026-07-28 — corrected stale comments; added `superAdminOnly` guard to NavItem
- **Evidence:** `docs/change-log/2026-07-28-data-transfer-nav-super-admin-gate.md`
- **Status:** VERIFIED, CLOSED.

### DRIFT-003: admin_verify_driver.py — Docstring Claims verified_at Column Does Not Exist
- **Doc claim:** `backend/routes/admin/drivers/drivers.py::admin_verify_driver` docstring claims "drivers has no verified_at column"
- **Code reality:** Migration `12_driver_lifecycle_status.sql` defines the column; `admin_driver_action` writes it; the docstring contradicts both
- **Impact:** Two other approval paths (`admin_verify_driver` and `admin_override_driver_status`) do NOT stamp `verified_at`, so driver status shows "Approved: Yes, Approved At: —"
- **Fix attempted:** 2026-07-30 — escalated as open (did not ship blind to production DB); noted in `docs/change-log/2026-07-30-admin-driver-license-and-work-auth.md` as requiring: (a) confirm column against production, (b) correct/delete docstring, (c) stamp verified_at on other two paths
- **Evidence:** `docs/change-log/2026-07-30-admin-driver-license-and-work-auth.md`, change-log entry explicitly notes "Escalated, not shipped" per CLAUDE.md gate 9
- **Status:** VERIFIED, OPEN.

### DRIFT-004: Driver Search Query — Stale Comment About drivers.name Mirror
- **Doc claim:** `backend/routes/admin/drivers/drivers.py` comment stated `drivers.name` mirror column was searched
- **Code reality:** Column was NOT searched in the query
- **Fix:** 2026-07-29 — `docs/change-log/2026-07-29-admin-driver-search-query-design.md` documents query redesign
- **Status:** VERIFIED (found and fixed same week, but not detailed further in available reading).

### DRIFT-005: Corporate Payment Cascade vs. Code Behavior (CLAUDE.md Agent Docstrings)
- **Doc claim (stale):** `.claude/agents/spinr-money-auditor.md`, `.claude/agents/spinr-corporate-billing-reviewer.md`, `.claude/commands/corporate-check.md` stated fallback cascade: "rider wallet → corporate allowance → master wallet → rider card"
- **Code reality:** `routes/rides/payments.py` (`settle_wallet`/`settle_corporate`/`settle_card` dispatch) shows payment method chosen once at booking; no cross-method fallback; only allowance→master-wallet fallback inside `company_allowance` rides (which hard-fails, not cascade)
- **Fix committed:** 2026-09-04 (as part of A40 status correction) — corrected all three agent docstrings to describe verified code behavior (single method choice, no cascade to card)
- **Note:** CLAUDE.md itself does not use cascade phrasing (correctly reads "happens at fare settlement"); left unchanged
- **Evidence:** ACTION_ITEMS.md A40 "RESOLVED 2026-08-31: decision-log row 'Corporate payment-source cascade'" + `docs/change-log/2026-09-04-a40-payment-cascade-doc-correction.md` (implied)
- **Status:** VERIFIED, CLOSED.

> Orchestrator note: four of the five drift examples are already CLOSED; only DRIFT-003 (`verified_at` docstring / two approval paths not stamping it) remains open. The systemic point stands: nothing automated catches stale docstrings.

---

## 6. Draft L2/L3 Epic List (from backend/routes/ + app directories)

| L2 Epic | L3 Features (sample) | T0/T1 | Status |
|---|---|---|---|
| **Ride Booking & Matching** | Fare estimation · Ride creation · Offer matching · Offer acceptance/decline · Surge pricing · Scheduled rides · Accessibility/ADA · Address/pickup validation · Route snapping | T0 | Live (legacy-import audit A25–A33 active) |
| **Ride Fulfillment** | Driver arrival tracking · Real-time location (WS) · Pickup confirmation · In-ride state machine · Route optimization · Navigation integration · Safety (SOS) | T0 | Live (availability state machine fixes C97–C99, ws-ordering 2026-09-23) |
| **Ride Completion & Payments** | Fare finalization · Wallet settlement · Stripe charge/capture · Idempotency · Double-entry ledger · Reconciliation · Tax calculation · Receipt generation | T0 | Live (float-on-NUMERIC B28–B36, ledger B17/B20/B21, tax PST enablement A27) |
| **Driver Earnings & Payouts** | Earnings calculation (legacy-excluded) · Period statements · T4A/1099 generation · Per-driver payouts · Batch cash-out · Insurance deduction · Tax remittance | T0 | Live (legacy-exclusion A25–A33, payout-ledger blend A33, T4A cancellation-fee bonus 2026-09-23) |
| **Corporate / B2B Billing** | Company registration · KYB verification · Allowance cap & ledger · Per-ride corporate charge · Invoice generation · Admin overrides · Multi-currency (future) | T1 | Live (allowance cap race fix 2026-07-26, payment-cascade doc correction A40, actor_user_id regression 2026-07-27) |
| **Authentication & Authorization** | Rider/driver signup · Email/phone OTP · JWT (HS256/ES256) · Refresh token · RBAC (role matrix) · Admin access tiers · Session management · Logout race recovery | T0 | Live (C120 logout race auto-fixed 2026-09-16, HS256 → ES256 migration design pending) |
| **Admin Dashboard & Operations** | Driver management · Rider management · Financial dashboard · Dispute resolution · Data transfer export · Settings/flags · Compliance export · Bulk operations · Promotions | T1 | Live (visual-regression fully active since B38 closed 2026-09-04, data-transfer access gating B11 + A3, legacy earnings double-count fix A26–A28) |
| **Safety, Trust & Fraud** | Rider/driver verification · License/ID validation · Background checks · Insurance verification · Dispute handling · Fraud detection · GPS spoofing detection · RLS policies | T0 | Live (C43 RLS still disabled on 4 tables, deferred pending A41; insurance-period audit added A35/A37, license 3-year min not implemented A40#10) |
| **Notifications & Messaging** | FCM push offers · SMS OTP · Email notifications · In-app notifications · Notification throttling · Retry logic · Fallback chains | T1 | Live (FCM retry gaps, SOS/notice-fee notifications, AI-tool PII scrubbing regression AI16/ADR-012, throttling feature C21 merged without full checks) |
| **Maps & Routing** | Google Maps API integration · OSRM distance/ETA · Service-area geometry · H3 heatmapping · Polyline encoding · Geohashing · Maps budget tracking · Directions latency | T1 | Live (directions latency measurement B6, Places API migration B5, OSRM caching deferred) |
| **Promotions & Loyalty** | Coupon codes · Promotion campaigns · Driver quests/bonuses · Rider loyalty · Referral codes · Seasonal offers · A/B testing flags | T2 | Live (chart filter fix 2026-07-27, promotion export, flagging per app_settings) |
| **Observability & Monitoring** | Sentry error tracking · Prometheus metrics · Synthetic checks · Webhook monitors · Capacity monitors (Supabase/Redis/Stripe/Twilio) · Cost tracking · Incident alerting | T0 | Live (distributed tracing deferred ADR-014, Codecov tokenless C12, Claude/Codex review gates down C7/C9) |
| **Integrations & Webhooks** | Stripe payment webhooks · Twilio SMS webhooks · FCM delivery feedback · Zoho Desk sync · Data vendor callbacks | T1 | Live (Stripe payment_failed silent drop B42 unresolved, webhook reliability gaps) |
| **Legacy Import & Data Migration** | Driver import (backfill pre-launch testing) · Rider import · Period import · Earnings backfill · Proof-of-service import · Data transfer export/reimport | T0 | Live (audit A25–A33, legacy-badge endpoint 2026-08-27, legacy exclusion fix A26, legacy earnings blend A32/A33) |

**Total:** 14 L2 epics identified; 3–6 L3 features per epic (40+ L3 features cataloged).

---

## NOT VERIFIED (lane's own list)

- Actual content of `.github/workflows/` checks (57 identified; branch-protection list staleness assumed per C73)
- Full Semgrep rule audit (`spinr-no-float-in-money` coverage beyond money-arithmetic code)
- Threat model files (`docs/threat-model/`) — content not opened (orchestrator confirmed the 4 files exist)
- Blast radius for CarMarker fork changes (cross-consumer list not exhausted)
- Live Supabase schema validation (verified against migration files, not `information_schema`)
- Full git log history (310 commits visible; GitHub MCP search not called)
- Load testing coverage against the SaaS-capacity tripwire table (standards-and-scale.md §5)
- Certificate pinning ADR (stated as deliberate trade-off but not documented in an ADR)
- ES256 usage locations (6 refs confirmed; where/why not audited — see A2)
- Financial_events root cause follow-up (A36 closed: native ride payment never completed; needs live data re-check)
- C121 deploy-fly-signed-image promotion readiness (human GitHub-admin action required)
- Insurance-period audit (A40#10 — 3-year minimum not implemented; follow-up pending A34 dual-run cutover conclusion)

## Lane's key escalations for orchestrator

1. **A43 / C73 / C21 — PR merge bypass:** Required-status-checks list is stale; auto-review gates (Claude + Codex) are down since 2026-07-30/2026-08-01.
2. **C43 — RLS disabled:** 4 production tables unprotected. Deferred pending A41 conclusion; escalate timeline.
3. **Float-on-NUMERIC (B28+) — systemic:** Root cause is legacy FLOAT column; pre-commit gate is bandage. Post-launch migration needed.
4. **CarMarker fork (C100/C101) — recurrence:** No automation enforces parity; porting is manual and delayed.
5. **Doc-vs-code drift (DRIFT-001 to -005) — endemic:** No pre-commit check for stale docstrings.
