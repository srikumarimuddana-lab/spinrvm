# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth, admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md D8 |

## 1. Issue / gap identified

Three SIN-touching admin endpoints — `POST /admin/drivers/{id}/reveal-sin`, `POST /admin/drivers/{id}/update-sin`, and `POST /admin/tax-ids/import/{validate,commit}` — had zero rate limiting, flagged by the 2026-08-10 security audit of the SIN-enforcement branch.

## 2. Root cause

These four endpoints were never given a `default_limiter.limit(...)` decorator when they were originally built (or, for `reveal-sin`, a pre-existing gap). Every other admin write path in `routes/admin/` that touches sensitive data (wallet mutations, staff deletion, bulk imports) already has one; these four were an inherited omission, not a deliberate decision.

## 3. Fix / remediation

- Added 4 new limiter objects to `utils/rate_limiter.py`: `admin_sin_reveal_limit` (10/hour), `admin_sin_update_limit` (10/hour), `tax_id_import_validate_limit` (30/hour), `tax_id_import_commit_limit` (10/hour).
- 10/hour for reveal/update is D8's own suggested figure. The import pair mirrors the existing validate/commit asymmetry already used by `data_transfer_import_*_limit` / `booking_import_*_limit` / `driver_import_commit_limit` — validate (read-only dry-run) gets the looser 30/hour, commit (the write path, up to `MAX_ROWS`=500 rows/call) gets the tighter 10/hour.
- All 4 are keyed **per-admin**, not per-IP, via the existing `get_user_or_ip_key` function (unmodified) — a deliberate deviation from every other `admin_*` limiter in the file, which defaults to IP keying. D8 explicitly asks for "10/hour per admin"; IP keying would let multiple super_admins sharing an office/VPN egress IP silently share one bucket, or under-count a single admin who rotates IPs. Confirmed admin JWTs carry a `user_id` claim (`routes/admin/auth.py::_mint_admin_access_token`), so `get_user_or_ip_key`'s unverified-JWT-decode key extraction works correctly here — no new key function was needed.
- Wired the decorators onto all 4 endpoints in `routes/admin/drivers.py` and `routes/admin/tax_id_import.py`. None of the 4 previously took a `request: Request` parameter — `AsyncLimiter.limit` requires one to find the connection object to key on — so each endpoint's signature gained one.
- Still defense-in-depth only, per the audit's own framing: all 4 endpoints remain super_admin-gated and audit-logged (`log_admin_action`, written before any decrypt/write) independently of this change.

## 4. Risk & impact on existing functionality

- **Blast radius of the touched shared code (`get_user_or_ip_key`, `default_limiter`)**: grepped every consumer across `backend/` (excluding tests) — `routes/marketing.py`, `routes/admin/auth.py`, `routes/admin/compliance.py`, `routes/admin/sentry.py`, `routes/admin/rides.py`, `routes/auth.py`, `routes/branding.py`, `routes/offer_card.py`, `routes/webhooks.py`, `routes/corporate_signup.py`, `routes/maps_proxy.py`, plus every `*_limit = default_limiter.limit(...)` definition already in `utils/rate_limiter.py` (api_rate_limit, ride_request_limit, cancel_ride_limit, ride_read_limit, location_update_limit, payment_action_limit, ride_rating_limit, ride_message_limit, ride_action_limit, ai_chat_limit, and every admin_* limiter) and `core/middleware.py`. Neither `get_user_or_ip_key` nor `default_limiter` was modified — this change only adds 4 new module-level limiter objects built from them. No other caller's behavior changes.
- **The 4 touched endpoint files (`routes/admin/drivers.py`, `routes/admin/tax_id_import.py`)**: adding a `request: Request` parameter to a FastAPI route handler is additive and does not change dependency resolution order or any existing parameter's binding — confirmed by the full existing test suites for both files passing unchanged (`test_admin_drivers_coverage.py`, `test_admin_tax_id_import.py`).
- **Existing tests exercise the "under-limit still succeeds" case implicitly**: conftest's autouse `reset_rate_limiters` fixture sets `default_limiter.enabled = False` for every test, so all pre-existing 200/403/404/422/502 assertions for these 4 endpoints (`TestRevealSin`, the update-sin tests, and every `test_admin_tax_id_import.py` case) continue to pass with the limiter attached but disabled — proving the decorator doesn't break the existing call path.
- **New failure mode this introduces**: a legitimate super_admin doing unusually heavy SIN work (e.g. a large T4A-season correction batch) could now hit a 429 they'd never have hit before. 10/hour comfortably covers the documented legitimate case (single-digit support tickets/admin/day per the code comment); if this proves too tight in practice the limiter value is a one-line change, no migration or flag needed.

## 5. User-experience effect

