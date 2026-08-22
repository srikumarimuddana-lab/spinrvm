# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend / admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch: `fix/legacy-consent-notice-admin-allowlist`) |
| Related issue or gap ID | found during "merged but not applied/orphaned" audit follow-up to G2 (`ACTION_ITEMS.md`) |

## 1. Issue / gap identified

`legacy_consent_notice_enabled` (migration `356_legacy_consent_notice_enabled_flag.sql`) is a live DB column,
fully wired end-to-end in `routes/legacy_consent.py`, `routes/auth.py`, and both rider-app/driver-app clients
(`otp.tsx`, `profile-setup.tsx`, `index.tsx`) — but there was no supported way to turn it on. It was missing
from `SettingsUpdateRequest` in `backend/routes/admin/settings.py`, so a `PUT /api/admin/settings` call setting
it was silently dropped by Pydantic's `exclude_none` dump (the field simply didn't exist on the model), leaving
a direct DB write as the only way to flip the flag.

## 2. Root cause

When migration 356 added the column and the flag's read paths were wired into both apps and `routes/auth.py`,
the corresponding admin-write path (`SettingsUpdateRequest` in `routes/admin/settings.py`) was never updated to
include the new field — the same class of gap `rideless_sos_enabled` had before it was added at the same
allow-list (see the adjacent code comment referencing `ACTION_ITEMS.md` E5 for `scheduled_dispatch_enabled` and
friends, a prior instance of the identical omission).

## 3. Fix / remediation

