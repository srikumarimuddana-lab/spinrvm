# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin, auth |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #6 |

## 1. Issue / gap identified

Demoting an admin (removing `super_admin`, dropping a module grant, or
narrowing their role) via `PUT /admin/staff/{id}` took effect in the DB
immediately but not in that admin's live session: their already-issued
access token kept working with the OLD role/modules for up to its full
1-hour TTL, and the matching refresh token would keep silently minting new
access tokens carrying the same stale claims until it was separately
revoked. A demoted `super_admin` could keep exercising super-admin-only
endpoints for up to an hour after being demoted.

## 2. Root cause

`dependencies.get_admin_user` treats admin JWTs as fully trusted — `role`
and `modules` are read straight from the token payload
(`payload["role"]`, `payload.get("modules", [])`), never re-fetched from
`admin_staff` per request (see CLAUDE.md's JWT trust model note: this is
intentional for admins, unlike riders/drivers whose role is always
re-read). The token_version gate exists precisely to force re-auth when a
staff row changes in a way the token can't self-correct for — and
`update_staff` already used it correctly for `is_active=False`
(deactivation) — but the same bump was never wired to role or modules
changes, even though both are equally trusted, equally stale JWT claims.

## 3. Fix / remediation

- In `update_staff`, after computing the `role`/`modules` updates, compare
  them against the stored `admin_staff` row (`s`). If either actually
  differs, bump `token_version` and call `revoke_all_for_user(staff_id)` —
  identical mechanism to the existing deactivation path, which already:
  1. invalidates every existing access token immediately via
     `_token_version_mismatch` in `dependencies.get_admin_user`, and
  2. revokes every refresh token, so no new access token can be silently
     minted with the stale claims either.
- Guarded with `"token_version" not in updates` so a request that sets
  both `is_active=False` and a new `role` in the same call only bumps
  once (both paths already agree on the mechanism, so this is purely
  about not double-incrementing / double-revoking).
- Deliberately compares against the *stored* value, not against whether
  the request field was merely present — re-submitting the identical
  role/modules (e.g. the admin-dashboard form re-posting the full object
  unchanged) must not force every admin back to a login screen on a
  true no-op.

## 4. Risk & impact on existing functionality

