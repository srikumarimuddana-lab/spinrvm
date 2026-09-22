# Change Impact & Risk Log — Explicit refresh-token revocation

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | `fix/ride-reliability-auth` (local commits; no PR created) |
| Related issue or gap ID | `Spinr_Combined_Review_2026-09-22.md`, authentication replay finding |

## 1. Issue / gap identified

A refresh token revoked by an explicit sign-out or account-wide revocation can later be presented by a stale client and trigger a cascade that revokes sessions created after the sign-out. `/auth/logout-all` also had no dedicated audit event describing the account-wide action.

## 2. Root cause

`refresh_tokens` stored `revoked_at` and `replaced_by`, but not why an unrotated token had been revoked. The replay detector therefore could not distinguish a dead token from a sign-out from a rotated predecessor replay, and guessed from its age and audience. That guess both left rider/driver sign-out replays destructive and allowed some admin sign-out replays to bypass the cascade without proof of their revocation cause.

## 3. Fix / remediation

- Added nullable `refresh_tokens.revocation_reason`. Explicit logout, logout-all, admin revocation, and account deletion now stamp a reason. A revoked token is excluded from the account-wide reuse cascade only when its reason is allowlisted and `replaced_by` is empty. A rotated predecessor still follows the existing bounded rotation-race rule; after that window, reuse cascades. Missing/unknown reasons, including legacy rows, retain the conservative theft cascade.
- Added audit events for rider/driver and admin logout-all with the number of revoked refresh rows and token-version watermark. No raw token values are recorded.
- If the new column is absent from the database or PostgREST schema cache, single and bulk revoke operations retry with only `revoked_at`; only `PGRST204`/`42703` errors that name `revocation_reason` trigger this fallback. This preserves revocation while leaving its reason null, so replay remains conservative.
- Updated Driver Profile confirmation copy to say sign-out covers every device using the Spinr account, including both Rider and Driver apps.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-surface, auth-only.** The changed shared table is read by `backend/utils/refresh_tokens.py`, the refresh endpoint, logout routes, admin staff/session routes, and account deletion. Explicit user/admin revocation call sites now pass a reason; the reuse cascade continues to call the ID-returning bulk helper without a sign-out reason and records its victim rows in the existing reuse audit.
- Rotated-token retries inside the existing 10-minute window remain non-cascading; older rotated-token replays and every legacy unclassified revocation retain the theft cascade. The new reason does not make a revoked token usable.
- Account deletion, staff disable/role/module/MFA changes, and admin force logout all revoke refresh credentials with explicit reasons. The audit additions do not change endpoint response shapes.
- Migration `441_refresh_token_revocation_reason.sql` is additive and has no backfill. It has **not** been applied to a live database. The migration must be manually applied and verified before deploying the new backend; the deploy workflow does not apply migrations automatically. The bounded fallback protects revocation if the column is missing but cannot provide the improved classification until the migration is present.
- Existing rows remain `NULL` and continue to use the theft cascade. No safe exact backfill exists because historical explicit sign-out rows did not record their refresh-token IDs/reasons. Alternatives considered: suppress all unrotated replay cascades (unsafe), or infer revocation cause from age/audience/audit chronology (ambiguous); both were rejected.

## 5. User-experience effect

The sign-out-all confirmation now explains that it signs the account out across Rider and Driver apps. It remains a confirmation dialog, so there is no mid-session copy update. The backend still takes an idle driver offline as before. No push or email was added.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/refresh_tokens.py` | Persist explicit reasons; classify only known sign-outs as non-cascading; handle the precise missing-column rollout error | Stop stale sign-out credentials from invalidating later sessions without weakening rotated-token reuse detection |
| `backend/migrations/441_refresh_token_revocation_reason.sql` | Add nullable reason column | Persist why non-rotation revocation occurred |
| `backend/routes/auth.py`, `backend/routes/admin/auth.py` | Mark single/account-wide logout reasons and write logout-all audit events | Capture actual revocation cause and a durable audit of account-wide sign-out |
| `backend/routes/admin/staff.py`, `backend/routes/users.py` | Mark admin credential revocations and account-deletion revocations | Preserve explicit reason at every bulk-revocation caller |
| `backend/tests/test_refresh_token_reuse_detection.py` | Cover explicit logout replay, rotated predecessor cascade, schema fallback and unrelated-error behavior | Pin classification and rollout fallback |
| `backend/tests/test_logout_all.py`, `backend/tests/test_auth_remaining_endpoints.py`, `backend/tests/test_admin_security.py` | Assert endpoint reason arguments and logout-all audit details | Verify the actual route call contract |
| `backend/tests/test_admin_staff_coverage.py`, `backend/tests/test_admin_staff_mfa_reset.py`, `backend/tests/test_routes_users_coverage.py` | Assert admin and account-deletion reasons | Verify remaining bulk-revocation callers |
| `driver-app/app/driver/(tabs)/profile.tsx` | Clarify cross-app sign-out coverage | Set accurate expectations for a shared account |
| `driver-app/__tests__/app/driverProfileScreen.test.tsx` | Assert cross-app confirmation copy | Prevent the account scope from becoming ambiguous again |

## 7. Before / after

```text
# Before
revoked_at is set, replaced_by is empty, and the row is presented again:
the detector guesses from age/audience whether to cascade.
```

```text
# After
revocation_reason is an allowlisted explicit sign-out reason and
replaced_by is empty: return the same generic 401, record the replay, and
leave newer sessions untouched. Unknown reason or rotated predecessor
outside its bounded grace: preserve the reuse cascade.
```

## 8. Rollback plan

Apply migration 441 manually before deploying the backend; confirm the column exists and a read/write through the service-role path succeeds. If the backend release causes a problem, redeploy the prior backend/app versions. Leave the additive nullable column in place during rollback; the old code ignores it, and dropping it would discard forensic reason data. Do not remove the fallback until every deployed backend has the migrated schema.

## 9. Verification performed

- [x] Utility and refresh regression suites: `test_refresh_token_reuse_detection.py`, `test_refresh_tokens_lifecycle.py`, `test_p1_token_refresh.py` — 75 passed, including the single/bulk schema-fallback tests.
- [x] Admin staff suites: `test_admin_staff_coverage.py`, `test_admin_staff_mfa_reset.py` — 51 passed.
- [x] Focused logout, admin logout, user deletion, and missing-column fallback tests passed individually.
- [x] `git diff --check` run before commits.
- [x] Grepped all route call sites for `revoke_refresh_token` and `revoke_all_for_user`; each explicit call now names its revocation reason. The reuse-cascade path remains unclassified.
- [x] Integration follow-up with locked app dependencies: driver auth-store initialization/races, background auth/location, and Profile suites passed 163 tests. Both Android JavaScript/Hermes exports passed with repository postinstall patches applied. This supersedes the earlier missing-`jest-expo` limitation; no signed native app or device test was performed.
- [ ] The full logout-all backend test file was stopped by automatic approval review after an unmocked case attempted a cloud metadata request; the targeted root/admin logout-all cases passed. No staging verification was performed.
- [ ] Live migration not run. Before backend rollout, manually apply migration 441 and verify the service-role write path.

## 10. Sign-off

- [x] Rollback plan is concrete: redeploy prior code and retain the additive column.
- [x] Blast radius is stated.
- [x] No raw refresh/access token values are included in the new audit event.
- [ ] Schema-first manual rollout and staging checks remain deployment gates.
