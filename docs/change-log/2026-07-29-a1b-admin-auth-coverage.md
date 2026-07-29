# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (agent session, A1b Track 1 item 3) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (auth-adjacent) |
| PR / commit link | claude/admin-auth-coverage (see PR description for URL) |
| Related issue or gap ID | ACTION_ITEMS.md → A1b Track 1, item 3 (`routes/admin/auth.py`) |

## 1. Issue / gap identified

`backend/routes/admin/auth.py` — the admin login/MFA/break-glass/session
module — was at 70% test coverage (measured fresh this session; the
previously-tracked figure was "64-70%"), below the ≥70% admin-routes floor
in `CLAUDE.md`'s coverage-minimums table, with two entire endpoints
(`/admin/auth/break-glass` and `/admin/auth/unlock`) carrying **zero**
direct test coverage despite `break-glass` being a super-admin-minting
emergency-access endpoint.

## 2. Root cause

Not a bug — a coverage gap. Nine sibling test files already covered
login/MFA/refresh/logout flows thoroughly (see file list in section 6), but
no test file had ever been written against `break_glass_access` or
`admin_unlock` directly. `dependencies.py`'s break-glass *verification*
path (the JWT/allowlist gate in `_verify_admin_payload`) was covered by
`test_admin_logout_revocation.py`, which likely gave a false sense that the
break-glass *route* itself (token minting, rate limiting, audit logging)
was covered too — it was not.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_admin_auth_coverage_gap.py`
(33 new unit tests, all mocking `db_supabase`/`redis_client` per the
`mock_supabase_client`/direct-function-call convention used by the sibling
files) covering:

- `/admin/auth/break-glass`: feature-gated-off (404), justification too
  short (400), Redis-unreadable rate counter / increment / allowlist-write
  failures (each fails closed, 503), rate limit exceeded (429), invalid
  token (401), and the happy path including the audit-log-write failure
  being logged but **not** blocking token issuance (matches the
  documented "operator is in an emergency" behavior).
- `/admin/auth/unlock`: non-super-admin role guard (403), empty email
  (422), target not found (404), idempotent not-locked response, Redis
  read failure (503 fail-closed), and successful unlock.
- `/admin/auth/mfa/status`: unauthenticated, non-bearer scheme, malformed
  token, admin-001 short-circuit, staff-not-found, enabled/disabled state.
- `/admin/auth/mfa/enroll`: happy path (secret + otpauth URI minted).
- `/admin/auth/session`: malformed `Authorization` header shapes
  (`"Bearer"` alone, wrong scheme).
- `/admin/auth/refresh`: admin-001 (env-var super-admin) branch, wrong
  audience.
- `/admin/auth/logout-all`: missing/malformed-token branches.
- `_is_totp_locked` / `_record_totp_failure` / `_clear_totp_failures`:
  Redis-unavailable fail-closed/logged-not-raised branches.

One gotcha discovered while writing the break-glass tests: the endpoint
does a **local** re-import of the redis helpers inside the function body
(`from utils.redis_client import redis_get, ...`) rather than using the
module-level names imported at the top of `auth.py`. Patching
`admin_auth.redis_get` (the pattern every sibling test file uses) silently
no-ops for this one function — the tests must patch `utils.redis_client`
directly instead. This is a **pre-existing structural quirk in the
production code**, not something these tests introduced; flagging it here
since it could similarly trip up a future contributor patching this
endpoint's Redis behavior and getting silent no-op mocks. Not fixed as
part of this test-only PR per the task's constraints.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** This PR adds one new test file
  (`backend/tests/test_admin_auth_coverage_gap.py`) and two documentation
  updates (`ACTION_ITEMS.md`, this file). Zero production code changed.
- Grepped for every importer of `admin_auth_router` / `routes.admin.auth`:
  only `backend/server.py` (`app.include_router(admin_auth_router,
  prefix="/api")`) and `backend/routes/admin/__init__.py` (re-exports it
  for `server.py`'s import). No other module imports functions from this
  file directly.
- No shared table, background loop, or ride/dispatch/payment code path is
  touched.

## 5. User-experience effect

None. Test-only change — no rider, driver, corporate-admin, or internal-admin-facing behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_auth_coverage_gap.py` | New file: 33 unit tests | Close the break-glass/unlock/mfa-status/mfa-enroll/session/refresh/logout-all coverage gap |
| `ACTION_ITEMS.md` | Updated A1b Track 1 item 3 entry | Record 70%→94% closure for `routes/admin/auth.py` |
| `docs/change-log/2026-07-29-a1b-admin-auth-coverage.md` | New file (this doc) | Mandatory Change Impact & Risk Log for an auth-surface change per `CLAUDE.md` |

