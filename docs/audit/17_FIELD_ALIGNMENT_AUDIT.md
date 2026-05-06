# Audit 17 — Cross-Surface Field Alignment
**Date:** 2026-04-29  
**Branch:** `claude/audit-field-names-RY10E`  
**Surfaces:** backend · rider-app · driver-app · admin-dashboard · shared  
**Status:** Findings committed; remediation phases planned, not yet implemented

---

## Executive Summary

Five parallel investigations audited field naming, type representation, error envelopes, naming conventions, and developer diagnostics across all Spinr surfaces. The codebase is largely consistent at the wire (snake_case everywhere, no accidental camelCase leakage) but has three P0 issues that need fixing before the next release and a structural gap—no central TypeScript contract—that is the root cause of most P1/P2 items.

| Severity | Count | Immediate action needed? |
|---|---|---|
| P0 | 3 | Yes |
| P1 | 9 | In next sprint |
| P2 | 6 | Backlog |

---

## P0 Findings

### P0-1 — Money represented as IEEE-754 float at every boundary

**Impact:** Rounding errors accumulate across fare splits, corporate wallet adjustments, and payout aggregations. Violates the CLAUDE.md "Decimal only" rule. Shows up as receipt-vs-statement mismatches and reconciliation tickets at scale.

**Evidence:**
- `backend/routes/wallet.py:139` — `float(_d(req.amount))` discards Decimal precision before DB write
- `backend/routes/fare_split.py:81` — `float(_d(total_fare / split_count))` divides then rounds to float
- `backend/services/corporate_wallet_service.py` — RPC `corporate_wallet_apply_delta` accepts float delta
- `rider-app/store/walletStore.ts:7,89` — `balance: number`, `amount: number` (JS float)
- All fare components (`base_fare`, `distance_fare`, `time_fare`, `booking_fee`, `tax_amount`, `tip_amount`, `total_charged`) stored and transmitted as float

**Fix:** Serialize all money fields as decimal strings on the wire (`"15.50"` not `15.5`). Parse on clients with `parseFloat` for display only; use `Decimal.js` / `big.js` for any client-side arithmetic. Block float money in client lint rules.

---

### P0-2 — `balance` semantic conflict: rider wallet vs driver earnings

**Impact:** Riders and drivers both see a field called `balance` that means different things. Driver `available_balance` is `total_earnings − pending_payouts` (a computed number); rider `balance` is raw wallet balance. Support tickets and payout disputes are the symptom.

**Evidence:**
- `backend/routes/drivers.py:726` — returns `available_balance` (computed)
- `backend/routes/wallet.py:44` — returns `balance` (raw)
- `rider-app/store/walletStore.ts:89` — reads `balance`
- `driver-app/store/driverStore.ts:150` — reads `available_balance` for payout eligibility

**Fix:** Rename driver field to `payable_balance` everywhere (backend response + driver-app store). Add a comment at the backend definition documenting the formula. The word `balance` then belongs exclusively to raw wallet balance.

---

### P0-3 — Backend error messages are hard-coded English, passed raw to mobile Alert dialogs

**Impact:** Riders on any non-English locale see English error strings. PIPEDA + AODA accessibility obligations apply. Blocks any future locale expansion. No machine-readable code means clients can't adapt UX per error type.

**Evidence:**
- Backend routes emit strings like `"Your account is suspended. Please renew your documents"` directly in `HTTPException(detail=...)`
- `shared/api/client.ts::extractErrorMessage` reads `error.message` and passes raw string to toast
- `rider-app/i18n/en.json` exists but only contains UI labels—no error-code → message map
- Mobile uses `Alert.alert()` with the raw backend string as the body

**Fix:** Add `message_key` to every error response (e.g. `"driver_account_suspended"`). Mobile resolves via i18n table; falls back to `message` if key absent. Backend `message` becomes the English fallback, not the display string.

---

## P1 Findings

| # | Finding | Key files |
|---|---|---|
| P1-1 | `total_fare` (backend) aliased as `estimated_fare` in driver-app. Works only because of `[key: string]: unknown` escape hatch. | `driver-app/store/driverStore.ts:69`, `backend/schemas.py:276` |
| P1-2 | User name fragmentation: drivers have atom `name`, users have split `first_name`/`last_name`. Admin re-concatenates. Risk of duplicate data. | `backend/schemas.py:62-66,202`, `backend/routes/drivers.py:510` |
| P1-3 | Dual flags `is_online` + `is_available` with undefined semantics. Admin filters on `is_online`; dispatch uses both. | `backend/routes/drivers.py:414-433`, `backend/schemas.py:230-231` |
| P1-4 | Bank account: DB column `account_number_last4`, API response key `account_last4`. Direct DB queries silently get empty. | `backend/routes/drivers.py:1487,1575`, `driver-app/store/driverStore.ts:161` |
| P1-5 | Boolean casing: backend `is_online` (snake), React component prop `isOnline` (camel). Manual rename at every prop boundary. | `driver-app/components/DriverTopBar.tsx` |
| P1-6 | `extractErrorMessage` reads `error.message` but drops `error.code`. 100+ error codes emitted by backend, none surfaced to client logic. | `shared/api/client.ts` |
| P1-7 | Mobile error logs lack `surface` + `screen` tag. Can't tell which app/screen produced `[API-ERR]` without user repro. | `shared/api/client.ts::recordApiError` |
| P1-8 | Mobile error ring buffer (50 entries) is memory-only. Crashes discard the trail. | `shared/api/client.ts` |
| P1-9 | No trace-ID propagation. Sentry is wired but no `X-Trace-ID` header, so payment failures can't be correlated across mobile → backend → Stripe. | `backend/core/middleware.py`, `shared/api/client.ts` |

