# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | local worktree commits (not pushed) — see commit SHAs in session report |
| Related issue or gap ID | ranked blocker #12, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` |

## 1. Issue / gap identified

`backend/routes/auth.py` wrote zero `consent_version` on rider/driver signup. Corporate
self-serve signup (`routes/corporate_signup.py`, migration 224) was the only signup path
in the codebase that stamped any consent-version field at all, contradicting CLAUDE.md's
PIPEDA convention: "consent language version is stored on signup. Material changes require
re-consent."

## 2. Root cause

The `users` table (the table every rider/driver signup writes to) never had a
`consent_version` column, and none of the three `users`-row-creating code paths in
`routes/auth.py` (`verify_otp`'s new-user branch, `firebase_auth_login`'s new-user branch,
`send_company_email_otp`'s new-user branch) ever wrote one. This is distinct from corporate
signup: `corporate_accounts.terms_accepted_version` (migration 224) is populated from a
**client-supplied** string (`body.terms_version`) that the admin-dashboard's company-signup
page sources from its own hardcoded constant, `BUSINESS_TERMS_VERSION = "biz-tos-2026-01-draft"`
(`admin-dashboard/src/app/company-signup/page.tsx`). Investigated whether a shared backend
constant already existed to reuse 1:1 — it does not: `marketing.py`'s `CONSENT_VERSION = "1"`
is a separate, CASL-specific marketing-opt-in version; `driver_crc_consent.py`/migration 319's
`consent_version` is a separate, background-check-specific consent; neither is the general
signup ToS/Privacy consent CLAUDE.md describes. Also confirmed both `rider-app` and
`driver-app` currently render the legacy single-blob `/settings/legal` text
(`docs/legal/terms-of-service.md`'s own header note), not the versioned per-audience
`legal_documents` rows `routes/legal_documents.py` serves — so there is no live,
already-shipped per-audience version number a mobile client could supply today either.

## 3. Fix / remediation

- Added a new backend-owned constant `CONSENT_VERSION = "consumer-tos-2026-01-draft"` in
  `routes/auth.py` (module-level, same pattern as `marketing.py`'s own `CONSENT_VERSION`
  constant for CASL consent — the closest existing precedent in this codebase for a
  backend-defined, non-client-supplied consent version string).
- Stamped `consent_version` and `consent_accepted_at` into the `new_user` insert dict at
  the two genuine rider/driver signup sites in `routes/auth.py`:
  - `verify_otp`'s "Creating new user" branch (phone-OTP signup — the primary rider/driver
    signup path both apps use)
  - `firebase_auth_login`'s new-user branch (Firebase signup — audience-bound to the driver
    app only, per the existing `FIREBASE_DRIVER_APP_ID` audience check)
  Both writes happen in the same dict passed to the single `db_supabase.create_user()` call
  already used for the initial insert — no separate follow-up write, so there is no race
  window where a user row could exist without its consent stamp.
- Added migration `334_users_consent_version.sql`: additive, nullable
  `ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_version TEXT, ADD COLUMN IF NOT EXISTS
  consent_accepted_at TIMESTAMPTZ` — mirrors the exact column-pair shape/naming style of
  migration 224's `terms_accepted_version`/`terms_accepted_at` on `corporate_accounts`,
  applied here to the table rider/driver signup actually writes.
- **Migration NOT applied in this sandbox** — no `DATABASE_URL` available. It must be run
  via the normal `python -m backend.scripts.run_migrations` pipeline before or with this
  code's deploy; deploying the code without the migration would make `create_user()` fail
  on every new rider/driver signup (unknown column), so the migration is a hard
  precondition, not an optional follow-up.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, no other callers found for the two touched insert sites.**
  Grepped every `create_user` caller in the backend: `routes/auth.py` (the two sites
  touched), `services/guest_user_service.py` (guest-booking user creation — **not touched**,
  out of scope since it's not the rider/driver phone/Firebase signup path this blocker names;
  flagged below as a known remaining gap, not silently left unaddressed), and
  `db_supabase.py`/`repositories/auth_repo.py` (the `create_user` definition itself,
  unchanged — it passes the dict through to `insert_one` verbatim, so no changes needed
  there).