## 7. Before / after

Not applicable — purely additive (new test file + docs), no existing behavior-changing diff.

## 8. Rollback plan

`git revert` is sufficient and complete: this PR adds only test files and
markdown documentation. No production code, migration, feature flag,
Stripe charge, wallet delta, or ride state is touched, so there is no data
to remediate on rollback.

## 9. Verification performed

- [x] Automated tests run — full backend suite: `cd backend && python -m
      pytest tests/ -q` → **5586 passed, 8 skipped, 1 xfailed** in 735s,
      zero failures (baseline run before this PR's tests were added: 5553
      passed, 8 skipped, 1 xfailed — the +33 new tests all pass, no
      regressions). Coverage of `routes/admin/auth.py` measured via the
      same full-suite run (per-file coverage is only accurate against the
      full suite, not a `-k`-filtered subset): **486 statements, 30
      missed, 94%** (up from 486 statements / 144 missed / 70% baseline).
      Real pytest-cov output, both baseline and final:
      ```
      # baseline (this session, before new tests)
      routes/admin/auth.py   486   144   70%   87-95, 99-107, 113-114, 137, 258-260, 279, 302,
        402-403, 463, 467, 471-493, 497, 573, 609-614, 644-645, 827-829, 852, 857, 878, 903,
        937-967, 974-978, 1001, 1003, 1077, 1082, 1117, 1120, 1123, 1129, 1145, 1208-1360,
        1399-1458

      # final (this session, after new tests)
      routes/admin/auth.py   486    30   94%   88, 105, 137, 279, 302, 402-403, 467, 497, 573,
        644-645, 827-829, 852, 857, 878, 903, 1001, 1003, 1077, 1082, 1117, 1120, 1123, 1129,
        1145, 1240-1241
      ```
- [x] Blast-radius grep performed: `grep -rn "admin_auth_router\|from
      .routes.admin import auth\|admin/auth import"` across `backend/`
      (excluding tests) — only `server.py` and `routes/admin/__init__.py`
      reference this module.
- [x] Reviewed against relevant `CLAUDE.md` conventions: JWT trust model
      (admin JWTs fully trusted — tests assert `aud`/`token_version`
      claims round-trip correctly), "do not silently swallow errors"
      (break-glass fail-closed-on-Redis-error branches specifically
      tested), PIPEDA (no raw email/PII asserted or logged in test
      fixtures — `_log_safe_email` hashing already covered by
      `test_admin_auth_log_redaction.py`, not duplicated here).
- [ ] Manual repro steps followed in staging — not performed; this is a
      backend Python unit-test-only change, no staging deploy needed to
      verify test correctness.
- [ ] Feature-flagged — not applicable, no behavior change.

## 10. What was NOT verified

- No production build (`npm run build` or equivalent) was run — not
  applicable, this PR touches only `backend/` Python test files and
  markdown, no frontend surface.
- The full suite was run once (735s), not repeated for flake detection;
  no new flakiness was observed but a single run cannot rule out rare
  intermittent failures.
- Coverage was measured via the repo's default `--cov=.` (pytest.ini),
  not an explicit `--cov=routes.admin.auth` flag, because the explicit
  flag reproducibly hits the known `KeyError: 'pydantic.root_model'`
  pyiceberg/supabase-py import-race error in this environment (retried
  once per task instructions, still failed) — the per-file coverage line
  quoted above was extracted from the full-suite run's terminal report,
  which is the documented reliable fallback.
- The local-import quirk in `break_glass_access` (see section 3) was
  discovered and worked around in the new tests, but not fixed — flagged
  here and in `ACTION_ITEMS.md`'s entry for visibility, not remediated,
  since this PR is scoped to test-only coverage closure per the task's
  constraints.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data impact)
- [x] Blast radius is stated, not assumed (isolated — single caller, grepped and confirmed)
- [x] No silent behavior change to an already-shipped flow — none occurred; this is additive test coverage only
