# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth, rides |
| PR / commit link | branch `claude/n14-auth-me-email-verified` |
| Related issue or gap ID | ACTION_ITEMS.md N14 residual gap (surfaced by PR #3607) |

## 1. Issue / gap identified

`GET /auth/me` (and every other endpoint that returns a `UserProfile`) never included `email_verified`/`email_verified_at` in its response, even after the rider-app verify-email flow (N14, PR #3607) started flipping those two columns server-side. The Account-screen badge worked around this client-side by merging the confirm response's own `email_verified: true` into local state — but that merge doesn't survive a full app restart, so a verified rider's badge silently reverted to "not verified" on the next `/auth/me` refetch. Found and documented as a residual gap by the PR #3607 session; not fixed there since it was a backend-file change out of that session's rider-app-only scope.

## 2. Root cause

`schemas.py`'s `UserProfile` Pydantic model never declared `email_verified`/`email_verified_at` as fields. Every call site that builds a `UserProfile` does `UserProfile(**user)` / `UserProfile(**current_user)`, unpacking the full DB row dict — which already contains both columns (they're real `users` table columns, written by the verify-email confirm endpoint) — directly into the model. Pydantic silently drops any dict key that isn't a declared field by default, so the two columns were present in the DB row the whole time but never reached any API response.

## 3. Fix / remediation

Added `email_verified: bool = False` and `email_verified_at: Optional[datetime] = None` to `UserProfile` in `backend/schemas.py`. No other file needed a change — every `UserProfile(**row)` call site (11 of them, across `routes/auth.py` and `routes/users.py`) already spreads the full DB row, so the new fields now flow through automatically wherever a `UserProfile` is returned, not just `/auth/me`.

## 4. Risk & impact on existing functionality

Grepped every `UserProfile(` construction site in `backend/` (11 total: `routes/users.py:90,173,575,689,733`; `routes/auth.py:634,1056,1176,1275,1452,1546`) — all use the `UserProfile(**row)` spread pattern, none construct it with an explicit, hand-picked field list that could conflict with or shadow the two new fields. Both new fields have safe defaults (`False`/`None`), so a legacy row that predates the `email_verified` column (or any mocked/test dict that doesn't include it) still constructs a valid `UserProfile` without erroring — verified directly with a dedicated test covering both the present-and-true and entirely-missing-key cases.

Blast radius: every consumer of `UserProfile` now receives two additional, previously-invisible fields. This is additive to the JSON response shape (new keys appearing), not a removal or rename — no existing client code that only reads specific known fields is affected. Rider-app's Account screen (added in PR #3607) can now read this field directly from a normal `/auth/me` refetch instead of relying solely on its local-state merge workaround (that workaround was not removed or required to be removed by this change — it remains as a reasonable immediate-UI-update mechanism for the instant right after a successful confirm, before the next natural refetch).

## 5. User-experience effect

Rider-facing, and it is a genuine live-behavior fix, not a new feature: a rider who verifies their email and then fully restarts the app will now correctly still see their email marked verified, instead of the badge silently reverting to "not verified" (the bug PR #3607 found and documented). Not visible mid-session to a rider already using the app in a way that changes anything else — this only affects what `/auth/me` returns, which the rider-app already reads on every app launch/focus.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `email_verified`/`email_verified_at` fields to `UserProfile` | Close the residual gap PR #3607 documented — the columns existed, the API response never surfaced them |
| `backend/tests/test_auth_remaining_endpoints.py` | New test `test_email_verified_fields_round_trip_through_response` | Pins both the verified-True and legacy-missing-key (defaults to False/None) cases |
| `docs/change-log/2026-08-11-n14-auth-me-email-verified-field.md` | New Change Impact Log | Required per `CLAUDE.md` — touches the auth/profile response surface |
| `ACTION_ITEMS.md` | N14 entry's residual-gap note updated | Tracking |

## 7. Before / after

```python
# Before (schemas.py)
class UserProfile(BaseModel):
    id: str
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    ...
```

```python
# After (schemas.py)
class UserProfile(BaseModel):
    id: str
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None
    gender: Optional[str] = None
    ...
```

## 8. Rollback plan

`git revert` — pure additive schema field, no migration (the underlying `users` columns already existed), no data mutation, no feature flag applicable. Reverting simply stops the two fields from appearing in API responses again; the rider-app's local-state merge workaround (already shipped in PR #3607, unmodified by this change) continues to cover the immediate post-confirm case regardless.

## 9. Verification performed

- [x] Automated tests run — real venv, real `pytest`: `test_auth_remaining_endpoints.py` + `test_auth.py` + `test_rider_email_verification.py` + `test_p1_auth_hardening.py` + `test_dependencies_auth_gaps.py` → **102 passed, 0 failed**. Broader sweep (`-k "schemas or user_profile or test_users or test_auth or rider_email"`) → **168 passed, 1 skipped, 0 failed**. `routes/users.py`/admin-users-adjacent sweep (`test_routes_users_coverage.py`, `test_p3_admin_jwt_modules.py`, `test_admin_users_management.py`, `test_admin_users_search.py`) → **123 passed, 0 failed**.
- [x] Blast-radius grep performed — see §4, all 11 `UserProfile(` construction sites checked directly.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — PIPEDA logging list checked: `email_verified` (bool) and `email_verified_at` (timestamp) are not on the "never log" list (raw GPS, phone numbers, names, emails, card numbers, government IDs) and are not newly logged anywhere by this change — they're an API response field, not a log line.
- [ ] Manual repro steps followed in staging — not performed, no staging environment available in this session.

## 10. What was NOT verified

- No live Supabase read exercised — verification is unit-level with a mocked `current_user` dict per this repo's established testing convention.
- The rider-app side of this fix (does the Account screen's badge actually now survive a real app restart against a live backend) was not re-verified end-to-end in this session — that would require the rider-app's own test/build tooling and a live backend, out of scope for this backend-only follow-up. The rider-app code itself needs no change (it already reads whatever `/auth/me` returns), which is the basis for calling this fix complete without a corresponding rider-app PR.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (documented in §5 — this is a real behavior fix, not silent)