- Grepped every reader of `consent_version` across the repo before adding this column: the
  string is used by three *other*, unrelated consent tables (`marketing_consents`.consent_version
  for CASL, `driver_crc_consents`/`driver_crc_consent_events`.consent_version for background-check
  consent, both from earlier migrations) — none of them read from `users`, so there is no
  existing code path that could misinterpret the new `users.consent_version`/`consent_accepted_at`
  columns. No code in the repo reads `users.consent_version` yet (this fix is additive-only);
  the columns exist purely as a compliance record for now, closing the same "zero write" gap the
  audit flagged for the *write* side. A future re-consent feature would need its own read path
  — out of scope here.
- **Existing rider/driver rows will show `consent_version IS NULL` after this migration** — this
  is correct and intentional (the honest state: they signed up before consent-version tracking
  existed; no fabricated backfill). Nothing in the codebase currently reads this field to gate
  behavior (see above), so a NULL value on old rows cannot break any existing flow — there is
  no "material change → re-consent" enforcement mechanism reading this column today, so this fix
  only satisfies the "stored on signup" half of the CLAUDE.md sentence; the "material changes
  require re-consent" half remains unimplemented and is not claimed as done by this fix.
- `UserProfile(**new_user)` (the Pydantic response model used to build both endpoints'
  responses) verified to silently drop unknown dict keys (Pydantic default, non-`extra="forbid"`
  model) — confirmed by reading `schemas.py`'s `UserProfile` definition and its existing
  comment about exactly this behavior for another field (`email_verified`). Adding
  `consent_version`/`consent_accepted_at` to the `new_user` dict therefore cannot change or
  break either endpoint's response shape.
- Third `users`-row-creating path in `routes/auth.py`, `send_company_email_otp`'s new-user
  branch (portal work-email OTP login creating a `role: "rider"` row for a corporate-portal
  user) was **found but deliberately left untouched** — it is not a rider/driver mobile-app
  signup, and touching it would have widened this fix's scope beyond the ranked blocker as
  written. It remains a same-table, same-gap write site; noting it explicitly here rather than
  leaving it as a silent gap.

## 5. User-experience effect

None. Backend-only, additive-column change. No UI, no API response shape change (fields are
silently dropped from the response model, per §4), no new client-facing behavior, no copy or
notification change. Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Added `CONSENT_VERSION` constant; added `consent_version`/`consent_accepted_at` to the `new_user` dict in `verify_otp` and `firebase_auth_login`'s new-user branches | Close ranked blocker #12 — stamp consent version atomically with signup insert |
| `backend/migrations/334_users_consent_version.sql` | New additive migration: `ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_version TEXT, ADD COLUMN IF NOT EXISTS consent_accepted_at TIMESTAMPTZ` | `users` table had no column to write the stamp into |
| `backend/tests/test_verify_otp_login_flow.py` | Extended `test_new_user_created_and_logged_in` to assert `consent_version`/`consent_accepted_at` on the `create_user` call payload | Regression coverage for the new write |
| `backend/tests/test_auth_remaining_endpoints.py` | Extended `TestFirebaseAuthLoginHappyPaths::test_new_user_created_and_logged_in` the same way | Regression coverage for the new write (Firebase path) |

## 7. Before / after

```python
# Before (routes/auth.py, verify_otp's "Creating new user" branch)
new_user = {
    "id": user_id,
    "phone": phone,
    "role": "rider",
    "is_rider": True,
    "is_driver": False,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "profile_complete": False,
    "current_session_id": session_id,
    "token_version": 0,
}
```

```python
# After
now_iso = datetime.now(timezone.utc).isoformat()
new_user = {
    "id": user_id,
    "phone": phone,
    "role": "rider",
    "is_rider": True,
    "is_driver": False,
    "created_at": now_iso,
    "profile_complete": False,
    "current_session_id": session_id,
    "token_version": 0,
    "consent_version": CONSENT_VERSION,
    "consent_accepted_at": now_iso,
}
```

(The `firebase_auth_login` new-user branch received the identical two-key addition.)

## 8. Rollback plan

- **Code**: additive-only diff (new constant, two new dict keys, no removed/changed
  behavior) — a plain `git revert` of the code commit is sufficient and safe; it does not
  touch any live data, Stripe charge, wallet delta, or ride state.