- **Blast radius: `update_staff` only.** Grepped for every other caller
  of `update_staff` (route function) — none; it's only reachable via
  `PUT /admin/staff/{id}`. Grepped every other `token_version` bump site
  (`routes/admin/auth.py`'s `logout-all`, MFA reset) — unrelated code
  paths, unmodified.
- The new branch reuses `revoke_all_for_user`, already exercised and
  tested via the deactivation path — no new revocation code, only a new
  call site.
- Every existing `update_staff` test that changes `role` or `modules`
  now exercises the new branch. Updated
  `test_promotion_correct_password_succeeds`,
  `test_promotion_skips_password_check_for_admin_001`,
  `test_demote_super_admin_allowed_when_others_remain`, and
  `test_role_preset_snaps_modules` to mock `revoke_all_for_user` (same
  pattern the pre-existing deactivation test already used) so they don't
  hit the real refresh-token table through the mocked Supabase client.
  `test_custom_modules_filtered` needed no change — its filtered modules
  equal the stored value, so the new branch correctly does not fire.
- Added 4 new tests: role-change-only bump, modules-change-only bump,
  identical-role-and-modules no-op (no bump), and
  deactivation-plus-role-change-in-one-request bumps exactly once (not
  twice).

## 5. User-experience effect

**Internal admin-facing only.** A demoted or role-narrowed admin's active
session (all devices) is now terminated at the moment of the change
instead of up to an hour later — they'll see their next request 401 and
be sent back to login, then get a fresh token reflecting the new
role/modules. A *promoted* admin sees the same forced re-login rather than
waiting up to an hour for elevated access to appear, which is a net UX
improvement for that direction too. An admin whose role/modules are
unchanged (e.g. only their name is edited) sees no change — no forced
logout for a true no-op.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/staff.py` | `update_staff` now bumps `token_version` + calls `revoke_all_for_user` when `role` or `modules` actually change (guarded against double-bumping alongside the existing deactivation path) | Trusted JWT claims must not outlive the DB change that invalidated them |
| `backend/tests/test_admin_staff_coverage.py` | Added `revoke_all_for_user` mocks to 4 existing tests that now hit the new branch; added 4 new dedicated tests | Cover the new behavior and its no-op/no-double-bump edges |

## 7. Before / after

```python
# Before
if req.role is not None:
    updates["role"] = req.role
    if req.role in ROLE_PRESETS:
        updates["modules"] = ROLE_PRESETS[req.role]
if req.modules is not None:
    updates["modules"] = [m for m in req.modules if m in AVAILABLE_MODULES]

if updates:
    ...  # DB write only — no session invalidation for role/module changes
```

```python
# After
if req.role is not None:
    updates["role"] = req.role
    if req.role in ROLE_PRESETS:
        updates["modules"] = ROLE_PRESETS[req.role]
if req.modules is not None:
    updates["modules"] = [m for m in req.modules if m in AVAILABLE_MODULES]

_role_changed = req.role is not None and req.role != s.get("role")
_modules_changed = "modules" in updates and updates["modules"] != (s.get("modules") or [])
if (_role_changed or _modules_changed) and "token_version" not in updates:
    updates["token_version"] = int(s.get("token_version") or 0) + 1
    await revoke_all_for_user(staff_id)

if updates:
    ...
```

## 8. Rollback plan

Plain code change, no migration, no data written beyond the pre-existing
`admin_staff.token_version` column (already in use for deactivation and
`/auth/logout-all`). `git revert` fully restores the prior behavior. No
feature flag — this closes a session-staleness gap using a mechanism
that's already live and already trusted for the deactivation case; there
is no meaningful dark-ship version of "a demoted admin's session should
end when they're demoted."

## 9. Verification performed

- [x] Automated tests: `test_admin_staff_coverage.py` (now 8 tests in
      `TestUpdateStaff`, up from 4 relevant to role/modules — 4 updated,
      4 new), `test_admin_security.py`, `test_admin_routes_auth.py`,
      `test_admin_staff_mfa_reset.py` — all passing via the session's
      `/tmp/spinr_venv` venv, run from repo root.
- [x] `ruff check` on both touched files — clean.
- [x] Blast-radius grep performed (see §4): only call site of
      `update_staff`, every `token_version` bump site, every existing
      test that changes role/modules.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: a `super_admin` demotes another `super_admin` to
      `operations`. Before this fix: the demoted admin's already-issued
      access token keeps working with `role=super_admin` in its JWT
      claims for up to 1hr; any super-admin-only route (e.g.
      `require_role("super_admin")`) stays reachable to them until the
      token naturally expires. After this fix: `token_version` bumps and
      every refresh token is revoked in the same request; the demoted
      admin's very next request fails `_token_version_mismatch` and
      returns 401, forcing a fresh login that mints a token with the
      correct (`operations`) claims.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — single call site, all
      dependent tests grepped and updated/added
- [x] No silent behavior change to a working flow for the no-op case
      (identical role/modules resubmission) — verified by a dedicated
      test; the behavior change is intentional and scoped exactly to
      actual role/modules mutations, matching the existing deactivation
      precedent

## What was NOT verified

Not tested against a live/staging Supabase or a real admin session —
only mocked DB writes and the pure-Python comparison logic. Did not
extend this to `create_staff` (a brand-new staff row has no prior
session to invalidate, so there's nothing to bump there) or to password
changes (self-service password change already goes through a separate
re-auth flow, out of scope for this finding). Did not add a
frontend-visible "your session will end" warning in the admin-dashboard
staff-edit UI before an admin submits a role/module change that will log
the target user out — the backend now enforces this correctly regardless,
but a proactive UI warning is a reasonable follow-up, not implemented
here.
