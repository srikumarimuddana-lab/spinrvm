# Sprint — Current

_Update this file at the start of every sprint. Claude loads it via `@.claude/context/sprint-current.md` referenced from CLAUDE.md._

## Sprint goal

Close all P0 security/safety findings across backend, admin, and rider surfaces — HttpOnly token storage, admin TTL reduction, first-rating crash, fare-collection state mismatch, GPS OOM, and SOS silent failure.

**Status (2026-04-27):** Sprint COMPLETE. All 6 P0s shipped. All P1/P2 candidates also shipped (B-P1-1 via #124, B-P1-5 via #124, B-P2-1 via #124, B-P2-2 via #124; B-P1-2 and B-P1-6 were already in-place). A-P0-3 GPS OOM fix (Postgres function + migration 54) shipped in #126. Sentry loguru bridge shipped in #126. logger.warning sweep on payment/dispatch/safety paths shipped in #127.

**Status (2026-05-06):** Full audit sweep complete. All P0/P1/P2/P3/P4 actionable findings resolved.
- PR #479 (merged): R-P2-30/31/33/43/44/45/46, B-P1-10, B-P2-1, R-P2-14, TASK-4-1, admin-07-1, B-P1-9
- PR #484 (merged): P4-7 T4A annual issuance background job (CRA Feb 28 deadline), 12 unit tests
- Remaining non-blockers: P3-4 coverage gap (2 pp), P4-5 E2E suite stabilising (non-blocking CI), rider-P4 roadmap items

## In-flight

| Ticket | Owner | State | Notes |
|---|---|---|---|
| A-P0-1 | — | shipped (#95/#97) | Admin JWT → HttpOnly cookie; access TTL 1 h; silent refresh |
| A-P0-2 | — | shipped (#95/#97) | Admin access token TTL 1 h confirmed end-to-end |
| B-P0-1 | — | shipped (#117) | `rider_rating` column fix + rolling-average in `rate_driver()` |
| B-P0-2 | — | shipped (#117) | Supabase-native atomic guard: `db_supabase.update_one` filters on `payment_status="pending"` |
| A-P0-3 | — | shipped (#117 + #126) | `complete_ride()` breadcrumb cap (10k→1k) + `compute_driver_phase_distances` Postgres function replaces Python haversine loop in `maintenance.py` |
| R-P0-1 | — | shipped (#117) | `triggerEmergency` retries 3× (1 s/2 s backoff) before 911 Alert |

## Blocked

| Ticket | Blocker | Unblock action |
|---|---|---|
| — | — | — |

## P0 incidents open

None currently open in production (pre-launch).

## Do not touch this sprint

- `backend/routes/rides/` ride-state machine modules (`lifecycle.py`, `booking.py`, `matching.py`, etc.) other than the rating endpoint and payment guard — active area, coordinate before touching
- `admin-dashboard/src/store/authStore.ts` — A-P0-1 has shipped; further changes to token storage need a fresh ticket and broader review

## Recently shipped

| PR / Commit | What | Why |
|---|---|---|
| `f274816` | **B-P0-2** RESOLVED — `process_payment` 409 fixed: state strings now consistent (`"completed"` matches between `routes/rides.py:1329` and `routes/drivers.py:2133`); plus double-booking race + SOS UX + dev-endpoint guard | Verified in `reports/audits/2026-04-26-backend-verification-rollup.md` lines 47–55 |
| `f274816` | **R-P0-1** RESOLVED — SOSButton: never green-checks until backend 200; amber "Sending…" state; one auto-retry on network failure; "Alert Not Sent + Call 911 + Retry" path | Verified in `shared/components/SOSButton.tsx:20,58,77,134` (4-state UX wired) |
| #105 | Group 4 backend misc fixes — `datetime.utcnow()` → `datetime.now(timezone.utc)`, scheduled-time validator | unblocked timezone correctness |
| #106 | **B-P0-1** RESOLVED + P1 auth/session/error column (B-P1-3,4,8,11,12,13 + B-P2-1 + B-P3-leak-cleanup) | `rides.py:1888` correctly guards `average_rating` inside `if rated_rides:`; verified in audit rollup line 33 |
| #113 | Restored B-P1-11/12 lost in #106 merge resolver | WS session-revocation + per-user rate-limit |
| #118 | **`pyotp` production bug fix** — admin MFA was importing pyotp without it being in requirements (would `ModuleNotFoundError` at runtime) | Surfaced during PR-watch on #113 CI failures; pip-compile drift check now green |
| #95/#97 | **A-P0-1** + **A-P0-2** RESOLVED — admin JWT moved to HttpOnly cookie + access TTL 12 h → 1 h with silent refresh | `authStore.ts:8,19-23,32,99,226-232` (HttpOnly via `/api/auth/set-cookie`); `core/config.py:40` (`ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1`) |
| #108 | CI error audit system + safeguards (`ci-error-audit.yml`, `ci-guardrails.yml`, `security-gates.yml`, `dependabot-auto-merge.yml`, `pip-compile-check.yml`) | Full audit + block pipeline |
| #109 | Fix invalid `yyz1` Vercel region → `iad1` | Unblocked Vercel deployments |

## Recently shipped (P1/P2 candidates — now closed)

| PR | Ticket | What |
|---|---|---|
| #124 | B-P1-1 | Firebase audience binding — fail-closed when `FIREBASE_DRIVER_APP_ID` unset |
| #124 | B-P1-5 | `logger.warning` → `logger.error` + `exc_info=True` across `auth.py` |
| #124 | B-P2-1 | Corporate allowance `float()` → `Decimal`; `str(amount)` at Supabase RPC boundary |
| #124 | B-P2-2 | Stripe webhook explicit allowlist replaces bare `else` fallthrough |
| #126 | A-P0-3 | `compute_driver_phase_distances` Postgres fn + migration 54 + GPS OOM fix in `maintenance.py` |
| #126 | observability | loguru→Sentry bridge in `server.py` — loguru ERRORs now reach Sentry |
| #127 | B-P1-5 ext. | `logger.warning` → `logger.error` sweep: `stripe_charge.py`, `payments.py`, `users.py`, `drivers.py` |
| already in code | B-P1-2 | `JWT_SECRET` length ≥32 enforced in `core/config.py` |
| already in code | B-P1-6 | Retention purge loop in `core/lifespan.py` + migration 50 |
| #133 | CI + PIPEDA | `@react-native/jest-preset@0.85.2` added to rider/driver devDeps (RN 0.85.2 CI fix); `send_default_pii=False` in Sentry init (PIPEDA) |
| #134 | B-P2-2 ext. | Unknown Stripe event types now return early without stamping `processed_at`; `test_unknown_event_type_not_marked_processed` added |
| #135 | test fix | `test_p2_payout_t4a.py` — `MagicMock` → `AsyncMock` for `get_rows`/`get_rides_for_driver` (non-awaitable cursor crash) |
| #131 | B-P1-5 + money | `auth.py:348` logger.warning→error; `rides.py` float→`_f()` at all payment serialisation sites; Vercel ignoreCommand; `test_corporate_allowance_service.py` str-equality assertions |
| #139 | B-P1-1 (refresh) + B-P2-1 | Driver refresh token audience `"rider"` → `"driver"`; `/auth/refresh` gate broadened to `{"rider","driver"}`; rotation preserves original audience; corporate billing totals quantized to 2dp before JSON |

## Recently shipped (post-sprint hardening)

| PR | What |
|---|---|
| #310 | **Admin middleware dead-auth fix** — `src/proxy.ts` → `src/middleware.ts`, `export function proxy` → `export function middleware`; Next.js was silently ignoring the misnamed file, leaving all `/dashboard/*` routes accessible without a valid `admin_token` cookie. JWT expiry check, IP allowlist (F-06), and CSP nonce injection (F-05) now all active. Identified in auth coverage audit 2026-04-29. |
| #305 | Dead `carpool.tsx` screen removed (309 lines, zero nav callsites; superseded by `fare-split.tsx`) |
| #128 | `logger.warning` → `error` sweep: `stripe_charge.py`, `payments.py`, `users.py`, `drivers.py` — payment/dispatch/safety failures now reach Sentry |
| #132 | `_vault_encrypt` fail-closed: driver PII (`license_number`, `vehicle_vin`) no longer stored as plaintext when Supabase Vault is unavailable |
| manual | [17-4] Branch protection on `main` — PR + 1 review required; `Post guard rail summary` + `Security gates summary` required checks; force-push + deletion blocked |
| #133 | CI + PIPEDA | `@react-native/jest-preset@0.85.2` added to rider/driver devDeps (RN 0.85.2 CI fix); `send_default_pii=False` in Sentry init (PIPEDA) |
| #134 | B-P2-2 ext. | Unknown Stripe event types now return early without stamping `processed_at`; `test_unknown_event_type_not_marked_processed` added |
| #135 | test fix | `test_p2_payout_t4a.py` — `MagicMock` → `AsyncMock` for `get_rows`/`get_rides_for_driver` (non-awaitable cursor crash) |
| #127 | **B-P1-5** (auth.py 2 sites — profile_complete self-heal, logout-all WS kick) + **B-P2-6** (DSAR `asyncio.gather` two-wave pattern, ~3× speedup) + **B-P3-2** (±10% per-tick jitter on surge / scheduled / payment-retry loops) |
| #137 | **B-P3-1** (WS `broadcast()` + `_deliver_broadcast_local()` per-message 2 s timeout) + **B-P2-3** (App Check log lines bind `req_id=` for X-Request-ID correlation) + **B-P2-7** (explicit `_DB_EXECUTOR` ThreadPoolExecutor with 32 workers, configurable via `DB_THREAD_POOL_SIZE`) |
| #138 | **B-P2-5** (`idx_drivers_dispatch_ready` partial composite index — turns the dispatch query from sequential scan to index-only lookup) + **B-P2-4** (audit_logs DELETE lockdown via `audit_logs_no_delete` trigger gated by `spinr.audit_logs.allow_delete` session flag) |
| #166 | **Retention regression fix** — migration 57 `CREATE OR REPLACE purge_pii_retention()` restores migration 51's audit-log INSERT schema fix and 7-year `audit_logs` purge step. PR #141 had silently overwritten both, breaking PIPEDA + SK Transportation Act retention enforcement. Caught by conflict-safety audit on a follow-up PR; patched within 30 min of detection. See `docs/runbooks/migration-conflict-detection.md`. |
| #173 | **B-P3-3** (sub-processor monitor) — `.github/workflows/subprocessor-monitor.yml` runs every Monday 13:00 UTC; opens a `subprocessor-review` issue when `docs/vendor-inventory.md` hasn't been touched in 90+ days. Idempotent. Closes the cadence-enforcement gap PIPEDA s.4.1.3 + SOC 2 CC9.2 require. |
| #176 | **Migration conflict-detection runbook** (`docs/runbooks/migration-conflict-detection.md`) — 5-step pre-merge checklist for any PR touching `backend/migrations/*.sql` to catch the slot-collision + outdated-fork failure mode that broke retention in #166. Includes verbatim incident timeline. |
| #182 | **B-P2-8 partial** — `backend/Dockerfile` builder pinned to `python:3.12.9-slim` (was `3.12-slim` mutable tag); `docs/runbooks/docker-image-pinning.md` documents the SHA256 digest-pin procedure (manual step, requires `docker pull`). RO-root half noted as future host-migration item (Railway doesn't expose). |
| #207 | **L-P1-4 + L-P1-2** — `gst_registered`/`gst_bn` T4A fields added (migration 58, `GET /drivers/me/t4a-summary`, CSV export); 10 unsafe `any` types tightened in `shared/store/authStore.ts`. Both next-sprint candidates now closed. |
| #266 | **E2E regression suite** — Playwright specs for cancellation flow, payment guard (no double-charge), driver-cancel path, admin rides view, and SOS/emergency-alert (R-P0-1 regression). Covers the full ride lifecycle end-to-end. |
| #313 | **B-P2-8 half 2 + L-P1-2 extended** — `backend/Dockerfile` both `FROM` lines pinned to `python:3.12.9-slim@sha256:48a11b7...` (bit-for-bit reproducible builds); `any` → `unknown` across `shared/api/client.ts`, `cachedClient.ts`, `offlineQueue.ts`, `upload.ts`, `locationStore.ts`; E2E CI `BACKEND_URL` fix. |
| #551 | **Any-type sweep — stores** (in review) — 68 `any` occurrences eliminated across `rideStore.ts` (27), `driverStore.ts` (13), `walletStore.ts` (10), `documentStore.ts` (6), `questStore.ts` (4), `workProfileStore.ts` (3), `firebase.ts` (1). New interfaces: `Promo`, `ChatMessage`, `CompletedRideData`, `RideHistoryItem`, `AllowanceRequest`, `RNFileDescriptor`. |

## Lessons learned (2026-04-28 incident)

PRs #138 and #141 both used migration slot 56 to `CREATE OR REPLACE` the same Postgres function (`purge_pii_retention`). The alphabetically-second migration (PR #141) was forked from migration **50** instead of migration **51**, silently regressing two intervening fixes — including a known-bad `audit_logs` INSERT column list that migration 51 had explicitly fixed with the comment "Today the function would error on first real run and roll back the entire purge transaction — silently disabling the retention job." The bug shipped to main; the daily retention loop was failing on every tick from the moment #141 merged until #166's forward-fix landed (~45 minutes silently broken in pre-launch, no impact to users).

**What caught it**: the next PR's conflict-safety audit (a 5-minute `git log origin/main` + diff every shared `CREATE OR REPLACE` target) found the regression before any production-impacting ride happened.

**What we changed**: `docs/runbooks/migration-conflict-detection.md` (PR #176) makes the conflict-safety check explicit for every future migration author. The runbook also documents what existing CI guard rails catch (append-only, forward-compat, lock-safety) and what they don't (slot-collision + outdated-fork — the failure mode that bit us). A future CI enhancement to walk every PR's `CREATE OR REPLACE` targets across open + recently-merged PRs is tracked as the next layer.

## Next sprint candidates

- **Sentry alert rule**: Create a Sentry alert for `REFRESH TOKEN REUSE DETECTED` → PagerDuty. ~30 min in Sentry UI. No code required.
- ~~**L-P0-3 WAV dispatch**~~ — shipped PR #240 (merged 2026-04-29); migrations 60 + 61; `is_wav` on drivers, `requires_wav` on rides; WAV-only dispatch matching. Saskatchewan Transportation Act accessibility mandate now satisfied.
- ~~**L-P1-2 authStore.ts**~~ — shipped in #207 (2026-04-28): 10 `any` types tightened in `shared/store/authStore.ts`.
- ~~**L-P1-4 earnings GST/BN**~~ — shipped in #207 (2026-04-28): `gst_registered`/`gst_bn` fields added via migration 58 + `GET /drivers/me/t4a-summary` updated.
- ~~**Dockerfile SHA256 digest pin** (B-P2-8 second half)~~ — shipped #313 (2026-05-06): both `FROM python:3.12.9-slim` lines pinned to `@sha256:48a11b7...`. Refresh quarterly per `docs/runbooks/docker-image-pinning.md`.
- **Read-only-root-filesystem** (B-P2-8 third half): host migration off Railway (Fly.io / K8s / ECS); not gating launch.
- ~~**CI enhancement: cross-PR migration target check**~~ — shipped (PR in flight on `claude/ci-error-audit-system-HPjKP`): new step in `migration-safety-gate` scans new `.sql` files for `CREATE OR REPLACE` targets and cross-checks against all existing migrations; annotate with `-- migration-override-ok: reason` to suppress.

## Launch readiness shipped

| PR | Item | What |
|---|---|---|
| #172 | L-P0-1 | `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` validated at startup in production |
| #172 | L-P1-1 | `logger.error` on startup if Redis missing in production |
| #172 | L-P0-2 | Railway primary / Render fallback (was inverted); `--fail-with-body` on curl |
| #172 | L-P0-4 | Smoke test extended: JSON validation + auth middleware (401 not 500) + fare service health |

---

# Sprint 2 — Backend Safety Hardening

**Sprint goal:** Close the top P1 backend safety and data-integrity gaps from the driver-app verification pass (2026-04-23): suspended-driver dispatch gap, document-expiry `$set` anti-pattern, and open items from `OPEN-ITEMS-TRACKER.md` section A/B.

**Status (2026-04-28):** COMPLETE. All 5 B-S2 tickets shipped. Post-sprint hardening ongoing (audit_logger schema fix, migration 57, DV-9, F-section ops gaps).

## In-flight

_None._

## Recently shipped (Sprint 2)

| PR | Ticket | What |
|---|---|---|
| #140 | B-S2-1 | **DV-1**: Migration 55 — `status != 'suspended'` filter added to `find_nearby_drivers`. |
| #140 | B-S2-2 | **DV-2**: `document_expiry.py` — `{"$set": ...}` wrapper removed from both `update_one` calls. |
| #141 | B-S2-3 | **DV-17 / PIPEDA**: `users.py` both deletion endpoints now write `audit_logs` row on DSAR request/execution. |
| #141 | B-S2-4 | **DV-8 / PIPEDA**: Migration 56 — `purge_pii_retention()` Step G anonymizes `pending_deletion` users after 30-day grace period. |
| #159 | B-S2-5 | **DV-5 / CASL**: `notificationQueries.ts` + `settings.tsx` — onError rollback + feedbackAlert on preference PUT failure. |

## Recently shipped (post-sprint hardening)

| Commit / PR | What |
|---|---|
| `c5abb75` | **Critical regression fix**: `audit_logger.py` + migration 57 — `audit_logs` INSERT used non-existent columns `actor_id/actor_role/resource`; every admin audit write was a silent no-op; daily retention tick was rolling back. Fixed: `entity_type`/`entity_id` mapping; migration 57 uses `CREATE OR REPLACE` to repair `purge_pii_retention()`. |
| `180bfd4` | **DV-9**: `driver-app/app/_layout.tsx` — `addNotificationResponseReceivedListener` added; push-notification taps now route to `/driver/` (ride offer) or `/driver/notifications` (all other types) instead of silent no-op. |
| `99f7810` | **F-section**: `backend/utils/stripe_reconcile.py` — daily 02:00 UTC Stripe ↔ DB reconciliation cron; detects `DB_PAID_STRIPE_MISSING`, `DB_PAID_STRIPE_MISMATCH`, `DB_PAID_AMOUNT_MISMATCH`, `STRIPE_ORPHAN`; logs at ERROR (→ Sentry) + writes to `audit_logs`. |

> **Migration note (2026-04-29):** Multiple migrations share numeric prefixes (08, 28, 29, 48, 50, 51, 52, 54, 55, 56, 57, 58) — all pre-existing convention violations. The runner uses the full filename as idempotency key so they apply correctly. Cannot be renamed after merge. On `main`, the highest slot used is `59_reconciliation_discrepancies.sql`. PR #240 (in-flight) claims **60** (`60_wav_dispatch.sql`) and **61** (`61_drivers_total_ratings.sql`). **Next free slot after #240 merges: 62.** A CI prefix-uniqueness check has been added to the migration safety gate to prevent new collisions.

## Deferred (non-code or future sprint)

| Item | Reason |
|---|---|
| DV-3 state machine strings | Not a bug — `"trip_in_progress"` is GPS phase name, `"in_progress"` is ride status. Intentional mapping confirmed in `websocket.py`. |
| DV-4 Stripe idempotency | Already correct — `ride-charge-{ride_id}` is ride-scoped, docstring documents the design. |
| Sentry alert rule (REFRESH TOKEN REUSE) | Requires Sentry UI; no code change. |