---

## P2 Findings

| # | Finding | Files |
|---|---|---|
| P2-1 | `pickup_otp` aliased as `otp` in driver-app only | `driver-app/store/driverStore.ts:74`, `backend/schemas.py:283` |
| P2-2 | `photo_url` vs `profile_image_url` both defined in backend schemas; clients use different keys | `backend/schemas.py:69,23` |
| P2-3 | `vehicle_type` nested object expected by rider-app; backend returns only `vehicle_type_id` | `rider-app/store/rideStore.ts:38`, `backend/schemas.py:251` |
| P2-4 | No pagination convention (no `{items, next_cursor, has_more}` standard) | Undefined across all surfaces |
| P2-5 | No central TypeScript contract — `shared/types/` has one `.d.ts` for a third-party module; every interface is hand-redeclared per surface | `shared/types/` |
| P2-6 | Ride status strings hardcoded in clients instead of imported from a shared enum | `rider-app/`, `driver-app/` |

---

## Additional Alignment Gaps (not field names, but identified during audit)

| Area | Status | Recommended action |
|---|---|---|
| Casing at wire | snake_case end-to-end — good | Document in CLAUDE.md; block accidental camelCase with lint |
| WS event type names | snake_case, consistent — good | Add `shared/types/wsEvents.ts` registry |
| Error code machine channel | Backend emits codes; clients ignore them — gap | Wire through `extractErrorMessage` |
| Sentry `surface`/`screen` tags | Backend domain tags good; mobile missing screen | Add to `recordApiError` |
| PII in logs | CLAUDE.md prohibits; not exhaustively scanned | Schedule dedicated PII-leak scan |
| Pagination | Undefined | Adopt `{items, next_cursor, has_more}` now |

---

## Remediation Plan

Each phase ≤ 3 files per subtask (per CLAUDE.md working style). Phases are ordered by risk reduction.

---

### Phase 0 — Establish the contract (no runtime change)

**Goal:** Give every surface a single source of truth for shared object shapes. Stops new drift from being introduced.

| Subtask | Files (≤3) | Notes |
|---|---|---|
| 0-A | `shared/types/api/ride.ts` | `Ride`, `RideStatus` enum, `WSRideEvent` |
| 0-B | `shared/types/api/user.ts` | `User`, `Driver`, `Rider` interfaces |
| 0-C | `shared/types/api/money.ts` | `MoneyString` (branded string type), `WalletBalance`, `Transaction`, `FareEstimate`, `Receipt` |
| 0-D | `shared/types/api/errors.ts` | `ErrorEnvelope`, `ErrorCode` enum mirroring backend |
| 0-E | `shared/types/api/wsEvents.ts` | Exhaustive WS event type union |
| 0-F | `shared/types/index.ts` | Re-export all above |

Acceptance: `rider-app`, `driver-app`, `admin-dashboard` can `import type { Ride } from '@spinr/shared'`. No runtime change.

---

### Phase 1 — P0: Money correctness

**Goal:** Eliminate float money at every boundary.

| Subtask | Files (≤3) | Change |
|---|---|---|
| 1-A | `backend/routes/wallet.py`, `backend/routes/fare_split.py` | Serialize money as `str(Decimal)` before JSON response. Never cast to `float`. |
| 1-B | `backend/services/corporate_wallet_service.py`, `backend/routes/fares.py` | Same: stringify Decimal on every money field in responses |
| 1-C | `backend/schemas.py` | Change fare/wallet money fields from `float` to `str` in Pydantic response models; add validator that rejects non-numeric strings |
| 1-D | `rider-app/store/walletStore.ts`, `rider-app/store/rideStore.ts` | Parse money fields with `parseFloat` for display; arithmetic via `Decimal.js` |
| 1-E | `driver-app/store/driverStore.ts` | Same for driver earnings / fare display |
| 1-F | `admin-dashboard/src/` (fare/wallet components) | Parse money strings for display |

Acceptance: `typeof ride.total_fare === 'string'` at the wire. No JS float money arithmetic anywhere.

---

### Phase 1 (cont.) — P0: `available_balance` rename

