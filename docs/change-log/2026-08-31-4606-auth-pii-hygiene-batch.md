# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (branch `claude/spinrvm-issues-crs-m2ar9u`) |
| Related issue or gap ID | #4606 (findings 1, 2, 3, 4 — finding 5 deferred, see below) |

## 1. Issue / gap identified

Four independent small auth/PII-hygiene gaps:
1. `POST /admin/auth/unlock` had no rate limit, unlike every sibling admin-auth endpoint.
2. `utils/crypto.py::_otp_pepper()` read `settings.OTP_PEPPER` via `getattr(..., "")`, but `OTP_PEPPER` was never a declared `Settings` field — it always fell through to `JWT_SECRET`, contradicting the docstring's claim of a "dedicated" pepper.
3. `verify_otp_hash()` kept a permanent unsalted-SHA-256 fallback (`_legacy_sha256`) meant only to bridge the ~5-minute OTP TTL window around the 2026-08-22 pepper deploy — it had no expiry and was never removed.
4. `backend/scripts/backfill_imported_ride_routes.py::_fetch_osrm_route` logged raw pickup/dropoff lat/lng on two error paths (`logger.warning`/`logger.error`), violating the PIPEDA "never log raw GPS" rule.

## 2. Root cause

1. Oversight when the endpoint was added — every other admin-auth route in the file carries a `@limiter.limit(...)` decorator; this one was missed.
2. The pepper feature was documented (docstring) as if `OTP_PEPPER` were configurable, but no corresponding `Settings` field was ever added — likely the config field was planned but never implemented, and the JWT_SECRET fallback silently made this invisible.
3. Standard "temporary bridge" code that was never revisited after the deploy it was meant to bridge (9 days prior to this fix, well past the stated ~5-minute window).
4. Script predates the `utils/pii.py::geohash()` helper convention, or was written without consulting it — the function batches over deduplicated coordinate pairs (not per-ride), so no `ride_id` was available at the call site.

## 3. Fix / remediation

1. Added `@limiter.limit("10/minute")` to `/admin/auth/unlock`, matching the rate used by other admin-auth POST endpoints that also gate account-state changes.
2. Added a real, optional `OTP_PEPPER: str = ""` field to `Settings` so the docstring's claim is now true; default empty string preserves the existing JWT_SECRET-fallback behavior for every deployment that doesn't set it.
3. Removed `_legacy_sha256()` and its fallback branch in `verify_otp_hash()` — any OTP hashed under the old unsalted scheme has long since expired (OTPs are short-TTL, and 9 days have passed since the pepper deploy), so the fallback was dead code that only weakened verification.
4. Replaced the four raw lat/lng arguments in both log calls with `geohash(lat, lng)` (precision=5, ~4.9km cell) per the existing PIPEDA-logging convention.

## 4. Risk & impact on existing functionality

