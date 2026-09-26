# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, fix/5623-admin001-reuse-detection) |
| Related issue or gap ID | #5623 (finding 2) |

## 1. Issue / gap identified

`backend/utils/refresh_tokens.py`'s `_handle_refresh_token_reuse()` — the cascade that fires when a revoked refresh token is replayed (suspected theft) — bumped `token_version` on `users` or `admin_staff`, but explicitly skipped admin-001 (the env-var-credentials super admin) with a comment saying it "has no row to bump." Migration 434 (already merged, `utils/env_admin_tokens.py`) gave admin-001 its own revocable `token_version` on the `settings` row, and `/admin/auth/logout-all` already bumps it — but the reuse-detection cascade was never updated to match.

## 2. Root cause

The comment and skip logic predate migration 434. When 434 landed and gave admin-001 a real revocation mechanism, only the explicit logout-all path was wired to use it; the reuse-detection cascade (a different code path, not touched by that migration's PR) kept its original "nothing to do here" behavior.

## 3. Fix / remediation

`_handle_refresh_token_reuse()` now calls `bump_env_admin_token_version()` when `user_id == ENV_ADMIN_USER_ID`, mirroring the `admin_staff` branch and matching what `/admin/auth/logout-all` already does. A detected replay of a stolen admin-001 refresh token now actually invalidates its live access tokens (up to 1hr per CLAUDE.md's token-lifetime table) instead of leaving them valid through natural expiry.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `_handle_refresh_token_reuse`**. Grepped every other caller of `bump_env_admin_token_version()` — only `/admin/auth/logout-all` (`routes/admin/auth.py:708`) — confirmed this fix reuses the exact same function, not a new write path, so no new failure mode is introduced.
- `bump_env_admin_token_version()` raises `DatabaseError` on failure rather than silently swallowing (per its own docstring) — this is caught by the existing `try/except Exception` wrapping the whole token_version-bump step in `_handle_refresh_token_reuse`, which already logs the error and continues (best-effort, matches every other step in this cascade per its own docstring: "each best-effort, none crash the caller").
- No other audience/user_id branch is affected: the `elif audience in _ADMIN_STAFF_AUDIENCES and user_id` branch no longer needs its own `and user_id != "admin-001"` guard because admin-001 is now handled in an earlier, mutually exclusive branch — verified this doesn't change behavior for any other admin_staff user_id.
- Does not touch the rider/driver (`_USERS_TABLE_AUDIENCES`) path at all.

## 5. User-experience effect

Internal-admin-facing only, and only in the rare case of a detected stolen admin-001 refresh token. Not visible mid-session under normal operation — only changes behavior at the moment reuse is detected, by forcing the next admin-001 request (on any device, including a legitimate one) to re-authenticate sooner than it otherwise would have.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/refresh_tokens.py` | `_handle_refresh_token_reuse` now bumps admin-001's env-admin token_version via `bump_env_admin_token_version()` instead of skipping it | Close the gap where a detected stolen admin-001 refresh token didn't revoke its live access tokens |
| `backend/tests/test_refresh_token_reuse_detection.py` | Replaced `test_cascade_skips_token_version_for_admin_001_super_admin` (asserted the old, buggy skip behavior) with `test_cascade_bumps_env_admin_token_version_for_admin_001_super_admin` | Pin the new, correct behavior |

## 7. Before / after

```python
# Before
if audience in _USERS_TABLE_AUDIENCES:
    target_table = "users"
elif audience in _ADMIN_STAFF_AUDIENCES and user_id and user_id != "admin-001":
    # admin-001 is the env-var-creds super admin — has no row to bump.
    target_table = "admin_staff"
if target_table and user_id:
    ...
```

```python
# After
if user_id == ENV_ADMIN_USER_ID:
    new_version = await bump_env_admin_token_version()
else:
    if audience in _USERS_TABLE_AUDIENCES:
        target_table = "users"
    elif audience in _ADMIN_STAFF_AUDIENCES and user_id:
        target_table = "admin_staff"
    if target_table and user_id:
        ...
```

## 8. Rollback plan

`git revert` — this is a pure code-path change with no schema/migration touched (migration 434 already exists and is unaffected). Reverting restores the prior skip behavior; no data-level remediation needed since no already-applied state depends on this change.

## 9. Verification performed

- [x] Automated tests run: `tests/test_refresh_token_reuse_detection.py` (40 tests, including the rewritten admin-001 test), `tests/test_auth.py`, `tests/test_refresh_tokens_lifecycle.py`, `tests/test_websocket_token_revocation.py`, `tests/test_refresh_generation_binding.py` — all pass (89 total in the broader run).
- [ ] Manual repro in staging — not performed, no staging environment reachable from this session.
- [x] Blast-radius grep performed: every caller of `bump_env_admin_token_version()` (see §4).
- [x] Reviewed against CLAUDE.md's "do not silently swallow errors" convention — the fix relies on the existing try/except's logging, matching the pattern the rest of this cascade already uses for every other best-effort step.
- [ ] Feature-flagged — not flagged; this closes a gap in an existing security control, not a new user-visible behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated: isolated to the reuse-detection cascade's admin-001 branch
- [x] No silent behavior change to an already-shipped flow without the UX field filled in

## What was NOT verified

- Not tested against a live/staging backend or a real `settings` row — verified via the existing mocked-Supabase unit test tier only, consistent with this repo's normal unit-test conventions for this file.
- Did not verify the `/admin/auth/logout-all` code path itself (unchanged by this fix) beyond confirming it calls the same `bump_env_admin_token_version()` function this fix now also calls.
