# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (branch: `claude/auth-middleware-deps-coverage`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1 item 3 (auth/RLS-adjacent coverage) |

## 1. Issue / gap identified

Two files sitting on the request-auth hot path and the production fail-fast safety net had real, high-value coverage gaps:

- `backend/dependencies/__init__.py` (the JWT/Firebase auth-gate all authenticated routes depend on via `get_current_user`) was at 62-77% depending on which test subset measured it. The Firebase-token success path (uid lookup, phone-number fallback, Firebase session revocation via `sessions_invalid_before`, driver caching, deleted-account enforcement) had **zero** direct coverage — existing tests only exercised the "token isn't a Firebase token, fall through to JWT" branch. `_verify_admin_payload`'s staff-inactive/stale-token-version/idle-timeout branches and the JWT-path's DB-error propagation were also untested.
- `backend/core/middleware.py` (60-69%): `_validate_production_config` — the function that refuses to let the server boot with a weak JWT secret, missing/placeholder Supabase credentials, default admin creds, or non-Redis rate-limit storage when `ENV=production` — was only ever **patched away** (mocked out via `patch("backend.core.middleware._validate_production_config")`) in `test_p1_cors.py`. It had never been called directly by any test.

## 2. Root cause

Both gaps share the same shape as the two files closed earlier this session (`refresh_tokens.py`, `auth_repo.py`): the *specific incident/security fix* that a function exists for got tested (e.g. `test_p1_cors.py` tests CORS behavior, not the production-config guard it happens to import), but the underlying function itself never got a dedicated direct test.

## 3. Fix / remediation

No application code changed — test-only.

- **`tests/test_dependencies_auth_gaps.py`** (new, 20 tests): Firebase success path (rider-app-id-not-configured → 503, wrong audience → 401, uid-lookup hit, uid-miss-falls-back-to-phone, both-miss → `ServiceUnavailableException`, session-revoked-by-logout-all → 401, driver-flag-set-true, deleted-account → 403); `_verify_admin_payload` (inactive staff, missing staff row, stale token_version, idle timeout, malformed `last_activity_at` lets through, activity-stamp write failure lets through, non-admin payload returns `None`); JWT-path DB-error propagation for both the user lookup and driver lookup (`DatabaseError` propagates untouched, generic exceptions get wrapped as `DatabaseError` — never silently swallowed, per CLAUDE.md); `get_current_user_allow_expired`'s admin-audience-gets-no-grace and not-actually-expired-reraises-original branches.
- **`tests/test_middleware_production_config_guard.py`** (new, 16 tests): non-production short-circuit, all-valid-config passes, each of the 5 checks' failure branches individually parametrized (weak/short JWT secret, missing/placeholder Supabase URL, missing/malformed/short service-role key, default admin email/password, short admin password, missing/non-`redis://` rate-limit URL), multiple-problems-reported-in-one-error, and the Firebase-creds-missing warn-only (non-raising) path.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. Test-only; neither `dependencies/__init__.py` nor `core/middleware.py` was modified.
- `get_current_user` is the auth dependency for effectively every authenticated route in the backend — confirmed via grep that it's imported across `routes/rides/*`, `routes/drivers/*`, `routes/auth.py`, and dozens more. None of those files were touched.
- No bugs found. Every failure-path test written matched the function's documented/existing behavior exactly (e.g. `_validate_production_config`'s 5 checks, `_verify_admin_payload`'s fail-open-on-Redis-down / fail-closed-on-break-glass documented trade-offs).
- No interaction with the ride state machine, wallet/allowance deltas, or the 16 background loops.

## 5. User-experience effect

`none` — test-only change, no application code touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_dependencies_auth_gaps.py` | New file, 20 tests | Close the Firebase-success-path and admin-verification gaps in `dependencies/__init__.py` |
| `backend/tests/test_middleware_production_config_guard.py` | New file, 16 tests | Give `_validate_production_config` its first direct test coverage |
| `ACTION_ITEMS.md` | Updated A1b Track 1 item 3 | Reflect the new measured coverage numbers |

## 7. Before / after

Not applicable — no application code was changed, only test additions.

## 8. Rollback plan

`git-revert-safe` — pure test-file additions and a doc update; a plain `git revert` fully undoes it with no data, schema, or runtime state involved.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_dependencies_auth_gaps.py -q` → 20 passed. `pytest tests/test_middleware_production_config_guard.py -q` → 16 passed.
- [x] Full backend suite re-run (not just a keyword-filtered subset, given how central `get_current_user` and the production-config guard are): `pytest tests/ -q` → **5553 passed, 8 skipped, 1 xfailed, 0 failed** (up from 5537 before this change — the 16 new middleware tests plus 20 - 20 already counted from the dependencies pass in the run before). Real `pytest-cov` output: `dependencies/__init__.py 287 21 93%` (remaining 21 lines: dual-import fallback + a handful of log-statement-only branches inside already-exercised except clauses), `core/middleware.py 233 44 81%` (remaining 44 lines: four nested middleware classes defined inside `init_middleware(app)` — App Check enforcement, CORS exception handler, relative-redirect rewriting, deadline propagation — which need `TestClient`-level request testing rather than direct unit tests, not pursued in this pass, diminishing returns for a P0 backlog item).
- [ ] Manual repro steps followed in staging — not applicable, test-only change.
- [x] Blast-radius grep performed — see §4 above.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — the DB-error-propagation tests specifically pin the "never silently swallow errors" rule (`DatabaseError`/`ServiceUnavailableException` propagate untouched; unexpected exceptions get wrapped as `DatabaseError`, never a soft warning-and-continue).
- [ ] Feature-flagged — not applicable, test-only change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — isolated to test files + one doc update, `get_current_user`'s real callers noted as unmodified
- [x] No silent behavior change to an already-shipped flow — none occurred; UX field states `none` explicitly

## What was NOT verified

- Not tested against a real Supabase/Firebase instance — all DB and Firebase Admin SDK calls are mocked, consistent with both modules' existing test conventions.
- `core/middleware.py`'s four nested middleware classes (App Check, CORS-exception-handler, relative-redirect, deadline-propagation) remain below the file's overall 81% — they're defined inside `init_middleware(app)`, so testing them requires spinning up a `TestClient` against the real app rather than direct unit tests. Tracked as a smaller follow-up, not blocking this pass.
- `routes/auth.py` (51%) and `routes/admin/auth.py` (64-70%) — the two largest remaining files in A1b Track 1 item 3 — were measured but not started this pass.