| Subtask | Files (≤3) | Change |
|---|---|---|
| 1-G | `backend/routes/drivers.py` | Rename response key `available_balance` → `payable_balance`; add comment with formula |
| 1-H | `driver-app/store/driverStore.ts` | Update read site from `available_balance` to `payable_balance` |
| 1-I | `admin-dashboard` payout pages | Update read site |

---

### Phase 2 — P0: Error envelope + i18n keys

| Subtask | Files (≤3) | Change |
|---|---|---|
| 2-A | `backend/utils/exceptions.py` (or create) | Centralise `SpinrHTTPException` that always emits `{code, message, message_key, action_hint, request_id, timestamp}` |
| 2-B | `backend/routes/auth.py`, `backend/routes/rides.py` | Migrate raise sites to new exception; replace bare strings with `message_key` constants |
| 2-C | `backend/routes/drivers.py`, `backend/routes/wallet.py` | Same migration |
| 2-D | `shared/api/client.ts` | `extractErrorMessage` returns `{code, message, messageKey, requestId}`; downstream callers updated |
| 2-E | `rider-app/i18n/en.json` | Add all `message_key` entries with English copy |
| 2-F | `rider-app` toast/alert handler | Resolve `messageKey` via i18n; fallback to `message` |
| 2-G | `driver-app` alert handler | Same |

---

### Phase 3 — P1: Field name cleanups

| Subtask | Files (≤3) | Change |
|---|---|---|
| 3-A | `driver-app/store/driverStore.ts` | `estimated_fare` → `total_fare`; `otp` → `pickup_otp` |
| 3-B | `backend/schemas.py`, `backend/routes/drivers.py` | Decide `name` vs `first_name`/`last_name` — adopt split everywhere; add backfill in the DB migration (next free slot: `62_`) |
| 3-C | `backend/schemas.py` | Drop `profile_image` (Base64) or `profile_image_url` — keep one; update all read sites in one pass |
| 3-D | `backend/routes/drivers.py`, `backend/schemas.py` | Document `is_online` (driver-toggled) vs `is_available` (system-computed) in docstring; assert invariant in `go_online` handler |
| 3-E | `backend/routes/drivers.py:1487,1575` | Align DB column name with API key — rename column in migration `62_` or rename API key to match DB; pick one |

---

### Phase 4 — P1: Developer diagnostics

| Subtask | Files (≤3) | Change |
|---|---|---|
| 4-A | `shared/api/client.ts` | Add `surface` + `screen` params to `recordApiError`; persist ring buffer (500 entries) to AsyncStorage |
| 4-B | `backend/core/middleware.py` | Emit `X-Trace-ID` header (UUID, or reuse `X-Request-ID` if already set); bind to loguru context |
| 4-C | `shared/api/client.ts` | Forward `X-Trace-ID` from response into Sentry breadcrumb and next request's `baggage` header |

---

### Phase 5 — P2 + contract enforcement

| Subtask | Files (≤3) | Change |
|---|---|---|
| 5-A | `shared/types/api/ride.ts` | Add `RideStatus` TS enum; rider-app and driver-app import instead of hardcoding strings |
| 5-B | `backend/tests/test_schema_contract.py` | Snapshot test: serialize each Pydantic response model → JSON, assert all money fields are strings |
| 5-C | `rider-app/__tests__/errorI18nCoverage.test.ts` | Assert every `ErrorCode` value has a key in `i18n/en.json` |
| 5-D | `backend/schemas.py` or new `pagination.py` | Define `PaginatedResponse[T]` with `{items, next_cursor, has_more, total}`; adopt in first paginated endpoint |

---

## Verification Checklist (per phase)

- [ ] Phase 0: `tsc --noEmit` passes with shared types imported in all three surfaces
- [ ] Phase 1: `pytest backend/tests/test_schema_contract.py` — all money fields serialize as strings
- [ ] Phase 1: `ruff check backend/` — no float arithmetic on money paths (pre-commit hook already blocks)
- [ ] Phase 2: Every `HTTPException` raise in backend has a `message_key`
- [ ] Phase 2: `rider-app/i18n/en.json` covers 100% of `ErrorCode` values
- [ ] Phase 3: `grep -r "estimated_fare" driver-app/` returns zero results
- [ ] Phase 3: `grep -r "available_balance" backend/ driver-app/ admin-dashboard/` returns zero results
- [ ] Phase 4: Sentry trace view shows mobile → backend correlation on a test payment error
- [ ] Phase 5: All snapshot tests green in CI

---

## Cross-Reference

- Sprint context: `.claude/context/sprint-current.md`
- Money arithmetic rules: `CLAUDE.md` § Critical Conventions
- Error handling rules: `CLAUDE.md` § Do not silently swallow errors
- Migration naming: next free slot is `62_` (PR #240 claims 60, 61)
- Related audits: `docs/audit/01_AUDIT_GAPS_REPORT.md`