- **Migration**: additive/nullable, so the primary rollback is simply **not applying it** if
  caught before deploy. If already applied to production, the safe rollback is a follow-up
  migration:
  ```sql
  ALTER TABLE users
      DROP COLUMN IF EXISTS consent_version,
      DROP COLUMN IF EXISTS consent_accepted_at;
  ```
  This only discards the (so far unread) consent-version audit trail collected since the
  migration shipped — no other column, row, or downstream state depends on these two
  columns, so dropping them is non-destructive to anything else. This is documented as a
  last-resort option, not a first choice — per CLAUDE.md's "additive over destructive" gate,
  prefer leaving the columns in place and simply reverting the code that populates them if a
  rollback is ever needed.
- **Sequencing note**: the migration must be applied at or before the code deploy — deploying
  the `routes/auth.py` change without migration 334 applied would make `create_user()` fail
  on every new rider/driver signup (unknown column error), i.e. this is NOT a
  ship-dark-then-flip-a-flag change; it is a coupled schema+code pair like any other additive
  column write. No feature flag was added because there is no user-visible behavior to gate —
  the only failure mode is "migration not yet applied," which is a deploy-ordering concern the
  standard migration pipeline already handles (CLAUDE.md's migration commands section).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_verify_otp_login_flow.py
  backend/tests/test_auth_remaining_endpoints.py backend/tests/test_corporate_signup.py -q`
  via `/tmp/spinr-venv/bin/pytest` — **53 passed**, 0 failed (the run's only non-pass signal
  was the pre-existing whole-repo 60% coverage-floor gate, which fails on any narrow test
  subset by design and is unrelated to this change — the full suite's aggregate coverage is
  what that gate actually gates in CI, not a 3-file subset run locally).
- [x] `ruff check` run on all three modified Python files (`routes/auth.py`,
  `tests/test_verify_otp_login_flow.py`, `tests/test_auth_remaining_endpoints.py`) — zero
  new findings. (One pre-existing, unrelated `F841` in
  `test_auth_remaining_endpoints.py:677`, in a different test class this change did not
  touch, was left as-is — out of scope.)
- [x] Migration filename/number collision check: re-ran `ls backend/migrations | sort -V |
  tail` immediately before committing — `334_users_consent_version.sql` is the first free
  number and collides with nothing.
- [x] Blast-radius grep performed: every `consent_version` reference in the repo (backend
  services, migrations, admin-dashboard, tests), every `create_user` caller, and the
  `UserProfile` Pydantic model's extra-field behavior — see §4.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA consent section, migration
  conventions (additive/nullable, append-only, next-free-number check), "do not silently
  swallow errors" (unchanged — the existing `create_user` try/except in both branches
  already surfaces a 503 loudly on any insert failure, and this change adds no new
  error-handling path since it only adds keys to the same dict passed to the same call).
- [ ] Manual repro against a live/staging Supabase — **not performed**, no `DATABASE_URL`
  available in this sandbox (see §9 "not verified" and the migration note above).
- [ ] Feature-flagged — not applicable; no user-visible behavior to gate (see §8).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — this is purely additive; no
  existing response, validation, or auth behavior changes for any existing user

## What was NOT verified

- **Migration was not applied to any real database.** No `DATABASE_URL` in this sandbox, per
  the task's own instruction not to attempt it here. Someone must run
  `python -m backend.scripts.run_migrations` (or the equivalent CI/deploy step) against
  staging and production before or with this code change — see §8's sequencing note for why
  this is a hard precondition, not optional.
- **Not exercised against a real Supabase instance** — only against the `mock_supabase_client`
  / `AsyncMock`-based unit tests already established for these two endpoints. No integration
  test run.
- **The "material changes require re-consent" half of CLAUDE.md's PIPEDA sentence remains
  unimplemented** — this fix only closes the "stored on signup" half (the literal ranked
  blocker #12 finding). Nothing in the codebase reads `consent_version` to prompt a returning
  user for re-consent when the constant is bumped; that would be a separate, larger feature
  (a mobile consent-recheck screen + backend gate) and was out of scope for this fix.
- **The company-email-OTP signup path (`send_company_email_otp`) was found but intentionally
  not touched** — same `users` table, same currently-zero consent stamp, but not a
  rider/driver mobile signup path per the blocker's own framing. Flagged here rather than
  silently left as a rediscovered gap next audit pass.
- **No visual/UI verification** — this is a backend-only, non-user-visible change; there is
  nothing to screenshot or visually regression-test.
