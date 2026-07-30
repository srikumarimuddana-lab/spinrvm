# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (branch: `claude/corporate-dev-seed-script`) |
| Related issue or gap ID | Ad hoc request — no e2e test path for the corporate module without a real inbox + staff KYB approval |

## 1. Issue / gap identified

There is no way to manually exercise the corporate portal (`/company-login`, `/company-portal`) locally without a real work-email inbox (for OTP) and a staff KYB approval step, and no seed data exists anywhere in the repo (migrations, `tests/_factories.py`, `.env.example`) to shortcut this.

## 2. Root cause

Corporate onboarding was built for the production self-serve flow (email OTP → `pending_verification` → staff KYB review → active), which is correct for production but has no dev/test shortcut. No prior seed script for this schema exists (unlike `135_seed_pickup_venues.sql`).

## 3. Fix / remediation

Added a new standalone, opt-in dev script: `backend/scripts/seed_corporate_test_data.py`. It is never invoked automatically (not called from `lifespan.py`, not part of `migrate.py`, no CI/CD reference) — a developer runs it manually against whatever `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` they have configured. It:

- Creates a `users` row for a test owner (or reuses one by email if present).
- Creates a `corporate_accounts` row directly with `status="active"` (bypassing `pending_verification`/KYB) via the existing `insert_corporate_account` repo helper.
- Calls the real, already-idempotent `bootstrap_owner()` service function (the same one production self-serve signup calls) to create the owner's `corporate_members` row — no new membership logic was written.
- Calls the real `ensure_corporate_wallet()` + `apply_topup()` service functions (same RPC path — `corporate_wallet_apply_delta` — that production top-ups use) to give the wallet a starting balance.
- Optionally seeds N additional non-owner members the same way.
- Supports `--cleanup --company-name "..."` to delete everything it created.

No existing application code was modified — this is a new, additive, opt-in file only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New standalone script, zero production code paths modified. Grepped for other importers/callers of `seed_corporate_test_data` — none (it's not imported anywhere).
- It reuses the same repository/service functions (`insert_corporate_account`, `bootstrap_owner`, `ensure_corporate_wallet`, `apply_topup`, `create_user`) that production code paths already exercise — no new SQL, no new RPC, no schema change. The only deviation from the real signup flow is setting `status="active"` directly instead of `pending_verification` (that's the entire point — bypassing KYB for local testing) and using a synthetic phone (same helper pattern `routes/auth.py`'s `_synthetic_phone_for_company_email` already uses for company-email-OTP-authenticated users).
- Because it writes real rows via the service-role key, running it against a shared staging/production Supabase project would create real corporate data. The script requires `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` to already be set (same requirement as every other `backend/scripts/*.py` tool, e.g. `migrate.py`) and does not default to or embed any project URL. It also refuses to run when `ENV=production` (checked before any DB call), so it cannot be pointed at a production-configured environment even by mistake — it does not distinguish staging from dev, however, so a developer with `ENV=development`/`staging` pointed at a shared staging Supabase project could still seed real-looking data there; the guard only covers the `ENV=production` case explicitly.
- No interaction with the ride state machine, dispatch, or the 16 background loops.

## 5. User-experience effect

`none` — dev tooling only, never runs in any deployed environment, touches no user-facing code path.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/seed_corporate_test_data.py` | New file | Let a developer seed a test corporate company/owner/members/wallet locally for manual e2e testing, bypassing OTP + KYB |
| `docs/change-log/2026-07-30-corporate-dev-seed-script.md` | New file | This log |

## 7. Before / after

Not applicable — pure additive new file, no existing behavior changed.

## 8. Rollback plan

`git-revert-safe` for the code itself. For any data it created against a real (non-ephemeral) Supabase project: re-run the same script with `--cleanup --company-name "<name>"`, which deletes the seeded `corporate_wallet_transactions`, `corporate_wallets`, `corporate_members`, and `corporate_accounts` rows for that company by name. This is a data-level remediation path (not just `git revert`), consistent with CLAUDE.md's rule that a code revert alone doesn't undo applied data changes.

## 9. Verification performed

- [x] Automated tests run — none apply (no test suite covers ad hoc scripts in this repo's convention; `python3 -m py_compile` run to confirm no syntax errors, and the module was loaded via `importlib` to confirm all imports resolve against the real `db_supabase`/`services.corporate_membership_service`/`services.corporate_wallet_service` modules — it failed only on `Settings` validation due to no `.env` present in this environment, i.e. every import and function reference resolved correctly).
- [ ] Manual repro steps followed in staging — not run against a live Supabase project as part of this change (no project configured in this environment); the exact function calls used (`insert_corporate_account`, `bootstrap_owner`, `ensure_corporate_wallet`, `apply_topup`) are the same ones already covered by existing corporate unit/integration tests.
- [x] Blast-radius grep performed — see §4.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — money arithmetic uses `Decimal` throughout (topup amount is `Decimal`), Stripe idempotency key is synthesized per-run (`seed-<uuid>`), no float arithmetic.
- [ ] Feature-flagged — not applicable, dev-only script with no runtime code path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`--cleanup` flag, or `git revert` for the code)
- [x] Blast radius is stated, not assumed — isolated, reuses existing production service functions unmodified
- [x] No silent behavior change to an already-shipped flow — none occurred; UX field states `none` explicitly

## What was NOT verified

- Not run end-to-end against a real Supabase instance in this session (no project credentials configured here) — only import-resolution and syntax were verified locally. The user should run it once locally and confirm the printed owner email successfully logs into `/company-login` before relying on it for regular use.
- The `ENV=production` guard was not tested against a real production-configured environment (none available here) — reasoned about from reading the check, not exercised end-to-end. It does not cover a shared staging project with `ENV=staging`/`development` pointed at real staging Supabase credentials — see §4.