- **Internal-admin facing only** — no rider/driver/corporate-admin visible change.
- A super_admin who exceeds 10 reveal-sin or update-sin calls, or 10 tax-ID-import commits, in a rolling hour now gets a 429 with the existing standard rate-limit error body (`rate_limit_exceeded`, `retry_after`, `Retry-After` header) instead of the request always succeeding. This is a new, visible-in-the-moment behavior change for that one admin session, but only under an access pattern (>10 SIN reveals/updates per hour) the audit itself flagged as unbounded and undesirable.
- Not visible mid-session to anyone other than the admin actively hitting the endpoint.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | Added `admin_sin_reveal_limit`, `admin_sin_update_limit`, `tax_id_import_validate_limit`, `tax_id_import_commit_limit` | New per-admin-keyed limiters for D8 |
| `backend/routes/admin/drivers.py` | `reveal-sin`/`update-sin` gained `request: Request` param + `@admin_sin_reveal_limit`/`@admin_sin_update_limit` decorators; `Request` added to the fastapi import; new rate_limiter import (both dual-import branches) | Wire the new limiters onto the two SIN endpoints |
| `backend/routes/admin/tax_id_import.py` | `validate`/`commit` gained `request: Request` param + `@tax_id_import_validate_limit`/`@tax_id_import_commit_limit` decorators; `Request` added to the fastapi import; new rate_limiter import (both dual-import branches) | Wire the new limiters onto the two bulk-import endpoints |
| `backend/tests/test_admin_sin_rate_limiting.py` (new) | 5 tests proving the real `AsyncLimiter`/`MemoryStorage` mechanics at each configured rate, plus a per-admin-not-per-IP keying proof | Regression coverage for D8 |
| `ACTION_ITEMS.md` | D8 marked `[x]` with implementation specifics | Close out the tracked item |

## 7. Before / after

```python
# Before (routes/admin/drivers.py)
@router.post("/drivers/{driver_id}/reveal-sin")
async def admin_reveal_driver_sin(driver_id: str, admin: dict = Depends(get_admin_user)):
    ...
```

```python
# After
@router.post("/drivers/{driver_id}/reveal-sin")
@admin_sin_reveal_limit
async def admin_reveal_driver_sin(request: Request, driver_id: str, admin: dict = Depends(get_admin_user)):
    ...
```

```python
# Before (routes/admin/tax_id_import.py)
@router.post("/tax-ids/import/commit")
async def commit_tax_id_import(
    tax_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    ...
```

```python
# After
@router.post("/tax-ids/import/commit")
@tax_id_import_commit_limit
async def commit_tax_id_import(
    request: Request,
    tax_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    ...
```

## 8. Rollback plan

- Pure application-logic addition, no migration, no data touched. `git revert` is a sufficient rollback for this specific change (unlike a money/ride-state change) because nothing here is applied to live data — the limiter is stateless request-time gating, not a stored value.
- Faster than a revert if only the *value* needs adjusting (e.g. 10/hour proves too tight): edit the `"10/hour"` / `"30/hour"` string literals in `utils/rate_limiter.py` and redeploy — no flag or migration involved either way, this project's `app_settings`-in-DB flag pattern does not currently cover rate-limit values.
- If the limiter needs to come off entirely without a deploy: none of the 4 limiter definitions are wired through a DB-backed flag today, so removing them requires a code deploy either way — noted as a gap, not solved by this change.

## 9. Verification performed

- [x] Automated tests: `/tmp/spinr-venv/bin/python -m pytest tests/test_admin_sin_rate_limiting.py tests/test_admin_drivers_coverage.py tests/test_admin_tax_id_import.py tests/test_admin_driver_import.py -q --no-cov` from `backend/` — **154 passed, 0 failed**.
- [x] `ruff format --check` on all 4 touched Python files — clean.
- [x] `ruff check` on all 4 touched Python files — 2 pre-existing `B904` findings at `routes/admin/drivers.py:3034` and `:3061`, both in `refresh-stripe-kyc` code this change never touches (confirmed via `git diff --stat` showing only additive lines in this file) — not introduced by this change, not fixed here (out of scope).
- [x] Blast-radius grep performed for every consumer of `get_user_or_ip_key` and `default_limiter` across `backend/` (excluding tests) — listed in §4; both are unmodified, only additive new limiter objects were added.
- [x] Backend package imports cleanly under both dual-import branches (`test_admin_driver_import.py`'s own conftest preload exercises `import backend.server`, which transitively imports `routes/admin/__init__.py` → `drivers.py` and `tax_id_import.py`).

## What was NOT verified

- Not tested against a real multi-replica deployment or real Redis-backed rate-limit storage — all coverage uses the in-process `MemoryStorage` path (this repo's standard rate-limit test pattern; `RATE_LIMIT_REDIS_URL` is unset in the test environment).
- Not manually exercised against a live/staging admin session with a real super_admin JWT — the new tests construct synthetic JWTs directly rather than going through the real admin login flow.
- No visual/UI check of what the admin dashboard shows on a 429 from these specific endpoints — the response body/headers match the existing, already-tested `rate_limit_exceeded_handler` contract used by every other rate-limited admin endpoint, so this was reasoned about rather than screenshotted (no visual regression tooling exists in this repo for the admin dashboard, a standing gap tracked elsewhere in ACTION_ITEMS.md).
- The 10/hour and 30/hour figures are a judgment call informed by D8's own suggestion and the existing sibling-endpoint conventions, not validated against real admin usage telemetry (no such telemetry exists yet for these specific endpoints).