Added `legacy_consent_notice_enabled: Optional[bool] = None` to `SettingsUpdateRequest`, mirroring the existing
`rideless_sos_enabled` field immediately above it (same comment style, same "not a credential, no
super-admin gate" annotation). No other code path needed changes — `update_fields` is built generically from
`settings.model_dump(exclude_none=True)`, so any field present on the Pydantic model is automatically forwarded
to `insert_one`/`update_one` on the `settings` table.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** This is a pure additive field on a request model already handling 20+ similar
  boolean flags via the same generic `model_dump(exclude_none=True)` → `update_fields` → `insert_one`/`update_one`
  path. No existing field's behavior changes.
- Grepped `routes/admin/settings.py` for every other consumer of `SettingsUpdateRequest`/`update_fields` — only
  the single `PUT /settings` handler uses it; no other route or background loop reads this model.
- The flag's *effect* when flipped on (both apps show a one-time legacy-consent notice) is unchanged by this PR —
  this PR only adds the missing admin-write path. The flag still defaults `false` and nothing about its runtime
  behavior changes until an admin explicitly sets it, same as `rideless_sos_enabled`'s existing gate.
- No interaction with ride state machine, background loops, or money/wallet deltas.

## 5. User-experience effect

- **Nobody sees a difference from this change alone.** The flag still defaults to `false` (unchanged). This PR
  only makes the flag reachable through the normal admin PATCH flow instead of a direct DB write — no rider,
  driver, or admin-facing behavior changes until a super-admin/admin explicitly flips the flag via the
  now-functional API path.
- Flipping the flag on (a separate, later action, not part of this PR) triggers a real, immediate, user-facing
  change per the existing code comment in `schemas.py::AppSettings.legacy_consent_notice_enabled` — both apps
  are already live-wired to show the legacy-consent notice as soon as it's true. That comment already documents
  this is not a no-op; this PR does not change that behavior or its readiness, only the mechanism to enable it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | Added `legacy_consent_notice_enabled: Optional[bool] = None` to `SettingsUpdateRequest` | Close the missing admin-write path for an already-live, fully-wired DB flag |
| `backend/tests/test_admin_business_logic.py` | Added a regression test asserting the field is actually forwarded to `insert_one("settings", ...)`, not just that the endpoint returns a non-error status | Prevent this exact class of bug (a field silently dropped from the allow-list) from recurring undetected |

## 7. Before / after

```python
# Before (routes/admin/settings.py, SettingsUpdateRequest)
rideless_sos_enabled: Optional[bool] = None
# Kill switches (ACTION_ITEMS.md E5). scheduled_dispatch_enabled already
# ...
scheduled_dispatch_enabled: Optional[bool] = None
```

```python
# After
rideless_sos_enabled: Optional[bool] = None
# Legacy/re-consent notice rollout gate (ACTION_ITEMS.md, 2026-08-19
# legacy-migration audit) -- dark-launched, both apps. Not a credential,
# no masking/super-admin gate needed. See schemas.py::AppSettings.
# legacy_consent_notice_enabled for what flipping this on actually does
# (both apps are live-wired to it; it is not a no-op).
legacy_consent_notice_enabled: Optional[bool] = None
# Kill switches (ACTION_ITEMS.md E5). scheduled_dispatch_enabled already
# ...
scheduled_dispatch_enabled: Optional[bool] = None
```

## 8. Rollback plan

- `git revert` this commit is sufficient — the field addition has no live-data side effect on its own (nobody
  can flip the flag through this path until it merges and deploys, so there's nothing in production to unwind).
- If an admin *does* flip the flag on after this ships and it needs to be reverted, that's a config revert, not
  a code rollback: `PUT /api/admin/settings {"legacy_consent_notice_enabled": false}` (or a direct
  `UPDATE settings SET legacy_consent_notice_enabled = false WHERE id = 'app_settings'`), independent of this PR.

## 9. Verification performed

- [x] Automated tests run — unit: `pytest backend/tests/test_admin_business_logic.py` (47 passed, includes new
      regression test), plus the full existing admin-settings suite (`test_admin_settings_payment_credential_gate.py`,
      `test_admin_settings_heatmap_endpoint.py`, `test_admin_settings_heatmap_config.py`,
      `test_admin_settings_company_logo.py`, `test_admin_settings_company_app_name.py`,
      `test_admin_settings_lms_gate.py`) and `test_sos_rideless.py` — 78 passed total, 0 failed.
- [ ] Manual repro steps followed in staging — **not performed**, no staging environment access in this session.
- [x] Blast-radius grep performed — searched `routes/admin/settings.py` for all consumers of `SettingsUpdateRequest`
      and `update_fields`; confirmed single call site.
- [x] Reviewed against relevant `CLAUDE.md` convention — this is a settings/admin change, not state-machine/money/
      RLS; no Decimal, ride-state, or RLS policy touched.
- [x] Feature-flagged / non-trivial — N/A, this PR does not flip the flag on, only restores the admin write path;
      the flag itself remains off by default, unchanged.
- Ran `ruff check` on both modified files — clean. Did **not** run a Python production build equivalent (backend
  has no separate build step distinct from `pytest`/`ruff`); `npm run build` is not applicable to this backend-only
  change (no `admin-dashboard`/`rider-app`/`driver-app` files were modified).

## 10. What was NOT verified

- No staging or live-Supabase exercise of the actual `PUT /api/admin/settings` call — verification was unit-test-only
  (mocked `db_supabase.get_rows`/`insert_one`/`update_one`), consistent with this repo's standing convention that
  there is no integration tier for admin routes in this session's environment.
- Did not verify the admin-dashboard frontend settings page actually renders a toggle UI control for this new field
  — this PR only unblocks the backend API path; if the admin dashboard's settings form needs its own UI addition to
  expose the toggle, that is a separate, not-yet-scoped follow-up.
- No visual/screenshot check — this is a backend-only change with no rendered UI to check, and this repo has no
  automated visual-regression tooling regardless (standing gap, ACTION_ITEMS.md).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (git revert; no live-data side effect to unwind from this PR alone)
- [x] Blast radius is stated, not assumed — isolated, single call site, single request model
- [x] No silent behavior change to an already-shipped flow — flag still defaults false; nothing changes for any
      user until an admin takes a separate, later, explicit action
