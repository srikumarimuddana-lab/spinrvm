# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W5 |

## 1. Issue / gap identified

`POST /admin/auth/change-password` (`change_password`) resolved the caller
with a bare `jwt.decode(...)` instead of this file's own shared
`_require_staff_from_token` helper — every other authenticated admin
action in `routes/admin/auth.py` goes through that helper. As a result this
one endpoint alone never checked `staff.is_active`, the per-JTI
`admin:revoked:{jti}` denylist (`/admin/auth/logout` single-token
revocation), or the `token_version` gate (the field `/admin/auth/logout-all`
bumps to force-invalidate every session). It also never re-verified a TOTP
code even when the account had MFA enrolled.

**Concrete exploit scenario:** an admin who suspects their session is
compromised calls `/admin/auth/logout-all`, which bumps `token_version` and
revokes every refresh token specifically to force-invalidate every existing
access token. An attacker who separately captured a still-unexpired (≤1h
TTL) access token — and, separately, obtained the account's current
password (phish, shoulder-surf, reused credential) — could still call
`change-password` with that stale token, since it was never checked
against `token_version`, `is_active`, or the JTI denylist, and never
required proving possession of the account's TOTP device even if MFA was
enrolled. This defeated both the logout-all revocation guarantee and MFA
for this one action.

## 2. Root cause

`change_password` predates (or was written independently of) the
`_require_staff_from_token` helper's introduction and was never migrated
onto it, unlike `admin_mfa_enroll`/`admin_mfa_confirm`/`admin_mfa_disable`,
which all already use it.

## 3. Fix / remediation

- `change_password` now resolves the caller via
  `_require_staff_from_token(authorization, admin001_detail=...)` instead
  of a bare `jwt.decode()` — gaining the `is_active` check, JTI-denylist
  check, and `token_version` revocation gate for free, identical to every
  sibling MFA endpoint.
- `_require_staff_from_token` gained an `admin001_detail` parameter (default
  preserves its 3 existing callers' behavior unchanged) so `change_password`
  can still surface its own specific "update ADMIN_PASSWORD in the
  environment" message for the `admin-001` super-admin account, instead of
  the generic MFA-flavored default.
- Added a `totp_code: Optional[str]` field to `ChangePasswordRequest`; when
  `staff.get("mfa_enabled")` is true, a fresh TOTP code is now required and
  verified (`pyotp.TOTP(...).verify(..., valid_window=1)`), mirroring
  `admin_mfa_disable`'s exact posture (password + TOTP, same rate limit,
  no additional per-account TOTP lockout beyond the existing route-level
  `3/minute` limit both endpoints already share).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `change_password`.** Grepped the whole repo
  (backend + admin-dashboard) for callers of `change_password`/
  `ChangePasswordRequest`/the `/change-password` path and `current_password`
  field name — **no admin-dashboard frontend caller exists today.** This
  endpoint is currently backend-only/unwired from any UI, so the added TOTP
  requirement has zero live user-facing impact right now; whichever future
  frontend work wires up a Settings-page password-change form will need to
  collect and send `totp_code` for MFA-enrolled accounts.
- **What could regress:** nothing for a legitimate, non-revoked, active
  session — every one of the new checks (`is_active`, JTI denylist,
  `token_version`) only rejects a token that should already be rejected. No
  pre-existing test exercised this endpoint at all (confirmed: absent from
  every sibling admin-auth test file's own scope docstring), so there was
  no prior test suite to break.

## 5. User-experience effect

**Internal admin only, and currently unreachable from any UI** (see blast
radius above). Once/if a frontend is built for this endpoint: an
MFA-enrolled admin changing their password will need to supply a fresh TOTP
code, matching the existing Settings → Disable MFA flow's requirement.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/auth.py` | `_require_staff_from_token` gained an `admin001_detail` parameter; `change_password` now uses it instead of a bare `jwt.decode()`; added TOTP re-verification when MFA is enrolled | Close the revocation-bypass gap; require the second factor a captured token alone can't provide |
| `backend/tests/test_admin_change_password.py` (new) | 13 tests: missing/malformed auth, admin-001 message, staff-not-found, inactive account, revoked JTI, stale token_version, wrong password, short new password, missing/wrong/correct TOTP, happy path with actual password-hash verification | This endpoint had zero test coverage before this fix — new file covers both the new revocation checks and the pre-existing password-verification logic |

## 7. Before / after

```python
# Before
payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM], audience=JWT_AUD_ADMIN)
user_id = payload.get("user_id")
if not user_id or user_id == "admin-001":
    raise HTTPException(400, "Super admin password cannot be changed here...")
staff = await db.find_one("admin_staff", {"id": user_id})
if not staff:
    raise HTTPException(404, "Staff member not found")
# is_active, JTI denylist, token_version: never checked.

# After
staff = await _require_staff_from_token(
    authorization,
    admin001_detail="Super admin password cannot be changed here. Update ADMIN_PASSWORD in the environment.",
)
# is_active, JTI denylist, and token_version are all enforced inside the helper.
...
if staff.get("mfa_enabled"):
    if not body.totp_code:
        raise HTTPException(422, "TOTP code required to change password on an MFA-enrolled account")
    if not pyotp.TOTP(staff["mfa_secret"]).verify(body.totp_code, valid_window=1):
        raise HTTPException(400, "Invalid TOTP code")
```

## 8. Rollback plan

`git revert`-safe. Pure authorization/verification tightening — no data
written or migrated by this change itself. Reverting restores the bare
`jwt.decode()` path (re-opens the revocation-bypass gap).

## 9. Verification performed

- [x] `pytest tests/test_admin_change_password.py -q` — 13 passed (all new), 0 failed.
- [x] `pytest tests/test_admin_mfa_challenge.py tests/test_admin_mfa_totp_lockout.py tests/test_admin_mfa_enforcement.py tests/test_admin_auth_log_redaction.py tests/test_admin_auth_coverage_gap.py tests/test_admin_login_resets_idle_clock.py tests/test_admin_staff_mfa_reset.py tests/test_admin_change_password.py -q` — 89 passed total, 0 failed (confirms the `admin001_detail` parameterization didn't change behavior for `_require_staff_from_token`'s 3 pre-existing callers).
- [x] `ruff check` + `ruff format --check` — clean.
- [x] Blast-radius grep performed repo-wide (backend + admin-dashboard) — no frontend caller found (see §4).

## What was NOT verified

- Not tested against live Supabase — mocked dependencies only.
- No real end-to-end token-lifecycle test (mint → logout-all → attempt
  change-password with the stale token against a running server) — the new
  test (`test_stale_token_version_rejected`) exercises the same logic path
  at the function level with a mocked DB row, which is this repo's standing
  convention for this test tier, not a live integration test.
- Full backend test suite not run in this pass; deferred to the final
  combined verification pass across all 9 audit fixes (this is fix 8 of 9;
  W6 remains).
