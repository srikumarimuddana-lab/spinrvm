# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (branch: `claude/routes-auth-coverage`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1 item 3 (auth/RLS-adjacent coverage) |

## 1. Issue / gap identified

`backend/routes/auth.py` was at 51-55% coverage. `verify_otp` — the core rider/driver login/signup endpoint (382 lines, the single largest function in the file) — had **zero** direct coverage of its success path. Existing tests (`test_auth_send_otp.py`) only pinned the SEC-008 lockout helpers and the "a DB read failure during OTP lookup is not a wrong code" 503 case.

## 2. Root cause

Same shape as every gap closed earlier in this A1b pass: the specific production incident (`_check_otp_lockout` NameError, the "DB error miscounted as wrong code" bug) got a regression test, but the endpoint's actual login/signup logic — existing-user login, new-user creation, the PIPEDA deletion-grace-window handoff — was never directly tested.

## 3. Fix / remediation

No application code changed — test-only. Added `tests/test_verify_otp_login_flow.py` (13 tests):

- **Existing-user login**: happy path returns a valid `AuthResponse` (token + refresh token); a guest account (provisioned by a corporate guest booking) has its `is_guest` flag cleared on verify since the phone owner just proved possession; a `current_session_id` write failure is logged but does not block login (best-effort, matching the endpoint's own comment).
- **PIPEDA branches**: a `pending_deletion` account gets the reactivation-token JSON handoff instead of normal tokens (must not silently undelete); a fully `deleted`/purged account gets a 410, never a token.
- **DB-error propagation**: `get_user_by_phone` raising surfaces as 503 (never silently falls through to "create new user", which would fork a duplicate account — CLAUDE.md).
- **New-user creation**: happy path persists the right payload shape (`role: "rider"`, `token_version: 0`, etc.) and returns `is_new_user: True`; `create_user` raising surfaces as 503 and never mints a token for a row that was never actually persisted.
- **OTP-record validation**: wrong code records a failure and 400s; expired code 400s and deletes the record; malformed/missing `expires_at` 500s (data-integrity guard); a `delete_otp_record` failure after successful verification falls back to marking the record verified instead (best-effort, login still completes).

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. Test-only; `routes/auth.py` was not modified. Grepped repo-wide for real (non-test) importers of the module — only `server.py` (the router mount point), unmodified.
- No bugs found. Every branch tested matched the endpoint's documented behavior and inline comments exactly (e.g. the fail-safe "never auto-create on a DB error" guarantee, the guest-claim-on-verify behavior, the best-effort OTP-cleanup fallback).
- No interaction with the ride state machine or the 16 background loops. Does touch wallet-adjacent state only in that a new `users` row is created — no wallet/allowance delta happens in this endpoint.

## 5. User-experience effect

`none` — test-only change, no application code touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_verify_otp_login_flow.py` | New file, 13 tests | Close the `verify_otp` success-path coverage gap |
| `ACTION_ITEMS.md` | Updated A1b Track 1 item 3 | Reflect the new measured coverage number and scope the remaining gap in this file |

## 7. Before / after

Not applicable — no application code was changed, only test additions.

## 8. Rollback plan

`git-revert-safe` — pure test-file addition and a doc update; a plain `git revert` fully undoes it with no data, schema, or runtime state involved.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_verify_otp_login_flow.py -q` → 13 passed.
- [x] Full backend suite re-run (not a keyword-filtered subset, given how central this endpoint is): `pytest tests/ -q` → **5566 passed, 8 skipped, 1 xfailed, 0 failed** (up from 5553 before this change). Real `pytest-cov` output: `routes/auth.py 671 209 69%` (up from 51-55% depending on which prior measurement).
- [ ] Manual repro steps followed in staging — not applicable, test-only change.
- [x] Blast-radius grep performed — see §4 above.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — the DB-error tests specifically pin "never silently swallow a DB error and continue" (surfacing 503s, never falling through to auto-create).
- [ ] Feature-flagged — not applicable, test-only change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — isolated to one test file + one doc update, real caller (`server.py`) confirmed unmodified
- [x] No silent behavior change to an already-shipped flow — none occurred; UX field states `none` explicitly

## What was NOT verified

- Not tested against a real Supabase instance — all DB calls are mocked, consistent with this module's existing test convention (`test_auth_send_otp.py` uses the same `_resolve_inner`-then-call-the-handler-directly pattern).
- `routes/auth.py` still has substantial untested scope after this pass: the company-email-OTP flow (`send_company_email_otp`/`verify_company_email_otp`), `firebase_auth_login`, `refresh_access_token`, `logout`/`logout_all`, and `reactivate_account` — none of these endpoints have direct tests yet. Tracked as still-open in `ACTION_ITEMS.md`; this was a deliberate scoping decision (verify_otp was the single largest, highest-value target) rather than an oversight.
- `routes/admin/auth.py` (64-70%), the other large file in this A1b track item, was measured but not started this pass.