- **Finding 1**: `admin_unlock` blast radius — grepped for other callers of the route; it's only reachable via the admin dashboard's account-unlock action. 10/minute matches the rate already used on sibling admin-auth mutation endpoints in the same file (e.g. staff-invite, password-reset-request), so this doesn't introduce an inconsistent UX; a legitimate admin retrying an unlock a handful of times per minute is unaffected.
- **Finding 2**: Purely additive — new optional field, default `""`, identical fallback behavior when unset. No existing deployment sets `OTP_PEPPER` today (it didn't exist as a field), so no behavior changes until an operator opts in by setting it.
- **Finding 3**: This is the one with real (intentional) behavior change: an OTP hashed under the old unsalted SHA-256 scheme will no longer verify. Blast radius: `verify_otp_hash` is called only from the OTP-verification path (`routes/auth.py`, `routes/users.py` per grep). Any OTP issued before the 2026-08-22 pepper deploy is 9 days past its ~5-minute TTL and already unusable regardless of hash scheme, so no live/pending OTP is affected. Grepped `backend/tests` for other consumers of `_legacy_sha256`/`verify_otp_hash` — only `test_otp_crypto.py` referenced the removed function directly; updated its test class to assert rejection instead of acceptance. `test_password.py`'s `test_legacy_sha256_*` tests are an unrelated password-hashing legacy path (`utils/password.py`), not touched.
- **Finding 4**: Isolated to a one-off backfill script (`backend/scripts/backfill_imported_ride_routes.py`), not part of the request-serving path. No other module imports this script's functions. Geohashing the coordinates only affects log readability for operators re-running the backfill, not the OSRM request itself (unchanged) or the DB writes (unchanged).

## 5. User-experience effect

- Finding 1: internal-admin-facing only — an admin brute-forcing or rapidly retrying `/admin/auth/unlock` now gets throttled after 10 requests/minute; a legitimate single unlock action is unaffected.
- Findings 2–4: no user-visible effect (backend config default and internal script logging).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/auth.py` | Added `@limiter.limit("10/minute")` to `/unlock` | Finding 1 |
| `backend/core/config.py` | Added `OTP_PEPPER: str = ""` optional field | Finding 2 |
| `backend/utils/crypto.py` | Removed `_legacy_sha256()` and its fallback branch in `verify_otp_hash()` | Finding 3 |
| `backend/tests/test_otp_crypto.py` | Updated `TestLegacyTransitionFallback` → `TestLegacyTransitionFallbackRemoved`, asserting rejection | Finding 3 |
| `backend/scripts/backfill_imported_ride_routes.py` | Imported `geohash` (dual-import pattern); replaced raw lat/lng in two log calls with geohashed values | Finding 4 |

## 7. Before / after

```python
# Before (utils/crypto.py)
def verify_otp_hash(stored_hash: str, input_otp: str) -> bool:
    if hmac.compare_digest(stored_hash, hash_otp(input_otp)):
        return True
    return hmac.compare_digest(stored_hash, _legacy_sha256(input_otp))
```
```python
# After
def verify_otp_hash(stored_hash: str, input_otp: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_otp(input_otp))
```

```python
# Before (backfill_imported_ride_routes.py)
logger.error("OSRM request failed for (%s,%s)->(%s,%s): %s", pickup_lat, pickup_lng, dest_lat, dest_lng, e)
```
```python
# After
logger.error("OSRM request failed for %s->%s: %s", geohash(pickup_lat, pickup_lng), geohash(dest_lat, dest_lng), e)
```

## 8. Rollback plan

- Finding 1: revert the single decorator line — no data implications.
- Finding 2: revert the `Settings` field addition — no data implications (field is unused unless explicitly set in an env that doesn't exist yet).
- Finding 3: revert the removal (`git revert`) — safe since no live data is affected either way; the legacy fallback removal doesn't touch any stored row, only the verification code path.
- Finding 4: revert the logging change — no data/behavior implications, log-format-only.

All four are pure code reverts with no live-data remediation needed (no Stripe charges, wallet deltas, or ride-state writes involved).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_otp_crypto.py backend/tests/test_utils_extended.py backend/tests/test_password.py backend/tests/test_company_email_login.py` (211 passed, 1 skipped), `pytest backend/tests/test_core_config_coverage.py backend/tests/test_admin_settings_write_allowlist_drift.py backend/tests/test_middleware_production_config_guard.py` (53 passed), `pytest backend/tests/test_admin_routes_auth.py backend/tests/test_admin_auth_coverage_gap.py` (47 passed) — all via the repo's existing `/tmp/venv` (pydantic 2.13.4, pytest 9.1.1).
- [ ] Manual repro steps followed in staging — not run against staging; verified via unit tests and manual `python -c` import/geohash sanity check only.
- [x] Blast-radius grep performed: `_legacy_sha256`/`verify_otp_hash`/`OTP_PEPPER` across `backend/` (see §4); `admin_unlock` callers; `geohash` import path.
- [x] Reviewed against CLAUDE.md conventions: PIPEDA no-raw-GPS-in-logs (finding 4), dual-import pattern (finding 4's new import), auth rate-limiting pattern (finding 1).
- [ ] Feature-flagged — not applicable; none of these four are user-visible/non-trivial UX changes requiring a flag.

## 10. What was NOT verified

- No production Supabase/staging run — all verification is against the mocked-DB unit-test suite and local import checks.
- No admin-dashboard UI check for the unlock rate-limit (e.g. confirming the 429 surfaces a sane error toast) — this is a backend-only rate-limit addition, consistent with untouched sibling endpoints' existing UX.

## Deferred (not fixed in this batch)

**Finding 5** (`backend/documents.py` — retried/failed driver document uploads leave prior storage blobs orphaned when superseded) is a product/retention decision, not fixed here: deleting a superseded document's Supabase Storage blob on supersede could destroy the only copy of a driver's ID/license/insurance document needed for a later dispute or admin audit trail, and there is no documented retention policy for *document versions* (only for trip/GPS records, per CLAUDE.md's PIPEDA section). Flagging for a product decision on whether superseded blobs should ever be deleted, and if so, after what retention window — rather than silently shipping a deletion that could regress a compliance/dispute-resolution flow.
