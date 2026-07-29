# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (branch: `claude/auth-refresh-tokens-coverage`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1 item 3 (auth/RLS-adjacent coverage) |

## 1. Issue / gap identified

Two auth-critical files were below CLAUDE.md's implied bar for security-sensitive code:

- `backend/utils/refresh_tokens.py` (62%) — the module owning refresh-token minting, lookup, revocation, and the OAuth2 BCP §4.14.2 theft-cascade escalation. The existing test file (`test_refresh_token_reuse_detection.py`) thoroughly pins `_handle_refresh_token_reuse` (the cascade), but `issue_refresh_token` (minting), most of `lookup_refresh_token`'s branches, `revoke_refresh_token`, and `revoke_all_for_user` had **zero** coverage.
- `repositories/auth_repo.py` (67%) — user lookup/creation and OTP CRUD helpers, with no dedicated test file at all; whatever coverage existed was incidental, from other tests exercising the auth routes that call through it.

## 2. Root cause

Both gaps are the same shape: the reuse-detection *incident response* (a specific, high-visibility security fix) got a thorough test file, but the *plumbing* those functions depend on — and that every login/refresh/logout request actually exercises — was never given its own direct tests. Coverage came only incidentally from whatever routes/tests happened to call through it.

## 3. Fix / remediation

No application code changed — test-only.

- **`tests/test_refresh_tokens_lifecycle.py`** (new, 24 tests): `_parse_iso_dt`'s type branches (None, naive/aware datetime, bad string, unknown type), `_is_benign_rotation_replay`'s unparseable-`revoked_at` branch, `issue_refresh_token` (basic mint, user-agent/IP truncation and empty→None coercion, rotation chaining via `replaces`, and confirming no chaining call when `replaces` is omitted), `lookup_refresh_token`'s remaining branches (empty input short-circuit, DB error, not-found, unparseable expiry, naive-expiry-treated-as-UTC, expired), `revoke_refresh_token` (empty input, DB error, not-found, already-revoked, success, update failure), and `revoke_all_for_user` (scan error, mixed active/already-revoked rows, empty result).
- **`tests/test_refresh_token_reuse_detection.py`** (extended, +1 test): a Sentry-capture-failure test closing the last real branch in `_handle_refresh_token_reuse` — confirms a `sentry_sdk.capture_message` failure doesn't interrupt the cascade (the `logger.error` immediately before it already carries the same signal via the loguru→Sentry bridge).
- **`tests/test_auth_repo.py`** (new, 18 tests): for all 8 functions (`get_user_by_id`, `get_user_by_phone`, `create_user`, `insert_otp_record`, `get_otp_record`, `get_otp_record_by_phone`, `verify_otp_record`, `delete_otp_record`) — the "Supabase client not configured" branch (some return `None`, others raise `RuntimeError`, per each function's documented contract) and the happy path hitting the right table/filters. Also covers `get_user_by_id`'s Redis read-through cache: cache-hit short-circuit, the `{}` negative-cache sentinel, and cache-miss-then-write.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. Test-only; neither `utils/refresh_tokens.py` nor `repositories/auth_repo.py` was modified.
- Real (non-test) callers grepped repo-wide: `routes/auth.py`, `routes/users.py`, `routes/admin/auth.py`, `routes/admin/staff.py` (all call into `refresh_tokens.py`'s public functions) — none modified.
- No bugs found in either file. `refresh_tokens.py`'s fail-safe behavior (DB errors return `None`/`False`/`0` rather than raising, matching each function's documented contract) held up under every failure-path test written.
- No interaction with the ride state machine, wallet/allowance deltas, or the 16 background loops.

## 5. User-experience effect

`none` — test-only change, no application code touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_refresh_tokens_lifecycle.py` | New file, 24 tests | Close the mint/lookup/revoke lifecycle gap in `utils/refresh_tokens.py` |
| `backend/tests/test_refresh_token_reuse_detection.py` | +1 test | Close the Sentry-capture-failure branch in the reuse cascade |
| `backend/tests/test_auth_repo.py` | New file, 18 tests | Give `repositories/auth_repo.py` its first dedicated test file |
| `ACTION_ITEMS.md` | Updated A1b Track 1 item 3 | Reflect the new measured coverage numbers |

## 7. Before / after

Not applicable — no application code was changed, only test additions.

## 8. Rollback plan

`git-revert-safe` — pure test-file additions and a doc update; a plain `git revert` fully undoes it with no data, schema, or runtime state involved.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_refresh_tokens_lifecycle.py tests/test_refresh_token_reuse_detection.py -q` → 44 passed; real `pytest-cov` output: `utils/refresh_tokens.py 157 1 99% 36` (line 36 is the dual-import fallback, unreachable under this repo's standard pytest invocation). `pytest tests/test_auth_repo.py -q` → 18 passed; `repositories/auth_repo.py 49 2 96% 20-21` (same dual-import fallback pattern).
- [x] Broader regression check: `pytest tests/ -k "auth or jwt or otp or refresh_token or crypto or mfa" -q` → 474 passed, 1 skipped, 0 failed.
- [ ] Manual repro steps followed in staging — not applicable, test-only change.
- [x] Blast-radius grep performed — see §4 above.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — patch targets follow the module's existing `AsyncMock`/`patch` pattern (`utils.refresh_tokens.db.*`, `repositories.auth_repo.supabase`), consistent with sibling test files in this repo (e.g. `test_corporate_membership_db_helpers.py`'s supabase-chain mocking pattern).
- [ ] Feature-flagged — not applicable, test-only change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — isolated to test files + one doc update, real callers identified and confirmed unmodified
- [x] No silent behavior change to an already-shipped flow — none occurred; UX field states `none` explicitly

## What was NOT verified

- Not tested against a real Supabase instance — all DB calls are mocked, consistent with both modules' existing test conventions.
- Did not re-run the full 5000+-test backend suite end-to-end; scoped the regression check to the `-k "auth or jwt or otp or refresh_token or crypto or mfa"` subset (474 tests) per this session's established "keep the diff scoped" pattern.
- `utils/crypto.py` was re-verified at 100% (exceeds its ≥90% CLAUDE.md target) but no new tests were added there since none were needed.
- Item 3's larger remaining files (`routes/auth.py` 51%, `routes/admin/auth.py` 64-70%, `core/middleware.py` 60%, `dependencies/__init__.py` 62%) were measured but not started — tracked as still-open in `ACTION_ITEMS.md`.
